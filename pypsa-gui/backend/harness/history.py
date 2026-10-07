"""
Conversation history: the in-memory trim and turn summary, the chat.jsonl
persistence with its rotation and pending-turn WAL, and the lineage rules
that move the transcript with a saved, renamed or snapshotted project.

Moved from harness/loop.py (the former services/chat_service.py) on
2026-10-05, chat harness issue 08, by AST selection of whole top-level
nodes; the loop re-imports every name, so `chat_service.<name>` is the same
object. A tunable here is patched on THIS module (see harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pathlib import Path
from services.project_context import ProjectContext
from services.redaction import (  # moved 2026-08-13 (provider seam, Task 1)
    redact_secrets_in_str as _redact_secrets_in_str,
)
from typing import Any
import collections
import datetime
import json
import os
import time
import logging

logger = logging.getLogger("pypsa_gui.chat")

if TYPE_CHECKING:  # the loop imports this module; never the reverse at runtime
    from harness.loop import ChatSession


# Per-project chat history filename.
CHAT_FILENAME = "chat.jsonl"


# Rotation threshold (bytes). When chat.jsonl exceeds this, append_turn renames
# the file to chat.jsonl.1 (overwriting any prior rotation) before writing the
# new turn — bounding per-project disk usage to ~2x ROTATE_BYTES (the current
# file plus the previous rotation). Sized so normal sessions never rotate and
# pathological producers can't fill disk in a few hours.
ROTATE_BYTES: int = 5 * 1024 * 1024  # 5 MiB


def _redact_for_persist(value: Any, _values: frozenset[str] | None = None) -> Any:
    """
    Strip plausible secrets from a value before it is written to chat.jsonl.

    Recurses structurally over dict / list / str (mirrors _coerce_jsonable's
    shape-preserving walk) so it can be applied to the assistant_blocks list
    (a list of content-block dicts) AND the plain user-message string. Returns
    the same container shape as the input. Non-str scalars (int / float / bool /
    None) pass through unchanged. Idempotent — re-redacting already-redacted
    text is a no-op (so re-importing an exported transcript is safe).

    Deliberately scoped to the high-value, low-false-positive patterns:
    sk-ant-* keys, password=/token=/api_key=/secret= values, and bearer
    tokens, plus (Task 4) every managed secret value currently in effect.
    Bare email addresses are NOT redacted — that pattern over-redacts
    legitimate component / project names and model summaries (see the reviewer
    note) for little secret-leak benefit, so it is intentionally omitted.

    PERFORMANCE: this recurses over every block of every turn. `_values` is
    the `app_secrets.live_secret_values()` snapshot, taken ONCE by the
    top-level caller (here, when `_values` is None) and threaded down through
    every recursive call — never re-read from disk per string.
    """
    if _values is None:
        from services.app_secrets import live_secret_values  # noqa: PLC0415

        _values = live_secret_values()
    if isinstance(value, str):
        return _redact_secrets_in_str(value, _values)
    if isinstance(value, dict):
        # C-16 — KEYS are scrubbed too. Recursing only into values let the
        # live provider key land verbatim in `chat.jsonl` when it appeared in
        # a key position, because the gap swallowed the managed-value
        # SUBSTITUTION and not merely the shape regexes. Reachable through
        # `POST /api/chat/import` and through model-authored `tool_use.input`
        # keys, and it propagates onward into snapshot/copy bundles.
        return {
            (_redact_secrets_in_str(k, _values) if isinstance(k, str) else k):
                _redact_for_persist(v, _values)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_for_persist(v, _values) for v in value]
    return value


def get_persist_path(ctx: ProjectContext) -> Path | None:
    """
    Resolve `ctx`'s chat.jsonl on-disk path, caching it on `ctx.chat_state`.

    Returns:
      * `Path` — when the context is BOUND (`loaded_project is not None`),
        absolute path to `<PROJECTS_DIR>/<loaded_project>/chat.jsonl`.
      * `None` — when the context is UNBOUND (fresh / New Project before
        first save). Callers (append_turn) treat None as "no on-disk home
        yet" — Phase 0 silently drops the turn; Phase 4 may add an in-memory
        ring buffer that flushes on first bind.

    Caches the resolved path on `ctx.chat_state.persist_path` (as a string —
    Path-typed fields conflict with project_context.py's
    `from __future__ import annotations` and dataclass field defaults if a
    user does `dataclasses.fields(...)`). On project rename (Phase 1+ tool
    `rename_project`), the cache MUST be invalidated by setting
    `ctx.chat_state.persist_path = None` before the next call so the new
    binding is resolved.
    """
    if ctx.loaded_project is None:
        return None
    # Resolve from the BOUND context, not the display name. Project data lives
    # at `projects_root/<org_uuid>/<project_uuid>/`; the flat-name path below is
    # the pre-tenancy shape, which put a project's chat history in a different
    # directory from the project itself — and is why chat.jsonl could not be
    # included in the export bundle.
    storage_dir = getattr(ctx, "storage_dir", None)
    if storage_dir:
        expected = Path(storage_dir) / CHAT_FILENAME
    else:
        # Bound by name but never stored (pre-tenancy projects, and any context
        # whose storage_dir has not been stamped yet). Lazy import — pulling
        # PROJECTS_DIR at module scope would be a circular import.
        from routers.projects import PROJECTS_DIR
        expected = PROJECTS_DIR / ctx.loaded_project / CHAT_FILENAME
    cached = ctx.chat_state.persist_path
    if cached is not None:
        # Phase 4 QA fix (state-lifecycle): self-validate the cache against
        # the current binding. A `load_project` / `import_bundle` call that
        # carries chat_state forward will leave the cached persist_path
        # pointing at the PRIOR project — invalidating in those call sites
        # is fragile (easy to miss a new load/swap entry point), so we
        # defensively re-resolve when the cache disagrees with the active
        # binding. Eviction path tolerance: this still never raises.
        cached_path = Path(cached)
        if cached_path == expected:
            return cached_path
        # Drift detected — invalidate and re-resolve below.
        ctx.chat_state.persist_path = None
    ctx.chat_state.persist_path = str(expected)
    return expected


def read_all_turns(ctx: ProjectContext) -> list[dict[str, Any]]:
    """
    Read + parse ALL persisted turn records for `ctx` (the rotated backup
    chat.jsonl.1 first — older — then the current chat.jsonl — newer), oldest
    first.

    DRY chokepoint shared by GET /history, the #9 daily-spend cap, and the #27
    export route. Best-effort: skips unparseable / trailing-partial lines and
    swallows OSError (a missing file → empty list). Returns ONLY the parsed
    turns — it does NOT rebuild any session (that side-effect stays in
    chat_history so callers like the cap / export don't accidentally trigger it).
    Empty list when the context is unbound (no persist path).

    Callers that need to know whether anything was skipped want
    `read_all_turns_with_gap`; this shape is preserved for the two callers
    (the daily-spend cap, the export route) for which a damaged line changes
    nothing they can act on.
    """
    return read_all_turns_with_gap(ctx)[0]


def read_all_turns_with_gap(
    ctx: ProjectContext,
) -> tuple[list[dict[str, Any]], int]:
    """
    `read_all_turns`, plus the number of lines that failed to parse.

    QA #10 — the skip itself is correct (a torn trailing line from a
    concurrent write is exactly what the rotation lock cannot prevent, and
    refusing to serve the other 200 turns over it would be worse). What was
    wrong is that the skip was SILENT: a transcript that lost a turn read as
    a transcript that never had one, so the panel rendered a shorter
    conversation than the user had and nothing anywhere said so.

    The count is deliberately a count and not the raw lines — the damaged
    bytes are unparseable by definition, so there is nothing to show; the
    honest statement is "N records here are unreadable".

    Holds `ctx.chat_state.lock` for path resolution + reads so a concurrent
    `append_turn` rotation (rename chat.jsonl → chat.jsonl.1) cannot expose a
    missing/empty file mid-read.
    """
    # Unbound: no files to touch — skip the lock.
    if ctx.loaded_project is None and ctx.chat_state.persist_path is None:
        return [], 0
    with ctx.chat_state.lock:
        path = get_persist_path(ctx)
        if path is None or not path.exists():
            return [], 0
        rotated = path.with_suffix(path.suffix + ".1")
        sources = [rotated, path] if rotated.exists() else [path]
        turns: list[dict[str, Any]] = []
        gap = 0
        for src in sources:
            try:
                for line in src.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Trailing partial line from a concurrent write — skip
                        # it, but count it so the caller can say so.
                        gap += 1
                        continue
            except OSError:
                continue
        return turns, gap


def _today_token_spend(ctx: ProjectContext) -> int:
    """
    Sum input+output tokens across `ctx`'s persisted turns whose timestamp
    falls on TODAY (UTC). Drives the #9 cross-session daily spend cap.

    UTC is intentional: the read side buckets by UTC date and the write side
    stamps `time.time()` (epoch — timezone-agnostic), so the cap resets at
    UTC midnight regardless of the host's local timezone. Best-effort: a record
    missing `ts` / `usage` contributes 0; never raises.
    """
    today = datetime.datetime.fromtimestamp(
        time.time(), datetime.timezone.utc
    ).date()
    total = 0
    for rec in read_all_turns(ctx):
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        try:
            rec_date = datetime.datetime.fromtimestamp(
                ts, datetime.timezone.utc
            ).date()
        except (OverflowError, OSError, ValueError):
            continue
        if rec_date != today:
            continue
        # The turn's own spend when recorded; else the legacy running total.
        usage = rec.get(TURN_USAGE_KEY)
        if not isinstance(usage, dict):
            usage = rec.get("usage")
        if isinstance(usage, dict):
            total += int(usage.get("input_tokens", 0) or 0)
            total += int(usage.get("output_tokens", 0) or 0)
    return total


# ── Turn record fields beyond the five `/chat/import` requires ──────────────

# What THIS turn spent. `usage` on a record is `session.usage_acc` — the
# session's running total, which is what the panel's token footer shows — so
# summing `usage` over records counts turn 1 once per later turn of its
# session, and the daily cap tripped at a fraction of its limit (three 30-token
# turns read as 180). Records written before this key existed have only the
# running total; `_today_token_spend` falls back to it, over-counting them as
# before rather than guessing.
TURN_USAGE_KEY = "turn_usage"

# A turn that produced model output and then ended without `turn_done` (an
# abort, the tool-call cap, a mid-turn project switch, a stream error, a
# client disconnect). Recorded so a reload shows what ran — its tools changed
# the network — but for DISPLAY ONLY: GET /history never replays it to the
# model. Its assistant half is what the user saw (streamed text, and the names
# of the tools it requested), not provider messages; an interrupted step can
# end on a `tool_use` with no `tool_result`, which would make every later turn
# of the session invalid to the provider.
INTERRUPTED_KEY = "interrupted"
INTERRUPTED_REASON_KEY = "interrupted_reason"

_USAGE_FIELDS = ("input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_create_tokens")


def usage_since(now: dict[str, Any], start: dict[str, Any]) -> dict[str, int]:
    """The per-turn usage: `usage_acc` now minus `usage_acc` at turn start."""
    return {k: max(0, int(now.get(k, 0) or 0) - int(start.get(k, 0) or 0))
            for k in _USAGE_FIELDS}


def is_interrupted_turn(rec: dict[str, Any]) -> bool:
    return rec.get(INTERRUPTED_KEY) is True


class TurnTrace:
    """
    What a turn showed, read off its frames by `run_turn`.

    Built from the frames rather than from provider messages because the
    record is for display: a stream cut off mid-text has deltas the user saw
    and no final message, and a provider message is exactly what the record
    must never be mistaken for.
    """

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self._text: list[str] = []
        self.model_spoke = False
        self.completed = False
        self._done_reason: str | None = None
        self._error_kind: str | None = None
        self.profile_id: str | None = None

    def note(self, event: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        if event == "token":
            self.model_spoke = True
            self._text.append(str(payload.get("delta") or ""))
        elif event == "thinking":
            self.model_spoke = True
        elif event == "tool_request":
            self.model_spoke = True
            self._flush_text()
            self.blocks.append({"type": "tool_use",
                                "name": str(payload.get("tool_name") or "?")})
        elif event == "session_init":
            self.profile_id = payload.get("profile_id") or self.profile_id
        elif event == "turn_done":
            self.completed = True
        elif event == "error":
            self._error_kind = payload.get("error_kind") or self._error_kind
        elif event == "session_done":
            self._done_reason = payload.get("reason") or self._done_reason

    def _flush_text(self) -> None:
        text = "".join(self._text)
        self._text = []
        if text.strip():
            self.blocks.append({"type": "text", "text": text})

    def interrupted_record(self, *, ended_by: str | None) -> dict[str, Any] | None:
        """
        The fields of an interrupted-turn record, or None when there is none
        to write: the turn completed (its own record was written), or the model
        never produced anything (a refusal before the first token: nothing ran,
        and the error frame already said why).
        """
        if self.completed or not self.model_spoke:
            return None
        self._flush_text()
        return {
            "assistant": list(self.blocks),
            INTERRUPTED_KEY: True,
            # The turn's own account of how it ended wins; `ended_by` is for
            # an end it never got to report (a disconnect, an exception).
            INTERRUPTED_REASON_KEY: (self._done_reason or self._error_kind
                                     or ended_by or "unknown"),
        }


def append_turn(ctx: ProjectContext, turn: dict[str, Any]) -> None:
    """
    Append a single turn to `ctx`'s chat.jsonl, rotating if oversize.

    Acquires `ctx.chat_state.lock` for the entire critical section
    (rotation check + rotation + write) so a concurrent reader / appender
    observes EITHER the pre-rotation state OR the post-rotation state, never
    a half-applied rename + partial write. M9 + v4-MINOR-2 invariant.

    Silent no-op when the context is unbound (`get_persist_path` → None):
      * Phase 0 — the chatbot front-end is not yet wired, so this path is
        unreachable from user-driven flows. The no-op exists so eviction +
        future call sites can safely fire on any ctx without a guard.
      * Phase 1+ — bind-first UX ensures the user creates / loads a project
        before the chat panel accepts a turn, so this branch stays
        defensive.

    The turn dict shape is intentionally NOT validated here — Phase 2
    formalises it with a schema. Phase 0 callers (tests) pass simple dicts.
    """
    with ctx.chat_state.lock:
        path = get_persist_path(ctx)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotation under the SAME lock (v4-MINOR-2) — a concurrent appender
        # holding `lock` cannot observe a half-rotated state.
        if path.exists() and path.stat().st_size >= ROTATE_BYTES:
            _rotate_chat_jsonl_unlocked(path)
        # Append the turn. json.dumps with ensure_ascii=False so non-ASCII
        # user content (e.g. German project names, Chinese messages)
        # round-trips faithfully through chat.jsonl.
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(turn, ensure_ascii=False))
            f.write("\n")
            # Durability, and it is worth being precise about what this buys.
            # Closing the file (which the `with` already does) flushes Python's
            # userspace buffer into the OS page cache, so the desktop shell's
            # `os._exit()` shutdown rung never loses a turn — page-cache data
            # is kernel-side and survives a process that skips its exit
            # handlers. What it does NOT survive is a power cut or a kernel
            # panic, and that is the gap `fsync` closes.
            #
            # One turn per user message, so the cost is a disk round-trip at
            # human typing speed, not a hot loop. On macOS `fsync` is not a
            # barrier down to the platter (`F_FULLFSYNC` is), which is
            # accepted here: this protects a chat transcript, not a ledger.
            f.flush()
            os.fsync(f.fileno())


def _pending_turn_path_unlocked(ctx: ProjectContext) -> Path | None:
    """`chat.jsonl.pending` beside the transcript. Caller MUST hold the lock."""
    path = get_persist_path(ctx)
    if path is None:
        return None
    return path.with_suffix(path.suffix + ".pending")


def begin_pending_turn(ctx: ProjectContext, record: dict[str, Any]) -> None:
    """
    Record that a turn STARTED, before anything risky happens (#20 / QA #10).

    `append_turn` only ever runs on the success path, so until now a turn that
    died between Send and completion left no evidence at all: not in
    chat.jsonl, not in the session (gone with the process). The user's own
    message was simply lost, and the reload could not even say so.

    This file survives a crash for the same reason it is useless against a
    clean exit — the code that removes it (`clear_pending_turn`, in
    `run_turn`'s `finally`) does not get to run when the process dies. So the
    presence of the file after a restart IS the signal.

    Written via tmp + `os.replace` so a crash DURING this write leaves either
    the old record or the new one, never a half-record that would then be
    reported as an unreadable pending turn. fsync'd for the same reason
    `append_turn` is: the page cache survives `os._exit`, not a power cut.

    Best-effort throughout: a WAL that cannot be written must not stop the
    turn the user asked for. Silent no-op on an unbound context.

    KNOWN LIMIT — one pending slot per PROJECT, not per session. Two tabs
    running turns against the same project at once (each tab has its own
    session_id, so this is reachable) share this file: the second write
    overwrites the first, and whichever turn ends first clears it for both.
    The failure mode is strictly under-reporting — an interruption that goes
    unreported, never a wrong report and never a damaged transcript — so the
    single slot is accepted rather than keyed per session, which would make
    recovery a glob-and-choose over files no reader would ever clean up. The
    guarantee to state out loud is therefore: an interrupted turn on a
    project with ONE active conversation is always recoverable.
    """
    try:
        with ctx.chat_state.lock:
            pending = _pending_turn_path_unlocked(ctx)
            if pending is None:
                return
            pending.parent.mkdir(parents=True, exist_ok=True)
            tmp = pending.with_suffix(pending.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, pending)
    except OSError:
        logger.exception("chat: could not write the pending-turn record")


def read_pending_turn(ctx: ProjectContext) -> dict[str, Any] | None:
    """
    The pending record, or None when there is none / it is unreadable.

    An unreadable pending file is treated as absent rather than surfaced: it
    carries no message to show, and the only honest thing left to say about
    it is what `history_gap` already says about chat.jsonl.
    """
    try:
        with ctx.chat_state.lock:
            pending = _pending_turn_path_unlocked(ctx)
            if pending is None or not pending.exists():
                return None
            raw = pending.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def clear_pending_turn(ctx: ProjectContext) -> None:
    """
    Drop the pending record — the turn reached an end this process observed.

    Called from `run_turn`'s `finally`, so it runs on EVERY exit path the
    process lives through: normal completion, an error frame, a cap
    rejection, `GeneratorExit` on client disconnect. All of those are ends
    the user can see; none of them is the crash this file exists for.

    Never raises. It runs in a `finally`, where an exception would replace
    whatever real failure is already in flight.
    """
    try:
        with ctx.chat_state.lock:
            pending = _pending_turn_path_unlocked(ctx)
            if pending is None:
                return
            pending.unlink(missing_ok=True)
            pending.with_suffix(pending.suffix + ".tmp").unlink(missing_ok=True)
    except OSError:
        logger.exception("chat: could not clear the pending-turn record")


def flush_to_disk(ctx: ProjectContext) -> None:
    """
    Eviction hook for `_save_evicted_ctx` — Phase 0 no-op.

    Phase 0: `append_turn` writes synchronously, so there is no in-memory
    buffer to flush. This helper exists so the eviction code in
    `pypsa_service._save_evicted_ctx` has a stable call site that compiles
    today and can be expanded in Phase 1+ when a buffered append path lands
    (e.g. a batch of pending turns held under `ChatSession._lock`).

    INVARIANT: called from `_save_evicted_ctx` AFTER `_save_context` succeeds,
    INSIDE the same try/except umbrella, OUTSIDE `_registry_lock`. Any disk
    write here must therefore tolerate concurrent reads from a B6 path-scoped
    endpoint that resolved the SAME ctx milliseconds ago — `append_turn`'s
    `ctx.chat_state.lock` is the chokepoint.
    """
    # Phase 0: nothing to flush. Future Phase 1+ implementation might iterate
    # over pending buffered turns under `ctx.chat_state.session._lock` and
    # write each via `append_turn`.
    _ = ctx  # silence linter; intentional no-op
    return


def _rotate_chat_jsonl_unlocked(path: Path) -> None:
    """
    Rotate chat.jsonl by renaming to chat.jsonl.1 (overwriting any prior
    rotation). Caller MUST hold `ctx.chat_state.lock` (v4-MINOR-2:
    rotation under the same lock as append). NOT thread-safe on its own.

    Why rename rather than truncate? Renaming is atomic on POSIX and best-
    effort atomic on Windows (PathLib uses MoveFileEx with replace) — a
    crash mid-rotation leaves either the old file at chat.jsonl OR the new
    rotation, never an empty file. A truncate-then-append would lose
    everything on a crash between truncate and the next write.
    """
    backup = path.with_suffix(path.suffix + ".1")
    try:
        if backup.exists():
            backup.unlink()
        path.rename(backup)
    except OSError:
        # A failed rotation must NOT block the append — log and continue.
        # Worst case: chat.jsonl grows past ROTATE_BYTES temporarily until
        # the next append succeeds at rotation. Better than dropping turns.
        logger.exception(
            "chat: rotation of %s failed; continuing without rotation", path,
        )


# A6 — session history soft/hard caps. Trim drops COMPLETE turn groups so a
# tool_use is never left without its matching tool_result.
SESSION_MESSAGES_MAX: int = 400


def _message_is_tool_results(msg: dict[str, Any]) -> bool:
    content = msg.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def _is_turn_start(msg: dict[str, Any]) -> bool:
    """
    A user message that begins a turn, as opposed to one carrying tool
    results back to the model.

    Role alone is not enough and this is the whole subtlety of rewinding: in
    the Messages API a tool_result travels as `role: "user"`, so "the last user
    message" is usually the tail of a tool loop, not the question that started
    it. The A11 turn summary is also a role=="user" text message, and it stands
    in for many turns that are already gone — rewinding into it would delete
    the only remaining trace of them.
    """
    if msg.get("role") != "user":
        return False
    if _message_is_tool_results(msg):
        return False
    return not is_turn_summary(msg)


def rewind_session(session: "ChatSession", turns: int = 1) -> int:
    """
    Drop the last `turns` complete turns from the API history, and report how
    many messages went.

    This is what makes "retry" and "edit and resend" honest. `session.messages`
    is the array replayed to the model every turn and it lives here, on the
    server — so a retry that only clears the browser re-asks the question with
    the previous answer still in context two messages above it, and the model
    reads its own last answer and repeats it.

    REFUSES while a turn is in flight. `_run_turn_body` appends to this deque
    as the turn proceeds; truncating underneath that writer races it and can
    strand a tool_use with no tool_result — the same 400 the pairing-aware
    trim exists to avoid at the other end. Returning 0 lets the caller retry
    after `turn_done` rather than corrupting the session.

    The durable transcript (chat.jsonl) is deliberately NOT rewritten. It is a
    record of what happened, and the discarded exchange did happen; the retry
    appends to it as a new turn. So a reload shows both, which is the honest
    reading of a log.
    """
    if turns <= 0:
        return 0
    with session._lock:
        if session._turn_in_flight:
            return 0
        before = len(session.messages)
        for _ in range(turns):
            # Walk back to the most recent turn start and cut there.
            cut: int | None = None
            for i in range(len(session.messages) - 1, -1, -1):
                if _is_turn_start(session.messages[i]):
                    cut = i
                    break
            if cut is None:
                break
            while len(session.messages) > cut:
                session.messages.pop()
        return before - len(session.messages)


def _drop_oldest_turn_group(messages: collections.deque) -> bool:
    """
    Remove the oldest complete turn group from the left.

    Group shape: user (text) → assistant* → user(tool_result)*  (repeat
    assistant/tool_result pairs), stopping before the next non-tool-result
    user message. Stray leading tool_result messages are dropped alone
    (recovery from a previously-broken history).
    """
    if not messages:
        return False
    first = messages.popleft()
    if _message_is_tool_results(first):
        return True
    while messages:
        nxt = messages[0]
        role = nxt.get("role")
        if role == "assistant":
            messages.popleft()
            continue
        if role == "user" and _message_is_tool_results(nxt):
            messages.popleft()
            continue
        break
    return True


# A11 — the marker that identifies the synthetic summary message. Kept as a
# literal prefix rather than a side table because `session.messages` is a
# plain deque that gets rebuilt from chat.jsonl on reload; anything held
# beside it would not survive that round trip.
TURN_SUMMARY_PREFIX = "[Earlier conversation summary]"


# The summary rides on EVERY subsequent request, so an unbounded one would
# eat the context budget it exists to defend.
TURN_SUMMARY_MAX_CHARS = 1200


_SUMMARY_LINE_CHARS = 110


_SUMMARY_MAX_LINES = 8


def is_turn_summary(msg: dict[str, Any]) -> bool:
    """True for the synthetic message that stands in for trimmed turns."""
    content = msg.get("content")
    return (
        msg.get("role") == "user"
        and isinstance(content, str)
        and content.startswith(TURN_SUMMARY_PREFIX)
    )


def _describe_dropped(group: list[dict[str, Any]]) -> str | None:
    """One line for one dropped turn: what was asked, and what ran."""
    asked = ""
    tools: list[str] = []
    for msg in group:
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, str) and not asked:
            asked = content.strip()
        elif msg.get("role") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    if name and name not in tools:
                        tools.append(name)
    if not asked and not tools:
        return None
    line = f'· "{asked[:_SUMMARY_LINE_CHARS]}"' if asked else "· (tool-only turn)"
    if tools:
        line += f" → {', '.join(tools[:4])}"
    return line


def _render_summary(count: int, lines: list[str]) -> str:
    head = (
        f"{TURN_SUMMARY_PREFIX} {count} earlier "
        f"{'turn' if count == 1 else 'turns'} were dropped to stay inside the "
        f"context budget. You cannot see them; say so rather than guessing if "
        f"the user refers back to one."
    )
    body = "\n".join(lines[-_SUMMARY_MAX_LINES:])
    out = f"{head}\n{body}" if body else head
    if len(out) > TURN_SUMMARY_MAX_CHARS:
        out = out[:TURN_SUMMARY_MAX_CHARS - 1] + "…"
    return out


def _parse_summary(msg: dict[str, Any]) -> tuple[int, list[str]]:
    """Recover (count, lines) from an existing summary so drops accumulate."""
    text = str(msg.get("content") or "")
    lines = [ln for ln in text.split("\n")[1:] if ln.startswith("·")]
    count = 0
    for token in text.split("\n", 1)[0].split():
        if token.isdigit():
            count = int(token)
            break
    return count, lines


def trim_session_messages(
    messages: collections.deque,
    max_len: int | None = None,
) -> None:
    """
    Drop oldest complete turn groups until `len(messages) <= max_len`, and
    leave one summary message in their place (A11 / Improvement #11).

    The drop itself was already pairing-aware — it never orphans a tool_use.
    What it was not is *visible*: the agent did not experience a trim, it
    experienced those turns never happening, so a user referring back to one
    got a confident guess instead of "I no longer have that".

    The summary is deterministic rather than an LLM call. An extra model
    call here would sit inside a loop that already carries a bounded retry,
    a model-fallback path, and cache breakpoints that must stay byte-stable
    across retries — and it would have to be computed once per turn rather
    than once per attempt, or it would bill twice and move the breakpoint
    underneath itself. Recovering the REFERENT is the fix; better prose is
    not what was broken.
    """
    limit = SESSION_MESSAGES_MAX if max_len is None else max_len
    if len(messages) <= limit:
        return

    # Absorb any existing summary rather than dropping it (which would lose
    # the record) or prepending beside it (which would grow a pile of
    # summaries that eventually fills the window it defends).
    count, lines = 0, []
    if messages and is_turn_summary(messages[0]):
        count, lines = _parse_summary(messages.popleft())

    # The summary occupies a slot of its own, so once one exists the deque
    # has to come one below the cap to leave room. Every drop runs through
    # THIS loop — a second uncounted drop pass to make that room would
    # silently lose turns, which is the defect this function exists to fix.
    while True:
        target = max(limit - 1, 0) if (count or lines) else limit
        if len(messages) <= target:
            break
        before = list(messages)
        if not _drop_oldest_turn_group(messages):
            break
        dropped = before[:len(before) - len(messages)]
        # A stray leading tool_result is recovery from a previously-broken
        # history, not a turn — it gets no line, but the deque still shrank.
        line = _describe_dropped(dropped)
        if line:
            count += 1
            lines.append(line)

    if count or lines:
        messages.appendleft({"role": "user", "content": _render_summary(count, lines)})


# Sentinel values for `mode` in handle_save_lineage. Kept as plain strings
# so the call sites remain greppable across the repo.
SAVE_LINEAGE_REBIND_MOVE: str = "rebind_move"


SAVE_LINEAGE_COPY: str = "copy"


SAVE_LINEAGE_SCENARIO_COPY: str = "scenario_copy"


def _chat_paths_in(directory) -> tuple[Path, Path]:
    """`(chat.jsonl, chat.jsonl.1)` inside an ALREADY-RESOLVED directory."""
    d = Path(directory)
    return d / CHAT_FILENAME, d / (CHAT_FILENAME + ".1")


def _project_chat_paths(project_name: str) -> tuple[Path | None, Path | None]:
    """
    Resolve `(chat.jsonl, chat.jsonl.1)` for a project directory BY NAME,
    or `(None, None)` if the project name is empty / unresolvable.

    This is the FLAT, pre-tenancy shape — `PROJECTS_DIR / name` — and
    `get_persist_path` says in its own comment why that is not where a
    project's data lives under tenancy: storage is org-scoped at
    `projects_root/<org_uuid>/<project_uuid>/`, so this path is wrong or
    absent there. Callers that can resolve the real directory MUST pass it
    (`_chat_paths_in`); this remains the fallback for local mode and the
    legacy layout, where the flat path IS the project directory.
    """
    if not project_name:
        return None, None
    try:
        from routers.projects import PROJECTS_DIR
    except Exception:  # noqa: BLE001 — never break the lineage path on import
        return None, None
    return _chat_paths_in(PROJECTS_DIR / project_name)


def handle_save_lineage(
    ctx: ProjectContext,
    target_name: str,
    mode: str,
    source_name: str | None = None,
    *,
    source_dir=None,
    target_dir=None,
) -> None:
    """
    F12 — apply the appropriate chat.jsonl lineage rule on a project-save
    transition. Idempotent: safe to call when the source chat.jsonl does
    not exist (no-op).

    Modes:
      * ``rebind_move`` (Save-As) — the active context's `loaded_project` is
        being re-bound to `target_name`. The chat.jsonl currently at
        ``<PROJECTS_DIR>/<source>/chat.jsonl`` is RENAMED (moved) to
        ``<PROJECTS_DIR>/<target_name>/chat.jsonl``. The cached
        ``ctx.chat_state.persist_path`` is invalidated so the next
        `get_persist_path(ctx)` resolves the new directory. The rotation
        backup (``chat.jsonl.1``) is moved alongside.
      * ``copy`` (Save-a-Copy) — the source binding is unchanged; the new
        ``target_name`` directory receives a COPY of the source's chat.jsonl
        so the branched project has the conversation history but the active
        session continues at the source.
      * ``scenario_copy`` (create_scenario) — same shape as `copy`: the
        scenario directory receives a copy of the BASE project's chat.jsonl.

    Acquires ``ctx.chat_state.lock`` for the entire move/copy so a
    concurrent append from another browser tab can't observe a half-moved
    file. Errors are logged + swallowed — the user's project save must not
    fail because chat history could not be carried over.
    """
    import shutil

    # Source can be passed explicitly when the caller rebinds ctx.loaded_project
    # BEFORE invoking the lineage hook (Save-As route handler does this — by
    # the time _save_context's tail runs, ctx.loaded_project is already the
    # NEW name, so reading from ctx would point at the wrong directory). Fall
    # back to ctx.loaded_project for callers that don't reassign first
    # (Save-a-Copy / scenario_copy paths where the binding stays put).
    source = source_name if source_name is not None else ctx.loaded_project
    if not source or not target_name:
        return  # nothing to move/copy

    # Resolved directories win over names. Under tenancy the flat
    # `PROJECTS_DIR / name` path is not where either project lives, so
    # resolving by name found nothing and the new project started with the
    # conversation silently gone. The caller already resolves both ends
    # through the registry for the `uploads/` half of the same hook.
    src_path, src_backup = (
        _chat_paths_in(source_dir) if source_dir is not None
        else _project_chat_paths(source)
    )
    dst_path, dst_backup = (
        _chat_paths_in(target_dir) if target_dir is not None
        else _project_chat_paths(target_name)
    )
    if src_path is None or dst_path is None:
        return

    with ctx.chat_state.lock:
        try:
            if mode == SAVE_LINEAGE_REBIND_MOVE:
                # Save-As: MOVE source files to target dir. Both src and
                # dst directories are guaranteed to exist post-save.
                if src_path.exists():
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    if dst_path.exists():
                        # Save-As to an existing project — the v6 F1 backend
                        # guard at projects.py:976 has already ensured the
                        # user opted in (rebind=true). We overwrite the
                        # destination chat.jsonl to reflect the new binding.
                        dst_path.unlink()
                    shutil.move(str(src_path), str(dst_path))
                if src_backup.exists():
                    if dst_backup.exists():
                        dst_backup.unlink()
                    shutil.move(str(src_backup), str(dst_backup))
                # Invalidate the cached persist_path so the next
                # `get_persist_path(ctx)` resolves the new project dir.
                ctx.chat_state.persist_path = None

            elif mode in (SAVE_LINEAGE_COPY, SAVE_LINEAGE_SCENARIO_COPY):
                # Save-a-Copy / create_scenario: COPY the file. The active
                # context keeps its persist_path pointing at the source
                # (loaded_project hasn't changed).
                if src_path.exists():
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_path), str(dst_path))
                if src_backup.exists():
                    shutil.copy2(str(src_backup), str(dst_backup))

            else:
                logger.warning(
                    "chat: handle_save_lineage called with unknown mode "
                    "%r — no-op", mode,
                )
        except OSError as exc:
            logger.exception(
                "chat: handle_save_lineage(%s→%s, mode=%s) failed: %s",
                source, target_name, mode, exc,
            )


def handle_rename_lineage(
    ctx: ProjectContext,
    old_name: str,
    new_name: str,
) -> None:
    """
    On project rename, the project directory is renamed on disk by the
    underlying handler — `chat.jsonl` moves with the directory. The only
    thing we have to do client-side is INVALIDATE the cached
    `ctx.chat_state.persist_path` so the next `get_persist_path(ctx)` call
    re-resolves under the new binding.

    Closes the Phase 3 QA Gate C-13 gap: prior to this hook,
    `rename_project` updated `ctx.loaded_project` but left the cached
    persist_path pointing at the OLD directory, causing subsequent
    `append_turn` writes to land in the wrong directory.
    """
    # `old_name` / `new_name` are accepted for log clarity; the only state
    # mutation is the cache invalidation, performed under the lock to
    # serialize with any concurrent append.
    with ctx.chat_state.lock:
        ctx.chat_state.persist_path = None
    logger.debug("chat: rename lineage applied %s -> %s", old_name, new_name)


def handle_snapshot_lineage(
    ctx: ProjectContext,
    snapshot_dir: Path,
    mode: str,
) -> None:
    """
    C2 — include chat.jsonl in a project snapshot bundle on ``create``;
    overwrite the active chat.jsonl from the snapshot bundle on ``restore``.

    Modes:
      * ``create``  — copy the active project's chat.jsonl (and the
        rotation backup if present) into ``snapshot_dir``.
      * ``restore`` — copy chat.jsonl from ``snapshot_dir`` into the active
        project's directory, overwriting the existing file. Invalidates the
        persist_path cache so the next append re-resolves (the file content
        changed but the path did not).

    Best-effort: snapshot create/restore must not fail because chat history
    could not be included.
    """
    import shutil

    # Through the CONTEXT, which already knows where its storage lives — the
    # flat-by-name resolution captured an empty (or another tenant's) history
    # under tenancy. `get_persist_path` is the one resolver that handles both
    # layouts, and it caches on `ctx.chat_state` as a side benefit.
    if not ctx.loaded_project:
        # `get_persist_path` returns None only for `loaded_project is None`;
        # an empty STRING would resolve to `PROJECTS_DIR / "" / chat.jsonl`,
        # i.e. the projects root itself. The by-name resolver this replaced
        # rejected both, so keep rejecting both.
        return
    active_path = get_persist_path(ctx)
    if active_path is None:
        return
    active_backup = active_path.parent / (CHAT_FILENAME + ".1")
    snap_chat = snapshot_dir / CHAT_FILENAME
    snap_backup = snapshot_dir / (CHAT_FILENAME + ".1")

    with ctx.chat_state.lock:
        try:
            if mode == "create":
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                if active_path.exists():
                    shutil.copy2(str(active_path), str(snap_chat))
                if active_backup.exists():
                    shutil.copy2(str(active_backup), str(snap_backup))
            elif mode == "restore":
                if snap_chat.exists():
                    active_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(snap_chat), str(active_path))
                else:
                    # Snapshot has no chat.jsonl — clear the active one too
                    # so the restored project state is consistent.
                    if active_path.exists():
                        active_path.unlink()
                if snap_backup.exists():
                    shutil.copy2(str(snap_backup), str(active_backup))
                elif active_backup.exists():
                    active_backup.unlink()
                # File content changed; the path didn't but the persist_path
                # cache is invalidated for symmetry with the rebind_move
                # path.
                ctx.chat_state.persist_path = None
            else:
                logger.warning(
                    "chat: handle_snapshot_lineage unknown mode %r", mode,
                )
        except OSError as exc:
            logger.exception(
                "chat: handle_snapshot_lineage(mode=%s) failed: %s",
                mode, exc,
            )


# Thinking blocks the API will reject on replay. `thinking` requires both
# `thinking` and `signature`; `redacted_thinking` requires `data`. Blocks
# written by the pre-fix serialiser (bare {"type": "thinking"}) are already
# on disk in users' chat.jsonl — see _sanitise_history_message.
_THINKING_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "thinking": ("thinking", "signature"),
    "redacted_thinking": ("data",),
}


def _thinking_block_is_wellformed(block: Any) -> bool:
    """
    True unless `block` is a thinking / redacted_thinking block whose required
    field is ABSENT or not a string. Non-thinking blocks and non-dict entries
    are always True — this predicate only ever rejects the shape that produced
    the observed 400.

    PRESENCE AND TYPE, NOT TRUTHINESS — do not "tighten" this to `all(...)` on
    the values. Measured against the live API (SDK 0.117.0, claude-sonnet-5,
    reasoning-heavy prompt): adaptive thinking is on by default and returns
    ThinkingBlock(thinking="", signature=<436 chars>) — an EMPTY thinking text
    with a valid signature. That block is well-formed and replays fine; a
    truthiness test drops it and silently discards the model's signed
    reasoning from history. Only the shape the old serialiser produced —
    the field missing entirely — is malformed.
    """
    if not isinstance(block, dict):
        return True
    required = _THINKING_REQUIRED_FIELDS.get(block.get("type"))
    if required is None:
        return True
    return all(isinstance(block.get(field), str) for field in required)


def _sanitise_history_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """
    Drop malformed thinking blocks from one history message.

    The pre-fix serialiser persisted bare {"type": "thinking"} blocks into
    live sessions' chat.jsonl. Fixing the serialiser does not repair what is
    already stored: rehydrating that history replays the same invalid shape
    and 400s again ('...thinking.thinking: Field required'). A thinking block
    with no content carries no information and the API accepts an assistant
    turn without one, so dropping is lossless. Well-formed thinking blocks
    are preserved — the API rejects a turn whose signed thinking is altered.

    Returns None when the message has no blocks the API will accept — whether
    they were dropped here or the list arrived empty. BOTH cases must return
    None: `content: []` is itself a 400 ("all messages must have non-empty
    content"), and it is reachable without any dropping at all, from a refused
    or aborted generation whose provider `message_done` event (`final_blocks`
    in `run_turn`, the seam's serialised-blocks source) comes back empty. An
    earlier version tested `len(kept) == len(content)` first, which is `0 == 0`
    for an already-empty list and returned it unchanged — a guard the
    docstring claimed but the code did not have.

    Otherwise returns the message unchanged (same object) when nothing needed
    dropping, or a shallow copy with the surviving blocks.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    kept = [b for b in content if _thinking_block_is_wellformed(b)]
    if not kept:
        return None
    if len(kept) == len(content):
        return msg
    return {**msg, "content": kept}
