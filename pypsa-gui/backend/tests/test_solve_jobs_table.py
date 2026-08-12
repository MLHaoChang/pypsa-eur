"""
R22 — every job is persisted, with who queued it and what it was queued with.

The queue was purely in-process: `itertools.count(1)` ids, a dict, and nothing
on disk. A restart lost every queued job silently, and a shared instance could
not say who queued a solve. Increment 3's boot reconciliation, requeue, dismiss
and config snapshot all read this table.
"""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import select

from db.models import SolveJobRow
from services import solve_job_store
from services.solve_queue import SolveJob
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _row(job_id):
    # Imported HERE, not at module top: `db.session.SessionLocal` is
    # monkeypatched onto the file-backed test database by the `_auth_db`
    # fixture, which only runs once a test requests it — AFTER pytest has
    # already collected (imported) this module. A top-level `from db.session
    # import SessionLocal` would bind to the pristine, un-migrated `:memory:`
    # sessionmaker captured at collection time and every query here would
    # raise `no such table: solve_jobs` regardless of what `solve_job_store`
    # correctly wrote. `solve_job_store.py` uses the same mid-function import
    # for the same reason.
    from db.session import SessionLocal

    with SessionLocal() as db:
        return db.scalar(select(SolveJobRow).where(SolveJobRow.id == _as_uuid(job_id)))


def _as_uuid(job_id):
    return job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))


def test_the_table_carries_a_uuid_pk_a_user_and_a_config():
    cols = SolveJobRow.__table__.columns
    assert cols["id"].primary_key
    assert "enqueued_by_user_id" in cols
    assert "solver_config" in cols
    assert SolveJobRow.__tablename__ == "solve_jobs"


def test_record_enqueued_writes_the_row_with_the_acting_user_and_config(seeded_identity):
    job = SolveJob(
        id=uuid.uuid4(), project_id="Persisted", project_key="org:proj",
        storage_dir="/tmp/persisted", enqueued_at=time.time(),
    )
    # A REAL seeded user, not a fabricated `uuid.uuid4()`: `enqueued_by_user_id`
    # is a genuine foreign key to `users.id` (ON DELETE SET NULL), and SQLite
    # FK enforcement is on for every connection (`configure_sqlite`). An actor
    # id with no backing row would 23503/IntegrityError the insert — which
    # `record_enqueued` correctly treats as an operational failure and
    # swallows, so the row would silently never be written and this assertion
    # would fail for a reason that has nothing to do with the code under test.
    actor = seeded_identity["user_id"]
    solve_job_store.record_enqueued(
        job, enqueued_by_user_id=actor, solver_config_json=json.dumps({"solver_name": "highs"}),
    )
    row = _row(job.id)
    assert row is not None, "no solve_jobs row was written"
    assert row.project_id == "Persisted"
    assert row.project_key == "org:proj"
    assert row.status == "queued"
    assert row.enqueued_by_user_id == actor
    assert json.loads(row.solver_config)["solver_name"] == "highs"


def test_record_enqueued_refuses_a_non_uuid_id_loudly():
    """
    The swallow-everything version turned a type error into a silent no-write:
    `Uuid(as_uuid=True)`'s bind processor calls `value.hex`, the AttributeError
    was caught, and every row went unwritten behind one log line. A programming
    error must reach the caller.
    """
    import pytest

    bogus = SolveJob(id=7, project_id="Wrong", enqueued_at=time.time())
    with pytest.raises(TypeError, match="UUID"):
        solve_job_store.record_enqueued(
            bogus, enqueued_by_user_id=None, solver_config_json=None,
        )


def test_record_status_mirrors_the_terminal_record():
    job = SolveJob(id=uuid.uuid4(), project_id="Finished", enqueued_at=time.time())
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=None)
    job.status = "completed"
    job.objective = 1234.5
    job.solve_time = 2.0
    job.condition = "optimal"
    job.finished_at = time.time()
    solve_job_store.record_status(job)
    row = _row(job.id)
    assert row.status == "completed"
    assert row.objective == 1234.5
    assert row.condition == "optimal"
    assert row.finished_at is not None


def test_load_by_status_returns_only_the_asked_for_statuses():
    queued = SolveJob(id=uuid.uuid4(), project_id="Q", enqueued_at=time.time())
    done = SolveJob(id=uuid.uuid4(), project_id="D", enqueued_at=time.time())
    for j in (queued, done):
        solve_job_store.record_enqueued(j, enqueued_by_user_id=None, solver_config_json=None)
    done.status = "completed"
    solve_job_store.record_status(done)

    ids = {r["id"] for r in solve_job_store.load_by_status(("queued",))}
    assert queued.id in ids
    assert done.id not in ids


def test_enqueuing_through_the_route_persists_the_row(
    client, install_network, tmp_projects_dir,
):
    install_network(build_network(), name="Durable")
    _save_project(client, "Durable")
    job = client.post("/api/simulation/queue", json={"project_id": "Durable"}).json()
    row = _row(job["id"])
    assert row is not None, "the enqueue route did not persist the job"
    assert row.project_id == "Durable"
    assert row.enqueued_by_user_id is not None, "the acting user was not stamped"
