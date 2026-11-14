"""LC-48b, LC-D21.report_resume: a report job a server restart interrupted reads interrupted.

Owner decision LC48B-RESUME-1: the job record is persisted; after a restart its status
reads ``interrupted`` and fetching it is 409 ``report_interrupted`` with a "generate
again" hint; nothing re-runs by itself.

The persisted record is ``reports/.job.json`` in the project: written when a job starts,
removed when it ends (done, aborted or failed). A restart is what a fresh process sees:
no job record in memory, and the marker a killed worker left on disk. The test takes the
marker as it stood while the job ran, lets the job finish, then puts the marker back and
empties the in-process record and slot, as a restart does.

The seam is the copilot's report tools over one stored study report and a scripted
model, as in ``test_lc48b_d21_report_stale.py``.

Hand oracle:
- while a job runs, the marker reads running with the job's report id; once it is done,
  aborted or (after a refused write) failed, no marker is left;
- after the restart, ``get_report_status`` is 409 report_interrupted with status
  ``interrupted``, that report id and the hint "generate the report again"; the model is
  not called again and ``lc43_gates.state`` is unchanged (nothing re-runs);
- generating again replaces the marker; once that job is done the status is the new
  job's record and no marker is left.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import json
import threading

import pytest
from fastapi import HTTPException

from services import chat_tools as T
from services.llm_fake import FakeProvider
from services.reports import report_job, store
from tests.lc43_gates import refused, state
from tests.test_chat_report_tools import (  # noqa: F401
    _Holding,
    _install_provider,
    _poll,
    _script,
    _store_eh_report,
    project,
)
from tests.test_lc06_d21_reports import SECTIONS
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _marker(project_dir):
    return project_dir / "reports" / store.JOB_MARKER


def _restart(monkeypatch) -> None:
    """What a fresh process holds: no job record and a free slot."""
    from routers.simulation import _state

    _state["report_job"] = None
    monkeypatch.setattr(report_job, "_ACTIVE", None)
    monkeypatch.setattr(report_job, "_ACTIVE_DIR", None)


def _held_job(monkeypatch, project_dir) -> tuple[str, str]:
    """Run one job to done; return its report id and the marker as it read mid-run."""
    _store_eh_report()
    hold = threading.Event()
    _install_provider(monkeypatch, _Holding(_script(), hold))
    try:
        report_id = T.generate_report(sections=SECTIONS)["report_id"]
        running = _marker(project_dir).read_text(encoding="utf-8")
    finally:
        hold.set()
    assert _poll()["status"] == "done"
    _worker_ended()
    return report_id, running


def _worker_ended() -> None:
    """The finished job's thread has returned: a restart leaves no worker behind."""
    from routers.simulation import _state

    _state["report_job"]["thread"].join(timeout=10)
    assert not _state["report_job"]["thread"].is_alive()


def test_an_interrupted_job_reads_interrupted_and_nothing_reruns(project, monkeypatch):
    report_id, running = _held_job(monkeypatch, project)
    marker = json.loads(running)
    assert marker["status"] == "running" and marker["report_id"] == report_id, marker
    assert not _marker(project).exists()

    _marker(project).write_text(running, encoding="utf-8")
    _restart(monkeypatch)
    model = _install_provider(monkeypatch, FakeProvider(_script()))
    before = state(project)

    refused(T.get_report_status, 409, "report_interrupted")
    with pytest.raises(HTTPException) as again:
        T.get_report_status()
    detail = again.value.detail
    assert detail["status"] == "interrupted" and detail["report_id"] == report_id, (
        detail
    )
    assert detail["hint"] == "generate the report again", detail
    assert model.requests == []
    assert state(project) == before
    assert _marker(project).exists()

    started = T.generate_report(sections=SECTIONS)
    assert started["report_id"] != report_id
    done = _poll()
    assert done["status"] == "done" and done["report_id"] == started["report_id"], done
    assert not _marker(project).exists()
    assert len(model.requests) == len(SECTIONS)


def test_an_aborted_or_failed_job_leaves_no_marker(project, monkeypatch):
    _store_eh_report()
    hold = threading.Event()
    _install_provider(monkeypatch, _Holding(_script(), hold))
    try:
        T.generate_report(sections=SECTIONS)
        assert _marker(project).exists()
        T.abort_report_generation()
    finally:
        hold.set()
    assert _poll()["status"] == "aborted"
    assert not _marker(project).exists()

    def refuse(*_a, **_k):
        raise store.ReportBusy("held")

    monkeypatch.setattr(store, "create_report", refuse)
    _install_provider(monkeypatch, FakeProvider(_script()))
    T.generate_report(sections=SECTIONS)
    assert _poll()["status"] == "failed"
    assert not _marker(project).exists()


def test_the_marker_stays_local_to_the_project():
    """Never data: copies, exports and imports skip it as they skip lock files."""
    assert store.is_lock_artifact(store.JOB_MARKER)
