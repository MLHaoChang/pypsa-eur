"""Job holds across replicas and restarts (assistant-assurance issue 16, S2).

GK B (35) H1-H3, H9 and GK B (36)/(37) K1-K6 (issue 16, Comments): the
owner of a hold record renews its lease; another process (a second replica,
or this one after a restart, under a new replica id) reads the record as held
until the lease lapses, and then clears it only when the work died with its
owner (in-process kinds) or its durable `solve_jobs` row is terminal (queue
kinds). Every clear of another owner's record is a compare-and-delete.
"""

# ruff: noqa: F811 -- shared public harness fixtures
from __future__ import annotations

import concurrent.futures
import contextvars
import logging
import subprocess
import sys
import time
import uuid
from datetime import datetime, UTC

import pytest

from harness import budget, job_hold_store, job_holds
from harness.session import ChatSession
from tests.assurance_acceptance.test_p3_job_hold_records import (
    QUEUE,
    _Row,
    _Future,
    _NeverDone,
    _make_project,
)
from tests.test_workflow_tools import baseline  # noqa: F401

INPROC_TOOL = "generate_report"
REPLICA = {"id": "replica-a"}


@pytest.fixture
def replica(monkeypatch):
    """This process's replica id, which a test can change (another replica,
    or a restart)."""
    import security

    REPLICA["id"] = "replica-a"
    monkeypatch.setattr(security, "replica_id", lambda: REPLICA["id"])
    return REPLICA


@pytest.fixture
def project(monkeypatch, seeded_identity, replica):
    project_id = _make_project(seeded_identity)
    monkeypatch.setattr(job_holds, "_binding", lambda: (project_id, "alpha"))
    monkeypatch.setattr(job_holds, "ensure_renewer", lambda: None)
    yield project_id
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()


def _open(tool=QUEUE, future=None):
    return job_holds.open_on_timeout(
        ChatSession(), tool, "F", "tu", future or _NeverDone()
    )


def _restart(replica):
    """The owner process is gone: a new process with a new replica id and an
    empty registry reads its records."""
    replica["id"] = "replica-b"
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()


def _set_lease(record_id, lease_until):
    from db.models import AssistantJobHold
    from db.session import SessionLocal

    with SessionLocal() as db:
        db.get(AssistantJobHold, record_id).lease_until = lease_until
        db.commit()


def _lapse(record_id):
    _set_lease(record_id, time.time() - 1.0)


def _check(tool=QUEUE, fingerprint="F", args=None):
    return job_holds.check(ChatSession(), tool, args or {}, fingerprint)


def _solve_job(project_id, status, *, kind=None, enqueued_at=None):
    """A `solve_jobs` row of the project, as the queue writes it."""
    from db.models import Project, SolveJobRow
    from db.session import SessionLocal

    job_id = uuid.uuid4()
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        db.add(
            SolveJobRow(
                id=job_id,
                project_id=project.name,
                project_key=f"{project.org_id}:{project.id}",
                kind=kind,
                status=status,
                enqueued_at=datetime.fromtimestamp(
                    time.time() if enqueued_at is None else enqueued_at, tz=UTC
                ),
            )
        )
        db.commit()
    return str(job_id)


def _set_job_status(job_id, status):
    from db.models import SolveJobRow
    from db.session import SessionLocal

    with SessionLocal() as db:
        db.get(SolveJobRow, uuid.UUID(job_id)).status = status
        db.commit()


# ── The kind table (H1) ─────────────────────────────────────────────────────


def test_every_job_tool_has_exactly_one_worker_kind():
    from harness import catalogue

    job_tools = {t["name"] for t in catalogue.TOOLS if job_holds.is_job_tool(t["name"])}
    assert len(job_tools) == 28
    assert set(job_holds.JOB_KINDS) == job_tools
    assert set(job_holds.JOB_KINDS.values()) == {
        job_holds.INPROC,
        job_holds.INPROC_SOLVER,
        job_holds.QUEUE,
    }
    queue_tools = {t for t, k in job_holds.JOB_KINDS.items() if k == job_holds.QUEUE}
    assert set(job_holds.QUEUE_JOB_KINDS) == queue_tools


def test_queue_job_kinds_are_the_queues_own_kinds():
    from services import solve_queue

    for kinds in job_holds.QUEUE_JOB_KINDS.values():
        assert kinds and set(kinds) <= set(solve_queue.KINDS)


# ── The lease ────────────────────────────────────────────────────────────────


def test_the_record_is_written_with_its_owners_lease(project):
    before = time.time()
    hold = _open()
    (record,) = job_hold_store.active(project, QUEUE)
    assert record.id == hold.id and record.owner_replica_id == "replica-a"
    assert before + job_holds.lease_seconds() <= record.lease_until
    assert record.lease_until <= time.time() + job_holds.lease_seconds()
    assert job_holds.lease_seconds() == 90.0


def test_another_replicas_record_holds_while_its_lease_runs(project, replica):
    _open(INPROC_TOOL)
    replica["id"] = "replica-b"  # a second replica; the owner still renews
    assert _check(INPROC_TOOL)[0] == job_holds.PENDING
    assert _check(INPROC_TOOL, fingerprint="G")[0] == job_holds.CONFLICT
    assert len(job_hold_store.active(project, INPROC_TOOL)) == 1


@pytest.mark.parametrize(
    "tool",
    [t for t, k in job_holds.JOB_KINDS.items() if k != job_holds.QUEUE],
)
def test_a_lapsed_in_process_record_clears_once_its_owner_is_gone(
    project, replica, tool
):
    """H1, K1: in-process work, a solve included, died with its owner."""
    hold = _open(tool)
    _restart(replica)
    _lapse(hold.id)
    assert _check(tool) is None
    assert job_hold_store.active(project, tool) == []


def test_a_restart_reads_its_own_old_records_as_another_owners(project, replica):
    """H3: never cleared on boot alone, only after the lapse."""
    hold = _open(INPROC_TOOL)
    _restart(replica)
    job_holds.renew_tick()  # the new process neither renews nor clears it
    assert _check(INPROC_TOOL)[0] == job_holds.PENDING
    (record,) = job_hold_store.active(project, INPROC_TOOL)
    assert record.lease_until > time.time()
    _lapse(hold.id)
    assert _check(INPROC_TOOL) is None


def test_a_record_adopted_while_held_clears_once_it_lapses(project, replica):
    hold = _open(INPROC_TOOL)
    _restart(replica)
    later = ChatSession()
    assert job_holds.check(later, INPROC_TOOL, {}, "F")[0] == job_holds.PENDING
    _lapse(hold.id)
    assert job_holds.check(later, INPROC_TOOL, {}, "F") is None
    assert not job_hold_store.exists(hold.id)


def test_a_repeat_card_releases_another_owners_record(project, replica):
    hold = _open(INPROC_TOOL)
    replica["id"] = "replica-b"
    later = ChatSession()
    job_holds.check(later, INPROC_TOOL, {}, "F")
    card = job_holds.repeat_card(later, INPROC_TOOL, {}, "F")
    assert card["hold_id"] == hold.id
    assert job_holds.check(later, INPROC_TOOL, {}, "F", card=card) is None
    assert not job_hold_store.exists(hold.id)


def test_an_unknown_tools_lapsed_record_never_clears(project, replica, monkeypatch):
    hold = _open(INPROC_TOOL)
    monkeypatch.delitem(job_holds.JOB_KINDS, INPROC_TOOL)
    _restart(replica)
    _lapse(hold.id)
    assert _check(INPROC_TOOL)[0] == job_holds.PENDING


# ── Queue kinds clear on their durable row (H1, H9, K3) ─────────────────────


def _queue_hold_on_job(project, replica, status):
    job_id = _solve_job(project, status)
    future = _Future()
    hold = _open(QUEUE, future)
    future.finished = True
    future.result = lambda: {"id": job_id}
    assert job_holds.terminal(hold) is False  # the result moves it onto the job
    _restart(replica)
    _lapse(hold.id)
    return hold, job_id


def test_a_lapsed_queue_record_whose_job_still_runs_holds(project, replica):
    """H9: a lapse never clears a durable family."""
    hold, job_id = _queue_hold_on_job(project, replica, "running")
    (record,) = job_hold_store.active(project, QUEUE)
    assert record.job_id == job_id
    assert _check()[0] == job_holds.PENDING
    job_holds.renew_tick()  # nor does the sweep
    assert job_hold_store.exists(hold.id)


@pytest.mark.parametrize("status", ["completed", "failed", "aborted", "interrupted"])
def test_a_lapsed_queue_record_clears_on_its_terminal_job(project, replica, status):
    hold, job_id = _queue_hold_on_job(project, replica, "running")
    _set_job_status(job_id, status)
    assert _check() is None
    assert not job_hold_store.exists(hold.id)


def test_a_queue_record_whose_job_row_is_missing_holds(project, replica):
    hold = _open(QUEUE)
    job_hold_store.set_job(hold.id, str(uuid.uuid4()))
    _restart(replica)
    _lapse(hold.id)
    assert _check()[0] == job_holds.PENDING


def test_a_queue_record_clears_only_after_its_lapse(project, replica):
    hold, job_id = _queue_hold_on_job(project, replica, "running")
    _set_job_status(job_id, "completed")
    _set_lease(hold.id, time.time() + 60)  # its owner still renews
    assert _check()[0] == job_holds.PENDING


def _window_start(record):
    return record.opened_at - budget.PER_TOOL_TIMEOUT_SECONDS - job_holds.lease_seconds()


@pytest.mark.parametrize("offset,held", [(-1.0, False), (1.0, True)])
def test_a_queue_record_with_no_job_reads_live_jobs_in_its_window(
    project, replica, offset, held
):
    """K3: an enqueue from the call's start, a tool timeout before the hold,
    give or take one lease of clock skew, counts."""
    hold = _open(QUEUE)
    (record,) = job_hold_store.active(project, QUEUE)
    _solve_job(project, "queued", enqueued_at=_window_start(record) + offset)
    _restart(replica)
    _lapse(hold.id)
    assert (_check() is not None) is held


def test_an_enqueue_after_the_hold_was_written_holds(project, replica):
    """K3: the hold row is written before the late worker's enqueue; the
    enqueue still counts."""
    hold = _open(QUEUE)
    (record,) = job_hold_store.active(project, QUEUE)
    _solve_job(project, "running", enqueued_at=record.opened_at + 5.0)
    _restart(replica)
    _lapse(hold.id)
    assert _check()[0] == job_holds.PENDING


def test_a_queue_record_with_no_job_ignores_other_kinds_and_projects(
    project, replica, seeded_identity
):
    hold = _open(QUEUE)
    _solve_job(project, "running", kind="gridspine")
    _solve_job(project, "completed")
    _solve_job(_make_project(seeded_identity, name="beta"), "running")
    _restart(replica)
    _lapse(hold.id)
    assert _check() is None


def test_an_unreadable_job_store_holds_a_lapsed_queue_record(
    project, replica, monkeypatch
):
    hold = _open(QUEUE)
    _restart(replica)
    _lapse(hold.id)

    def broken(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(job_hold_store, "live_solve_jobs", broken)
    assert _check()[0] == job_holds.PENDING
    assert job_hold_store.exists(hold.id)


# ── An S1 replica's NULL lease (K2) ──────────────────────────────────────────


@pytest.mark.parametrize("offset,cleared", [(-1.0, False), (1.0, True)])
def test_a_null_lease_lapses_at_its_bound(project, replica, monkeypatch, offset, cleared):
    hold = _open(INPROC_TOOL)
    _restart(replica)
    _set_lease(hold.id, None)
    (record,) = job_hold_store.active(project, INPROC_TOOL)
    started = record.opened_at - 7200.0  # the row opened after this process started
    monkeypatch.setattr(job_holds, "_STARTED_AT", started)
    bound = record.opened_at + job_holds.NULL_LEASE_BOUND_SECONDS
    assert job_holds.lapsed(record, bound + offset) is cleared
    assert job_holds.clear_foreign(record, bound + offset) is cleared
    assert job_hold_store.exists(hold.id) is not cleared


def test_a_null_lease_from_before_this_process_started_runs_from_its_start(
    project, replica, monkeypatch
):
    """An S1 hold has no finite end, so a row opened long before the deploy
    is not lapsed at once: the bound runs from this process's start."""
    hold = _open(INPROC_TOOL)
    _restart(replica)
    _set_lease(hold.id, None)
    (record,) = job_hold_store.active(project, INPROC_TOOL)
    started = record.opened_at + 7200.0
    monkeypatch.setattr(job_holds, "_STARTED_AT", started)
    bound = started + job_holds.NULL_LEASE_BOUND_SECONDS
    assert not job_holds.lapsed(record, bound - 1.0)
    assert job_holds.lapsed(record, bound + 1.0)


@pytest.mark.parametrize("lease", [float("nan"), float("inf"), "soon"])
def test_a_malformed_lease_is_a_malformed_record(lease):
    assert job_hold_store._record(_Row(lease_until=2.0)).lease_until == 2.0
    with pytest.raises(job_hold_store.MalformedRecord):
        job_hold_store._record(_Row(lease_until=lease))


def test_a_malformed_lease_in_the_database_denies(project, replica, _auth_db):
    from sqlalchemy import text

    _open(INPROC_TOOL)
    _restart(replica)
    engine, _ = _auth_db
    with engine.begin() as conn:
        conn.execute(text("UPDATE assistant_job_holds SET lease_until = 'soon'"))
    error = _check(INPROC_TOOL)
    assert error[0] == job_holds.PENDING and "could not be checked" in error[1]


# ── Compare-and-delete (H2) ─────────────────────────────────────────────────


def test_a_renewal_racing_a_clear_leaves_the_record(project, replica):
    """H2, H9: the clear read the lapsed lease; the owner's renewal landed
    before its delete, so the delete misses."""
    hold = _open(INPROC_TOOL)
    _lapse(hold.id)
    (stale,) = job_hold_store.active(project, INPROC_TOOL)
    job_holds.renew_tick()  # the owner, replica-a, renews
    replica["id"] = "replica-b"
    assert job_holds.clear_foreign(stale, time.time()) is False
    assert job_hold_store.exists(hold.id)
    assert _check(INPROC_TOOL)[0] == job_holds.PENDING


def test_delete_if_matches_id_owner_and_lease(project):
    hold = _open(INPROC_TOOL)
    (record,) = job_hold_store.active(project, INPROC_TOOL)
    assert not job_hold_store.delete_if(hold.id, "replica-x", record.lease_until)
    assert not job_hold_store.delete_if(hold.id, "replica-a", record.lease_until + 1)
    assert not job_hold_store.delete_if(hold.id, "replica-a", None)
    assert job_hold_store.exists(hold.id)
    assert job_hold_store.delete_if(hold.id, "replica-a", record.lease_until)
    assert not job_hold_store.exists(hold.id)


# ── Renewal (H2, K4) ─────────────────────────────────────────────────────────


def test_the_renewer_extends_only_its_own_leases(project, replica):
    mine = _open(INPROC_TOOL)
    _set_lease(mine.id, time.time() + 5)
    replica["id"] = "replica-b"
    theirs = _open(QUEUE)
    _set_lease(theirs.id, time.time() + 5)
    replica["id"] = "replica-a"
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.pop(theirs.id)
    job_holds.renew_tick()
    assert job_hold_store.get(mine.id).lease_until > time.time() + 80
    assert job_hold_store.get(theirs.id).lease_until < time.time() + 10


def test_a_record_cleared_elsewhere_is_lost_and_never_rewritten(project, caplog):
    """K4: renewal is an UPDATE, never an upsert."""
    hold = _open(INPROC_TOOL)
    job_hold_store.delete(hold.id)
    with caplog.at_level(logging.WARNING, logger=job_holds.__name__):
        job_holds.renew_tick()
    assert "job hold lost" in caplog.text
    assert not job_hold_store.exists(hold.id)
    assert hold.id not in job_holds._REGISTRY
    job_holds.renew_tick()
    assert not job_hold_store.exists(hold.id)


def test_a_renewal_that_raises_renews_on_the_next_tick(project, monkeypatch):
    hold = _open(INPROC_TOOL)
    _set_lease(hold.id, time.time() + 5)
    real = job_hold_store.renew
    calls = []

    def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("db blip")
        return real(*args)

    monkeypatch.setattr(job_hold_store, "renew", flaky)
    job_holds.renew_tick()
    assert job_hold_store.get(hold.id).lease_until < time.time() + 10
    job_holds.renew_tick()
    assert job_hold_store.get(hold.id).lease_until > time.time() + 80


def test_a_sweep_that_raises_deletes_nothing_and_renewal_goes_on(
    project, replica, monkeypatch
):
    mine = _open(QUEUE)
    _set_lease(mine.id, time.time() + 5)
    replica["id"] = "replica-b"
    theirs = _open(INPROC_TOOL)
    _lapse(theirs.id)
    replica["id"] = "replica-a"
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.pop(theirs.id)

    def broken(_me):
        raise RuntimeError("db down")

    real = job_hold_store.foreign
    monkeypatch.setattr(job_hold_store, "foreign", broken)
    job_holds.renew_tick()
    assert job_hold_store.exists(theirs.id)
    assert job_hold_store.get(mine.id).lease_until > time.time() + 80
    monkeypatch.setattr(job_hold_store, "foreign", real)
    _set_lease(mine.id, time.time() + 5)
    job_holds.renew_tick()
    assert job_hold_store.get(mine.id).lease_until > time.time() + 80
    assert not job_hold_store.exists(theirs.id)  # swept by the same rules


def test_the_sweep_keeps_live_and_durable_records(project, replica):
    replica["id"] = "replica-b"
    live = _open(INPROC_TOOL)
    queued = _open(QUEUE)
    _solve_job(project, "running")
    _lapse(queued.id)
    replica["id"] = "replica-a"
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()
    job_holds.renew_tick()
    assert job_hold_store.exists(live.id) and job_hold_store.exists(queued.id)


def test_the_renewer_loop_survives_a_tick_that_raises(monkeypatch):
    ticks = []

    def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("boom")
        if len(ticks) >= 3:
            raise SystemExit  # end the loop under test

    monkeypatch.setattr(job_holds, "RENEW_SECONDS", 0.0)
    monkeypatch.setattr(job_holds, "renew_tick", tick)
    with pytest.raises(SystemExit):
        job_holds._renew_loop()
    assert len(ticks) == 3
    # renew_tick itself never raises: each step is caught.
    monkeypatch.undo()
    monkeypatch.setattr(job_holds, "_renew", lambda: (_ for _ in ()).throw(KeyError()))
    monkeypatch.setattr(job_holds, "_sweep", lambda: (_ for _ in ()).throw(KeyError()))
    job_holds.renew_tick()


# ── The renewer thread (K5) ─────────────────────────────────────────────────


@pytest.fixture
def no_renewer(monkeypatch):
    monkeypatch.setattr(job_holds, "_RENEWER", None)
    monkeypatch.setattr(job_holds, "_renew_loop", lambda: time.sleep(0.5))


def test_the_renewer_is_one_per_process(no_renewer):
    first = job_holds.ensure_renewer()
    assert job_holds.ensure_renewer() is first
    assert job_holds._RENEWER[0] == __import__("os").getpid()


def test_a_forked_process_starts_its_own_renewer(no_renewer, monkeypatch):
    first = job_holds.ensure_renewer()
    monkeypatch.setattr(job_holds.os, "getpid", lambda: -1)
    second = job_holds.ensure_renewer()
    assert second is not first and job_holds._RENEWER == (-1, second)


def test_the_first_hold_write_starts_the_renewer(
    no_renewer, seeded_identity, monkeypatch
):
    project_id = _make_project(seeded_identity, name="gamma")
    monkeypatch.setattr(job_holds, "_binding", lambda: (project_id, "gamma"))
    try:
        assert job_holds._RENEWER is None
        _open(INPROC_TOOL)
        assert job_holds._RENEWER is not None
    finally:
        with job_holds._REGISTRY_LOCK:
            job_holds._REGISTRY.clear()


def test_importing_the_module_starts_no_renewer():
    code = (
        "import threading, harness.job_holds as j; "
        "print(j._RENEWER, any(t.name == 'job-hold-renewer' for t in threading.enumerate()))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["None", "False"]


# ── One real tool per kind (K6) ─────────────────────────────────────────────


def _jobs_of(project_id):
    from db.models import Project, SolveJobRow
    from db.session import SessionLocal

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        key = f"{project.org_id}:{project.id}"
        return {str(r.id): r.status for r in db.query(SolveJobRow).filter_by(project_key=key)}


def _run_real(tool, call):
    """The real handler on a worker, as the shared pool runs it; its hold
    opens as on a timeout."""
    pool = concurrent.futures.ThreadPoolExecutor(1)
    try:
        future = pool.submit(contextvars.copy_context().run, call)
        hold = job_holds.open_on_timeout(ChatSession(), tool, "F", "tu", future)
        future.result(timeout=120)
        return hold
    finally:
        pool.shutdown(wait=True)


@pytest.fixture
def real(baseline, replica, monkeypatch):
    monkeypatch.setattr(job_holds, "ensure_renewer", lambda: None)
    yield baseline
    with job_holds._REGISTRY_LOCK:
        job_holds._REGISTRY.clear()


def test_a_real_in_process_tool_leaves_no_durable_job(real, replica):
    from services import chat_tools

    _, project_id, _ = real
    before = _jobs_of(project_id)
    hold = _run_real(
        "generate_load_profile",
        lambda: chat_tools.DISPATCHERS["generate_load_profile"](
            project_id=project_id,
            archetype="industrial",
            loads=["L1"],
            year=2026,
            params={"shifts": 2, "peak_mw": 8.0, "timezone": "Europe/Berlin"},
        ),
    )
    assert _jobs_of(project_id) == before
    _restart(replica)
    _lapse(hold.id)
    assert _check("generate_load_profile") is None


def test_a_real_solve_runs_on_a_thread_of_its_process(real, replica):
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    ctx, project_id, _ = real
    before = _jobs_of(project_id)
    assert chat_tools.run_simulation()["status"] == "started"
    thread = job_holds.solve_thread(PyPSAService.get_active_context())
    hold = job_holds._open(
        ChatSession(), "run_simulation", "F", "tu", "uncertain_raise", worker=thread
    )
    thread.join(120)
    assert _jobs_of(project_id) == before
    _restart(replica)
    _lapse(hold.id)
    assert _check("run_simulation") is None


def test_a_real_queue_tool_holds_on_its_solve_jobs_row(real, replica):
    from services import chat_tools

    _, project_id, _ = real
    hold = _run_real(QUEUE, lambda: chat_tools.solve_queue_enqueue(project_id))
    job_holds.terminal(hold)  # the late result moves the hold onto its job
    (record,) = job_hold_store.active(project_id, QUEUE)
    assert record.job_id and record.job_id in _jobs_of(project_id)
    from services.workflow_tools import wait_for_job

    for _ in range(5):  # the queue is done writing the row
        if wait_for_job(record.job_id, 25).get("completed"):
            break
    _restart(replica)
    _lapse(hold.id)
    _set_job_status(record.job_id, "running")  # as if a replica still ran it
    assert _check()[0] == job_holds.PENDING
    _set_job_status(record.job_id, "interrupted")  # reconcile_on_boot's mark
    assert _check() is None
