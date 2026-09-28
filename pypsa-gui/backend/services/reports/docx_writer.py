"""
Render a study report as a Word document.

WP0 + WP5 of docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md.

There is ONE writer: ``render_document_docx`` takes a ``ReportDocument``
(``models/report.py``) and renders its blocks. ``render_reference_design_docx``
(the WP0 spike's entry point) is now a thin adapter: it collects the
evidence from the ``ReferenceDesignReport`` dict, builds the code-only
document with ``services.reports.assemble`` and renders that — decision 16's
spirit (one report builder), so a chip exported from the chat strip and a
report exported from the Reports panel are the same bytes for the same
evidence.

Block mapping (the writer's whole vocabulary):

* ``Paragraph`` — the markdown inline subset (``**bold**``, ``*italic*``,
  ``` `code` ```, ``[text](url)``) as runs, through a small tokenizer; no
  markdown dependency.
* ``Bullets`` — ``List Bullet``; a template without that style gets a
  ``Normal`` paragraph with a bullet marker.
* ``TableRef`` — the table from ``doc.tables`` in ``Table Grid`` with a
  numbered caption; a missing id is one ``Disclosure`` line, never a crash.
* ``FigureRef`` — the picture and a numbered caption when the bytes are
  present, else a ``Disclosure`` line saying the figure was not produced.
* ``Callout`` — ``Disclosure`` style; a gap is prefixed "Gap:", a
  not-established statement is a "Not established:" sentence.
* ``Field`` — bold label, then the value.

Every section heading carries a bookmark ``sec:<section_id>`` — the anchor
the round trip (increment 3) matches an edited Word file back on.

Template handling: ``template`` may be ``None`` (the code-built default),
bytes, or a path. The writer keeps the template's section properties (page
size, margins, headers, footers) and styles, clears its body, and writes
the report into it — the same path a user template will take in increment 2.
"""
from __future__ import annotations

import copy
import io
import json
import pathlib
import re
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, RGBColor

from models.report import (
    Bullets,
    Callout,
    Field,
    FigureRef,
    Paragraph,
    ReportDocument,
    Section,
    TableRef,
)
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

# WP0's ``figures=`` argument is keyed by EH section; the document's figure
# ids are WP4's. The adapter translates.
_SECTION_FIGURE_IDS = {"fmea_top": "fmea_pareto", "frontier": "frontier",
                       "sizing": "capacity_mix"}

BOOKMARK_PREFIX = "sec:"
APPENDIX_HEADING = "Numbers to check"
_CODE_FONT = "Consolas"
_LINK_COLOR = RGBColor(0x1F, 0x4E, 0x9E)
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
        return _para(doc, str(text), DISCLOSURE_STYLE)
    return None


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


# ── bookmarks ────────────────────────────────────────────────────────────


class _Bookmarks:
    """Unique ``w:id`` values per document; Word rejects duplicates."""

    def __init__(self) -> None:
        self._next = 0

    def wrap(self, paragraph, name: str) -> None:
        """Wrap the paragraph's runs in ``bookmarkStart``/``bookmarkEnd``."""
        bid = str(self._next)
        self._next += 1
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), bid)
        start.set(qn("w:name"), name)
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), bid)
        p = paragraph._p
        p_pr = p.find(qn("w:pPr"))
        index = list(p).index(p_pr) + 1 if p_pr is not None else 0
        p.insert(index, start)
        p.append(end)


def _heading(doc, text: str, level: int, bookmarks: _Bookmarks | None = None,
             name: str | None = None):
    p = doc.add_heading(text, level=level)
    if bookmarks is not None and name:
        bookmarks.wrap(p, name)
    return p


# ── the markdown inline subset ───────────────────────────────────────────

_INLINE = re.compile(
    r"(?P<code>`(?P<code_t>[^`]+)`)"
    r"|(?P<bold>\*\*(?P<bold_t>.+?)\*\*)"
    r"|(?P<italic>\*(?P<italic_t>[^*]+?)\*)"
    r"|(?P<link>\[(?P<link_t>[^\]]+)\]\((?P<link_u>[^)\s]+)\))"
)


def tokenize_inline(md: str) -> list[tuple[str, str, str | None]]:
    """
    ``[(kind, text, url)]`` for the inline subset: ``text``, ``bold``,
    ``italic``, ``code``, ``link``. Nesting is not supported (bold inside a
    link renders its markers) — the generator's schema does not produce it.
    """
    out: list[tuple[str, str, str | None]] = []
    pos = 0
    for m in _INLINE.finditer(md):
        if m.start() > pos:
            out.append(("text", md[pos:m.start()], None))
        if m.group("code"):
            out.append(("code", m.group("code_t"), None))
        elif m.group("bold"):
            out.append(("bold", m.group("bold_t"), None))
        elif m.group("italic"):
            out.append(("italic", m.group("italic_t"), None))
        else:
            out.append(("link", m.group("link_t"), m.group("link_u")))
        pos = m.end()
    if pos < len(md):
        out.append(("text", md[pos:], None))
    return out


def _add_runs(paragraph, md: str) -> None:
    for kind, text, url in tokenize_inline(md):
        run = paragraph.add_run(text)
        if kind == "bold":
            run.bold = True
        elif kind == "italic":
            run.italic = True
        elif kind == "code":
            run.font.name = _CODE_FONT
        elif kind == "link":
            run.underline = True
            run.font.color.rgb = _LINK_COLOR
            if url and url != text:
                paragraph.add_run(f" ({url})").font.color.rgb = _LINK_COLOR


# ── block renderers ──────────────────────────────────────────────────────


class _Counters:
    def __init__(self) -> None:
        self.tables = 0
        self.figures = 0


def _render_paragraph(doc, block: Paragraph) -> None:
    for chunk in [c for c in block.md.split("\n\n") if c.strip()]:
        p = doc.add_paragraph()
        _add_runs(p, chunk.replace("\n", " ").strip())


def _render_bullets(doc, block: Bullets) -> None:
    for item in block.items:
        try:
            p = doc.add_paragraph(style=doc.styles["List Bullet"])
            _add_runs(p, item)
        except KeyError:
            p = doc.add_paragraph()
            p.add_run("• ")
            _add_runs(p, item)


def _render_table_ref(doc, block: TableRef, report: ReportDocument,
                      counters: _Counters) -> None:
    table = report.tables.get(block.table_id)
    if table is None:
        _disclosure(doc, f"Table {block.table_id!r} is not available in this "
                         f"version of the report.")
        return
    counters.tables += 1
    caption = block.caption or table.caption
    label = f"Table {counters.tables}"
    _table(doc, list(table.columns), [list(r) for r in table.rows],
           f"{label} — {caption}" if caption else label)


def _render_figure_ref(doc, block: FigureRef, report: ReportDocument,
                       figure_bytes: dict[str, bytes], counters: _Counters) -> None:
    figure = report.figures.get(block.figure_id)
    png = figure_bytes.get(block.figure_id)
    if not png:
        _disclosure(doc, f"Figure {block.figure_id!r} was not produced for this "
                         f"version of the report.")
        return
    counters.figures += 1
    caption = block.caption or (figure.caption if figure is not None else None)
    label = f"Figure {counters.figures}"
    _picture(doc, png, f"{label} — {caption}" if caption else label)


def _render_callout(doc, block: Callout) -> None:
    if block.kind == "gap":
        _disclosure(doc, f"Gap: {block.text}")
    elif block.kind == "not_established":
        text = block.text.strip()
        if text and text[-1] not in ".!?":
            text += "."
        _para(doc, f"Not established: {text}")
    else:
        _disclosure(doc, block.text)


def _render_field(doc, block: Field) -> None:
    p = doc.add_paragraph()
    p.add_run(f"{block.key}: ").bold = True
    p.add_run(block.value)


def _render_block(doc, block, report: ReportDocument,
                  figure_bytes: dict[str, bytes], counters: _Counters) -> None:
    if isinstance(block, Paragraph):
        _render_paragraph(doc, block)
    elif isinstance(block, Bullets):
        _render_bullets(doc, block)
    elif isinstance(block, TableRef):
        _render_table_ref(doc, block, report, counters)
    elif isinstance(block, FigureRef):
        _render_figure_ref(doc, block, report, figure_bytes, counters)
    elif isinstance(block, Callout):
        _render_callout(doc, block)
    elif isinstance(block, Field):
        _render_field(doc, block)
    else:  # a block type the model grew after this writer — say so, never drop it
        _disclosure(doc, f"Unsupported block {getattr(block, 'type', type(block).__name__)!r}.")


def _render_doc_section(doc, section: Section, report: ReportDocument,
                        figure_bytes: dict[str, bytes], counters: _Counters,
                        bookmarks: _Bookmarks) -> None:
    _heading(doc, section.heading, 1, bookmarks,
             f"{BOOKMARK_PREFIX}{section.section_id}")
    for block in section.blocks:
        _render_block(doc, block, report, figure_bytes, counters)
    # An ESTABLISHED section can still carry a note the reader must see — the
    # generation job writes "prose not established: <reason> (profile …)" on a
    # section whose evidence is fine but whose write-up the model could not
    # produce. State it, unless a Callout block already carries the same text
    # (the evidence-only assembler puts stage notes into a disclosure Callout).
    if section.status == "ok" and section.note and not any(
            isinstance(b, Callout) and b.text.strip() == section.note.strip()
            for b in section.blocks):
        _disclosure(doc, f"Note: {section.note}")
    # A section whose status says it was not established but which carries
    # no such block (a hand-edited version, a future generator) is still
    # stated: the omission is the finding.
    if section.status != "ok" and not any(
            isinstance(b, Callout) and b.kind == "not_established"
            for b in section.blocks):
        sentence = f"Not established: this section was {F.fmt_status(section.status)}"
        if section.note:
            sentence += f" ({section.note})"
        _para(doc, sentence + ".")


def _generated_from_line(report: ReportDocument) -> str:
    parts = [f"Version {report.version}",
             f"mode {report.mode.replace('_', ' ')}",
             f"evidence {report.evidence_hash[:12]}",
             f"generated {report.created_at}"]
    if report.profile_id:
        parts.append(f"profile {report.profile_id}")
    if report.model:
        parts.append(f"model {report.model}")
    return " · ".join(parts)


def _appendix(doc, report: ReportDocument, bookmarks: _Bookmarks) -> None:
    flagged = [s for s in report.sections if s.audit.unverified]
    if not flagged:
        return
    _heading(doc, APPENDIX_HEADING, 1, bookmarks, f"{BOOKMARK_PREFIX}numbers_to_check")
    _disclosure(doc, "Numbers in the prose that could not be matched to the "
                     "evidence. They were flagged, not edited: check each "
                     "against the section's table before the report leaves "
                     "your hands.")
    for section in flagged:
        p = doc.add_paragraph()
        p.add_run(f"{section.heading}: ").bold = True
        p.add_run(", ".join(section.audit.unverified))


# ── entry points ─────────────────────────────────────────────────────────


def render_document_docx(doc: ReportDocument, *, template: Any = None,
                         figure_bytes: dict[str, bytes]) -> bytes:
    """
    ``doc`` rendered to ``.docx`` bytes. ``figure_bytes`` maps a figure id to
    its PNG; a referenced figure with no bytes is stated as not produced.
    """
    d = _open_template(template)
    bookmarks = _Bookmarks()
    counters = _Counters()
    d.add_heading(doc.title, level=0)
    _para(d, _generated_from_line(doc))
    _disclosure(d, (
        "Every table and figure in this document was computed by the study "
        "engines and rendered by code. Sections the study did not establish "
        "say so."))
    for section in doc.sections:
        _render_doc_section(d, section, doc, figure_bytes, counters, bookmarks)
    _appendix(d, doc, bookmarks)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def render_reference_design_docx(
    report: dict,
    *,
    fmea_worksheet: dict | None = None,
    template: Any = None,
    figures: dict[str, bytes] | None = None,
) -> bytes:
    """
    WP0's entry point, as an adapter over the one writer.

    ``report`` is the ``export_reference_design`` dict (or a full
    ``ReferenceDesignReport.model_dump``); ``figures`` is keyed by EH section
    (``fmea_top`` → the Pareto PNG), as the chat tool passes it. Returns the
    ``.docx`` bytes.
    """
    from services.reports.assemble import evidence_only_document
    from services.reports.evidence import collect_evidence
    from services.reports.store import new_report_id

    report = copy.deepcopy(report)
    evidence = collect_evidence(study_report=None, eh_report=report,
                                worksheet=fmea_worksheet)
    figure_pngs = {_SECTION_FIGURE_IDS[k]: v for k, v in (figures or {}).items()
                   if k in _SECTION_FIGURE_IDS and v}
    doc = evidence_only_document(
        evidence, title="Energy Hub reference design report",
        report_id=new_report_id(), figure_pngs=figure_pngs)
    return render_document_docx(doc, template=template, figure_bytes=figure_pngs)
