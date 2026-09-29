"""
WP0 — the no-LLM ``.docx`` render of the Energy Hub ``ReferenceDesignReport``.

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(WP0). The writer is the one place study numbers become a Word document, so
the tests pin the two properties the assessment calls non-negotiable:
nothing that was ``None`` renders as ``0`` (ADR-0001), and a section that
was not established is stated, never omitted ("the omission is the finding").
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from docx import Document

from models.energy_hub import REPORT_SECTIONS
from services.reports import formatting as F
from services.reports.docx_writer import (
    DOCX_MIME,
    SECTION_TITLES,
    render_reference_design_docx,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eh_archetypes"

def _png_1x1() -> bytes:
    """
    A valid 1×1 grey PNG built from its chunks (python-docx parses the
    IHDR to size the picture, so a hand-typed blob is not enough).
    """
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x80")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


_PNG_1x1 = _png_1x1()


def _skeleton() -> dict:
    return json.loads((FIXTURES / "mvp_a_report_skeleton.json").read_text())


def _populated() -> dict:
    """A report with the six sections increment 1 renders as tables."""
    report = _skeleton()
    report.update({
        "ens_cap_permyriad": 10.0,
        "achieved_ens_permyriad": 8.25,
        "achieved_shed_hours": 12.5,
        "mc_lole_h": 3.21,
        "cost_at_target_eur": 1_234_567.89,
        "period_basis": "single_period",
        "tea": {"lcoe_eur_per_mwh": 61.7, "lcoh_eur_per_kg": None,
                "notes": "LCOE = cost / served", "lcoh_status": "skipped",
                "lcoh_note": "LCOH skipped: the network has no electrolyser Links"},
        "gates": {"scr": "pass", "emt_recommended": False},
    })
    s = report["sections"]
    s["target"] = {"status": "ok", "note": None, "payload": {
        "binding": "ens_cap",
        "system": {"cap_mwh": 990.0, "achieved_ens_mwh": 816.75,
                   "achieved_shed_hours": 12.5},
        "metrics": {"demand_mwh": 99_000.0, "ens_mwh": 816.75},
    }}
    s["cost"] = {"status": "ok", "note": None, "payload": {
        "total_system_cost_eur": 1_234_567.89, "period_basis": "single_period",
        "excludes_shed_cost": True}}
    s["sizing"] = {"status": "ok", "note": None, "payload": {
        "by_carrier": {"gas": 100.0, "wind": 25.0}, "total_p_nom_mw": 125.0}}
    s["certification"] = {"status": "ok", "note": "certified at 200 draws",
                          "payload": {
        "metric": "mc_lole", "target_lole_h": 4.0, "mc_lole_h": 3.21,
        "lole_ci": [2.9, 3.5], "eue_mwh": 41.2, "eue_ci": [36.0, 46.0],
        "n_samples": 200, "draws_requested": 200, "converged": True,
        "verdict": "certified", "engine": "mc", "fidelity": "sequential_mc",
        "warning": "MC LOLE is SAMPLED: the interval travels with the mean",
    }}
    s["frontier"] = {"status": "ok", "note": "5 points around 10‱", "payload": {
        "points": [
            {"target_permyriad": 40.0, "status": "ok", "period_basis": "single_period",
             "point": {"cap_mwh": 3960.0, "achieved_ens_mwh": 3900.0,
                       "achieved_shed_hours": 60.0,
                       "total_system_cost_eur": 900_000.0}},
            {"target_permyriad": 10.0, "status": "ok", "period_basis": "single_period",
             "point": {"cap_mwh": 990.0, "achieved_ens_mwh": 816.75,
                       "achieved_shed_hours": 12.5,
                       "total_system_cost_eur": 1_234_567.89}},
            {"target_permyriad": 2.5, "status": "infeasible", "point": None},
        ],
        "knee_index": 1, "voll_eur_per_mwh": 3000.0, "warning": None,
        "period_basis": "single_period", "excludes_shed_cost": True,
        "engine": "lp_proxy",
    }}
    s["fmea_top"] = {"status": "ok", "note": None, "payload": {
        "top": [
            {"rank": 1, "mode_id": "gen:backup:forced_outage",
             "component_class": "Generator", "name": "backup",
             "failure_class": "A", "occurrence_per_year": 2.5,
             "occurrence_basis": "FOR", "severity_eur": 40_000.0,
             "criticality_eur_per_year": 100_000.0, "delta_eue_mwh": 13.3,
             "engine": "copt", "fidelity": "analytic_convolution"},
            {"rank": 2, "mode_id": "link:import:forced_outage",
             "component_class": "Link", "name": "import",
             "failure_class": "B", "occurrence_per_year": 1.0,
             "occurrence_basis": "FOR", "severity_eur": 25_000.0,
             "criticality_eur_per_year": 25_000.0, "delta_eue_mwh": None,
             "engine": "lp_proxy", "fidelity": "deterministic_scenario"},
        ],
        "top_n": 10, "n_total_modes": 2, "classes_included": ["A", "B"],
        "class_b": {"status": "run", "reason": None, "solves_charged": 3},
        "voll_eur_per_mwh": 3000.0,
        "note": ("Link-primary residual risk (Class-B Link sweep); "
                 "AC Line/Transformer N-1 remains on SCLOPF and is omitted "
                 "from FMEA ranking"),
    }}
    for name in ("target", "cost", "sizing", "certification", "frontier",
                 "fmea_top"):
        report["completeness"][name] = "ok"
    return report


def _text(data: bytes) -> str:
    """Every paragraph and every table cell, joined — what a reader sees."""
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


def _pictures(data: bytes) -> int:
    doc = Document(io.BytesIO(data))
    return len(doc.inline_shapes)


# ── formatting ───────────────────────────────────────────────────────────


def test_none_and_nan_render_as_not_established_never_zero():
    assert F.fmt_number(None) == F.NOT_ESTABLISHED
    assert F.fmt_number(float("nan")) == F.NOT_ESTABLISHED
    assert F.fmt_number(float("inf")) == F.NOT_ESTABLISHED
    assert "0" not in F.fmt_number(None)


def test_fmt_number_groups_thousands_and_keeps_unit():
    assert F.fmt_number(1_234_567.891, unit="€") == "1,234,568 €"
    assert F.fmt_number(3.2109, unit="h/yr", digits=2) == "3.21 h/yr"
    assert F.fmt_number(0.0) == "0.000"
    assert F.fmt_number(True) == "yes"
    assert F.fmt_number(200) == "200"


def test_fmt_ci_states_missing_interval():
    assert F.fmt_ci([2.9, 3.5], unit="h/yr") == "2.90 – 3.50 h/yr"
    assert F.fmt_ci([], unit="h/yr") == F.NOT_ESTABLISHED
    assert F.fmt_ci(None) == F.NOT_ESTABLISHED


def test_fmt_status_maps_the_completeness_enum():
    assert F.fmt_status("ok") == "established"
    assert F.fmt_status("not_established") == "not established"
    assert F.fmt_status("skipped") == "not run in this study"


# ── the golden skeleton: nothing established ─────────────────────────────


def test_skeleton_renders_and_states_every_section_as_not_established():
    data = render_reference_design_docx(_skeleton())
    assert data[:2] == b"PK"  # a zip — a .docx
    text = _text(data)
    for name in REPORT_SECTIONS:
        assert SECTION_TITLES[name] in text, name
    # One "Not established:" statement per section (WP5: the adapter renders
    # the section's `not_established` callout), plus the headline fields.
    assert text.count("Not established: this section was not established") \
        == len(REPORT_SECTIONS)


def test_skeleton_never_renders_a_missing_headline_as_zero():
    text = _text(render_reference_design_docx(_skeleton()))
    doc = Document(io.BytesIO(render_reference_design_docx(_skeleton())))
    headline = doc.tables[0]
    values = [row.cells[1].text for row in headline.rows]
    assert all(v != "0" and v != "0.0" for v in values)
    assert F.NOT_ESTABLISHED in text


# ── a populated report ───────────────────────────────────────────────────


def test_populated_report_renders_the_numbers_as_formatted():
    text = _text(render_reference_design_docx(_populated()))
    assert "1,234,568 €" in text          # cost at target
    assert "3.21 h/yr" in text            # MC LOLE
    assert "2.90 – 3.50 h/yr" in text     # its interval
    assert "100,000 €/yr" in text         # top criticality
    assert "certified" in text
    assert "excludes load-shedding cost" in text
    assert "single_period" in text


def test_populated_report_keeps_fmea_rows_in_payload_order():
    doc = Document(io.BytesIO(render_reference_design_docx(_populated())))
    fmea = next(t for t in doc.tables if t.rows[0].cells[0].text == "Rank")
    names = [row.cells[3].text for row in fmea.rows[1:]]
    assert names == ["backup", "import"]
    # A None ΔEUE in a row renders as not established, not 0.
    assert fmea.rows[2].cells[7].text == F.NOT_ESTABLISHED


def test_populated_report_carries_the_disclosures_verbatim():
    text = _text(render_reference_design_docx(_populated()))
    assert "Link-primary residual risk" in text
    assert "MC LOLE is SAMPLED" in text
    assert "LCOH skipped: the network has no electrolyser Links" in text


def test_frontier_table_marks_the_knee_and_states_infeasible_points():
    doc = Document(io.BytesIO(render_reference_design_docx(_populated())))
    frontier = next(t for t in doc.tables
                    if t.rows[0].cells[0].text.startswith("Target"))
    rows = [[c.text for c in r.cells] for r in frontier.rows[1:]]
    assert any("knee" in r[1] for r in rows)
    assert rows[2][1].startswith("infeasible")
    assert rows[2][2] == F.NOT_ESTABLISHED


def test_figures_are_embedded_only_when_given():
    assert _pictures(render_reference_design_docx(_populated())) == 0
    data = render_reference_design_docx(
        _populated(), figures={"fmea_top": _PNG_1x1})
    assert _pictures(data) == 1


def test_expert_worksheet_rows_render_when_present():
    ws = {"manual_rows": [{
        "mode_id": "expert:cooling:blockage", "component_class": "Auxiliary",
        "name": "cooling water intake", "failure_class": "D",
        "occurrence_per_year": 0.2, "occurrence_basis": "expert",
        "severity_eur": 50_000.0, "criticality_eur_per_year": 10_000.0,
        "engine": "expert", "fidelity": "expert_judgement",
        "rate_source": "site interview 2026-09",
    }], "overlays": {}, "version": 3}
    text = _text(render_reference_design_docx(_populated(), fmea_worksheet=ws))
    assert "cooling water intake" in text
    assert "site interview 2026-09" in text
    assert "10,000 €/yr" in text


def test_a_user_template_is_honoured_and_its_body_replaced():
    tpl = Document()
    tpl.add_paragraph("Confidential — draft template body")
    tpl.sections[0].footer.paragraphs[0].text = "ACME Energy Consulting"
    buf = io.BytesIO()
    tpl.save(buf)
    data = render_reference_design_docx(_skeleton(), template=buf.getvalue())
    doc = Document(io.BytesIO(data))
    assert doc.sections[0].footer.paragraphs[0].text == "ACME Energy Consulting"
    assert "Confidential — draft template body" not in _text(data)
    assert SECTION_TITLES["fmea_top"] in _text(data)


def test_docx_mime_is_the_upload_allowlist_entry():
    from services import upload_service
    assert DOCX_MIME in upload_service.ALLOWED_MIME_TYPES


# ═════════════════════════════════════════════════════════════════════════
# WP5 — ``render_document_docx``: the writer from a ``ReportDocument``
# ═════════════════════════════════════════════════════════════════════════

from docx.oxml.ns import qn  # noqa: E402

from models.report import (  # noqa: E402
    Bullets,
    Callout,
    Field,
    Figure,
    FigureRef,
    Paragraph,
    ReportDocument,
    Section,
    SectionAudit,
    Table,
    TableRef,
)
from services.reports.docx_writer import render_document_docx  # noqa: E402


def _document(*, unverified: list[str] | None = None) -> ReportDocument:
    """One document with every block type, three sections, a table and a figure."""
    return ReportDocument(
        report_id="0123456789abcdef", version=2, title="Client study report",
        created_at="2026-09-28T10:00:00+00:00", evidence_hash="c" * 64,
        profile_id="local-llama", model="llama-3.1-8b", mode="generated",
        sections=[
            Section(section_id="executive_summary", heading="Executive summary",
                    source="code", status="ok", blocks=[
                        Callout(kind="gap", text="the demand profile is frozen"),
                        Callout(kind="disclosure", text="excludes load-shedding cost"),
                        TableRef(table_id="headline"),
                    ]),
            Section(section_id="fmea_top", heading="Residual failure modes",
                    source="llm", status="ok",
                    audit=SectionAudit(unverified=unverified or []),
                    blocks=[
                        Paragraph(md="The **top mode** is *backup* at `100,000 €/yr` "
                                     "([source](https://example.org/x))."),
                        Bullets(items=["first point", "second point"]),
                        TableRef(table_id="fmea_top", caption="Ranked modes"),
                        FigureRef(figure_id="fmea_pareto", caption="Pareto"),
                        Field(key="Engine", value="copt"),
                        TableRef(table_id="does_not_exist"),
                    ]),
            Section(section_id="certification", heading="Certification",
                    source="code", status="skipped", note="budget exhausted",
                    blocks=[Callout(kind="not_established",
                                    text="this section was not run in this study "
                                         "(budget exhausted).")]),
        ],
        tables={
            "headline": Table(table_id="headline", columns=["Item", "Value"],
                              rows=[["MC LOLE (h/yr)", "not established"],
                                    ["Cost at target", "1,234,568 €"]],
                              caption="Headline results"),
            "fmea_top": Table(table_id="fmea_top",
                              columns=["Rank", "Name", "Criticality"],
                              rows=[["1", "backup", "100,000 €/yr"],
                                    ["2", "import", "25,000 €/yr"],
                                    ["3", "spare", "not established"]]),
        },
        figures={"fmea_pareto": Figure(figure_id="fmea_pareto",
                                       png_file="figures/fmea_pareto.png",
                                       caption="criticality by mode")},
    )


def _headings(doc) -> list[str]:
    return [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")
            or p.style.name == "Title"]


def _bookmarks(doc) -> list[str]:
    starts = doc.element.body.iter(qn("w:bookmarkStart"))
    return [el.get(qn("w:name")) for el in starts]


def test_document_with_every_block_type_renders_as_a_docx():
    data = render_document_docx(_document(), figure_bytes={"fmea_pareto": _PNG_1x1})
    assert data[:2] == b"PK"
    text = _text(data)
    assert "Client study report" in text
    # The generated-from line names the version, hash, mode and profile.
    assert "cccccccccccc" in text and "local-llama" in text
    assert "version 2" in text.lower()
    assert "first point" in text and "second point" in text
    assert "the demand profile is frozen" in text
    assert "excludes load-shedding cost" in text
    assert "copt" in text


def test_headings_are_in_order_and_each_carries_its_section_bookmark():
    doc = Document(io.BytesIO(render_document_docx(
        _document(), figure_bytes={"fmea_pareto": _PNG_1x1})))
    heads = _headings(doc)
    assert heads[:4] == ["Client study report", "Executive summary",
                         "Residual failure modes", "Certification"]
    marks = _bookmarks(doc)
    assert marks == ["sec:executive_summary", "sec:fmea_top", "sec:certification"]
    # The bookmark wraps the heading run, and its ids are unique.
    starts = list(doc.element.body.iter(qn("w:bookmarkStart")))
    ends = list(doc.element.body.iter(qn("w:bookmarkEnd")))
    ids = [s.get(qn("w:id")) for s in starts]
    assert len(set(ids)) == len(ids) == len(ends)
    heading_p = next(p for p in doc.paragraphs if p.text == "Residual failure modes")
    assert heading_p._p.find(qn("w:bookmarkStart")) is not None
    assert heading_p._p.find(qn("w:bookmarkEnd")) is not None


def test_tables_have_the_right_row_counts_and_a_caption():
    doc = Document(io.BytesIO(render_document_docx(_document(), figure_bytes={})))
    assert len(doc.tables) == 2
    headline, fmea = doc.tables
    assert len(headline.rows) == 1 + 2
    assert [c.text for c in headline.rows[0].cells] == ["Item", "Value"]
    assert headline.rows[1].cells[1].text == "not established"
    assert len(fmea.rows) == 1 + 3
    assert fmea.rows[3].cells[2].text == "not established"
    assert fmea.style.name == "Table Grid"
    text = _text(render_document_docx(_document(), figure_bytes={}))
    assert "Headline results" in text      # the table's own caption
    assert "Ranked modes" in text          # the block's caption wins when given


def test_one_picture_per_figure_ref_with_bytes_else_a_sentence():
    with_png = render_document_docx(_document(), figure_bytes={"fmea_pareto": _PNG_1x1})
    assert _pictures(with_png) == 1
    assert "Pareto" in _text(with_png)
    without = render_document_docx(_document(), figure_bytes={})
    assert _pictures(without) == 0
    assert "fmea_pareto" in _text(without) and "not produced" in _text(without)


def test_a_missing_table_id_renders_a_disclosure_line_never_a_crash():
    data = render_document_docx(_document(), figure_bytes={})
    doc = Document(io.BytesIO(data))
    line = next(p for p in doc.paragraphs if "does_not_exist" in p.text)
    assert "not available" in line.text
    assert line.style.name == "Disclosure"


def test_callouts_map_to_their_styles_and_prefixes():
    doc = Document(io.BytesIO(render_document_docx(_document(), figure_bytes={})))
    gap = next(p for p in doc.paragraphs if "demand profile is frozen" in p.text)
    assert gap.text.startswith("Gap:")
    assert gap.style.name == "Disclosure"
    disc = next(p for p in doc.paragraphs if "excludes load-shedding" in p.text)
    assert disc.style.name == "Disclosure"
    ne = next(p for p in doc.paragraphs if "budget exhausted" in p.text)
    assert ne.text.startswith("Not established:")


def test_paragraph_markdown_inline_subset_becomes_runs():
    doc = Document(io.BytesIO(render_document_docx(_document(), figure_bytes={})))
    p = next(p for p in doc.paragraphs if p.text.startswith("The top mode"))
    assert "**" not in p.text and "`" not in p.text and "](" not in p.text
    bold = [r.text for r in p.runs if r.bold]
    italic = [r.text for r in p.runs if r.italic]
    code = [r.text for r in p.runs if r.font.name == "Consolas"]
    assert bold == ["top mode"]
    assert italic == ["backup"]
    assert code == ["100,000 €/yr"]
    assert "source" in p.text and "https://example.org/x" in p.text


def test_bullets_use_the_list_bullet_style_or_fall_back_to_a_marker():
    doc = Document(io.BytesIO(render_document_docx(_document(), figure_bytes={})))
    items = [p for p in doc.paragraphs if p.text.endswith("point")]
    assert len(items) == 2
    assert all(p.style.name == "List Bullet" or p.text.startswith("• ")
               for p in items)


def test_field_renders_bold_label_and_value():
    doc = Document(io.BytesIO(render_document_docx(_document(), figure_bytes={})))
    p = next(p for p in doc.paragraphs if p.text.startswith("Engine"))
    assert p.runs[0].bold and p.runs[0].text.startswith("Engine")
    assert "copt" in p.text


def test_numbers_to_check_appendix_only_when_unverified_numbers_exist():
    clean = render_document_docx(_document(), figure_bytes={})
    assert "Numbers to check" not in _text(clean)
    flagged = render_document_docx(
        _document(unverified=["3.5 h/yr", "42 MWh"]), figure_bytes={})
    doc = Document(io.BytesIO(flagged))
    heads = _headings(doc)
    assert heads[-1] == "Numbers to check"
    text = _text(flagged)
    assert "Residual failure modes" in text.split("Numbers to check")[-1]
    assert "3.5 h/yr" in text and "42 MWh" in text


def test_render_document_honours_a_user_template():
    tpl = Document()
    tpl.add_paragraph("Confidential — draft template body")
    tpl.sections[0].footer.paragraphs[0].text = "ACME Energy Consulting"
    buf = io.BytesIO()
    tpl.save(buf)
    data = render_document_docx(_document(), template=buf.getvalue(), figure_bytes={})
    doc = Document(io.BytesIO(data))
    assert doc.sections[0].footer.paragraphs[0].text == "ACME Energy Consulting"
    assert "Confidential — draft template body" not in _text(data)
    assert "sec:fmea_top" in _bookmarks(doc)


def test_an_established_section_with_a_prose_failure_note_states_it():
    """
    WP6 finding: the job records "prose not established" in `note` on a
    section whose EVIDENCE is ok; the writer used to state notes only on
    non-ok sections, so the reader never learned the prose was missing.
    """
    doc = _document()
    note = "prose not established: garbage twice (profile local-llama, model llama-3.1-8b)"
    doc.sections[1].note = note
    text = _text(render_document_docx(doc, figure_bytes={}))
    assert f"Note: {note}" in text


def test_a_note_already_carried_by_a_callout_is_not_stated_twice():
    doc = _document()
    doc.sections[0].note = "excludes load-shedding cost"   # same text as its Callout
    text = _text(render_document_docx(doc, figure_bytes={}))
    assert text.count("excludes load-shedding cost") == 1

