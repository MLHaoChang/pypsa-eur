"""LC-43, LC-D21.report_gates: each report gate refuses, and the stored versions stay as they were.

The seam is the copilot's report tools over one stored study report and a scripted
model, as in ``test_lc06_d21_reports.py`` (archetype ``strong_grid``, ENS cap 10,
MC LOLE 3.21 h/yr; sections ``executive_summary`` and ``fmea_top``).

Hand oracle. After every refusal, the project still lists exactly the reports it had,
each at the version it had, and the headline table still reads MC LOLE 3.21 h/yr:

- an unknown section is 404 report_section_not_found on ``generate_report`` and on
  ``regenerate_report_section``; an unknown table is 404 report_table_not_found; an
  unknown version is 404 report_not_found;
- a second generate while one runs is 409 report_job_in_flight; aborting it leaves the
  report ``aborted`` with each unwritten section carrying an ``aborted`` note and no
  model prose; a generate after the abort runs to ``done`` as its own report, and
  the earlier reports are unchanged;
- an edited Word copy that changes the headline MC LOLE to 1.00 and the summary to
  "1.0 h/yr" becomes version 2: the summary is the user's edit (``source``
  ``user_edit``) and the edited table is stored as ``headline_edited_v2``; the
  ``headline`` table still reads 3.21 h/yr, and version 1 still reads "The plan meets
  3.2 h/yr." with 3.21 h/yr in its headline. (That version 2's delivered headline then
  reads 1.00 h/yr unflagged is the pending row ``LC-D21.edited_admitted_number``.)
- a Word file that is not a copy of the report is 400 roundtrip_not_a_report;
- a mapping plan with no template bound is 400 no_template;
- PDF export without LibreOffice is 501 pdf_not_available.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import io
import threading

import pytest
from docx import Document
from fastapi import HTTPException

from services import chat_tools as T, upload_service
from services.llm_fake import FakeProvider
from services.reports import pdf
from services.reports.docx_writer import DOCX_MIME
from tests.test_chat_report_tools import (
    PROJECT,
    _Holding,
    _install_provider,
    _poll,
    _script,
    project,  # noqa: F401
)
from tests.test_lc06_d21_reports import HEADLINE, SECTIONS, _docx, _generated
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _stored() -> dict[str, int]:
    return {
        r["report_id"]: T.get_report(r["report_id"])["version"]
        for r in T.list_reports()
    }


def _refused(call, status: int, kind: str) -> None:
    with pytest.raises(HTTPException) as failure:
        call()
    assert failure.value.status_code == status, failure.value.detail
    assert failure.value.detail["error_kind"] == kind, failure.value.detail


def _upload(project_dir, doc) -> str:
    buffer = io.BytesIO()
    doc.save(buffer)
    return upload_service.add_upload(
        PROJECT,
        buffer.getvalue(),
        "edited.docx",
        DOCX_MIME,
        kind="report_roundtrip",
        project_dir=project_dir,
    ).file_id


def test_an_unknown_section_table_or_version_is_refused_and_changes_nothing(
    project, monkeypatch
):
    report_id = _generated(monkeypatch)
    before = _stored()
    _refused(
        lambda: T.generate_report(sections=["no_such_section"]),
        404,
        "report_section_not_found",
    )
    _refused(
        lambda: T.regenerate_report_section(report_id, "no_such_section"),
        404,
        "report_section_not_found",
    )
    _refused(
        lambda: T.get_report_table(report_id, "no_such_table"),
        404,
        "report_table_not_found",
    )
    _refused(lambda: T.get_report(report_id, version=7), 404, "report_not_found")
    assert _stored() == before == {report_id: 1}
    assert T.get_report_table(report_id, "headline")["items"] == HEADLINE


def test_a_second_run_is_refused_an_abort_writes_no_prose_and_a_restart_is_its_own_report(
    project, client, monkeypatch
):
    first = _generated(monkeypatch)
    hold = threading.Event()
    _install_provider(monkeypatch, _Holding(_script(), hold))
    try:
        second = T.generate_report(sections=SECTIONS)["report_id"]
        _refused(
            lambda: T.generate_report(sections=SECTIONS), 409, "report_job_in_flight"
        )
        assert T.abort_report_generation() == {"status": "running", "aborting": True}
    finally:
        hold.set()
    assert _poll()["status"] == "aborted"
    aborted = {s["section_id"]: s for s in T.get_report(second)["sections"]}
    for sid in SECTIONS:
        assert aborted[sid]["source"] == "code", aborted[sid]
        assert aborted[sid]["note"].startswith("prose not established: aborted"), (
            aborted[sid]
        )
    paragraphs = [p.text for p in _docx(client, second).paragraphs]
    assert (
        "The plan meets 3.2 h/yr." not in paragraphs
        and "Generator g dominates." not in paragraphs
    )

    _install_provider(monkeypatch, FakeProvider(_script()))
    third = T.generate_report(sections=SECTIONS)["report_id"]
    assert _poll()["status"] == "done"
    assert len({first, second, third}) == 3
    assert _stored() == {first: 1, second: 1, third: 1}
    assert T.get_report(third)["sections"][0]["blocks"][0] == {
        "type": "paragraph",
        "md": "The plan meets 3.2 h/yr.",
    }
    assert T.get_report(first)["sections"][0]["blocks"][0] == {
        "type": "paragraph",
        "md": "The plan meets 3.2 h/yr.",
    }


def test_an_edited_copy_is_a_new_user_version_and_leaves_the_admitted_table_and_version_one(
    project, client, monkeypatch
):
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    for row in doc.tables[0].rows:
        if row.cells[0].text == "MC LOLE (h/yr)":
            row.cells[1].text = "1.00 h/yr"
    for paragraph in doc.paragraphs:
        if paragraph.text == "The plan meets 3.2 h/yr.":
            paragraph.text = "The plan meets 1.0 h/yr."

    out = T.import_edited_report(
        report_id, _upload(project, doc), bind_as_template=False
    )

    assert out["version"] == 2 and out["changed"] == ["executive_summary"]
    summary = T.get_report(report_id)["sections"][0]
    assert summary["source"] == "user_edit"
    assert summary["blocks"][0] == {
        "type": "paragraph",
        "md": "The plan meets 1.0 h/yr.",
    }
    tables = [b["table_id"] for b in summary["blocks"] if "table_id" in b]
    assert "headline" not in tables and "headline_edited_v2" in tables
    # The admitted table stays under its own id, and version 1 still reads as delivered.
    assert T.get_report_table(report_id, "headline")["items"] == HEADLINE
    first = T.get_report(report_id, version=1)["sections"][0]
    assert first["source"] != "user_edit"
    assert first["blocks"][0] == {"type": "paragraph", "md": "The plan meets 3.2 h/yr."}
    headline = [
        [c.text for c in row.cells]
        for row in _docx(client, report_id, 1).tables[0].rows
    ]
    assert headline == [["Item", "Value"]] + HEADLINE


def test_a_word_file_that_is_not_the_report_merges_nothing(project, monkeypatch):
    report_id = _generated(monkeypatch)
    before = _stored()
    other = Document()
    other.add_heading("Quarterly note", 1)
    other.add_paragraph("The plan meets 1.0 h/yr.")
    _refused(
        lambda: T.import_edited_report(report_id, _upload(project, other)),
        400,
        "roundtrip_not_a_report",
    )
    assert _stored() == before
    assert T.get_report(report_id)["sections"][0]["blocks"][0] == {
        "type": "paragraph",
        "md": "The plan meets 3.2 h/yr.",
    }


def test_no_mapping_without_a_template_and_no_pdf_without_a_converter(
    project, monkeypatch
):
    report_id = _generated(monkeypatch)
    before = _stored()
    _refused(
        lambda: T.set_report_mapping(report_id, {"sections": {}}), 400, "no_template"
    )
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    uploads = sorted(
        m.file_id for m in upload_service.list_uploads(PROJECT, project_dir=project)
    )
    _refused(lambda: T.export_report_pdf(report_id), 501, "pdf_not_available")
    assert (
        sorted(
            m.file_id for m in upload_service.list_uploads(PROJECT, project_dir=project)
        )
        == uploads
    )
    assert _stored() == before
