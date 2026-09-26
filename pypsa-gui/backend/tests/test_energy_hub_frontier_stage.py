"""
WP2 — the ε-constraint ``frontier`` stage wired into the EH study.

Plan: docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md WP2
Spec: decision 3 (cost axis excludes shed, period basis on every cost field),
decision 17 (budget), decision 18 (stage order).
"""
from __future__ import annotations

import queue
import threading

import pytest

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network


def _run(n, pack, *, stages, budget=30, cfg=None):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=stages,
        budget_solves=budget,
    )


def test_ladder_is_around_the_target_and_passes_through_it():
    targets, why = ST.frontier_targets_for(10.0, remaining_solves=29)
    assert why is None
    assert targets == [40.0, 20.0, 10.0, 5.0, 2.5]
    assert 10.0 in targets


def test_ladder_trims_to_budget_keeping_the_target_point():
    targets, why = ST.frontier_targets_for(10.0, remaining_solves=4)  # 3 pts + restore
    assert why is None
    assert len(targets) == 3
    assert 10.0 in targets


@pytest.mark.parametrize("remaining", [0, 1, 2, 3])
def test_ladder_skips_below_minimum_points(remaining):
    targets, why = ST.frontier_targets_for(10.0, remaining_solves=remaining)
    assert targets == []
    assert why and "budget" in why


def test_ladder_needs_a_positive_cap():
    assert ST.frontier_targets_for(None, remaining_solves=29)[0] == []
    assert ST.frontier_targets_for(0.0, remaining_solves=29)[0] == []


def _pack(cap: float):
    return default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=cap),
    })


@pytest.mark.live_solve
def test_frontier_stage_fills_points_with_shed_exclusion_and_period_basis():
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "frontier", "assemble"))
    assert report.completeness["cost"] == "ok"
    assert report.completeness["frontier"] == "ok"
    sec = report.sections["frontier"]
    pts = sec.payload["points"]
    ok = [p for p in pts if p["status"] == "ok"]
    assert len(ok) >= ST.MIN_EH_FRONTIER_POINTS
    for p in pts:
        assert p["excludes_shed_cost"] is True
        if p["status"] == "ok":
            assert p["period_basis"] in ("single_period", "multi_period")
            assert p["point"]["total_system_cost_eur"] >= 0
    assert sec.payload["excludes_shed_cost"] is True
    assert sec.payload["period_basis"] == report.period_basis
    # Loosest first (the engine's order); tightening never gets cheaper.
    costs = [p["point"]["total_system_cost_eur"] for p in ok]
    assert all(b >= a - 1e-6 for a, b in zip(costs, costs[1:]))
    # The curve passes through the report's own target.
    assert any(abs(p["target_permyriad"] - 10.0) < 1e-9 for p in pts)
    rec = next(s for s in report.pipeline.stages if s.stage == "frontier")
    assert rec.status == "run"
    assert rec.solves_charged == len(pts) + 1  # + closing restore
    assert report.pipeline.solves_consumed == 1 + rec.solves_charged
    assert report.pipeline.solves_consumed <= report.pipeline.budget_solves
    assert sec.payload["base_restored"] is True


@pytest.mark.live_solve
def test_frontier_skipped_when_budget_cannot_afford_three_points():
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "frontier", "assemble"),
                  budget=3)  # ens_solve takes 1 → 2 left → below 3 + restore
    assert report.completeness["cost"] == "ok"
    assert report.completeness["frontier"] == "skipped"
    rec = next(s for s in report.pipeline.stages if s.stage == "frontier")
    assert rec.status == "skipped"
    assert rec.note and "budget" in rec.note
    assert report.pipeline.solves_consumed == 1
    assert report.pipeline.solves_consumed <= report.pipeline.budget_solves


@pytest.mark.live_solve
def test_frontier_skipped_when_not_requested():
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0), stages=("apply_pack", "ens_solve", "assemble"))
    assert report.completeness["frontier"] == "skipped"
    assert "not requested" in (report.sections["frontier"].note or "")


def test_stage_order_is_still_decision_18():
    from models.energy_hub import EH_PIPELINE_STAGES

    assert tuple(S.DEFAULT_STAGES) == EH_PIPELINE_STAGES
    order = list(EH_PIPELINE_STAGES)
    assert order.index("ens_solve") < order.index("frontier") \
        < order.index("mc_certify") < order.index("fmea_top") \
        < order.index("redundancy")
