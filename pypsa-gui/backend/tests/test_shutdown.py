"""
Quitting without losing the user's work (phase 2a, Task 4 — the workstream).

Everything before this task was scaffolding. This is the part that can lose
data, and the hazard is not the obvious one. From the plan's verified
constraints:

  #4  A hard quit mid-solve LOSES THE SOLVE; it does not corrupt the project.
      `atomic_io` leaves the previous file intact on a killed export, and
      `_save_context` REFUSES with 409 while a solver is in flight for that
      context. The real hazard is the inverse: an incomplete abort makes the
      flush 409, unsaved edits are dropped, and the app reports a clean quit.

So the sequence must abort BEFORE it flushes, and it must REPORT a flush it
could not perform rather than swallowing it.

**Three solve paths, and they are not interchangeable** (constraint #5):

  (a) the active context's stop event, via `/api/simulation/abort`
  (b) queue jobs, each with its own event, aborted by `solve_queue.abort(id)`
  (c) `run_ac_pf`, which creates a stop event that NOTHING READS —
      `ac_pf_service` contains zero occurrences of `stop_event`

(c) cannot be interrupted at all. The decision taken rather than deferred:
wait a bounded time, then warn and skip that context's flush, reporting it.
Abandoning the wait silently would report a clean quit over dropped edits,
which is exactly constraint #4's hazard.

**Why path (b) gets its own test driven through `solve_queue.enqueue`**
(constraint #6): `solve_queue._run_job` builds its context with
`PyPSAService.build_context()` — documented as "off to the side, not
activated" — and the module never calls `register`. A running queue job's
context is therefore in NEITHER `_contexts` NOR `_active`. A test that
hand-registers a context would pass against an implementation that only
iterates the registry, which is precisely the bug this guards.
"""
from __future__ import annotations

import pathlib
import threading
import time

import pytest
from fastapi import HTTPException

from services import shutdown as shutdown_service


# ── the three solve paths ───────────────────────────────────────────────────


def test_no_solve_running_means_nothing_is_in_flight():
    assert shutdown_service.solves_in_flight() == []


def test_path_a_the_active_context_solver_is_seen(monkeypatch):
    """(a) the foreground solve, the one `/api/simulation/abort` targets."""
    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    try:
        monkeypatch.setattr(shutdown_service, "_active_solver_thread", lambda: worker)

        in_flight = shutdown_service.solves_in_flight()

        assert [s.path for s in in_flight] == ["active"]
    finally:
        running.set()
        worker.join(5)


def test_path_b_a_queued_job_is_seen_through_the_real_queue():
    """
    (b) driven through `solve_queue.enqueue`, NEVER a hand-registered context.

    A queue job's context is in neither `_contexts` nor `_active`, so an
    implementation that iterates the context registry misses this entirely —
    and would pass a test that registered a context by hand.
    """
    from services.solve_queue import solve_queue

    solve_queue.reset_for_tests()
    try:
        solve_queue.enqueue("quitting-mid-queue")

        in_flight = shutdown_service.solves_in_flight()

        assert "queue" in [s.path for s in in_flight], in_flight
    finally:
        solve_queue.reset_for_tests()


def test_path_c_an_ac_pf_run_is_seen_even_though_it_cannot_be_aborted(monkeypatch):
    """
    (c) `run_ac_pf` creates a stop event nothing reads. Being unable to stop it
    is not a reason to be unable to SEE it — the sequence has to know it is
    there in order to wait, then warn, then report the skipped flush.
    """
    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    try:
        monkeypatch.setattr(shutdown_service, "_ac_pf_thread", lambda: worker)

        in_flight = shutdown_service.solves_in_flight()

        assert [s.path for s in in_flight] == ["ac_pf"]
        assert in_flight[0].interruptible is False, (
            "ac_pf must not be reported as abortable — nothing reads its stop event"
        )
    finally:
        running.set()
        worker.join(5)


# ── the ordered sequence ────────────────────────────────────────────────────


class _Recorder:
    """
    A double over every injected collaborator, recording the ORDER.

    Steps 6 and 8 live outside `services/`, which is why `shutdown_sequence`
    takes its collaborators as arguments at all: `release_all_for_user` needs a
    DB session and `stop_server` is the launcher's. Injecting them is what
    makes the order assertable without standing up a server.
    """

    # One in-flight solve by default. `confirm` is skipped when nothing is
    # running (D12), so an order test that passed none would assert an order
    # the sequence never takes.
    SOLVING = [shutdown_service.InFlightSolve("active", "a project", True)]

    def __init__(self, *, confirm=True, aborted=True, in_flight=None):
        self.calls: list[str] = []
        self._confirm = confirm
        self._aborted = aborted
        self._in_flight = self.SOLVING if in_flight is None else in_flight

    def gate(self):
        self.calls.append("gate")

    def un_gate(self):
        self.calls.append("un_gate")

    def confirm(self, in_flight):
        self.calls.append("confirm")
        return self._confirm

    def hide(self):
        self.calls.append("hide")

    def abort_and_wait(self):
        self.calls.append("abort_and_wait")
        return self._aborted

    def flush(self, *, safe):
        self.calls.append(f"flush(safe={safe})")
        return []

    def release_locks(self):
        self.calls.append("release_locks")

    def stop_executor(self):
        self.calls.append("stop_executor")

    def stop_server(self):
        self.calls.append("stop_server")
        return "clean"

    def run(self):
        return shutdown_service.shutdown_sequence(
            gate=self.gate, un_gate=self.un_gate, confirm=self.confirm,
            hide=self.hide, abort_and_wait=self.abort_and_wait, flush=self.flush,
            release_locks=self.release_locks, stop_executor=self.stop_executor,
            stop_server=self.stop_server, in_flight=self._in_flight,
        )


def test_the_eight_steps_run_in_order():
    rec = _Recorder()

    report = rec.run()

    assert rec.calls == [
        "gate",              # 1. reversible, no GUI
        "confirm",           # 2. D12, only when a solve is in flight
        "hide",              # 3. ONLY after the user chose Quit
        "abort_and_wait",    # 4. before the flush, per constraint #4
        "flush(safe=True)",  # 5.
        "release_locks",     # 6.
        "stop_executor",     # 7.
        "stop_server",       # 8.
    ]
    assert report.quit is True


def test_the_gate_comes_before_the_confirm_dialog():
    """
    Gating is reversible and touches no GUI; hiding is neither. A confirm
    dialog raised against a just-hidden window is invisible on macOS, and the
    previous design's "quiesce first" left the app wedged with no reverse path
    when the user chose Cancel.
    """
    rec = _Recorder()
    rec.run()

    assert rec.calls.index("gate") < rec.calls.index("confirm") < rec.calls.index("hide")


def test_cancelling_un_gates_and_leaves_the_app_usable():
    """
    The veto branch. The window is still visible because step 1 did not hide
    it, so the app must go back to fully working — not merely "not quit".
    """
    rec = _Recorder(confirm=False)

    report = rec.run()

    assert report.quit is False
    assert "un_gate" in rec.calls, "mutations would stay 503 forever"
    assert "hide" not in rec.calls
    assert "stop_server" not in rec.calls


def test_a_second_close_after_a_cancel_starts_a_new_sequence():
    """
    The latch must be CLEARED by the veto, not merely not-set. Otherwise the
    user cancels once and can never quit again.
    """
    first = _Recorder(confirm=False)
    first.run()

    second = _Recorder(confirm=True)
    report = second.run()

    assert report.quit is True
    assert second.calls[0] == "gate"


def test_a_second_close_while_one_is_running_is_a_no_op():
    """
    Idempotent WHILE RUNNING. `webview` will happily deliver a second close
    event while the first sequence is mid-flush; running the eight steps twice
    concurrently would abort twice and flush twice.
    """
    started = threading.Event()
    release = threading.Event()
    seen: list[str] = []

    def slow_flush(*, safe):
        seen.append("flush")
        started.set()
        release.wait(10)
        return []

    def run_one():
        shutdown_service.shutdown_sequence(
            gate=lambda: None, un_gate=lambda: None,
            confirm=lambda in_flight: True, hide=lambda: None,
            abort_and_wait=lambda: True, flush=slow_flush,
            release_locks=lambda: None, stop_executor=lambda: None,
            stop_server=lambda: "clean",
        )

    worker = threading.Thread(target=run_one, daemon=True)
    worker.start()
    try:
        assert started.wait(10), "the first sequence never reached the flush"

        second = _Recorder()
        report = second.run()

        assert report.already_running is True
        assert second.calls == [], second.calls
    finally:
        release.set()
        worker.join(15)

    assert seen == ["flush"], "the flush ran more than once"


def test_a_flush_that_could_not_run_is_REPORTED_not_swallowed():
    """
    Constraint #4's real hazard. If the abort timed out, the save refuses with
    409 and the edits are gone — reporting a clean quit over that is the
    failure this whole workstream exists to prevent.
    """
    rec = _Recorder(aborted=False)

    report = rec.run()

    assert "flush(safe=False)" in rec.calls, (
        "the flush must still be attempted, and told the abort did not finish"
    )
    assert report.abort_timed_out is True
    assert report.quit is True, "a failed abort must not leave the app unquittable"


def test_the_process_still_exits_when_a_step_raises():
    """
    Steps 6 and 7 touch a database session and a thread pool. If either throws,
    the user is left with a window that will not close — worse than the failure
    it is reacting to. The report carries the error instead.
    """
    def exploding_release():
        raise RuntimeError("the database went away")

    report = shutdown_service.shutdown_sequence(
        gate=lambda: None, un_gate=lambda: None,
        confirm=lambda in_flight: True, hide=lambda: None,
        abort_and_wait=lambda: True, flush=lambda *, safe: [],
        release_locks=exploding_release, stop_executor=lambda: None,
        stop_server=lambda: "clean",
    )

    assert report.quit is True
    assert any("database went away" in e for e in report.errors), report.errors


def test_the_confirm_dialog_is_skipped_when_nothing_is_solving():
    """
    D12 asks only when there is something to lose. Prompting on every quit
    trains the user to dismiss it, which is how the one that mattered gets
    dismissed too.
    """
    asked: list[bool] = []

    shutdown_service.shutdown_sequence(
        gate=lambda: None, un_gate=lambda: None,
        confirm=lambda in_flight: (asked.append(True), True)[1],
        hide=lambda: None, abort_and_wait=lambda: True,
        flush=lambda *, safe: [], release_locks=lambda: None,
        stop_executor=lambda: None, stop_server=lambda: "clean",
        in_flight=[],
    )

    assert asked == [], "asked for confirmation with no solve running"


# ── flushing every resident context ─────────────────────────────────────────


def test_the_active_context_persists_its_time_series_and_others_do_not():
    """
    Constraint #8, and it is asymmetric for a reason: `_serialize_user_ts`
    reads a PROCESS-GLOBAL store belonging to the foreground. Copying
    `persist_user_ts=True` to every context stamps the foreground's series onto
    all of them; copying False loses the active project's.
    """
    saved: list[tuple[str, bool]] = []

    class _Ctx:
        def __init__(self, name): self.name = name

    active, other = _Ctx("active"), _Ctx("other")

    shutdown_service.flush_all(
        contexts=[active, other],
        active=active,
        save=lambda ctx, persist_user_ts: saved.append((ctx.name, persist_user_ts)),
        flush_chat=lambda: None,
        safe=True,
    )

    assert saved == [("active", True), ("other", False)], saved


def test_one_context_failing_does_not_strand_the_others():
    saved: list[str] = []

    class _Ctx:
        def __init__(self, name): self.name = name

    a, b, c = _Ctx("a"), _Ctx("b"), _Ctx("c")

    def save(ctx, persist_user_ts):
        if ctx.name == "b":
            raise RuntimeError("disk full")
        saved.append(ctx.name)

    problems = shutdown_service.flush_all(
        contexts=[a, b, c], active=a, save=save, flush_chat=lambda: None, safe=True,
    )

    assert saved == ["a", "c"], saved
    assert any("b" in p for p in problems), problems


def test_a_409_is_caught_specifically_and_reported():
    """
    `_save_context` raises `HTTPException(409)` when a solver is still in
    flight for that context — a DIFFERENT failure from a disk error, and the
    one that means "your edits were not written". Catching bare `Exception`
    would report it as a generic problem; the user needs to know their work is
    the thing at stake.
    """
    class _Ctx:
        name = "still-solving"

    def save(ctx, persist_user_ts):
        raise HTTPException(status_code=409, detail="solver in flight")

    problems = shutdown_service.flush_all(
        contexts=[_Ctx()], active=None, save=save, flush_chat=lambda: None, safe=False,
    )

    assert len(problems) == 1
    # NOT `"409" in message` — the generic branch echoes the status code too,
    # so that assertion passes whether or not the 409 is handled specially.
    # What distinguishes them is that the user is told what it MEANS.
    assert "still running" in problems[0], problems
    assert "abort did not finish" in problems[0], problems


def test_a_non_409_failure_reads_differently_from_a_409():
    """
    The other half of the distinction. Without this, one message could satisfy
    both tests and the branch would be decorative.
    """
    class _Ctx:
        name = "broken"

    def save(ctx, persist_user_ts):
        raise HTTPException(status_code=500, detail="something else entirely")

    problems = shutdown_service.flush_all(
        contexts=[_Ctx()], active=None, save=save, flush_chat=lambda: None, safe=True,
    )

    assert "still running" not in problems[0], problems
    assert "something else entirely" in problems[0], problems


def test_the_chat_transcript_is_flushed_even_though_it_is_a_no_op_today():
    """
    `chat_service.flush_to_disk` is documented as a Phase-0 no-op —
    `append_turn` already writes synchronously. Called for a stable call site,
    NOT because omitting it loses data. Claiming otherwise is what got an
    earlier revision of the plan rejected.
    """
    called: list[str] = []

    shutdown_service.flush_all(
        contexts=[], active=None, save=lambda ctx, persist_user_ts: None,
        flush_chat=lambda: called.append("chat"), safe=True,
    )

    assert called == ["chat"]


# ── the HTTP mutation gate ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _never_leave_the_gate_closed():
    """
    A gate is process-global state. A test that closed it and failed before
    re-opening would 503 every mutating request in every later test — and the
    failure would look like a bug in whatever ran next.
    """
    yield
    shutdown_service.un_gate_mutations()


def test_the_gate_refuses_mutations_with_a_typed_code(client):
    """
    503, not 409. 409 already means "a solver is in flight, try again" and the
    frontend retries it; a shutting-down backend must not invite a retry. The
    typed code is what lets the SPA tell them apart without parsing prose.
    """
    shutdown_service.gate_mutations()

    response = client.post("/api/network/reset")

    assert response.status_code == 503, response.text
    assert response.json().get("code") == "shutting_down", response.json()


def test_reads_still_work_while_the_gate_is_closed(client):
    """
    The window is still visible at this point — step 1 does not hide it — and
    the user may be looking at a confirm dialog. A blank workbench behind it
    would be alarming and is not necessary: reads never mutate.
    """
    shutdown_service.gate_mutations()

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/network/buses").status_code == 200


def test_un_gating_restores_mutations(client):
    """The Cancel path. Without this the app is wedged with no reverse."""
    shutdown_service.gate_mutations()
    assert client.post("/api/network/reset").status_code == 503

    shutdown_service.un_gate_mutations()

    assert client.post("/api/network/reset").status_code == 200


def test_the_gate_is_open_by_default():
    """
    In a FRESH interpreter, because the autouse fixture above un-gates after
    every test — so an in-process assertion here is satisfied by whatever ran
    before it, and passes against a module that gates itself on import.

    Caught by mutation: `_gated.set()` at module scope survived the whole
    suite. A shipped build with that defect would refuse every mutation from
    the moment it started, with a message saying it was shutting down.
    """
    import subprocess
    import sys

    backend = str(pathlib.Path(__file__).resolve().parent.parent)
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {backend!r})\n"
         "from services import shutdown\n"
         "print('GATED' if shutdown.mutations_gated() else 'OPEN')"],
        capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "OPEN" in result.stdout, result.stdout


def test_mutations_work_when_nothing_has_gated_them(client):
    assert client.post("/api/network/reset").status_code == 200


# ── aborting, across three paths that behave differently ────────────────────


def test_aborting_signals_the_active_solve_and_every_queue_job():
    """
    Paths (a) and (b) can both be stopped, but not by the same call:
    `/api/simulation/abort` targets the foreground context's stop event, while
    each queue job carries its own and is reached by `solve_queue.abort(id)`.
    """
    signalled: list[str] = []

    report = shutdown_service.abort_and_wait(
        in_flight=[
            shutdown_service.InFlightSolve("active", "open project", True),
            shutdown_service.InFlightSolve("queue", "job 7", True),
        ],
        abort_active=lambda: signalled.append("active"),
        abort_queue=lambda: signalled.append("queue"),
        wait=lambda timeout: True,
        timeout=1.0,
    )

    assert sorted(signalled) == ["active", "queue"]
    assert report is True


def test_an_ac_pf_run_is_waited_for_but_never_signalled():
    """
    Path (c). `run_ac_pf` creates a stop event and `ac_pf_service` reads it
    nowhere, so there is nothing to signal — pretending otherwise would make
    the code look like it handles a case it does not.

    The decision, taken rather than deferred: wait the bounded time, then warn
    and let the flush report what it could not write.
    """
    signalled: list[str] = []

    result = shutdown_service.abort_and_wait(
        in_flight=[shutdown_service.InFlightSolve("ac_pf", "AC power flow", False)],
        abort_active=lambda: signalled.append("active"),
        abort_queue=lambda: signalled.append("queue"),
        wait=lambda timeout: False,          # never finishes in time
        timeout=0.1,
    )

    assert signalled == [], "nothing reads AC PF's stop event; signalling it is theatre"
    assert result is False, "an unfinished abort must be reported, not assumed"


def test_interruptible_is_the_authority_not_the_path_name():
    """
    A solve marked non-interruptible must not be signalled even when its path
    HAS a signaller.

    Without this the `if s.interruptible` filter is dead code: every
    non-interruptible solve today happens to sit on a path (`ac_pf`) that has
    no entry in the signal table, so the field never changes what happens.
    Caught by mutation — dropping the filter left all 27 tests green.

    It matters because AC PF and the LP worker both live in `routers.simulation`
    `_state`. The moment one is reported under the other's path — a refactor
    away — the path name stops being a proxy for "can this be stopped" and the
    field is the only thing standing between the shutdown and a signal that
    nothing reads.
    """
    signalled: list[str] = []

    shutdown_service.abort_and_wait(
        in_flight=[shutdown_service.InFlightSolve("active", "ac pf, active slot", False)],
        abort_active=lambda: signalled.append("active"),
        abort_queue=lambda: signalled.append("queue"),
        wait=lambda timeout: True,
        timeout=1.0,
    )

    assert signalled == [], "signalled a solve that nothing can stop"


def test_a_solver_that_ignores_the_abort_is_reported_not_waited_on_forever():
    """
    HiGHS and Gurobi do not yield until the next iteration boundary, so
    `status` flips to "aborted" instantly while the worker stays alive for
    seconds. An unbounded wait here is a window that never closes.
    """
    # Records the budget it was handed rather than sleeping through it. A
    # double that slept would turn "the bound was dropped" into a hung test
    # instead of a failing one — which is exactly what happened first time.
    handed: list[float] = []

    def wait(timeout):
        handed.append(timeout)
        return False

    result = shutdown_service.abort_and_wait(
        in_flight=[shutdown_service.InFlightSolve("active", "stubborn", True)],
        abort_active=lambda: None, abort_queue=lambda: None,
        wait=wait, timeout=0.3,
    )

    assert result is False
    assert handed == [0.3], (
        f"the wait was given {handed} rather than the caller's bound — an "
        "unbounded wait here is a window that never closes"
    )


def test_nothing_in_flight_needs_no_abort_and_reports_success():
    called: list[str] = []

    result = shutdown_service.abort_and_wait(
        in_flight=[],
        abort_active=lambda: called.append("active"),
        abort_queue=lambda: called.append("queue"),
        wait=lambda timeout: called.append("wait") or True,
        timeout=1.0,
    )

    assert called == [], "nothing was running; there is nothing to abort or wait for"
    assert result is True


def test_a_failing_abort_signal_does_not_stop_the_others():
    """
    One path throwing must not leave the rest un-signalled — that would strand
    a solve the sequence is about to flush around.
    """
    signalled: list[str] = []

    def explode():
        raise RuntimeError("abort endpoint blew up")

    result = shutdown_service.abort_and_wait(
        in_flight=[
            shutdown_service.InFlightSolve("active", "a", True),
            shutdown_service.InFlightSolve("queue", "b", True),
        ],
        abort_active=explode,
        abort_queue=lambda: signalled.append("queue"),
        wait=lambda timeout: True,
        timeout=1.0,
    )

    assert signalled == ["queue"]
    assert result is False, "an abort that partly failed is not a clean abort"


# ── the window's close handler ──────────────────────────────────────────────
#
# TRI-STATE, and each state exists because of a specific failure:
#
#   not started  -> start the worker, VETO this close
#   in progress  -> VETO, and do not start a second worker
#   complete     -> ALLOW
#
# The GUI thread must return from the handler promptly: a UI thread blocked
# past ~5 s is ghosted as "Not Responding" on Windows, and the sequence's own
# worst case is far past that (`abort_and_wait` alone budgets 30 s). So the
# eight steps run on a worker and the handler only ever answers a question.


def _handler(**kwargs):
    kwargs.setdefault("run", lambda: shutdown_service.ShutdownReport(quit=True))
    kwargs.setdefault("destroy", lambda: None)
    return shutdown_service.CloseHandler(**kwargs)


def test_the_first_close_starts_the_sequence_and_vetoes_the_close():
    started = threading.Event()

    handler = _handler(run=lambda: (started.set(), shutdown_service.ShutdownReport(quit=True))[1])

    assert handler.on_closing() is False, "the window must not close before the flush"
    assert started.wait(10), "the sequence never ran"


def test_the_handler_returns_immediately_rather_than_blocking_the_ui_thread():
    """
    A UI thread blocked past ~5 s is ghosted as *Not Responding* on Windows,
    and `abort_and_wait` alone budgets 30 s. The handler must answer, not work.
    """
    release = threading.Event()
    handler = _handler(run=lambda: (release.wait(20), shutdown_service.ShutdownReport(quit=True))[1])

    started = time.monotonic()
    handler.on_closing()
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 2, f"the close handler blocked the UI thread for {elapsed:.1f}s"


def test_a_second_close_while_running_vetoes_without_starting_another_sequence():
    runs: list[int] = []
    release = threading.Event()

    def run():
        runs.append(1)
        release.wait(20)
        return shutdown_service.ShutdownReport(quit=True)

    handler = _handler(run=run)
    try:
        assert handler.on_closing() is False
        time.sleep(0.2)

        assert handler.on_closing() is False
        assert len(runs) == 1, f"started {len(runs)} sequences"
    finally:
        release.set()


def test_the_state_flips_to_complete_BEFORE_destroy_is_called():
    """
    The deadlock this prevents is subtle and total. `window.destroy()` re-fires
    the `closing` event, so if the state were flipped AFTER the call, the
    re-entrant handler would still see "in progress", veto its own destroy, and
    the window would never close — with the backend already stopped behind it.
    """
    observed: list[bool] = []
    handler = None

    def destroy():
        # Exactly what pywebview does: `destroy()` re-enters `closing`.
        observed.append(handler.on_closing())

    handler = _handler(destroy=destroy)
    handler.on_closing()
    assert handler.wait_for_completion(20)

    assert observed == [True], (
        "destroy() re-entered the handler and was vetoed — the window would "
        "never close and the backend is already down"
    )


def test_destroy_is_called_exactly_once():
    calls: list[int] = []
    handler = _handler(destroy=lambda: calls.append(1))

    handler.on_closing()
    assert handler.wait_for_completion(20)

    assert calls == [1], calls


def test_a_cancelled_quit_resets_so_the_user_can_close_again_later():
    """
    The veto path. `shutdown_sequence` un-gates and clears its own latch; the
    handler has to do the same or the window is stuck open forever — the user
    cancels once and can never quit.
    """
    outcomes = [shutdown_service.ShutdownReport(quit=False),
                shutdown_service.ShutdownReport(quit=True)]
    destroyed: list[int] = []

    handler = _handler(run=lambda: outcomes.pop(0), destroy=lambda: destroyed.append(1))

    assert handler.on_closing() is False
    assert handler.wait_for_completion(20)
    assert destroyed == [], "the window was destroyed despite the user cancelling"

    # A later close must start a NEW sequence, not report the old outcome.
    assert handler.on_closing() is False
    assert handler.wait_for_completion(20)
    assert destroyed == [1]


def test_a_sequence_that_raises_still_lets_the_window_close():
    """
    A window that cannot be closed is worse than any failure it is reacting
    to — the user's only remaining option is Force Quit, which is exactly the
    outcome this workstream exists to avoid.
    """
    destroyed: list[int] = []

    def explode():
        raise RuntimeError("the shutdown sequence blew up")

    handler = _handler(run=explode, destroy=lambda: destroyed.append(1))

    assert handler.on_closing() is False
    assert handler.wait_for_completion(20)
    assert destroyed == [1], "the window was left un-closable after a failure"
    assert handler.report is not None and handler.report.errors
