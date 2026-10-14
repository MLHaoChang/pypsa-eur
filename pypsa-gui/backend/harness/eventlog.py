"""
The session event log (chat harness issue 13): every model-visible input of a
session, append-only, so a session can be replayed, forked and compared
across providers. "Model-visible means logged."

Borrowed from DeepSeek Harness's `SessionEvent` log (assessment design 1);
written for this harness. Part a, here, writes the log in SHADOW mode beside
today's path: `chat.jsonl`, the pending-turn WAL and the frames are
unchanged, and nothing reads the log on the turn path yet. Part b makes
`derive_messages` the projection the loop sends and retires the WAL.

A session's log is `<app data>/chat-events/<sha(owner)>/<sha(session)>.jsonl`
(0700 directory, 0600 file, appended and fsynced under
`harness/durable.transaction`, swept after `RETENTION_SECONDS`): one header
line, then events with a monotonic `seq`:

* `turn/start {user}`: written and fsynced at once, the WAL's job.
* `user/message`, `assistant/attempt`, `tool/result {message}`: every message
  that enters the provider history, as stored there (after sanitising and a
  checked turn's result replacement), so the log is what the model saw.
* `context/injected {kind, text}`: each per-turn addendum (`ui_context`,
  `workflow_step`, `checked_request`), also inside the user message.
* `history/trim {limit}`, `history/rewind {turns}`, `history/reset`: the other
  ways the history changes, so `derive_messages` can replay it exactly.
* `turn/end {reason, usage}`: `completed | aborted | error | interrupted`.

Within a turn the events are buffered and written with one fsync at
`turn/end`; outside a turn (a rewind, a `/history` rehydration) each is
written at once. Redaction (`history._redact_for_persist`) applies before the
write. A failure here is logged and never costs the turn.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("pypsa_gui.chat")

FORMAT = "pypsa-gui/chat-events/1"
RETENTION_SECONDS: int = 30 * 24 * 3600
MESSAGE_TYPES = ("user/message", "assistant/attempt", "tool/result")
_TURN_REASONS = frozenset({"completed", "aborted", "error", "interrupted"})


# ── where ──────────────────────────────────────────────────────────────────

def _root() -> Path:
    from app_paths import app_data_dir
    return app_data_dir() / "chat-events"


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _owner_dir(owner: Any) -> Path:
    return _root() / _key(str(owner or "_local"))


def path_for(session: Any) -> Path:
    sid = str(getattr(session, "session_id", None) or "_none")
    return _owner_dir(getattr(session, "owner_user_id", None)) / f"{_key(sid)}.jsonl"


# ── writing ────────────────────────────────────────────────────────────────

class _Log:
    def __init__(self, path: Path, header: dict[str, Any], next_seq: int) -> None:
        self.path = path
        self.header = header
        self.next_seq = next_seq
        self.buffer: list[dict[str, Any]] = []
        self.in_turn = False
        self.header_written = path.exists()
        self.lock = threading.Lock()


_LOGS_LOCK = threading.Lock()


def _log(session: Any) -> _Log:
    log = getattr(session, "_eventlog", None)
    if log is not None:
        return log
    with _LOGS_LOCK:
        log = getattr(session, "_eventlog", None)
        if log is None:
            path = path_for(session)
            header = {"format": FORMAT, "session_id": str(getattr(session, "session_id", "")),
                      "owner_user_id": getattr(session, "owner_user_id", None),
                      "created_at": _now(), "parent": None, "inherited_event_count": 0}
            log = _Log(path, header, _next_seq_on_disk(path))
            try:
                session._eventlog = log
            except AttributeError:  # a stand-in session that takes no attributes
                pass
    return log


def _next_seq_on_disk(path: Path) -> int:
    if not path.exists():
        return 1
    events = read_events(path)
    return (events[-1]["seq"] + 1) if events else _header_start(path)


def _header_start(path: Path) -> int:
    header = read_header(path) or {}
    return int(header.get("next_seq") or 1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_lines(path: Path, lines: list[str]) -> None:
    from harness.durable import transaction

    path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    data = "".join(lines).encode("utf-8")
    with transaction(path.parent):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        _sweep(path.parent, keep=path)


def _sweep(directory: Path, keep: Path) -> None:
    cutoff = time.time() - RETENTION_SECONDS
    for candidate in directory.glob("*.jsonl"):
        if candidate == keep:
            continue
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink(missing_ok=True)
        except OSError:
            continue


def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str) + "\n"


def record(session: Any, event_type: str, /, **fields: Any) -> None:
    """Append one event. Buffered inside a turn; written at once outside one.
    Never raises."""
    try:
        from harness.history import _redact_for_persist

        log = _log(session)
        with log.lock:
            event = {"seq": log.next_seq, "type": event_type, "at": _now(),
                     **_redact_for_persist(fields)}
            log.next_seq += 1
            log.buffer.append(event)
            immediate = not log.in_turn
        if immediate:
            flush(session)
    except Exception:  # noqa: BLE001 — the log must never cost the turn
        logger.warning("chat: event %s could not be recorded", event_type, exc_info=True)


def flush(session: Any) -> None:
    """Write the buffered events with one fsync. Never raises; on failure the
    events stay buffered and the next flush retries them."""
    try:
        log = _log(session)
        with log.lock:
            if not log.buffer and log.header_written:
                return
            lines = [] if log.header_written else [_dump(log.header)]
            lines += [_dump(e) for e in log.buffer]
            _write_lines(log.path, lines)
            log.header_written = True
            log.buffer.clear()
    except Exception:  # noqa: BLE001
        logger.warning("chat: the session event log could not be written", exc_info=True)


def turn_start(session: Any, user: Any) -> None:
    log = _log(session)
    with log.lock:
        log.in_turn = True
    record(session, "turn/start", user=user, model=getattr(session, "model", None))
    flush(session)


def turn_end(session: Any, reason: str, usage: dict[str, Any] | None = None) -> None:
    """Close the open turn (a no-op when none is open: a turn refused before
    it started) and write its batch with one fsync."""
    try:
        if not _log(session).in_turn:
            return
    except Exception:  # noqa: BLE001
        return
    if reason not in _TURN_REASONS:
        reason = "error"
    record(session, "turn/end", reason=reason, usage=dict(usage or {}))
    log = _log(session)
    with log.lock:
        log.in_turn = False
    flush(session)


def message(session: Any, msg: dict[str, Any]) -> None:
    """A message that has just entered the provider history."""
    record(session, message_type(msg), message=msg)


def message_type(msg: dict[str, Any]) -> str:
    if msg.get("role") == "assistant":
        return "assistant/attempt"
    content = msg.get("content")
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return "tool/result"
    return "user/message"


def context(session: Any, kind: str, text: str) -> None:
    if text:
        record(session, "context/injected", kind=kind, text=text)


# ── reading ────────────────────────────────────────────────────────────────

def _read_lines(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    header: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    if not path.exists():
        return None, []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:      # a torn final write: the batch never landed
                break
            raise
        if header is None and "format" in obj:
            header = obj
        else:
            events.append(obj)
    return header, events


def read_header(path: Path) -> dict[str, Any] | None:
    return _read_lines(Path(path))[0]


def read_events(path: Path, *, repair: bool = False) -> list[dict[str, Any]]:
    """The session's events, oldest first, with a fork's inherited prefix
    resolved from its parent. `repair=True` closes a torn tail (see
    `repair`)."""
    path = Path(path)
    if repair:
        _repair(path)
    header, own = _read_lines(path)
    if header and header.get("parent"):
        parent = header["parent"]
        parent_events = read_events(path.parent / parent["file"])
        own = parent_events[: int(header.get("inherited_event_count") or 0)] + own
    return own


def _repair(path: Path) -> None:
    """A turn that started and never ended (the process died) gets synthetic
    closers, appended as an ordinary batch: a `tool/result` with
    `error_kind: interrupted` for every unanswered tool call, then
    `turn/end {reason: interrupted}`. This is what the pending-turn WAL does
    for chat.jsonl today."""
    events = read_events(path)
    starts = [i for i, e in enumerate(events) if e["type"] == "turn/start"]
    if not starts:
        return
    tail = events[starts[-1]:]
    if any(e["type"] == "turn/end" for e in tail):
        return
    answered = {b.get("tool_use_id") for e in tail if e["type"] == "tool/result"
                for b in e["message"]["content"] if isinstance(b, dict)}
    pending = [b["id"] for e in tail if e["type"] == "assistant/attempt"
               for b in (e["message"].get("content") or []) if isinstance(b, dict)
               and b.get("type") == "tool_use" and b.get("id") not in answered]
    seq = events[-1]["seq"] + 1
    closers: list[dict[str, Any]] = []
    if pending:
        closers.append({"seq": seq, "type": "tool/result", "at": _now(), "error_kind": "interrupted",
                        "message": {"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": tid, "is_error": True,
                             "content": "interrupted"} for tid in pending]}})
        seq += 1
    closers.append({"seq": seq, "type": "turn/end", "at": _now(), "reason": "interrupted", "usage": {}})
    # Truncate a torn final line before appending, so the batch starts clean.
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        keep = text[: text.rfind("\n") + 1]
        with open(path, "w", encoding="utf-8") as f:
            f.write(keep)
            f.flush()
            os.fsync(f.fileno())
    _write_lines(path, [_dump(c) for c in closers])


# ── the projection ─────────────────────────────────────────────────────────

def derive_messages(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The provider history the events describe: what the next request of the
    session is seeded from (`session.messages`)."""
    from harness.history import cut_turns, trim_session_messages

    messages: collections.deque = collections.deque()
    for event in events:
        kind = event["type"]
        if kind in MESSAGE_TYPES:
            messages.append(event["message"])
        elif kind == "history/trim":
            trim_session_messages(messages, int(event["limit"]))
        elif kind == "history/rewind":
            cut_turns(messages, int(event["turns"]))
        elif kind == "history/reset":
            messages.clear()
    return list(messages)


# ── fork ───────────────────────────────────────────────────────────────────

def fork(parent_path: Path, at_seq: int, *, model: str | None = None) -> Any:
    """A new session whose history is the parent's events before `at_seq`.

    The child's log names the parent and the inherited prefix in its header;
    those events are shared, not copied. The child is a fresh session with
    the parent's owner: no open cards, approvals, checked-answer checkpoints
    or task tickets come with it, only the history."""
    from harness.session import ChatSession

    parent_path = Path(parent_path)
    parent_header = read_header(parent_path) or {}
    prefix = [e for e in read_events(parent_path) if e["seq"] < at_seq]
    next_seq = (prefix[-1]["seq"] + 1) if prefix else 1
    child = ChatSession(model=model or "replay",
                        owner_user_id=parent_header.get("owner_user_id"))
    child._eventlog = _Log(parent_path.parent / f"{_key(child.session_id)}.jsonl", {
        "format": FORMAT, "session_id": child.session_id,
        "owner_user_id": parent_header.get("owner_user_id"), "created_at": _now(),
        "parent": {"session_id": parent_header.get("session_id"), "file": parent_path.name},
        "inherited_event_count": len(prefix), "next_seq": next_seq,
    }, next_seq)
    flush(child)
    for msg in derive_messages(prefix):
        child.messages.append(msg)
    return child
