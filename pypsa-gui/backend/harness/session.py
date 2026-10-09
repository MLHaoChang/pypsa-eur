"""
Chat sessions: the per-session state (history, usage, pending confirmations,
the active workflow), the in-process registry with its idle TTL and
resident cap, and the test reset hook.

Moved from harness/loop.py (the former services/chat_service.py) on
2026-10-05, chat harness issue 08, by AST selection of whole top-level
nodes; the loop re-imports every function and class, so `chat_service.<name>`
is the same object, and forwards every tunable here (PEP 562), so a read
through the alias is live. A tunable is patched on THIS module (see
harness/README.md, "Splitting the loop").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from services.llm_config import DEFAULT_MODEL
from typing import Any
import collections
import os
import threading
import time
import uuid
from harness.history import _sanitise_history_message
from harness.ratelimit import _RATE_BUCKETS, _RATE_LOCK
from harness.history import trim_session_messages
from harness.metrics import _reset_metrics_for_tests


# Confirmation card TTL (Phase 2 / F13). Tokens older than this are rejected
# with 409 error_kind='confirmation_expired'; the agent re-prompts with a
# fresh token. 300s matches the v6 plan default.
CONFIRMATION_TTL_SECONDS: float = 300.0


# Result-ref FIFO cap (Phase 2 / Phase 4 polish). The session keeps a small
# in-memory list of recent (tool_name, result_summary) refs so the model can
# reference earlier outputs without re-fetching. FIFO-capped so a long turn
# doesn't grow unbounded.
RESULT_REFS_MAXLEN: int = 50


# Idle-session eviction (chat reliability). `_SESSIONS` is process-lifetime;
# without eviction it leaks one ChatSession (a 400-msg deque + usage + pending
# confirmations) per abandoned session id — every browser reload/tab mints one.
# A cheap sweep runs opportunistically on session creation; chat.jsonl + GET
# /history back replay, so dropping an idle in-memory session is safe.
SESSION_IDLE_TTL_SECONDS: float = float(
    os.environ.get("PYPSA_GUI_CHAT_SESSION_TTL", str(24 * 3600))
)


SESSION_MAX_RESIDENT: int = int(os.environ.get("PYPSA_GUI_CHAT_SESSION_MAX", "1000"))


@dataclass
class PendingConfirmation:
    """
    Server-stamped confirmation card record (F13). Created when the agent
    requests user approval for a destructive / execution tool; consumed
    EXACTLY ONCE by `/api/chat/{session_id}/confirm`.

    `expires_at` is a monotonic-clock deadline (so wall-clock changes do not
    advance / retreat TTL). Lookups consume the entry — single-use enforced
    by `ChatSession.consume_confirmation` under `ChatSession._lock`.
    """

    token: str
    tool_name: str
    args: dict[str, Any]
    safety_tier: str  # one of DESTRUCTIVE_TIERS (+ "write" in Guided mode)
    created_at: float
    expires_at: float

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self.expires_at


@dataclass
class ChatSession:
    """
    In-memory chatbot conversation session for ONE project.

    `session_id` — stable UUID hex (audit-log prefix `agent:<verb>:<session6>`).

    `_lock` — per-session mutex. v4-MINOR-3 invariant: guards every mutation
    of `pending_confirmations` / `confirmation_decisions` / `result_refs` /
    `usage_acc` so two concurrent `/confirm` POSTs (from two browser tabs,
    or a quick double-click) serialise — one wins (200), the other observes
    a missing token and returns 404 (`error_kind='unknown_confirmation_token'`).

    `confirmation_decisions` — once `/confirm` resolves a token, the decision
    ('approve' | 'deny' | 'expired') is recorded here AND `_decision_event`
    is set. The agent loop blocks on `_decision_event.wait()` and consults
    this dict to learn the outcome. Phase 2 stub uses a per-token Event;
    Phase 3 may switch to asyncio.Future once the SDK is wired.

    `abort_event` — M8 invariant. Set when the SSE generator observes a
    client disconnect; any cooperating worker thread checks this between
    iterations to shut down cleanly.

    `usage_acc` — running token totals (in / out / cache_read / cache_create).
    M10: only token counts are stored; the client renders them as-is. No
    cost figure is computed or stored anywhere.

    `result_refs` — FIFO of recent tool-call result summaries the agent can
    cite without re-issuing the tool call. Bounded by RESULT_REFS_MAXLEN.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # WHO this conversation belongs to, as a string user id. `None` means "no
    # owner recorded", which the /confirm, /rewind and /abort routes treat as
    # REFUSE rather than allow — see `session_owner_allows`. Recorded once at
    # creation and never reassigned: letting a later caller claim an existing
    # session would be the hole this closes.
    owner_user_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    # Monotonic stamp of the last time this session was touched (created or
    # resolved via get_or_create_session). Drives idle eviction.
    last_activity: float = field(default_factory=time.monotonic)
    model: str = DEFAULT_MODEL
    # Task 7 — the LLM profile this session is bound to. `None` until the
    # router's first `/stream` call resolves + binds one (or a caller that
    # constructs a `ChatSession` directly and never sets it — `run_turn`
    # falls back to `llm_config.resolve_legacy_model(session.model)` in that
    # case, so a bare `ChatSession(model=...)` keeps resolving the profile
    # its `model` string always implied). `bound_wire` is the bound
    # profile's `wire` ("anthropic" | "openai") — kept alongside `profile_id`
    # rather than re-resolved on every check because it is what the
    # cross-wire guard in `routers/chat.py` compares against, and a profile
    # can be edited/deleted out from under a live session id.
    profile_id: str | None = None
    bound_wire: str | None = None
    # The active workflow, {"id", "step"}, or None (chat harness issue 06).
    # Set by the start_workflow / advance_workflow / end_workflow tools; the
    # per-turn addendum (`_workflow_addendum`) reads it.
    workflow: dict[str, Any] | None = None
    # Catalogue view; kept separate from project identity and permissions.
    toolset: str = "all"
    job_wait_progress: dict[str, Any] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    pending_confirmations: dict[str, PendingConfirmation] = field(default_factory=dict)
    confirmation_decisions: dict[str, str] = field(default_factory=dict)
    # Per-token Event the agent waits on. Keyed by token (cleared after
    # consume). Always created under _lock so two concurrent /confirm cannot
    # observe a missing event.
    _decision_events: dict[str, threading.Event] = field(default_factory=dict)
    abort_event: threading.Event = field(default_factory=threading.Event)
    # #19 — True for the lifetime of one in-flight run_turn on this session.
    # Guarded by `_lock` (v4-MINOR-3 doctrine): set/checked at run_turn entry,
    # cleared in run_turn's try/finally so a concurrent second run_turn on the
    # same session_id (two tabs) is rejected with turn_already_in_flight.
    _turn_in_flight: bool = field(default=False)
    # CH-3 — the undo stacks this turn has already pushed its one snapshot
    # onto (each project's `_UndoState` object; see `_snapshot_for_turn_undo`).
    # A list of objects compared by identity, not a set of names: the state
    # object is what `reset_network`/`set_network` carry across an in-place
    # swap (undo, clustering), so it identifies "this project's stack" where a
    # name would not. Emptied at the start and end of every turn.
    _undo_snapshotted: list = field(default_factory=list)
    # W-3 (ADR-0001) — whether the provider has EVER reported usage for this
    # session. `stream_options.include_usage` is a request, not a guarantee:
    # an OpenAI-compatible endpoint that omits the usage chunk leaves
    # `usage_acc` at its zero initialisation, and shipping that renders as
    # "0 in / 0 out · 0 cached" — indistinguishable from a legitimately
    # unused session, which is precisely the "unresolvable rendered as a
    # real value" shape ADR-0001 forbids. This wire is new on this branch,
    # so the state is new too.
    usage_reported: bool = False
    # W-3 — turns started on this session, the fallback bound for an endpoint
    # that never reports usage. See the ceiling in `_run_turn_body`.
    turns_started: int = 0
    usage_acc: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_create_tokens": 0,
        }
    )
    result_refs: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=RESULT_REFS_MAXLEN)
    )
    # Multi-turn message history for the Anthropic Messages API. Each entry
    # is a dict matching the SDK's message shape ({role, content}). Run_turn
    # appends user + assistant + tool_result messages per turn so subsequent
    # turns see the full conversation context. Bounded via pairing-aware
    # trim (A6) — NOT deque(maxlen=…), which can orphan a tool_use without
    # its tool_result and make the next Anthropic call reject the sequence.
    messages: collections.deque = field(default_factory=collections.deque)

    # ── Identity ────────────────────────────────────────────────────────────
    def session6(self) -> str:
        """First 6 hex chars of session_id — audit-log action-prefix tag."""
        return self.session_id[:6]

    def append_history_message(self, msg: dict[str, Any]) -> None:
        """
        Append one history message and trim pairing-aware if over cap.

        Scope of the sanitisation here — stated precisely, because the earlier
        wording overclaimed: this is the only writer to `self.messages`, so
        every entry in THIS deque is sanitised, whether it came from the live
        turn or from the GET /history rehydration that replays chat.jsonl.
        It is NOT the array sent to the API — `_run_turn_body` keeps a separate
        local `messages` list which it appends to directly. That list is
        seeded from this deque once per turn (and sanitised again at the seed,
        since a caller may pass its own `message_history=`); everything
        appended to it afterwards is freshly serialised by
        `_serialise_for_anthropic` and therefore already well-formed.

        A message with no blocks the API will accept is skipped entirely —
        whether it was emptied by dropping or arrived with `content: []`,
        which an aborted or refused generation produces. An empty content
        array is itself a 400, so admitting one would swap the bug this
        branch fixes for a neighbouring one.
        """
        sanitised = _sanitise_history_message(msg)
        if sanitised is None:
            return
        self.messages.append(sanitised)
        trim_session_messages(self.messages)

    # ── Confirmation lifecycle (F13 + v4-MINOR-3) ──────────────────────────
    def issue_confirmation(
        self, *, tool_name: str, args: dict[str, Any], safety_tier: str,
        ttl_seconds: float | None = None,
    ) -> PendingConfirmation:
        """
        Mint a fresh single-use token bound to (tool_name, args). Caller emits
        the token to the client in a `tool_pending_confirmation` SSE frame and
        BLOCKS on `wait_for_decision(token, …)` until the user
        approves / denies / TTL fires.

        `ttl_seconds` resolves to the module-level `CONFIRMATION_TTL_SECONDS`
        at CALL TIME (not function-def time) so a test can monkeypatch the
        module attribute and observe the new value without touching the
        per-call kwarg.
        """
        if ttl_seconds is None:
            ttl_seconds = CONFIRMATION_TTL_SECONDS
        token = uuid.uuid4().hex
        now = time.monotonic()
        pc = PendingConfirmation(
            token=token,
            tool_name=tool_name,
            args=args,
            safety_tier=safety_tier,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        with self._lock:
            self.pending_confirmations[token] = pc
            self._decision_events[token] = threading.Event()
        return pc

    def record_decision(self, token: str, decision: str) -> PendingConfirmation:
        """
        Atomically pop a pending token + record the decision. Idempotent on
        re-call: returns 404 (replay defence) if the token was already
        consumed by an earlier concurrent /confirm POST.

        Returns the popped PendingConfirmation on success. Raises
        HTTPException 404 / 409 with structured error_kind on
        replay / expiry. v4-MINOR-3: both the lookup AND the pop happen
        under `_lock`, so two concurrent /confirm POSTs against the same
        token cannot BOTH succeed.
        """
        # Lazy import — avoids services.chat_service ↔ fastapi at module load.
        from fastapi import HTTPException
        with self._lock:
            pc = self.pending_confirmations.pop(token, None)
            event = self._decision_events.pop(token, None)
            if pc is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_kind": "unknown_confirmation_token",
                        "message": (
                            "confirmation token not found; it may have been "
                            "consumed by another request, expired and pruned, "
                            "or never existed."
                        ),
                    },
                )
            if pc.is_expired():
                # Pop already happened; signal the waiting agent so it can
                # surface error_kind='confirmation_expired' rather than block.
                self.confirmation_decisions[token] = "expired"
                if event is not None:
                    event.set()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_kind": "confirmation_expired",
                        "tool_name": pc.tool_name,
                        "message": (
                            f"confirmation token for {pc.tool_name!r} "
                            f"expired ({int(time.monotonic() - pc.created_at)}s "
                            f"after creation; TTL "
                            f"{int(CONFIRMATION_TTL_SECONDS)}s). Ask the "
                            "agent to re-prompt with a fresh token."
                        ),
                    },
                )
            if decision not in ("approve", "deny"):
                # Defensive: keep the token around for a retry under _lock.
                self.pending_confirmations[token] = pc
                if event is not None:
                    self._decision_events[token] = event
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error_kind": "invalid_decision",
                        "message": "decision must be 'approve' or 'deny'",
                    },
                )
            self.confirmation_decisions[token] = decision
        if event is not None:
            event.set()
        return pc

    def wait_for_decision(self, token: str, timeout: float | None = None) -> str:
        """
        Block until /confirm resolves the token OR the TTL fires OR the
        session is aborted. Returns the decision string ('approve' / 'deny'
        / 'expired' / 'aborted').
        """
        with self._lock:
            event = self._decision_events.get(token)
            pc = self.pending_confirmations.get(token)
        if event is None or pc is None:
            # Token never issued or already consumed by `record_decision`
            # (which pops pending + events but writes the decision into
            # `confirmation_decisions`). Pop the decision so the dict
            # doesn't accumulate across long sessions (INT-009).
            with self._lock:
                decision = self.confirmation_decisions.pop(token, None)
            return decision or "expired"

        # Compute remaining TTL relative to the token's expiry, capped by the
        # caller-provided timeout if any.
        now = time.monotonic()
        remaining = max(0.0, pc.expires_at - now)
        wait_for = remaining if timeout is None else min(remaining, timeout)
        # Wake on the decision event OR on abort. Poll abort periodically
        # so we don't need a separate combined-event primitive.
        poll = 0.1
        deadline = now + wait_for
        while True:
            if self.abort_event.is_set():
                with self._lock:
                    self.confirmation_decisions[token] = "aborted"
                return "aborted"
            slice_ = min(poll, max(0.0, deadline - time.monotonic()))
            if event.wait(slice_):
                # Phase 4 QA fix (INT-009): pop the decisions entry once
                # consumed so long-running sessions don't leak unbounded
                # tokens.
                with self._lock:
                    return self.confirmation_decisions.pop(token, "expired")
            if time.monotonic() >= deadline:
                # TTL elapsed without /confirm. Mark expired + pop the token
                # under _lock so a late /confirm sees 404 (or 409 if its
                # caller raced the expiry window — still safe under _lock).
                with self._lock:
                    self.pending_confirmations.pop(token, None)
                    self._decision_events.pop(token, None)
                    # Don't write expired into the dict — it's a transient
                    # state that the caller observes via this return value.
                    self.confirmation_decisions.pop(token, None)
                return "expired"

    # ── Usage / result refs (v4-MINOR-3) ───────────────────────────────────
    def accrue_usage(self, **deltas: int) -> None:
        with self._lock:
            for k, v in deltas.items():
                if k in self.usage_acc:
                    self.usage_acc[k] += int(v)
                    # W-3 — a real report arrived, so the totals below now
                    # mean something. Set on any recognised key, including an
                    # honest zero: "the endpoint told us zero" is a different
                    # fact from "the endpoint never told us".
                    self.usage_reported = True

    def push_result_ref(self, ref: dict[str, Any]) -> None:
        with self._lock:
            self.result_refs.append(ref)


_SESSIONS: dict[str, ChatSession] = {}


_SESSIONS_LOCK = threading.Lock()


def get_session(session_id: str) -> ChatSession | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(session_id)


# CH-6 — the transcript key naming the user who ran a turn (the session owner).
TURN_AUTHOR_KEY = "owner_user_id"


def turn_is_callers(rec: dict[str, Any], user_id: str | None) -> bool:
    """
    Did `user_id` run this transcript turn?

    `chat.jsonl` is per PROJECT and shared by everyone who works on it, but a
    chat session is per USER. GET /history used to hand back the session of the
    LAST turn in the file, whoever ran it, and mint it owned by whoever asked
    first — so a co-member could end up owning another user's thread, which the
    `/stream` ownership check then refused to its real author. Picking the
    caller's own last turn removes the conflict at the source.

    A record without an author predates this key, and keeps the behaviour it
    always had: it counts as the caller's. Treating it as nobody's in server
    mode was the stricter option and the worse one — every server user would
    lose session continuity (and the model its prior context) on the first
    reload after upgrading — while what it would prevent is bounded already:
    `/history` will not hand back or rebuild a live session with a DIFFERENT
    known owner, and `/stream` refuses one with `session_not_yours`, which the
    panel recovers from by starting a new chat. Legacy records age out with
    each user's next turn. With no caller identity at all there is nothing to
    distinguish, which is also the pre-existing behaviour.
    """
    if user_id is None:
        return True
    author = rec.get(TURN_AUTHOR_KEY)
    if author is None:
        return True
    return str(author) == str(user_id)


def session_owner_allows(sess: "ChatSession", user_id: str | None) -> bool:
    """
    May `user_id` act on `sess`?

    FAIL-CLOSED, deliberately. An owner-less session is refused rather than
    shared: if a future creation path forgets to record the owner, the symptom is
    "I cannot abort my own turn" — loud and fixed in minutes — instead of silently
    reopening the hole this closes. `tests/test_chat_session_ownership.py` asserts
    the normal path DOES record an owner, so that is a caught bug rather than a
    discovered outage.

    Before this existed, `/confirm`, `/rewind` and `/abort` authenticated (the
    global /api middleware) and authorized nothing: `_SESSIONS` is a process
    global and any signed-in caller who knew a session id could truncate a
    stranger's conversation, kill their in-flight turn, or supply the approval
    for their destructive tool. Verified cross-ORG before the fix.
    """
    if user_id is None:
        # Local mode issues no cookie and has exactly one identity; nothing to
        # distinguish, and refusing would break the desktop build.
        import local_mode

        return local_mode.is_local_mode()
    return sess.owner_user_id == str(user_id)


def _evict_idle_sessions_locked(now: float) -> None:
    """
    Drop idle-past-TTL sessions, then enforce the LRU resident cap.

    ASSUMES `_SESSIONS_LOCK` is already held: it pops entries directly rather
    than calling `drop_session` (which re-acquires the non-reentrant lock and
    would deadlock). Cheap — one pass over a small dict on session creation.
    """
    if SESSION_IDLE_TTL_SECONDS > 0:
        stale = [
            sid for sid, s in _SESSIONS.items()
            if now - s.last_activity > SESSION_IDLE_TTL_SECONDS
        ]
        for sid in stale:
            _SESSIONS.pop(sid, None)
    if SESSION_MAX_RESIDENT > 0 and len(_SESSIONS) > SESSION_MAX_RESIDENT:
        # Evict the least-recently-active sessions until back at the cap.
        ordered = sorted(_SESSIONS.values(), key=lambda s: s.last_activity)
        for s in ordered[: len(_SESSIONS) - SESSION_MAX_RESIDENT]:
            _SESSIONS.pop(s.session_id, None)


def get_or_create_session_reporting(
    session_id: str | None = None,
    *,
    model: str = DEFAULT_MODEL,
    owner_user_id: str | None = None,
) -> tuple[ChatSession, bool]:
    """
    Resolve-or-create a session, reporting whether THIS call minted it.

    `created` is True only when this call registered a brand-new session --
    the only safe basis for a caller (`GET /history`'s rehydration) to adopt
    a profile onto it. Existence-check and creation happen under a SINGLE
    `_SESSIONS_LOCK` acquisition (fix round 2): the round-1 fix read
    "already registered?" via a standalone `get_session` call and then
    creating/fetching via a SEPARATE `get_or_create_session` call -- two
    critical sections with a gap between them where a concurrent `/stream`
    could register-and-bind the session. Whoever observes it as freshly
    created here did so atomically with the registration itself, so there's
    no stale read to race.

    Touches `last_activity` (create or reuse) and opportunistically sweeps idle
    sessions so the in-memory registry can't grow unbounded.
    """
    with _SESSIONS_LOCK:
        now = time.monotonic()
        _evict_idle_sessions_locked(now)
        if session_id and session_id in _SESSIONS:
            sess = _SESSIONS[session_id]
            sess.last_activity = now
            return sess, False
        sess = ChatSession(model=model)
        # Set on CREATE only. An existing session's owner is never reassigned:
        # `get_or_create` is reached by /stream and /history, and letting the
        # second caller overwrite the owner would let anyone adopt a live
        # session just by naming its id.
        sess.owner_user_id = owner_user_id
        if session_id:
            sess.session_id = session_id
        sess.last_activity = now
        _SESSIONS[sess.session_id] = sess
        return sess, True


def get_or_create_session(
    session_id: str | None = None,
    *,
    model: str = DEFAULT_MODEL,
    owner_user_id: str | None = None,
) -> ChatSession:
    """
    Resolve a session by id, creating a fresh one if unknown. Use the same
    `session_id` across `/stream` and `/confirm` calls so the LLM/UI/server
    agree on which conversation a token belongs to.

    Thin wrapper over `get_or_create_session_reporting` -- kept because its
    signature/return type is pinned by callers and tests that don't care
    which branch fired.
    """
    sess, _created = get_or_create_session_reporting(
        session_id, model=model, owner_user_id=owner_user_id,
    )
    return sess


def drop_session(session_id: str) -> None:
    """Called by `/abort` to release the session record. Safe on unknown id."""
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session_id, None)


def _reset_sessions_for_tests() -> None:
    """
    Test-only cleanup hook so the registry can't bleed across pytest runs.

    Also clears the #20 metrics and #26 rate-limit buckets — the chat test
    suites' autouse `_reset_chat_sessions` fixture calls this around every
    test, so folding the resets here keeps turn-counts / bucket state from
    bleeding without adding a second autouse seam.
    """
    with _SESSIONS_LOCK:
        _SESSIONS.clear()
    _reset_metrics_for_tests()
    with _RATE_LOCK:
        _RATE_BUCKETS.clear()
