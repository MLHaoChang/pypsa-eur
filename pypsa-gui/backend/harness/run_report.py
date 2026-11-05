"""Stage 7 of a harness run: the run's own report job (study harness issue
07, design §3 stage 7).

The runner alone starts it (`start`), on the run's bound context, and it is
the only caller that passes `strict=True` and `run_binding=(task path, lease
owner)` to the report job, plus the `harness_run` evidence source (GK B's
stage-7 ruling). The route and the chat tool never set either: a human
request through them is never strict and never charged to a run, even while
a run sits at this stage. A chat call of `generate_report` never claims the
step either (`assistant_tasks.begin_step` leaves native steps alone).

`settle` completes the waiting step from the job record on the run's
context; `status` and `unsupported_listed` are the stage's gate facts, read
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
    return next((s for s in reversed(task.get("steps") or []) if s.get("native") == KIND), None)


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
    return {"task": task, "envelope": run_envelope.read(path), "chapters": list(profile.get("chapters") or []),
            "coverage": run_handoff.coverage(task, profile, None)}


def start(path, ctx, step, *, instance) -> str:
    """Start the run's report job (the runner, on the run's bound context).
    Returns "report" when it started, else the refusal reason."""
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
    task = json.loads(path.read_text())
    try:
        project = chat_tools._report_project()
        if str(project.uuid) != task["project_id"] or str(getattr(ctx, "project_uuid", "")) != task["project_id"]:
            _stop(path, step["id"], "project_changed", "the bound project is not the run's")
            return "project_changed"
        profile = run_profiles.resolve(task["run"]["profile"])
        llm = report_jobs._resolve_profile()
        provider = report_jobs._provider_or_400(llm)
        evidence, figures = report_jobs._evidence_for(project, harness_run=harness_source(path, task, profile))
        report_id = report_jobs._start(project, evidence=evidence, figure_pngs=figures, profile=llm,
                                       provider=provider, language=LANGUAGE, strict=True,
                                       run_binding=(path, instance))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        reason = str(detail.get("error_kind") or "report_refused")
        _stop(path, step["id"], reason, str(detail.get("message") or "")[:240])
        return reason

    def wait(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s.get("attempt_id") != attempt or s["status"] != "running":
            return False
        s["status"] = "waiting"
        s["job"] = {"kind": KIND, "report_id": report_id, "started_at": time.time()}
        if t["status"] != "cancelled":
            t["status"] = "waiting_job"
    runner._update(path, wait)
    return KIND


def settle(path, ctx) -> str | None:
    """Complete a waiting report step once its job ended, from the job record
    on the run's context: `job.outcome` is the record's status. A record that
    is no longer this job's (another job took the slot) settles `lost`."""
    from harness import runner
    path = Path(path)
    task = json.loads(path.read_text())
    step = report_step(task)
    if step is None or step["status"] != "waiting":
        return None
    with ctx.solver_state_lock:
        record = ctx.solver_state.get("report_job")
        record = dict(record) if isinstance(record, dict) else None
    job = step["job"]
    if record is not None and record.get("report_id") == job["report_id"]:
        if record.get("status") not in _TERMINAL:
            return None
        outcome, error = record["status"], record.get("error")
    else:
        outcome, error = "lost", "the job record is gone or another job took the slot"

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "waiting":
            return False
        s["job"].update(outcome=outcome, version=(record or {}).get("version"), finished_at=time.time())
        s["status"] = "completed"
        if error:
            s["error"] = str(error)[:480]
        if t["status"] == "waiting_job":
            t["status"] = "running"
        if t["run"]["status"] == "waiting_job":
            t["run"]["status"] = "running"
    runner._update(path, done)
    return outcome


def saved(task: dict, project_dir):
    """The run's saved report document, or None (no completed job, or the
    document cannot be read)."""
    from services.reports import store
    step = report_step(task)
    job = (step or {}).get("job") or {}
    if step is None or step["status"] != "completed" or job.get("outcome") != "done" or project_dir is None:
        return None
    try:
        return store.load_report(Path(project_dir), job["report_id"], job.get("version"))
    except Exception:  # noqa: BLE001 -- an unreadable report is no evidence (C3)
        logger.info("run report %s could not be read", job.get("report_id"), exc_info=True)
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
