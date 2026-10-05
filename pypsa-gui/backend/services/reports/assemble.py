"""
The evidence-only report: a code-built ``ReportDocument`` from ``Evidence``.

WP5 of docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(phase 1, ``POST /{name}/reports`` with ``mode: "evidence_only"``). No prose
and no model: every block references a table the collector built, states a
disclosure it carried, or says that a section was not established. The
generator (WP3) writes prose between the same blocks; this is the document
it starts from and the one the user gets when no model is configured.

Rules:

* **Pure.** Takes the ``Evidence`` and the PNG bytes the caller already
  rendered, returns a document. It writes nothing: the route stores the
  document and the figures (``store.create_report`` / ``store.write_figure``).
* **Gaps before anything numeric** — ``build_study_report``'s own rule: the
  executive summary opens with every evidence gap, then every required
  disclosure, and only then the headline table.
* **Every ``REPORT_SECTIONS`` entry is present**, in order, with the
  completeness status the study gave it; an unestablished one carries
  exactly one ``not_established`` callout with the stage's note.
* **A figure is referenced only when its PNG exists.** ``figure_pngs`` is
  the set of figures actually produced; a section whose figure was not
  rendered simply has no ``FigureRef``, and the writer never sees a dangling
  id.
* ``None`` never becomes ``0``: the tables are the collector's (already
  formatted), and the ``Field`` blocks built here go through
  ``services.reports.formatting`` too.
"""
from __future__ import annotations

import json
from datetime import datetime, UTC
from typing import Any

from models.energy_hub import REPORT_SECTIONS
from models.report import (
    Block,
    Callout,
    Field,
    Figure,
    FigureRef,
    ReportDocument,
    Section,
    Table,
    TableRef,
)
from services.adequacy.study_report import SECTION_ORDER
from services.reports import formatting as F
from services.reports.docx_writer import SECTION_TITLES
from services.reports.evidence import (
    NO_DATA_HINTS,
    WORKSHEET_SECTION,
    Evidence,
    EvidenceSection,
    adequacy_section_id,
    evidence_hash,
)

# EH section → the figure id WP4 renders for it (`services.reports.figures`).
EH_FIGURE_IDS: dict[str, str] = {
    "fmea_top": "fmea_pareto",
    "frontier": "frontier",
    "sizing": "capacity_mix",
}

FIGURE_CAPTIONS: dict[str, str] = {
    "fmea_pareto": ("Criticality of the ranked residual failure modes (€/yr), "
                    "cumulative share annotated"),
    "frontier": ("Cost-vs-availability frontier, one full expansion solve per "
                 "target; the knee is marked"),
    "capacity_mix": "Installed capacity of the least-cost plan by carrier",
}

EXECUTIVE_SUMMARY_ID = "executive_summary"
ADEQUACY_SURFACES_ID = "adequacy_surfaces"
PIPELINE_ID = "pipeline"

DEFAULT_TITLE = "Study report"

# Payload keys the engines use for their own standing notes; carried next to
# the table so a section reads whole on its own (the executive summary
# repeats them as required disclosures, on purpose).
_PAYLOAD_NOTE_KEYS = ("warning", "note", "copt_fidelity_note")
_MAX_FIELDS = 40
_MAX_FIELD_CHARS = 200


# ── helpers ──────────────────────────────────────────────────────────────


def _scalar(value: Any) -> str:
    """One payload leaf as a field value; ``None`` is NOT_ESTABLISHED."""
    if value is None or isinstance(value, (bool, int, float)):
        return F.fmt_number(value)
    if isinstance(value, str):
        return F.fmt_text(value)
    if isinstance(value, (list, tuple)) and all(
            v is None or isinstance(v, (str, bool, int, float)) for v in value):
        return F.fmt_text([_scalar(v) for v in value])
    text = json.dumps(value, default=str, separators=(",", ":"))
    if len(text) > _MAX_FIELD_CHARS:
        text = text[:_MAX_FIELD_CHARS - 1] + "…"
    return text


def _fields(payload: dict, *, skip: tuple[str, ...] = ()) -> list[Field]:
    """
    A section's payload as ``Field`` blocks when the collector built no
    table for it: scalars at the top level, one level of nesting joined as
    ``parent / child``. Deeper structures are shown as compact JSON.
    """
    out: list[Field] = []
    for key, value in payload.items():
        if key in skip:
            continue
        if isinstance(value, dict):
            for sub, leaf in value.items():
                out.append(Field(key=f"{key} / {sub}", value=_scalar(leaf)))
        else:
            out.append(Field(key=str(key), value=_scalar(value)))
        if len(out) >= _MAX_FIELDS:
            break
    return out[:_MAX_FIELDS]


def _tables_for(evidence: Evidence, section_id: str) -> list[str]:
    """The ids of the tables the collector built from this section (``slice_for``'s rule)."""
    own = f"/sections/{section_id}"
    return [t.table_id for t in evidence.tables.values()
            if t.source_path == own or t.source_path.startswith(own + "/")
            or t.source_path == f"/headline/{section_id}"]


def _not_established_text(section: EvidenceSection) -> str:
    status = F.fmt_status(section.status)
    text = f"this section was {status}"
    if section.note and section.note != status:
        text += f" ({section.note})"
    return text + "."


def _not_established_section(section_id: str, heading: str,
                              section: EvidenceSection) -> Section:
    status = section.status if section.status != "ok" else "not_established"
    return Section(
        section_id=section_id, heading=heading, source="code", status=status,
        note=section.note,
        blocks=[Callout(kind="not_established",
                        text=_not_established_text(section))],
    )


def _payload_notes(payload: dict | None) -> list[Block]:
    out: list[Block] = []
    for key in _PAYLOAD_NOTE_KEYS:
        text = (payload or {}).get(key)
        if text:
            out.append(Callout(kind="disclosure", text=str(text)))
    return out


def _figure_block(section_id: str, figure_pngs: dict[str, bytes],
                  figures: dict[str, Figure]) -> FigureRef | None:
    figure_id = EH_FIGURE_IDS.get(section_id)
    if figure_id is None or not figure_pngs.get(figure_id):
        return None
    caption = FIGURE_CAPTIONS.get(figure_id)
    figures[figure_id] = Figure(
        figure_id=figure_id, png_file=f"figures/{figure_id}.png",
        caption=caption, source_path=f"/sections/{section_id}/payload")
    return FigureRef(figure_id=figure_id, caption=caption)


# ── sections ─────────────────────────────────────────────────────────────


def _executive_summary(evidence: Evidence) -> Section:
    blocks: list[Block] = []
    for gap in evidence.evidence_gaps:
        subject = gap.get("subject") or gap.get("kind") or "evidence"
        detail = gap.get("detail") or gap.get("code") or "unspecified gap"
        blocks.append(Callout(kind="gap", text=f"{subject}: {detail}"))
    for line in evidence.required_disclosures:
        blocks.append(Callout(kind="disclosure", text=line))
    status = "ok"
    if "headline" in evidence.tables:
        blocks.append(TableRef(table_id="headline"))
    else:
        status = "not_established"
        blocks.append(Callout(kind="not_established",
                              text=f"no headline results: "
                                   f"{NO_DATA_HINTS['eh_reference_design']}."))
    if "completeness" in evidence.tables:
        blocks.append(TableRef(table_id="completeness"))
    return Section(section_id=EXECUTIVE_SUMMARY_ID, heading="Executive summary",
                   source="code", status=status, blocks=blocks)


def _eh_section(evidence: Evidence, name: str, figure_pngs: dict[str, bytes],
                figures: dict[str, Figure]) -> Section:
    section = evidence.sections[name]
    heading = SECTION_TITLES.get(name, section.title)
    if section.status != "ok" or section.payload is None:
        return _not_established_section(name, heading, section)
    blocks: list[Block] = []
    table_ids = _tables_for(evidence, name)
    if table_ids:
        blocks.extend(TableRef(table_id=t) for t in table_ids)
    else:
        blocks.extend(_fields(section.payload, skip=_PAYLOAD_NOTE_KEYS))
    fig = _figure_block(name, figure_pngs, figures)
    if fig is not None:
        blocks.append(fig)
    if section.note:
        blocks.append(Callout(kind="disclosure", text=f"Stage note: {section.note}"))
    blocks.extend(_payload_notes(section.payload))
    return Section(section_id=name, heading=heading, source="code", status="ok",
                   note=section.note, blocks=blocks)


def _adequacy_sections(evidence: Evidence) -> list[Section]:
    out: list[Section] = []
    if "adequacy_sections" in evidence.tables:
        out.append(Section(
            section_id=ADEQUACY_SURFACES_ID,
            heading="Reliability surfaces of this session",
            source="code", status="ok",
            blocks=[TableRef(table_id="adequacy_sections")]))
    for study_id in SECTION_ORDER:
        section_id = adequacy_section_id(study_id)
        section = evidence.sections.get(section_id)
        if section is None or section.status != "ok" or section.payload is None:
            continue  # stated in the surfaces table, with its reason
        blocks: list[Block] = [
            Field(key="Engine", value=F.fmt_text(section.engine)),
            Field(key="Fidelity", value=F.fmt_text(section.fidelity)),
            Field(key="Source", value=F.fmt_text(section.source)),
        ]
        table_ids = _tables_for(evidence, section_id)
        if table_ids:
            blocks.extend(TableRef(table_id=t) for t in table_ids)
        else:
            blocks.extend(_fields(section.payload,
                                  skip=("engine", "fidelity") + _PAYLOAD_NOTE_KEYS))
        if section.note:
            blocks.append(Callout(kind="disclosure", text=section.note))
        blocks.extend(_payload_notes(section.payload))
        out.append(Section(section_id=section_id, heading=section.title,
                           source="code", status="ok", note=section.note,
                           blocks=blocks))
    return out


def _worksheet_section(evidence: Evidence) -> Section | None:
    section = evidence.sections.get(WORKSHEET_SECTION)
    if section is None or section.status != "ok" \
            or WORKSHEET_SECTION not in evidence.tables:
        return None
    blocks: list[Block] = [TableRef(table_id=WORKSHEET_SECTION)]
    if section.note:
        blocks.append(Callout(kind="disclosure", text=section.note))
    return Section(section_id=WORKSHEET_SECTION,
                   heading="Expert-entered failure modes (class D)",
                   source="code", status="ok", note=section.note, blocks=blocks)


def _pipeline_section(evidence: Evidence) -> Section:
    if PIPELINE_ID in evidence.tables:
        return Section(section_id=PIPELINE_ID, heading="Study pipeline",
                       source="code", status="ok",
                       blocks=[TableRef(table_id=PIPELINE_ID)])
    return Section(
        section_id=PIPELINE_ID, heading="Study pipeline", source="code",
        status="not_established",
        blocks=[Callout(kind="not_established",
                        text="no study pipeline was recorded in this session: "
                             f"{NO_DATA_HINTS['eh_reference_design']}.")])


# ── entry point ──────────────────────────────────────────────────────────


def evidence_only_document(evidence: Evidence, *, title: str, report_id: str,
                           figure_pngs: dict[str, bytes]) -> ReportDocument:
    """
    The code-only report (``mode="evidence_only"``) for ``evidence``.

    ``figure_pngs`` maps a figure id (``fmea_pareto``, ``frontier``,
    ``capacity_mix``) to the PNG the caller rendered; only those figures are
    referenced, and the caller writes them under ``figures/<id>.png``.
    """
    figures: dict[str, Figure] = {}
    sections: list[Section] = [_executive_summary(evidence)]
    for name in REPORT_SECTIONS:
        sections.append(_eh_section(evidence, name, figure_pngs, figures))
    sections.extend(_adequacy_sections(evidence))
    worksheet = _worksheet_section(evidence)
    if worksheet is not None:
        sections.append(worksheet)
    sections.append(_pipeline_section(evidence))

    tables = {
        table_id: Table(table_id=t.table_id, columns=list(t.columns),
                        rows=[list(r) for r in t.rows], caption=t.caption,
                        source_path=t.source_path)
        for table_id, t in evidence.tables.items()
    }
    return ReportDocument(
        report_id=report_id, version=1, title=title or DEFAULT_TITLE,
        created_at=datetime.now(tz=UTC).isoformat(),
        evidence_hash=evidence_hash(evidence),
        profile_id=None, model=None, mode="evidence_only",
        sections=sections, tables=tables, figures=figures,
    )
