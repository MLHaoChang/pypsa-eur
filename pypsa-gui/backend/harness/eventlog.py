"""
The session event log (chat harness issue 13): every model-visible input of a
session, append-only, so a session can be replayed, forked and compared
across providers. "Model-visible means logged."

Borrowed from DeepSeek Harness's `SessionEvent` log (assessment design 1);
written for this harness. The log is the source of truth (part b):

* The provider history `ChatSession.messages` changes only through `apply`,
  which records an event and folds it into the deque with `_apply`, the step
  `derive_messages` folds over the whole log. So the deque IS the log's
  projection, redaction included, and a session not in memory is rebuilt from
  its log (`resume`). Nothing else changes the deque
  (`tests/test_harness_eventlog_truth.py` keeps it so).
* `turn/start` is on disk before the model is called, which retires the
  pending-turn WAL: a turn that started and never ended is the interrupted
  turn `GET /chat/history` reports (`interrupted_turn`), once.
* `chat.jsonl` is a projection: each record is a `turn/record` event, written
  before the line (`project_chat` reads them back).

A session's log is `<app data>/chat-events/<sha(owner)>/<sha(session)>.jsonl`
(0700 directory, 0600 file, appended and fsynced under
`harness/durable.transaction`, swept after `RETENTION_SECONDS`): one header
line, then events with a monotonic `seq`:

* `turn/start {user, model, project, ts}`: written and fsynced at once.
* `user/message`, `assistant/attempt`, `tool/result {message}`: every message
  that enters the provider history, as stored there (after sanitising and a
  checked turn's result replacement), so the log is what the model saw.
* `context/injected {kind, text}`: each per-turn addendum (`ui_context`,
  `workflow_step`, `checked_request`), also inside the user message.
* `history/trim {limit}`, `history/rewind {turns}`, `history/reset`: the other
  ways the history changes, so `derive_messages` can replay it exactly.
* `turn/record {record}`: the turn's `chat.jsonl` record (display, not
  model-visible), flushed before the line is appended.
* `turn/end {reason, usage}`: `completed | aborted | error | interrupted`.

Within a turn the events are buffered and written with one fsync at
`turn/end`; outside a turn (a rewind, a `/history` rehydration) each is
written at once. Redaction (`history._redact_for_persist`) applies before the
write. A failure to write is logged and never costs the turn; the deque is
updated all the same and the events stay buffered for the next flush.
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


def record(session: Any, event_type: str, /, **fields: Any) -> dict[str, Any]:
    """Append one event and return it (as logged: redacted). Buffered inside a
    turn; written at once outside one. Never raises: an event that could not
    be recorded is still returned, so the caller's fold goes on."""
    from harness.history import _redact_for_persist

    try:
        fields = _redact_for_persist(fields)
    except Exception:  # noqa: BLE001 — never write what could not be redacted
        logger.warning("chat: event %s could not be redacted; not logged", event_type, exc_info=True)
        return {"type": event_type, **fields}
    event = {"type": event_type, **fields}
    try:
        log = _log(session)
        with log.lock:
            event = {"seq": log.next_seq, "type": event_type, "at": _now(), **fields}
            log.next_seq += 1
            log.buffer.append(event)
            immediate = not log.in_turn
        if immediate:
            flush(session)
    except Exception:  # noqa: BLE001 — the log must never cost the turn
        logger.warning("chat: event %s could not be recorded", event_type, exc_info=True)
    return event


def apply(session: Any, event_type: str, /, **fields: Any) -> None:
    """Change the session's provider history: record the event, then fold it
    into `session.messages` with the step `derive_messages` uses. The only
    way the history changes (see the module docstring)."""
    _apply(session.messages, record(session, event_type, **fields))


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


def project_key(ctx: Any) -> str | None:
    """The project a turn ran on, as `turn/start` records it."""
    key = getattr(ctx, "project_uuid", None) or getattr(ctx, "loaded_project", None)
    return str(key) if key else None


def turn_start(session: Any, user: Any, ctx: Any = None) -> None:
    """Open a turn: on disk, fsynced, before the model is called (what the
    pending-turn WAL did). `ts` is epoch seconds, as the panel reads it."""
    log = _log(session)
    with log.lock:
        log.in_turn = True
    record(session, "turn/start", user=user, model=getattr(session, "model", None),
           project=project_key(ctx), ts=time.time())
    flush(session)


def turn_record(session: Any, chat_record: dict[str, Any]) -> None:
    """The turn's `chat.jsonl` record, logged and flushed BEFORE the caller
    appends the line, so the line is a projection of the log."""
    record(session, "turn/record", record=chat_record)
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
    """A message entering the provider history (already sanitised)."""
    apply(session, message_type(msg), message=msg)


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
    `turn/end {reason: interrupted}`, so the turn is closed (and reported
    once, `interrupted_turn`)."""
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

def _apply(messages: Any, event: dict[str, Any]) -> None:
    """One step of the fold: what `event` does to a provider history."""
    from harness.history import cut_turns, trim_session_messages

    kind = event["type"]
    if kind in MESSAGE_TYPES:
        messages.append(event["message"])
    elif kind == "history/trim":
        trim_session_messages(messages, int(event["limit"]))
    elif kind == "history/rewind":
        cut_turns(messages, int(event["turns"]))
    elif kind == "history/reset":
        messages.clear()


def derive_messages(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The provider history the events describe; `session.messages` is this
    fold kept up to date event by event (`apply`)."""
    messages: collections.deque = collections.deque()
    for event in events:
        _apply(messages, event)
    return list(messages)


def project_chat(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The `chat.jsonl` records the events describe, in order."""
    return [e["record"] for e in events if e["type"] == "turn/record"]


# ── resuming and recovering ────────────────────────────────────────────────

def exists(session: Any) -> bool:
    try:
        return path_for(session).exists()
    except Exception:  # noqa: BLE001
        return False


def resume(session: Any) -> bool:
    """Rebuild a session that is not in memory from its log (a restart, an
    eviction): close a torn tail, then set `session.messages` to the
    projection. Its next events go on in the same file. False when there is
    no readable log, and the caller falls back to `chat.jsonl`."""
    path = path_for(session)
    if not path.exists():
        return False
    try:
        events = read_events(path, repair=True)
    except Exception:  # noqa: BLE001
        logger.warning("chat: the session event log could not be read", exc_info=True)
        return False
    session.messages.clear()
    for event in events:
        _apply(session.messages, event)
    return True


def _tail(path: Path, nbytes: int = 1 << 16) -> list[dict[str, Any]]:
    """The last events of a log, read from its end (whole lines only)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    if size > nbytes and lines:
        lines = lines[1:]                     # the first may be cut
    out = []
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue                          # a torn last write
        if "seq" in obj:
            out.append(obj)
    return out


def interrupted_turn(owner: Any, ctx: Any, *, live: Any) -> dict[str, Any] | None:
    """The owner's latest turn on this project that started and never ended
    (the process died), as the panel's `pending_turn` (`ts`, `session_id`,
    `model`, `user`), or None. Its log is closed (`repair`), so it is reported
    once. A session `live(session_id)` says is in this process is left alone:
    its turn is running, or its own buffer still holds the end."""
    project = project_key(ctx)
    directory = _owner_dir(owner)
    if project is None or not directory.is_dir():
        return None
    found: list[tuple[float, Path, dict[str, Any], str]] = []
    for path in directory.glob("*.jsonl"):
        try:
            tail = _tail(path)
            starts = [i for i, e in enumerate(tail) if e.get("type") == "turn/start"]
            if not starts or any(e.get("type") == "turn/end" for e in tail[starts[-1]:]):
                continue
            start = tail[starts[-1]]
            if start.get("project") != project:
                continue
            sid = str((read_header(path) or {}).get("session_id") or "")
            if live(sid):
                continue
            found.append((float(start.get("ts") or 0), path, start, sid))
        except Exception:  # noqa: BLE001 — one unreadable log never hides the rest
            logger.warning("chat: an event log could not be checked", exc_info=True)
    if not found:
        return None
    for _, path, _, _ in found:
        try:
            _repair(path)
        except Exception:  # noqa: BLE001
            logger.warning("chat: an interrupted turn could not be closed", exc_info=True)
    ts, _, start, sid = max(found, key=lambda f: f[0])
    return {"ts": ts, "session_id": sid, "model": start.get("model"), "user": start.get("user")}


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
    for event in prefix:
        _apply(child.messages, event)
    return child
