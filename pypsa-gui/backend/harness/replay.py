"""
Replay (chat harness issue 13): fork a recorded session just before one of
its turns, run that turn again on another provider, and report the two
trajectories side by side. The provider-parity instrument the scripted
battery in issue 09 approximates, and the evidence ADR-0002 asks for.

`smoke/replay_session.py` is the command line around `replay`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness import eventlog


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict)
                   and b.get("type") == "text")


def trajectory(events: list[dict[str, Any]], start_seq: int) -> dict[str, Any]:
    """The turn that starts at `start_seq`, read off its events."""
    turn = []
    for event in events:
        if event["seq"] < start_seq:
            continue
        turn.append(event)
        if event["type"] == "turn/end":
            break
    tools = [b["name"] for e in turn if e["type"] == "assistant/attempt"
             for b in (e["message"].get("content") or []) if isinstance(b, dict)
             and b.get("type") == "tool_use"]
    answers = [_text_of(e["message"]) for e in turn if e["type"] == "assistant/attempt"]
    end = next((e for e in turn if e["type"] == "turn/end"), {})
    return {"tools": tools, "text": next((t for t in reversed(answers) if t), ""),
            "usage": end.get("usage", {}), "reason": end.get("reason")}


def replay(events_path: Path, at_seq: int, provider: Any, *, model: str | None = None,
           profile_id: str | None = None) -> dict[str, Any]:
    """Run the turn recorded at `at_seq` (a `turn/start`) again on `provider`
    from a fork of the session's history before it, asking for `model` (by
    default the one the recorded turn ran on) under the LLM profile
    `profile_id` (its tools payload, token cap and fallback model). The
    original log is never written."""
    from services import chat_service

    events = eventlog.read_events(Path(events_path))
    start = next((e for e in events if e["seq"] == at_seq), None)
    if start is None or start["type"] != "turn/start":
        raise ValueError(f"seq {at_seq} is not a turn/start in {events_path}")
    child = eventlog.fork(Path(events_path), at_seq, model=model or start.get("model"))
    child.profile_id = profile_id
    for _ in chat_service.run_turn(child, start["user"], provider=provider):
        pass
    replayed = eventlog.read_events(eventlog.path_for(child))
    child_start = next(e for e in replayed if e["seq"] >= at_seq and e["type"] == "turn/start")
    return {"user": start["user"], "fork_session": child.session_id,
            "original": trajectory(events, at_seq),
            "replayed": trajectory(replayed, child_start["seq"])}
