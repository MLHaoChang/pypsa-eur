"""
Binding detection + the minimal AdequacyReport (Phase 1 Task 3).

Design: spec §§5.5, 7; plan Phase 1 Task 3. After a solve with the target on,
the solver emits a minimal AdequacyReport into the solver state
(`adequacy_report`, persisted like `last_lost_load`). The report's numbers
are assembled from SOLVE-TIME truth: the wrapper stashes its computed
targets (restore reverts load scalers, so a post-solve recomputation of the
demand denominator would drift), and the capture computes the weighted
achieved values at capture time.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.adequacy import AdequacyReport
from services.project_context import RESULT_STATE_KEYS, ProjectSolverState
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation
from tests.test_adequacy_ens_cap import (
    CAP_MWH,
    CAP_PERMYRIAD,
    UNCAPPED_SHED_MWH,
    VOLL,
    WEIGHT,
    ZONE_CAP_PERMYRIAD,
    ZONE_MULTIPLE,
    _network,
    _two_zone_network,
)


def _solve(n: pypsa.Network, **cfg_kw) -> dict:
    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(voll=VOLL, **cfg_kw)
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
    )
    assert status in ("ok", "optimal"), (status, condition)
    return sink


def _report(sink: dict) -> AdequacyReport:
    raw = sink.get("adequacy_report")
    assert raw is not None, "solve with target on emitted no adequacy_report"
    return AdequacyReport.model_validate(raw)


def test_binding_cap_yields_system_cap_report():
    n = _network()
    sink = _solve(n, ens_cap_permyriad=CAP_PERMYRIAD)
    r = _report(sink)
    assert r.engine == "lp_proxy" and r.fidelity == "deterministic_scenario"
    assert r.target.binding == "system_cap"
    assert r.metrics.ens_mwh == pytest.approx(CAP_MWH, rel=1e-3)
    assert r.metrics.shed_hours > 0
    assert r.target.system.cap_mwh == pytest.approx(CAP_MWH, rel=1e-6)
    # Cost excludes shed by construction: objective − shed cost.
    shed_cost = sink["last_lost_load"]["lost_load_cost_eur"]
    objective = float(sink.get("objective") or n.objective)
    assert r.cost.total_system_cost_eur == pytest.approx(
        objective - shed_cost, rel=1e-6)
    assert r.energy.involuntary_mwh == pytest.approx(
        sink["last_lost_load"]["lost_load_total_mwh"], rel=1e-9)
    assert r.energy.demand_response_mwh == 0.0
    assert r.target.zone_field_populated is False


def test_loose_cap_reports_voll_as_the_standard():
    sink = _solve(_network(), ens_cap_permyriad=9000.0)
    r = _report(sink)
    assert r.target.binding == "voll"
    assert r.metrics.ens_mwh == pytest.approx(UNCAPPED_SHED_MWH, rel=1e-3)


def test_zone_ceiling_reports_zone_cap_and_flags_the_zone():
    sink = _solve(
        _two_zone_network(),
        ens_cap_permyriad=ZONE_CAP_PERMYRIAD,
        ens_zone_cap_multiple=ZONE_MULTIPLE,
    )
    r = _report(sink)
    assert r.target.binding == "zone_cap"
    assert r.target.zone_field_populated is True
    binding_zones = {z.zone for z in r.target.zones if z.binding}
    assert binding_zones == {"AA", "BB"}


def test_no_target_no_report():
    sink = _solve(_network())
    assert "adequacy_report" not in sink or sink["adequacy_report"] is None


def test_report_is_a_persisted_result_state_key():
    assert "adequacy_report" in RESULT_STATE_KEYS
    assert hasattr(ProjectSolverState(), "adequacy_report")


def test_endpoint_serves_204_then_200():
    import routers.results as R
    from routers.simulation import _state
    old = _state.get("adequacy_report")
    try:
        _state["adequacy_report"] = None
        resp = R.get_adequacy()
        assert getattr(resp, "status_code", 200) == 204
        sink = _solve(_network(), ens_cap_permyriad=CAP_PERMYRIAD)
        _state["adequacy_report"] = sink["adequacy_report"]
        out = R.get_adequacy()
        r = AdequacyReport.model_validate(out)
        assert r.target.binding == "system_cap"
    finally:
        _state["adequacy_report"] = old
