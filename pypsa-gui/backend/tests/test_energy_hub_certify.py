"""
WP1 — ``mc_certify`` wired into the EH study (plan on ENS, certify on MC LOLE).

Plan: docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md WP1
Spec: docs/superpowers/specs/2026-09-14-eh-reference-design.md decisions 1–2

The MC runs on the FIXED plan the report describes (snapshot frozen at the
end of ``ens_solve``), fills ``mc_lole_h`` and a ``certification`` section
whose verdict fails on LOLE even when ENS is met. It solves nothing, so it
charges nothing to the LP budget.
"""
from __future__ import annotations

import math
import queue
import threading

import pytest

from models.energy_hub import (
    DEFAULT_EH_MC_DRAWS,
    MAX_EH_MC_DRAWS,
    REPORT_SECTIONS,
    AvailabilityTarget,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import (
    VOLL,
    certifiable_weak_network,
    islanded_certify_network,
    no_occurrence_network,
)


def _run(n, pack, *, stages, cfg=None, budget=30, store=None):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=stages,
        budget_solves=budget,
        store=store,
    )


def test_certification_is_a_report_section_after_target():
    assert "certification" in REPORT_SECTIONS
    assert REPORT_SECTIONS.index("certification") == REPORT_SECTIONS.index("target") + 1


def test_pack_mc_draw_budget_matches_engine_cap():
    from services.adequacy.mc import MAX_DRAWS

    assert MAX_EH_MC_DRAWS == MAX_DRAWS
    assert 1 <= DEFAULT_EH_MC_DRAWS <= MAX_DRAWS
    assert default_weak_flexible_pack().mc_draws == DEFAULT_EH_MC_DRAWS


def test_mc_certify_is_implemented_in_the_driver():
    # Merge 2026-09-28: the stage body is the P11 one in the driver's stage
    # table; eh_stages supplies the fixed-plan fleet it samples.
    from services.adequacy import eh_stages

    assert callable(S._STAGE_HANDLERS["mc_certify"])
    assert callable(eh_stages.freeze_fixed_plan)
    assert callable(S.certification_verdict)


# Merge 2026-09-28: the verdict is the P11 rule on the LOLE 95% CI (spec §4
# amendment, Q1) — pass / fail / inconclusive, none without a target — not a
# point comparison. Same decision-2 cases, on the CI.
@pytest.mark.parametrize(
    "ci,target,floor,expected",
    [
        ((0.5, 1.5), 3.0, None, "pass"),
        ((2.0, 3.0), 3.0, None, "pass"),            # upper bound AT target
        ((3.2, 3.9), 3.0, None, "fail"),
        ((2.5, 3.5), 3.0, None, "inconclusive"),    # CI straddles
        ((0.0, 0.1), 3.0, 5.0, "inconclusive"),     # below the resolution floor
        ((0.5, 1.5), None, None, None),             # no target
    ],
)
def test_certification_verdict_rule(ci, target, floor, expected):
    verdict, _note = S.certification_verdict(
        lole_ci=ci, target_h=target, resolution_floor_h=floor)
    assert verdict == expected


def test_no_mc_lole_gives_no_verdict():
    """Master's (None, 3.0) → not_established case, restored (merge review
    N3): without an MC LOLE there is no verdict; the section itself is
    not_established (see the no-occurrence test below)."""
    for ci in (None, (None, None), (float("nan"), 1.0)):
        verdict, note = S.certification_verdict(lole_ci=ci, target_h=3.0)
        assert verdict is None
        assert "not established" in note


@pytest.mark.live_solve
def test_off_grid_pack_certification_fails_on_lole_even_when_ens_met():
    """Spec decision 2: LOLE failure fails certification even if ENS is met."""
    n = islanded_certify_network()
    # Loose ENS cap (met by the LP) + a 3 h/yr LOLE target the MC cannot meet:
    # losing either unit sheds, so LOLE is hundreds of hours.
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=2000.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "levers": default_off_grid_pack().levers.model_copy(
            update={"storage_duration": False}),
        "mc_draws": 100,
    })
    report = _run(n, pack, stages=("apply_pack", "ens_solve", "mc_certify", "assemble"))
    assert report.completeness["target"] == "ok"
    assert report.completeness["certification"] == "ok"
    assert report.mc_lole_h is not None and math.isfinite(report.mc_lole_h)
    assert report.mc_lole_h > 3.0
    sec = report.sections["certification"]
    assert sec.payload["verdict"] == "fail"          # P11 vocabulary
    assert "decision 2" in (sec.note or "")
    assert report.certified is False
    assert sec.payload["metric"] == "mc_lole"
    assert sec.payload["target_lole_h"] == 3.0
    assert sec.payload["mc_lole_h"] == report.mc_lole_h
    assert sec.payload["ens_met"] is True
    assert sec.payload["n_samples"] >= 1
    assert sec.payload["warning"]  # MC_WARNING_V1 travels with the number
    rec = next(s for s in report.pipeline.stages if s.stage == "mc_certify")
    assert rec.status == "run"
    assert rec.solves_charged == 0  # the MC solves nothing
    assert report.pipeline.solves_consumed == 1  # ens_solve only


@pytest.mark.live_solve
def test_weak_flexible_default_pipeline_certifies_with_loose_target():
    n = certifiable_weak_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=10.0, target_lole_h=8760.0,
            certification_metric="mc_lole"),
        "mc_draws": 50,
    })
    store: dict = {}
    report = _run(n, pack, stages=None, store=store)
    assert report.completeness["certification"] == "ok"
    assert report.sections["certification"].payload["verdict"] == "pass"
    assert report.certified is True
    assert report.mc_lole_h is not None and report.mc_lole_h <= 8760.0
    assert store["eh_reference_design_report"]["mc_lole_h"] == report.mc_lole_h
    mc = next(s for s in report.pipeline.stages if s.stage == "mc_certify")
    assert mc.status == "run"


@pytest.mark.live_solve
def test_certification_not_established_when_fleet_has_no_occurrence_data():
    n = no_occurrence_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=1000.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "mc_certify_required": True,
    })
    report = _run(n, pack, stages=("apply_pack", "ens_solve", "mc_certify", "assemble"),
                  cfg=SolverConfig(voll=150.0, ens_cap_permyriad=1000.0))
    assert report.completeness["target"] == "ok"
    assert report.completeness["certification"] == "not_established"
    assert report.mc_lole_h is None
    note = report.sections["certification"].note or ""
    assert "occurrence" in note
    rec = next(s for s in report.pipeline.stages if s.stage == "mc_certify")
    assert rec.status == "skipped"
    assert rec.note and "occurrence" in rec.note


@pytest.mark.live_solve
def test_certification_skipped_when_not_requested_and_not_required():
    n = no_occurrence_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    report = _run(n, pack, stages=("apply_pack", "ens_solve", "assemble"),
                  cfg=SolverConfig(voll=150.0, ens_cap_permyriad=1000.0))
    assert report.completeness["certification"] == "skipped"
    assert report.mc_lole_h is None


@pytest.mark.live_solve
def test_certification_not_established_when_required_but_not_requested():
    n = no_occurrence_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=1000.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "mc_certify_required": True,
    })
    report = _run(n, pack, stages=("apply_pack", "ens_solve", "assemble"),
                  cfg=SolverConfig(voll=150.0, ens_cap_permyriad=1000.0))
    assert report.completeness["certification"] == "not_established"
    assert "required" in (report.sections["certification"].note or "")


def test_no_stage_is_recorded_as_unimplemented_anymore():
    """The P1.5 'not implemented in sync driver' note must be gone for good."""
    import inspect

    src = inspect.getsource(S)
    assert "not implemented in P1.5" not in src
    assert "not implemented in sync driver" not in src


# template-e2e 21: a goal below the resolution floor names the floor and the
# fewest draws that resolve it. The first MC batch is min(draws, MAX_DRAWS),
# so draws >= ceil(w_min / target_h) puts the floor (w_min / n) at or under
# the target.
@pytest.mark.parametrize(
    "floor,n,target,expected",
    [
        (52.1, 1, 3.0, 18),          # 52.1 / 3 = 17.37 → 18
        (52.1, 2, 3.0, 35),          # w_min = floor·n = 104.2 h → 34.7 → 35
        (6.0, 1, 3.0, 2),            # exact boundary: 6 / 3 = 2 draws
        (1.0, 500, 3.0, 167),        # w_min 500 h → 166.7 → 167
        (52.1, 1, 0.0, None),        # no draw count resolves a zero goal
        (None, 1, 3.0, None),        # no floor → nothing to state
        (52.1, None, 3.0, None),
    ],
)
def test_min_draws_to_resolve(floor, n, target, expected):
    assert S.min_draws_to_resolve(floor, n, target) == expected


def test_min_draws_resolves_the_target_exactly_at_the_boundary():
    """At `min_draws` the floor is at or under the target, one fewer is not."""
    for w_min, target in ((52.1, 3.0), (6.0, 3.0), (0.25, 0.1), (168.0, 8.4)):
        m = S.min_draws_to_resolve(w_min, 1, target)
        assert w_min / m <= target
        assert m == 1 or w_min / (m - 1) > target


def test_below_floor_note_names_the_floor_and_the_runs_needed():
    verdict, note = S.certification_verdict(
        lole_ci=(0.0, 0.0), target_h=6.0, resolution_floor_h=104.2,
        n_samples=1, horizon_years=2.0)
    assert verdict == "inconclusive"
    # Per year: goal 3 h/yr, floor 52.1 h/yr at 1 draw; 104.2 / 6 → 18 draws.
    assert note == ("target 3 h/yr is below the MC resolution floor 52.1 h/yr "
                    "at 1 draw: at least 18 draws resolve it")


def test_below_floor_note_for_a_zero_goal_says_no_draw_count_resolves_it():
    _v, note = S.certification_verdict(
        lole_ci=(0.0, 0.0), target_h=0.0, resolution_floor_h=52.1, n_samples=1)
    assert note == ("target 0 h/yr is below the MC resolution floor 52.1 h/yr "
                    "at 1 draw: no draw count resolves a zero target")


@pytest.mark.parametrize("floor,n,target", [
    (52.1, 1, None), (float("inf"), 1, 3.0), (52.1, 1, float("nan")),
    (52.1, 1, -1.0)])
def test_min_draws_has_no_answer_without_a_finite_positive_target(floor, n, target):
    assert S.min_draws_to_resolve(floor, n, target) is None


def test_min_draws_compares_as_the_floor_does_not_by_rounded_ratio():
    # 2.1 / 0.15 is 14.000000000000002 in floats, but 2.1 / 14 == 0.15: the
    # MC's floor at 14 draws is exactly the target, so 14 resolve it.
    assert 2.1 / 0.15 > 14 and 2.1 / 14 == 0.15
    assert S.min_draws_to_resolve(2.1, 1, 0.15) == 14


def test_one_draw_suffices_when_the_weight_is_under_the_target():
    assert S.min_draws_to_resolve(0.5, 1, 3.0) == 1


# ── the study start's runs check, on a network without a solve ─────────────


def _weighted_network(hours: int = 168, weight: float = 1.0):
    import pypsa
    n = pypsa.Network()
    n.set_snapshots(range(hours))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Bus", "hub")
    n.add("Generator", "g", bus="hub", p_nom=10.0)
    return n


def _goal_pack(target, draws=500):
    from models.energy_hub import AvailabilityTarget, default_off_grid_pack
    return default_off_grid_pack().model_copy(update={
        "mc_draws": draws,
        "availability": AvailabilityTarget(
            ens_cap_permyriad=5000.0, target_lole_h=target,
            certification_metric="mc_lole" if target is not None else "none")})


def test_runs_check_refuses_below_and_passes_at_the_minimum():
    # 168 h of weight 1: 3 h/yr is 0.05753 h on the horizon; 1/18 ≤ it < 1/17.
    n = _weighted_network()
    msg = S.draws_short_of_target(n, _goal_pack(3.0), 17)
    assert msg == ("Simulation runs per check: 17 runs cannot tell whether the "
                   "3 h/yr goal is met (its smallest step is 3.07 h/yr): set at "
                   "least 18.")
    assert S.draws_short_of_target(n, _goal_pack(3.0), 18) is None
    # Unset draws are the pack's own.
    assert S.draws_short_of_target(n, _goal_pack(3.0, draws=17), None) == msg
    assert S.draws_short_of_target(n, _goal_pack(3.0, draws=18), None) is None


def test_runs_check_at_the_exact_boundary_passes():
    # Weight 2 h over 168 snapshots: horizon 336/8760 yr; a goal making
    # target_h exactly 2/4 = 0.5 h resolves at 4 draws, not 3.
    n = _weighted_network(weight=2.0)
    goal = 0.5 / (336 / 8760)
    assert S.draws_short_of_target(n, _goal_pack(goal), 4) is None
    assert "set at least 4." in S.draws_short_of_target(n, _goal_pack(goal), 3)


@pytest.mark.parametrize("target", [None, 0.0])
def test_runs_check_has_nothing_to_refuse_without_a_positive_goal(target):
    assert S.draws_short_of_target(_weighted_network(), _goal_pack(target), 1) is None


def test_runs_check_has_nothing_to_refuse_without_weights():
    n = _weighted_network(weight=0.0)
    assert S.draws_short_of_target(n, _goal_pack(3.0), 1) is None


def test_runs_check_ignores_zero_weight_snapshots():
    # A snapshot of weight 0 is never sampled into the floor (mc: w > 0).
    n = _weighted_network()
    n.snapshot_weightings.loc[n.snapshots[0], :] = 0.0
    assert "set at least 18." in S.draws_short_of_target(n, _goal_pack(3.0 * 168 / 167), 1)


def test_below_floor_note_counts_one_draw_and_many_draws():
    """UX 3 on #1927: '1 draw', '18 draws' (the Expert panel shows this note)."""
    _v, one = S.certification_verdict(lole_ci=(0.0, 0.0), target_h=3.0,
                                      resolution_floor_h=52.1, n_samples=1)
    _v, many = S.certification_verdict(lole_ci=(0.0, 0.0), target_h=3.0,
                                       resolution_floor_h=52.1 / 18 * 1.5,
                                       n_samples=18)
    assert "at 1 draw:" in one and "at 1 draws" not in one
    assert "at 18 draws:" in many
