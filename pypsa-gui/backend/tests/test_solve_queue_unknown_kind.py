"""
SC-00 N, decision D04: no unknown job kind may fall through to an electrical solve.

Before this guard, `SolveQueue._run_job` sent every kind that was not
gridspine/campus/profile_gen to `_run_solve_job`, `enqueue`/`enqueue_unique`
accepted any string, and `reconcile_on_boot` re-admitted a persisted `queued`
row whatever its kind. A future (or corrupt) kind would therefore have run as a
solve. Now each entry point refuses it with `unknown_job_kind`; a row with no
kind keeps its historical meaning, `solve`.

See .scratch/site-scene-generation/sc-00-n/decisions.md (D04).
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

from db.models import SolveJobRow
from services import solve_job_store
from services import solve_queue as sq
from services.solve_queue import KIND_SOLVE, SolveJob, solve_queue


def _row(job_id: uuid.UUID) -> SolveJobRow:
    from db.session import SessionLocal  # bound late: see test_solve_queue_boot_reconcile._status

    with SessionLocal() as db:
        return db.scalar(select(SolveJobRow).where(SolveJobRow.id == job_id))


def _seed_queued(kind: str | None, project_id: str) -> uuid.UUID:
    job = SolveJob(id=uuid.uuid4(), project_id=project_id, enqueued_at=time.time())
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=None)
    from db.session import SessionLocal

    with SessionLocal() as db:
        orm = db.get(SolveJobRow, job.id)
        orm.kind = kind
        db.commit()
    return job.id


@pytest.fixture
def clean_queue():
    solve_queue.reset_for_tests()
    yield
    solve_queue.reset_for_tests()


def test_enqueue_refuses_an_unknown_kind(clean_queue):
    with pytest.raises(ValueError, match="unknown_job_kind"):
        solve_queue.enqueue("P", kind="scene_native")
    assert solve_queue.list_jobs() == []


def test_enqueue_unique_refuses_an_unknown_kind(clean_queue):
    with pytest.raises(ValueError, match="unknown_job_kind"):
        solve_queue.enqueue_unique("P", kind="scene_native")
    assert solve_queue.list_jobs() == []


def test_run_job_never_solves_an_unknown_kind(clean_queue, monkeypatch):
    solved: list[uuid.UUID] = []
    monkeypatch.setattr(sq.SolveQueue, "_run_solve_job", lambda self, job: solved.append(job.id))
    job = SolveJob(id=uuid.uuid4(), project_id="P", kind="scene_native", enqueued_at=time.time())

    solve_queue._run_job(job)

    assert solved == []
    assert job.status == "failed"
    assert job.error and job.error.startswith(sq.UNKNOWN_KIND_CONDITION)


def test_run_job_still_solves_kind_solve(clean_queue, monkeypatch):
    solved: list[uuid.UUID] = []
    monkeypatch.setattr(sq.SolveQueue, "_run_solve_job", lambda self, job: solved.append(job.id))
    job = SolveJob(id=uuid.uuid4(), project_id="P", kind=KIND_SOLVE, enqueued_at=time.time())

    solve_queue._run_job(job)

    assert solved == [job.id]


def test_restore_refuses_an_unknown_kind(clean_queue):
    row = {"id": uuid.uuid4(), "project_id": "P", "kind": "scene_native", "enqueued_at": time.time()}
    with pytest.raises(ValueError, match="unknown_job_kind"):
        solve_queue.restore(row)
    assert solve_queue.get_job(row["id"]) is None


def test_boot_fails_an_unknown_kind_row_in_the_persisted_store(clean_queue):
    unknown = _seed_queued("scene_native", "FromTheFuture")

    interrupted, resumed = solve_job_store.reconcile_on_boot()

    assert (interrupted, resumed) == (0, 0)
    assert solve_queue.get_job(unknown) is None
    row = _row(unknown)
    assert row.status == "failed"
    assert row.condition == sq.UNKNOWN_KIND_CONDITION
    assert row.finished_at is not None


def test_boot_still_resumes_a_row_without_a_kind_as_a_solve(clean_queue):
    legacy = _seed_queued(None, "BeforeKinds")

    _interrupted, resumed = solve_job_store.reconcile_on_boot()

    assert resumed == 1
    assert solve_queue.get_job(legacy)["kind"] == KIND_SOLVE
