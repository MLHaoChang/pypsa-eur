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

# Hard bound on the WHOLE sequence, enforced by `CloseHandler`. Generous enough
# for a 30 s abort plus a large flush, short enough that a user is not left
# staring at a vanished window. See `CloseHandler._run_with_deadline`.
SHUTDOWN_DEADLINE = 90.0


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


def _context_solves() -> list[tuple[Any, Any, str | None]]:
    """
    `(context, thread, kind)` for every RESIDENT context with a live worker.

    Walks the contexts directly instead of reading `routers.simulation._state`.
    That proxy resolves through `PyPSAService._ensure_active()`, which
    self-heals AND ASSIGNS `_active` — so merely asking "is anything solving?"
    CREATED an empty unbound context, which `resident_contexts()` then handed
    to `flush_all`, and which took `persist_user_ts=True` for itself. The real
    open project was then saved with `False` and lost its `_user_ts`:
    constraint #8's exact failure mode, caused by asking the question.

    It also covers EVERY resident context rather than only the foreground. A
    solve on a background context — where the queue puts them, and
    `RESIDENT_CAP` allows five — was previously invisible, so there was no
    confirm, no abort, and the resulting 409 was reported as a clean abort.
    """
    found: list[tuple[Any, Any, str | None]] = []
    for ctx in resident_contexts():
        try:
            with ctx.solver_state_lock:
                thread = ctx.solver_state.get("thread")
                kind = ctx.solver_state.get("kind")
        except Exception:  # pragma: no cover - defensive
            logger.exception("could not read solver state for a context")
            continue
        if kind == "queue":
            # A queue job's context is REGISTERED now (one ProjectContext per
            # project, always), so this walk and the job table in
            # `solves_in_flight` would each report the same solve. The job
            # table stays the single source for the queue path because it is
            # the only one that also sees a job that is still `queued`.
            #
            # Skipping here is also what keeps a background queue solve out of
            # the `"active"` bucket below, whose label ("an open project") and
            # remedy are written for a foreground `/run`. A queue solve is
            # reported from the job table instead, under the `"queue"` kind,
            # and `desktop/gui.py:_abort_everything` stops it with
            # `solve_queue.abort`. NOTE it is no longer true that
            # `/api/simulation/abort` cannot reach a queue job: now that the
            # session resolves to the queue-owned context, `_state["stop_event"]`
            # IS that job's stop event, so the endpoint does stop it. The
            # double-counting above is the reason for this `continue`; the
            # bucket is only about which name and channel the quit dialog
            # reports.
            continue
        if thread is not None and thread.is_alive():
            found.append((ctx, thread, kind))
    return found


def solves_in_flight() -> list[InFlightSolve]:
    """
    Every running solve, across all three paths.

    Path (b) consults the QUEUE'S OWN JOB TABLE, not the context registry.
    Every queue job's context IS registered now — `solve_queue._run_job` and
    `PyPSAService.build_context()` both go through the shared
    `hydrate_or_adopt` lock, one `ProjectContext` per project, always — and
    `_context_solves()` deliberately SKIPS any context whose `kind` is
    `"queue"` so this walk and the job table never double-report the same
    solve.

    The job table stays the source for path (b) regardless: it is the only
    one that also sees a job that is still `queued` (not yet dispatched to a
    context at all), so a context-only view would still be incomplete even
    though every RUNNING queue solve now has a resident context.
    """
    found: list[InFlightSolve] = []

    for ctx, _thread, kind in _context_solves():
        label = getattr(ctx, "loaded_project", None) or "an open project"
        if kind == "ac_pf":
            # Path (c), and the discriminator is load-bearing. `run_ac_pf`
            # claims the SAME `_state["thread"]` key as the LP worker with the
            # same status and condition, so without `kind` the two are
            # indistinguishable — an earlier version of this function read a
            # key NOTHING ever wrote, so path (c) never fired once and the
            # "decision taken rather than deferred" never executed.
            found.append(InFlightSolve("ac_pf", f"AC power flow ({label})", False))
        else:
            found.append(InFlightSolve("active", label, True))

    try:
        from services.solve_queue import solve_queue

        for job in solve_queue.list_jobs():
            if job.get("status") in ("queued", "running"):
                found.append(InFlightSolve(
                    "queue", job.get("project_name") or f"job {job.get('id')}", True,
                ))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not inspect the solve queue: %s", exc)

    return found


def _context_label(ctx: Any) -> str:
    """
    A short, human-readable name for a context.

    `ProjectContext` has `loaded_project`, `org_id`, `project_uuid` and
    `storage_dir` — it has NO `name`. An earlier version tried `name` first and
    fell back to `repr(ctx)`, which is a multi-line dataclass repr embedding the
    entire `pypsa.Network` repr. Every test double defined `name`, so the only
    branch the tests exercised was the one that never runs, and the string a
    user would have been shown as "which project was not saved" was a wall of
    network internals.
    """
    label = getattr(ctx, "loaded_project", None)
    if label:
        return str(label)

    uuid = getattr(ctx, "project_uuid", None)
    if uuid:
        return f"project {uuid}"

    # A never-saved draft: it genuinely has no name. Say so plainly rather than
    # inventing one — the user needs to recognise what is at stake.
    return "an unsaved draft"


def _holds_work(ctx: Any) -> bool:
    """
    Does this context contain anything a user would miss?

    Buses as the proxy, matching `_save_evicted_ctx`: every other PyPSA
    component attaches to one, and an empty-bus network is the fresh scratch
    context every launch creates. Defensive because this runs during a quit —
    a context whose network cannot be inspected is reported rather than
    silently dropped.
    """
    network = getattr(ctx, "network", None)
    if network is None:
        return False
    try:
        return not network.buses.empty
    except Exception:  # noqa: BLE001 - never let reporting break the shutdown
        logger.exception("could not inspect a context during the flush")
        return True


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
        name = _context_label(ctx)
        try:
            save(ctx, ctx is active)
        except HTTPException as exc:
            # Caught SPECIFICALLY. A 409 means the write was REFUSED rather
            # than failed, which the user needs to hear about — but the cause
            # is reported, never assumed. `_save_context` raises 409 for at
            # least two reasons reachable from here: a solver still in flight,
            # and the guard that refuses to overwrite a saved project with an
            # empty network. Naming the first unconditionally told the user "a
            # solve was still running" when none was, in the single artifact
            # whose whole job is to be truthful about dropped work.
            if exc.status_code == 409:
                detail = str(getattr(exc, "detail", "") or "refused")
                problems.append(
                    f"{name}: not saved (409 — {detail}"
                    f"{'' if safe else '; the abort did not finish'})"
                )
            else:
                problems.append(f"{name}: not saved ({exc.status_code} {exc.detail})")
            logger.warning("shutdown flush refused for %s: %s", name, exc.detail)
        except Exception as exc:
            problems.append(f"{name}: not saved ({exc})")
            logger.exception("shutdown flush failed for %s", name)
        else:
            # A context with no `loaded_project` is SKIPPED by every saver —
            # `make_saver` returns on the first line. Nothing raised, so the
            # loop recorded nothing, `unflushed` stayed empty, and
            # `persist_report` therefore wrote no report file at all: a quit
            # that looks clean over work that is gone.
            #
            # `_context_label`'s "an unsaved draft" branch was written for
            # exactly this and was unreachable — computed on every iteration,
            # consumed only inside the except arms.
            #
            # Gated on the network actually holding something, or every clean
            # quit from a fresh launch would report a draft that does not
            # exist. Same proxy `_save_evicted_ctx` uses.
            if not getattr(ctx, "loaded_project", None) and _holds_work(ctx):
                problems.append(
                    f"{name}: NOT saved — it has never been saved to a project, "
                    f"so there was nowhere to write it"
                )

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


def resident_contexts() -> list[Any]:
    """
    Every context holding possibly-unsaved work, the active one included, with
    no duplicates.

    `_active` is a BOOTSTRAP slot rather than a member of `_contexts`, and the
    two overlap unpredictably: `adopt_process_foreground()` hands `_active` out
    and CLEARS it, so a session-less caller — the desktop configuration
    exactly — ends up with a context in no registry at all. Iterating only
    `_contexts` therefore misses the OPEN project, which is the one whose
    unsaved edits matter most. Concatenating blindly saves it twice, which is
    wasted 100 MB-scale I/O during a quit the user is watching.

    `RESIDENT_CAP` is 5 by default, so this is a handful of projects.
    """
    from services.pypsa_service import PyPSAService

    found: list[Any] = []
    for ctx in list(PyPSAService._contexts.values()):
        if ctx is not None and not any(ctx is seen for seen in found):
            found.append(ctx)

    active = PyPSAService._active
    if active is not None and not any(active is seen for seen in found):
        found.append(active)
    return found


def _default_tool_executor():
    from services.chat_service import _TOOL_EXECUTOR

    return _TOOL_EXECUTOR


def make_saver(save_context: Callable[..., Any]) -> Callable[..., None]:
    """
    Build the per-context save the flush uses.

    Lives here rather than inline in `gui.py` for the usual reason — `gui.py`
    is wiring and this is a decision — but the decision itself was found by
    acceptance step 5, not by design:

    **Pass the directory the context was LOADED FROM.** Omitting `storage_dir`
    makes `_save_context` re-derive the destination from the display name via
    `_safe_project_dir`, which resolves against `flat_projects_root`
    (`<app-data>/flat_projects`). `load_project` reads
    `project_registry.project_dir(project)`, which resolves against
    `projects_root` (`~/Documents/PyPSA GUI/Projects`). `settings.py` makes
    those different on purpose, so a flush that re-derives writes somewhere the
    app will never read — and reports success.

    Measured before this existed: a quit flushed 4 buses into `flat_projects`,
    the next launch loaded 3 from `Documents`, and the report carried
    `unflushed: []` with no errors. That is constraint #4's hazard exactly — a
    clean-looking quit over dropped work — reached by a route the workstream's
    own tests could not see, because they injected the saver.

    `PyPSAService._evict_if_over_cap` already passes `storage_dir` for the same
    reason, with the same warning in its comment. The desktop flush was the
    last caller still deriving a path from a display name.
    """
    import pathlib

    def save(ctx, persist_user_ts: bool) -> None:
        name = getattr(ctx, "loaded_project", None)
        if not name:
            # A never-saved draft has nowhere to go. Declared residual risk;
            # skipped rather than invented a filename for.
            return
        raw = getattr(ctx, "storage_dir", None)
        save_context(
            ctx, name,
            persist_user_ts=persist_user_ts,
            # None is `_save_context`'s "derive it yourself" signal, and is
            # correct for a context that was never bound to a directory.
            storage_dir=pathlib.Path(raw) if raw else None,
        )

    return save


def stop_tool_executor(executor: Any = None) -> None:
    """
    Step 7. `chat_service` creates a module-level `ThreadPoolExecutor` that is
    never shut down, and CPython's atexit joiner blocks interpreter exit on it.

    `cancel_futures=True` cancels only PENDING work — a tool call already
    running still has to finish — so this is necessary and NOT sufficient. The
    bounded server exit and, past that, the hard exit are what make quitting
    actually terminal.

    `wait=False`: this runs inside a shutdown the user is waiting on, and a
    long-running tool call must not extend it.
    """
    try:
        (executor if executor is not None else _default_tool_executor()).shutdown(
            wait=False, cancel_futures=True,
        )
    except Exception:
        # Idempotent by requirement — the shutdown path can run twice — and a
        # failure here must never be the reason a window will not close.
        logger.exception("stopping the chat tool executor failed")


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
        # Both of these are guarded, and the fallback is deliberately the
        # PESSIMISTIC one. `confirm` is the only modal this design runs on a
        # worker thread, and the plan's own Task 6 notes that a modal off the
        # GUI thread is a classic macOS hang; `solves_in_flight` touches the
        # context registry under locks. If either fails, the safe answer to "is
        # there work to lose?" is YES — proceed to the abort and the flush.
        # Unguarded, a raise here propagated out of the sequence, `CloseHandler`
        # caught it, reported `quit=True`, and destroyed the window having
        # skipped hide, abort, FLUSH, locks, executor and server.
        try:
            pending = solves_in_flight() if in_flight is None else in_flight
        except Exception as exc:
            report.errors.append(f"detecting solves: {exc}")
            logger.exception("could not determine what is still solving")
            pending = []

        try:
            vetoed = bool(pending) and not confirm(pending)
        except Exception as exc:
            report.errors.append(f"confirm: {exc}")
            logger.exception("the quit confirmation failed")
            vetoed = False      # could not ask -> do not quit silently, FLUSH

        if vetoed:
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
        persist_report(report)
        _running.release()


def persist_report(report: ShutdownReport):
    """
    Write the report where a user can find it — but ONLY when it says
    something.

    `report.unflushed` is this module's entire reason to exist, and it reached
    nobody: constraint #16 records that the backend has no `basicConfig` and no
    `FileHandler` anywhere, so on a packaged windowless build `logger` output
    goes nowhere at all. A sequence that dropped edits could write a truthful
    sentence into a field and exit silently.

    `main._persist_import_report` is the precedent, two files away, and its
    docstring says it plainly: "Logging alone is not a channel."

    Returns the path written, or None when there was nothing worth saying — a
    file that appears after every quit trains the user to ignore the one that
    matters, the same reasoning as only confirming when a solve is in flight.
    """
    import json

    if not (report.unflushed or report.errors or report.abort_timed_out):
        return None

    try:
        import app_paths

        path = app_paths.app_data_dir() / "last-shutdown-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "quit": report.quit,
            "abort_timed_out": report.abort_timed_out,
            "unflushed": report.unflushed,
            "errors": report.errors,
            "server_stage": report.server_stage,
        }, indent=2))
        return path
    except Exception:
        logger.exception("could not persist the shutdown report")
        return None


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

    **The state flips to complete BEFORE `destroy()`** — and this is
    PLATFORM-SPECIFIC, which an earlier version of this docstring asserted
    unqualified.

    Measured against the installed pywebview 6.2.1 with a real window: on
    **cocoa the `closing` event does NOT re-fire** (`destroy()` is
    `AppHelper.callAfter(self.window.close)`, and `NSWindow.close()` does not
    send `windowShouldClose:`). On **winforms** it does — `destroy_window` ->
    `i.Close()` -> `on_closing` — and on **gtk** it does, via an explicit
    `emit('delete-event')`.

    So the ordering is load-bearing on Windows and GTK and inert on macOS. It
    stays, because Windows needs it — but note the consequence for testing:
    the macOS half of Task 7 CANNOT detect a regression here, and
    `test_the_state_flips_to_complete_BEFORE_destroy_is_called` asserts a
    re-entry that only ever happens on a platform nobody here can run. The unit
    test fakes the re-entry deliberately for that reason.
    """

    def __init__(
        self,
        *,
        run: Callable[[], ShutdownReport],
        destroy: Callable[[], None],
        deadline: float = SHUTDOWN_DEADLINE,
    ) -> None:
        self._run = run
        self._destroy = destroy
        self._deadline = deadline
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
            report = self._run_with_deadline()
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

    def _run_with_deadline(self) -> ShutdownReport:
        """
        Bound the whole sequence, not just its steps.

        `on_closing` vetoes for as long as this worker lives, and on macOS
        `applicationShouldTerminate` routes through the same event — so Cmd-Q
        and Dock->Quit are vetoed too. Step 3 has already hidden the window, so
        past this point the user sees a vanished app, a live process, and Force
        Quit as the only way out: exactly what this workstream exists to
        prevent. `_save_context` takes `ctx.mutation_lock` with no timeout, so
        an unbounded flush is reachable without anything being wrong.

        On expiry the inner worker is ABANDONED rather than killed — it is a
        daemon thread and the process is on its way out. What matters is that
        the window is released and the report says why.
        """
        outcome: list[ShutdownReport] = []
        failure: list[BaseException] = []

        def target() -> None:
            try:
                outcome.append(self._run())
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                failure.append(exc)

        inner = threading.Thread(target=target, name="pypsa-gui-shutdown-steps",
                                 daemon=True)
        inner.start()
        inner.join(self._deadline)

        if inner.is_alive():
            report = ShutdownReport(quit=True)
            report.errors.append(
                f"the shutdown sequence did not finish within {self._deadline:g}s "
                "and was abandoned — some work may not have been saved"
            )
            logger.error("shutdown deadline of %ss expired; abandoning", self._deadline)
            persist_report(report)
            return report

        if failure:
            raise failure[0]
        return outcome[0]

    def wait_for_completion(self, timeout: float) -> bool:
        """For tests and for a caller that wants to join the worker."""
        return self._complete.wait(timeout)
