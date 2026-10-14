"""
I7 end to end: gridspine's real cuts (``cuts_campus``), the real campus Run,
the cut rows in a real re-solve, and the ratings refresh, on a hand hub.

The hub: a 20 MW critical load, 8 MW of PV, a fixed 15 MW spinning droop
genset and an extendable GFM droop BESS (10 MW floor, 100/MW), solved first
without island constraints, as a project is before the user turns them on.
The island limits are 1 Hz/s and 1 Hz. At 10 MW the BESS saturates on the
20 MW load step (E2) and the island fails at both hours; the cuts ask for
about 20 MW/Hz of droop, 27 to 42 MW of headroom and 455 to 507 MW·s of
inertia (f0·ΔP/2 for a 20 MW step at 1 Hz/s is 500).

One trial applies them: the re-solve buys the BESS that meets them, the
refresh carries its size into the campus file (owner, 2026-10-14), and the
rerun passes everything a cut can fix. What is left is the trip of that
BESS (E3), which no cut on the total can fix: gridspine says it needs N−1
cover from the other units. Without the refresh the rerun checks the old
10 MW BESS, and the loop cannot get there.

The solve here is ``optimize`` with the project's sidecar and applied cuts,
saved to the project, which the solve queue does in the app.
"""
import json
import threading

import pandas as pd
import pypsa
import pytest

from services import campus_electrical_service as ce
from services import campus_island_cuts as cic
from services import project_registry
from tests import test_campus_electrical_service as _ces
from tests.test_island_rows import _BESS, _a, _genset

user_and_db = _ces.user_and_db
SETTINGS = {"k": 1, "invest": False}
pytestmark = pytest.mark.slow                  # a campus Run with the island checks per trial


def _network():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=2, freq="h"))
    n.add("Bus", "grid", v_nom=110.0)
    n.add("Bus", "mv", v_nom=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0, marginal_cost=50.0)
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0, efficiency=1.0)
    n.links["eh_role"] = ["grid_import"]
    n.add("Load", "dc_load", bus="mv", p_set=20.0)
    n.add("Generator", "pv", bus="mv", carrier="solar", p_nom=8.0)
    n.add("Generator", "g", bus="mv", carrier="diesel", p_nom=15.0, marginal_cost=1000.0)
    n.add("StorageUnit", "bess", bus="mv", carrier="battery", p_nom_extendable=True, p_nom_min=10.0,
          capital_cost=100.0, max_hours=2.0, cyclic_state_of_charge=True)
    n.buses["eh_poc"] = [True, False]
    n.buses["eh_sk_mva"] = [2000.0, float("nan")]
    n.optimize(solver_name="highs")
    return n


def _sidecar():
    limits = {"rocof_hz_per_s": _a(1.0), "f_min_hz": _a(47.5), "f_max_hz": _a(51.5), "qss_band_hz": _a(1.0)}
    return {"units": {"bess": dict(_BESS), "dc_load": {"kind": "load", "critical": True},
                      "g": _genset(spinning=True, p_min_pu=_a(0.3))},
            "requirements": {"bridge_s": _a(1.0), "sustained_h": _a(1.0), "rocof_window_ms": _a(500.0),
                             "gfm_margin": _a(0.0), "scenarios": ["seamless"], "genset_redundancy_n": _a(0),
                             "frequency_limits": {"transition": dict(limits), "steady_island": dict(limits)}}}


@pytest.fixture
def hub(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Cut Hub")
    d = project_registry.ensure_project_dir(row)
    _network().export_to_netcdf(str(d / "network.nc"))
    (d / "island_config.json").write_text(json.dumps(_sidecar()))
    ce.draft(row)
    ce.run(row, SETTINGS)
    return row


def _solve(project):
    from services.adequacy.island_config import load_island_config
    from services.solver.island import island_extra_functionality, load_applied_cuts

    d = project_registry.project_dir(project)
    n = pypsa.Network(str(d / "network.nc"))
    island = load_island_config(d / "island_config.json")
    island["cuts"] = load_applied_cuts(d)
    assert n.optimize(solver_name="highs", extra_functionality=island_extra_functionality(island)) == \
        ("ok", "optimal")
    n.export_to_netcdf(str(d / "network.nc"))
    return float(n.objective)


def _loop(project, rerun):
    return cic.apply_loop(project, solve=_solve, rerun=rerun, stop_event=threading.Event())


def test_one_trial_with_the_refresh_fixes_everything_a_cut_can_fix(hub):
    proposed = cic.get_cuts(hub)["proposed"]
    assert {r["hour"] for r in proposed} == {0, 1}
    feasible = [r for r in proposed if r["applicable"]]
    assert {r["kind"] for r in feasible} >= {"droop_gain", "headroom_up", "inertia"}
    assert all(r["estimate"] is not None and r["candidate"] == "bess" for r in feasible)
    # the trip of the only grid-forming storage: no cut on the total, and said why
    e3 = [r for r in proposed if r["event"] == "E3"]
    assert e3 and not any(r["applicable"] for r in e3) and all(r["reason"] for r in e3)

    out = _loop(hub, rerun=lambda p: cic._rerun(p, SETTINGS))
    assert out["stopped"] == "no cut can fix what still fails"
    (trial,) = out["trials"]
    bess = {r["field"]: r for r in trial["refreshed"] if r.get("unit") == "BESS"}
    assert bess["p_mw"]["from"] == pytest.approx(10.0) and bess["p_mw"]["to"] > 300.0
    left = cic.get_cuts(hub)["proposed"]
    assert {(r["hour"], r["event"]) for r in left} == {(0, "E3")}    # only the trip no cut can fix
    assert not any(r["applicable"] for r in left)
    assert trial["verdict"] == "fails at 1 hour"


def test_without_the_refresh_the_loop_does_not_get_there(hub, monkeypatch):
    """The re-solved hub grows the BESS, but the campus file keeps 10 MW:
    the rerun fails as before, so the loop proposes the same cuts again and
    spends its trials."""
    monkeypatch.setattr(cic, "refresh_ratings", lambda project: [])
    out = _loop(hub, rerun=lambda p: cic._rerun(p, SETTINGS))
    assert out["trials"][0]["refreshed"] == []
    assert out["stopped"] != "all pass"
    left = cic.get_cuts(hub)["proposed"]
    assert {r["hour"] for r in left} == {0, 1}                      # nothing fixed in the campus check
    assert any(r["applicable"] for r in left)                       # the same cuts, again
