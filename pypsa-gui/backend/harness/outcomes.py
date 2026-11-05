"""Verified outcomes of blocking foreground jobs (assistant assurance issue 04, S1).

`classify` is a pure function. Its two inputs are the call's own submission
record, captured when the loop minted the job handle, and an independent read
of live state taken after the call: the solver record bound to the handle and
the run's evidence marker. It never reads the handler's returned status
fields (GK B J1). Every unreadable, missing or other-identity input gives
"uncertain", never "completed" (#1079, J2/J3).

Scope: run_simulation, run_ac_pf_stage, wait_for_job on an `fg:` handle and
abort_simulation. wait_for_job on study (`ad:`), report (`rpt:`) or queue
jobs reads "uncertain" with a pending reason; nonblocking jobs wait for
live-voice issue 11.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

VERSION = 1
JOB_TOOLS = frozenset(
    {"run_simulation", "run_ac_pf_stage", "wait_for_job", "abort_simulation"}
)
OUTCOMES = (
    "submitted",
    "running",
    "completed",
    "partial",
    "failed",
    "cancel_requested",
    "cancelled",
    "uncertain",
)

_LOCK = threading.Lock()
_SUBMISSIONS: OrderedDict[str, dict] = OrderedDict()
_MAX = 512


def note_submission(job_id: str, ctx: Any, token: dict | None) -> None:
    """Record the call's own identity when the loop mints a foreground handle.

    `token` is run_evidence.start_foreground_run's return; None (an unsaved
    scratch network) leaves no run marker, so the job can never read completed.
    """
    from harness import job_adapters as ja

    with ja._LOCK:
        handle = ja._FOREGROUND.get(job_id)
        binding = ja._ACTIVITY_BINDINGS.get(job_id)
    record = {
        "job_id": job_id,
        "tool_use_id": handle.tool_use_id if handle else None,
        "project": str(handle.project_uuid) if handle and handle.project_uuid else None,
        "ctx": binding[0] if binding else None,
        "worker": binding[2] if binding else None,
        "run_id": token.get("run_id") if token else None,
        "started_at": token.get("started_at") if token else None,
        "evidence": (token["root"], token["project_id"]) if token else None,
    }
    with _LOCK:
        _SUBMISSIONS[job_id] = record
        while len(_SUBMISSIONS) > _MAX:
            _SUBMISSIONS.popitem(last=False)


def submission(job_id: Any) -> dict | None:
    with _LOCK:
        return (
            dict(_SUBMISSIONS[job_id])
            if isinstance(job_id, str) and job_id in _SUBMISSIONS
            else None
        )


def _by_tool_use(tool_use_id: str) -> dict | None:
    with _LOCK:
        found = [r for r in _SUBMISSIONS.values() if r["tool_use_id"] == tool_use_id]
    return dict(found[-1]) if found else None


def _bound_to_live_record() -> dict | None:
    """The submission whose worker is the live solver record's own (abort's target)."""
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        worker = ctx.solver_state.get("thread")
    with _LOCK:
        found = [
            r
            for r in _SUBMISSIONS.values()
            if worker is not None and r["worker"] is worker and r["ctx"] == id(ctx)
        ]
    return dict(found[-1]) if found else None


def read_live(expected: dict) -> dict:
    """Independent read after the call. Raises nothing: a failure is a read error."""
    try:
        from services import run_evidence
        from services.pypsa_service import PyPSAService

        ctx = PyPSAService.get_active_context()
        with ctx.solver_state_lock:
            state = ctx.solver_state
            live = {
                k: state.get(k)
                for k in (
                    "status",
                    "kind",
                    "ac_pf_converged_count",
                    "ac_pf_total_snapshots",
                )
            }
            live["worker"] = state.get("thread")
        live["ctx"] = id(ctx)
        live["project"] = str(ctx.project_uuid) if ctx.project_uuid else None
        run = None
        if expected.get("evidence") and expected.get("run_id"):
            root, project_id = expected["evidence"]
            run = run_evidence._load_run(root, project_id, expected["run_id"])
        live["run"] = (
            {k: run.get(k) for k in ("run_id", "job_id", "started_at")}
            if isinstance(run, dict)
            else None
        )
        return live
    except Exception as exc:  # noqa: BLE001 -- an unreadable outcome is uncertain (#1079)
        return {"error": type(exc).__name__}


def _counts(read: dict) -> tuple[int, int] | None:
    ok, total = read.get("ac_pf_converged_count"), read.get("ac_pf_total_snapshots")
    if (
        type(ok) is not int
        or type(total) is not int
        or total < 1
        or not 0 <= ok <= total
    ):
        return None
    return ok, total


def classify(
    tool: str, expected: dict | None, read: dict | None, *, abort: bool = False
) -> dict:
    """Pure: the outcome of one job call from its own submission and an independent read."""

    def out(outcome, reason=None):
        return {"outcome": outcome, "reason": reason}

    if expected is None:
        return out("uncertain", "no_submission")
    if not isinstance(read, dict) or "error" in read:
        return out("uncertain", "read_failed")
    if expected.get("ctx") is None or read.get("ctx") != expected["ctx"]:
        return out("uncertain", "other_context")
    if expected.get("project") is None or read.get("project") != expected["project"]:
        return out("uncertain", "other_project")
    if expected.get("worker") is None or read.get("worker") is not expected["worker"]:
        return out("uncertain", "other_job")
    run = read.get("run")
    if expected.get("run_id") is None or not isinstance(run, dict):
        return out("uncertain", "no_run_record")
    if (
        run.get("run_id") != expected["run_id"]
        or run.get("job_id") != expected["job_id"]
    ):
        return out("uncertain", "other_run")
    if run.get("started_at") != expected.get("started_at"):
        return out("uncertain", "other_start")
    status = read.get("status")
    if status == "running":
        return out("cancel_requested" if abort else "running")
    if status == "queued":
        return out("cancel_requested" if abort else "submitted")
    if status == "aborted":
        return out("cancelled")
    if status == "failed":
        return out("failed")
    if status == "completed":
        if read.get("kind") != "ac_pf":
            return out("completed")
        counts = _counts(read)
        if counts is None:
            return out("uncertain", "convergence_unknown")
        return (
            out("completed")
            if counts[0] == counts[1]
            else out("partial", "snapshots_not_converged")
        )
    return out("uncertain", "status_unknown")


def project(tool: str, args: dict, tool_use_id: str) -> dict:
    """The `_outcome` block for one job tool result (ordinary turns)."""
    if tool == "wait_for_job":
        job_id = args.get("job_id")
        if not (isinstance(job_id, str) and job_id.startswith("fg:")):
            return {
                "version": VERSION,
                "outcome": "uncertain",
                "job_id": job_id if isinstance(job_id, str) else None,
                "reason": "adapter_pending",
            }
        expected = submission(job_id)
    elif tool == "abort_simulation":
        expected = _bound_to_live_record()
    else:
        expected = _by_tool_use(tool_use_id)
    verdict = classify(
        tool,
        expected,
        read_live(expected) if expected else None,
        abort=tool == "abort_simulation",
    )
    return {
        "version": VERSION,
        **verdict,
        "job_id": expected["job_id"] if expected else None,
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _SUBMISSIONS.clear()
