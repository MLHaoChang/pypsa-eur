"""
R5 — a registered queue solve is counted ONCE, by the job table.

Registering the dispatcher's context (increment 1) falsified the constraint
`services/shutdown.py:144-153` records: that a running queue job's context is
in neither registry. Without this skip, `_context_solves()` walks the now-
resident context and `solves_in_flight()` also reads the job table, so the quit
confirmation lists the same solve twice — once labelled `"active"`, i.e. as
abortable through `/api/simulation/abort`, which it is not.

The job table stays the single source for queue solves because it is the only
one that also sees a job that is still `queued`.
"""
from __future__ import annotations

import threading

from services import shutdown as shutdown_service


def test_a_queue_owned_context_is_counted_once_by_the_job_table():
    from services.pypsa_service import PyPSAService
    from services.solve_queue import SolveJob, solve_queue

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    solve_queue.reset_for_tests()
    PyPSAService._contexts["queue-owned-solve"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="queue")
        with solve_queue._lock:
            job = SolveJob(id=931, project_id="Q", enqueued_at=0.0)
            job.status = "running"
            solve_queue._jobs[931] = job
            solve_queue._order.append(931)

        paths = [s.path for s in shutdown_service.solves_in_flight()]

        assert paths == ["queue"], paths
    finally:
        running.set()
        worker.join(5)
        PyPSAService._contexts.pop("queue-owned-solve", None)
        solve_queue.reset_for_tests()


def test_a_foreground_solve_on_a_background_context_is_still_seen():
    """
    The skip must key on `kind`, not on residency. A legacy `/run` worker that
    happens to own a non-active resident context is still path (a) and still
    abortable — narrowing the walk to the foreground is the regression
    `tests/test_shutdown.py::test_a_solve_on_a_NON_ACTIVE_context_is_seen`
    already pins from the other side.
    """
    from services.pypsa_service import PyPSAService

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    PyPSAService._contexts["lopf-on-background"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="lopf")

        assert [s.path for s in shutdown_service.solves_in_flight()] == ["active"]
    finally:
        running.set()
        worker.join(5)
        PyPSAService._contexts.pop("lopf-on-background", None)
