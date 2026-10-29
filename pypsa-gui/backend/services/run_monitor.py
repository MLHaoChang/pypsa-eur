"""The run monitor: what the companion's plan card reads and does for a
harness run (study harness issue 08, design §10).

Every entry point is a user's click on the plan card, never a tool call, and
reads the task as `assistant_tasks._read` does: the caller's own task in the
active project, or `task_not_found` (an unknown or foreign task, one in a
project the caller was removed from or that was deleted, are told apart by
nothing, SC-16, GK B (1)). `present`, `resume` and `confirm` re-check at the
click, with nothing cached from `view` (GK B (2)): the owner and project, the
edit right and lock, the envelope's expiry and the budget.

* `view`: the run's `get_task` view and its envelope, read only (the runner
  advances the run, not this read), plus `monitor`: budgets used, the card
  the run waits on, and whether "Approve and run unattended" can work here.
* `present`: the user has the plan card open; a card the run waits on is
  issued now, its TTL starting at display (`runner.present`). One live card
  per run: presenting again re-issues it and the first token is refused. A
  paused or stopped run is refused (`run_paused_or_stopped`).
* `confirm`: the checks before the plan card answers the run's card on the
  run's own session (`/chat/run:<id>/confirm`); a paused or stopped run is
  refused.
* `pause` and `resume`: the run stops at the next tick (a step in flight
  finishes) and goes on from where it stopped. A granted envelope stays
  granted and its expiry runs on; one that expired meanwhile ends at the
  Resume, and the run stays paused (`envelope_expired`), as the runner
  pauses it.
* `stop`: `cancel_task`, which revokes the envelope. Nothing is reverted:
  the edits applied so far and the results so far are kept.

Pause and stop drop the run's live card (`runner.drop_card`): its token is
refused from then on and the step it waited on stays to do.
"""
from __future__ import annotations

import copy

from fastapi import HTTPException

from services import assistant_tasks

_PAUSABLE = ("running", "waiting_job", "needs_user")
_STOPPED = ("paused", "cancelled")


def not_found():
    assistant_tasks._refuse("task_not_found", "Task not found in this project.", 404)


def _task(task_id):
    try:
        path, task = assistant_tasks._read(task_id)
    except HTTPException as exc:
        kind = exc.detail.get("error_kind") if isinstance(exc.detail, dict) else None
        if exc.status_code == 404 or kind == "task_project_required":
            not_found()  # a project the caller no longer reaches, or none: the same bytes (GK B (1))
        raise
    if not task.get("run"):
        not_found()
    return path, task


def _recheck(path, task):
    """GK B (2): at the click, nothing cached from `view`. `_task` re-ran the
    owner and the project; then the edit right and lock, the envelope's
    expiry and the budget, stopping the run as the runner's own checks would
    (an expired envelope ends and pauses it, a spent budget stops it).
    Returns the task as it now is, and whether this check stopped it."""
    from harness import runner
    from services import run_envelope
    if not assistant_tasks._may_write(task):
        assistant_tasks._refuse("project_locked", "Another user is editing this project; the run waits.")
    stop = None
    if run_envelope.lapsed(path) and run_envelope.end(path, "expired", "expires_at"):
        stop = ("paused", "envelope_expired")
    elif runner.budget_spent(copy.deepcopy(task)):
        stop = ("needs_user", "budget")
    if stop is None:
        return task, False
    done = runner._update(path, lambda t: runner._stop_in(t, *stop)
                          if t["status"] not in ("cancelled", "completed") else False)
    runner.drop_card(task["task_id"])
    return done or task, True


def _refuse_stopped(task):
    if task["status"] == "cancelled" or task["run"]["status"] in _STOPPED:
        # `{"error_kind": ...}` literals, so tests/test_tool_error_kind_manifest.py sees each kind here.
        raise HTTPException(409, {"error_kind": "run_paused_or_stopped", "message": "This run is paused or "
                                  "stopped: resume a paused run first; a stopped run cannot go on."})


def _budgets(task, envelope):
    from harness import runner
    from services import run_envelope
    budget = runner._budget(copy.deepcopy(task))
    out = {"cost_eur": {"used": round(budget["used"]["cost_eur"], 2), "limit": budget["limit"]["cost_eur"],
                        "unmetered": bool(budget["used"]["unmetered"])}}
    if envelope is not None and envelope["state"] == "granted":
        out["solves"] = {"used": sum(1 for a in envelope["audit"] if a["event"] == "used"
                                     and a.get("tool") in run_envelope.RUN_TOOLS),
                         "of": envelope["budget"]["solves"]}
        out["expires_at"] = envelope["expires_at"]
    return out


def _unattended(task, provider_ready):
    """Why "Approve and run unattended" cannot work here, or None."""
    from harness import runner
    if not runner.enabled():
        return "runner_off"
    if not provider_ready:
        return "no_provider"
    if not assistant_tasks._may_write(task):
        return "locked"
    return None


def view(task_id, provider_ready):
    from harness import runner
    from services import email_service, run_envelope
    with assistant_tasks._LOCK:
        path, task = _task(task_id)
        out = assistant_tasks._with_envelope(path, assistant_tasks._view(task, fresh=True))
        envelope = run_envelope._load(path)
    reason = _unattended(task, provider_ready)
    card = runner.live_card(task_id)
    waiting = task["run"].get("waiting")
    out["monitor"] = {
        "budgets": _budgets(task, envelope),
        "unattended": {"available": reason is None, **({"reason": reason} if reason else {})},
        "email": {"available": bool(email_service.get_status()["configured"])},
        **({"card": card} if card else {}),
        **({"waiting": {k: waiting[k] for k in ("kind", "ref")}} if waiting else {}),
    }
    return out


def present(task_id):
    from harness import runner
    confirm(task_id)
    runner.present(task_id)
    return {"ok": True}


def confirm(task_id):
    """The checks before a card of the run is issued (`present`) or the plan
    card's answer to it reaches the run's session (`/chat/run:<id>/confirm`)."""
    with assistant_tasks._LOCK:
        path, task = _task(task_id)
    _refuse_stopped(task)
    _refuse_stopped(_recheck(path, task)[0])


def pause(task_id):
    with assistant_tasks._LOCK:
        path, task = _task(task_id)
        run = task["run"]
        if task["status"] in ("cancelled", "completed") or run["status"] not in _PAUSABLE:
            raise HTTPException(409, {"error_kind": "run_not_pausable",
                                      "message": "This run is not going on, so there is nothing to pause."})
        run["status"], run["reason"], run["waiting"] = "paused", "user", None
        assistant_tasks._save(path, task)
    from harness import runner
    runner.drop_card(task_id)
    assistant_tasks._wake(path)


def resume(task_id, session):
    """The plan card's Resume. The user's own pause goes on as it was; any
    other stop is `resume_task`'s, on the user's chat session, whose checks
    decide anew. Both re-check at the click first (`_recheck`)."""
    with assistant_tasks._LOCK:
        path, task = _task(task_id)
    if _recheck(path, task)[1]:
        return  # stopped at the click, as the runner would have: nothing goes on unattended
    with assistant_tasks._LOCK:
        path, task = _task(task_id)
        run = task["run"]
        own = run["status"] == "paused" and run.get("reason") == "user" and task["status"] != "cancelled"
        if own:
            run["status"] = "running"
            run.pop("reason")
            assistant_tasks._save(path, task)
    if own:
        assistant_tasks._advance_run(path)
        assistant_tasks._wake(path)
        return
    from services import chat_tools
    token = chat_tools._CHAT_SESSION.set(session)
    try:
        assistant_tasks.resume_task(task_id)
    finally:
        chat_tools._CHAT_SESSION.reset(token)


def stop(task_id):
    from harness import runner
    with assistant_tasks._LOCK:
        _task(task_id)
    assistant_tasks.cancel_task(task_id)
    runner.drop_card(task_id)
