"""
The solve queue must release everything it claims, on every exit path.

SL-5. `_dispatch_loop` catches BaseException around each job and keeps going —
deliberately, so one bad job cannot kill the dispatcher. But `_run_solve_job`
caught only `Exception`, and its `finally` updated the job row without releasing
the CONTEXT. A BaseException therefore left `ctx.solver_state` at
`status="running"` with `thread` pointed at the dispatcher, which never exits, so
`_solver_in_flight_ctx` read True forever and every save, activate and load of
that project 409'd until a restart. Reachable through `SystemExit` in
admin-enabled user code, or the abort watcher's injected interrupt landing after
`disarm()`.

SL-7. `_claim`'s docstring said it was "extracted from `_run_solve_job` so both
runners claim identically". `_run_solve_job` never called it, and the quit-time
drain check landed only in its inline claim — so a gridspine job popped after
`stop_dispatching()` claimed and started under a process that was exiting.
Separately, the gridspine runner's "no storage directory" early return ran AFTER
`_claim` had published the log queue but BEFORE the try/finally that closes it,
so anything streaming the job's log waited for an end-of-stream that never came.
"""
from __future__ import annotations

import inspect
import time
import uuid

import pytest

from services import solve_queue as SQ
from services import solver_service as SS
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService
from tests.conftest import build_network


class _Escapes(BaseException):
    """A BaseException that is not KeyboardInterrupt — that one would stop
    pytest itself rather than the job under test."""


@pytest.fixture(autouse=True)
def _clean_queue():
    SQ.solve_queue.reset_for_tests()
    yield
    SQ.solve_queue.reset_for_tests()


def _wait_terminal(job_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = SQ.solve_queue.get_job(job_id)
        if j and j["status"] in ("completed", "failed", "aborted"):
            return j
        time.sleep(0.05)
    return SQ.solve_queue.get_job(job_id)


# ── SL-5 ───────────────────────────────────────────────────────────────────


def test_a_base_exception_does_not_leave_the_context_owned(monkeypatch):
    ctx = ProjectContext(network=build_network(), loaded_project="esc")
    PyPSAService.register("esc-key", ctx)

    def escaping_run(*_a, **_k):
        raise _Escapes("e.g. SystemExit from user code")

    # The dispatcher imports `run_simulation` lazily from this module.
    monkeypatch.setattr(SS, "run_simulation", escaping_run)
    try:
        job = SQ.solve_queue.enqueue("esc", project_key="esc-key", storage_dir=None)
        j = _wait_terminal(job.id)
        assert j is not None and j["status"] in ("failed", "aborted"), j

        with ctx.solver_state_lock:
            owner = ctx.solver_state.get("thread")
            status = ctx.solver_state.get("status")
        assert owner is None, (
            f"the context is still owned by {getattr(owner, 'name', owner)!r} — "
            "the dispatcher never exits, so every save and load of this project "
            "would 409 until a restart"
        )
        assert status != "running", status
    finally:
        PyPSAService._contexts.pop("esc-key", None)


def test_the_dispatcher_survives_the_escape_and_runs_the_next_job(monkeypatch):
    """The control on the other half of the contract: the release must not have
    been bought by letting the BaseException kill the dispatcher."""
    ctx = ProjectContext(network=build_network(), loaded_project="next")
    PyPSAService.register("next-key", ctx)
    calls = {"n": 0}

    def run(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Escapes("first job escapes")
        return "ok", "optimal"

    monkeypatch.setattr(SS, "run_simulation", run)
    try:
        first = SQ.solve_queue.enqueue("next", project_key="next-key", storage_dir=None)
        _wait_terminal(first.id)
        second = SQ.solve_queue.enqueue("next", project_key="next-key", storage_dir=None)
        j = _wait_terminal(second.id)
        assert calls["n"] == 2, "the dispatcher stopped after the escaping job"
        assert j is not None and j["status"] != "queued", j
    finally:
        PyPSAService._contexts.pop("next-key", None)


def test_the_restore_guard_marks_done_only_after_the_restore_ran():
    """
    The other half of SL-5. `_guarded_restore` is a closure inside
    `run_simulation`, reachable only by interrupting a real solve mid-restore,
    so its ORDERING is pinned here instead: with the flag set first, the
    defensive second call in the KeyboardInterrupt handler — which exists for
    exactly the mid-restore case — found it True and did nothing.
    """
    src = inspect.getsource(SS)
    i = src.index("def _guarded_restore")
    body = src[i:i + 900]
    assert body.index("_real_restore()") < body.index('_restore_done["v"] = True'), (
        "the restore guard is marked done before the restore runs again"
    )


# ── SL-7 ───────────────────────────────────────────────────────────────────


def _gridspine_job(**kw) -> SQ.SolveJob:
    return SQ.SolveJob(id=uuid.uuid4(), project_id="study", kind=SQ.KIND_GRIDSPINE, **kw)


def test_claim_parks_a_job_while_the_queue_drains():
    SQ.solve_queue._draining.set()
    job = _gridspine_job()
    assert SQ.solve_queue._claim(job, None, None) == "parked"
    assert job.status == "queued", (
        "a job popped during the quit drain was claimed; the process is "
        "exiting under it and interrupted jobs are never resumed"
    )


def test_a_parked_gridspine_job_is_left_queued_not_finished(monkeypatch):
    """A `False` from `_claim` would have sent it down the abort path, which
    records a terminal status — losing a job the drain exists to keep.

    The study itself is stubbed. Without the fix the job is NOT parked, so it
    runs — and the first cut of this test let it run for real against a
    nonexistent directory, where it blocked instead of failing. A test that
    hangs on the bug it guards burns the CI timeout and reports nothing; this
    one reports that the study ran.
    """
    from services import gridspine_service

    ran = []
    monkeypatch.setattr(gridspine_service, "run_study_dir",
                        lambda *a, **k: ran.append(a))
    SQ.solve_queue._draining.set()
    job = _gridspine_job(storage_dir="/nonexistent")
    SQ.solve_queue._run_gridspine_job(job)
    assert not ran, "a job popped during the quit drain RAN its study"
    assert job.status == "queued"
    assert job.finished_at is None


def test_claim_still_claims_when_not_draining():
    """The control."""
    job = _gridspine_job()
    assert SQ.solve_queue._claim(job, None, None) == "claimed"
    assert job.status == "running"


def test_a_job_with_no_storage_dir_closes_its_log_stream():
    job = _gridspine_job(storage_dir=None)
    SQ.solve_queue._run_gridspine_job(job)

    assert job.status == "failed"
    assert "storage directory" in (job.error or "")
    drained = []
    q = job.log_queue
    while True:
        try:
            drained.append(q.get_nowait())
        except Exception:  # noqa: BLE001 — empty
            break
    assert None in drained, (
        "the job's log stream was never closed; anything streaming it waits "
        "for an end-of-stream sentinel that is never sent"
    )
