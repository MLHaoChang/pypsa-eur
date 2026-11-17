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
import re
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
TIMED_OUT = (
    "The investment case passed its follow deadline; its outcome cannot be verified."
)
# Study harness 36 (GK B (60) B1, follow-up (a)): a profile action may carry
# `on_end: chapter_gap` (eh_site@4). Its case that ends refused, failed or
# aborted, or that is still running at the run's wall time (the envelope's
# `expires_at`), keeps the step `failed` with its typed error and is judged by
# the report (`judged_by`), so the run goes on to a report that lists the
# investment chapter as a gap with that error. An unverifiable step stays
# `uncertain` and stops the run, as before. A step without it is unchanged.
ON_END, CHAPTER_GAP = "on_end", "chapter_gap"
JUDGED = "report"
# O4: a gap step's error is its typed kind and fixed user words, plus the
# case's own error code when it is one (never its raw error text or a path).
GAP_KINDS = {
    "refused": ("ic_case_refused", "the investment case was refused for its inputs"),
    "failed": ("ic_case_failed", "the investment case failed"),
    "aborted": (
        "ic_case_aborted",
        "the investment case was stopped before it finished",
    ),
}
WALL_TIME_KIND = "ic_wall_time"
WALL_TIME = (
    f"{WALL_TIME_KIND}: the investment case did not finish within the run's wall time."
)
BUDGET_KIND = "ic_budget"  # `run_envelope.IC_BUDGET_KIND` (A2, A5)
BUDGET = (
    f"{BUDGET_KIND}: the run's solves left do not cover the investment case's baseline "
    "solves, so it did not run."
)
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def gap_error(native, record) -> str:
    """The typed error of a gap step whose case ended `native`."""
    kind, words = GAP_KINDS[native]
    code = (record or {}).get("error_code")
    code = f" ({code})" if isinstance(code, str) and _CODE.fullmatch(code) else ""
    return f"{kind}{code}: {words}, so this chapter is not established."


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
        return {
            k: record.get(k)
            for k in ("status", "started_at", "error", "error_code", "thread")
        }


def outcome(record, started_at):
    """(native status, step status, error) from the step's own record. A
    replaced or missing record is unverifiable, never completed; a refused
    case (its inputs are the user's to fix) and a failed one are failed."""
    if (
        not isinstance(record, dict)
        or started_at is None
        or record.get("started_at") != started_at
    ):
        return (
            "unknown",
            "uncertain",
            "The case's own record is gone or was replaced; it cannot be verified.",
        )
    status = record.get("status")
    if status == "done":
        return "done", "completed", None
    if status in ("refused", "failed", "aborted"):
        detail = record.get("error_code") or record.get("error") or ""
        return (
            status,
            "failed",
            f"The investment case ended {status}: {str(detail)[:200]}",
        )
    return "unknown", "uncertain", "The case's outcome cannot be verified."


def _alive(record) -> bool:
    worker = record.get("thread")
    return not (
        isinstance(worker, threading.Thread)
        and worker.ident is not None
        and not worker.is_alive()
    )


def _gap_step(task, step_id) -> bool:
    step = next((s for s in task.get("steps", []) if s["id"] == step_id), None)
    return step is not None and step.get(ON_END) == CHAPTER_GAP


def _wall_deadline(path):
    """The run's wall-time end (the granted envelope's `expires_at`, epoch
    seconds), or None with no granted envelope."""
    from services import run_envelope

    envelope = run_envelope.read(path) or {}
    if envelope.get("state") != "granted" or not envelope.get("expires_at"):
        return None
    return run_envelope._epoch(envelope["expires_at"])


def chapter_gaps(task) -> dict[str, str]:
    """The report chapters this run's own `chapter_gap` case steps leave as
    gaps: {section id: the step's typed error}, for a live (not superseded)
    step that settled anything but completed."""
    from services.reports.chapters import INVESTMENT

    out = {}
    for step in task.get("steps") or []:
        if (
            is_ic_step(step)
            and step.get(ON_END) == CHAPTER_GAP
            and not step.get("superseded")
            and step.get("status") in ("failed", "uncertain")
        ):
            out[INVESTMENT] = str(
                step.get("error") or f"the investment case step {step['status']}"
            )
    return out


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

    deadline = time.time() + MAX_FOLLOW_SECONDS
    wall = _wall_deadline(path) if _gap_step(task, ticket[1]) else None
    walled = wall is not None and wall < deadline  # B1: the run's wall time ends first

    def mark(task):
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if (
            step is None
            or step.get("attempt_id") != ticket[2]
            or step["status"] != "waiting"
        ):
            return False
        step["job"].update(
            kind=STUDY,
            started_at=begun,
            call_id=ticket[2],
            deadline_at=wall if walled else deadline,
            **({"deadline_kind": WALL_TIME_KIND} if walled else {}),
        )

    runner._update(path, mark)
    with _FOLLOW_LOCK:
        _start_follower(path, ticket, job_id, ctx, begun)
    return True


def _start_follower(path, ticket, job_id, ctx, begun):
    for key in [k for k, t in _FOLLOWERS.items() if not t.is_alive()]:
        _FOLLOWERS.pop(key, None)
    thread = threading.Thread(
        target=_follow,
        args=(path, ticket, job_id, ctx, begun),
        name=f"run-ic-{str(job_id)[-8:]}",
        daemon=True,
    )
    _FOLLOWERS[tuple(ticket)] = thread
    thread.start()
    return thread


def following(ticket) -> bool:
    thread = _FOLLOWERS.get(tuple(ticket))
    return thread is not None and thread.is_alive()


def _follow(path, ticket, job_id, ctx, begun):
    try:
        deadline = time.monotonic() + MAX_FOLLOW_SECONDS
        wall = _wall_of(path, ticket)
        record = _record(ctx)
        while (
            record is not None
            and record.get("started_at") == begun
            and record.get("status") == "running"
            and _alive(record)
            and time.monotonic() < deadline
            and (wall is None or time.time() < wall)
        ):
            _WAKE.wait(POLL_SECONDS)
            record = _record(ctx)
        if wall is not None and time.time() >= wall and _running(record, begun):
            settle_walled(path, ticket, job_id)
            return
        settle(path, ticket, job_id, record, begun)
    except Exception:  # noqa: BLE001 — a lost settlement leaves the step waiting, never re-runs
        logger.exception("run investment case %s could not be settled", job_id)


def _wall_of(path, ticket):
    """B1: the step's deadline (epoch seconds) when it is the run's wall
    time, else None."""
    from harness import runner

    task = runner._read(path) or {}
    step = next((s for s in task.get("steps", []) if s["id"] == ticket[1]), None)
    job = (step or {}).get("job") or {}
    deadline = job.get("deadline_at")
    if job.get("deadline_kind") != WALL_TIME_KIND or not isinstance(
        deadline, (int, float)
    ):
        return None
    return deadline


def _running(record, begun) -> bool:
    return (
        isinstance(record, dict)
        and record.get("started_at") == begun
        and record.get("status") == "running"
    )


def _judge(path, step_id, error, gap=None):
    """The step's error; a failed `chapter_gap` step takes its typed `gap`
    error and is judged by the report (the run goes on)."""
    from harness import runner

    def mark(task):
        step = next(s for s in task["steps"] if s["id"] == step_id)
        step["error"] = error
        if step.get(ON_END) == CHAPTER_GAP and step["status"] == "failed":
            step["error"] = gap or error
            step["judged_by"] = JUDGED

    runner._update(path, mark)


def budget_gap(path, step_id) -> bool:
    """A2: admission refused the gap step `ic_budget` (no LP ran): the step
    fails with that kind and is judged by the report. False for any other
    step, which keeps its card."""
    from harness import runner

    done = []

    def mark(task):
        step = next((s for s in task["steps"] if s["id"] == step_id), None)
        if (
            step is None
            or not is_ic_step(step)
            or step.get(ON_END) != CHAPTER_GAP
            or step["status"] in ("completed", "failed", "uncertain")
        ):
            return False
        step.update(status="failed", error=BUDGET, judged_by=JUDGED)
        done.append(step_id)

    runner._update(path, mark)
    return bool(done)


def settle_walled(path, ticket, job_id):
    """B1: a case still running at the run's wall time ends the step failed
    with its typed kind, never completed and never left waiting."""
    from services import assistant_tasks

    view = assistant_tasks.settle_job(path, ticket, job_id, "timed_out", "failed")
    if view is not None:
        _judge(path, ticket[1], WALL_TIME)
    return view


def reserved(path) -> int:
    """The solves the run's grant reserved for its latest admitted case (the
    baseline, study harness 36 A2; one case runs at a time), else `SOLVES`:
    the case itself solves no LP."""
    from services import run_envelope

    try:
        audit = (run_envelope._load(path) or {}).get("audit") or []
    except Exception:  # noqa: BLE001 -- no envelope to read: nothing was reserved
        return SOLVES
    last = next(
        (
            a
            for a in reversed(audit)
            if a.get("event") == "used" and a.get("tool") == TOOL
        ),
        None,
    )
    solves = (last or {}).get("solves")
    return solves if type(solves) is int else SOLVES


def settle(path, ticket, job_id, record, begun):
    """Settle the step from `record`; the case is never started again."""
    from services import assistant_tasks

    native, status, error = outcome(record, begun)
    result = (
        {"study": STUDY, "started_at": begun, "solves": reserved(path)}
        if status == "completed"
        else None
    )
    view = assistant_tasks.settle_job(path, ticket, job_id, native, status, result)
    if view is not None and error:
        _judge(
            path,
            ticket[1],
            error,
            gap_error(native, record) if native in GAP_KINDS else None,
        )
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
    if lease.get("lease_owner") != instance and not (
        lapsed_ok and lease.get("lease_until", 0) <= now
    ):
        return 0
    mine = ctx is not None and str(getattr(ctx, "project_uuid", "")) == task.get(
        "project_id"
    )
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
            walled = late and job.get("deadline_kind") == WALL_TIME_KIND
            if not late:
                record = _record(ctx) if mine else None
                if (
                    begun is not None
                    and record is not None
                    and record.get("started_at") == begun
                    and record.get("status") == "running"
                    and _alive(record)
                ):
                    _start_follower(path, ticket, job_id, ctx, begun)
                    continue
        if walled:  # B1: the run's wall time ended with the case still out
            if settle_walled(path, ticket, job_id) is not None:
                swept += 1
            continue
        if late:
            if (
                assistant_tasks.settle_job(
                    path, ticket, job_id, "timed_out", "uncertain"
                )
                is not None
            ):
                runner._update(
                    path,
                    lambda t, sid=step["id"]: next(
                        s for s in t["steps"] if s["id"] == sid
                    ).update(error=TIMED_OUT),
                )
                swept += 1
            continue
        if settle(path, ticket, job_id, record, begun) is not None:
            swept += 1
    return swept
