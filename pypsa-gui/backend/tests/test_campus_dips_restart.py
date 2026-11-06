"""The campus run's dip and restart screens (grid-to-process issue 12b-3): the stage after the P–V/Q–V screen,
the two CSVs in the results payload, a screen failure that never fails the run, and the units whose restart,
ride-through or motor field changed since the run read out of date."""

import math

import pypsa

from services import campus_electrical_service as ce
from services import project_registry
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

SETTINGS = {"k": 1, "invest": False}


def _with_units(project):
    """The test hub with two process units on a tonnes bus with a shortfall Generator: the mill with every
    restart input, the pump with none (unknown everywhere)."""
    nc = project_registry.project_dir(project) / ce.NETWORK_FILE
    n = pypsa.Network(str(nc))
    n.add("Bus", "ore", carrier="ore", unit="t")
    n.add("Generator", "__lost_ore", bus="ore", p_nom=100.0)
    n.generators["process_role"] = ""
    n.generators.loc["__lost_ore", "process_role"] = "shortfall"
    for name in ("mill", "pump"):
        n.add("Link", name, bus0="mv", bus1="ore", p_nom=2.0, efficiency=2.0)
    n.links["eh_process"] = [False, True, True]
    n.links["ride_through_curve"] = ["", "20:0.5", ""]
    n.links["restart_time_h"] = [math.nan, 4.0, math.nan]
    n.links["criticality_tier"] = ["", "process_critical", "safety"]
    n.links["motor_kw"] = [math.nan, 1500.0, math.nan]
    n.links["start_method"] = ["", "vfd", ""]
    n.links["i_start_pu"] = [math.nan, 1.2, math.nan]
    n.export_to_netcdf(str(nc))
    return nc


def _run(project, **kw):
    ce.draft(project)
    return ce.run(project, {**SETTINGS, "profile": "generic_assumed"}, **kw)


def test_the_run_screens_dips_and_restarts_after_the_pv_qv_screen(hub):  # noqa: F811
    _with_units(hub)
    stages = []
    _run(hub, progress=lambda name, done, total: stages.append(name))
    assert stages.index("dips_restart") == stages.index("voltage_stability") + 1
    state = ce.get_state(hub)
    dips = state["results"]["dips_restart"]
    assert {r["level"] for r in dips["dip_trips"]} == {"class", "unit", "total"}
    assert {r["level"] for r in dips["restart"]} == {"unit", "event", "total"}
    unit = {r["unit"]: r for r in dips["dip_trips"] if r["level"] == "unit"}
    assert unit["pump"]["verdict"] == "unknown" and unit["mill"]["verdict"] == "known"
    assert dips["inputs"]["mill"] == {
        "ride_through_curve": "20:0.5",
        "restart_time_h": 4.0,
        "motor_kw": 1500.0,
        "start_method": "vfd",
        "i_start_pu": 1.2,
    }
    assert dips["inputs"]["pump"] == dict.fromkeys(ce.DIPS_RESTART_FIELDS)
    assert "event model" in dips["restart_label"] and dips["basis"]
    assert state["stale"] is False and dips["out_of_date"] == []


def test_a_changed_restart_field_marks_that_unit_out_of_date_only(hub):  # noqa: F811
    nc = _with_units(hub)
    _run(hub)
    n = pypsa.Network(str(nc))
    n.links.loc["mill", "restart_time_h"] = 6.0
    n.export_to_netcdf(str(nc))
    state = ce.get_state(hub)
    assert state["stale"] is True
    assert state["results"]["dips_restart"]["out_of_date"] == ["mill"]


def test_an_unrelated_change_leaves_every_unit_current(hub):  # noqa: F811
    nc = _with_units(hub)
    _run(hub)
    n = pypsa.Network(str(nc))
    n.loads.loc["dc_load", "p_set"] = 41.0
    n.export_to_netcdf(str(nc))
    state = ce.get_state(hub)
    assert (
        state["stale"] is True and state["results"]["dips_restart"]["out_of_date"] == []
    )


def test_a_screen_error_never_fails_the_run_and_leaves_no_screen_file(hub, monkeypatch):  # noqa: F811
    _with_units(hub)
    real = ce.cs.restart_campus

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)  # it wrote its CSVs, then fails: none of them may stay
        raise ce.ContractError("restart: test failure")

    monkeypatch.setattr(ce.cs, "restart_campus", broken)
    failed = _run(hub)["results"]
    assert failed["dips_restart"] == {"error": "restart: test failure"}
    run = ce.campus_dir(hub) / "run"
    for name in (ce.cs.DIP_TRIPS_CSV, ce.cs.RESTART_CSV, ce.DIPS_RESTART_META):
        assert not (run / name).exists()
    assert failed["compliance"]


def test_a_run_from_before_the_stage_has_no_screens(hub):  # noqa: F811
    _with_units(hub)
    _run(hub)
    run = ce.campus_dir(hub) / "run"
    for name in (ce.cs.DIP_TRIPS_CSV, ce.cs.RESTART_CSV, ce.DIPS_RESTART_META):
        (run / name).unlink()
    assert ce.get_state(hub)["results"]["dips_restart"] is None
