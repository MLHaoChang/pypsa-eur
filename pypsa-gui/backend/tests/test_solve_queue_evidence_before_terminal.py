"""A job's run evidence is published BEFORE its status turns terminal.

`wait_for_job` returns the moment `job.status` is terminal. Both terminal
paths of the queue (the `_run_job` finally block and `_finish`, which the
gridspine and campus runners share) used to flip the status first and write
the evidence after, so a caller that refreshed right away could read
`experiment_evidence_unavailable` (CI flake on #648).

The hook below records what any reader could see at the instant the
evidence is written: the status must not be terminal yet, and the evidence
must carry the final status the job is about to take.
"""
from __future__ import annotations

import uuid

import pytest

from services import run_evidence
from services import solve_queue as sq


@pytest.fixture
def publishes(monkeypatch):
    seen: list[dict] = []

    def hook(job, status=None):
        with sq.solve_queue._lock:
            seen.append({"job_status": job.status, "status": status})
        return None

    monkeypatch.setattr(run_evidence, "publish_queue_run", hook)
    sq.solve_queue.reset_for_tests()
    yield seen
    sq.solve_queue.reset_for_tests()


def _job(**kw) -> sq.SolveJob:
    return sq.SolveJob(id=uuid.uuid4(), project_id="Evidence Order", **kw)


def _assert_published_before_flip(seen, job, final):
    assert job.status == final
    assert len(seen) == 1, seen
    assert seen[0]["job_status"] not in sq._TERMINAL, (
        "the evidence was written after the status turned terminal; a "
        "wait_for_job caller can read missing evidence"
    )
    assert seen[0]["status"] == final


def test_run_job_publishes_evidence_before_the_status_flip(publishes, monkeypatch):
    import routers.projects as projects

    def boom(*_a, **_kw):
        raise RuntimeError("hydrate failed")

    monkeypatch.setattr(projects, "_hydrate_context_from_disk", boom)
    job = _job()
    sq.solve_queue._run_job(job)
    _assert_published_before_flip(publishes, job, "failed")


def test_finish_publishes_evidence_before_the_status_flip(publishes):
    # A gridspine job with no storage dir fails through `_finish`.
    job = _job(kind=sq.KIND_GRIDSPINE)
    sq.solve_queue._run_gridspine_job(job)
    _assert_published_before_flip(publishes, job, "failed")


@pytest.fixture
def failing_publish(monkeypatch):
    from services import solve_job_store

    recorded: list[str] = []

    def hook(job, status=None):
        raise RuntimeError("evidence store down")

    monkeypatch.setattr(run_evidence, "publish_queue_run", hook)
    monkeypatch.setattr(solve_job_store, "record_status",
                        lambda job: recorded.append(job.status) or True)
    sq.solve_queue.reset_for_tests()
    yield recorded
    sq.solve_queue.reset_for_tests()


def _drain(log_queue) -> list:
    items = []
    while not log_queue.empty():
        items.append(log_queue.get_nowait())
    return items


def test_run_job_still_finishes_when_publishing_raises(failing_publish, monkeypatch):
    import routers.projects as projects

    def boom(*_a, **_kw):
        raise RuntimeError("hydrate failed")

    monkeypatch.setattr(projects, "_hydrate_context_from_disk", boom)
    job = _job()
    sq.solve_queue._run_job(job)
    assert job.status == "failed" and job.finished_at is not None
    assert failing_publish[-1] == "failed"
    assert _drain(job.log_queue)[-1] is None


def test_finish_still_finishes_when_publishing_raises(failing_publish):
    job = _job(kind=sq.KIND_GRIDSPINE)
    sq.solve_queue._run_gridspine_job(job)
    assert job.status == "failed" and job.finished_at is not None
    assert failing_publish[-1] == "failed"
    assert None in _drain(job.log_queue)
