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
    from models.energy_hub import DEFAULT_EH_FMEA_TOP_N

    assert S.FMEA_TOP_LINK_PRIMARY_NOTE is ST.FMEA_TOP_LINK_PRIMARY_NOTE
    assert "Link-primary" in ST.FMEA_TOP_LINK_PRIMARY_NOTE
    assert "SCLOPF" in ST.FMEA_TOP_LINK_PRIMARY_NOTE
    # Merge 2026-09-28: the default N is the P12 spec amendment's top-5.
    assert ST.FMEA_TOP_N == S.FMEA_TOP_N == DEFAULT_EH_FMEA_TOP_N == 5


def _assert_ranked(top: list[dict], *, ranked: bool = True) -> None:
    crits = [float(m["criticality_eur_per_year"]) for m in top]
    assert crits == sorted(crits, reverse=True)
    if ranked:
        assert [m["rank"] for m in top] == list(range(1, len(top) + 1))
    for m in top:
        assert m["failure_class"] in ("A", "B")
        assert m["mode_id"] and m["name"] and m["component_class"]
        assert m["engine"] in ("copt", "lp_proxy")


# Merge 2026-09-28: fmea_top keeps the P12 contract — the Class-B Link
# ranking in ``rows`` (private copy, no closing restore, no partial sweep)
# decides the section — and carries master's class-A COPT screening of the
# same plan in ``class_a`` (zero solves). The cases below are master's,
# on that shape.


@pytest.mark.live_solve
def test_fmea_top_ranks_class_a_and_class_b_on_the_fixed_plan():
    """
    The fixture's import Link carries occurrence data → one Class-B
    contingency; budget 30 affords the frozen base + 1 (no restore, Q4).
    """
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    assert report.completeness["fmea_top"] == "ok"
    sec = report.sections["fmea_top"]
    rows = sec.payload["rows"]
    class_a = sec.payload["class_a"]
    assert rows and class_a["rows"]
    _assert_ranked(rows, ranked=False)
    _assert_ranked(class_a["rows"])
    assert any(m["component_class"] == "Link" and m["failure_class"] == "B"
               for m in rows)
    assert any(m["component_class"] == "Generator" and m["failure_class"] == "A"
               for m in class_a["rows"])
    assert class_a["status"] == "ok" and class_a["solves_charged"] == 0
    assert "Link-primary" in (sec.note or "") and "SCLOPF" in (sec.note or "")
    rec = next(s for s in report.pipeline.stages if s.stage == "fmea_top")
    assert rec.status == "run"
    assert rec.solves_charged == 1 + 1  # frozen base + one Link, no restore
    assert report.pipeline.solves_consumed == 1 + rec.solves_charged


@pytest.mark.live_solve
def test_fmea_top_falls_back_to_class_a_when_budget_cannot_afford_class_b():
    n = certifiable_weak_network()
    report = _run(n, _pack(10.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"),
                  budget=2)  # 1 left after ens_solve; Class B needs 2
    # P12: no partial Link ranking — the section is not established …
    assert report.completeness["fmea_top"] == "not_established"
    sec = report.sections["fmea_top"]
    assert "budget" in (sec.note or "")
    # … but the zero-solve class-A screening still reports.
    class_a = sec.payload["class_a"]
    assert class_a["status"] == "ok"
    assert class_a["rows"] and all(m["failure_class"] == "A"
                                   for m in class_a["rows"])
    rec = next(s for s in report.pipeline.stages if s.stage == "fmea_top")
    assert rec.solves_charged == 0
    assert report.pipeline.solves_consumed == 1 <= report.pipeline.budget_solves


@pytest.mark.live_solve
def test_fmea_top_class_a_only_when_no_link_carries_occurrence_data():
    n = islanded_certify_network()  # its PoC Link has no occurrence data
    report = _run(n, _pack(2000.0),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    assert report.completeness["fmea_top"] == "not_established"
    sec = report.sections["fmea_top"]
    assert "no Class-B-eligible Links" in (sec.note or "")
    assert sec.payload["rows"] == []
    assert len(sec.payload["class_a"]["rows"]) == 2  # base + peaker


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
    assert len(payload["rows"]) == 1
    assert len(payload["class_a"]["rows"]) == 1
    assert payload["class_a"]["n_modes"] > 1
