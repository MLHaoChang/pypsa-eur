"""
Solve-run plumbing: solver availability, the abort watcher, the heartbeat, and
the log handlers that keep one solve's records out of another's queue.

Carved out of `services/solver_service.py`. A leaf — it imports nothing from
`solver_service` and nothing from the rest of the `solver` package.

`SolveAborted` lives here and is caught by an `except SolveAborted` back in
`run_simulation`. The façade re-exports the class ITSELF, not a copy: a
same-named second class object would sail straight through that handler, which
is why `tests/test_solver_facade_surface.py` asserts `is` rather than `hasattr`.

`_safe_log` is here rather than with the objective wrappers that call it. It is
log-queue plumbing, and putting it in `objective.py` would leave the only
generic None-safe queue helper inside a module about LP objectives.
"""
import logging
import logging.handlers
import threading
import time


def _safe_log(log_queue, msg: str) -> None:
    """
    Put `msg` on the solver log queue, swallowing a None queue or any put
    error. The None-safe try/except wrapper for the per-phase `_emit`/`_put`
    closures (BUDGET / CURT / NUMERICS / objective-scale), which keep their own
    tag prefixes and delegate the boilerplate here.
    """
    if log_queue is None:
        return
    try:
        log_queue.put(msg)
    except Exception:
        pass


def check_solver_availability() -> dict[str, bool]:
    try:
        from linopy.solvers import available_solvers
    except ImportError:
        available_solvers = []
    known = ["highs", "gurobi", "glpk", "cplex", "scip", "cbc"]
    return {s: s in available_solvers for s in known}


# ── Abort plumbing ──────────────────────────────────────────────────────────
# The /abort endpoint sets a stop_event that the worker observes two ways:
#
#   1. COOPERATIVE checkpoints — `_check_stop()` polls the event BEFORE optimize()
#      (validation, modelling assumptions, snapshot build), BETWEEN myopic
#      iterations, and AFTER optimize() (AC PF, diagnostics, storage). Cheap,
#      always-safe, raises `SolveAborted`.
#   2. NATIVE-SOLVE interrupt — while HiGHS is inside the blocking `n.optimize()`
#      C call, cooperative polling can't fire. linopy 0.6.5 runs `h.run()` on its
#      own sub-thread and polls `finished.wait(0.1)` catching `KeyboardInterrupt`
#      → `h.cancelSolve()` (HiGHS returns `kInterrupt` → a CLEAN user-interrupt
#      status, not an error). So we make the running LP abortable by INJECTING a
#      `KeyboardInterrupt` into the solver worker thread the moment the stop_event
#      fires during the optimize window — `_AbortWatcher` does this. The injected
#      KeyboardInterrupt lands at linopy's next poll tick (≤0.1s), HiGHS cancels
#      mid-iteration, and PyPSA returns. The worker's KeyboardInterrupt handler
#      re-raises it as `SolveAborted` so the existing restore chain runs.
#
# Hard constraint preserved: `restore_modelling()` MUST run regardless of abort
# — it reverts vintage rows, slack generators, dispatch fix, etc. Both the
# cooperative and injected paths converge on `SolveAborted` → the outer except
# runs restore before returning ("aborted", "user_aborted").

import ctypes as _ctypes


def _async_raise_in_thread(thread_id: int, exc_type: type) -> int:
    """
    Inject `exc_type` into the thread with id `thread_id` (CPython async-exc).

    Returns the count of threads affected (1 = delivered). The exception is
    raised at that thread's NEXT bytecode boundary — which, for a thread parked
    in linopy's `finished.wait(0.1)` poll loop around HiGHS's `h.run()`, is
    within ~0.1s. Used only to interrupt a native solve on user abort; every
    other phase uses the cooperative `_check_stop`.
    """
    res = _ctypes.pythonapi.PyThreadState_SetAsyncExc(
        _ctypes.c_long(thread_id), _ctypes.py_object(exc_type)
    )
    if res > 1:
        # Should never happen (one thread id) — undo to avoid corrupting state.
        _ctypes.pythonapi.PyThreadState_SetAsyncExc(_ctypes.c_long(thread_id), None)
    return res


class _AbortWatcher:
    """
    Background watcher that injects KeyboardInterrupt into the SOLVER WORKER
    thread when the stop_event fires WHILE ARMED.

    The armed window (via `arm()`/`disarm()`) spans the WHOLE solve `try:` body —
    the native `n.optimize()` call (the case that matters: it's the long blocking
    one) AND the post-solve work that follows it inside the same try (capacity
    capture, objective rescale, post-solve diagnostics, and — for myopic — every
    iteration plus its per-iteration restore). An interrupt landing ANYWHERE in
    that window is safe: it propagates to the solve `finally:`, whose FIRST act is
    `disarm()` (so injection stops before the restore walk), then the once-guarded
    `restore_modelling()` runs (idempotent, per-entry-guarded) and the outer
    `except KeyboardInterrupt` re-runs it defensively. So the restore discipline
    holds regardless of WHERE the interrupt fired — the network is never left
    carrying transient LP transforms, and an aborted solve is never autosaved.
    What's NOT in the window: validation + modelling-assumptions apply (pre-arm,
    covered by cooperative `_check_stop`) and `restore_modelling` itself + result
    writes + AC-PF (post-disarm).

    SINGLE-SHOT delivery (critical correctness invariant). The watcher injects
    KeyboardInterrupt EXACTLY ONCE — the instant `PyThreadState_SetAsyncExc`
    confirms delivery (return == 1) it NEVER injects again for this run. It does
    NOT need to: linopy 0.6.5 runs `highspy.Highs.run()` on a daemon sub-thread
    while the worker (this `worker_tid`) sits in a Python-level
    `while not finished.wait(0.1): pass` poll loop, so a single async-exc is
    reliably raised at the next 0.1s poll tick — there is no "deep in C code that
    misses the signal" window to retry around. Re-injecting would be actively
    HARMFUL: after linopy catches the first KeyboardInterrupt it calls
    `h.cancelSolve()` and enters a SECOND, un-guarded `finished.wait(0.1)` loop to
    wait for HiGHS to actually stop; a second injected interrupt landing there
    propagates out of linopy WHILE `h.run()` is still executing on the sub-thread
    (orphaning the native solve — CPU thrash that starves the next queued job),
    and a still-later injection can land inside `restore_modelling` (which catches
    only `Exception`, not `BaseException`) and strand the network half-restored.
    Single-shot lets linopy's own clean cancel→wait→re-raise path complete
    uninterrupted, so the network is fully restored and no native thread leaks.
    A genuine non-delivery (return == 0, e.g. the worker thread already gone) is
    the only case the loop retries — there is nothing to orphan in that case.
    """

    def __init__(self, stop_event: "threading.Event | None", worker_tid: int) -> None:
        self._stop_event = stop_event
        self._worker_tid = worker_tid
        self._armed = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._stop_event is None:
            return  # no abort channel (qa scripts) → nothing to watch
        self._thread = threading.Thread(
            target=self._loop, name="solve-abort-watcher", daemon=True
        )
        self._thread.start()

    def arm(self) -> None:
        self._armed.set()

    def disarm(self) -> None:
        self._armed.clear()

    def shutdown(self) -> None:
        self._shutdown.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2)

    def _loop(self) -> None:
        se = self._stop_event
        injected = False  # latch: inject ONCE on confirmed delivery, never again
        while not self._shutdown.is_set():
            # Wait for an abort request (cheap; wakes immediately on .set()).
            if se is None or not se.wait(timeout=0.2):
                continue
            # Abort requested. Inject a SINGLE KeyboardInterrupt while armed (the
            # native-solve window). The instant SetAsyncExc confirms delivery
            # (==1) we latch `injected` and stop — re-injection would break
            # linopy's post-cancelSolve finish-wait and orphan h.run() / strand
            # restore (see class docstring). Only a genuine non-delivery (==0,
            # worker gone) retries on the next tick.
            while se.is_set() and not self._shutdown.is_set():
                if self._armed.is_set() and not injected:
                    if _async_raise_in_thread(self._worker_tid, KeyboardInterrupt) == 1:
                        injected = True
                if self._shutdown.wait(timeout=0.2):
                    return


class _SolveHeartbeat:
    """
    Periodic liveness pings during the otherwise-silent native solve window.

    HiGHS runs the LP inside a C-extension call (`h.run()`); depending on the
    solver phase — presolve especially — it can go many seconds, minutes on a
    large model, WITHOUT writing a single line to its log file. The log tail
    then shows nothing and the GUI looks frozen: the user can't tell a long
    solve from a hung one. This daemon emits a `[PHASE]` heartbeat every
    `interval` seconds while a solve is active, carrying elapsed wall time and
    the reminder that Abort is available — so a long solve always reads as
    alive and interruptible. It writes to the SAME `log_queue` the SSE consumer
    drains, so the lines stream live and land in the replay buffer.

    Lifecycle mirrors `_AbortWatcher`: `start()` once at run entry, `begin()` /
    `end()` bracket the native-solve window (the same arm/disarm points), and
    `shutdown()` in the outer finally joins the thread. Inactive (parked, no
    pings) outside a begin/end window, so pre-LP setup and post-solve
    diagnostics stay quiet. The first ping fires one full `interval` AFTER
    begin() — a fast solve that finishes inside the interval emits nothing.
    """

    def __init__(self, log_queue, interval: float = 15.0) -> None:
        self._q = log_queue
        self._interval = interval
        self._active = threading.Event()
        self._shutdown = threading.Event()
        self._start_monotonic = 0.0
        self._label = "Optimising"
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="solve-heartbeat", daemon=True
        )
        self._thread.start()

    def begin(self, label: str = "Optimising") -> None:
        self._label = label
        self._start_monotonic = time.monotonic()
        self._active.set()

    def end(self) -> None:
        self._active.clear()

    def shutdown(self) -> None:
        self._shutdown.set()
        self._active.set()  # wake the loop so it observes _shutdown promptly
        t = self._thread
        if t is not None:
            t.join(timeout=2)

    def _loop(self) -> None:
        while not self._shutdown.is_set():
            # Park until a solve begins (cheap; wakes on begin()/shutdown()).
            if not self._active.wait(timeout=0.5):
                continue
            if self._shutdown.is_set():
                return
            # Active. Sleep the interval in short hops so end() silences pings
            # within ~0.5s, then emit ONE heartbeat if the solve is still going.
            waited = 0.0
            while waited < self._interval:
                if self._shutdown.is_set() or not self._active.is_set():
                    break
                time.sleep(0.5)
                waited += 0.5
            if self._active.is_set() and not self._shutdown.is_set():
                elapsed = int(time.monotonic() - self._start_monotonic)
                mm, ss = divmod(elapsed, 60)
                try:
                    self._q.put(
                        f"[PHASE] ⏳ {self._label}… {mm:d}:{ss:02d} "
                        f"elapsed — click Abort to cancel."
                    )
                except Exception:
                    pass  # never let a logging hiccup kill the heartbeat thread


class SolveAborted(Exception):
    """
    Raised when the user-set stop_event is observed at a checkpoint.
    Caller's existing try/except/finally chain unwinds modelling-assumption
    restores cleanly before propagating. Treat as a normal control-flow
    exception, not an error — log at INFO, not ERROR.
    """

    pass


def _check_stop(stop_event: threading.Event | None, phase, where: str) -> None:
    """
    Raise SolveAborted if the user requested abort.

    Call at natural checkpoints — never inside an LP solver call (we can't
    interrupt that). Emits a single phase line so the SSE log records WHERE
    the abort was honoured, which is useful for diagnosing "why didn't it
    abort sooner" on long pre-LP setup.
    """
    if stop_event is not None and stop_event.is_set():
        try:
            phase(f"Aborted by user (at: {where}). Reverting modelling assumptions…")
        except Exception:
            pass
        raise SolveAborted(where)


class _RollingWindowFailureCatcher(logging.Handler):
    """
    Captures PyPSA's per-window "Optimization failed" warnings.

    `optimize_with_rolling_horizon` solves each window and, on a non-ok window,
    LOGS ``logger.warning("Optimization failed with status %s and condition
    %s", status, condition)`` and CONTINUES — it returns the network (not a
    status tuple) and never raises. So a clean return does NOT mean every window
    solved. This handler, attached to ``pypsa.optimization.abstract`` for the
    duration of the call, records each failed window's ``(status, condition)``
    (read from ``record.args``) so the caller can surface a real failure instead
    of reporting a masked "optimal".

    Thread-scoped: the logger is process-global, so a CONCURRENT solve (foreground
    vs queue background) attaching its own catcher would otherwise see THIS
    solve's window warnings and cross-attribute them. PyPSA emits the warning
    synchronously inside the solving thread, so we capture this solve's thread
    ident at construction and ignore records emitted from any other thread.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.failures: list[tuple[str, str]] = []
        self._tid = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        # Only count failures emitted from the thread that owns this catcher —
        # a concurrent solve on another thread shares this global logger.
        if getattr(record, "thread", None) != self._tid:
            return
        try:
            msg = record.getMessage()
        except Exception:
            return
        if "Optimization failed with status" in msg:
            args = record.args if isinstance(record.args, tuple) else ()
            st = str(args[0]) if len(args) >= 1 else "warning"
            cond = str(args[1]) if len(args) >= 2 else "unknown"
            self.failures.append((st, cond))


class _ThreadScopedQueueHandler(logging.handlers.QueueHandler):
    """
    Root-logger → SSE bridge that only forwards THIS solve's records.

    The plain ``QueueHandler`` this replaced was attached to the **root**
    logger, so on a shared process every log record produced by every other
    tenant's request — 500 tracebacks included — was streamed into whichever
    tenant happened to be solving. That is a cross-tenant disclosure, not a
    cosmetic one.

    The fix is NOT to attach to a narrower logger. Attaching to, say, the
    solver's own logger would EMPTY the stream: the root attachment is
    deliberate, because the log the user reads *is* ``pypsa.*`` / ``linopy.*`` /
    HiGHS output emitted under those third-party logger names. Instead, filter
    on the emitting thread, which is exactly the pattern
    ``_RollingWindowFailureCatcher`` above already uses for the same reason —
    PyPSA emits synchronously inside the solving thread, so the ident captured
    at construction identifies this solve's records and no one else's.

    STEP 2 CAVEAT: this holds only while one job runs per thread. If the worker
    ever runs jobs asyncio-concurrently on a single event loop, ``thread``
    degenerates to a constant and cross-job contamination returns *silently*.
    Switch to a ``contextvars.ContextVar`` at that point — it survives both
    threads and tasks — and keep the S0.6 assertion that pypsa/linopy lines are
    still present, so the fix cannot regress into the bug it replaced.
    """

    def __init__(self, log_queue) -> None:
        super().__init__(log_queue)
        self._owner_tid = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "thread", None) != self._owner_tid:
            return
        super().emit(record)
