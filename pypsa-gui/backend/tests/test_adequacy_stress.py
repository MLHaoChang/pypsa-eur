"""
Class-C stress scenarios (Phase 4 Task 3): the per-project scenario
registry and the whole-scenario re-solves that price correlated
weather+demand extremes.

Design: spec §4.1 class C; plan 2026-08-28-fmea-phase4-taxonomy.md. Data
honesty: real coincident climate years are a recorded procurement
follow-up; this machinery accepts PARAMETRIC stress definitions (loudly
labelled) and is forward-compatible with uploaded profile sets — a real
climate year later becomes just another scenario entry.

Fixture: load 100 MW on one bus, cheap gen 90 MW, voll=3000, weights 3 ×
2 snapshots. Base: 10 MW short every hour → EUE 60 MWh. Cold snap
(load ×1.3, renewables ×0.5 — no renewables here): 130 − 90 = 40 short →
EUE 240; ΔEUE = 180 MWh exactly.
"""
from __future__ import annotations

import pathlib
import queue

import pandas as pd
import pypsa
import pytest

from routers.deps import AuthorizedProject
from services.adequacy import stress as ST
from services.adequacy import sweep as S
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

WEIGHT = 3.0
N = 2
VOLL = 3000.0
BASE_EUE = 10.0 * N * WEIGHT       # 60
SNAP_EUE = 40.0 * N * WEIGHT       # 240


def _proj(tmp_path: pathlib.Path) -> AuthorizedProject:
    return AuthorizedProject(name="Demo", directory=tmp_path,
                             uuid="u-1", org_id="o-1", registry_key="o-1/u-1")


def _scenario(**kw) -> dict:
    base = {
        "id": "cold_snap",
        "name": "1-in-20 cold snap",
        "kind": "parametric",
        "frequency_per_year": 0.05,
        "electrical_load_multiplier": 1.3,
        "renewable_availability_multiplier": 0.5,
    }
    base.update(kw)
    return base


# ── the registry sidecar ──────────────────────────────────────────────────

def test_registry_round_trips(tmp_path):
    assert ST.load_scenarios(tmp_path) == []
    ST.save_scenarios(tmp_path, [_scenario()])
    loaded = ST.load_scenarios(tmp_path)
    assert loaded[0]["id"] == "cold_snap"
    assert loaded[0]["frequency_per_year"] == pytest.approx(0.05)


def test_registry_validates(tmp_path):
    with pytest.raises(ST.StressValidationError):
        ST.save_scenarios(tmp_path, [_scenario(frequency_per_year=0.0)])
    with pytest.raises(ST.StressValidationError):
        ST.save_scenarios(tmp_path, [_scenario(electrical_load_multiplier=100.0)])
    with pytest.raises(ST.StressValidationError):
        ST.save_scenarios(tmp_path, [_scenario(kind="magic")])
    with pytest.raises(ST.StressValidationError):
        ST.save_scenarios(tmp_path, [_scenario(id="a b c!")])
    with pytest.raises(ST.StressValidationError):
        ST.save_scenarios(tmp_path, [_scenario() for _ in range(11)])


# ── the re-solve ──────────────────────────────────────────────────────────

def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas"); n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=90.0,
          marginal_cost=10.0)
    return n


def test_cold_snap_prices_exactly(tmp_path):
    n = _network()
    PyPSAService.set_network(n)
    rows = ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(voll=VOLL),
        [_scenario()], log_queue=queue.SimpleQueue())
    assert len(rows) == 1
    r = rows[0]
    assert r["delta_eue_mwh"] == pytest.approx(SNAP_EUE - BASE_EUE, rel=1e-3)
    fm = r["failure_mode"]
    assert fm["failure_class"] == "C"
    assert fm["occurrence_per_year"] == pytest.approx(0.05)
    # criticality €/yr = frequency × ΔEUE × VoLL; f×S identity.
    assert fm["criticality_eur_per_year"] == pytest.approx(
        0.05 * (SNAP_EUE - BASE_EUE) * VOLL, rel=1e-3)
    assert fm["severity_eur"] * fm["occurrence_per_year"] == pytest.approx(
        fm["criticality_eur_per_year"], rel=1e-9)
    # Parametric provenance is loud: the basis names it.
    assert "parametric" in fm["occurrence_basis"]
    from models.adequacy import FailureModeResult
    FailureModeResult.model_validate(fm)
    # The network is back at base afterwards.
    assert float(n.loads.at["l", "p_set"]) == pytest.approx(100.0)


def test_renewable_multiplier_hits_profile_borne_availability(tmp_path):
    """Renewables (no occurrence data → must-take semantics) get their
    p_max_pu scaled; the cold snap halves 40 MW of wind → 20 MW more short.
    Base: 100 load − 90 gas − 40×0.5profile... build: wind p_nom 40, profile
    1.0 → base short = 100−90−40 → 0 shed... use gas 50: base = 100−50−40=10
    short → EUE 60. Snap: load 130, wind 20, gas 50 → 60 short → EUE 360;
    ΔEUE = 300."""
    n = _network()
    n.generators.at["g", "p_nom"] = 50.0
    n.add("Generator", "wind1", bus="b", carrier="wind", p_nom=40.0)
    n.generators_t.p_max_pu = pd.DataFrame({"wind1": [1.0, 1.0]}, index=n.snapshots)
    PyPSAService.set_network(n)
    rows = ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(voll=VOLL),
        [_scenario()], log_queue=queue.SimpleQueue())
    assert rows[0]["delta_eue_mwh"] == pytest.approx((60.0 - 10.0) * N * WEIGHT, rel=1e-3)
    # Profile restored.
    assert float(n.generators_t.p_max_pu["wind1"].min()) == pytest.approx(1.0)


# ── routes ────────────────────────────────────────────────────────────────

def test_registry_routes(tmp_path):
    import routers.adequacy_worksheet as R
    proj = _proj(tmp_path)
    assert R.get_stress_scenarios(project=proj) == {"scenarios": []}
    out = R.put_stress_scenarios(
        body=R.StressScenariosPut(scenarios=[_scenario()]), project=proj)
    assert out["scenarios"][0]["id"] == "cold_snap"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        R.put_stress_scenarios(
            body=R.StressScenariosPut(scenarios=[_scenario(frequency_per_year=-1)]),
            project=proj)
    assert e.value.status_code == 422


def test_sweep_route_runs_b_and_c_together():
    import routers.results as R
    from routers.simulation import _state
    n = _network()
    # Give it a class-B link too: second bus fed only via a tie.
    n.add("Bus", "b2", carrier="AC")
    n.add("Load", "l2", bus="b2", p_set=5.0)
    n.generators.at["g", "p_nom"] = 200.0   # base fully served
    n.add("Link", "tie", bus0="b", bus1="b2", p_nom=10.0,
          outage_rate_value=0.02, outage_rate_basis="FOR", mttr_hours=24.0)
    PyPSAService.set_network(n)
    _state.pop("fmea_sweep", None)
    _state["solver_config"] = SolverConfig(voll=VOLL)
    out = R.post_fmea_sweep(body=R.FmeaSweepRequest(scenarios=[_scenario()]))
    assert out["status"] == "running"
    _state["fmea_sweep"]["thread"].join(timeout=600)
    done = R.get_fmea_sweep()
    assert done["status"] == "done", done
    classes = {r["failure_mode"]["failure_class"]
               for r in done["rows"] if r["failure_mode"]}
    assert classes == {"B", "C"}, classes


def test_fmea_modes_aggregates_all_computed_classes():
    """GET /results/fmea_modes = COPT class-A rows + the last sweep's B/C
    rows, one list, criticality-sorted; 204 only when every source is
    empty. Reuses the network from the combined-sweep test plus a
    class-A-bearing generator."""
    import routers.results as R
    from routers.simulation import _state
    n = _network()
    n.generators.at["g", "p_nom"] = 200.0
    # Class A: give the gas unit occurrence data.
    n.generators.at["g", "outage_rate_value"] = 0.05
    n.generators.at["g", "outage_rate_basis"] = "EFORd"
    n.generators.at["g", "mttr_hours"] = 50.0
    n.add("Bus", "b2", carrier="AC")
    n.add("Load", "l2", bus="b2", p_set=5.0)
    n.add("Link", "tie", bus0="b", bus1="b2", p_nom=10.0,
          outage_rate_value=0.02, outage_rate_basis="FOR", mttr_hours=24.0)
    PyPSAService.set_network(n)
    _state.pop("fmea_sweep", None)
    _state["solver_config"] = SolverConfig(voll=VOLL)

    # A alone (no sweep yet).
    out = R.get_fmea_modes()
    classes = {r["failure_class"] for r in out["per_mode"]}
    assert classes == {"A"}

    # After a B+C sweep: all three.
    R.post_fmea_sweep(body=R.FmeaSweepRequest(scenarios=[_scenario()]))
    _state["fmea_sweep"]["thread"].join(timeout=600)
    out = R.get_fmea_modes()
    classes = {r["failure_class"] for r in out["per_mode"]}
    assert classes == {"A", "B", "C"}, classes
    crits = [r["criticality_eur_per_year"] for r in out["per_mode"]]
    assert crits == sorted(crits, reverse=True)


def test_fmea_modes_204_when_empty():
    import pypsa
    import routers.results as R
    from routers.simulation import _state
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    PyPSAService.set_network(n)
    _state.pop("fmea_sweep", None)
    resp = R.get_fmea_modes()
    assert getattr(resp, "status_code", 200) == 204


# ---------------------------------------------------------------------------
# A contingency mutation must survive the solver's `_user_ts` reapply.
#
# Found by an end-to-end QA run on a three-zone system whose load and VRE
# profiles were uploaded through the GUI, which is the normal workflow. Every
# test in this file builds its network in process, so `_user_ts` is empty and
# the reapply is a no-op — the whole suite was structurally blind to this.
#
# What happened: `run_simulation` re-broadcasts every user-uploaded series from
# `_user_ts` onto the live `_t` tables just before building the LP
# (solver_service, "Re-broadcast every user-uploaded time series"). The sweep
# runs on the FOREGROUND network, so that reapply fired inside every
# contingency solve and restored the pristine profile OVER the mutation the
# contingency had just made. The LP then solved an unmutated network, returned
# "ok", and the row reported ΔEUE = 0.
#
# Concretely: a cold snap with load ×2.0 and renewables ×0.0 priced at exactly
# zero criticality. The row looked like a successful measurement of "this
# failure mode costs nothing", not like a failure to measure.
# ---------------------------------------------------------------------------


def _tv_network() -> pypsa.Network:
    """Same shape as the module fixture but with a TIME-VARYING load, so the
    profile lives in `loads_t.p_set` where `_user_ts` reapply writes."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=pd.Series([100.0] * N, index=n.snapshots))
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=90.0,
          marginal_cost=10.0)
    return n


def test_contingency_mutation_survives_the_user_ts_reapply(monkeypatch):
    from routers import network as network_router

    n = _tv_network()
    PyPSAService.set_network(n)          # makes the sweep's solves FOREGROUND

    # Populate the store exactly as a GUI profile upload does, with the
    # PRISTINE (unstressed) series — this is what used to clobber the
    # mutation mid-sweep.
    store = {("loads", "p_set", "l"): pd.Series([100.0] * N, index=n.snapshots)}
    monkeypatch.setattr(network_router, "_user_ts", store, raising=False)

    scenario = {"id": "coldsnap", "kind": "parametric",
                "frequency_per_year": 2.0,
                "electrical_load_multiplier": 1.3}
    rows = ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(solver_name="highs", voll=VOLL),
        [scenario], log_queue=queue.SimpleQueue())

    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] in ("ok", "optimal"), row
    # load ×1.3 → 130 MW against 90 MW firm → 40 MW short per snapshot.
    # ΔEUE = (40 − 10) × N × WEIGHT = 30 × 2 × 3 = 180 MWh.
    assert row["delta_eue_mwh"] == pytest.approx(180.0), (
        "the stress scenario measured no degradation — the uploaded profile "
        "was reapplied over the mutation before the LP was built"
    )
    assert row["failure_mode"]["criticality_eur_per_year"] > 0


def test_the_reapply_marker_never_outlives_its_contingency():
    """
    The suppression marker is set per contingency and cleared in the same
    `finally` as the undo. If it leaked, every SUBSEQUENT foreground solve
    would silently stop honouring uploaded profiles — a far worse bug than
    the one it fixes.
    """
    n = _tv_network()
    PyPSAService.set_network(n)
    ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(solver_name="highs", voll=VOLL),
        [{"id": "s", "kind": "parametric", "frequency_per_year": 1.0,
          "electrical_load_multiplier": 1.3}],
        log_queue=queue.SimpleQueue())
    assert not getattr(n, "_adequacy_transient_profiles", False)
