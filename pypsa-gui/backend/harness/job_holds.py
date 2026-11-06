"""Holds on job calls whose outcome is unknown (issue 04, slice S5).

A job call (a tool of the `execution` or `execution_long_running` tier) that
ends uncertain in an ordinary, untasked turn opens a hold on its
`ChatSession`, keyed by the project binding and the tool. The hold carries the
call's G3 fingerprint (`guards.fingerprint`), its tool_use_id and whatever can
later show the operation ended: the worker future, a job id, or the solve
thread.

While the hold is open, a later call of that tool on that binding is denied
before any effect seam (GK B (30) N7: after the G3 compare and the edit-lock
check, before `effects.reserve`, `assistant_tasks.begin_step` and the
handler): `operation_pending` with the same fingerprint, `operation_conflict`
with another one. Once a same-fingerprint call has been told `operation_pending`,
the next one shows the repeat card instead, which names the hold: the model
first checks the job, and only a call it makes again goes to the user. The
card's approval is bound to the hold id and that fingerprint, both fixed when
the card is issued (N5), and runs once (N6). A different-fingerprint call
never gets a card (N5, builder's choice): it is denied until the hold clears.

What opens a hold, by path (loop.py `_dispatch_real_tool_call`):
- `tool_timeout` on the shared worker pool: the worker may still run. Its
  future is the evidence.
- `tool_not_started` opens none: `Future.cancel()` succeeded, so the handler
  can never run (N3).
- run_simulation and run_ac_pf_stage run on the SSE thread and never time out
  (N9). They open a hold on a raise only when the raise is uncertain: a new
  solve thread is alive after it, or the solver state could not be read. A
  successful call whose `_outcome` reads uncertain opens one on its job id.
- Any other raise opens none: the worker returned, so the call has ended.

What clears a hold (N3; never time alone):
- future: done. A late result that names a job id moves the hold onto that job.
- job id: the job's status reads terminal (`harness.job_adapters` for fg:/ad:/
  rpt:, the solve queue for its UUIDs).
- solve thread: the thread the raise left alive has ended; with no thread
  known, the live solver record shows no live thread.
- an approved repeat card (the new call replaces the hold), or the end of the
  session (the hold lives on the in-memory session).

A read that fails counts as not terminal, so the hold stays (N4, #1079).

Across sessions (N1) the only persisted record is the solve-queue store:
`solve_queue_enqueue` with no hold in this session reads the store's queued
and running rows for the project, strictly (a failed read denies). Every
other job tool's cross-session replay is a `_pending` row in issue 04.

abort_simulation (destructive) and wait_for_job (read) are not job calls here:
re-aborting is idempotent and a hold never blocks an abort (N2).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

PENDING = "operation_pending"
CONFLICT = "operation_conflict"
KINDS = frozenset({PENDING, CONFLICT})
# Literals for tests/test_tool_error_kind_manifest.py, which reads every kind.
_KINDS = (
    {"error_kind": "operation_pending"},
    {"error_kind": "operation_conflict"},
)
JOB_TIERS = frozenset({"execution", "execution_long_running"})
SSE_TOOLS = frozenset({"run_simulation", "run_ac_pf_stage"})
# N1: the job tools whose duplicate the solve-queue store can show across sessions.
QUEUE_STORE_TOOLS = frozenset({"solve_queue_enqueue"})
_ACTIVE = ("queued", "running")


@dataclass
class Hold:
    id: str
    tool: str
    binding: Any
    fingerprint: str
    tool_use_id: str
    opened: str  # tool_timeout | uncertain_raise | uncertain_outcome
    opened_at: float = field(default_factory=time.time)
    future: Any = None
    job_id: str | None = None
    worker: Any = None  # the solve thread a raise left alive
    from_store: bool = False
    told: bool = False  # a same-fingerprint call was denied operation_pending


def is_job_tool(tool: str) -> bool:
    from harness import catalogue

    try:
        return catalogue.safety_tier_for(tool) in JOB_TIERS
    except Exception:  # noqa: BLE001 - an uncatalogued tool is no job call
        return False


def _binding() -> Any:
    from harness.outcomes import _binding as binding

    return binding()


_UNREADABLE = object()


def _current_binding() -> Any:
    try:
        return _binding()
    except Exception:  # noqa: BLE001 - an unreadable binding matches every hold
        return _UNREADABLE


def _holds(session) -> dict:
    holds = getattr(session, "job_holds", None)
    if holds is None:
        holds = {}
        session.job_holds = holds
    return holds


# ── Opening ──────────────────────────────────────────────────────────────────


def _open(session, tool, fingerprint, tool_use_id, opened, **evidence) -> Hold:
    hold = Hold(
        id=uuid.uuid4().hex[:12],
        tool=tool,
        binding=_current_binding(),
        fingerprint=fingerprint,
        tool_use_id=tool_use_id,
        opened=opened,
        **evidence,
    )
    with session._lock:
        _holds(session)[(hold.binding, tool)] = hold
    return hold


def open_on_timeout(session, tool, fingerprint, tool_use_id, future) -> Hold | None:
    if not is_job_tool(tool):
        return None
    return _open(session, tool, fingerprint, tool_use_id, "tool_timeout", future=future)


def solve_thread(ctx) -> Any:
    """The live solver record's thread; raises when it cannot be read."""
    with ctx.solver_state_lock:
        return ctx.solver_state.get("thread")


def open_on_raise(session, tool, fingerprint, tool_use_id, ctx, prior) -> Hold | None:
    """run_simulation / run_ac_pf_stage raised. Uncertain only when a new solve
    thread is alive after it, or the solver state cannot be read."""
    if tool not in SSE_TOOLS:
        return None
    try:
        thread = solve_thread(ctx)
    except Exception:  # noqa: BLE001 - an unreadable record is uncertain (N4)
        return _open(session, tool, fingerprint, tool_use_id, "uncertain_raise")
    if thread is None or thread is prior or not _alive(thread):
        return None
    return _open(
        session, tool, fingerprint, tool_use_id, "uncertain_raise", worker=thread
    )


def open_on_outcome(session, tool, fingerprint, tool_use_id, outcome) -> Hold | None:
    """A returned run_simulation / run_ac_pf_stage whose `_outcome` is uncertain."""
    if tool not in SSE_TOOLS or not isinstance(outcome, dict):
        return None
    if outcome.get("outcome") != "uncertain":
        return None
    job_id = outcome.get("job_id")
    return _open(
        session,
        tool,
        fingerprint,
        tool_use_id,
        "uncertain_outcome",
        job_id=job_id if isinstance(job_id, str) else None,
    )


# ── Clearing evidence ────────────────────────────────────────────────────────


def _alive(thread) -> bool:
    is_alive = getattr(thread, "is_alive", None)
    return bool(is_alive()) if callable(is_alive) else False


# The queue routes answer with the job's public record, whose id is `id`.
_ID_IS_JOB = frozenset({"solve_queue_enqueue", "solve_queue_requeue"})


def _job_id_in(tool, result) -> str | None:
    if isinstance(result, dict):
        for key in ("job_id", "id") if tool in _ID_IS_JOB else ("job_id",):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        job = result.get("job")
        if isinstance(job, dict) and isinstance(job.get("id"), str):
            return job["id"]
    return None


def job_terminal(job_id: str) -> bool:
    """The job's own status, read now; raises when it cannot be read."""
    from harness import job_adapters as ja
    from services import chat_tools

    if (
        ja.is_foreground_job_id(job_id)
        or ja.is_study_job_id(job_id)
        or ja.is_report_job_id(job_id)
    ):
        if ja.is_foreground_job_id(job_id):
            waiter = ja.wait_foreground_job
        elif ja.is_study_job_id(job_id):
            waiter = ja.wait_study_job
        else:
            waiter = ja.wait_report_job
        view = waiter(
            job_id,
            0,
            actor_id=chat_tools.acting_user_id(),
            stop=threading.Event(),
            progress_slot={},
        )
        return bool(view["terminal"])
    from routers.solve_queue import _visible_job_or_404
    from services.solve_queue import _TERMINAL

    with chat_tools._acting() as (db, user):
        job = _visible_job_or_404(db, user, job_id)
    return job["status"] in _TERMINAL


def _live_solve_thread() -> Any:
    from services.pypsa_service import PyPSAService

    return solve_thread(PyPSAService.get_active_context())


def terminal(hold: Hold) -> bool:
    """Whether independent evidence shows the held operation has ended. Any
    read that fails raises, and the caller keeps the hold (N4)."""
    if hold.from_store:
        return False
    if hold.future is not None:
        if not hold.future.done():
            return False
        if hold.future.cancelled() or hold.future.exception() is not None:
            return True
        job_id = _job_id_in(hold.tool, hold.future.result())
        if job_id is None:
            return True
        hold.future, hold.job_id = None, job_id  # the late result names its job
    if hold.job_id is not None:
        return job_terminal(hold.job_id)
    if hold.worker is not None:
        return not _alive(hold.worker)
    if hold.tool in SSE_TOOLS:
        thread = _live_solve_thread()
        return thread is None or not _alive(thread)
    return False


# ── The check (P2) ───────────────────────────────────────────────────────────


def _held(session, tool) -> Hold | None:
    """This session's open hold for `tool` on the current binding, after its
    clearing evidence is read. A failed read keeps it."""
    binding = _current_binding()
    with session._lock:
        found = [
            (key, hold)
            for key, hold in _holds(session).items()
            if key[1] == tool
            and (
                binding is _UNREADABLE
                or hold.binding is _UNREADABLE
                or key[0] == binding
            )
        ]
    for key, hold in found:
        try:
            ended = terminal(hold)
        except Exception:  # noqa: BLE001 - unreadable evidence is not terminal (N4)
            ended = False
        if ended:
            with session._lock:
                if _holds(session).get(key) is hold:
                    del _holds(session)[key]
            continue
        return hold
    return None


def _store_hold(tool, args, fingerprint) -> Hold | None:
    """N1: the solve-queue store's queued or running solve of the project
    `solve_queue_enqueue` names. Read strictly: a failed read is a hold. The
    store keeps no fingerprint; the tool's only effective argument is the
    project (`rerun_current` is ignored by its handler), so a matching row is
    the same operation and reads pending, never conflict."""
    if tool not in QUEUE_STORE_TOOLS:
        return None
    project = (args or {}).get("project_id")
    if not isinstance(project, str) or not project:
        return None
    from services import chat_tools, project_registry, solve_job_store

    try:
        with chat_tools._acting() as (db, user):
            key = project_registry.registry_key(
                project_registry.resolve_project(db, user, project)
            )
    except HTTPException:
        return None  # not found or not allowed: the handler gives its own refusal
    except Exception:  # noqa: BLE001 - a lookup that fails denies (N4)
        key = None
    try:
        if key is None:
            raise LookupError("project unresolved")
        rows = solve_job_store.load_by_status(
            _ACTIVE, project_key_prefix=key, strict=True
        )
        rows = [
            r
            for r in rows
            if r.get("project_key") == key and r.get("kind") in (None, "solve")
        ]
    except Exception:  # noqa: BLE001 - an unreadable store denies (N4)
        rows = [{"id": None}]
    if not rows:
        return None
    return Hold(
        id="store",
        tool=tool,
        binding=key,
        fingerprint=fingerprint,
        tool_use_id="",
        opened="queued_elsewhere",
        job_id=str(rows[0]["id"]) if rows[0].get("id") is not None else None,
        from_store=True,
    )


def repeat_card(session, tool, args, fingerprint) -> dict | None:
    """The card block naming the open hold a same-fingerprint call repeats.
    None when no session hold matches; a store hold gets no card (the queue
    cannot run the project twice)."""
    if not is_job_tool(tool):
        return None
    hold = _held(session, tool)
    if hold is None or hold.fingerprint != fingerprint or not hold.told:
        return None
    return {
        "hold_id": hold.id,
        "fingerprint": fingerprint,
        "tool_use_id": hold.tool_use_id,
        "opened": hold.opened,
        "opened_at": hold.opened_at,
        "job_id": hold.job_id,
    }


def _pointer(hold: Hold) -> str:
    if hold.job_id:
        return f"Check it with wait_for_job (job {hold.job_id})"
    if hold.tool in SSE_TOOLS:
        return "Check the solve's status"
    return "Check its job status"


def _stop(hold: Hold) -> str:
    if hold.tool in SSE_TOOLS:
        return "abort_simulation"
    if hold.tool in QUEUE_STORE_TOOLS:
        return "solve_queue_abort"
    return "the job's own cancel"


def check(session, tool, args, fingerprint, card: dict | None = None):
    """`(kind, message)` when an open hold refuses this call, else None.

    An approved repeat `card` admits the call only while the hold it names is
    still the open one and the call's fingerprint is the one it bound; the
    hold is then dropped, since the call runs as a new operation (N5/N6)."""
    if not is_job_tool(tool):
        return None
    hold = _held(session, tool)
    if (
        hold is not None
        and card is not None
        and card.get("hold_id") == hold.id
        and card.get("fingerprint") == fingerprint == hold.fingerprint
    ):
        with session._lock:
            holds = _holds(session)
            for key in [k for k, h in holds.items() if h is hold]:
                del holds[key]
        return None
    if hold is None:
        hold = _store_hold(tool, args, fingerprint)
    if hold is None:
        return None
    if hold.fingerprint == fingerprint:
        if hold.from_store:
            return PENDING, (
                f"The solve queue already has this project's solve, queued or "
                f"running, so it was not queued again. {_pointer(hold)}, or stop "
                f"it with {_stop(hold)}."
            )
        with session._lock:
            hold.told = True
        return PENDING, (
            f"The same {tool} request from earlier may still be running; its "
            f"outcome is unknown, so it was not started again. {_pointer(hold)}, "
            f"or stop it with {_stop(hold)}. If the user wants it started again "
            f"anyway, call it again with the same settings: they will be asked "
            f"to confirm the repeat."
        )
    return CONFLICT, (
        f"An earlier {tool} request with other settings may still be running; "
        f"its outcome is unknown, so this one was not started. {_pointer(hold)}, "
        f"or stop it with {_stop(hold)}, before starting another."
    )
