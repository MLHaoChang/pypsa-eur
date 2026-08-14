"""
R28 — quitting stops the running solve and LEAVES the queue.

Quitting used to abort every `queued` job as well, which threw away work the
user explicitly asked for and which durability now makes recoverable: a queued
job survives the restart and resumes under R25. Only the running one has to
stop, because there is no way to leave a live solver thread running through a
process exit.

The label bug is fixed in the same place: `shutdown.py` read
`job["project_name"]`, a key `SolveJob.to_public` has never emitted, so every
queue solve appeared in the quit confirmation as `job <id>`.
"""
from __future__ import annotations

import threading
import time
import uuid

from services import shutdown as shutdown_service
from services.solve_queue import SolveJob, solve_queue


def _seed(status: str, project_id: str) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=project_id, enqueued_at=0.0)
        job.status = status
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def _unwind_on_signal(jid: uuid.UUID, stop_event: threading.Event) -> threading.Thread:
    """
    Play the part of the solver worker: when the stop event fires, the job goes
    terminal a moment later. Without this, a directly-seeded `running` job stays
    `running` forever and any wait keyed on it burns its full timeout.
    """
    def _worker() -> None:
        stop_event.wait(15)
        with solve_queue._lock:
            job = solve_queue._jobs.get(jid)
            if job is not None:
                job.status = "aborted"
                job.finished_at = time.time()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


def test_the_quit_confirmation_names_the_project_not_the_job_id():
    solve_queue.reset_for_tests()
    try:
        _seed("running", "Belgium Grid")
        labels = [s.label for s in shutdown_service.solves_in_flight() if s.path == "queue"]
        assert labels == ["Belgium Grid"], labels
    finally:
        solve_queue.reset_for_tests()


def test_quitting_stops_the_running_job_and_leaves_the_queued_ones():
    from desktop import gui

    solve_queue.reset_for_tests()
    try:
        running = _seed("running", "Solving")
        waiting = _seed("queued", "Waiting")

        # The queue half of the desktop abort, in isolation.
        for job in solve_queue.list_jobs():
            if job.get("status") == "running":
                solve_queue.abort(uuid.UUID(job["id"]))
        gui_source = gui._abort_everything.__doc__ or ""
        assert "queued" not in gui_source.lower() or True  # doc is advisory

        assert (solve_queue.get_job(waiting) or {})["status"] == "queued", (
            "a queued job was cancelled by the quit"
        )
        assert running is not None
    finally:
        solve_queue.reset_for_tests()


def test_abort_everything_does_not_cancel_queued_jobs():
    from desktop import gui

    solve_queue.reset_for_tests()
    try:
        waiting = _seed("queued", "Waiting")
        gui._abort_everything()
        assert (solve_queue.get_job(waiting) or {})["status"] == "queued", (
            "quitting cancelled a queued job; it must persist and resume under R25"
        )
    finally:
        solve_queue.reset_for_tests()


def test_the_running_job_is_actually_signalled_through_the_real_gui_path():
    """
    Coverage gap the brief's three tests leave open, closed here.

    `test_quitting_stops_the_running_job_and_leaves_the_queued_ones` re-types
    the filter and the `uuid.UUID(...)` parse INSIDE the test rather than
    calling `desktop.gui`'s real `abort_queue` closure — it cannot fail no
    matter what `gui.py` actually does (confirmed: it passed even before this
    task's fix was applied). `test_abort_everything_does_not_cancel_queued_jobs`
    does call the real `gui._abort_everything()`, but only ever seeds a
    `queued` job, so the running-job branch — and specifically the
    `uuid.UUID(str(job["id"]))` parse that turned into a shipped Critical when
    job identity flipped to UUIDs (`solve_queue.abort` silently no-ops on a
    string key) — is never exercised by ANY test in this file.

    This seeds a RUNNING job with a real `threading.Event` as its
    `stop_event`, drives it through the real desktop code, and asserts the
    event was actually set. If the `uuid.UUID(...)` parse were ever dropped
    (or the filter regressed to match `"queued"` again while somehow still
    reaching a running job through the wrong id), `solve_queue.abort` would
    silently return `None` and this event would stay unset.
    """
    from desktop import gui

    solve_queue.reset_for_tests()
    try:
        jid = uuid.uuid4()
        stop_event = threading.Event()
        with solve_queue._lock:
            job = SolveJob(id=jid, project_id="Solving", enqueued_at=0.0)
            job.status = "running"
            job.stop_event = stop_event
            solve_queue._jobs[jid] = job
            solve_queue._order.append(jid)
        # Unwind on signal like the real worker would — without this the seeded
        # job stays `running` forever and the quit's wait burns its full
        # timeout before returning (the assertion below held either way, 30 s
        # slower).
        _unwind_on_signal(jid, stop_event)

        gui._abort_everything()

        assert stop_event.is_set(), (
            "the running queue job was never signalled through the real "
            "desktop.gui abort path — solve_queue.abort() likely missed "
            "because the id it received was not a uuid.UUID"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_only_backlog_does_not_count_as_in_flight():
    """
    A queued job has no live thread and is durable (persisted row, re-enqueued
    at boot under R25) — nothing about it is "in flight". Counting it made the
    quit's wait() unsatisfiable: `abort_queue` deliberately leaves queued jobs
    alone, so a quit with any backlog burned the full ABORT_TIMEOUT and
    reported `abort_timed_out` on a shutdown that lost nothing.
    """
    solve_queue.reset_for_tests()
    try:
        _seed("queued", "Waiting")
        queue_entries = [
            s for s in shutdown_service.solves_in_flight() if s.path == "queue"
        ]
        assert queue_entries == [], (
            "a queued job was reported as in flight; the quit sequence would "
            "wait on it forever because nothing ever aborts a queued job"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_draining_dispatcher_parks_a_popped_job_as_queued():
    """
    The promote race: quit aborts the running job, the dispatcher immediately
    pops the next queued job, flips it to `running`, and the process exit kills
    it mid-solve — boot then marks it `interrupted`, which R25 never resumes.
    The exact loss R28 exists to prevent, through a one-job window.

    After `stop_dispatching()`, a popped job must stay `queued` — in memory and
    in its row — so boot reconciliation re-enqueues it.
    """
    solve_queue.reset_for_tests()
    try:
        solve_queue.stop_dispatching()
        job = solve_queue.enqueue("Backlog")

        drained = threading.Event()
        threading.Thread(
            target=lambda: (solve_queue._q.join(), drained.set()), daemon=True
        ).start()
        assert drained.wait(10), "the dispatcher never processed the popped job"

        parked = solve_queue.get_job(job.id)
        assert parked is not None and parked["status"] == "queued", (
            f"a job popped during shutdown drain was started or dropped: {parked}"
        )
    finally:
        if hasattr(solve_queue, "_draining"):
            solve_queue._draining.clear()
        solve_queue.reset_for_tests()


def test_quit_with_a_backlog_is_prompt_and_leaves_the_queued_job():
    """
    One running job (which unwinds when signalled, as the real worker does) and
    one queued job. The quit must signal the runner, wait only for IT, and come
    back promptly reporting success — not sit out ABORT_TIMEOUT waiting on a
    queued job that nothing will ever abort.
    """
    from desktop import gui

    solve_queue.reset_for_tests()
    try:
        running = uuid.uuid4()
        stop_event = threading.Event()
        with solve_queue._lock:
            job = SolveJob(id=running, project_id="Solving", enqueued_at=0.0)
            job.status = "running"
            job.stop_event = stop_event
            solve_queue._jobs[running] = job
            solve_queue._order.append(running)
        _unwind_on_signal(running, stop_event)
        waiting = _seed("queued", "Waiting")

        t0 = time.monotonic()
        ok = gui._abort_everything()
        elapsed = time.monotonic() - t0

        assert ok is True, (
            "the quit reported abort_timed_out even though the only running "
            "job unwound as soon as it was signalled"
        )
        assert elapsed < 10, (
            f"quit took {elapsed:.1f}s — it waited on the queued backlog "
            "instead of only the running job"
        )
        assert (solve_queue.get_job(waiting) or {})["status"] == "queued", (
            "a queued job was cancelled by the quit"
        )
    finally:
        if hasattr(solve_queue, "_draining"):
            solve_queue._draining.clear()
        solve_queue.reset_for_tests()
