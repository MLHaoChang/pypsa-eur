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
