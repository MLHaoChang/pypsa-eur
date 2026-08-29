from __future__ import annotations

import asyncio
import collections
import contextvars
import json
import logging
import queue
import threading
from dataclasses import asdict
from typing import Any

# Module logger for /results endpoints. The catch-all `except Exception:
# return _not_solved()` arms below convert ANY failure into a 204 ("no
# results") so the UI degrades gracefully — but that also hides genuine bugs
# (KeyError, dtype, NaN-render) as if the network simply wasn't solved. Log
# the traceback before the 204 so the cause is visible in the backend log;
# `logger.exception(...)` captures the active exception (works from a helper
# called inside the except block too).
logger = logging.getLogger("pypsa_gui.results")


class BufferedLogQueue:
    """
    SimpleQueue + ring buffer of recently emitted lines + fanout subscribers.

    Drop-in for `queue.SimpleQueue` from the producer side (solver_service
    just calls `.put(line)`). On the consumer side, exposes `.history()`
    so the SSE replay and the `/log_history` endpoint can return lines
    that have already been drained by an earlier (now-disconnected) SSE
    client. Sentinels (`None`) are intentionally NOT recorded — they're
    a transport detail, not real log content. logging.handlers.QueueHandler
    inserts LogRecord objects; we coerce to string before recording so the
    history is plain text regardless of producer type.

    Subscribers (chatbot integration v6 Phase 0):
      The legacy `get()` consumer is destructive (SimpleQueue.get pops the
      item — a SECOND consumer would race for items, breaking the
      `/log_stream` SSE). The fanout side channel — `subscribe()` returns
      a `(sub_id, deque)` pair; the chat tool-progress SSE bridge appends
      to its deque from `.put()`. The legacy `get()` path is untouched
      (still drains `self._queue`). Subscribers MUST `unsubscribe(sub_id)`
      on disconnect (e.g. in the chat SSE generator's `try/finally`) or the
      deque + its lock leak per closed tab. None sentinels are NOT
      forwarded to subscribers (fanout happens INSIDE the `if item is not
      None` block) so a subscriber-driven loop never observes the SSE
      close-signal that belongs to the legacy `get()` consumer; without
      this skip, a chat consumer would interpret end-of-stream when a
      different /run's None sentinel fires.
    """

    # Per-subscriber bounded deque size. Sized to match `_history` so a
    # subscriber that briefly stalls (await on a slow client) still catches up
    # on the same number of recent lines the SSE replay holds. New lines past
    # the cap silently drop from the OLDEST end — preferable to growing
    # unbounded under a stuck consumer.
    SUBSCRIBER_MAXLEN: int = 5000

    def __init__(self, maxlen: int = 5000) -> None:
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._history: collections.deque = collections.deque(maxlen=maxlen)
        self._hist_lock = threading.Lock()
        # Fanout subscribers — id -> bounded deque. Guarded by `_sub_lock` for
        # subscribe/unsubscribe membership + the put-time iteration (so a
        # concurrent unsubscribe never sees the snapshot mid-mutation).
        self._subscribers: dict[int, collections.deque] = {}
        self._sub_lock = threading.Lock()
        self._next_sub_id: int = 0

    def put(self, item: Any) -> None:
        if item is not None:
            try:
                text = item.getMessage() if hasattr(item, "getMessage") else str(item)
            except Exception:
                text = str(item)
            with self._hist_lock:
                self._history.append(text)
            # Fanout to chatbot subscribers — INSIDE the `if item is not None`
            # block so the None close-signal stays a transport detail of the
            # legacy `_queue.get()` path. v6 Phase 0 invariant.
            with self._sub_lock:
                # Snapshot the deque references under the lock so the actual
                # append runs without holding it (deque.append is thread-safe
                # per docs; the lock here just guards the dict membership).
                subs_snapshot = list(self._subscribers.values())
            for dq in subs_snapshot:
                dq.append(text)
        self._queue.put(item)

    # The consumer side still uses get(timeout=...) — pass through.
    def get(self, *args, **kwargs):
        return self._queue.get(*args, **kwargs)

    def history(self) -> list[str]:
        with self._hist_lock:
            return list(self._history)

    def subscribe(self) -> tuple[int, collections.deque]:
        """
        Register a new fanout subscriber and return its (id, deque).

        The caller drains the deque (e.g. via `deque.popleft` in a polling
        loop or by handing it to a thread that signals on append) and MUST
        call `unsubscribe(sub_id)` on disconnect — chatbot SSE generators
        use a `try/finally` for this so a closed browser tab does not leak
        the deque + dict entry.
        """
        with self._sub_lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            dq: collections.deque = collections.deque(maxlen=self.SUBSCRIBER_MAXLEN)
            self._subscribers[sub_id] = dq
        return sub_id, dq

    def unsubscribe(self, sub_id: int) -> None:
        """
        Drop a fanout subscriber. Safe to call with an unknown id (no-op) so
        a `try/finally` cleanup doesn't double-fault on a subscribe failure.
        """
        with self._sub_lock:
            self._subscribers.pop(sub_id, None)

from fastapi import APIRouter, Request
from models.schemas import SolverConfigSchema
from services.dispatch_status import dispatch_status as _dispatch_status
from services.pypsa_service import PyPSAService
from services.solver_service import (
    SolverConfig,
    check_solver_availability,
    periodized_capital_costs,
    run_simulation,
    user_code_enabled,
)
from services import study_state as _study_state
from services.ac_pf_service import run_ac_pf_stage
from services.validation_service import has_errors, validate_for_run
from starlette.responses import StreamingResponse


router = APIRouter()
# `results_router` (the ~33 read-only /results/* serializers) now lives in
# `routers/results.py`; this module keeps only the run/abort/SSE lifecycle.

# ── Module-level simulation state ────────────────────────────────────────────

# Thread guard for `_state`. Separate from the PyPSA-network lock so HTTP
# reads (status/results) don't compete with PyPSA mutations. Worker threads
# write into `_state` while HTTP threads read it; without a lock the reader
# can observe a half-updated snapshot (e.g. `status="completed"` paired with
# `objective=None`). The lock is now PER-PROJECT: it lives on the active
# ProjectContext and is reached via PyPSAService.get_solver_state_lock(), so a
# background solve (B4) locks its OWN state, not the foreground's. Acquire it
# (via that accessor, or the helpers below) for any multi-key read/write, OR any
# write a reader is allowed to observe in full. It is an RLock — the /run claim
# holds it across helpers that re-acquire it.


def _state_update(**kw) -> None:
    """
    Atomic multi-key update on `_state`. Holds `_state_lock` for the
    duration so other threads either see all of `kw` applied or none of it.
    """
    with PyPSAService.get_solver_state_lock():
        _state.update(kw)


def _state_snapshot() -> dict:
    """
    Return a coherent shallow copy of `_state`. Use for response payloads
    that depend on multiple keys (e.g. status + objective + solve_time).
    """
    with PyPSAService.get_solver_state_lock():
        return dict(_state)


def _solver_in_flight() -> bool:
    """
    True when a solver worker thread is still alive — either actively
    optimising or in the post-solve restore phase that reverts
    ``_apply_modelling_assumptions`` (vintage rows, VOLL slacks, dispatch
    fix, load scalers, …).

    During this window the network carries transient LP transforms and is
    NOT safe to:
      * validate (the validator sees the half-applied state and reports
        bogus issues — orphan vintage rows, slacks on every bus, etc.),
      * serialise to disk (``n.export_to_netcdf`` races with PyPSA's
        ``n.add`` calls inside the LP build, producing corrupted netcdfs).

    /abort flips ``status`` to ``"aborted"`` instantly but HiGHS / Gurobi
    don't yield until the next iteration boundary, so this can stay True
    for many seconds after the user has clicked Abort. Endpoints that
    mutate or read DataFrames must gate on this — checking ``status``
    alone is not enough.
    """
    # Read the thread handle under `_state_lock` — it's written by the /run
    # and /run_ac_pf claim (now also under the lock) and cleared by
    # force_reset. Reading under the same lock guarantees a coherent value
    # rather than racing a concurrent claim/clear. `Thread.is_alive()` is
    # itself thread-safe, so it's fine to call after releasing the lock.
    with PyPSAService.get_solver_state_lock():
        t = _state.get("thread")
    return t is not None and t.is_alive()


def _solver_in_flight_ctx(ctx) -> bool:
    """
    `_solver_in_flight()` for a SPECIFIC ProjectContext rather than the active
    one. Reads the worker handle from that ctx's own solver_state under its own
    solver_state_lock. The dispatcher (B4.3) uses this to gate a save against the
    BACKGROUND ctx it is solving — checking the active foreground's in-flight
    state would be the wrong question for a non-foreground solve. Same alive-check
    semantics; `Thread.is_alive()` is itself thread-safe so it's fine after the
    lock is released.
    """
    with ctx.solver_state_lock:
        t = ctx.solver_state.get("thread")
    return t is not None and t.is_alive()


# The live solver-lifecycle + result state for the ACTIVE (foreground) project.
# `_state` is a STABLE proxy that forwards every mapping operation to the active
# ProjectContext's solver_state dict (PyPSAService.get_solver_state()). It
# replaces the old module-global dict so the (lazy) `from routers.simulation
# import _state` importers across projects.py / snapshots.py and the `sim._state`
# attribute access in solve_queue.py always resolve to whichever project is
# active NOW — never a stale binding captured at import time. The dict's shape is
# the canonical ProjectSolverState (services/project_context.py), the single
# source of truth the persistence layer derives its key list from. A background
# solve (B4) writes a NON-active project's state via its own context handle, not
# through this proxy.
class _ActiveStateProxy:
    """Mapping view of the active project's solver_state dict — see comment above."""

    __slots__ = ()

    @staticmethod
    def _t() -> dict[str, Any]:
        return PyPSAService.get_solver_state()

    def __getitem__(self, key):
        return self._t()[key]

    def __setitem__(self, key, value):
        self._t()[key] = value

    def __delitem__(self, key):
        del self._t()[key]

    def __contains__(self, key):
        return key in self._t()

    def __iter__(self):
        return iter(self._t())

    def __len__(self):
        return len(self._t())

    def __repr__(self):
        return f"_ActiveStateProxy({self._t()!r})"

    def get(self, key, default=None):
        return self._t().get(key, default)

    def update(self, *args, **kwargs):
        self._t().update(*args, **kwargs)

    def clear(self):
        self._t().clear()

    def keys(self):
        return self._t().keys()

    def items(self):
        return self._t().items()

    def values(self):
        return self._t().values()

    def setdefault(self, key, default=None):
        return self._t().setdefault(key, default)

    def pop(self, key, *args):
        return self._t().pop(key, *args)

    def copy(self):
        return self._t().copy()


_state = _ActiveStateProxy()


# ── Solver config ─────────────────────────────────────────────────────────────

@router.get("/solver_config")
def get_solver_config():
    return asdict(_state["solver_config"])


@router.put("/solver_config")
def update_solver_config(cfg: SolverConfigSchema):
    # Real partial-PUT: merge submitted fields over the existing config so
    # callers can flip a single knob without echoing the rest of the
    # payload. exclude_unset=True only emits fields the request body
    # actually set — Pydantic schema defaults for OMITTED fields no longer
    # silently overwrite live state (e.g. "PUT run_ac_pf_after_lopf=true"
    # used to reset voll/discount_rate/sclopf back to defaults).
    submitted = cfg.model_dump(exclude_unset=True)
    # Legacy mode 'lpf' was removed in v1.x — coerce to 'lopf' silently so
    # old saved configs and stale frontend caches don't 400 the user. Same
    # treatment applied in projects.py at load time.
    if submitted.get("mode") == "lpf":
        submitted["mode"] = "lopf"
    # Read-modify-write is racy without the lock: a concurrent PUT could
    # observe the same baseline and clobber a sibling's update.
    with PyPSAService.get_solver_state_lock():
        merged = asdict(_state["solver_config"])
        merged.update(submitted)
        _state["solver_config"] = SolverConfig(**merged)
        return asdict(_state["solver_config"])


@router.get("/check_solvers")
def check_solvers():
    return check_solver_availability()


@router.get("/capabilities")
def capabilities():
    """
    Operator-controlled feature flags. Drives UI disable/banner state for
    features that are off-by-default for security reasons (e.g. arbitrary user
    code in extra_functionality_code).
    """
    return {"user_code_enabled": user_code_enabled()}


@router.get("/asset_costs")
def asset_costs():
    """
    Periodized per-asset capital cost for every cost-bearing component.

    Frontend uses this to compute "Investments by asset" CAPEX when the
    user parameterised an asset via `overnight_cost` (capital_cost = 0).
    PyPSA's annuity (overnight × annuity(rate, life) × nyears) is applied
    here so the table matches the LP-effective numbers — without it, the
    table would show €0 for any asset whose `capital_cost` field is blank.

    Returns {component_attr: {asset_name: effective_capital_cost}} where
    component_attr is one of 'generators', 'storage_units', 'stores',
    'links', 'lines', 'transformers'.
    """
    n = PyPSAService.get_network()
    cfg = _state["solver_config"]
    try:
        return periodized_capital_costs(n, cfg)
    except Exception:
        return {}


# ── Preflight validation ──────────────────────────────────────────────────────

@router.post("/preflight")
def preflight():
    """
    Run the same validation that gates a real /run, but without solving.
    Returns the issue list so the UI can show it inline (e.g. a "Validate"
    button in Solver Settings). The /run endpoint executes the same checks
    again — keeping a single source of truth in validation_service.

    Defers if a solver worker is still alive — abort sets the status to
    "aborted" instantly but the LP transforms (vintage rows, VOLL slacks,
    dispatch fix) only get reverted by ``restore_modelling()`` in the
    worker's finally block AFTER HiGHS/Gurobi yields. Running the
    validator on the transient state produces a wall of bogus issues
    (orphan vintages, slack-on-every-bus, …) that vanish the moment the
    worker exits.
    """
    if _solver_in_flight():
        # Two distinct stuck states map to different remediation:
        #   * status == "running"  — solve genuinely in progress; wait.
        #   * status == "aborted"  — user clicked Abort but HiGHS/Gurobi is
        #                            still iterating in native code (linopy
        #                            can't interrupt a C-level optimiser
        #                            between checkpoints). The ONLY fix is
        #                            a backend restart — the worker won't
        #                            yield on its own, the lock will never
        #                            release, and every mutate/save will
        #                            refuse until the process is killed.
        current_status = _state.get("status")
        if current_status == "aborted":
            reason = (
                "Solver was aborted but is still running in native solver "
                "code (HiGHS/Gurobi can't be interrupted mid-iteration "
                "from Python). The PyPSA lock will not release on its own. "
                "Restart the backend to recover — there is no clean way to "
                "kill the in-flight LP from within the process."
            )
        else:
            reason = (
                "Solver worker still active — validation deferred until the "
                "LP transforms (vintage rows, slack generators, dispatch fix) "
                "are reverted. Click Revalidate once the lock is released."
            )
        return {
            "ok": True,
            "errors": 0,
            "warnings": 0,
            "issues": [],
            "deferred": True,
            "deferred_reason": reason,
            "deferred_stuck": current_status == "aborted",
        }
    n = PyPSAService.get_network()
    config = _state["solver_config"]
    issues = validate_for_run(n, config)
    return {
        "ok": not has_errors(issues),
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warning"),
        "issues": [i.to_dict() for i in issues],
    }


# ── Run / Abort / Status ──────────────────────────────────────────────────────

def _compute_run_objective(n, cfg=None) -> float | None:
    """
    Total system cost reported in the status bar = n.objective + n.objective_constant.

    `n.objective` is the LP's VARIABLE-only objective (can be negative when the
    curtailment wrapper subtracts dispatch subsidies). `objective_constant`
    carries fixed-asset CAPEX (existing-line capex etc.) plus the wrapper's
    constant offset. The user-visible objective is the TOTAL — otherwise it
    appears wildly negative on networks with large existing-capacity CAPEX.

    MYOPIC: each period is a SEPARATE LP, and NO combination of the per-period
    LP objectives is the horizon system cost.

    Capacity frozen by an earlier period carries `p_nom_extendable=False`, and
    PyPSA charges CAPEX only for extendables — a non-extendable's capex is meant
    to reach the total through `n.objective_constant`, which is IDENTICALLY ZERO
    under `multi_investment_periods=True` (PyPSA's `define_objective` builds the
    multi-invest constant but appends it to `terms` only in the single-period
    branch). Summing the per-period LPs therefore charges each asset's CAPEX
    once, in its build period, and never for the rest of its service life.
    Measured on a 3-period system: -42.9% against the true cost, and +22.2% the
    OTHER way with `lf_aggregate_future=True`, where the lookahead window's
    future-period OPEX is counted once in the lookahead and again when that
    period is actually solved. The sign of the error depends on config, so the
    number was not trustworthy in either direction.

    So for myopic we report the statistics-based horizon cost — the same basis
    the Economics tab (`/results/cost_breakdown`) and the Compare tab already
    use, which is what makes a myopic run comparable with a full-foresight one.
    `n._myopic_period_objectives` is still populated and still surfaced by
    `/results/objective_decomposition` for anyone who wants the per-period LP
    values.

    Full-horizon solves keep the LP total (variable + constant): a single LP
    already prices the whole horizon, and the two bases agree there.

    `cfg` is the solver config to price with. It is a parameter rather than a
    read of `_state["solver_config"]` because the solve-queue dispatcher prices
    a background project with ITS own config; omitting it falls back to the
    foreground config, which is right for the `/run` path only.

    Shared by the foreground `/run` worker and the multi-project solve-queue
    dispatcher (services/solve_queue.py) so both report the objective identically.
    """
    myopic_entries = getattr(n, "_myopic_period_objectives", None)
    if isinstance(myopic_entries, list) and myopic_entries:
        from services.cost_totals import horizon_system_cost
        total = horizon_system_cost(n, cfg if cfg is not None else _state["solver_config"])
        if total is not None:
            return total
        # Statistics unavailable (unsolved / empty frame). The per-period LP sum
        # is wrong as established above, but it beats reporting nothing at all
        # for a run that did complete — the alternative is a blank status bar.
        try:
            return sum(v + c for _, v, c in myopic_entries)
        except Exception:
            return None
    try:
        obj_var = float(n.objective) if hasattr(n, "objective") and n.objective is not None else None
    except Exception:
        obj_var = None
    try:
        obj_const = float(getattr(n, "objective_constant", 0.0) or 0.0)
    except Exception:
        obj_const = 0.0
    return (obj_var + obj_const) if obj_var is not None else None


@router.post("/run")
def run():
    from fastapi import HTTPException
    import time as _time

    # MESH HOLE, fixed in Phase 7 (coupling-loop spec §3, plan [S7]). The
    # adequacy studies guard each other, but nothing stopped a FOREGROUND
    # solve from landing between two of a study's own iterates. The frontier,
    # the sweep and the coupling loop all mutate the network by re-solving it,
    # and the loop's `evaluate` then samples whatever plan the network happens
    # to be holding — so an interleaved /run silently re-solves under the
    # user's config and the loop certifies a cap against a plan it never
    # produced. Refused BEFORE the claim, so the study is never left racing a
    # half-started worker.
    _blocked = _study_state.blocking_study_detail()
    if _blocked:
        raise HTTPException(409, _blocked)

    stop_event = threading.Event()
    log_queue = BufferedLogQueue()

    def _worker():
        t0 = _time.time()
        config = _state["solver_config"]
        n = PyPSAService.get_network()
        lock = PyPSAService.get_lock()
        # Inject `_state_update` as the side-result sink so run_simulation
        # writes lost-load / AC-PF results into THIS (foreground) project's
        # state without importing the module global. A future multi-project
        # dispatcher passes the solving project's own state_update instead.
        status, condition = run_simulation(
            config, n, lock, stop_event, log_queue, state_update=_state_update
        )
        elapsed = _time.time() - t0
        # Total system cost (variable objective + objective_constant, summed
        # across per-period LPs in myopic mode) — see _compute_run_objective.
        obj = _compute_run_objective(n)
        # If the user force-reset us, _state["thread"] has been swapped to
        # another worker (or cleared). Skip the final state write so we
        # don't clobber the new run's status.
        if _state.get("thread") is not threading.current_thread():
            return
        # Map run_simulation's returned status to the lifecycle status the
        # frontend consumes. "aborted" is a clean user-requested exit (the
        # solver honoured stop_event and restore_modelling ran); don't
        # demote it to "failed".
        if status == "aborted":
            final_status = "aborted"
        elif status in ("ok", "optimal"):
            final_status = "completed"
        else:
            final_status = "failed"
        _state_update(
            status=final_status,
            condition=condition,
            solve_time=round(elapsed, 2),
            objective=obj,
        )

    # Carry the request's context into the worker. Step 0b resolves the active
    # project into a ContextVar, and a bare `threading.Thread` does NOT inherit
    # contextvars — so the worker would fall back to the PROCESS foreground and
    # write its status, objective and results into a context nobody is reading,
    # while the caller's `/status` polls its own and sees `None` forever.
    _worker_ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: _worker_ctx.run(_worker), daemon=True)

    # Gate + claim + start under a SINGLE _state_lock hold so two concurrent
    # /run requests cannot both pass the "not running" check and spawn racing
    # workers that mutate the shared network via n.add/n.remove. The previous
    # code checked status at the top and registered the thread separately at
    # the bottom — the check and the claim weren't atomic, so two requests
    # arriving close together both observed "not running" and both started.
    # The thread is started INSIDE the lock so a competing request observes
    # thread.is_alive()==True; a not-yet-started thread reports is_alive()==
    # False and would be mistaken for stale state, re-opening the race.
    # _state_lock is an RLock, so the nested _state_update is safe.
    with PyPSAService.get_solver_state_lock():
        if _state["status"] == "running":
            # Recover from stale state: if the worker thread died without
            # resetting status (uncaught exception, segfault, process restart
            # with frozen flag), allow the new run to proceed. Only reject if
            # a live thread is still solving.
            existing = _state.get("thread")
            if existing is not None and existing.is_alive():
                from fastapi import HTTPException
                raise HTTPException(409, "Simulation already running")
        # Atomic multi-key claim. A concurrent /status poll sees all of these
        # applied or none. Wipes the previous run's results so a fresh solve
        # doesn't surface stale objective / lost-load / Stage-2 state. The
        # thread handle is registered in the SAME apply so there's no window
        # where status="running" but thread is unset.
        _state_update(
            status="running",
            condition=None,
            objective=None,
            solve_time=None,
            last_failure=None,
            stop_event=stop_event,
            log_queue=log_queue,
            last_lost_load=None,
            adequacy_report=None,
            lopf_results=None,
            ac_pf_results=None,
            ac_pf_convergence=None,
            ac_pf_convergence_list=None,
            ac_pf_slack_bus_used=None,
            ac_pf_stripped_voll_slacks=None,
            ac_pf_converged_count=None,
            ac_pf_total_snapshots=None,
            thread=t,
            # Which KIND of worker owns `thread`. AC PF and the LP solve share
            # this key with identical status and condition, so without it a
            # consumer cannot tell them apart — and the desktop shutdown must,
            # because nothing reads AC PF's stop event and it therefore cannot
            # be aborted at all. Set on BOTH claims rather than only on AC PF:
            # a stale "ac_pf" left over from a previous run would mark the next
            # LP solve non-interruptible and it would never be aborted.
            # Observability only; nothing in this module branches on it.
            kind="lopf",
        )
        t.start()
    return {"status": "started"}


@router.get("/lock_status")
def lock_status():
    """
    Non-blocking probe of the PyPSA lock + worker thread state.

    The ``/abort`` endpoint flips ``status`` to ``'aborted'`` instantly
    (just setting the stop_event), but the PyPSA lock stays held until
    the worker thread actually exits — HiGHS / Gurobi C-level iterations
    can't be cancelled mid-iteration, so a long iteration may keep the
    lock for several more seconds AFTER abort was requested. Any
    subsequent endpoint that needs the lock (``/projects/{name}`` load,
    ``/simulation/run``, ``/simulation/preflight``) blocks for axios'
    full 30 s timeout in that window.

    This endpoint gives the frontend an authoritative signal:
      * ``lock_held=False`` AND ``worker_alive=False`` — safe to load /
        run; the previous worker has cleared the lock.
      * ``lock_held=True`` — solver still in native code; switching
        scenarios will block.

    Implementation: ``RLock.acquire(blocking=False)`` returns instantly
    with True if the lock is free, False otherwise. We immediately
    release if acquired. The probe itself takes ~microseconds; safe to
    poll every 500 ms from the frontend without measurable load.
    """
    lock = PyPSAService.get_lock()
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
        held = False
    else:
        held = True
    worker = _state.get("thread")
    worker_alive = bool(worker is not None and worker.is_alive())
    return {"lock_held": held, "worker_alive": worker_alive}


@router.post("/force_reset")
def force_reset():
    """
    Disown a hung simulation so a new /run can start.

    Sets the stop_event (best-effort), forgets the worker thread, and marks
    state as failed. The old thread continues running in the background
    until its native solver call returns — when it eventually finishes, the
    final _state write is skipped because the thread reference no longer
    matches. The PyPSA lock remains held by the old worker until that
    happens, so a follow-up /run still blocks on lock acquisition. If the
    old solve is truly hung in HiGHS/Gurobi native code, only a backend
    restart frees the lock — there is no clean way to interrupt a C-level
    optimiser call from Python.
    """
    stop_event = _state.get("stop_event")
    if stop_event is not None:
        try:
            stop_event.set()
        except Exception:
            pass
    _state_update(
        status="failed",
        condition="force_reset",
        thread=None,
        stop_event=None,
        log_queue=None,
    )
    return {"status": "reset"}


@router.post("/abort")
def abort():
    """
    Request the running solve stop and wait briefly for the worker to
    actually exit (so the ``restore_modelling`` finally block runs and the
    network is left clean for save/validate).

    Best-case path: HiGHS/Gurobi is between LP iterations, sees the abort,
    returns to PyPSA. The worker's ``finally:`` reverts vintage rows /
    slacks / dispatch fix and exits. We notice within the grace window
    and respond with ``cleanup_complete=True``.

    Worst case: the solver is mid-iteration in native code (uninterruptible
    from Python). We return after the grace window with
    ``cleanup_complete=False``; the frontend's existing ``/lock_status``
    polling waits the rest. The network stays "dirty" until the worker
    eventually exits — but the ``deferred`` path in /preflight and the
    409 in /projects/{name} save now hide that from the user instead of
    surfacing a wall of phantom validation errors.
    """
    from fastapi import HTTPException
    stop_event = _state.get("stop_event")
    worker = _state.get("thread")
    if stop_event and _state["status"] == "running":
        stop_event.set()
        # Use _state_update for the status flip so a concurrent status
        # poll sees the transition atomically (it's a single key here, but
        # keeping the pattern uniform across the codebase makes the
        # lock-discipline easier to audit).
        _state_update(status="aborted")
        # Best-effort grace wait. Long enough that a solver near a yield
        # point can finish + run restore; short enough that the HTTP
        # response stays well within axios' 30 s timeout.
        cleanup_complete = False
        if worker is not None and worker.is_alive():
            worker.join(timeout=5.0)
            cleanup_complete = not worker.is_alive()
        else:
            cleanup_complete = True
        return {
            "status": "abort_requested",
            "cleanup_complete": cleanup_complete,
        }
    raise HTTPException(400, "No simulation running")


@router.post("/run_ac_pf")
def run_ac_pf():
    """
    Standalone Stage 2 trigger.

    Pre-conditions:
      • No solve is in-flight (`status != 'running'`).
      • The network is solved AND has dispatch (n.generators_t.p non-empty).

    Behaviour:
      • Fresh log_queue → existing /log_stream SSE picks up Stage 2 phase
        markers without any frontend changes.
      • Snapshots the LP state first, then runs `run_ac_pf_stage`. PyPSA's
        `n.pf()` overwrites lines_t.p0/p1, buses_t.v_mag_pu/v_ang in place;
        the LP snapshot in `_state['lopf_results']` is the only way back.
      • Holds the PyPSA lock for the duration of the worker thread —
        mutations during AC PF would corrupt the dispatch fix.
    """
    import time as _time

    from fastapi import HTTPException

    # The SECOND foreground entrypoint, guarded for the same reason as /run
    # (spec §3 asks for "both solve entrypoints if two exist"). Stage 2 is not
    # an LP, but it holds the PyPSA lock for its whole run and overwrites
    # `lines_t.p0/p1` and `buses_t.v_mag_pu` in place — so a study's next
    # re-solve queues behind it and the foreground results a study restores to
    # are no longer the ones it measured. Checked FIRST, before the
    # solved/dispatch pre-conditions: "a study is running" is the actionable
    # answer, and a 400 about stale dispatch would send the user to re-run the
    # very solve this refuses.
    _blocked = _study_state.blocking_study_detail()
    if _blocked:
        raise HTTPException(409, _blocked)

    n = PyPSAService.get_network()
    stop_event = threading.Event()
    log_queue = BufferedLogQueue()
    from services.dispatch_status import dispatch_status as _disp_status

    def _worker():
        t0 = _time.time()
        config = _state["solver_config"]
        lock = PyPSAService.get_lock()
        try:
            with lock:
                ac_pf_out = run_ac_pf_stage(n, config, log_queue)
            # Single atomic write: AC-PF result payload + lifecycle fields go
            # in together so /status can't observe a half-applied update.
            _state_update(
                **ac_pf_out,
                status="completed",
                condition="ac_pf_ok",
                solve_time=round(_time.time() - t0, 2),
            )
            log_queue.put(f"[PHASE] Stage 2 complete in {_time.time() - t0:.2f} s")
        except Exception as exc:
            log_queue.put(f"[PHASE] Failed: {exc}")
            log_queue.put(f"ERROR: {exc}")
            # Push the traceback so opaque PyPSA/linopy/xarray errors surface with
            # file/line in the SSE log (same pattern as run_simulation's except).
            import traceback as _tb
            for _line in _tb.format_exc().rstrip().split("\n"):
                log_queue.put(f"TRACEBACK: {_line}")
            _state_update(status="failed", condition=str(exc))

    # Carry the request's context into the worker. Step 0b resolves the active
    # project into a ContextVar, and a bare `threading.Thread` does NOT inherit
    # contextvars — so the worker would fall back to the PROCESS foreground and
    # write its status, objective and results into a context nobody is reading,
    # while the caller's `/status` polls its own and sees `None` forever.
    _worker_ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: _worker_ctx.run(_worker), daemon=True)

    # Gate + pre-checks + claim + start under a SINGLE _state_lock hold so two
    # concurrent triggers can't both pass the "not running" check and spawn
    # racing workers (same TOCTOU fix as /run). dispatch_status reads only the
    # network (no lock of its own) so holding _state_lock across it is safe and
    # brief. The thread is started inside the lock so a competing request
    # observes is_alive()==True. _state_lock is an RLock → nested _state_update
    # is safe; raising inside the block releases the lock via the context manager.
    with PyPSAService.get_solver_state_lock():
        if _state["status"] == "running":
            existing = _state.get("thread")
            if existing is not None and existing.is_alive():
                raise HTTPException(409, "Simulation already running")
            # stale state (worker died) → fall through and claim
        if not getattr(n, "is_solved", False):
            raise HTTPException(
                400, "No LOPF solution to fix dispatch from. Run /run first."
            )
        # Reject stale dispatch BEFORE claiming. `dispatch_status` returns
        # 'fresh'/'stale'/'none'; only 'fresh' is acceptable here.
        _s = _disp_status(n)
        if _s == "none":
            raise HTTPException(
                400, "n.generators_t.p is empty — re-run LOPF to populate dispatch."
            )
        if _s == "stale":
            raise HTTPException(
                400,
                "Dispatch tables exist but their column-set doesn't match the "
                "current topology (rows added/removed since the last solve). "
                "Re-run LOPF to regenerate consistent dispatch before Stage 2.",
            )
        # Atomic claim — lifecycle reset + thread handle in one apply, so no
        # window where status="running" but thread is unset.
        _state_update(
            status="running",
            condition=None,
            stop_event=stop_event,
            log_queue=log_queue,
            lopf_results=None,
            ac_pf_results=None,
            ac_pf_convergence=None,
            ac_pf_convergence_list=None,
            ac_pf_slack_bus_used=None,
            ac_pf_stripped_voll_slacks=None,
            ac_pf_converged_count=None,
            ac_pf_total_snapshots=None,
            thread=t,
            kind="ac_pf",   # see the `kind` note on the LP claim above
        )
        t.start()
    return {"status": "started"}


@router.get("/status")
def get_status():
    # Snapshot atomically so the four lifecycle fields are guaranteed
    # consistent with each other (no `status="completed"` + `objective=None`).
    s = _state_snapshot()
    # Dispatch freshness — 'fresh' (results consistent with topology), 'stale'
    # (results exist but columns no longer match the components), or 'none' (no
    # results / cleared). A topology edit on a solved network clears dispatch
    # (the undo middleware empties the `_t` tables → 'none'); the frontend
    # watches a fresh→non-fresh transition to tell the user their results were
    # invalidated by an edit and to re-run. Read-only (shape/column compare) —
    # no lock needed per the read-never-lock policy.
    try:
        dispatch = _dispatch_status(PyPSAService.get_network())
    except Exception:
        dispatch = "none"
    return {
        "running": s["status"] == "running",
        "status": s["status"],
        "condition": s["condition"],
        "objective": s["objective"],
        "solve_time": s["solve_time"],
        # Actionable failure card for the last finished solve (None on
        # success/abort or before any run). Lets a late poll / reload surface
        # the same "why it failed" card the SSE `done` event carries live.
        "failure": s.get("last_failure"),
        # 'fresh' | 'stale' | 'none' — see comment above.
        "dispatch": dispatch,
    }


# ── Log history (replay buffer) ───────────────────────────────────────────────

@router.get("/log_history")
def get_log_history():
    """
    Return lines emitted by the current (or most recent) solve.

    Used by the frontend to reconstruct the log on page reload / SSE
    reconnect. Lines are kept in a ring buffer (BufferedLogQueue, default
    maxlen=5000) populated by every solver_service .put() call, so this
    endpoint returns content even after the live SSE consumer drained
    them. Returns {"lines": [], "running": false} when no solve has been
    started since process boot.
    """
    q = _state.get("log_queue")
    lines = q.history() if isinstance(q, BufferedLogQueue) else []
    return {"lines": lines, "running": _state["status"] == "running"}


# ── SSE log stream ────────────────────────────────────────────────────────────

@router.get("/log_stream")
async def log_stream(request: Request):
    """
    Server-Sent Events stream of solver log lines.

    Disconnect handling: each loop iteration polls
    ``await request.is_disconnected()`` before pulling from the queue. When
    the client closes the SSE (browser tab close, navigation away, network
    drop past the auto-reconnect window), the generator returns within one
    queue-poll interval (~0.5 s). Without this check the generator kept
    running until the worker pushed ``None`` to the queue — on a multi-hour
    solve that meant one executor thread per disconnected client was stuck
    in ``q.get(timeout=0.5)`` for the entire remaining duration, blocking
    the default thread pool and eventually starving other endpoints that
    use ``run_in_executor`` (e.g. the undo-snapshot middleware's
    ``asyncio.to_thread(_push_undo_snapshot)`` call).
    """
    async def generate():
        q = _state.get("log_queue")
        if q is None:
            yield "data: No simulation running\n\n"
            return

        # Replay history before going live. Late-connecting clients (post-
        # reload, post-blip auto-reconnect) get every line emitted so far —
        # avoids the "log empty after refresh" UX bug where the in-memory
        # Zustand store starts fresh but the backend run is still in flight.
        # The same line CAN show up once via history + once via live drain
        # if it was queued between snapshot and drain; the frontend de-dupes
        # by tracking the last-seen tail of the history.
        if isinstance(q, BufferedLogQueue):
            for line in q.history():
                # Bail early if the client disconnected mid-history-replay
                # (e.g. clicked away before the dump completed).
                if await request.is_disconnected():
                    return
                safe = str(line).replace("\n", " ").replace("\r", "")
                yield f"data: {safe}\n\n"

        loop = asyncio.get_event_loop()

        while True:
            # Check disconnect FIRST — cheaper than queuing another
            # executor call we'd immediately drop. The check is sub-
            # millisecond on the asyncio side (just inspects the ASGI
            # message buffer).
            if await request.is_disconnected():
                # Client gone — don't emit `done` (nobody to receive it).
                # The worker thread keeps producing lines into the queue;
                # they age out via BufferedLogQueue's bounded ring.
                return
            try:
                msg = await loop.run_in_executor(
                    None, lambda: q.get(timeout=0.5)
                )
            except Exception:
                if _state["status"] not in ("running",):
                    break
                continue

            if msg is None:
                # Results-delivery race: `run_simulation` queues this sentinel
                # from its `finally` BEFORE the worker / queue-dispatcher writes
                # the final lifecycle status (completed/failed/aborted) + the
                # objective. Snapshotting right now can therefore read a
                # mid-transition `status="running"`, which the frontend's `done`
                # handler would mis-map to "failed" on a SUCCESSFUL solve. Wait
                # briefly (≤2s, cheap 50ms polls) for the status to go terminal
                # so the `done` payload carries the TRUE outcome + objective.
                # The worker sets it within ~ms of returning, so this almost
                # always exits on the first poll; the cap bounds a worker that
                # died mid-finalise (then "running" → "failed" is acceptable).
                deadline = loop.time() + 2.0
                while loop.time() < deadline:
                    if _state_snapshot()["status"] != "running":
                        break
                    # Honour a mid-wait client disconnect (matches the loop's
                    # disconnect-first discipline) — no point waiting to emit a
                    # `done` nobody will read.
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(0.05)
                # Atomic snapshot of the lifecycle triple for the SSE
                # `done` event — same consistency reason as /status.
                s = _state_snapshot()
                payload = json.dumps({
                    "status": s["status"],
                    "objective": s["objective"],
                    "solve_time": s["solve_time"],
                    "condition": s["condition"],
                    # Actionable failure card (None on success/abort) so the
                    # frontend can show "why it failed + what to try" and
                    # auto-open the Issues panel the moment the solve ends.
                    "failure": s.get("last_failure"),
                })
                yield f"event: done\ndata: {payload}\n\n"
                break

            safe = str(msg).replace("\n", " ").replace("\r", "")
            yield f"data: {safe}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


