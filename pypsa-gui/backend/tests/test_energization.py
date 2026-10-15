"""A connection energized in steps, in absolute MW (gatekeeper review of #266).

The schedule E(t) composes with whatever p_nom a consumer writes: the
import_cap lever's rungs at 20, 30 and 60 MW read 20/20/20 in the 20 MW year
and min(E, rung) later, never a rescaled fraction. The MC import credit takes the
horizon minimum.
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pytest

from services.adequacy import energization as E

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

YEARS = (2027, 2030, 2035)
#: The template's schedule (20 MW, then the full connection), read from it.
SCHEDULE = {y: T.DC_ENERGIZATION_MW[y] for y in YEARS}


def test_the_schedule_values_themselves():
    """The tests below read SCHEDULE from the template; pin its values once so a
    drift is caught (#294 review): 20 MW until 2030, then the 55 MW connection."""
    assert SCHEDULE == {2027: 20.0, 2030: 55.0, 2035: 55.0}


def _staged():
    return T.BUILDERS["eh_datacenter_staged"]()


def _cap_by_period(n, link="grid_import"):
    p_nom = float(n.links.at[link, "p_nom"])
    pu = n.links_t.p_max_pu[link]
    return {y: round(float((pu.loc[y] * p_nom).max()), 6) for y in YEARS}


def test_the_template_stores_the_schedule_in_mw_not_as_a_fraction():
    n = _staged()
    assert "grid_import" not in n.links_t.p_max_pu.columns
    e = E.energization_mw(n, "grid_import")
    by = {y: set(np.round(e[n.snapshots.get_level_values(0) == y], 6)) for y in YEARS}
    assert by == {y: {v} for y, v in SCHEDULE.items()}


def test_applied_for_a_solve_and_undone_after():
    n = _staged()
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == SCHEDULE
    undo()
    undo()                                         # idempotent
    assert "grid_import" not in n.links_t.p_max_pu.columns


@pytest.mark.parametrize("rung", [20.0, 30.0, 60.0])
def test_a_rewritten_p_nom_composes_as_min_of_schedule_and_cap(rung):
    """The import_cap lever writes p_nom = rung; the solve hook then caps at
    min(E, rung)."""
    from services.adequacy.levers import apply_lever_scenario

    want = {y: min(v, rung) for y, v in SCHEDULE.items()}
    n = _staged()
    undo_lever, _mutation = apply_lever_scenario(n, "import_cap", value=rung)
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == want
    undo()
    undo_lever()


def test_an_existing_tighter_profile_still_wins():
    n = _staged()
    n.links_t.p_max_pu["grid_import"] = 0.25           # a quarter of the rating, everywhere
    quarter = 0.25 * float(n.links.at["grid_import", "p_nom"])
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == {y: min(quarter, v) for y, v in SCHEDULE.items()}
    undo()
    assert np.allclose(n.links_t.p_max_pu["grid_import"], 0.25)


def test_the_firm_credit_is_the_horizon_minimum():
    n = _staged()
    assert E.firm_energized_mw(n, "grid_import") == pytest.approx(20.0)
    assert E.firm_energized_mw(n, "site_transformer") is None


def test_a_network_without_a_schedule_is_untouched():
    n = T.BUILDERS["eh_datacenter"]()
    before = n.links_t.p_max_pu.copy()
    E.apply_energization(n)()
    assert E.energized_links(n) == []
    assert n.links_t.p_max_pu.equals(before)


def test_the_schedule_survives_the_netcdf_round_trip(tmp_path):
    import pypsa

    n = _staged()
    n.export_to_netcdf(str(tmp_path / "n.nc"))
    m = pypsa.Network(str(tmp_path / "n.nc"))
    assert np.allclose(E.energization_mw(m, "grid_import"), E.energization_mw(n, "grid_import"))


def test_the_mc_import_credit_is_the_horizon_minimum_with_a_label():
    """MC certification credits the import as one two-state unit; a staged
    connection is firm only at its 20 MW minimum (not the 33 MW mean)."""
    from services.adequacy.archetypes import _import_capacity_to_hub

    n = _staged()
    far = {str(n.links.at["grid_import", "bus0"])}
    assert _import_capacity_to_hub(n, "grid_import", far) == pytest.approx(
        20.0 * float(n.links.at["grid_import", "efficiency"]))
    flat = T.BUILDERS["eh_datacenter"]()
    assert _import_capacity_to_hub(flat, "grid_import", far) == pytest.approx(
        float(flat.links.at["grid_import", "p_nom"]) * float(flat.links.at["grid_import", "efficiency"]))


@pytest.mark.live_solve
def test_run_simulation_applies_the_schedule_and_undoes_it():
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = _staged()
    PyPSAService.set_network(n)
    cfg = SolverConfig(**{k: v for k, v in T.solver_config("eh_datacenter_staged").items()
                          if k != "island_constraints"})
    status, _ = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal")
    flow = n.links_t.p0["grid_import"]
    assert flow.loc[2027].max() <= 20.0 + 1e-6
    assert flow.loc[2035].max() > 20.0 + 1e-6        # the full connection once energized
    assert "grid_import" not in n.links_t.p_max_pu.columns   # undone after the solve


def test_readiness_reports_the_firm_import_beside_the_rating():
    from services.adequacy.eh_readiness import _import_firm_mw

    n = _staged()
    assert _import_firm_mw(n, ["grid_import"]) == pytest.approx(20.0)
    assert _import_firm_mw(T.BUILDERS["eh_datacenter"](), ["grid_import"]) is None


def test_the_island_study_solves_its_copies_with_the_schedule_applied():
    """`island.island_study` (the campus cost-of-islanding study) solves
    copies outside run_simulation; each copy gets the schedule too."""
    import pandas as pd

    from services.solver import island as I

    n = T.BUILDERS["eh_datacenter"]()                     # single-period
    n.links_t[E.ATTR] = pd.DataFrame({"grid_import": 20.0}, index=n.snapshots)
    seen = []

    def solve(c, _extra, **_kw):
        pu = c.links_t.p_max_pu["grid_import"] * float(c.links.at["grid_import", "p_nom"])
        seen.append(round(float(pu.max()), 6))
        return "warning", "not solved in this test"

    I.island_study(n, {"requirements": {"scenarios": [], "values": {}}}, scenarios=[], solve=solve)
    assert seen == [20.0]
    assert "grid_import" not in n.links_t.p_max_pu.columns      # the network itself untouched


def test_the_island_study_refuses_a_multi_period_network():
    from services.solver import island as I

    with pytest.raises(I.IslandRefusal, match="island_study_multi_period_unsupported"):
        I.island_study(_staged(), {"requirements": {"scenarios": [], "values": {}}}, scenarios=[])


def test_an_hour_without_a_schedule_value_is_uncapped_in_that_hour_only():
    """A period added after the schedule was written has NaN rows: they get
    no cap, and 2027 keeps its 20 MW."""
    n = _staged()
    period = n.snapshots.get_level_values(0)
    n.links_t[E.ATTR].loc[period == 2035, "grid_import"] = np.nan
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == SCHEDULE
    undo()
    assert E.firm_energized_mw(n, "grid_import") == pytest.approx(20.0)


def test_the_mc_samples_the_import_hour_by_hour_at_the_schedule():
    """`eh_stages.hub_fleet_scope` builds the sampled import unit from
    cap × p_max_pu; it composes the schedule the same way the solve does."""
    import threading

    from services.adequacy import eh_stages as ST
    from services.adequacy.eh_study_runner import _PACK_FACTORY, apply_pack_overrides
    from services.solver_service import SolverConfig

    meta = T.TEMPLATE_META["eh_datacenter_staged"]
    pack = apply_pack_overrides(_PACK_FACTORY[meta["recommended_archetype"]](),
                                meta["pack_overrides"])
    n = _staged()
    frozen = ST.freeze_fixed_plan(n, SolverConfig(voll=5000.0, multi_investment_periods=True),
                                  threading.Lock(), overlay=pack.import_overlay,
                                  import_model="sampled_unit")
    [unit] = [u for u in frozen.mc_inputs.units if u.name == "link:grid_import"]
    series = np.asarray(unit.capacity_series)
    period = n.snapshots.get_level_values(0)
    eff = float(n.links.at["grid_import", "efficiency"])
    assert {y: round(float(series[period == y].max()), 6) for y in YEARS} == \
        {y: round(v * eff, 6) for y, v in SCHEDULE.items()}


def test_the_boundary_credit_discloses_the_schedule_by_period():
    from services.adequacy.energization import energized_mw_by_period

    assert energized_mw_by_period(_staged(), "grid_import") == {
        str(y): v for y, v in SCHEDULE.items()}
