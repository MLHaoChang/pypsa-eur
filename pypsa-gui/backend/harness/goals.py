"""
A goal with a round budget (chat harness issue 17, decision Q17).

`set_goal` puts a goal on the chat session: an objective and a number of
unattended rounds (default 5, at most 20). After a turn ends, while the goal
is active and rounds remain, `run_turn` runs the next round itself on the
same stream, as a user message `Continue toward the goal (round n of N).`
admitted through utterance admission like any other utterance, so a
correction the user queued is answered first. A round changes nothing a
tool needs: every card still blocks, and the session and daily budgets
still apply.

Phases: `active`, `paused`, `blocked`, `complete`. The model completes or
pauses its goal; the loop pauses or blocks it when a turn shows a reason to
stop (`after_turn`); only a person resumes a paused or blocked goal
(`POST /api/chat/{id}/goal`), and a resume grants a fresh set of rounds.

The goal lives on the session, like the active workflow, and every change is
an event in the session's event log (`goal/set`, `goal/round`,
`goal/phase`), so the trajectory shows what ran unattended.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

DEFAULT_ROUNDS = 5
MAX_ROUNDS = 20
OBJECTIVE_MAX_CHARS = 500
CONTINUE = "Continue toward the goal (round {n} of {of})."

# A card the user said no to (or let lapse) pauses the goal: they decide what
# happens next. These are confirm.py's refusals.
_DECLINED = frozenset({"confirmation_denied", "confirmation_expired",
                       "confirmation_revoked", "approval_unavailable"})
_BUDGET = frozenset({"budget_exhausted", "daily_budget_exhausted"})


# The refusals, as `{"error_kind": ...}` literals so
# tests/test_tool_error_kind_manifest.py sees every kind the tools can raise.
_INVALID = {"error_kind": "invalid_goal"}
_ALREADY_SET = {"error_kind": "goal_already_set"}
_NO_GOAL = {"error_kind": "no_goal"}


def _refuse(kind: dict[str, str], message: str, status: int = 409) -> None:
    raise HTTPException(status_code=status, detail={**kind, "message": message})


def _generation(session: Any) -> int:
    from services import utterance_admission as ua
    return ua._DEFAULT.generation(session.session_id)


def _log(session: Any, event_type: str, **fields: Any) -> None:
    from harness import eventlog
    eventlog.record(session, event_type, **fields)


def payload(session: Any) -> dict[str, Any]:
    """What the panel shows: the `goal_state` frame and `/chat/history.goal`."""
    goal = getattr(session, "goal", None)
    if not goal:
        return {"goal": None}
    return {"goal": {k: goal[k] for k in ("objective", "max_rounds", "rounds_used", "phase", "reason")}}


def active(session: Any) -> bool:
    goal = getattr(session, "goal", None)
    return bool(goal) and goal["phase"] == "active"


# ── the model's tools ──────────────────────────────────────────────────────

def set_goal(session: Any, objective: Any, max_rounds: Any = None) -> dict[str, Any]:
    if not isinstance(objective, str) or not objective.strip():
        _refuse(_INVALID, "objective must be a non-empty sentence saying what done looks like", 422)
    objective = " ".join(objective.split())
    if len(objective) > OBJECTIVE_MAX_CHARS:
        _refuse(_INVALID, f"objective must be at most {OBJECTIVE_MAX_CHARS} characters", 422)
    if max_rounds is None:
        max_rounds = DEFAULT_ROUNDS
    if type(max_rounds) is not int or not 1 <= max_rounds <= MAX_ROUNDS:
        _refuse(_INVALID, f"max_rounds must be a whole number from 1 to {MAX_ROUNDS}", 422)
    with session._lock:
        current = getattr(session, "goal", None)
        if current and current["phase"] != "complete":
            _refuse(_ALREADY_SET,
                    f"a goal is already set ({current['phase']}); complete it first, or ask the "
                    "user to stop it. Only the user resumes a paused or blocked goal.")
        session.goal = {"objective": objective, "max_rounds": max_rounds, "rounds_used": 0,
                        "phase": "active", "reason": None, "generation": _generation(session)}
    _log(session, "goal/set", objective=objective, max_rounds=max_rounds)
    return {**payload(session)["goal"],
            "note": (f"Up to {max_rounds} more rounds will run on their own after this turn. "
                     "Call complete_goal when it is reached, pause_goal if you need the user.")}


def _set_phase(session: Any, phase: str, reason: str | None, *, by: str) -> bool:
    """Move the goal to `phase`; False when there is no goal or nothing changed."""
    with session._lock:
        goal = getattr(session, "goal", None)
        if not goal or (goal["phase"], goal["reason"]) == (phase, reason):
            return False
        goal["phase"], goal["reason"] = phase, reason
    _log(session, "goal/phase", phase=phase, reason=reason, by=by)
    return True


def pause(session: Any, reason: str | None = None, *, by: str) -> dict[str, Any]:
    goal = getattr(session, "goal", None)
    if goal and goal["phase"] == "active":
        _set_phase(session, "paused", reason or by, by=by)
    return payload(session)


def pause_goal_tool(session: Any, reason: Any = None) -> dict[str, Any]:
    if not getattr(session, "goal", None):
        _refuse(_NO_GOAL, "no goal is set on this session")
    text = " ".join(reason.split())[:200] if isinstance(reason, str) and reason.strip() else "model"
    return pause(session, text, by="model")["goal"]


def complete_goal_tool(session: Any, summary: Any = None) -> dict[str, Any]:
    if not getattr(session, "goal", None):
        _refuse(_NO_GOAL, "no goal is set on this session")
    text = " ".join(summary.split())[:200] if isinstance(summary, str) and summary.strip() else None
    _set_phase(session, "complete", text, by="model")
    return payload(session)["goal"]


# ── a person's controls ────────────────────────────────────────────────────

def resume(session: Any) -> dict[str, Any]:
    """Only a person resumes. A resume re-arms the goal at the session's
    current admission generation with a fresh set of rounds; it starts no
    turn by itself (the panel sends one)."""
    with session._lock:
        goal = getattr(session, "goal", None)
        if not goal or goal["phase"] == "complete":
            _refuse(_NO_GOAL, "there is no paused or blocked goal to resume")
        changed = goal["phase"] != "active"
        goal.update(phase="active", reason=None, rounds_used=0, generation=_generation(session))
    if changed:
        _log(session, "goal/phase", phase="active", reason=None, by="user")
    return payload(session)


def stop(session: Any) -> dict[str, Any]:
    with session._lock:
        had = getattr(session, "goal", None) is not None
        session.goal = None
    if had:
        _log(session, "goal/phase", phase="stopped", reason=None, by="user")
    return payload(session)


# ── per turn ───────────────────────────────────────────────────────────────

def addendum(session: Any) -> str | None:
    """The goal, for the user turn (outside the untrusted fence, like the
    workflow step). None without a goal or once it is complete."""
    goal = getattr(session, "goal", None)
    if not goal or goal["phase"] == "complete":
        return None
    quoted = json.dumps(goal["objective"], ensure_ascii=False)
    if goal["phase"] != "active":
        return (f"Goal: {quoted} is {goal['phase']} ({goal['reason']}). No rounds run on their "
                "own until the user resumes it; only the user can.")
    return (f"Goal: {quoted} ({goal['rounds_used']} of {goal['max_rounds']} unattended rounds "
            "used). Work toward it; after this turn the next round starts on its own. When it "
            "is reached, call complete_goal with a one-line summary; if you need the user, ask "
            "with ask_user or call pause_goal with the reason. Every change still needs its card.")


class Watch:
    """What a turn showed that decides whether the goal goes on."""

    def __init__(self) -> None:
        self.completed = False
        self.choice = False
        self.declined = False
        self.uncertain = False
        self.error_kind: str | None = None
        self.done_reason: str | None = None

    def note(self, event: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        if event == "turn_done":
            self.completed = True
        elif event == "choice_request":
            self.choice = True
        elif event == "tool_error":
            kind = payload.get("error_kind")
            self.declined |= kind in _DECLINED
            self.uncertain |= kind == "task_step_uncertain"
        elif event == "error":
            self.error_kind = payload.get("error_kind") or "error"
        elif event == "session_done":
            self.done_reason = payload.get("reason") or self.done_reason


def _needs_review(session: Any) -> bool:
    task_id = getattr(session, "task_id", None)
    if not task_id:
        return False
    try:
        from services import assistant_tasks
        return assistant_tasks.get_task(task_id).get("status") == "needs_review"
    except Exception:  # noqa: BLE001 — an unreadable task is not a reason to go on
        return True


def _stop(session: Any, watch: Watch, checked: bool) -> tuple[str, str] | None:
    goal = session.goal
    if _generation(session) != goal["generation"]:
        return "blocked", "generation_changed"
    if checked:
        return "blocked", "checked_turn"
    if watch.done_reason == "aborted":
        return "paused", "aborted"
    if watch.done_reason in _BUDGET:
        return "blocked", "budget"
    if watch.error_kind or not watch.completed:
        return "blocked", "error"
    if watch.declined:
        return "paused", "card_declined"
    if watch.uncertain or _needs_review(session):
        return "blocked", "needs_review"
    return None


def on_error(session: Any) -> bool:
    """An `error` frame is about to go out (the panel closes the stream on
    it): block the goal first so the panel's strip says so."""
    if not active(session):
        return False
    return _set_phase(session, "blocked", "error", by="harness")


def after_turn(session: Any, watch: Watch, *, checked: bool) -> str | None:
    """Decide what follows a finished turn. Returns the next round's message
    (and counts the round), or None, moving the goal to `paused` or
    `blocked` when the turn showed a reason to stop."""
    if not active(session):
        return None
    stop_ = _stop(session, watch, checked)
    if stop_ is not None:
        _set_phase(session, *stop_, by="harness")
        return None
    if watch.choice:
        return None          # a Choice card waits for the user; their answer is the next turn
    with session._lock:
        goal = session.goal
        if goal["rounds_used"] >= goal["max_rounds"]:
            exhausted = True
        else:
            exhausted = False
            goal["rounds_used"] += 1
            n, of = goal["rounds_used"], goal["max_rounds"]
    if exhausted:
        _set_phase(session, "paused", "rounds_used_up", by="harness")
        return None
    _log(session, "goal/round", round=n, of=of)
    return CONTINUE.format(n=n, of=of)
