"""AA-05 S4a (A4.4, H9): the assistant's reports are strict.

Plan P1–P6 with GK B (32) V1–V6 (`.scratch/assistant-assurance/issues/
05-verified-artifacts.md`):

* P1/V2: the assistant's generate and regenerate run the job strict through
  an in-process switch; a client sending `strict` to the human routes gets
  the flag-only behaviour, and the same report exports to identical bytes.
* P2/V1: a statement the audit cannot check is withheld; an inline
  placeholder names the count and the reason only. The values appear only in
  the "Numbers to check" appendix (docx, the PDF's docx, get_report's
  audit), marked withheld. V3: an audit that raises withholds.
* P3/V6: a table equals a hand oracle from the fixture's raw numbers.
* P4/V5: required disclosures survive generate, regenerate and export.
* P5/V4: a strict regenerate of one section leaves the others byte-equal; a
  later human regenerate may un-withhold it, and get_report reads that.
* P6: the human routes are byte-identical with and without `strict`.
"""

from __future__ import annotations

import io
import json
import math
import pathlib
import tempfile

import pytest
from docx import Document
from fastapi import HTTPException

from services import chat_tools as T, upload_service
from services.llm_fake import FakeProvider
from services.reports import docx_writer, report_job, store
from services.reports.assemble import EXECUTIVE_SUMMARY_ID
from services.reports.docx_reader import body_text
from tests import test_chat_report_tools as _tools, test_workflow_tools as _workflow
from tests.test_chat_report_tools import (
    PROJECT,
    _draft,
    _install_provider,
    _poll,
    _store_eh_report,
)

# The report-tool fixtures, shared by name (pytest finds a fixture bound here).
project = _tools.project
helpers_state = _workflow.helpers_state

SECTIONS = [EXECUTIVE_SUMMARY_ID, "fmea_top"]
INJECTED = "Generator g saves 4,700 EUR a year."
PLACEHOLDER = (
    "Withheld: 1 number could not be checked against the study; see Numbers to check."
)


def _script(fmea: str = INJECTED) -> list[dict]:
    return [
        _draft(EXECUTIVE_SUMMARY_ID, "The plan meets 3.2 h/yr."),
        _draft("fmea_top", "Generator g dominates.", fmea),
    ]


def _assistant_report(monkeypatch, fmea: str = INJECTED) -> str:
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(fmea)))
    rid = T.generate_report(sections=SECTIONS)["report_id"]
    assert _poll()["status"] == "done"
    return rid


def _human_report(
    client, monkeypatch, body: dict | None = None, params: dict | None = None
) -> str:
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script()))
    started = client.post(
        f"/api/projects/{PROJECT}/reports/generate",
        json={"sections": SECTIONS, **(body or {})},
        params=params or {},
    )
    assert started.status_code == 200, started.text
    assert _poll()["status"] == "done"
    return started.json()["report_id"]


def _export_bytes(
    client, rid: str, *, version: int | None = None, params: dict | None = None
) -> bytes:
    response = client.post(
        f"/api/projects/{PROJECT}/reports/{rid}/export",
        json={"version": version, **(params or {})},
        params=params or {},
    )
    assert response.status_code == 200, response.text
    meta = response.json()
    return upload_service.get_upload_bytes(PROJECT, meta["file_id"], project_dir=None)


def _split_appendix(text: str) -> tuple[str, str]:
    body, _, appendix = text.partition("\nNumbers to check\n")
    return body, appendix


def _stored(project_dir, rid: str, version: int) -> dict:
    return json.loads(
        (store.report_dir(project_dir, rid) / f"v{version}.json").read_text()
    )


# ── P1/P2, V1: the assistant withholds, the placeholder names the count only ──


def test_the_assistant_withholds_an_unchecked_statement_behind_a_placeholder(
    project, monkeypatch
):
    rid = _assistant_report(monkeypatch)
    fmea = T.get_report(rid, section_id="fmea_top")["sections"][0]
    blocks = fmea["blocks"]
    assert blocks[0] == {"type": "paragraph", "md": "Generator g dominates."}
    assert blocks[1] == {"type": "callout", "kind": "gap", "text": PLACEHOLDER}
    assert fmea["audit"]["unverified"] == ["4,700 EUR"]
    assert fmea["audit"]["withheld"] is True
    # V1: the value is in no block of any section's body.
    for section in T.get_report(rid)["sections"]:
        assert "4,700" not in json.dumps(section.get("blocks") or [])
    # The summary's checked number is kept, unflagged.
    summary = T.get_report(rid, section_id=EXECUTIVE_SUMMARY_ID)["sections"][0]
    assert summary["blocks"][0] == {
        "type": "paragraph",
        "md": "The plan meets 3.2 h/yr.",
    }
    assert "withheld" not in summary.get("audit", {})


def test_the_withheld_value_is_only_in_the_docx_appendix_marked_withheld(
    project, client, monkeypatch
):
    rid = _assistant_report(monkeypatch)
    blob = upload_service.get_upload_bytes(
        PROJECT, T.export_report_docx(rid)["file_id"], project_dir=project
    )
    body, appendix = _split_appendix(body_text(blob))
    assert PLACEHOLDER in body
    assert "4,700" not in body and INJECTED not in body
    assert "4,700 EUR (unverified, withheld)" in appendix
    assert "Numbers marked withheld were taken out of the prose" in appendix


def test_the_pdf_converts_the_withheld_docx(project, monkeypatch):
    """The PDF is LibreOffice's conversion of the checked docx: the bytes the
    converter receives are the withheld document."""
    from services.reports import pdf

    rid = _assistant_report(monkeypatch)
    seen: list[bytes] = []
    monkeypatch.setattr(pdf, "pdf_available", lambda: True)
    monkeypatch.setattr(
        pdf,
        "convert_docx_to_pdf",
        lambda data, **_: seen.append(data) or b"%PDF-1.4 fake",
    )
    T.export_report_pdf(rid)
    body, appendix = _split_appendix(body_text(seen[0]))
    assert PLACEHOLDER in body and "4,700" not in body
    assert "4,700 EUR (unverified, withheld)" in appendix


def test_a_two_number_statement_names_two_and_no_value(project, monkeypatch):
    rid = _assistant_report(monkeypatch, fmea="It saves 4,700 EUR in 7 years.")
    fmea = T.get_report(rid, section_id="fmea_top")["sections"][0]
    gap = [
        b["text"]
        for b in fmea["blocks"]
        if b["type"] == "callout" and b["kind"] == "gap"
    ]
    assert gap == [
        "Withheld: 2 numbers could not be checked against the study; see Numbers to check."
    ]
    assert set(fmea["audit"]["unverified"]) == {"4,700 EUR", "7"}


def test_an_audit_that_raises_withholds_and_publishes_nothing(project, monkeypatch):
    """V3: fail closed through the assistant's path (hand mutant: an audit
    exception that publishes)."""

    def broken(paragraphs, facts):
        raise ValueError("cannot parse")

    monkeypatch.setattr(report_job, "audit", broken)
    rid = _assistant_report(monkeypatch, fmea="Generator g dominates again.")
    fmea = T.get_report(rid, section_id="fmea_top")["sections"][0]
    assert not [b for b in fmea["blocks"] if b["type"] in ("paragraph", "bullets")]
    gap = [
        b["text"]
        for b in fmea["blocks"]
        if b["type"] == "callout" and b["kind"] == "gap"
    ]
    assert gap and all(t.startswith("Withheld: ") for t in gap)
    assert "dominates" not in json.dumps(fmea["blocks"])


# ── V2/P6: the human routes are never strict ─────────────────────────────


@pytest.mark.parametrize("how", ["body", "query", "keyword"])
def test_a_client_asking_the_human_route_for_strict_gets_flag_only(
    project, client, monkeypatch, how
):
    body, params = {}, {}
    if how == "body":
        body = {"strict": True}
    elif how == "query":
        params = {"strict": "true"}
    else:
        body, params = (
            {"_ASSISTANT_STRICT": True, "assistant_strict": True},
            {"report_assistant_strict": "1"},
        )
    rid = _human_report(client, monkeypatch, body=body, params=params)
    fmea = T.get_report(rid, section_id="fmea_top")["sections"][0]
    assert [b for b in fmea["blocks"] if b["type"] == "paragraph"][1] == {
        "type": "paragraph",
        "md": INJECTED,
    }
    assert not [
        b
        for b in fmea["blocks"]
        if b["type"] == "callout" and b.get("text", "").startswith("Withheld")
    ]
    assert "withheld" not in fmea["audit"]


def test_the_human_export_is_byte_identical_with_and_without_strict(
    project, client, monkeypatch
):
    rid = _human_report(client, monkeypatch)
    plain = body_text(_export_bytes(client, rid))
    asked = body_text(_export_bytes(client, rid, params={"strict": True}))
    assert plain == asked
    _, appendix = _split_appendix(plain)
    assert "withheld" not in appendix and "4,700 EUR" in appendix


def test_the_human_generate_and_regenerate_store_the_same_bytes_with_and_without_strict(
    project, client, monkeypatch
):
    plain = _human_report(client, monkeypatch)
    asked = _human_report(
        client, monkeypatch, body={"strict": True}, params={"strict": "true"}
    )

    def comparable(rid: str, version: int) -> dict:
        doc = _stored(project, rid, version)
        for key in ("report_id", "created_at"):
            doc.pop(key)
        return doc

    assert comparable(plain, 1) == comparable(asked, 1)
    for rid in (plain, asked):
        _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", INJECTED)]))
        response = client.post(
            f"/api/projects/{PROJECT}/reports/{rid}/sections/fmea_top/regenerate",
            json={"strict": True} if rid == asked else {},
            params={"strict": "true"} if rid == asked else {},
        )
        assert response.status_code == 200, response.text
        assert _poll()["status"] == "done"
    assert comparable(plain, 2) == comparable(asked, 2)
    assert "withheld" not in json.dumps(_stored(project, plain, 2)).lower()


# ── P5/V4: a strict regenerate leaves the other sections byte-equal ─────────


def test_a_strict_regenerate_of_a_human_report_leaves_the_others_byte_equal(
    project, client, monkeypatch
):
    rid = _human_report(client, monkeypatch)
    _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", INJECTED)]))
    T.regenerate_report_section(rid, "fmea_top")
    assert _poll()["status"] == "done"
    v1, v2 = _stored(project, rid, 1), _stored(project, rid, 2)
    others = [s["section_id"] for s in v1["sections"] if s["section_id"] != "fmea_top"]
    assert others
    by1 = {s["section_id"]: s for s in v1["sections"]}
    by2 = {s["section_id"]: s for s in v2["sections"]}
    for sid in others:
        assert json.dumps(by1[sid], sort_keys=True) == json.dumps(
            by2[sid], sort_keys=True
        ), sid
    assert v1["tables"] == v2["tables"] and v1["figures"] == v2["figures"]
    fmea = T.get_report(rid, section_id="fmea_top")["sections"][0]
    assert {"type": "callout", "kind": "gap", "text": PLACEHOLDER} in fmea["blocks"]
    assert fmea["audit"]["withheld"] is True

    # V4: a later human, non-strict regenerate may put it back (master
    # behaviour); the assistant reads that version as stored, flagged.
    _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", INJECTED)]))
    response = client.post(
        f"/api/projects/{PROJECT}/reports/{rid}/sections/fmea_top/regenerate", json={}
    )
    assert response.status_code == 200, response.text
    assert _poll()["status"] == "done"
    v3 = T.get_report(rid, section_id="fmea_top")["sections"][0]
    assert {"type": "paragraph", "md": INJECTED} in v3["blocks"]
    assert v3["audit"] == {"unverified": ["4,700 EUR"], "verified": []}


# ── P4/V5: required disclosures survive generate, regenerate and export ────


def _dropping_disclosures(monkeypatch, *, once: bool = False):
    """Fault injection: the prose step loses the section's disclosure callouts."""
    real = report_job._with_prose
    calls = {"n": 0}

    def lossy(section, draft, facts):
        written = real(section, draft, facts)
        calls["n"] += 1
        if once and calls["n"] > 1:
            return written
        return written.model_copy(
            update={
                "blocks": [
                    b
                    for b in written.blocks
                    if not (b.type == "callout" and b.kind == "disclosure")
                ]
            }
        )

    monkeypatch.setattr(report_job, "_with_prose", lossy)


def test_a_strict_generate_that_drops_a_disclosure_fails_and_saves_nothing(
    project, monkeypatch
):
    _store_eh_report()
    _install_provider(
        monkeypatch, FakeProvider(_script("Generator g dominates again."))
    )
    _dropping_disclosures(monkeypatch)
    T.generate_report(sections=SECTIONS)
    status = _poll()
    assert status["status"] == "failed", status
    assert status["error_kind"] == "report_disclosure_missing"
    assert T.list_reports() == []


def test_a_strict_regenerate_that_drops_a_disclosure_fails_and_keeps_the_version(
    project, monkeypatch
):
    rid = _assistant_report(monkeypatch, fmea="Generator g dominates again.")
    stored = _stored(project, rid, 1)
    assert [
        b
        for s in stored["sections"]
        if s["section_id"] == "fmea_top"
        for b in s["blocks"]
        if b["type"] == "callout" and b["kind"] == "disclosure"
    ]
    _dropping_disclosures(monkeypatch)
    _install_provider(
        monkeypatch, FakeProvider([_draft("fmea_top", "Generator g dominates.")])
    )
    T.regenerate_report_section(rid, "fmea_top")
    status = _poll()
    assert (
        status["status"] == "failed"
        and status["error_kind"] == "report_disclosure_missing"
    )
    assert store.load_meta(project, rid).latest_version == 1


def test_the_human_regenerate_is_not_checked(project, client, monkeypatch):
    """P6: the disclosure check is the assistant's; a human regenerate with
    the same fault saves as on master."""
    rid = _human_report(client, monkeypatch)
    _dropping_disclosures(monkeypatch)
    _install_provider(
        monkeypatch, FakeProvider([_draft("fmea_top", "Generator g dominates.")])
    )
    response = client.post(
        f"/api/projects/{PROJECT}/reports/{rid}/sections/fmea_top/regenerate", json={}
    )
    assert response.status_code == 200, response.text
    assert _poll()["status"] == "done"
    assert store.load_meta(project, rid).latest_version == 2


def test_an_assistant_export_that_loses_a_disclosure_is_refused_and_saves_nothing(
    project, monkeypatch
):
    rid = _assistant_report(monkeypatch, fmea="Generator g dominates again.")
    before = {
        m.file_id for m in upload_service.list_uploads(PROJECT, project_dir=project)
    }
    real = docx_writer._disclosure

    def lossy(doc, text):
        if text in report_job.disclosure_texts(store.load_report(project, rid)):
            return None
        return real(doc, text)

    monkeypatch.setattr(docx_writer, "_disclosure", lossy)
    with pytest.raises(HTTPException) as exc:
        T.export_report_docx(rid)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "report_disclosure_missing"
    assert {
        m.file_id for m in upload_service.list_uploads(PROJECT, project_dir=project)
    } == before


def test_an_unreadable_export_is_refused(project, monkeypatch):
    from services.reports import docx_reader

    rid = _assistant_report(monkeypatch, fmea="Generator g dominates again.")

    def broken(data):
        raise ValueError("unreadable")

    monkeypatch.setattr(docx_reader, "body_text", broken)
    with pytest.raises(HTTPException) as exc:
        T.export_report_docx(rid)
    assert exc.value.detail["error_kind"] == "report_disclosure_missing"


# ── P3/V6: a table equals a hand oracle from the fixture's raw numbers ─────


def test_a_report_table_equals_the_hand_oracle_cell_for_cell():
    from services.reports import network_chapters as NC
    from services.reports.evidence import collect_evidence
    from services.reports.report_job import prepare_report_job
    from tests.test_report_job import _PROFILE
    from tests.test_report_network_chapters import _sources

    source = _sources()
    stats = source["reads"]["results_summary"]["statistics"]
    del stats[3:]
    stats[0]["Optimal Capacity"] = -5.25  # a negative value
    stats[1]["Installed Capacity"] = math.nan  # a missing value
    ev = collect_evidence(
        study_report=None, eh_report=None, worksheet=None, network=source
    )
    job = prepare_report_job(
        project_name="demo",
        project_dir=pathlib.Path(tempfile.mkdtemp()),
        evidence=ev,
        profile=_PROFILE,
        provider=FakeProvider([]),
        language="en",
        sections=[NC.HEADLINE],
        title="t",
        figure_pngs={},
    )
    doc = job.document

    # The oracle, from the raw numbers: largest optimal capacity first, two
    # decimals, a missing value stated, never drawn as a number.
    def cell(value):
        return (
            "not established"
            if value is None or (isinstance(value, float) and math.isnan(value))
            else f"{value:.2f}"
        )

    raw = sorted(stats, key=lambda r: -r["Optimal Capacity"])
    oracle = [["component", "carrier", "optimal_mw", "installed_mw"]] + [
        [
            r["level_0"],
            r["level_1"],
            cell(r["Optimal Capacity"]),
            cell(r["Installed Capacity"]),
        ]
        for r in raw
    ]
    assert oracle[1:] == [
        ["Generator", "c2", "12.00", "3.00"],
        ["Generator", "c1", "11.00", "not established"],
        ["Generator", "c0", "-5.25", "1.00"],
    ]

    table = doc.tables["capacity__rows"]
    assert [list(table.columns)] + [list(r) for r in table.rows] == oracle
    capacity = next(s for s in doc.sections if s.section_id == NC.CAPACITY)
    unit = [b.value for b in capacity.blocks if b.type == "field" and b.key == "unit"]
    assert unit == ["MW (MWh for stores)"]

    rendered = Document(
        io.BytesIO(docx_writer.render_document_docx(doc, figure_bytes={}))
    )
    grids = [[[c.text for c in row.cells] for row in t.rows] for t in rendered.tables]
    assert oracle in grids


def test_a_disclosure_required_twice_must_be_written_twice():
    """V5: the job's and the export's check count each disclosure."""
    need = ["Proxy only.", "Proxy only.", "Stage note: x"]
    assert report_job.missing_disclosures(need, ["Proxy only.", "Stage note: x"]) == [
        "Proxy only."
    ]
    assert report_job.missing_disclosures(need, "Proxy only.\nStage note: x") == [
        "Proxy only."
    ]
    assert (
        report_job.missing_disclosures(need, "Proxy only.\nProxy only.\nStage note: x")
        == []
    )
    assert report_job.missing_disclosures(need, need) == []


def test_only_a_withheld_gap_callout_marks_a_section():
    from services.reports import formatting as F

    text = F.withheld_placeholder(2)
    assert F.withheld_placeholder(0) == (
        "Withheld: a statement could not be checked against the study; see Numbers to check."
    )
    assert F.withheld_placeholder(1) == PLACEHOLDER
    assert F.is_withheld_block({"type": "callout", "kind": "gap", "text": text})
    assert not F.is_withheld_block(
        {"type": "callout", "kind": "disclosure", "text": text}
    )
    assert not F.is_withheld_block({"type": "paragraph", "kind": "gap", "text": text})
    assert not F.is_withheld_block(
        {"type": "callout", "kind": "gap", "text": "Gap: no read."}
    )
    assert not F.is_withheld_block({"type": "callout", "kind": "gap", "text": None})
    assert not F.is_withheld_section({"blocks": None, "audit": None})
    assert F.withheld_marked({"blocks": None, "audit": {"unverified": None}}) == []
    withheld = {
        "blocks": [{"type": "callout", "kind": "gap", "text": text}],
        "audit": {"unverified": ["7"]},
    }
    assert F.withheld_marked(withheld) == ["7 (unverified, withheld)"]


def test_a_strict_regenerate_that_checks_out_drops_the_old_placeholder(
    project, monkeypatch
):
    """Only disclosures are required: an earlier placeholder (a gap callout)
    may go when the rewritten prose checks out."""
    rid = _assistant_report(monkeypatch)
    _install_provider(
        monkeypatch, FakeProvider([_draft("fmea_top", "Generator g dominates.")])
    )
    T.regenerate_report_section(rid, "fmea_top")
    assert _poll()["status"] == "done"
    fmea = T.get_report(rid, section_id="fmea_top")["sections"][0]
    assert not [
        b
        for b in fmea["blocks"]
        if b["type"] == "callout" and b["text"].startswith("Withheld")
    ]
    assert {"type": "paragraph", "md": "Generator g dominates."} in fmea["blocks"]
