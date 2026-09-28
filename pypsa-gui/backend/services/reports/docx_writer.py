"""
Render the Energy Hub ``ReferenceDesignReport`` as a Word document.

WP0 of docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md.
No language model is involved: every cell comes from the report dict the
assembler produced (``export_reference_design``), formatted by
``services.reports.formatting`` so nothing missing becomes a zero, and every
one of ``REPORT_SECTIONS`` is present in the document — an unestablished
section is one sentence saying so, with the stage's note.

Template handling: ``template`` may be ``None`` (the code-built default),
bytes, or a path. The writer keeps the template's section properties
(page size, margins, headers, footers) and styles, clears its body, and
writes the report into it — the same path a user template will take in
increment 2.
"""
from __future__ import annotations

import copy
import io
import json
import pathlib
from typing import Any
from collections.abc import Callable

from docx import Document
from docx.shared import Inches

from models.energy_hub import REPORT_SECTIONS
from services.reports import formatting as F
from services.reports.default_template import (
    CAPTION_STYLE,
    DISCLOSURE_STYLE,
    default_template_document,
    ensure_report_styles,
)

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

SECTION_TITLES: dict[str, str] = {
    "target": "Availability target and achieved adequacy",
    "certification": "Certification (sequential Monte Carlo)",
    "cost": "Cost at target",
    "frontier": "Cost-vs-availability frontier",
    "sizing": "Sizing of the least-cost plan",
    "redundancy": "Redundancy options",
    "levers": "Optimisation levers",
    "dtc": "Critical-load (DtC) stress",
    "fmea_top": "Residual failure modes (FMEA top-N)",
    "tea": "Techno-economic summary (LCOE / LCOH)",
    "gates": "Dynamics gates",
    "multi_energy": "Multi-energy adequacy",
}

_MAX_CELL_CHARS = 200


# ── document plumbing ────────────────────────────────────────────────────


def _open_template(template: Any):
    if template is None:
        return default_template_document()
    if isinstance(template, (bytes, bytearray)):
        doc = Document(io.BytesIO(bytes(template)))
    else:
        doc = Document(str(pathlib.Path(template)))
    ensure_report_styles(doc)
    _clear_body(doc)
    return doc


def _clear_body(doc) -> None:
    """Drop every body element except the final section properties."""
    body = doc.element.body
    for child in list(body):
        if child.tag.endswith("}sectPr"):
            continue
        body.remove(child)


def _para(doc, text: str, style: str | None = None):
    p = doc.add_paragraph(text)
    if style:
        try:
            p.style = doc.styles[style]
        except KeyError:
            pass
    return p


def _disclosure(doc, text: str | None):
    if text:
        _para(doc, str(text), DISCLOSURE_STYLE)


def _caption(doc, text: str):
    _para(doc, text, CAPTION_STYLE)


def _table(doc, header: list[str], rows: list[list[str]],
           caption: str | None = None):
    table = doc.add_table(rows=1, cols=len(header))
    try:
        table.style = doc.styles["Table Grid"]
    except KeyError:
        pass
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = text
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = text
    if caption:
        _caption(doc, caption)
    else:
        doc.add_paragraph()
    return table


def _kv_table(doc, rows: list[tuple[str, str]], caption: str | None = None):
    return _table(doc, ["Item", "Value"], [[k, v] for k, v in rows], caption)


def _cell(value: Any) -> str:
    """A generic payload leaf as a table cell."""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str, separators=(",", ":"))
        if len(text) > _MAX_CELL_CHARS:
            text = text[:_MAX_CELL_CHARS - 1] + "…"
        return text
    if isinstance(value, bool) or value is None:
        return F.fmt_number(value)
    if isinstance(value, (int, float)):
        return F.fmt_number(value)
    return F.fmt_text(value)


def _picture(doc, png: bytes, caption: str | None = None):
    doc.add_picture(io.BytesIO(png), width=Inches(6.0))
    if caption:
        _caption(doc, caption)


# ── headline ─────────────────────────────────────────────────────────────


def _title_block(doc, report: dict) -> None:
    doc.add_heading("Energy Hub reference design report", level=0)
    _para(doc, f"Archetype: {F.fmt_text(report.get('archetype'))}")
    _disclosure(doc, (
        "Every figure in this document was computed by the study engines "
        "and rendered by code. Sections the study did not establish say so."))


def _headline_table(doc, report: dict) -> None:
    tea = report.get("tea") or {}
    cost = F.fmt_number(report.get("cost_at_target_eur"), unit="€")
    basis = report.get("period_basis")
    if cost != F.NOT_ESTABLISHED:
        cost += f" ({F.fmt_text(basis)}; excludes load-shedding cost)"
    lcoh = F.fmt_number(tea.get("lcoh_eur_per_kg"), unit="€/kg", digits=2)
    if lcoh == F.NOT_ESTABLISHED and tea.get("lcoh_note"):
        lcoh = f"{F.NOT_ESTABLISHED} — {tea['lcoh_note']}"
    rows = [
        ("ENS cap (‱ of demand)", F.fmt_number(report.get("ens_cap_permyriad"), digits=2)),
        ("Achieved ENS (‱ of demand)", F.fmt_number(report.get("achieved_ens_permyriad"), digits=2)),
        ("Achieved shed hours (h/yr)", F.fmt_number(report.get("achieved_shed_hours"), digits=1)),
        ("MC LOLE (h/yr)", F.fmt_number(report.get("mc_lole_h"), unit="h/yr", digits=2)),
        ("Cost at target", cost),
        ("LCOE", F.fmt_number(tea.get("lcoe_eur_per_mwh"), unit="€/MWh", digits=1)),
        ("LCOH", lcoh),
        ("Pack hash", F.fmt_text(report.get("pack_hash"))),
        ("Assumptions hash", F.fmt_text(report.get("assumptions_hash"))),
    ]
    _kv_table(doc, rows, "Table 1 — Headline results of the reference design")


def _completeness_table(doc, report: dict) -> None:
    completeness = report.get("completeness") or {}
    rows = [[SECTION_TITLES.get(name, name), F.fmt_status(completeness.get(name))]
            for name in REPORT_SECTIONS]
    _table(doc, ["Section", "Status"], rows,
           "Table 2 — What this study established, and what it did not")


# ── section renderers: (doc, payload, note, report) ──────────────────────


def _render_target(doc, payload: dict, note, report) -> None:
    system = payload.get("system") or {}
    metrics = payload.get("metrics") or {}
    rows = [
        ("Binding target", F.fmt_text(payload.get("binding"))),
        ("ENS cap (MWh)", F.fmt_number(system.get("cap_mwh"))),
        ("Achieved ENS (MWh)", F.fmt_number(system.get("achieved_ens_mwh"))),
        ("Achieved shed hours (h/yr)", F.fmt_number(system.get("achieved_shed_hours"), digits=1)),
        ("Demand (MWh)", F.fmt_number(metrics.get("demand_mwh"))),
    ]
    _kv_table(doc, rows)


def _render_certification(doc, payload: dict, note, report) -> None:
    rows = [
        ("Metric", F.fmt_text(payload.get("metric"))),
        ("Target LOLE", F.fmt_number(payload.get("target_lole_h"), unit="h/yr", digits=2)),
        ("MC LOLE", F.fmt_number(payload.get("mc_lole_h"), unit="h/yr", digits=2)),
        ("LOLE interval", F.fmt_ci(payload.get("lole_ci"), unit="h/yr")),
        ("EUE", F.fmt_number(payload.get("eue_mwh"), unit="MWh", digits=1)),
        ("EUE interval", F.fmt_ci(payload.get("eue_ci"), unit="MWh", digits=1)),
        ("Draws (sampled / requested)",
         f"{F.fmt_number(payload.get('n_samples'))} / {F.fmt_number(payload.get('draws_requested'))}"),
        ("Converged", F.fmt_number(payload.get("converged"))),
        ("ENS target met by the plan", F.fmt_number(payload.get("ens_met"))),
        ("Verdict", F.fmt_text(payload.get("verdict"))),
        ("Engine / fidelity",
         f"{F.fmt_text(payload.get('engine'))} / {F.fmt_text(payload.get('fidelity'))}"),
    ]
    _kv_table(doc, rows)
    _disclosure(doc, payload.get("warning"))


def _render_cost(doc, payload: dict, note, report) -> None:
    rows = [
        ("Total system cost", F.fmt_number(payload.get("total_system_cost_eur"), unit="€")),
        ("Period basis", F.fmt_text(payload.get("period_basis"))),
        ("Excludes load-shedding cost", F.fmt_number(payload.get("excludes_shed_cost", True))),
    ]
    _kv_table(doc, rows)


def _render_frontier(doc, payload: dict, note, report) -> None:
    knee = payload.get("knee_index")
    rows = []
    for i, pt in enumerate(payload.get("points") or []):
        point = pt.get("point") or {}
        status = F.fmt_text(pt.get("status"))
        if knee is not None and i == knee:
            status += " (knee)"
        rows.append([
            F.fmt_number(pt.get("target_permyriad"), digits=2),
            status,
            F.fmt_number(point.get("total_system_cost_eur"), unit="€"),
            F.fmt_number(point.get("achieved_ens_mwh")),
            F.fmt_number(point.get("achieved_shed_hours"), digits=1),
        ])
    basis = F.fmt_text(payload.get("period_basis"))
    _table(doc, ["Target (‱)", "Status", "Total system cost", "ENS (MWh)",
                 "Shed hours (h/yr)"], rows,
           f"Cost of reliability, one full expansion solve per target; "
           f"{basis}; excludes load-shedding cost; "
           f"VoLL {F.fmt_number(payload.get('voll_eur_per_mwh'), unit='€/MWh')}")
    _disclosure(doc, payload.get("warning"))


def _render_sizing(doc, payload: dict, note, report) -> None:
    by_carrier = payload.get("by_carrier") or {}
    rows = [[str(k), F.fmt_number(v, unit="MW", digits=1)]
            for k, v in by_carrier.items()]
    rows.append(["Total", F.fmt_number(payload.get("total_p_nom_mw"), unit="MW", digits=1)])
    _table(doc, ["Carrier", "Installed capacity"], rows)


def _render_fmea_top(doc, payload: dict, note, report) -> None:
    rows = []
    for r in payload.get("top") or []:
        rows.append([
            F.fmt_number(r.get("rank")),
            F.fmt_text(r.get("failure_class")),
            F.fmt_text(r.get("component_class")),
            F.fmt_text(r.get("name")),
            F.fmt_number(r.get("occurrence_per_year"), unit="/yr", digits=2),
            F.fmt_number(r.get("severity_eur"), unit="€"),
            F.fmt_number(r.get("criticality_eur_per_year"), unit="€/yr"),
            F.fmt_number(r.get("delta_eue_mwh"), digits=2),
        ])
    classes = F.fmt_text(payload.get("classes_included"))
    _table(doc, ["Rank", "Class", "Component", "Name", "Occurrence",
                 "Severity", "Criticality", "ΔEUE (MWh)"], rows,
           f"Ranked by criticality (€/yr) then mode id; classes included: "
           f"{classes}; {F.fmt_number(payload.get('n_total_modes'))} modes in "
           f"total; VoLL {F.fmt_number(payload.get('voll_eur_per_mwh'), unit='€/MWh')}")
    _disclosure(doc, payload.get("note"))
    class_b = payload.get("class_b") or {}
    if class_b:
        line = f"Class-B Link sweep: {F.fmt_text(class_b.get('status'))}"
        if class_b.get("reason"):
            line += f" — {class_b['reason']}"
        _disclosure(doc, line)
    _disclosure(doc, payload.get("copt_fidelity_note"))


def _render_tea(doc, payload: dict, note, report) -> None:
    tea = report.get("tea") or payload or {}
    rows = [
        ("LCOE", F.fmt_number(tea.get("lcoe_eur_per_mwh"), unit="€/MWh", digits=1)),
        ("LCOH", F.fmt_number(tea.get("lcoh_eur_per_kg"), unit="€/kg", digits=2)),
        ("LCOH status", F.fmt_status(tea.get("lcoh_status"))),
    ]
    _kv_table(doc, rows)
    _disclosure(doc, tea.get("notes"))
    _disclosure(doc, tea.get("lcoh_note"))


def _render_gates(doc, payload: dict, note, report) -> None:
    gates = report.get("gates") or payload or {}
    rows = [
        ("Short-circuit ratio gate", F.fmt_text(gates.get("scr"))),
        ("EMT study recommended", F.fmt_number(gates.get("emt_recommended"))),
    ]
    _kv_table(doc, rows)


def _render_generic(doc, payload: dict, note, report) -> None:
    rows = [(str(k), _cell(v)) for k, v in payload.items()]
    _kv_table(doc, rows)


_RENDERERS: dict[str, Callable] = {
    "target": _render_target,
    "certification": _render_certification,
    "cost": _render_cost,
    "frontier": _render_frontier,
    "sizing": _render_sizing,
    "fmea_top": _render_fmea_top,
    "tea": _render_tea,
    "gates": _render_gates,
}

_FIGURE_CAPTIONS = {
    "fmea_top": "Figure — criticality of the ranked residual failure modes "
                "(€/yr), cumulative share annotated",
}


def _render_section(doc, name: str, report: dict,
                    figures: dict[str, bytes] | None) -> None:
    doc.add_heading(SECTION_TITLES.get(name, name), level=1)
    section = (report.get("sections") or {}).get(name) or {}
    status = section.get("status") or "not_established"
    note = section.get("note")
    payload = section.get("payload")
    # tea / gates live on the report as blocks even when the section payload
    # is empty; treat a present block as established evidence.
    block_present = name in ("tea", "gates") and bool(report.get(name))
    if status != "ok" or (not payload and not block_present):
        sentence = f"This section was {F.fmt_status(status)}."
        if note:
            sentence += f" {note}"
        _para(doc, sentence)
        return
    _RENDERERS.get(name, _render_generic)(doc, payload or {}, note, report)
    if note:
        _disclosure(doc, f"Stage note: {note}")
    png = (figures or {}).get(name)
    if png:
        _picture(doc, png, _FIGURE_CAPTIONS.get(name))


def _render_worksheet(doc, worksheet: dict | None) -> None:
    rows_in = (worksheet or {}).get("manual_rows") or []
    if not rows_in:
        return
    doc.add_heading("Expert-entered failure modes (class D)", level=1)
    rows = [[
        F.fmt_text(r.get("component_class")),
        F.fmt_text(r.get("name")),
        F.fmt_number(r.get("occurrence_per_year"), unit="/yr", digits=2),
        F.fmt_number(r.get("severity_eur"), unit="€"),
        F.fmt_number(r.get("criticality_eur_per_year"), unit="€/yr"),
        F.fmt_text(r.get("rate_source")),
    ] for r in rows_in]
    _table(doc, ["Component", "Name", "Occurrence", "Severity",
                 "Criticality", "Rate source"], rows,
           "Rows entered by the analyst in the FMEA worksheet "
           "(engine: expert; fidelity: expert judgement)")


def _render_pipeline(doc, report: dict) -> None:
    pipeline = report.get("pipeline") or {}
    doc.add_heading("Study pipeline", level=1)
    rows = [[
        F.fmt_text(s.get("stage")),
        F.fmt_status(s.get("status")),
        F.fmt_number(s.get("solves_charged")),
        F.fmt_text(s.get("note")) if s.get("note") else "",
    ] for s in pipeline.get("stages") or []]
    _table(doc, ["Stage", "Status", "Solves", "Note"], rows,
           f"Solves consumed {F.fmt_number(pipeline.get('solves_consumed'))} "
           f"of a budget of {F.fmt_number(pipeline.get('budget_solves'))}"
           + ("; the study was aborted" if pipeline.get("aborted") else ""))


# ── entry point ──────────────────────────────────────────────────────────


def render_reference_design_docx(
    report: dict,
    *,
    fmea_worksheet: dict | None = None,
    template: Any = None,
    figures: dict[str, bytes] | None = None,
) -> bytes:
    """
    ``report`` is the ``export_reference_design`` dict (or a full
    ``ReferenceDesignReport.model_dump``). Returns the ``.docx`` bytes.
    """
    report = copy.deepcopy(report)
    doc = _open_template(template)
    _title_block(doc, report)
    _headline_table(doc, report)
    _completeness_table(doc, report)
    for name in REPORT_SECTIONS:
        _render_section(doc, name, report, figures)
    _render_worksheet(doc, fmea_worksheet)
    _render_pipeline(doc, report)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
