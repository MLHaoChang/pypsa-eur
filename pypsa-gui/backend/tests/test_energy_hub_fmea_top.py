"""
WP3 — ``fmea_top``: ranked residual failure modes on the fixed plan.

Plan: docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md WP3
Spec: decision 14 (Link-primary Class-B; SCLOPF rows omitted).

Class A comes from the frozen COPT screening (zero solves); Class B from the
Link outage sweep on frozen capacities when Links carry occurrence data and
the budget affords ``n + 2`` solves.
"""
from __future__ import annotations

import queue
import threading

import pytest

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import (
    VOLL,
    certifiable_weak_network,
    islanded_certify_network,
    no_occurrence_network,
)


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


def _pack(cap: float):
    return default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=cap),
    })


def test_note_is_owned_by_eh_stages_and_reexported_by_the_driver():
    assert S.FMEA_TOP_LINK_PRIMARY_NOTE is ST.FMEA_TOP_LINK_PRIMARY_NOTE
    assert "Link-primary" in ST.FMEA_TOP_LINK_PRIMARY_NOTE
    assert "SCLOPF" in ST.FMEA_TOP_LINK_PRIMARY_NOTE
    assert ST.FMEA_TOP_N == 10


def _assert_ranked(top: list[dict]) -> None:
    crits = [float(m["criticality_eur_per_year"]) for m in top]
    assert crits == sorted(crits, reverse=True)
    assert [m["rank"] for m in top] == list(range(1, len(top) + 1))
    for m in top:
        assert m["failure_class"] in ("A", "B")
        assert m["mode_id"] and m["name"] and m["component_class"]
        assert m["engine"] in ("copt", "lp_proxy")


@pytest.mark.live_solve
def test_fmea_top_ranks_class_a_and_class_b_on_the_fixed_plan():
    """
    The fixture's import Link carries occurrence data → one Class-B
    contingency; budget 30 affords base + 1 + restore.
    """
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    assert report.completeness["fmea_top"] == "ok"
    sec = report.sections["fmea_top"]
    top = sec.payload["top"]
    assert top
    _assert_ranked(top)
    assert set(sec.payload["classes_included"]) == {"A", "B"}
    assert any(m["component_class"] == "Link" and m["failure_class"] == "B"
               for m in top)
    assert any(m["component_class"] == "Generator" and m["failure_class"] == "A"
               for m in top)
    assert sec.payload["class_b"]["status"] == "run"
    assert sec.payload["class_b"]["base_restored"] is True
    assert sec.payload["note"] == ST.FMEA_TOP_LINK_PRIMARY_NOTE
    assert "Link-primary" in (sec.note or "") and "SCLOPF" in (sec.note or "")
    rec = next(s for s in report.pipeline.stages if s.stage == "fmea_top")
    assert rec.status == "run"
    assert rec.solves_charged == 1 + 1 + 1  # base + one Link + restore
    assert report.pipeline.solves_consumed == 1 + rec.solves_charged


@pytest.mark.live_solve
def test_fmea_top_falls_back_to_class_a_when_budget_cannot_afford_class_b():
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"),
                  budget=2)  # 1 left after ens_solve; Class B needs 3
    assert report.completeness["fmea_top"] == "ok"
    sec = report.sections["fmea_top"]
    assert sec.payload["classes_included"] == ["A"]
    assert all(m["failure_class"] == "A" for m in sec.payload["top"])
    assert sec.payload["class_b"]["status"] == "skipped"
    assert "budget" in sec.payload["class_b"]["reason"]
    rec = next(s for s in report.pipeline.stages if s.stage == "fmea_top")
    assert rec.status == "run"
    assert rec.solves_charged == 0
    assert report.pipeline.solves_consumed == 1 <= report.pipeline.budget_solves


@pytest.mark.live_solve
def test_fmea_top_class_a_only_when_no_link_carries_occurrence_data():
    n = islanded_certify_network()  # its PoC Link has no occurrence data
    report = _run(n, _pack(2000.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    assert report.completeness["fmea_top"] == "ok"
    sec = report.sections["fmea_top"]
    assert sec.payload["classes_included"] == ["A"]
    assert "no Link carries" in sec.payload["class_b"]["reason"]
    assert len(sec.payload["top"]) == 2  # base + peaker


@pytest.mark.live_solve
def test_fmea_top_not_established_when_fleet_has_no_occurrence_data():
    n = no_occurrence_network()
    report = _run(n, _pack(1000.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"),
                  cfg=SolverConfig(voll=150.0, ens_cap_permyriad=1000.0))
    assert report.completeness["fmea_top"] == "not_established"
    assert "occurrence" in (report.sections["fmea_top"].note or "")
    rec = next(s for s in report.pipeline.stages if s.stage == "fmea_top")
    assert rec.status == "skipped"


@pytest.mark.live_solve
def test_fmea_top_skipped_note_keeps_link_primary_wording():
    n = no_occurrence_network()
    report = _run(n, _pack(1000.0), stages=("apply_pack", "ens_solve", "assemble"),
                  cfg=SolverConfig(voll=150.0, ens_cap_permyriad=1000.0))
    assert report.completeness["fmea_top"] == "skipped"
    note = report.sections["fmea_top"].note or ""
    assert "Link-primary" in note and "SCLOPF" in note and "not requested" in note


@pytest.mark.live_solve
def test_the_pack_top_n_bounds_the_ranking():
    n = certifiable_weak_network()
    pack = _pack(10.0).model_copy(update={"fmea_top_n": 1})
    report = _run(n, pack, stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    payload = report.sections["fmea_top"].payload
    assert payload["top_n"] == 1
    assert len(payload["top"]) == 1
    assert payload["n_total_modes"] > 1
