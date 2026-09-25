"""
P1.5 — EH study orchestrator (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §5, decisions 16–18
Plan: Phase 1.5

Sync driver first (testable). HTTP start_* runner wires later like frontier_loop_runner.
Report emission MUST go through assemble_reference_design_report only.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    DEFAULT_EH_BUDGET_SOLVES,
    EH_PIPELINE_STAGES,
    AvailabilityTarget,
    ReferenceDesignReport,
    default_strong_grid_pack,
)
from services.adequacy import eh_report as R
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig


def _ens_bind_network() -> pypsa.Network:
    weight, snaps, load_mw, cheap_mw = 3.0, 4, 100.0, 60.0
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snaps, freq="h"))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=load_mw)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=cheap_mw, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=load_mw - cheap_mw, marginal_cost=200.0)
    return n


def test_assemble_is_only_report_builder_entry_point():
    assert callable(R.assemble_reference_design_report)


def test_default_pipeline_stage_order():
    assert tuple(S.DEFAULT_STAGES) == EH_PIPELINE_STAGES


def test_budget_defaults_match_contract():
    assert S.DEFAULT_EH_BUDGET_SOLVES == DEFAULT_EH_BUDGET_SOLVES


def test_run_skips_optional_stages_as_skipped():
    """MVP-A: frontier/MC/redundancy/dtc not requested → completeness skipped."""
    from services.pypsa_service import PyPSAService

    n = _ens_bind_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n,
        pack,
        SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert isinstance(report, ReferenceDesignReport)
    assert report.completeness["frontier"] == "skipped"
    assert report.completeness["redundancy"] == "skipped"
    assert report.completeness["dtc"] == "skipped"
    assert report.completeness["cost"] == "ok"
    assert report.cost_at_target_eur is not None
    assert report.excludes_shed_cost is True


def test_run_marks_not_established_when_required_mc_missing():
    from services.pypsa_service import PyPSAService

    n = _ens_bind_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=1000.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "mc_certify_required": True,
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),  # mc omitted
    )
    mc_rec = next(s for s in report.pipeline.stages if s.stage == "mc_certify")
    assert mc_rec.status == "skipped"
    assert mc_rec.note and "required" in mc_rec.note
    # P11: missing required certification is reported on its own section;
    # `gates` is the SCR dynamics gate only (skipped outside weak_flexible).
    assert report.completeness["certification"] == "not_established"
    assert "required" in (report.sections["certification"].note or "")
    assert report.certified is None
    assert report.completeness["gates"] == "skipped"


def test_default_stages_never_leave_unimplemented_pending():
    from services.pypsa_service import PyPSAService

    n = _ens_bind_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=None,  # full default pipeline
    )
    for rec in report.pipeline.stages:
        assert rec.status != "pending", rec
    assert report.completeness["frontier"] == "skipped"
    assert report.completeness["cost"] == "ok"


def test_abort_before_solve_restores_pack_and_flags_pipeline():
    from services.pypsa_service import PyPSAService
    from models.energy_hub import default_off_grid_pack

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "demand", bus="hub", p_set=10.0)
    n.add("Generator", "local", bus="hub", carrier="gas",
          p_nom=50.0, marginal_cost=10.0)
    n.add("Link", "import", bus0="grid", bus1="hub", p_nom=100.0,
          efficiency=1.0, eh_role="grid_import")
    n.add("Generator", "grid_supply", bus="grid", carrier="gas",
          p_nom=100.0, marginal_cost=5.0)

    pack = default_off_grid_pack()

    class _Flip(threading.Event):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_set(self):
            self.checks += 1
            # Allow apply_pack (1st check); abort before ens_solve (2nd).
            return self.checks > 1

    stop = _Flip()
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=3000.0),
        lock=PyPSAService.get_lock(),
        stop_event=stop,
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report.pipeline.aborted is True
    # Undo must restore import availability after off_grid apply + abort.
    assert float(n.links.at["import", "p_max_pu"]) == 1.0


@pytest.mark.live_solve
def test_mvp_a_strong_grid_study_binds_and_reports():
    from services.pypsa_service import PyPSAService

    n = _ens_bind_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report.archetype == "strong_grid"
    assert report.completeness["target"] == "ok"
    assert report.completeness["cost"] == "ok"
    assert report.achieved_ens_permyriad is not None or report.sections[
        "target"].payload is not None


def test_fmea_top_skip_note_is_link_primary_residual_risk():
    """P2 / spec decision 14: EH report omits SCLOPF merge; label Link-primary."""
    from services.pypsa_service import PyPSAService

    n = _ens_bind_network()
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    note = report.sections["fmea_top"].note or ""
    assert "Link-primary" in note
    assert "SCLOPF" in note
    assert S.FMEA_TOP_LINK_PRIMARY_NOTE.split(";")[0] in note
