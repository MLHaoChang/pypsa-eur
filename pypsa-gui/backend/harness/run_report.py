"""Stage 7 of a harness run: the run's own report job (study harness issue
07, design §3 stage 7).

The runner alone starts it (`start`), on the run's bound context, and it is
the only caller that passes `run_binding=(task path, lease owner)` to the
report job, plus the `harness_run` evidence source (GK B's stage-7 ruling),
and it passes `strict=True`. The human route never sets either: a human
request through it is never strict and never charged to a run, even while a
run sits at this stage. The chat tools run strict through the in-process
`report_job.assistant_strict` switch (AA-05 S4a), never charged to a run. A chat call of `generate_report` never claims the
step either (`assistant_tasks.begin_step` leaves native steps alone).

`settle` settles the waiting step from the job record on the run's
context: only a job that ended `done` completes it; a missing record, one
another job replaced, or a job that ended refused, aborted or failed fails
the step and stops the run for the user (GK B B1-c); `status` and `unsupported_listed` are the stage's gate facts, read
from the saved report and failing closed on anything unreadable (GK B C3).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

KIND = "report"
LANGUAGE = "en"
_TERMINAL = ("done", "aborted", "failed")
CHAPTER_GAP = "Chapter not established: "


def report_step(task: dict) -> dict | None:
    """The run's latest report step (a stage entered again numbers on)."""
    return next(
        (s for s in reversed(task.get("steps") or []) if s.get("native") == KIND), None
    )


def _stop(path, step_id, reason, detail=None):
    """A start that did not start: the step failed, the stage failed with
    `reason`, the run needs the user."""
    from harness import runner

    def change(task):
        step = next(s for s in task["steps"] if s["id"] == step_id)
        step["status"] = "failed"
        step["error"] = (f"{reason}: {detail}" if detail else reason)[:480]
        run = task["run"]
        run["stages"]["report"].update(status="failed", reason=reason)
        if run["status"] in ("running", "waiting_job"):
            run["status"], task["status"] = "needs_user", "needs_review"

    runner._update(path, change)


def harness_source(path, task: dict, profile: dict) -> dict:
    """The `harness_run` evidence source: the run record, its envelope, the
    chapters the profile asks for and the request coverage so far (HH-09A)."""
    from harness import run_handoff
    from services import run_envelope

    return {
        "task": task,
        "envelope": run_envelope.read(path),
        "chapters": list(profile.get("chapters") or []),
        "coverage": run_handoff.coverage(task, profile, None),
    }


def start(path, ctx, step, *, instance, summaries: dict[str, list[str]] | None = None) -> str:
    """Start the run's report job (the runner, on the run's bound context).
    Returns "report" when it started, else the refusal reason. `summaries`
    (AA-05 Y1/Y2) is a caller's summary declaration; the runner passes none."""
    from fastapi import HTTPException

    from harness import runner
    from routers import report_jobs
    from services import chat_tools, run_profiles

    path = Path(path)
    attempt = uuid.uuid4().hex
    claimed = {}

    def claim(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "pending":
            return False
        s.update(status="running", attempt_id=attempt)
        claimed["ok"] = True

    runner._update(path, claim)
    if not claimed:
        return "claimed_elsewhere"
    try:
        task = json.loads(path.read_text())
        project = chat_tools._report_project()
        if (
            str(project.uuid) != task["project_id"]
            or str(getattr(ctx, "project_uuid", "")) != task["project_id"]
        ):
            _stop(
                path,
                step["id"],
                "project_changed",
                "the bound project is not the run's",
            )
            return "project_changed"
        profile = run_profiles.resolve(task["run"]["profile"])
        llm = report_jobs._resolve_profile()
        provider = report_jobs._provider_or_400(llm)
        evidence, figures = report_jobs._evidence_for(
            project, harness_run=harness_source(path, task, profile)
        )
        report_id = report_jobs._start(
            project,
            evidence=evidence,
            figure_pngs=figures,
            profile=llm,
            provider=provider,
            language=LANGUAGE,
            strict=True,
            summaries=summaries,
            run_binding=(path, instance),
        )
    except HTTPException as exc:
        detail = (
            exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        )
        reason = str(detail.get("error_kind") or "report_refused")
        _stop(path, step["id"], reason, str(detail.get("message") or "")[:240])
        return reason
    except Exception as exc:  # noqa: BLE001 -- B1-c: a launch that did not start fails the step, never strands it running
        logger.exception("report job launch failed for run %s", path.stem)
        _stop(path, step["id"], "report_start_failed", type(exc).__name__)
        return "report_start_failed"

    def wait(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s.get("attempt_id") != attempt or s["status"] != "running":
            return False
        s["status"] = "waiting"
        s["job"] = {"kind": KIND, "report_id": report_id, "started_at": time.time()}
        if t["status"] != "cancelled":
            t["status"] = "waiting_job"

    try:
        runner._update(path, wait)
    except Exception as exc:  # noqa: BLE001 -- B1-c: a started job's step is never left running
        logger.exception(
            "report job %s started but its step was not recorded", report_id
        )
        _stop(
            path,
            step["id"],
            "report_start_failed",
            f"{type(exc).__name__}; job {report_id}",
        )
        return "report_start_failed"
    return KIND


def settle(path, ctx) -> str | None:
    """Settle a waiting report step once its job ended, from the job record
    on the run's context: `job.outcome` is the record's status. Only `done`
    completes the step; anything else, or a record that is gone or is no
    longer this job's (`lost`), fails it and the run needs the user (B1-c).
    The job's own sends were settled or released by `RunSends.close` (C4)."""
    with ctx.solver_state_lock:
        record = ctx.solver_state.get("report_job")
        record = dict(record) if isinstance(record, dict) else None
    return settle_record(path, record)


def _foreign(task, step, record, owner) -> bool:
    """From the job's own thread (`owner` set): a step that waits on another
    job, or a run another runner instance holds, is not this job's to settle."""
    if owner is None:
        return False
    lease = (task.get("run") or {}).get("runner") or {}
    return (record or {}).get("report_id") != step["job"].get("report_id") or lease.get(
        "lease_owner"
    ) != owner


def settle_record(path, record: dict | None, *, owner: str | None = None) -> str | None:
    """`settle` from a job record in hand. The job calls it itself as it
    ends (`report_job.run_report_job`, `owner` its runner instance), so its
    outcome lands on the step even when the run already stopped (the budget,
    a pause) and the runner no longer ticks it; it then settles only its own
    `report_id` under its own runner instance, else does nothing (GK B). The
    runner's tick calls it too (no `owner`). Settling twice is a no-op."""
    from harness import runner

    path = Path(path)
    task = json.loads(path.read_text())
    step = report_step(task)
    if (
        step is None
        or step["status"] != "waiting"
        or _foreign(task, step, record, owner)
    ):
        return None
    job = step["job"]
    if record is not None and record.get("report_id") == job["report_id"]:
        if record.get("status") not in _TERMINAL:
            return None
        outcome, error = record["status"], record.get("error")
        if record.get("refused"):
            outcome = "refused"  # C4: a send the run's reservation refused; the report is incomplete
    else:
        outcome, error = "lost", "the job record is gone or another job took the slot"
        record = None  # another job's record is no evidence about this one

    refused = (record or {}).get("refused")

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "waiting" or _foreign(t, s, record, owner):
            return False
        s["job"].update(
            outcome=outcome,
            version=(record or {}).get("version"),
            finished_at=time.time(),
        )
        if outcome == "done":
            s["status"] = "completed"
            if t["status"] == "waiting_job":
                t["status"] = "running"
            if t["run"]["status"] == "waiting_job":
                t["run"]["status"] = "running"
            return
        s["status"] = "failed"
        s["error"] = str(f"refused: {refused}" if refused else error or outcome)[:480]
        run = t["run"]
        run["stages"]["report"].update(status="failed", reason="report_" + outcome)
        if run["status"] in ("running", "waiting_job"):
            run["status"], t["status"] = "needs_user", "needs_review"

    runner._update(path, done)
    return outcome


def saved(task: dict, project_dir):
    """The run's saved report document, or None (no completed job, or the
    document cannot be read)."""
    from services.reports import store

    step = report_step(task)
    job = (step or {}).get("job") or {}
    if (
        step is None
        or step["status"] != "completed"
        or job.get("outcome") != "done"
        or project_dir is None
    ):
        return None
    try:
        return store.load_report(
            Path(project_dir), job["report_id"], job.get("version")
        )
    except Exception:  # noqa: BLE001 -- an unreadable report is no evidence (C3)
        logger.info(
            "run report %s could not be read", job.get("report_id"), exc_info=True
        )
        return None


def status(task: dict, project_dir) -> str | None:
    """`report.status`: "ready" only when the run's own job finished `done`
    and its document reads back; the job's own outcome otherwise; None
    (not evaluated) with no completed job or an unreadable document."""
    step = report_step(task)
    job = (step or {}).get("job") or {}
    if step is None or step["status"] != "completed" or not job.get("outcome"):
        return None
    if job["outcome"] != "done":
        return job["outcome"]
    return "ready" if saved(task, project_dir) is not None else None


def unsupported_listed(task: dict, project_dir, profile: dict) -> bool | None:
    """`report.unsupported_listed` (SC-15): every chapter the profile asks for
    is established in the saved report or named in its harness chapter's
    gaps table. None when the report, its harness chapter or the gaps table
    cannot be read."""
    from services.reports import harness_chapter as H
    from services.reports.evidence import SECTION_TITLES

    doc = saved(task, project_dir)
    if doc is None:
        return None
    sections = {s.section_id: s for s in doc.sections}
    table = doc.tables.get("harness_gaps")
    if H.SECTION_ID not in sections or table is None:
        return None
    gaps = {str(row[0]) for row in table.rows if row}
    for sid in profile.get("chapters") or []:
        sec = sections.get(sid)
        if sec is not None and sec.status == "ok":
            continue
        title = SECTION_TITLES.get(sid, sid)
        if not any(g.startswith(CHAPTER_GAP + title) for g in gaps):
            return False
    return True
