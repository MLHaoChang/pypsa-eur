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
#
# The original path (a) and path (c) tests monkeypatched `_active_solver_thread`
# and `_ac_pf_thread`. Both are gone. The second read a `_state` key NOTHING in
# the backend ever wrote, so path (c) never fired in production and the test
# passed only because it patched the very function that was wrong. They are
# replaced at the end of this file by tests that drive the real
# `ctx.solver_state`.


def test_no_solve_running_means_nothing_is_in_flight():
    assert shutdown_service.solves_in_flight() == []


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
        # `loaded_project`, NOT `name` — `ProjectContext` has no `name`, and
        # doubles that invented one meant these tests only ever exercised a
        # branch production never takes. Round 5 found the naming code was
        # therefore falling through to `repr(ctx)`, a multi-line dump of the
        # whole pypsa.Network, as the user-facing "which project was not
        # saved" string.
        def __init__(self, name): self.loaded_project = name

    active, other = _Ctx("active"), _Ctx("other")

    shutdown_service.flush_all(
        contexts=[active, other],
        active=active,
        save=lambda ctx, persist_user_ts: saved.append((ctx.loaded_project, persist_user_ts)),
        flush_chat=lambda: None,
        safe=True,
    )

    assert saved == [("active", True), ("other", False)], saved


def test_one_context_failing_does_not_strand_the_others():
    saved: list[str] = []

    class _Ctx:
        # `loaded_project`, NOT `name` — `ProjectContext` has no `name`, and
        # doubles that invented one meant these tests only ever exercised a
        # branch production never takes. Round 5 found the naming code was
        # therefore falling through to `repr(ctx)`, a multi-line dump of the
        # whole pypsa.Network, as the user-facing "which project was not
        # saved" string.
        def __init__(self, name): self.loaded_project = name

    a, b, c = _Ctx("a"), _Ctx("b"), _Ctx("c")

    def save(ctx, persist_user_ts):
        if ctx.loaded_project == "b":
            raise RuntimeError("disk full")
        saved.append(ctx.loaded_project)

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
        loaded_project = "still-solving"

    def save(ctx, persist_user_ts):
        raise HTTPException(status_code=409, detail="solver in flight")

    problems = shutdown_service.flush_all(
        contexts=[_Ctx()], active=None, save=save, flush_chat=lambda: None, safe=False,
    )

    assert len(problems) == 1
    # NOT `"409" in message` — the generic branch echoes the status code too,
    # so that assertion passes whether or not the 409 is handled specially.
    # What distinguishes them is that the user is told what it MEANS.
    #
    # This assertion used to be `"still running" in problems[0]`, which pinned
    # a CAUSE the code asserted rather than knew. `_save_context` also raises
    # 409 to refuse overwriting a saved project with an empty network, and that
    # case was reported as "a solve was still running" when none was. The
    # reason given is now echoed, so the assertion is on the detail.
    assert "solver in flight" in problems[0], problems
    assert "abort did not finish" in problems[0], problems


def test_a_non_409_failure_reads_differently_from_a_409():
    """
    The other half of the distinction. Without this, one message could satisfy
    both tests and the branch would be decorative.
    """
    class _Ctx:
        loaded_project = "broken"

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
    The deadlock this prevents is subtle, total, and WINDOWS-ONLY.

    `destroy()` re-fires `closing` on winforms and gtk; measured with a real
    window against the installed pywebview 6.2.1, it does NOT on cocoa. So this
    test fakes the re-entry on purpose — on the development platform the real
    thing never happens, and without the fake there would be no coverage at all
    for the platform where flipping the state late leaves a window that can
    never close with the backend already stopped behind it.
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


# ── wiring the real collaborators ───────────────────────────────────────────


def test_the_resident_contexts_include_the_active_one_exactly_once():
    """
    `_active` is a BOOTSTRAP slot, not a member of `_contexts`. After the first
    request it is usually `None`, and when it is set it may ALSO be registered
    — so a naive `list(_contexts.values()) + [_active]` either misses the open
    project or saves it twice. Saving twice is not harmless: the second write
    goes through `atomic_io` again with `persist_user_ts` computed the same way,
    so it is wasted 100 MB-scale I/O during a quit the user is waiting on.
    """
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.build_context()
    PyPSAService._contexts["shutdown-test"] = ctx
    PyPSAService._active = ctx
    try:
        contexts = shutdown_service.resident_contexts()

        assert contexts.count(ctx) == 1, "the active context appears twice"
    finally:
        PyPSAService._contexts.pop("shutdown-test", None)
        PyPSAService._active = None


def test_a_registered_context_that_is_not_active_is_still_flushed():
    """
    The other direction, and it was untested: both earlier tests put the
    context in `_active`, so deleting the registry walk entirely left them
    green. `RESIDENT_CAP` is 5, so up to four projects can hold unsaved edits
    while not being the open one — LRU eviction and the solve queue both put
    them there.
    """
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.build_context()
    PyPSAService._contexts["registered-not-active"] = ctx
    PyPSAService._active = None
    try:
        assert ctx in shutdown_service.resident_contexts()
    finally:
        PyPSAService._contexts.pop("registered-not-active", None)


def test_an_active_context_that_was_never_registered_is_still_flushed():
    """
    The case that loses work. `adopt_process_foreground()` hands `_active` out
    and CLEARS it, so a session-less caller — which is exactly the desktop
    configuration — gets a freshly created context that is in NO registry. Miss
    it and the open project's unsaved edits are the ones that go.
    """
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.build_context()
    PyPSAService._active = ctx
    try:
        assert ctx in shutdown_service.resident_contexts()
    finally:
        PyPSAService._active = None


def test_stopping_the_executor_cancels_pending_work_but_does_not_hang():
    """
    Constraint #10: `chat_service` creates a module-level `ThreadPoolExecutor`
    that is never shut down, and CPython's atexit joiner blocks interpreter
    exit on it. `cancel_futures=True` cancels only PENDING work — a running
    tool call still has to finish — so this is necessary, not sufficient, and
    the bounded server exit is what makes it safe.
    """
    import concurrent.futures

    # Its OWN executor. The first version shut down the real, shared one and
    # passed only because pytest runs `test_chat_*.py` alphabetically earlier —
    # any future test file sorting after this one would have been poisoned by
    # it, and the failure would look like a bug in that file.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    executor.submit(time.sleep, 0.05)
    started = time.monotonic()

    shutdown_service.stop_tool_executor(executor)

    assert time.monotonic() - started < 10


def test_the_executor_shutdown_does_not_WAIT_for_running_work():
    """
    `wait=False` is the point and it was untested — the earlier test submitted
    a 50 ms task, so `wait=True` returned just as fast and the mutant survived.

    This submits work that outlasts any reasonable quit. A running tool call
    cannot be cancelled (`cancel_futures` reaches only PENDING work), so
    waiting for it would hold the window open for as long as the model takes.
    """
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    executor.submit(time.sleep, 30)
    time.sleep(0.1)                      # let it actually start
    started = time.monotonic()

    shutdown_service.stop_tool_executor(executor)
    elapsed = time.monotonic() - started

    assert elapsed < 2, (
        f"waited {elapsed:.1f}s for a running tool call — the quit would hang "
        "for as long as the model takes"
    )


def test_a_failing_executor_shutdown_is_swallowed():
    """
    Step 7 must never be the reason a window will not close. Untested before:
    no test passed an executor whose `shutdown` raises, so narrowing the
    `except` to a type that never fires changed nothing.
    """
    class _Hostile:
        def shutdown(self, **kwargs):
            raise RuntimeError("executor is in a bad way")

    shutdown_service.stop_tool_executor(_Hostile())   # must not raise


def test_the_executor_it_stops_by_default_is_the_chat_tool_executor():
    """
    The wiring, asserted WITHOUT shutting the shared executor down. Otherwise
    the test above proves only that some ThreadPoolExecutor can be stopped.
    """
    from services import chat_service

    assert shutdown_service._default_tool_executor() is chat_service._TOOL_EXECUTOR


def test_stopping_the_executor_twice_is_not_an_error():
    """The shutdown path can run twice; a second call must not raise."""
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    shutdown_service.stop_tool_executor(executor)
    shutdown_service.stop_tool_executor(executor)


# ── review round 5: five ways this could still have lost work ───────────────


def test_ac_pf_is_distinguished_from_an_lp_solve_by_the_REAL_state():
    """
    `_ac_pf_thread` read `_state["ac_pf_thread"]` — a key NOTHING writes.
    `run_ac_pf` claims `_state["thread"]`, the same key as the LP worker, with
    the same `status="running"` and `condition=None`. So path (c) never fired,
    the AC-PF decision never ran, and the old test passed only because it
    monkeypatched the very function that was wrong.

    Both claims now set a `kind` discriminator and this drives the real state.
    """
    from services.pypsa_service import PyPSAService

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    PyPSAService._contexts["ac-pf-ctx"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="ac_pf")

        in_flight = shutdown_service.solves_in_flight()

        assert [s.path for s in in_flight] == ["ac_pf"], in_flight
        assert in_flight[0].interruptible is False
    finally:
        running.set(); worker.join(5)
        PyPSAService._contexts.pop("ac-pf-ctx", None)


def test_an_lp_solve_is_reported_as_interruptible():
    """
    The other side of the discriminator. `kind` must be set on the LP claim
    too — a stale "ac_pf" from a previous run on the same context would mark
    the next LP solve non-interruptible and it would never be aborted.
    """
    from services.pypsa_service import PyPSAService

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    PyPSAService._contexts["lp-ctx"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="lopf")

        in_flight = shutdown_service.solves_in_flight()

        assert [s.path for s in in_flight] == ["active"], in_flight
        assert in_flight[0].interruptible is True
    finally:
        running.set(); worker.join(5)
        PyPSAService._contexts.pop("lp-ctx", None)


def test_both_worker_claims_in_the_backend_set_a_kind():
    """
    The discriminator is only as good as its coverage. If a claim site forgets
    it, that solve inherits whatever `kind` the context last had — and the
    dangerous direction is a stale "ac_pf" making a real LP solve
    un-abortable.
    """
    import re

    source = pathlib.Path(__file__).resolve().parent.parent / "routers" / "simulation.py"
    text = source.read_text()

    claims = text.count("thread=t,")
    kinds = len(re.findall(r'kind="(?:lopf|ac_pf)"', text))

    assert kinds == claims, (
        f"{claims} worker claim(s) but {kinds} kind marker(s) — a claim site "
        "without one inherits the previous run's kind"
    )


def test_a_solve_on_a_NON_ACTIVE_context_is_seen():
    """
    The detector read only the process foreground, so a solve on any other
    resident context was invisible: no confirm, no abort, `abort_timed_out`
    False, and the resulting 409 reported as if the abort had succeeded.
    `RESIDENT_CAP` is 5 and the solve queue puts solves on these contexts.
    """
    from services.pypsa_service import PyPSAService

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    PyPSAService._contexts["background-solve"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="lopf")

        assert [s.path for s in shutdown_service.solves_in_flight()] == ["active"]
    finally:
        running.set(); worker.join(5)
        PyPSAService._contexts.pop("background-solve", None)


def test_detecting_solves_does_not_MANUFACTURE_an_active_context():
    """
    Reading the foreground solver state went through `_ensure_active()`, which
    self-heals AND ASSIGNS `_active`. Merely asking "is anything solving?"
    created an empty unbound context — which `resident_contexts()` then handed
    to `flush_all`, and which took `persist_user_ts=True` for itself, so the
    REAL open project was saved with False and lost its `_user_ts`.
    Constraint #8's exact failure mode, caused by asking the question.
    """
    from services.pypsa_service import PyPSAService

    PyPSAService._active = None
    shutdown_service.solves_in_flight()

    assert PyPSAService._active is None, "asking the question created a context"


def test_the_abort_endpoint_stays_reachable_while_the_gate_is_closed(client):
    """
    The gate 503'd every write with no exemption — including
    `POST /api/simulation/abort`, which is step 4's own mechanism. Step 1 would
    have refused step 4, the flush would 409, and nobody would be told.
    """
    shutdown_service.gate_mutations()

    assert client.post("/api/simulation/abort").status_code != 503


def test_a_confirm_that_raises_still_flushes_rather_than_quitting_clean():
    """
    `solves_in_flight()` and `confirm()` were the only unguarded calls, and
    `confirm` is the one modal this design runs on a worker thread — the plan's
    own Task 6 says such a modal must be marshalled to the GUI thread or it
    hangs on macOS. A raise skipped hide, abort, FLUSH, locks, executor and
    server, and `CloseHandler` still destroyed the window with `quit=True`.
    """
    calls: list[str] = []

    def confirm(in_flight):
        raise RuntimeError("the dialog blew up")

    report = shutdown_service.shutdown_sequence(
        gate=lambda: calls.append("gate"), un_gate=lambda: None,
        confirm=confirm, hide=lambda: calls.append("hide"),
        abort_and_wait=lambda: (calls.append("abort"), True)[1],
        flush=lambda *, safe: (calls.append("flush"), [])[1],
        release_locks=lambda: calls.append("locks"),
        stop_executor=lambda: calls.append("executor"),
        stop_server=lambda: (calls.append("server"), "clean")[1],
        in_flight=[shutdown_service.InFlightSolve("active", "x", True)],
    )

    assert "flush" in calls, f"the flush was skipped: {calls}"
    assert "abort" in calls, f"the abort was skipped: {calls}"
    assert report.errors, "a failed confirm must be reported"


def test_the_report_is_written_somewhere_a_human_can_read(tmp_path, monkeypatch):
    """
    `report.unflushed` reached nobody: no test asserted it, deleting its
    assignment left the suite green, and constraint #16 records that the
    backend has no FileHandler anywhere — so on a packaged windowless build the
    log goes nowhere. `main._persist_import_report` is the precedent.
    """
    import json

    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    report = shutdown_service.ShutdownReport(quit=True)
    report.unflushed = ["Belgium Grid: not saved (409 — a solve was still running)"]
    report.abort_timed_out = True

    path = shutdown_service.persist_report(report)

    assert path is not None and path.exists()
    written = json.loads(path.read_text())
    assert written["unflushed"] == report.unflushed
    assert written["abort_timed_out"] is True


def test_a_clean_quit_with_nothing_unsaved_writes_no_alarming_file(tmp_path, monkeypatch):
    """
    A file that appears after every quit trains the user to ignore the one that
    matters — the same reasoning as only confirming when a solve is in flight.
    """
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))

    assert shutdown_service.persist_report(shutdown_service.ShutdownReport(quit=True)) is None
    assert not list(tmp_path.glob("*shutdown*"))


def test_the_SEQUENCE_persists_a_report_when_something_could_not_be_saved(
    tmp_path, monkeypatch,
):
    """
    Testing `persist_report` in isolation proved only that the function works.
    Deleting its call from `shutdown_sequence` left all 50 tests green — the
    unit was covered and the WIRING was not, which is the same gap that hid the
    executor and context defects earlier in this task.
    """
    import json

    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))

    shutdown_service.shutdown_sequence(
        gate=lambda: None, un_gate=lambda: None,
        confirm=lambda in_flight: True, hide=lambda: None,
        abort_and_wait=lambda: False,
        flush=lambda *, safe: ["Belgium Grid: not saved (409 — a solve was still running)"],
        release_locks=lambda: None, stop_executor=lambda: None,
        stop_server=lambda: "clean",
        in_flight=[shutdown_service.InFlightSolve("active", "Belgium Grid", True)],
    )

    written = json.loads((tmp_path / "last-shutdown-report.json").read_text())
    assert written["unflushed"], written
    assert written["abort_timed_out"] is True


# ── review round 5 leftovers ────────────────────────────────────────────────


def test_a_running_queue_job_is_seen_deterministically():
    """
    The original path-(b) test called `solve_queue.enqueue(...)`, which starts
    a REAL dispatcher that tries to solve a project that does not exist. The
    job goes terminal in ~340 ms, so the test passed only by winning that race
    — and it only ever observed status `"queued"`, never `"running"`, which is
    the state constraint #6 is actually about. `reset_for_tests` does not join
    the dispatcher either, so a background `_run_job` could bleed into later
    tests.

    This builds the job table directly and drives the same `list_jobs()` the
    detector reads, covering BOTH live statuses with no dispatcher and no race.
    """
    import uuid

    from services.solve_queue import SolveJob, solve_queue

    solve_queue.reset_for_tests()
    try:
        for status in ("queued", "running"):
            jid = uuid.uuid4()
            with solve_queue._lock:
                solve_queue._jobs[jid] = SolveJob(
                    id=jid, project_id=f"project-{status}", enqueued_at=0.0,
                )
                solve_queue._jobs[jid].status = status
                solve_queue._order.append(jid)

        paths = [s.path for s in shutdown_service.solves_in_flight()]

        assert paths.count("queue") == 2, paths
    finally:
        solve_queue.reset_for_tests()


def test_a_finished_queue_job_is_not_reported_as_in_flight():
    """
    Otherwise every completed solve since boot prompts a confirm dialog, and a
    dialog that always appears is a dialog nobody reads.
    """
    import uuid

    from services.solve_queue import SolveJob, solve_queue

    solve_queue.reset_for_tests()
    try:
        for status in ("completed", "failed", "aborted"):
            jid = uuid.uuid4()
            with solve_queue._lock:
                solve_queue._jobs[jid] = SolveJob(id=jid, project_id="done", enqueued_at=0.0)
                solve_queue._jobs[jid].status = status
                solve_queue._order.append(jid)

        assert [s.path for s in shutdown_service.solves_in_flight()] == []
    finally:
        solve_queue.reset_for_tests()


def test_an_unsaved_context_is_named_readably_not_by_its_repr():
    """
    `flush_all` did `getattr(ctx, "name", None) or ... or repr(ctx)`, and
    `ProjectContext` has no `name` — it has `loaded_project`, `org_id`,
    `project_uuid`, `storage_dir`. Every test double defined `name`, so the only
    branch the tests exercised was the one that never runs in production.

    For an unbound context the fallback was `repr(ctx)`: a multi-line dataclass
    repr embedding the entire `pypsa.Network` repr. That string is what a user
    would have been shown as "which project was not saved".
    """
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.build_context()          # unbound: loaded_project is None

    def save(c, persist_user_ts):
        raise RuntimeError("disk full")

    problems = shutdown_service.flush_all(
        contexts=[ctx], active=None, save=save, flush_chat=lambda: None, safe=True,
    )

    assert len(problems) == 1
    assert "\n" not in problems[0], f"multi-line repr leaked into the report: {problems[0]!r}"
    assert len(problems[0]) < 200, f"{len(problems[0])} chars: {problems[0][:120]}"
    assert "PyPSA" not in problems[0], problems[0]


def test_the_sequence_has_an_overall_deadline():
    """
    Nothing bounded the whole sequence. `CloseHandler.on_closing` vetoes for as
    long as the worker lives, and on macOS `applicationShouldTerminate` routes
    through the same event — so Cmd-Q and Dock->Quit are vetoed too. Step 3 has
    already hidden the window, so the user sees a vanished app, a live process,
    and Force Quit as the only exit: the outcome this workstream exists to
    prevent.

    `_save_context` takes `ctx.mutation_lock` with no timeout, so an unbounded
    flush is reachable without anything being wrong.
    """
    release = threading.Event()
    destroyed: list[int] = []

    handler = shutdown_service.CloseHandler(
        run=lambda: (release.wait(60), shutdown_service.ShutdownReport(quit=True))[1],
        destroy=lambda: destroyed.append(1),
        deadline=1.0,
    )
    try:
        assert handler.on_closing() is False

        assert handler.wait_for_completion(20), "the deadline never fired"
        assert destroyed == [1], "the window was never released"
        assert handler.report is not None
        assert any("did not finish" in e or "abandoned" in e
                   for e in handler.report.errors), handler.report.errors
    finally:
        release.set()


# ── where the flush writes (found by acceptance step 5) ─────────────────────


class _Ctx:
    """Minimal stand-in for a ProjectContext: only what the saver reads."""

    def __init__(self, loaded_project, storage_dir):
        self.loaded_project = loaded_project
        self.storage_dir = storage_dir


def test_the_flush_writes_where_the_context_was_LOADED_FROM():
    """
    The defect acceptance step 5 found, and the reason this helper exists.

    `gui.py` used to call `_save_context(ctx, name)` with no `storage_dir`, so
    the destination was re-derived from the DISPLAY NAME via
    `_safe_project_dir`, which resolves against
    `get_settings().flat_projects_root` — `<app-data>/flat_projects`.
    `load_project` reads `project_registry.project_dir(project)`, which
    resolves against `get_settings().projects_root` — `~/Documents/PyPSA GUI/
    Projects`. Those are different directories in every install, deliberately
    (see `settings.py`), so the quit-flush persisted somewhere the app never
    reads.

    Measured before the fix: one run wrote 4 buses to `flat_projects` and then
    loaded 3 from `Documents`, while the report said `unflushed: []` and
    `errors: []` — a clean-looking quit over dropped work, which is precisely
    the hazard constraint #4 names.

    `PyPSAService._evict_if_over_cap` already passes `storage_dir` for the same
    reason; the desktop flush was the last caller re-deriving from the name.
    """
    calls = []

    def record(ctx, name, **kwargs):
        calls.append({"name": name, **kwargs})

    save = shutdown_service.make_saver(record)
    save(_Ctx("Demo", "/somewhere/orgs/abc/def"), persist_user_ts=True)

    assert calls, "the saver never called through"
    assert calls[0]["storage_dir"] == pathlib.Path("/somewhere/orgs/abc/def"), (
        "the flush re-derived the path from the display name — it will write "
        "to flat_projects while load_project reads projects_root"
    )


def test_a_context_with_no_storage_dir_still_saves():
    """
    `storage_dir=None` is `_save_context`'s documented "derive it yourself"
    signal. A context that was never bound to a directory must still be
    written rather than skipped — skipping is the data loss.
    """
    calls = []
    save = shutdown_service.make_saver(lambda ctx, name, **kw: calls.append(kw))

    save(_Ctx("Demo", None), persist_user_ts=False)

    assert calls and calls[0]["storage_dir"] is None


def test_a_never_saved_draft_is_skipped_not_invented():
    """A context with no project name has nowhere to go. Declared residual risk."""
    calls = []
    save = shutdown_service.make_saver(lambda ctx, name, **kw: calls.append(kw))

    save(_Ctx(None, "/somewhere"), persist_user_ts=True)

    assert calls == []


def test_the_GUI_actually_uses_the_saver(monkeypatch):
    """
    The tests above pass whether or not `gui.py` calls `make_saver`. This is
    the one that fails if it stops — the realistic regression, since the old
    inline form looked correct and silently wrote to the wrong root.

    Drives the real `_wire_close_handler` against a fake window, so the
    assertion is about this module's wiring rather than about a helper nobody
    may be calling.
    """
    # Deliberately NOT `pytest.importorskip`. A skip is how the two desktop
    # download tests silently passed for a whole review cycle; this is the only
    # test standing between the codebase and a silent-wrong-directory flush, so
    # a missing pywebview must FAIL here. See `test_desktop_downloads._webview`.
    try:
        import webview  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment defect
        raise AssertionError(
            "pywebview is missing from this environment, so the one test "
            f"guarding gui.py's wiring cannot run: {exc}"
        ) from exc

    from desktop import gui

    built = []
    real_make_saver = shutdown_service.make_saver

    def spy(save_context):
        built.append(save_context)
        saver = real_make_saver(save_context)
        built.append(saver)
        return saver

    monkeypatch.setattr(shutdown_service, "make_saver", spy)

    # Capture what actually REACHES the flush, not merely what was constructed.
    # An earlier version asserted only that `make_saver` had been called, and a
    # partial revert — keeping the wire-time call while shadowing `save` inside
    # `flush` with the old inline form — was measured to SURVIVE it. That is
    # the realistic regression, and it restores the original data-loss defect.
    seen: dict = {}

    def fake_flush_all(**kw):
        seen["save"] = kw["save"]
        return []          # `flush_all` returns the list of problem strings

    def fake_shutdown_sequence(**kw):
        seen["flush"] = kw["flush"]
        return shutdown_service.ShutdownReport(quit=True)

    monkeypatch.setattr(shutdown_service, "flush_all", fake_flush_all)
    monkeypatch.setattr(shutdown_service, "shutdown_sequence", fake_shutdown_sequence)

    subscribed = []

    class _Events:
        def __init__(self):
            self.closing = self

        def __iadd__(self, handler):
            # RECORDED, not discarded. Dropping it made `window.events.closing
            # += handler.on_closing` deletable with every test still green —
            # and with that line gone the app closes with no flush, no abort
            # and no lock release, silently.
            subscribed.append(handler)
            return self

    destroyed = []

    class _Window:
        events = _Events()

        def hide(self):
            pass

        def destroy(self):
            destroyed.append(True)

        def create_confirmation_dialog(self, *a):
            return True

    state = {"main_window": _Window(), "server": None}
    gui._wire_close_handler(state)

    from routers.projects import _save_context

    assert built[0] is _save_context, (
        "gui.py no longer builds its saver through shutdown.make_saver — the "
        "flush will re-derive the path from the display name and write to "
        "flat_projects_root, which load_project never reads"
    )
    the_saver = built[1]

    handler = state["close_handler"]
    assert subscribed == [handler.on_closing], (
        "gui.py no longer subscribes the close handler to `closing`; the "
        "window then closes with no flush, no abort and no lock release"
    )

    # Drive the real wiring: run -> shutdown_sequence(flush=...) -> flush_all.
    handler._run()
    seen["flush"](safe=False)

    assert seen["save"] is the_saver, (
        "the saver reaching flush_all is not the one make_saver returned — a "
        "shadowed inline saver drops storage_dir and writes to "
        "flat_projects_root, which is the defect 11806022 fixed"
    )


# ── the report tells the truth about drafts and refusals ────────────────────


class _Net:
    def __init__(self, empty: bool):
        import pandas as pd

        self.buses = pd.DataFrame() if empty else pd.DataFrame({"v_nom": [380.0]})


class _Draft:
    """A context that was never saved: it has content and no binding."""

    def __init__(self, empty=False):
        self.network = _Net(empty)
        self.loaded_project = None
        self.project_uuid = None
        self.storage_dir = None


def test_a_never_saved_draft_with_work_in_it_is_REPORTED_not_silently_dropped():
    """
    Import a `.nc` with no project open, edit for an hour, quit. No solve is in
    flight so there is no confirm dialog; `make_saver` returns on its first
    line because there is no `loaded_project`; nothing raises. Before this the
    loop recorded nothing, `unflushed` stayed empty, and `persist_report` wrote
    no file — a quit that reports clean over work that is gone.
    """
    draft = _Draft()

    problems = shutdown_service.flush_all(
        contexts=[draft], active=draft,
        save=lambda ctx, persist: None, flush_chat=lambda: None, safe=True,
    )

    assert len(problems) == 1, problems
    assert "an unsaved draft" in problems[0]
    assert "never been saved" in problems[0]


def test_a_fresh_empty_scratch_context_is_not_reported():
    """
    Every launch creates one. Reporting it would put "NOT saved" in front of
    the user on every clean quit, which trains them to ignore the report.
    """
    problems = shutdown_service.flush_all(
        contexts=[_Draft(empty=True)], active=None,
        save=lambda ctx, persist: None, flush_chat=lambda: None, safe=True,
    )

    assert problems == []


def test_a_409_reports_the_reason_it_was_given_not_an_assumed_one():
    """
    `_save_context` raises 409 for at least two reasons reachable from the
    flush: a solver in flight, and the guard refusing to overwrite a saved
    project with an empty network. The message named the first unconditionally
    — so deleting every bus and quitting produced "a solve was still running"
    when none was, in the one artifact whose job is to be truthful.
    """
    from fastapi import HTTPException

    ctx = _Draft()
    ctx.loaded_project = "Baseline"

    def refuse(_ctx, _persist):
        raise HTTPException(409, "refusing to overwrite a 40-bus project with an empty network")

    problems = shutdown_service.flush_all(
        contexts=[ctx], active=ctx,
        save=refuse, flush_chat=lambda: None, safe=True,
    )

    assert "refusing to overwrite" in problems[0], problems
    assert "a solve was still running" not in problems[0]
