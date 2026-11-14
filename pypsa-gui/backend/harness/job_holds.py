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

Across sessions (N1; assistant-assurance issue 16, GK B (35) H1-H9):
- Each hold on a saved project is also written as an `assistant_job_holds`
  row (`harness.job_hold_store`) and its evidence is kept in this process's
  registry, so the hold outlives its chat. A chat with no hold of its own
  reads the rows for the project and tool: a row this process owns is
  checked on its registry evidence, the same evidence that clears it here,
  and adopted into that chat; a row this process owns with no registry entry
  holds (fail closed). A failed read denies (N4). The row is deleted exactly
  when the hold clears: on terminal evidence or an approved repeat card, never
  at the end of a chat. The registry is bounded by that: an entry whose
  future never ends stays until its repeat card is approved.
- A row another process owns (another replica, or this process's own rows
  from before a restart, read under its new replica id: H3) holds while its
  owner's lease runs (slice S2, GK B (35) H1-H3, K1-K6). The owner writes
  `lease_until` = now + `LEASE_SECONDS` at open and one renewer thread per
  process extends it every `RENEW_SECONDS`. Once the lease has lapsed the row
  clears by its tool's worker kind (`JOB_KINDS`, H1): in-process work, a solve
  included (K1), died with its owner and clears; queue-backed work clears only
  on its terminal `solve_jobs` row, or with no job id when no live row of its
  kind was enqueued in its window (K3). Every clear of another owner's row is
  a compare-and-delete on (id, owner, lease_until), so a renewal that landed
  first wins (H2). A NULL lease (an S1 replica's row) lapses
  `NULL_LEASE_BOUND_SECONDS` after the later of its opening and this process's
  start (K2). Nothing clears on boot alone (H3); the renewer also sweeps
  lapsed rows by the same rules.
- A hold whose row could not be written (no saved project, or a failed write)
  stays on its chat only, and the call's result says so (H4). The window
  between a call starting and its timeout is unprotected across chats, as it
  always was.
- `solve_queue_enqueue` also reads the solve-queue store's queued and
  running rows for the project, strictly (a failed read denies).

abort_simulation (destructive) and wait_for_job (read) are not job calls here:
re-aborting is idempotent and a hold never blocks an abort (N2).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from harness import job_hold_store

logger = logging.getLogger(__name__)

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
    # Issue 16: the persisted record (`job_hold_store`).
    project_id: str | None = None
    opened_by: str | None = None  # the acting user who made the call
    shared: bool = False  # its record was written; other chats can read it
    share_error: str | None = None  # why not, when it was not
    orphan: bool = False  # a record this process owns with no registry entry
    foreign: bool = False  # a record another process owns, read by its lease
    unverified: bool = False  # the records could not be read (N4)


# Issue 16: every shared hold's evidence in this process, by record id. An
# entry leaves exactly when its record is deleted (`_forget`), or when the
# renewer finds its record cleared elsewhere (K4).
_REGISTRY: dict[str, Hold] = {}
_REGISTRY_LOCK = threading.Lock()

# ── Worker kinds (S2, GK B (35) H1) ─────────────────────────────────────────
# What a lapsed lease means for each job tool. INPROC and INPROC_SOLVER work
# runs on a thread or future of the owner process and dies with it; a solve's
# cbc/glpk child may outlive it, spending CPU with no one to read its result
# (K1, Q1 = (a): a named residual), and so may the queued per-case solves a
# dead orchestrator thread enqueued (run_decision_study,
# campus_apply_island_cuts). QUEUE work is a `solve_jobs` row the queue
# runs, or a restart reconciles to `interrupted`, whoever made the call.
INPROC = "inproc"
INPROC_SOLVER = "inproc+solver"
QUEUE = "queue"
JOB_KINDS: dict[str, str] = {
    "apply_project_changes": INPROC,
    "campus_apply_island_cuts": INPROC,
    "generate_availability_profile": INPROC,
    "generate_load_profile": INPROC,
    "generate_report": INPROC,
    "generate_series_profile": INPROC,
    "preview_default_series": INPROC,
    "propose_report_mapping": INPROC,
    "refine_generated_profile": INPROC,
    "regenerate_report_section": INPROC,
    "run_investment_case": INPROC,  # values a solved dispatch; no LP
    "run_business_case": INPROC,  # values the investment case's own solve; no LP
    "run_mc_study": INPROC,  # solves nothing
    "campus_start_milp": INPROC_SOLVER,
    # homer-parity 03a: one queued fork solve per supply architecture.
    "compare_supply_architectures": INPROC_SOLVER,
    "run_ac_pf_stage": INPROC_SOLVER,
    "run_coupling_loop": INPROC_SOLVER,
    "run_decision_study": INPROC_SOLVER,
    "run_eh_study": INPROC_SOLVER,
    "run_fmea_sweep": INPROC_SOLVER,
    "run_frontier_study": INPROC_SOLVER,
    "run_margin_loop": INPROC_SOLVER,
    "run_simulation": INPROC_SOLVER,
    "campus_recommend_gfm_settings": QUEUE,
    "campus_run_study": QUEUE,
    "generate_renewable_profile": QUEUE,  # a `profile_gen` job
    "gridspine_run_pipeline": QUEUE,
    "run_sensitivity_sweep": QUEUE,
    "solve_queue_enqueue": QUEUE,
    "solve_queue_requeue": QUEUE,
}
# The `solve_jobs.kind` values each QUEUE tool enqueues (NULL reads `solve`).
QUEUE_JOB_KINDS: dict[str, tuple[str, ...]] = {
    "campus_recommend_gfm_settings": ("campus",),
    "campus_run_study": ("campus",),
    "generate_renewable_profile": ("profile_gen",),
    "gridspine_run_pipeline": ("gridspine",),
    "run_sensitivity_sweep": ("solve",),
    "solve_queue_enqueue": ("solve",),
    "solve_queue_requeue": ("solve", "gridspine", "campus", "profile_gen"),
}

RENEW_SECONDS = 30.0  # three renewals per lease
# K2: how long an S1 replica (which writes NULL leases) can outlive the start
# of a process that reads leases: a rolling deploy's length, with margin. An
# S1 hold has no finite end, so the bound runs from the later of the row's
# opening and this process's start, never from the opening alone.
NULL_LEASE_BOUND_SECONDS = 3600.0
_STARTED_AT = time.time()


def lease_seconds() -> float:
    """The lease length, shared with the durable runner (`runner.LEASE_SECONDS`)."""
    from harness import runner

    return float(runner.LEASE_SECONDS)


UNSHARED_NOTE = (
    "This hold is kept in this chat only: another chat on this project is not "
    "told that the operation may still be running."
)


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


def _told(session) -> set:
    """Ids of the holds a same-fingerprint call of this chat was told
    operation_pending about (a hold read from a record is shared by chats)."""
    told = getattr(session, "job_holds_told", None)
    if told is None:
        told = set()
        session.job_holds_told = told
    return told


# ── The persisted record (issue 16) ─────────────────────────────────────────


def _project_of(binding) -> str | None:
    if isinstance(binding, tuple) and binding and binding[0]:
        return str(binding[0])
    return None


def _share(hold: Hold) -> None:
    """Write the hold's record and register its evidence. A failure leaves
    the hold on its chat only and says why (H4)."""
    project = _project_of(hold.binding)
    if project is None:
        hold.share_error = "no_saved_project"
        return
    try:
        import security
        from services import chat_tools

        user = chat_tools.acting_user_id()
        job_hold_store.write(
            record_id=hold.id,
            project_id=project,
            tool=hold.tool,
            fingerprint=hold.fingerprint,
            job_id=hold.job_id,
            opened=hold.opened,
            opened_at=hold.opened_at,
            opened_by=user,
            owner_replica_id=security.replica_id(),
            lease_until=time.time() + lease_seconds(),
        )
    except Exception as exc:  # noqa: BLE001 - the call already ran; say so (H4)
        hold.share_error = type(exc).__name__
        logger.warning(
            "job hold %s on %s not shared with other chats: %s",
            hold.id,
            hold.tool,
            hold.share_error,
        )
        return
    hold.project_id, hold.opened_by, hold.shared = project, user, True
    with _REGISTRY_LOCK:
        _REGISTRY[hold.id] = hold
    try:
        ensure_renewer()
    except Exception as exc:  # noqa: BLE001 - the lease then lapses; say why
        logger.error("job hold renewer not started: %s", type(exc).__name__)


def unshared_note(hold: Hold | None) -> str | None:
    """The plain-words note for a call whose hold other chats cannot see."""
    if hold is None or hold.shared:
        return None
    return UNSHARED_NOTE


def _forget(hold: Hold) -> None:
    """The hold cleared: delete its record, then its registry entry. A failed
    delete keeps the entry, so the next reader finds the evidence, reads it
    terminal and deletes the record then."""
    if not hold.shared:
        return
    try:
        job_hold_store.delete(hold.id)
    except Exception as exc:  # noqa: BLE001 - the entry stays for the next reader
        logger.warning(
            "job hold %s record not deleted: %s", hold.id, type(exc).__name__
        )
        return
    with _REGISTRY_LOCK:
        if _REGISTRY.get(hold.id) is hold:
            del _REGISTRY[hold.id]


def _record_job(hold: Hold, job_id: str) -> None:
    if not hold.shared:
        return
    try:
        job_hold_store.set_job(hold.id, job_id)
    except Exception as exc:  # noqa: BLE001 - this process keeps the evidence
        logger.warning(
            "job hold %s record kept no job id: %s", hold.id, type(exc).__name__
        )


# ── Opening ──────────────────────────────────────────────────────────────────


def _open(session, tool, fingerprint, tool_use_id, opened, **evidence) -> Hold:
    hold = Hold(
        id=uuid.uuid4().hex,
        tool=tool,
        binding=_current_binding(),
        fingerprint=fingerprint,
        tool_use_id=tool_use_id,
        opened=opened,
        **evidence,
    )
    _share(hold)
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
    if hold.from_store or hold.unverified:
        return False
    if hold.orphan:
        # Its only evidence is the record: gone once another chat's approved
        # repeat card deleted it.
        return not job_hold_store.exists(hold.id)
    if hold.foreign:
        record = job_hold_store.get(hold.id)
        return record is None or clear_foreign(record, time.time())
    # Chats share a registry hold: read each field once (another chat may
    # hand the future over to the job id meanwhile).
    future = hold.future
    if future is not None:
        if not future.done():
            return False
        if future.cancelled() or future.exception() is not None:
            return True
        job_id = _job_id_in(hold.tool, future.result())
        if job_id is None:
            return True
        hold.future, hold.job_id = None, job_id  # the late result names its job
        _record_job(hold, job_id)
    job_id = hold.job_id
    if job_id is not None:
        return job_terminal(job_id)
    if hold.worker is not None:
        return not _alive(hold.worker)
    if hold.tool in SSE_TOOLS:
        thread = _live_solve_thread()
        return thread is None or not _alive(thread)
    return False


# ── Another owner's records: the lease (S2) ─────────────────────────────────


def lapsed(record: job_hold_store.Record, now: float) -> bool:
    """Whether the record's owner stopped renewing it. A NULL lease (an S1
    replica's row) lapses only K2's bound after this process started."""
    if record.lease_until is not None:
        return now > record.lease_until
    return now > max(record.opened_at, _STARTED_AT) + NULL_LEASE_BOUND_SECONDS


def _ended_with_owner(record: job_hold_store.Record) -> bool:
    """H1: whether the lapsed owner's operation has ended, by its tool's kind.
    Any read that fails raises; an unknown tool never ends here."""
    kind = JOB_KINDS.get(record.tool)
    if kind in (INPROC, INPROC_SOLVER):
        return True  # it died with its owner (K1: a solver child may not)
    if kind != QUEUE:
        return False
    if record.job_id is not None:
        from services.solve_queue import _TERMINAL

        return job_hold_store.solve_job_status(record.job_id) in _TERMINAL
    from harness import budget

    # K3: the call may have enqueued at any time from its start, which was up
    # to a tool timeout before the hold opened, give or take the clock skew
    # between replicas (allowed one lease).
    since = record.opened_at - budget.PER_TOOL_TIMEOUT_SECONDS - lease_seconds()
    return not job_hold_store.live_solve_jobs(
        record.project_id, QUEUE_JOB_KINDS[record.tool], since
    )


def clear_foreign(record: job_hold_store.Record, now: float) -> bool:
    """Clear another owner's record once its lease lapsed and its operation
    ended (H1), by compare-and-delete (H2). True when the record is gone;
    False while it holds. Raises when a read fails."""
    if not lapsed(record, now) or not _ended_with_owner(record):
        return False
    job_hold_store.delete_if(record.id, record.owner_replica_id, record.lease_until)
    return not job_hold_store.exists(record.id)


def _renew() -> None:
    """Extend this process's leases. A record cleared elsewhere is lost: the
    log says so and it is never written again (K4)."""
    import security

    me = security.replica_id()
    with _REGISTRY_LOCK:
        holds = list(_REGISTRY.values())
    for hold in holds:
        try:
            kept = job_hold_store.renew(hold.id, me, time.time() + lease_seconds())
        except Exception as exc:  # noqa: BLE001 - the next tick tries again (H2)
            logger.warning("job hold %s not renewed: %s", hold.id, type(exc).__name__)
            continue
        if not kept:
            logger.warning(
                "job hold lost: %s on %s was cleared elsewhere", hold.id, hold.tool
            )
            with _REGISTRY_LOCK:
                if _REGISTRY.get(hold.id) is hold:
                    del _REGISTRY[hold.id]


def _sweep() -> None:
    """Clear other owners' lapsed records by the same rules as a read."""
    import security

    now = time.time()
    for record in job_hold_store.foreign(security.replica_id()):
        try:
            clear_foreign(record, now)
        except Exception as exc:  # noqa: BLE001 - the record stays (N4)
            logger.warning("job hold %s not swept: %s", record.id, type(exc).__name__)


def renew_tick() -> None:
    """One renewer tick: renew, then sweep. Never raises (H2)."""
    for step in (_renew, _sweep):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - the renewer keeps running
            logger.warning("job hold %s failed: %s", step.__name__, type(exc).__name__)


_RENEWER_LOCK = threading.Lock()
_RENEWER: tuple[int, threading.Thread] | None = None


def _renew_loop() -> None:
    while True:
        time.sleep(RENEW_SECONDS)
        try:
            renew_tick()
        except Exception:  # noqa: BLE001 - never ends the renewer (H2)
            logger.exception("job hold renewer tick failed")


def ensure_renewer() -> threading.Thread:
    """The process's one renewer, started on its first hold write and keyed by
    pid, so a forked worker starts its own (K5). Never started at import."""
    global _RENEWER
    pid = os.getpid()
    with _RENEWER_LOCK:
        if _RENEWER is not None and _RENEWER[0] == pid:
            return _RENEWER[1]
        thread = threading.Thread(
            target=_renew_loop, name="job-hold-renewer", daemon=True
        )
        thread.start()
        _RENEWER = (pid, thread)
        return thread


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
            _forget(hold)
            continue
        return hold
    return None


def _unverified(tool, binding) -> Hold:
    """The hold a chat meets when the records cannot be read (N4, H6)."""
    return Hold(
        id="unverified",
        tool=tool,
        binding=binding,
        fingerprint="",
        tool_use_id="",
        opened="unverified",
        unverified=True,
    )


def _from_record(record, binding, project, **flags) -> Hold:
    return Hold(
        id=record.id,
        tool=record.tool,
        binding=binding,
        fingerprint=record.fingerprint,
        tool_use_id="",
        opened=record.opened,
        opened_at=record.opened_at,
        job_id=record.job_id,
        project_id=project,
        opened_by=record.opened_by,
        shared=True,
        **flags,
    )


def _record_hold(session, tool, fingerprint) -> Hold | None:
    """Issue 16: a hold another chat of this process opened on this project,
    read from its record. A row this process owns is checked on its
    registry evidence and, while open, adopted into this chat; one with no
    registry entry holds. A failed read holds (N4, H6). Rows of another
    process hold while their owner's lease runs (`clear_foreign`)."""
    binding = _current_binding()
    if binding is _UNREADABLE:
        return _unverified(tool, binding)
    project = _project_of(binding)
    if project is None:
        return None
    try:
        import security

        me = security.replica_id()
        records = job_hold_store.active(project, tool)
    except Exception as exc:  # noqa: BLE001 - an unreadable record denies (N4, H6)
        logger.warning("job hold records unreadable: %s", type(exc).__name__)
        return _unverified(tool, binding)
    for record in records:
        if record.owner_replica_id != me:
            try:
                gone = clear_foreign(record, time.time())
            except Exception as exc:  # noqa: BLE001 - unreadable evidence holds (N4)
                logger.warning(
                    "job hold %s evidence unreadable: %s", record.id, type(exc).__name__
                )
                gone = False
            if gone:
                continue
            hold = _from_record(record, binding, project, foreign=True)
            with session._lock:
                _holds(session)[(binding, tool)] = hold
            return hold
        with _REGISTRY_LOCK:
            hold = _REGISTRY.get(record.id)
        if hold is None:
            hold = _from_record(record, binding, project, orphan=True)
        else:
            try:
                ended = terminal(hold)
            except Exception:  # noqa: BLE001 - unreadable evidence is not terminal (N4)
                ended = False
            if ended:
                _forget(hold)
                continue
        with session._lock:
            _holds(session)[(binding, tool)] = hold
        return hold
    return None


def _viewer_opened(hold: Hold) -> bool:
    """Whether the acting user made the held call (H7: only they see its ids).
    A shared hold whose maker is unknown shows its ids to no one."""
    if not hold.shared:
        return True  # it never left the chat that made the call
    from services import chat_tools

    user = chat_tools.acting_user_id()
    return user is not None and user == hold.opened_by


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
    if hold is None or hold.fingerprint != fingerprint or hold.id not in _told(session):
        return None
    own = _viewer_opened(hold)
    return {
        "hold_id": hold.id,
        "fingerprint": fingerprint,
        "tool_use_id": hold.tool_use_id if own else "",
        "opened": hold.opened,
        "opened_at": hold.opened_at,
        "job_id": hold.job_id if own else None,
    }


def _pointer(hold: Hold) -> str:
    if hold.job_id and _viewer_opened(hold):
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
            _told(session).discard(hold.id)
        _forget(hold)
        return None
    if hold is None:
        from services import assistant_tasks

        # GK B (30) U1: the task's admitted next step is left to the handler's
        # own duplicate refusal and begin_step's record; session holds above
        # still apply to it (T1).
        if not assistant_tasks.admits_next_step(session, tool, args or {}):
            hold = _record_hold(session, tool, fingerprint) or _store_hold(
                tool, args, fingerprint
            )
    if hold is None:
        return None
    if hold.unverified:
        return PENDING, (
            f"Whether an earlier {tool} request on this project may still be "
            f"running could not be checked, so this one was not started. Try "
            f"again shortly."
        )
    if hold.fingerprint == fingerprint:
        if hold.from_store:
            return PENDING, (
                f"The solve queue already has this project's solve, queued or "
                f"running, so it was not queued again. {_pointer(hold)}, or stop "
                f"it with {_stop(hold)}."
            )
        with session._lock:
            _told(session).add(hold.id)
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
