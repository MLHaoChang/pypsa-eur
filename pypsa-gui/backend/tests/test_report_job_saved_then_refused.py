# ruff: noqa: F811 -- fixtures imported from other test modules are parameters here
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
    return FakeProvider(
        [
            _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
            _draft("frontier", "Frontier."),
            _draft("fmea_top", "Modes."),
        ]
    )


def _rewrite():
    return FakeProvider([_draft("fmea_top", "Modes, rewritten.")])


def test_a_report_busy_before_the_save_is_typed_and_has_no_version(
    tmp_path,
    evidence,
    monkeypatch,
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
    """The settings' write meets the report's lock held elsewhere."""
    monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 0.2)
    real, stack = store.update_meta, contextlib.ExitStack()

    def held_first(*a, **k):
        stack.enter_context(held_elsewhere(project_dir, rid))
        return real(*a, **k)

    monkeypatch.setattr(store, "update_meta", held_first)
    return stack


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


@pytest.mark.parametrize(
    "refuse", [_settings_refused, _receipt_refused], ids=["settings", "receipt"]
)
def test_saved_then_refused_is_failed_with_the_saved_version(
    tmp_path,
    evidence,
    monkeypatch,
    refuse,
):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    job = _job(
        tmp_path,
        evidence,
        _rewrite(),
        base_document=v1,
        regenerate_section="fmea_top",
        instruction="shorter",
        meta_updates={"template_language": "de"},
    )
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
    meta = store.load_meta(tmp_path, v1.report_id)
    assert meta.generation is None
    if refuse is _settings_refused:
        assert meta.template_language is None
        assert meta.settings_unrecorded == {
            "version": 2,
            "fields": ["generation", "template_language"],
        }
    else:  # the settings landed and left the mark
        assert meta.template_language == "de"
        assert meta.settings_unrecorded == {"version": 2, "fields": ["generation"]}
    # Version 2 exists and can be opened.
    assert store.load_report(tmp_path, v1.report_id, 2).version == 2


def _refused_v2(tmp_path, evidence, monkeypatch, *, v1=None):
    """Version 2 saved by a regenerate whose generation receipt was refused."""
    v1 = v1 or run_report_job(_job(tmp_path, evidence, _full()))
    assert store.load_meta(tmp_path, v1.report_id).generation is not None
    job = _job(
        tmp_path,
        evidence,
        _rewrite(),
        base_document=v1,
        regenerate_section="fmea_top",
        instruction="shorter",
    )
    with _receipt_refused(monkeypatch, tmp_path, v1.report_id):
        with pytest.raises(store.ReportBusy):
            run_report_job(job)
    monkeypatch.setattr(store, "load_report", _real_load)
    return v1


_real_load = store.load_report


def test_the_report_list_shows_no_receipt_for_a_version_whose_receipt_was_refused(
    tmp_path,
    evidence,
    monkeypatch,
):
    _refused_v2(tmp_path, evidence, monkeypatch)
    [meta] = store.list_reports(tmp_path)
    assert meta.latest_version == 2
    assert meta.generation is None  # never version 1's
    # E1: told apart from a report no job ever wrote (also `generation: null`).
    assert meta.settings_unrecorded == {"version": 2, "fields": ["generation"]}


def test_a_written_receipt_is_the_new_versions_own(tmp_path, evidence):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    job = _job(
        tmp_path,
        evidence,
        _rewrite(),
        base_document=v1,
        regenerate_section="fmea_top",
        instruction="shorter",
    )
    run_report_job(job)
    [meta] = store.list_reports(tmp_path)
    assert meta.latest_version == 2
    assert meta.generation["sections"] == ["fmea_top"]
    assert meta.settings_unrecorded is None  # the job's last write removed the mark


def test_a_new_reports_settings_refused_are_marked_unrecorded(
    tmp_path,
    evidence,
    monkeypatch,
):
    job = _job(
        tmp_path,
        evidence,
        _full(),
        meta_updates={"template_mode": "tagged", "template_language": "de"},
    )
    _settings_refused(monkeypatch, tmp_path, job.report_id)
    with pytest.raises(store.ReportBusy):
        run_report_job(job)
    assert job.record["status"] == "failed" and job.record["version"] == 1
    assert job.record["error_kind"] == "report_busy"
    meta = store.load_meta(tmp_path, job.report_id)
    assert meta.template_mode is None and meta.generation is None
    assert meta.settings_unrecorded == {
        "version": 1,
        "fields": ["generation", "template_language", "template_mode"],
    }


def test_a_field_the_job_does_not_write_still_carries(tmp_path, evidence, monkeypatch):
    """E4: the save clears only `generation` and this job's settings."""
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    store.update_meta(
        tmp_path, v1.report_id, template_language="de", roundtrip_file_id="up-1"
    )
    _refused_v2(tmp_path, evidence, monkeypatch, v1=v1)
    meta = store.load_meta(tmp_path, v1.report_id)
    assert meta.template_language == "de" and meta.roundtrip_file_id == "up-1"
    assert meta.settings_unrecorded["fields"] == ["generation"]


def test_a_user_edit_carries_every_field_and_the_mark(tmp_path, evidence, monkeypatch):
    v1 = _refused_v2(tmp_path, evidence, monkeypatch)
    store.update_meta(tmp_path, v1.report_id, template_language="de")
    v2 = store.load_report(tmp_path, v1.report_id, 2)
    assert store.save_version(tmp_path, v2) == 3
    meta = store.load_meta(tmp_path, v1.report_id)
    assert meta.template_language == "de"
    # Version 3 has no receipt either, so the mark moves to it.
    assert meta.settings_unrecorded == {"version": 3, "fields": ["generation"]}


def test_a_field_written_again_leaves_the_mark(tmp_path, evidence, monkeypatch):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    v1_doc = store.load_report(tmp_path, v1.report_id, 1)
    store.save_version(tmp_path, v1_doc, pending=("generation", "mapping_plan"))
    store.update_meta(tmp_path, v1.report_id, mapping_plan=None)  # re-mapped (cleared)
    meta = store.load_meta(tmp_path, v1.report_id)
    assert meta.settings_unrecorded == {"version": 2, "fields": ["generation"]}
    store.update_meta(tmp_path, v1.report_id, generation={"sections": []})
    assert store.load_meta(tmp_path, v1.report_id).settings_unrecorded is None
    with pytest.raises(TypeError):  # the mark is the store's, never set directly
        store.update_meta(tmp_path, v1.report_id, settings_unrecorded=None)


def test_an_unrecorded_field_stays_marked_through_the_next_jobs_save(
    tmp_path,
    evidence,
    monkeypatch,
):
    """A later job writes only its own fields; one still null stays marked."""
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    v1_doc = store.load_report(tmp_path, v1.report_id, 1)
    store.save_version(tmp_path, v1_doc, pending=("generation", "mapping_plan"))
    v2 = store.load_report(tmp_path, v1.report_id, 2)
    run_report_job(
        _job(
            tmp_path,
            evidence,
            _rewrite(),
            base_document=v2,
            regenerate_section="fmea_top",
            instruction="shorter",
        )
    )
    meta = store.load_meta(tmp_path, v1.report_id)
    assert meta.generation is not None and meta.mapping_plan is None
    assert meta.settings_unrecorded == {"version": 3, "fields": ["mapping_plan"]}


def test_an_unreadable_meta_after_the_save_is_never_done(
    tmp_path, evidence, monkeypatch
):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    meta_path = store.report_dir(tmp_path, v1.report_id) / "meta.json"
    real = store.load_report

    def then_unreadable(*a, **k):
        out = real(*a, **k)
        meta_path.write_text("{not json", encoding="utf-8")
        return out

    monkeypatch.setattr(store, "load_report", then_unreadable)
    job = _job(
        tmp_path,
        evidence,
        _rewrite(),
        base_document=v1,
        regenerate_section="fmea_top",
        instruction="shorter",
    )
    with pytest.raises(store.ReportStoreError):
        run_report_job(job)
    assert job.record["status"] == "failed" and job.record["version"] == 2
    assert "unreadable" in job.record["error"]


def test_a_field_that_is_not_carried_cannot_be_pending(tmp_path, evidence):
    v1 = run_report_job(_job(tmp_path, evidence, _full()))
    for bad in (("title",), ("settings_unrecorded",)):
        with pytest.raises(TypeError):
            store.save_version(tmp_path, v1, pending=bad)
        with pytest.raises(TypeError):
            store.create_report(
                tmp_path,
                v1.model_copy(update={"report_id": "00000000000000ff"}),
                pending=bad,
            )
    assert store.load_meta(tmp_path, v1.report_id).latest_version == 1
    assert not (store.report_dir(tmp_path, v1.report_id) / "v2.json").exists()
    assert not store.report_dir(tmp_path, "00000000000000ff").exists()


# ── E3 (d): the strict check reads version n's document, never the meta ────


def test_a_strict_regenerate_of_an_unrecorded_version_checks_its_document(
    tmp_path,
    evidence,
    monkeypatch,
):
    from tests.assurance_acceptance.test_p4_reports import _dropping_disclosures

    v1 = _refused_v2(tmp_path, evidence, monkeypatch)
    v2 = store.load_report(tmp_path, v1.report_id, 2)
    required = [
        b
        for s in v2.sections
        for b in s.blocks
        if b.type == "callout" and b.kind == "disclosure"
    ]
    assert required  # version n's own disclosures are what the check needs
    _dropping_disclosures(monkeypatch)
    job = _job(
        tmp_path,
        evidence,
        _rewrite(),
        base_document=v2,
        regenerate_section="fmea_top",
        strict=True,
    )
    with pytest.raises(Exception, match="disclosure"):
        run_report_job(job)
    assert job.record["error_kind"] == "report_disclosure_missing"
    assert store.load_meta(tmp_path, v1.report_id).latest_version == 2


def test_the_mark_never_blocks_a_strict_regenerate(tmp_path, evidence, monkeypatch):
    v1 = _refused_v2(tmp_path, evidence, monkeypatch)
    v2 = store.load_report(tmp_path, v1.report_id, 2)
    job = _job(
        tmp_path,
        evidence,
        _rewrite(),
        base_document=v2,
        regenerate_section="fmea_top",
        strict=True,
    )
    run_report_job(job)
    assert job.record["status"] == "done" and job.record["version"] == 3
    meta = store.load_meta(tmp_path, v1.report_id)
    assert meta.settings_unrecorded is None and meta.generation is not None
