# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14 (GK B (52) I2): a run's investment-case step.

`run_investment_case` starts a worker thread and returns `{"status":
"running"}`; the step is not done then. It waits on its own job, and a
follower settles it from the case's own record, the one whose `started_at`
the start receipt names, never one read after the call returned (the shape of
`run_eh`). Nothing polls: the model is released while the step waits (LC-45).
The case solves no LP, so the step reserves and spends no solve."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

TOOL = "run_investment_case"
STUDY = "investment_case"
POLL_SECONDS = 0.5
MAX_FOLLOW_SECONDS = 6 * 3600
SOLVES = 0  # the case values the solved dispatch; it solves no LP
_WAKE = threading.Event()  # never set in production; a poll interval (tests set it)
_FOLLOW_LOCK = threading.Lock()
_FOLLOWERS: dict[tuple, threading.Thread] = {}
TIMED_OUT = "The investment case passed its follow deadline; its outcome cannot be verified."


def is_ic_step(step) -> bool:
    return isinstance(step, dict) and step.get("tool") == TOOL


def _record(ctx):
    """The case's record on `ctx`, as plain values (None: no record)."""
    if ctx is None:
        return None
    with ctx.solver_state_lock:
        record = ctx.solver_state.get(STUDY)
        if not isinstance(record, dict):
            return None
        return {k: record.get(k) for k in ("status", "started_at", "error", "error_code", "thread")}


def outcome(record, started_at):
    """(native status, step status, error) from the step's own record. A
    replaced or missing record is unverifiable, never completed; a refused
    case (its inputs are the user's to fix) and a failed one are failed."""
    if not isinstance(record, dict) or started_at is None or record.get("started_at") != started_at:
        return "unknown", "uncertain", "The case's own record is gone or was replaced; it cannot be verified."
    status = record.get("status")
    if status == "done":
        return "done", "completed", None
    if status in ("refused", "failed", "aborted"):
        detail = record.get("error_code") or record.get("error") or ""
        return status, "failed", f"The investment case ended {status}: {str(detail)[:200]}"
    return "unknown", "uncertain", "The case's outcome cannot be verified."


def _alive(record) -> bool:
    worker = record.get("thread")
    return not (isinstance(worker, threading.Thread) and worker.ident is not None and not worker.is_alive())


def wait(ticket, job_id, session_id, ctx, begun) -> bool:
    """The run's case step waits on its job and a follower settles it from
    its own record. True when the step now waits; a chat plan's step keeps
    its own rules, and a start receipt with no `started_at` is not followed."""
    from harness import runner
    from services import assistant_tasks

    if begun is None:
        return False
    with assistant_tasks._LOCK:
        _, task = assistant_tasks._read(ticket[0])
    if not task.get("run"):
        return False
    path = assistant_tasks.await_job(ticket, job_id, session_id)
    if path is None:
        return False

    def mark(task):
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if step is None or step.get("attempt_id") != ticket[2] or step["status"] != "waiting":
            return False
        step["job"].update(kind=STUDY, started_at=begun, call_id=ticket[2], deadline_at=time.time() + MAX_FOLLOW_SECONDS)
    runner._update(path, mark)
    with _FOLLOW_LOCK:
        _start_follower(path, ticket, job_id, ctx, begun)
    return True


def _start_follower(path, ticket, job_id, ctx, begun):
    for key in [k for k, t in _FOLLOWERS.items() if not t.is_alive()]:
        _FOLLOWERS.pop(key, None)
    thread = threading.Thread(target=_follow, args=(path, ticket, job_id, ctx, begun),
                              name=f"run-ic-{str(job_id)[-8:]}", daemon=True)
    _FOLLOWERS[tuple(ticket)] = thread
    thread.start()
    return thread


def following(ticket) -> bool:
    thread = _FOLLOWERS.get(tuple(ticket))
    return thread is not None and thread.is_alive()


def _follow(path, ticket, job_id, ctx, begun):
    try:
        deadline = time.monotonic() + MAX_FOLLOW_SECONDS
        record = _record(ctx)
        while (record is not None and record.get("started_at") == begun and record.get("status") == "running"
               and _alive(record) and time.monotonic() < deadline):
            _WAKE.wait(POLL_SECONDS)
            record = _record(ctx)
        settle(path, ticket, job_id, record, begun)
    except Exception:  # noqa: BLE001 — a lost settlement leaves the step waiting, never re-runs
        logger.exception("run investment case %s could not be settled", job_id)


def settle(path, ticket, job_id, record, begun):
    """Settle the step from `record`; the case is never started again."""
    from harness import runner
    from services import assistant_tasks

    native, status, error = outcome(record, begun)
    result = {"study": STUDY, "started_at": begun, "solves": SOLVES} if status == "completed" else None
    view = assistant_tasks.settle_job(path, ticket, job_id, native, status, result)
    if view is not None and error:
        runner._update(path, lambda t: next(s for s in t["steps"] if s["id"] == ticket[1]).update(error=error))
    return view


def recover(path, ctx, *, instance, now=None, lapsed_ok=False):
    """Each tick, on a takeover and at boot: each waiting case step with no
    live follower here is settled uncertain past its `deadline_at`, else
    followed again while its own record still runs on `ctx`, else settled
    from its own record (a missing one is uncertain). Only by the run's lease
    holder, or on a lapsed lease at boot. Returns the steps it settled."""
    from harness import runner
    from services import assistant_tasks

    now = time.time() if now is None else now
    task = runner._read(path) or {}
    lease = (task.get("run") or {}).get("runner") or {}
    if lease.get("lease_owner") != instance and not (lapsed_ok and lease.get("lease_until", 0) <= now):
        return 0
    mine = ctx is not None and str(getattr(ctx, "project_uuid", "")) == task.get("project_id")
    swept = 0
    for step in task.get("steps", []):
        job = step.get("job") or {}
        if step.get("status") != "waiting" or job.get("kind") != STUDY:
            continue
        ticket = (task["task_id"], step["id"], step.get("attempt_id"))
        job_id, begun = job.get("job_id"), job.get("started_at")
        record = None
        with _FOLLOW_LOCK:
            if following(ticket):
                continue
            deadline = job.get("deadline_at")
            late = isinstance(deadline, (int, float)) and now >= deadline
            if not late:
                record = _record(ctx) if mine else None
                if (begun is not None and record is not None and record.get("started_at") == begun
                        and record.get("status") == "running" and _alive(record)):
                    _start_follower(path, ticket, job_id, ctx, begun)
                    continue
        if late:
            if assistant_tasks.settle_job(path, ticket, job_id, "timed_out", "uncertain") is not None:
                runner._update(path, lambda t, sid=step["id"]: next(s for s in t["steps"] if s["id"] == sid).update(
                    error=TIMED_OUT))
                swept += 1
            continue
        if settle(path, ticket, job_id, record, begun) is not None:
            swept += 1
    return swept
