"""Study harness 07f-b: a report job that meets a busy report lock.

Refused before the save: `failed`, `error_kind: report_busy`, no version.
Refused after the save (figures, settings or generation receipt): `failed`,
`error_kind: report_busy`, `version: n`, never `done` (GK B (46) Q2).
"""

from __future__ import annotations

import contextlib

import pytest

from services.reports import store
from services.reports.assemble import EXECUTIVE_SUMMARY_ID
from services.reports.report_job import run_report_job
from services.llm_fake import FakeProvider
from tests.test_report_copy_lock import held_elsewhere
from tests.test_report_job import _draft, _job, evidence  # noqa: F401 -- fixture


def _full():
    return FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ])


def _rewrite():
    return FakeProvider([_draft("fmea_top", "Modes, rewritten.")])


def test_a_report_busy_before_the_save_is_typed_and_has_no_version(
    tmp_path, evidence, monkeypatch,
):
    def busy(*_a, **_k):
        raise store.ReportBusy("report 00000000000000aa is being written")

    monkeypatch.setattr(store, "create_report", busy)
    job = _job(tmp_path, evidence, _full())
    with pytest.raises(store.ReportBusy):
        run_report_job(job)
    assert job.record["status"] == "failed"
    assert job.record["error_kind"] == "report_busy"
    assert job.record["version"] is None
    assert "is being written" in job.record["error"]


def test_a_project_gone_is_a_plain_failure(tmp_path, evidence, monkeypatch):
    def gone(*_a, **_k):
        raise store.ReportProjectGone("project directory gone")

    monkeypatch.setattr(store, "create_report", gone)
    job = _job(tmp_path, evidence, _full())
    with pytest.raises(store.ReportProjectGone):
        run_report_job(job)
    assert job.record["status"] == "failed"
    assert "error_kind" not in job.record and job.record["version"] is None


def _settings_refused(monkeypatch, project_dir, rid):
    def busy(*_a, **_k):
        raise store.ReportBusy(f"report {rid} is being written")

    monkeypatch.setattr(store, "update_meta", busy)


def _receipt_refused(monkeypatch, project_dir, rid):
    """The generation receipt's own read-modify-write meets the report's
    lock held elsewhere: taken once the version and settings are written."""
    monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 0.2)
    real, stack = store.load_report, contextlib.ExitStack()

    def then_held(*a, **k):
        out = real(*a, **k)
        stack.enter_context(held_elsewhere(project_dir, rid))
        return out

    monkeypatch.setattr(store, "load_report", then_held)
    return stack


@pytest.mark.parametrize("refuse", [_settings_refused, _receipt_refused],
                         ids=["settings", "receipt"])
def test_saved_then_refused_is_failed_with_the_saved_version(
    tmp_path, evidence, monkeypatch, refuse,
):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    job = _job(tmp_path, evidence, _rewrite(), base_document=v1,
               regenerate_section="fmea_top", instruction="shorter",
               meta_updates={"template_language": "de"})
    stack = refuse(monkeypatch, tmp_path, v1.report_id)
    try:
        with pytest.raises(store.ReportBusy):
            run_report_job(job)
    finally:
        if stack is not None:
            stack.close()
    assert job.record["status"] == "failed"  # never `done` (#1170)
    assert job.record["error_kind"] == "report_busy"
    assert job.record["version"] == 2
    assert job.record["error"].startswith("version 2 was saved")
    # Version 2 exists and can be opened.
    assert store.load_report(tmp_path, v1.report_id, 2).version == 2


def _refused_v2(tmp_path, evidence, monkeypatch):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    assert store.load_meta(tmp_path, v1.report_id).generation is not None
    job = _job(tmp_path, evidence, _rewrite(), base_document=v1,
               regenerate_section="fmea_top", instruction="shorter")
    with _receipt_refused(monkeypatch, tmp_path, v1.report_id):
        with pytest.raises(store.ReportBusy):
            run_report_job(job)
    monkeypatch.setattr(store, "load_report", _real_load)
    return v1


_real_load = store.load_report


def test_the_report_list_shows_no_receipt_for_a_version_whose_receipt_was_refused(
    tmp_path, evidence, monkeypatch,
):
    v1 = _refused_v2(tmp_path, evidence, monkeypatch)
    [meta] = store.list_reports(tmp_path)
    assert meta.latest_version == 2
    assert meta.generation is None  # never version 1's


def test_a_written_receipt_is_the_new_versions_own(tmp_path, evidence):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    job = _job(tmp_path, evidence, _rewrite(), base_document=v1,
               regenerate_section="fmea_top", instruction="shorter")
    run_report_job(job)
    [meta] = store.list_reports(tmp_path)
    assert meta.latest_version == 2
    assert meta.generation["sections"] == ["fmea_top"]
