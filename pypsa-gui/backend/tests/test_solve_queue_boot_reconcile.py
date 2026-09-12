"""
R25/R26 — what a restart does to the queue.

Every job left `running` becomes `interrupted`: the process died under it and
nobody stopped it, which is a different fact from `aborted` and the user needs
to be able to tell. It is NEVER re-enqueued automatically — that is what stops a
job that crashed the process from crash-looping the boot. Every job left
`queued` is re-enqueued and the dispatcher starts, which is the whole promise of
walking away.

The reconciliation runs in `lifespan` and cannot fail the boot, following
`_chatbot_startup_check`: a soft probe that logs and raises nothing.
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import select

from db.models import SolveJobRow
from services import solve_job_store
from services.solve_queue import SolveJob, solve_queue


def _seed(status: str, project_id: str = "Rebooted") -> uuid.UUID:
    job = SolveJob(id=uuid.uuid4(), project_id=project_id, enqueued_at=time.time())
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=None)
    job.status = status
    if status == "running":
        job.started_at = time.time()
    solve_job_store.record_status(job)
    return job.id


def _status(job_id: uuid.UUID) -> str:
    # Imported HERE, not at module top: `db.session.SessionLocal` is
    # monkeypatched onto the file-backed test database by the `_auth_db`
    # fixture, which only runs once a test requests it — AFTER pytest has
    # already collected (imported) this module. A top-level `from db.session
    # import SessionLocal` binds to the pristine, un-migrated sessionmaker
    # captured at collection time and this query would raise `no such table:
    # solve_jobs` regardless of what reconciliation correctly wrote. Same
    # trap `test_solve_jobs_table.py::_row` documents and avoids.
    from db.session import SessionLocal

    with SessionLocal() as db:
        return db.scalar(select(SolveJobRow.status).where(SolveJobRow.id == job_id))


def test_interrupted_is_terminal():
    from services.solve_queue import _TERMINAL

    assert "interrupted" in _TERMINAL
    assert set(_TERMINAL) == {"completed", "failed", "aborted", "interrupted"}


def test_a_running_job_becomes_interrupted_and_is_not_restarted():
    solve_queue.reset_for_tests()
    was_running = _seed("running")
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()

        assert interrupted == 1, (interrupted, resumed)
        assert _status(was_running) == "interrupted"
        # NEVER automatically re-enqueued: a job that crashed the process would
        # otherwise crash-loop the boot.
        assert solve_queue.get_job(was_running) is None
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_job_is_re_enqueued_under_its_own_id():
    solve_queue.reset_for_tests()
    was_queued = _seed("queued", project_id="StillWaiting")
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()

        assert resumed == 1, (interrupted, resumed)
        restored = solve_queue.get_job(was_queued)
        assert restored is not None, "the queued job was not restored"
        assert restored["project_id"] == "StillWaiting"
        # Its id survives, so a client holding the id can still abort it.
        assert restored["id"] == str(was_queued)
    finally:
        solve_queue.reset_for_tests()


def test_a_terminal_job_is_left_alone():
    solve_queue.reset_for_tests()
    done = _seed("completed")
    try:
        solve_job_store.reconcile_on_boot()
        assert _status(done) == "completed"
        assert solve_queue.get_job(done) is None
    finally:
        solve_queue.reset_for_tests()


def test_reconciliation_never_raises_when_the_table_is_unreadable(monkeypatch):
    """R26 — it cannot fail the boot, following `_chatbot_startup_check`."""
    def boom(*_a, **_k):
        raise RuntimeError("no such table: solve_jobs")

    monkeypatch.setattr(solve_job_store, "load_by_status", boom)
    assert solve_job_store.reconcile_on_boot() == (0, 0)


def test_a_cancelled_queued_job_is_not_resurrected():
    """
    Task 13 closed a gap where `abort()` of a still-QUEUED job never reached
    the table, leaving the row `status="queued"` forever — which THIS
    reconciliation would then resurrect on every restart. Exercise the real
    `abort()` path end-to-end (not `_seed`, which writes the status directly
    and would not catch a regression in `abort()`'s own persistence), then
    simulate a restart (the in-memory queue is wiped, only the table
    survives) and confirm reconciliation does not bring the job back.

    Seeds directly into `solve_queue._jobs`/`_order` rather than calling
    `enqueue()`, so the dispatcher thread never sees the job and there is no
    race between the dispatcher claiming it (queued -> running) and this
    test's `abort()` call.
    """
    solve_queue.reset_for_tests()
    jid = uuid.uuid4()
    job = SolveJob(id=jid, project_id="CancelledBeforeRestart", enqueued_at=time.time())
    solve_queue._jobs[jid] = job
    solve_queue._order.append(jid)
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=None)

    result = solve_queue.abort(jid)
    assert result["status"] == "aborted"
    assert _status(jid) == "aborted", "abort() of a queued job did not reach the table"

    # Simulate the process restarting: the in-memory queue is gone, only the
    # persisted row survives.
    solve_queue.reset_for_tests()
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()

        assert solve_queue.get_job(jid) is None, (
            "a job the user cancelled while queued was resurrected on reconciliation"
        )
        assert _status(jid) == "aborted", "reconciliation must not touch an aborted row"
    finally:
        solve_queue.reset_for_tests()


class _NullQueue:
    """A `queue.Queue` stand-in that swallows work instead of dispatching it.
    Same rationale as `test_solve_queue_persisted_listing._parked_dispatcher`:
    keeps the restored job deterministically `queued` instead of racing the
    real dispatcher (which would fail-fast a job for a project that does not
    exist and flip its row terminal mid-test)."""

    def put(self, item) -> None:
        pass

    def get_nowait(self):
        import queue as _q

        raise _q.Empty

    def task_done(self) -> None:
        pass


def test_reconciliation_running_twice_does_not_duplicate_a_job(monkeypatch):
    """
    `restore()` appended unconditionally, so any re-entry of the lifespan (a
    second TestClient context in one interpreter, a re-run of the startup
    hook) put the same id in `_order`/`_q` twice: the listing showed one job
    as two rows, and the dispatcher would run the same solve twice back to
    back. Restoring an id that is already resident must be a no-op.
    """
    monkeypatch.setattr(solve_queue, "_ensure_dispatcher_locked", lambda: None)
    monkeypatch.setattr(solve_queue, "_q", _NullQueue())
    solve_queue.reset_for_tests()
    was_queued = _seed("queued", project_id="DoubleBoot")
    try:
        first = solve_job_store.reconcile_on_boot()
        second = solve_job_store.reconcile_on_boot()
        assert first == (0, 1), first

        ids = [j["id"] for j in solve_queue.list_jobs()]
        assert ids.count(str(was_queued)) == 1, (
            f"a second reconciliation duplicated the job in the queue: {ids} "
            f"(second run reported {second})"
        )
    finally:
        solve_queue.reset_for_tests()


def test_two_queued_rows_for_the_same_project_do_not_both_restore(monkeypatch):
    """
    Final whole-branch review, Important 1 — REPRODUCED with a throwaway probe.

    `restore()` had no per-project dedupe, unlike `enqueue_unique`'s
    `_active_job_locked` check. Reachable path: a best-effort `record_status`
    mirror fails (see the `abort()` / `cancel_if_queued()` WARNING added
    alongside this fix), so a job memory has already marked terminal still
    reads `queued` in the table; `enqueue_unique` then sees nothing active IN
    MEMORY and legitimately inserts a SECOND `queued` row for the same
    project. A restart must not restore BOTH into active jobs — at
    `MAX_CONCURRENT_SOLVES=1` that is a wasted duplicate solve, and above it
    the two jobs would `hydrate_or_adopt` the SAME resident context and race
    `_save_context` on one `mutation_lock` (R35).

    Uses the same neutered-dispatcher harness as
    `test_reconciliation_running_twice_does_not_duplicate_a_job` so both rows
    restore deterministically instead of racing a real dispatcher thread.
    """
    monkeypatch.setattr(solve_queue, "_ensure_dispatcher_locked", lambda: None)
    monkeypatch.setattr(solve_queue, "_q", _NullQueue())
    solve_queue.reset_for_tests()
    first = _seed("queued", project_id="DupeProject")
    second = _seed("queued", project_id="DupeProject")
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()

        assert interrupted == 0, (interrupted, resumed)
        assert resumed == 1, (
            f"both queued rows for the same project were counted as resumed: "
            f"{(interrupted, resumed)} — the boot log would lie about how "
            f"many jobs it put back to work"
        )

        active = [
            j for j in solve_queue.list_jobs()
            if j["project_id"] == "DupeProject" and j["status"] in ("queued", "running")
        ]
        assert len(active) == 1, (
            f"two active jobs exist for one project after restore: {active} — "
            f"this is the exact invariant enqueue_unique exists to enforce"
        )

        # The row that lost the race is untouched — still `queued` in the
        # table, not silently dropped, not resurrected into a second active
        # job. It will be reconsidered next boot once the winner goes terminal.
        statuses = {"first": _status(first), "second": _status(second)}
        assert list(statuses.values()).count("queued") == 2, statuses
    finally:
        solve_queue.reset_for_tests()
