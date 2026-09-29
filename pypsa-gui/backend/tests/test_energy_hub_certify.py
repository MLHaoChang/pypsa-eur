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
