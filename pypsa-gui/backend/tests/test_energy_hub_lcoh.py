"""
WP4 — LCOH in ``compute_tea`` via ``services.results.lcoh`` (spec decision 9).

Plan: docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md WP4
ADR-0001: an unresolvable LCOH is ``None`` + ``lcoh_status`` flag, never 0.
"""
from __future__ import annotations

import math
import queue
import threading

import pytest

from models.energy_hub import AvailabilityTarget, TeaBlock, default_strong_grid_pack
from services.adequacy import eh_report as R
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import (
    VOLL,
    certifiable_weak_network,
    electrolyser_network,
)


def test_tea_block_carries_lcoh_flag_fields():
    t = TeaBlock()
    assert t.lcoh_eur_per_kg is None
    assert t.lcoh_status is None
    assert t.lcoh_note is None


def test_compute_tea_without_network_is_unchanged():
    tea = R.compute_tea(cost_eur=9_720.0, served_energy_mwh=1_080.0)
    assert tea.lcoe_eur_per_mwh == pytest.approx(9.0)
    assert tea.lcoh_eur_per_kg is None
    assert tea.lcoh_status is None


def test_compute_tea_flags_skipped_without_electrolyser_links():
    n = certifiable_weak_network()
    assert R.has_electrolyser_links(n) is False
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=n)
    assert tea.lcoe_eur_per_mwh == pytest.approx(10.0)
    assert tea.lcoh_eur_per_kg is None
    assert tea.lcoh_status == "skipped"
    assert "no electrolyser" in (tea.lcoh_note or "")


def test_compute_tea_flags_not_established_when_links_never_ran():
    """Electrolyser Links exist but the network is unsolved → no H₂, no 0."""
    n = electrolyser_network()
    assert R.has_electrolyser_links(n) is True
    tea = R.compute_tea(cost_eur=100.0, served_energy_mwh=10.0, network=n,
                        cfg=SolverConfig(voll=VOLL))
    assert tea.lcoh_eur_per_kg is None
    assert tea.lcoh_status == "not_established"
    assert tea.lcoh_note


def _run(n, *, cap=10.0):
    from services.pypsa_service import PyPSAService

    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=cap),
    })
    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )


@pytest.mark.live_solve
def test_eh_study_tea_lcoh_finite_with_a_consuming_electrolyser():
    report = _run(electrolyser_network())
    assert report.completeness["tea"] == "ok"
    assert report.tea is not None
    assert report.tea.lcoh_status == "ok"
    assert report.tea.lcoh_eur_per_kg is not None
    assert math.isfinite(report.tea.lcoh_eur_per_kg)
    assert report.tea.lcoh_eur_per_kg > 0
    assert report.sections["tea"].payload["lcoh_status"] == "ok"


@pytest.mark.live_solve
def test_eh_study_tea_lcoh_null_with_flag_on_electrical_only_network():
    report = _run(certifiable_weak_network())
    assert report.completeness["tea"] == "ok"  # LCOE is established
    assert report.tea is not None
    assert report.tea.lcoe_eur_per_mwh is not None
    assert report.tea.lcoh_eur_per_kg is None
    assert report.tea.lcoh_status == "skipped"
    assert report.sections["tea"].payload["lcoh_eur_per_kg"] is None
    assert report.sections["tea"].payload["lcoh_status"] == "skipped"
