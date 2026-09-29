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


# Merge 2026-09-28: the ladder (master PR #53) feeds the P12 frontier stage
# (``eh_study.frontier_targets`` + ``frontier_point_count``): at most ~40 % of
# the budget, at least TWO points, on a private copy with no closing restore
# (decision Q4) — so no "+ restore" solve and no three-point minimum.


def test_ladder_is_around_the_target_and_passes_through_it():
    n = S.frontier_point_count(remaining=29, budget_solves=30)
    targets = sorted(S.frontier_targets(10.0, n), reverse=True)
    assert targets == pytest.approx([40.0, 20.0, 10.0, 5.0, 2.5])
    assert 10.0 in targets


def test_ladder_trims_to_budget_keeping_the_target_point():
    n = S.frontier_point_count(remaining=3, budget_solves=4)
    targets = S.frontier_targets(10.0, n)
    assert len(targets) == n == 2
    assert 10.0 in targets


@pytest.mark.parametrize("remaining", [0, 1])
def test_ladder_skips_below_minimum_points(remaining):
    assert S.frontier_point_count(remaining=remaining, budget_solves=30) \
        < ST.MIN_EH_FRONTIER_POINTS


def test_ladder_needs_a_positive_cap():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AvailabilityTarget(ens_cap_permyriad=0.0)
    # A pack without an ENS cap: the stage is not_established (see
    # test_energy_hub_frontier_fmea::test_frontier_without_a_pack_ens_target_…).


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
    assert rec.solves_charged == len(pts)  # private copy: no closing restore
    assert report.pipeline.solves_consumed == 1 + rec.solves_charged
    assert report.pipeline.solves_consumed <= report.pipeline.budget_solves
    assert sec.payload["restore_skipped_on_private_copy"] is True


@pytest.mark.live_solve
def test_frontier_skipped_when_budget_cannot_afford_three_points():
    # P12 rule: two points make the minimum curve, so the budget that
    # cannot afford one is 2 (ens_solve takes 1 → 1 left). A requested stage
    # that produced nothing is not_established (spec §4), its record skipped.
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "frontier", "assemble"),
                  budget=2)
    assert report.completeness["cost"] == "ok"
    assert report.completeness["frontier"] == "not_established"
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


# ── pack-level ladder / top-N (cleanup after WP2/WP3) ────────────────────

def test_pack_defaults_match_stage_constants_and_engine_cap():
    from models.energy_hub import (
        DEFAULT_EH_FMEA_TOP_N,
        DEFAULT_EH_FRONTIER_LADDER,
        MAX_EH_FRONTIER_POINTS,
        default_weak_flexible_pack,
    )
    from services.adequacy.frontier import MAX_FRONTIER_POINTS

    assert MAX_EH_FRONTIER_POINTS == MAX_FRONTIER_POINTS
    assert ST.EH_FRONTIER_LADDER == DEFAULT_EH_FRONTIER_LADDER
    assert ST.FMEA_TOP_N == DEFAULT_EH_FMEA_TOP_N
    pack = default_weak_flexible_pack()
    assert pack.frontier_ladder == DEFAULT_EH_FRONTIER_LADDER
    assert pack.fmea_top_n == DEFAULT_EH_FMEA_TOP_N


@pytest.mark.parametrize("ladder", [(), (1.0, 1.0, 2.0), (1.0, -2.0, 4.0),
                                    (1.0, float("inf"), 2.0),
                                    tuple(float(i + 1) for i in range(13))])
def test_pack_refuses_an_unusable_frontier_ladder(ladder):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        default_strong_grid_pack().model_validate({
            **default_strong_grid_pack().model_dump(), "frontier_ladder": ladder})


def test_pack_refuses_an_out_of_range_top_n():
    from pydantic import ValidationError

    for bad in (0, 51):
        with pytest.raises(ValidationError):
            default_strong_grid_pack().model_validate({
                **default_strong_grid_pack().model_dump(), "fmea_top_n": bad})


def test_trim_keeps_the_points_nearest_the_target_on_both_sides():
    targets = sorted(S.frontier_targets(10.0, 3), reverse=True)
    assert targets == pytest.approx([20.0, 10.0, 5.0])


def test_a_custom_ladder_is_used_and_needs_two_points():
    targets = sorted(S.frontier_targets(10.0, 5, ladder=(3.0, 1.0, 0.3)),
                     reverse=True)
    assert targets == pytest.approx([30.0, 10.0, 3.0])
    # Two points (×1 is always one) are the P12 minimum curve.
    assert S.frontier_targets(10.0, 5, ladder=(2.0, 1.0)) == pytest.approx(
        [10.0, 20.0])


@pytest.mark.live_solve
def test_the_pack_ladder_drives_the_study_frontier():
    n = certifiable_weak_network()
    pack = _pack(10.0).model_copy(update={"frontier_ladder": (3.0, 1.0, 0.5)})
    report = _run(n, pack, stages=("apply_pack", "ens_solve", "frontier", "assemble"))
    payload = report.sections["frontier"].payload
    assert payload["targets_permyriad"] == pytest.approx([30.0, 10.0, 5.0])
    rec = next(s for s in report.pipeline.stages if s.stage == "frontier")
    assert rec.solves_charged == 3                  # no closing restore
