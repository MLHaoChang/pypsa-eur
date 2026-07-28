"""
Quitting without losing the user's work (workstream H, Task 4).

The hazard here is not the obvious one. A hard quit mid-solve loses the SOLVE
and does not corrupt the project: `atomic_io` leaves the previous file intact
on a killed export, and `_save_context` refuses with 409 while a solver is in
flight for that context. The real hazard is the inverse — an incomplete abort
makes the flush 409, the unsaved edits are dropped, and the app reports a clean
quit. So: abort BEFORE flushing, and REPORT a flush that could not happen.

The eight steps, and the order is the design:

    1. GATE           mutations -> 503. Reversible, touches no GUI.
    2. CONFIRM        only when a solve is in flight. Cancel un-gates.
    3. HIDE           only now, after the user chose Quit.
    4. ABORT + WAIT   bounded per path.
    5. FLUSH          every resident context, told whether the abort finished.
    6. RELEASE LOCKS
    7. EXECUTOR
    8. SERVER

Gate before confirm, hide after: gating is reversible and invisible, hiding is
neither, and a confirm dialog raised against a just-hidden window is invisible
on macOS. Hiding does NOT quiesce anything on its own — the frontend has 20
`refetchInterval` sites, a 5-minute autosave and a 45 s lock heartbeat, none of
which a hidden window stops.

Nothing here imports `desktop`: steps 6 and 8 live outside `services/`, so they
arrive as injected callables. That is also what makes the ORDER assertable.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# How long to wait for each solve path to notice its stop event. AC PF is not
# in this budget because nothing reads its event — see `InFlightSolve`.
ABORT_TIMEOUT = 30.0


@dataclass(frozen=True)
class InFlightSolve:
    """One running solve, and whether anything can actually stop it."""

    path: str            # "active" | "queue" | "ac_pf"
    label: str
    interruptible: bool


@dataclass
class ShutdownReport:
    """
    What actually happened. Every field exists because something can go wrong
    silently, and a clean-looking quit over dropped work is the failure mode.
    """

    quit: bool = False
    already_running: bool = False
    abort_timed_out: bool = False
    unflushed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    server_stage: str | None = None


# ── the mutation gate (step 1) ──────────────────────────────────────────────
#
# A plain flag rather than anything cleverer: it is read on every mutating
# request and flipped twice in the life of the process. `threading.Event` would
# read the same and imply a wait nobody performs.
_gated = threading.Event()

# 503, deliberately, and NOT 409. A 409 from this backend already means "a
# solver is in flight, try again in a moment" and the frontend treats it as
# retryable. A shutting-down backend must not invite a retry, and the SPA needs
# to tell the two apart without parsing prose — hence the typed code.
SHUTTING_DOWN_CODE = "shutting_down"


def gate_mutations() -> None:
    """Step 1. Reversible, touches no GUI, and NOT the same as hiding."""
    _gated.set()


def un_gate_mutations() -> None:
    """The Cancel path, and the only way back."""
    _gated.clear()


def mutations_gated() -> bool:
    return _gated.is_set()


# The latch. Cleared on the Cancel path so a user who cancels once can still
# quit later; held for the duration otherwise, because `webview` will deliver a
# second close event while the first sequence is mid-flush and running the
# eight steps twice would abort twice and flush twice.
_running = threading.Lock()


def _active_solver_thread():
    """The foreground solver worker, or None. Seam for tests."""
    from routers import simulation

    with __import__("services.pypsa_service", fromlist=["PyPSAService"]) \
            .PyPSAService.get_solver_state_lock():
        return simulation._state.get("thread")


def _ac_pf_thread():
    """
    The AC PF worker, or None.

    Separate from `_active_solver_thread` because it is a different KIND of
    thing: `run_ac_pf` creates a stop event and `ac_pf_service` contains zero
    occurrences of `stop_event`, so this one cannot be interrupted at all.
    """
    from routers import simulation

    return simulation._state.get("ac_pf_thread")


def solves_in_flight() -> list[InFlightSolve]:
    """
    Every running solve, across all three paths.

    Path (b) consults the QUEUE'S OWN JOB TABLE, not the context registry.
    `solve_queue._run_job` builds its context with `PyPSAService.build_context()`
    — "off to the side, not activated" — and never calls `register`, so a
    running queue job's context is in neither `_contexts` nor `_active`.
    Iterating the registry finds nothing and reports a clean quit over a live
    solve.
    """
    found: list[InFlightSolve] = []

    thread = _active_solver_thread()
    if thread is not None and thread.is_alive():
        found.append(InFlightSolve("active", "the open project", True))

    try:
        from services.solve_queue import solve_queue

        for job in solve_queue.list_jobs():
            if job.get("status") in ("queued", "running"):
                found.append(InFlightSolve(
                    "queue", job.get("project_name") or f"job {job.get('id')}", True,
                ))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not inspect the solve queue: %s", exc)

    ac_pf = _ac_pf_thread()
    if ac_pf is not None and ac_pf.is_alive():
        # interruptible=False, and it is not a detail. The sequence waits, then
        # warns and skips that context's flush, and REPORTS it — abandoning the
        # wait silently would report a clean quit over dropped edits.
        found.append(InFlightSolve("ac_pf", "AC power flow", False))

    return found


def flush_all(
    *,
    contexts: list[Any],
    active: Any,
    save: Callable[[Any, bool], None],
    flush_chat: Callable[[], None],
    safe: bool,
) -> list[str]:
    """
    Persist every resident context. Returns the ones that could NOT be saved.

    `persist_user_ts` is `ctx is active`, and the asymmetry is required:
    `_serialize_user_ts` reads a process-global store belonging to the
    foreground. True for everything stamps the foreground's series onto every
    project; False for everything loses the active project's.

    `safe=False` means the abort did not finish, so a 409 is expected rather
    than surprising — it is still reported, because the point is to tell the
    user what was not written.
    """
    from fastapi import HTTPException

    problems: list[str] = []

    for ctx in contexts:
        name = getattr(ctx, "name", None) or getattr(ctx, "loaded_project", None) or repr(ctx)
        try:
            save(ctx, ctx is active)
        except HTTPException as exc:
            # Caught SPECIFICALLY. A 409 means "a solver is still in flight for
            # this context, so your edits were not written" — a different thing
            # from a disk error, and the one the user needs to hear about.
            if exc.status_code == 409:
                problems.append(
                    f"{name}: not saved (409 — a solve was still running"
                    f"{'' if safe else ', and the abort did not finish'})"
                )
            else:
                problems.append(f"{name}: not saved ({exc.status_code} {exc.detail})")
            logger.warning("shutdown flush refused for %s: %s", name, exc.detail)
        except Exception as exc:
            problems.append(f"{name}: not saved ({exc})")
            logger.exception("shutdown flush failed for %s", name)

    try:
        # A documented Phase-0 no-op — `append_turn` already writes
        # synchronously. Called for a stable call site, NOT because omitting it
        # loses data today.
        flush_chat()
    except Exception as exc:
        problems.append(f"chat transcript: {exc}")
        logger.exception("chat flush failed")

    return problems


def abort_and_wait(
    *,
    in_flight: list[InFlightSolve],
    abort_active: Callable[[], None],
    abort_queue: Callable[[], None],
    wait: Callable[[float], bool],
    timeout: float = ABORT_TIMEOUT,
) -> bool:
    """
    Signal every solve that CAN be stopped, then wait a bounded time for all of
    them. Returns whether everything actually finished.

    Three paths, and they are not symmetric:

      (a) active   `/api/simulation/abort` sets the foreground context's event
      (b) queue    each job carries its own; `solve_queue.abort(id)` reaches it
      (c) ac_pf    creates an event that NOTHING READS. There is nothing to
                   signal, so this does not pretend to. It is still waited for
                   and still reported.

    The bound is not optional. HiGHS and Gurobi do not yield until the next
    iteration boundary, so `status` flips to "aborted" instantly while the
    worker stays alive for seconds — an unbounded wait is a window that never
    closes. Returning False is how the caller learns the flush may 409, which
    is the difference between reporting a clean quit and reporting the truth.
    """
    if not in_flight:
        return True

    ok = True
    paths = {s.path for s in in_flight if s.interruptible}

    for path, signal in (("active", abort_active), ("queue", abort_queue)):
        if path not in paths:
            continue
        try:
            signal()
        except Exception as exc:
            # One path failing must not leave the others un-signalled — that
            # strands a solve the sequence is about to flush around.
            ok = False
            logger.exception("aborting the %s solve failed", path)

    try:
        finished = bool(wait(timeout))
    except Exception:
        logger.exception("waiting for solves to finish failed")
        finished = False

    return ok and finished


def shutdown_sequence(
    *,
    gate: Callable[[], None] = gate_mutations,
    un_gate: Callable[[], None] = un_gate_mutations,
    confirm: Callable[[list[InFlightSolve]], bool],
    hide: Callable[[], None],
    abort_and_wait: Callable[[], bool],
    flush: Callable[..., list[str]],
    release_locks: Callable[[], None],
    stop_executor: Callable[[], None],
    stop_server: Callable[[], Any],
    in_flight: list[InFlightSolve] | None = None,
) -> ShutdownReport:
    """
    The eight steps, in order, with every collaborator injected.

    Steps 6 and 8 live outside `services/` — `release_all_for_user` needs a DB
    session, `stop_server` is the launcher's — so they have to be arguments.
    Making the rest arguments too is what lets a recording double assert the
    ORDER, which is the property that actually matters here.
    """
    report = ShutdownReport()

    if not _running.acquire(blocking=False):
        # A second close event arrived mid-sequence. `webview` delivers these
        # freely; running the steps twice would abort twice and flush twice.
        report.already_running = True
        return report

    try:
        # 1. GATE — reversible, no GUI. NOT hide: a hidden window keeps every
        #    refetch interval, the autosave and the lock heartbeat running.
        _step(report, "gate", gate)

        # 2. CONFIRM — D12, and only when there is something to lose. Asking on
        #    every quit trains the user to dismiss it.
        pending = solves_in_flight() if in_flight is None else in_flight
        if pending and not confirm(pending):
            # The veto. Un-gate and CLEAR the latch: the window was never
            # hidden, so the app must go back to fully working, and a user who
            # cancels once must still be able to quit later.
            _step(report, "un_gate", un_gate)
            report.quit = False
            return report

        # 3. HIDE — only now.
        _step(report, "hide", hide)

        # 4. ABORT + WAIT — before the flush, per constraint #4.
        aborted = True
        try:
            aborted = bool(abort_and_wait())
        except Exception as exc:
            report.errors.append(f"abort: {exc}")
            logger.exception("shutdown abort failed")
            aborted = False
        report.abort_timed_out = not aborted

        # 5. FLUSH — told whether the abort finished, so a 409 can be reported
        #    as "your edits were not written" rather than as a generic error.
        try:
            report.unflushed = list(flush(safe=aborted) or [])
        except Exception as exc:
            report.errors.append(f"flush: {exc}")
            logger.exception("shutdown flush failed")

        # 6-8. Each guarded: a window that will not close is worse than the
        #      failure it is reacting to.
        _step(report, "release_locks", release_locks)
        _step(report, "stop_executor", stop_executor)
        try:
            report.server_stage = stop_server()
        except Exception as exc:
            report.errors.append(f"stop_server: {exc}")
            logger.exception("stopping the server failed")

        report.quit = True
        return report
    finally:
        _running.release()


def _step(report: ShutdownReport, name: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:
        report.errors.append(f"{name}: {exc}")
        logger.exception("shutdown step %s failed", name)


# ── the window's close handler ──────────────────────────────────────────────


class CloseHandler:
    """
    Tri-state answer to "may the window close?".

        not started  -> start the worker, VETO
        in progress  -> VETO, and do NOT start a second worker
        complete     -> ALLOW

    Webview-free on purpose: `gui.py` wires this to `window.events.closing` and
    passes `window.destroy`, but nothing here imports a toolkit, so the whole
    state machine is covered by the backend suite on a headless box.

    **The handler must return promptly.** A UI thread blocked past ~5 s is
    ghosted as *Not Responding* on Windows, and the sequence's own budget is
    far past that — `abort_and_wait` alone allows 30 s. So the eight steps run
    on a worker and this only ever answers a question.

    **The state flips to complete BEFORE `destroy()`.** `window.destroy()`
    re-fires `closing`, so flipping afterwards would have the re-entrant call
    still see "in progress", veto its own destroy, and leave a window that can
    never close — with the backend already stopped behind it. That is a total
    deadlock and the only way out is Force Quit, which is the outcome this
    workstream exists to prevent.
    """

    def __init__(
        self,
        *,
        run: Callable[[], ShutdownReport],
        destroy: Callable[[], None],
    ) -> None:
        self._run = run
        self._destroy = destroy
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._complete = threading.Event()
        self._may_close = False
        self.report: ShutdownReport | None = None

    def on_closing(self) -> bool:
        """`True` lets the window close. Wire to `window.events.closing`."""
        with self._lock:
            if self._may_close:
                return True
            if self._worker is not None and self._worker.is_alive():
                # A second close while the first is mid-flush. `webview`
                # delivers these freely; starting another sequence would abort
                # twice and flush twice.
                return False

            self._complete.clear()
            self._worker = threading.Thread(
                target=self._work, name="pypsa-gui-shutdown", daemon=True,
            )
            self._worker.start()
            return False

    def _work(self) -> None:
        try:
            report = self._run()
        except Exception as exc:
            # A window that cannot be closed is worse than whatever failed.
            report = ShutdownReport(quit=True)
            report.errors.append(f"shutdown: {exc}")
            logger.exception("the shutdown sequence raised")

        self.report = report
        try:
            if report.quit:
                # BEFORE destroy(), which re-enters `on_closing`.
                with self._lock:
                    self._may_close = True
                self._destroy()
            else:
                # Cancelled. Nothing to reset: `on_closing` gates on
                # `is_alive()`, so the finished worker is replaced by the next
                # close on its own, and `shutdown_sequence` has already
                # un-gated and cleared its own latch. Clearing `_worker` here
                # as well was redundant — a mutation that deleted it left the
                # whole suite green, which is what redundant means.
                with self._lock:
                    self._may_close = False
        finally:
            self._complete.set()

    def wait_for_completion(self, timeout: float) -> bool:
        """For tests and for a caller that wants to join the worker."""
        return self._complete.wait(timeout)
