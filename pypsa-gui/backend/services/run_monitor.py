"""The run monitor: what the companion's plan card reads and does for a
harness run (study harness issue 08, design §10).

Every entry point is a user's click on the plan card, never a tool call, and
reads the task as `assistant_tasks._read` does: the caller's own task in the
active project, or `task_not_found` (an unknown, foreign or no longer
reachable task are told apart by nothing, SC-16).

* `view`: the run's `get_task` view and its envelope, read only (the runner
  advances the run, not this read), plus `monitor`: budgets used, the card
  the run waits on, and whether "Approve and run unattended" can work here.
* `present`: the user has the plan card open; a card the run waits on is
  issued now, its TTL starting at display (`runner.present`).
* `pause` and `resume`: the run stops at the next tick (a step in flight
  finishes) and goes on from where it stopped. A granted envelope stays
  granted and its expiry runs on.
* `stop`: `cancel_task`, which revokes the envelope. Nothing is reverted:
  the edits applied so far and the results so far are kept.
"""
from __future__ import annotations

import copy

from services import assistant_tasks

_PAUSABLE = ("running", "waiting_job", "needs_user")


def _task(task_id):
    path, task = assistant_tasks._read(task_id)
    if not task.get("run"):
        assistant_tasks._refuse("task_not_found", "Task not found in this project.", 404)
    return path, task


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
    with assistant_tasks._LOCK:
        _task(task_id)
    runner.present(task_id)
    return {"ok": True}


def pause(task_id):
    with assistant_tasks._LOCK:
        path, task = _task(task_id)
        run = task["run"]
        if task["status"] in ("cancelled", "completed") or run["status"] not in _PAUSABLE:
            assistant_tasks._refuse("run_not_pausable", "This run is not going on, so there is nothing to pause.")
        run["status"], run["reason"], run["waiting"] = "paused", "user", None
        assistant_tasks._save(path, task)
    assistant_tasks._wake(path)


def resume(task_id, session):
    """The plan card's Resume. The user's own pause goes on as it was; any
    other stop is `resume_task`'s, on the user's chat session, whose checks
    decide anew."""
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
    with assistant_tasks._LOCK:
        _task(task_id)
    assistant_tasks.cancel_task(task_id)
