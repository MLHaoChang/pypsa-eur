"""
WP5 — ``evidence_only_document``: the code-only ``ReportDocument`` built from
the evidence collector's output (no prose, no model).

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(phase 1, ``mode: "evidence_only"``). The tests pin the ordering rule
``build_study_report`` states (gaps BEFORE anything numeric), the "never
omitted" rule for every ``REPORT_SECTIONS`` entry, ADR-0001 at the table
level, that a figure is referenced only when its PNG exists, and that the
document's hash is the collector's own.
"""
from __future__ import annotations

import json
from pathlib import Path

import pypsa

from models.energy_hub import REPORT_SECTIONS
from models.report import Callout, FigureRef, Field, ReportDocument, TableRef
from services.adequacy.study_report import build_study_report
from services.reports import formatting as F
from services.reports.assemble import evidence_only_document
from services.reports.docx_writer import SECTION_TITLES
from services.reports.evidence import (
    NO_DATA_HINTS,
    WORKSHEET_SECTION,
    collect_evidence,
    evidence_hash,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eh_archetypes"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _skeleton() -> dict:
    return json.loads((FIXTURES / "mvp_a_report_skeleton.json").read_text())


def _populated() -> dict:
    """The MVP-A skeleton with four sections established (copied, not imported)."""
    report = _skeleton()
    report.update({
        "ens_cap_permyriad": 10.0, "achieved_ens_permyriad": 8.25,
        "achieved_shed_hours": 12.5, "mc_lole_h": None,
        "cost_at_target_eur": 1_234_567.89, "period_basis": "single_period",
        "tea": {"lcoe_eur_per_mwh": 61.7, "lcoh_eur_per_kg": None,
                "notes": "LCOE = cost / served", "lcoh_status": "skipped",
                "lcoh_note": "LCOH skipped: the network has no electrolyser Links"},
    })
    s = report["sections"]
    s["target"] = {"status": "ok", "note": None, "payload": {
        "binding": "ens_cap",
        "system": {"cap_mwh": 990.0, "achieved_ens_mwh": None,
                   "achieved_shed_hours": 12.5},
        "metrics": {"demand_mwh": 99_000.0},
    }}
    s["sizing"] = {"status": "ok", "note": None, "payload": {
        "by_carrier": {"gas": 100.0, "wind": 25.0}, "total_p_nom_mw": 125.0}}
    s["frontier"] = {"status": "ok", "note": "3 points around 10‱", "payload": {
        "points": [
            {"target_permyriad": 40.0, "status": "ok", "period_basis": "single_period",
             "point": {"cap_mwh": 3960.0, "achieved_ens_mwh": 3900.0,
                       "achieved_shed_hours": 60.0,
                       "total_system_cost_eur": 900_000.0}},
            {"target_permyriad": 2.5, "status": "infeasible", "point": None},
        ],
        "knee_index": 0, "voll_eur_per_mwh": 3000.0, "warning": None,
        "period_basis": "single_period", "excludes_shed_cost": True,
        "engine": "lp_proxy",
    }}
    s["fmea_top"] = {"status": "ok", "note": "ranked 2 modes", "payload": {
        "top": [
            {"rank": 1, "mode_id": "gen:backup:forced_outage",
             "component_class": "Generator", "name": "backup",
             "failure_class": "A", "occurrence_per_year": 2.5,
             "occurrence_basis": "FOR", "severity_eur": 40_000.0,
             "criticality_eur_per_year": 100_000.0, "delta_eue_mwh": None,
             "engine": "copt", "fidelity": "analytic_convolution"},
        ],
        "top_n": 10, "n_total_modes": 1, "classes_included": ["A"],
        "class_b": {"status": "skipped", "reason": "budget exhausted"},
        "voll_eur_per_mwh": 3000.0,
        "note": "Link-primary residual risk (Class-B Link sweep)",
    }}
    s["certification"] = {"status": "skipped",
                          "note": "budget exhausted before mc_certify",
                          "payload": None}
    for name in ("target", "sizing", "frontier", "fmea_top"):
        report["completeness"][name] = "ok"
    report["completeness"]["certification"] = "skipped"
    return report


def _worksheet() -> dict:
    return {"__schema__": 1, "version": 1, "overlays": {}, "manual_rows": [{
        "mode_id": "expert:scada", "component_class": "ControlSystem",
        "name": "SCADA outage", "failure_class": "D",
        "occurrence_per_year": 0.2, "occurrence_basis": "expert",
        "severity_eur": 500_000.0, "criticality_eur_per_year": 100_000.0,
        "engine": "expert", "fidelity": "expert_judgement",
        "rate_source": "operator interview",
    }]}


def _study_report(**payloads) -> dict:
    def read(section: str):
        payload = payloads.get(section)
        if payload is None:
            return {"status": "no_data", "message": f"no {section} yet"}
        return payload
    return build_study_report(pypsa.Network(), read)


def _doc(eh=None, study=None, worksheet=None, figure_pngs=None,
         title="Client report") -> tuple[ReportDocument, object]:
    ev = collect_evidence(study_report=study, eh_report=eh, worksheet=worksheet)
    doc = evidence_only_document(
        ev, title=title, report_id="0123456789abcdef",
        figure_pngs=figure_pngs or {})
    return doc, ev


def _by_id(doc: ReportDocument) -> dict:
    return {s.section_id: s for s in doc.sections}


# ── shape ────────────────────────────────────────────────────────────────


def test_document_is_evidence_only_code_sourced_and_hashed_by_the_collector():
    doc, ev = _doc(eh=_populated())
    assert doc.mode == "evidence_only"
    assert doc.report_id == "0123456789abcdef"
    assert doc.version == 1
    assert doc.title == "Client report"
    assert doc.profile_id is None and doc.model is None
    assert all(s.source == "code" for s in doc.sections)
    assert doc.evidence_hash == evidence_hash(ev)
    assert doc.schema_version == 1
    # Round-trips through the store's JSON boundary.
    ReportDocument.model_validate_json(doc.model_dump_json())


def test_tables_are_the_evidence_tables_with_the_same_ids_and_cells():
    doc, ev = _doc(eh=_populated(), worksheet=_worksheet())
    assert set(doc.tables) == set(ev.tables)
    for table_id, t in ev.tables.items():
        got = doc.tables[table_id]
        assert got.table_id == table_id
        assert got.columns == t.columns
        assert got.rows == t.rows
        assert got.caption == t.caption
        assert got.source_path == t.source_path


# ── ordering: gaps before anything numeric ───────────────────────────────


def test_executive_summary_puts_gaps_then_disclosures_before_the_headline():
    study = _study_report()
    study["evidence_gaps"] = [
        {"kind": "input_data", "code": "frozen_profile", "subject": "Load L1",
         "detail": "the demand profile is a frozen constant"},
        {"kind": "topology", "code": "island_no_supply", "subject": "island 2",
         "detail": "no generator or import can serve it"},
    ]
    doc, ev = _doc(eh=_populated(), study=study)
    summary = doc.sections[0]
    assert summary.section_id == "executive_summary"
    kinds = [(b.type, getattr(b, "kind", None)) for b in summary.blocks]
    first_gap = kinds.index(("callout", "gap"))
    first_disc = kinds.index(("callout", "disclosure"))
    first_table = kinds.index(("table_ref", None))
    assert first_gap < first_disc < first_table
    gap_texts = [b.text for b in summary.blocks if b.type == "callout" and b.kind == "gap"]
    assert len(gap_texts) == len(ev.evidence_gaps)
    assert all(g["detail"] in t for g, t in zip(ev.evidence_gaps, gap_texts))
    disc_texts = [b.text for b in summary.blocks
                  if b.type == "callout" and b.kind == "disclosure"]
    assert disc_texts == ev.required_disclosures
    headline = next(b for b in summary.blocks if b.type == "table_ref")
    assert headline.table_id == "headline"


# ── every EH section present, with the right status ──────────────────────


def test_every_report_section_is_present_in_order_with_its_status():
    doc, ev = _doc(eh=_populated())
    ids = [s.section_id for s in doc.sections]
    eh_ids = [i for i in ids if i in REPORT_SECTIONS]
    assert eh_ids == list(REPORT_SECTIONS)
    by = _by_id(doc)
    for name in REPORT_SECTIONS:
        s = by[name]
        assert s.heading == SECTION_TITLES[name]
        assert s.status == ev.sections[name].status, name
        assert s.source == "code"
    assert by["fmea_top"].status == "ok"
    assert by["certification"].status == "skipped"
    assert by["multi_energy"].status == "not_established"


def test_an_ok_section_references_its_table_and_carries_its_note():
    doc, _ = _doc(eh=_populated())
    fmea = _by_id(doc)["fmea_top"]
    refs = [b for b in fmea.blocks if isinstance(b, TableRef)]
    assert [r.table_id for r in refs] == ["fmea_top"]
    notes = [b.text for b in fmea.blocks
             if isinstance(b, Callout) and b.kind == "disclosure"]
    assert any("ranked 2 modes" in t for t in notes)
    assert any("Link-primary" in t for t in notes)
    assert not any(isinstance(b, Callout) and b.kind == "not_established"
                   for b in fmea.blocks)


def test_a_skipped_or_missing_section_has_exactly_one_not_established_callout():
    doc, _ = _doc(eh=_populated())
    by = _by_id(doc)
    cert = by["certification"]
    callouts = [b for b in cert.blocks if isinstance(b, Callout)]
    assert len(callouts) == 1 and callouts[0].kind == "not_established"
    assert "budget exhausted before mc_certify" in callouts[0].text
    assert F.fmt_status("skipped") in callouts[0].text
    assert cert.note == "budget exhausted before mc_certify"
    assert not any(isinstance(b, (TableRef, FigureRef)) for b in cert.blocks)
    missing = by["multi_energy"]
    callouts = [b for b in missing.blocks if isinstance(b, Callout)]
    assert len(callouts) == 1 and callouts[0].kind == "not_established"


def test_no_eh_report_at_all_states_every_section_with_the_tool_hint():
    doc, _ = _doc()
    by = _by_id(doc)
    for name in REPORT_SECTIONS:
        s = by[name]
        assert s.status == "not_established"
        callouts = [b for b in s.blocks if isinstance(b, Callout)]
        assert len(callouts) == 1 and callouts[0].kind == "not_established"
        assert "run_eh_study" in callouts[0].text
    assert "headline" not in doc.tables
    summary = doc.sections[0]
    texts = [b.text for b in summary.blocks if isinstance(b, Callout)]
    assert any("Nothing was measured" in t for t in texts)
    assert any(NO_DATA_HINTS["eh_reference_design"] in t for t in texts)


def test_an_ok_section_without_an_evidence_table_gets_field_blocks_not_zeros():
    doc, _ = _doc(eh=_populated())
    target = _by_id(doc)["target"]
    fields = {b.key: b.value for b in target.blocks if isinstance(b, Field)}
    assert fields, "target has no evidence table, so its scalars become fields"
    assert fields["binding"] == "ens_cap"
    assert fields["system / cap_mwh"] == F.fmt_number(990.0)
    # ADR-0001 at the field level.
    assert fields["system / achieved_ens_mwh"] == F.NOT_ESTABLISHED
    assert not any(v in ("0", "0.0", "0.00", "0.000") for v in fields.values())


# ── ADR-0001: None never becomes 0 ───────────────────────────────────────


def test_a_none_payload_value_never_yields_a_zero_cell():
    doc, _ = _doc(eh=_populated())
    fmea = doc.tables["fmea_top"]
    delta_col = fmea.columns.index("ΔEUE (MWh)")
    assert fmea.rows[0][delta_col] == F.NOT_ESTABLISHED
    headline = doc.tables["headline"]
    lole = next(r for r in headline.rows if r[0].startswith("MC LOLE"))
    assert lole[1] == F.NOT_ESTABLISHED
    # The pipeline's "solves charged: 0" is a legitimate zero; every other
    # table came from a payload where None must not have become one.
    for t in doc.tables.values():
        if t.table_id == "pipeline":
            continue
        for row in t.rows:
            assert "0" not in row, (t.table_id, row)


# ── figures: only when a PNG was produced ────────────────────────────────


def test_figures_are_referenced_only_when_their_png_was_passed():
    without, _ = _doc(eh=_populated())
    assert without.figures == {}
    assert not any(isinstance(b, FigureRef)
                   for s in without.sections for b in s.blocks)

    with_pngs, _ = _doc(eh=_populated(),
                        figure_pngs={"fmea_pareto": _PNG, "capacity_mix": _PNG})
    assert set(with_pngs.figures) == {"fmea_pareto", "capacity_mix"}
    assert with_pngs.figures["fmea_pareto"].png_file == "figures/fmea_pareto.png"
    assert with_pngs.figures["fmea_pareto"].caption
    by = _by_id(with_pngs)
    assert [b.figure_id for b in by["fmea_top"].blocks if isinstance(b, FigureRef)] \
        == ["fmea_pareto"]
    assert [b.figure_id for b in by["sizing"].blocks if isinstance(b, FigureRef)] \
        == ["capacity_mix"]
    # The frontier PNG was not produced → no reference, no figure entry.
    assert not any(isinstance(b, FigureRef) for b in by["frontier"].blocks)
    # Every referenced figure id is in doc.figures and vice versa.
    referenced = {b.figure_id for s in with_pngs.sections for b in s.blocks
                  if isinstance(b, FigureRef)}
    assert referenced == set(with_pngs.figures)


def test_a_png_for_an_unestablished_section_is_not_referenced():
    doc, _ = _doc(eh=_skeleton(), figure_pngs={"fmea_pareto": _PNG})
    assert not any(isinstance(b, FigureRef) for s in doc.sections for b in s.blocks)
    assert doc.figures == {}


# ── adequacy, worksheet, pipeline ────────────────────────────────────────


def test_established_adequacy_sections_carry_engine_and_fidelity_fields():
    study = _study_report(
        copt={"engine": "copt", "fidelity": "analytic_convolution",
              "metrics": {"lole_hours": 4.0, "eue_mwh": None}},
    )
    doc, ev = _doc(study=study)
    by = _by_id(doc)
    assert "adequacy_surfaces" in by
    assert [b.table_id for b in by["adequacy_surfaces"].blocks
            if isinstance(b, TableRef)] == ["adequacy_sections"]
    copt = by["copt"]
    assert copt.status == "ok"
    fields = {b.key: b.value for b in copt.blocks if isinstance(b, Field)}
    assert fields["Engine"] == "copt"
    assert fields["Fidelity"] == "analytic_convolution"
    assert fields["metrics / eue_mwh"] == F.NOT_ESTABLISHED
    assert any(isinstance(b, Callout) and "screening only" in b.text
               for b in copt.blocks)
    # Surfaces that were never run are stated in the table, not as sections.
    assert "mc" not in by and "adequacy_frontier" not in by
    ids = [s.section_id for s in doc.sections]
    assert ids.index("adequacy_surfaces") < ids.index("copt")
    assert ids.index("multi_energy") < ids.index("adequacy_surfaces")


def test_worksheet_section_only_when_rows_exist_and_pipeline_last():
    doc, _ = _doc(eh=_populated())
    ids = [s.section_id for s in doc.sections]
    assert WORKSHEET_SECTION not in ids
    assert ids[-1] == "pipeline"
    assert [b.table_id for b in doc.sections[-1].blocks if isinstance(b, TableRef)] \
        == ["pipeline"]

    doc, _ = _doc(eh=_populated(), worksheet=_worksheet())
    ids = [s.section_id for s in doc.sections]
    assert WORKSHEET_SECTION in ids
    ws = _by_id(doc)[WORKSHEET_SECTION]
    assert ws.status == "ok"
    assert [b.table_id for b in ws.blocks if isinstance(b, TableRef)] \
        == [WORKSHEET_SECTION]
    assert ids.index(WORKSHEET_SECTION) < ids.index("pipeline")


def test_pipeline_without_a_record_is_stated_not_omitted():
    doc, _ = _doc()
    pipeline = doc.sections[-1]
    assert pipeline.section_id == "pipeline"
    assert pipeline.status == "not_established"
    assert [b.kind for b in pipeline.blocks if isinstance(b, Callout)] \
        == ["not_established"]
