"""LC-43, LC-D21.report_gates: each report gate refuses, and the stored versions stay as they were.

The seam is the copilot's report tools over one stored study report and a scripted
model, as in ``test_lc06_d21_reports.py`` (archetype ``strong_grid``, ENS cap 10,
MC LOLE 3.21 h/yr; sections ``executive_summary`` and ``fmea_top``).

Hand oracle. After every refusal, ``lc43_gates.state`` (the open network's fingerprint,
each report's version files, every upload and the blob count) is as it was, the project
still lists exactly the reports it had at the versions it had, and the headline table
still reads MC LOLE 3.21 h/yr:

- an unknown section is 404 report_section_not_found on ``generate_report`` and on
  ``regenerate_report_section``; an unknown table is 404 report_table_not_found; an
  unknown version is 404 report_not_found;
- a second generate while one runs is 409 report_job_in_flight; aborting it leaves the
  report ``aborted`` with each unwritten section carrying an ``aborted`` note and no
  model prose; a generate after the abort runs to ``done`` as its own report, and
  the earlier reports are unchanged;
- a deleted report is 404 report_not_found to get, regenerate and export;
- an edited Word copy (bound as the template or not) that changes the headline MC LOLE
  to 1.00 becomes version 2 whose DOCX headline still reads the admitted 3.21 h/yr and
  whose "Numbers to check" lists "Executive summary: 1.00 h/yr (unverified, withheld)"
  (LC-43a); the ``headline`` table still reads 3.21 h/yr under its own id and version 1
  still reads "The plan meets 3.2 h/yr." with 3.21 h/yr in its headline;
- a copy that edits the summary prose to "The plan meets 1.0 h/yr." becomes version 2
  that keeps the sentence and lists "Executive summary: 1.0 h/yr" under "Numbers to
  check" (the section's audit re-run against the facts version 1 verified, LC-43a);
- table cells are compared one by one (GK B 52): two rows that swap values, a unit
  changed beside the number ("3.21 min/yr") or in the row label ("MC LOLE (min/yr)")
  are withheld and listed the same way, as is an added row "42 MW"; a reformat
  ("3.210 h/yr"), a label renamed in a row with no number and an added row with no
  number are accepted with no Numbers to check entry;
- a copy that changes only the summary prose to "The plan is robust." becomes version 2
  with that summary's ``source`` ``user_edit``, the headline and version 1 as above, and
  a diff with version 3 is 404 report_version_not_found;
- a Word file that is not a copy of the report is 400 roundtrip_not_a_report;
- a mapping plan with no template bound is 400 no_template;
- PDF export without LibreOffice is 501 pdf_not_available.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import io
import threading

import pytest
from docx import Document

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
from tests.lc43_gates import refused, state
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _stored() -> dict[str, int]:
    return {
        r["report_id"]: T.get_report(r["report_id"])["version"]
        for r in T.list_reports()
    }


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
    before, kept = _stored(), state(project)
    refused(
        lambda: T.generate_report(sections=["no_such_section"]),
        404,
        "report_section_not_found",
    )
    refused(
        lambda: T.regenerate_report_section(report_id, "no_such_section"),
        404,
        "report_section_not_found",
    )
    refused(
        lambda: T.get_report_table(report_id, "no_such_table"),
        404,
        "report_table_not_found",
    )
    refused(lambda: T.get_report(report_id, version=7), 404, "report_not_found")
    assert _stored() == before == {report_id: 1}
    assert state(project) == kept
    assert T.get_report_table(report_id, "headline")["items"] == HEADLINE


def test_a_deleted_report_is_gone_from_every_report_tool(project, monkeypatch):
    report_id = _generated(monkeypatch)
    assert T.delete_report(report_id) == {"deleted": True, "report_id": report_id}
    assert _stored() == {}
    kept = state(project)
    refused(lambda: T.get_report(report_id), 404, "report_not_found")
    refused(
        lambda: T.regenerate_report_section(report_id, "executive_summary"),
        404,
        "report_not_found",
    )
    refused(lambda: T.export_report_docx(report_id), 404, "report_not_found")
    assert _stored() == {}
    assert state(project) == kept


def test_a_second_run_is_refused_an_abort_writes_no_prose_and_a_restart_is_its_own_report(
    project, client, monkeypatch
):
    first = _generated(monkeypatch)
    hold = threading.Event()
    _install_provider(monkeypatch, _Holding(_script(), hold))
    try:
        second = T.generate_report(sections=SECTIONS)["report_id"]
        kept = state(project)
        refused(
            lambda: T.generate_report(sections=SECTIONS), 409, "report_job_in_flight"
        )
        assert state(project) == kept
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


def _version_one_reads_as_delivered(client, report_id) -> None:
    first = T.get_report(report_id, version=1)["sections"][0]
    assert first["source"] != "user_edit"
    assert first["blocks"][0] == {"type": "paragraph", "md": "The plan meets 3.2 h/yr."}
    headline = [
        [c.text for c in row.cells]
        for row in _docx(client, report_id, 1).tables[0].rows
    ]
    assert headline == [["Item", "Value"]] + HEADLINE


@pytest.mark.parametrize("bind_as_template", [False, True])
def test_a_copy_that_changes_an_admitted_number_leaves_the_headline_and_version_one(
    project, client, monkeypatch, bind_as_template
):
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    for row in doc.tables[0].rows:
        if row.cells[0].text == "MC LOLE (h/yr)":
            row.cells[1].text = "1.00 h/yr"

    out = T.import_edited_report(
        report_id, _upload(project, doc), bind_as_template=bind_as_template
    )

    assert out["version"] == 2
    delivered = _docx(client, report_id, 2)
    headline = [[c.text for c in row.cells] for row in delivered.tables[0].rows]
    assert headline == [["Item", "Value"]] + HEADLINE
    paragraphs = [p.text for p in delivered.paragraphs]
    assert "Executive summary: 1.00 h/yr (unverified, withheld)" in paragraphs
    assert T.get_report_table(report_id, "headline")["items"] == HEADLINE
    _version_one_reads_as_delivered(client, report_id)


_LOLE, _CAP = "MC LOLE (h/yr)", "ENS cap (‱ of demand)"


@pytest.mark.parametrize(
    "edits, listed",
    [
        # T1: two rows swap their values; the multiset of numbers is unchanged.
        ({_CAP: (None, "3.21 h/yr"), _LOLE: (None, "10.00")},
         "3.21 h/yr (unverified, withheld), 10.00 (unverified, withheld)"),
        # T2: a unit changed beside the number, and in the row label.
        ({_LOLE: (None, "3.21 min/yr")}, "3.21 min/yr (unverified, withheld)"),
        ({_LOLE: ("MC LOLE (min/yr)", None)}, "MC LOLE (min/yr) (unverified, withheld)"),
    ],
    ids=["swap", "unit", "label_unit"],
)
def test_a_table_edit_that_moves_or_relabels_an_admitted_number_is_withheld(
    project, client, monkeypatch, edits, listed
):
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    for row in doc.tables[0].rows:
        label, value = edits.get(row.cells[0].text, (None, None))
        if value is not None:
            row.cells[1].text = value
        if label is not None:
            row.cells[0].text = label

    assert T.import_edited_report(report_id, _upload(project, doc))["version"] == 2

    delivered = _docx(client, report_id, 2)
    headline = [[c.text for c in row.cells] for row in delivered.tables[0].rows]
    assert headline == [["Item", "Value"]] + HEADLINE
    assert f"Executive summary: {listed}" in [p.text for p in delivered.paragraphs]


def test_an_added_row_that_states_a_number_is_withheld(project, client, monkeypatch):
    """A row the admitted table does not have, as the reshaped-table pairing reads it."""
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    row = doc.tables[0].add_row()
    row.cells[0].text, row.cells[1].text = "Peak shed (MW)", "42 MW"

    assert T.import_edited_report(report_id, _upload(project, doc))["version"] == 2

    delivered = _docx(client, report_id, 2)
    headline = [[c.text for c in row.cells] for row in delivered.tables[0].rows]
    assert headline == [["Item", "Value"]] + HEADLINE
    paragraphs = [p.text for p in delivered.paragraphs]
    assert "Executive summary: 42 MW (unverified, withheld)" in paragraphs


@pytest.mark.parametrize(
    "edit, shown",
    [
        # T3: "3.210 h/yr" states the admitted 3.21 h/yr.
        ("reformat", [_LOLE, "3.210 h/yr"]),
        # A label renamed in a row that holds no number.
        ("label", ["Archetype name", "strong_grid"]),
        # A row added with no number in it (the reshaped-table pairing).
        ("row", ["Reviewer", "not established"]),
    ],
)
def test_a_table_edit_that_changes_no_number_is_accepted(
    project, client, monkeypatch, edit, shown
):
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    table = doc.tables[0]
    if edit == "row":
        row = table.add_row()
        row.cells[0].text, row.cells[1].text = shown
    for row in table.rows:
        if edit == "reformat" and row.cells[0].text == _LOLE:
            row.cells[1].text = "3.210 h/yr"
        if edit == "label" and row.cells[0].text == "Archetype":
            row.cells[0].text = "Archetype name"

    assert T.import_edited_report(report_id, _upload(project, doc))["version"] == 2

    delivered = _docx(client, report_id, 2)
    headline = [[c.text for c in row.cells] for row in delivered.tables[0].rows]
    assert shown in headline
    paragraphs = [p.text for p in delivered.paragraphs]
    assert not [p for p in paragraphs if p.startswith("Executive summary:")]
    assert not [p for p in paragraphs if "Withheld:" in p]


@pytest.mark.parametrize("bind_as_template", [False, True])
def test_a_copy_that_changes_an_admitted_number_in_prose_lists_it_to_check(
    project, client, monkeypatch, bind_as_template
):
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    for paragraph in doc.paragraphs:
        if paragraph.text == "The plan meets 3.2 h/yr.":
            paragraph.text = "The plan meets 1.0 h/yr."

    out = T.import_edited_report(
        report_id, _upload(project, doc), bind_as_template=bind_as_template
    )

    assert out["version"] == 2
    summary = T.get_report(report_id)["sections"][0]
    assert summary["source"] == "user_edit"
    assert summary["blocks"][0] == {"type": "paragraph", "md": "The plan meets 1.0 h/yr."}
    assert summary["audit"]["unverified"] == ["1.0 h/yr"]
    paragraphs = [p.text for p in _docx(client, report_id, 2).paragraphs]
    assert "Executive summary: 1.0 h/yr" in paragraphs
    assert T.get_report_table(report_id, "headline")["items"] == HEADLINE
    _version_one_reads_as_delivered(client, report_id)


@pytest.mark.parametrize("bind_as_template", [False, True])
def test_a_prose_only_copy_is_a_new_user_version_and_leaves_version_one(
    project, client, monkeypatch, bind_as_template
):
    report_id = _generated(monkeypatch)
    doc = _docx(client, report_id)
    for paragraph in doc.paragraphs:
        if paragraph.text == "The plan meets 3.2 h/yr.":
            paragraph.text = "The plan is robust."

    out = T.import_edited_report(
        report_id, _upload(project, doc), bind_as_template=bind_as_template
    )

    assert out["version"] == 2
    assert state(project)["reports"] == {report_id: 2}
    summary = T.get_report(report_id)["sections"][0]
    assert summary["source"] == "user_edit"
    assert summary["blocks"][0] == {"type": "paragraph", "md": "The plan is robust."}
    assert T.get_report_table(report_id, "headline")["items"] == HEADLINE
    _version_one_reads_as_delivered(client, report_id)
    kept = state(project)
    refused(
        lambda: T.diff_report_versions(report_id, 1, 3), 404, "report_version_not_found"
    )
    assert state(project) == kept


def test_a_word_file_that_is_not_the_report_merges_nothing(project, monkeypatch):
    report_id = _generated(monkeypatch)
    before = _stored()
    other = Document()
    other.add_heading("Quarterly note", 1)
    other.add_paragraph("The plan meets 1.0 h/yr.")
    upload = _upload(project, other)
    kept = state(project)
    refused(
        lambda: T.import_edited_report(report_id, upload),
        400,
        "roundtrip_not_a_report",
    )
    assert _stored() == before
    assert state(project) == kept
    assert T.get_report(report_id)["sections"][0]["blocks"][0] == {
        "type": "paragraph",
        "md": "The plan meets 3.2 h/yr.",
    }


def test_no_mapping_without_a_template_and_no_pdf_without_a_converter(
    project, monkeypatch
):
    report_id = _generated(monkeypatch)
    before, kept = _stored(), state(project)
    refused(
        lambda: T.set_report_mapping(report_id, {"sections": {}}), 400, "no_template"
    )
    assert state(project) == kept
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    refused(lambda: T.export_report_pdf(report_id), 501, "pdf_not_available")
    assert state(project) == kept
    assert _stored() == before
