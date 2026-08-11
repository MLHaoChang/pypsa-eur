"""
Phase 2 chatbot integration v6 — chat router.

Endpoints (mounted under prefix `/api/chat` in main.py):
  * GET  /health  — cheap probe for the frontend; reports whether the
    Anthropic API key is present without leaking its value.
  * POST /stream  — open a Server-Sent Events stream for one turn. Body
    body: `{session_id?, model?, script?, message?}`. The Phase 2 stub
    drives `script` directly so the SSE protocol can be exercised without
    an LLM call; Phase 3 wires the real Anthropic SDK call.
  * POST /{session_id}/confirm — resolve a pending confirmation token.
    Body: `{token: str, decision: "approve" | "deny"}`. v4-MINOR-3:
    serialised under `ChatSession._lock`; concurrent retries see 404.
  * POST /{session_id}/abort — set `session.abort_event` so any open SSE
    generator + cooperating tool worker shut down cleanly.

The router is intentionally small — all stateful logic lives in
`services.chat_service` so it stays unit-testable without the FastAPI
TestClient.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from db.models import Session as SessionRow
from db.models import User
from deps import current_session, optional_user
from services import app_secrets, chat_service


router = APIRouter()


class StreamRequest(BaseModel):
    """
    Body for POST /api/chat/stream.

    `session_id` — caller-provided so /confirm and /abort can resolve the
    same session. Server creates one if omitted.
    `model`     — Anthropic model identifier; Phase 3 honours this.
    `script`    — Phase 2 STUB driver. Tests inject a list of frame dicts
                  (see chat_service.agent_loop_stub for the schema). Phase 3
                  ignores this field (the real LLM emits frames).
    `message`   — User message text. Phase 2 stub records it as a token
                  frame for the assistant; Phase 3 sends it to the SDK.
    `attachment_file_ids` — Phase C. List of `upload_service` file_ids to
                  forward to Anthropic as multimodal content blocks
                  (images + PDFs). Order is preserved. Excel / Word / CSV
                  files are NOT valid attachments — they go through the
                  `read_excel_sheet` tool instead.
    """

    session_id: str | None = None
    model: str | None = None
    script: list[dict[str, Any]] | None = None
    message: str | None = None
    attachment_file_ids: list[str] | None = None
    # Deixis — what the user is LOOKING AT when they hit send, so "why is this
    # so high?" has a referent. Identifiers only; the allowlist that enforces
    # that lives in `chat_service._format_ui_context`, not here, so a client
    # attaching values fails closed. Optional is load-bearing: the smoke
    # harness and the existing test suite send neither field.
    ui_context: dict[str, Any] | None = None
    # 'voice' | 'text'. A field rather than an inference because speech
    # reciprocity depends on it and reconstructing it later from timing or
    # content is guesswork. Carried now; the spoken-reply half is not built.
    input_mode: str | None = None


class ConfirmRequest(BaseModel):
    token: str
    decision: str  # "approve" | "deny"


class ApiKeyRequest(BaseModel):
    """
    Body for PUT /api/chat/settings/api-key.

    No `Field(max_length=…)` here on purpose: `app_secrets.validate_value`
    owns every rule about what is storable, and duplicating one of them in the
    schema splits the error copy across two layers that would then drift.
    """

    value: str


class ImportRequest(BaseModel):
    """
    Body for POST /api/chat/import (#27).

    `turns` — a list of transcript records (the same shape GET /export emits).
              Each must be a dict with ts / session_id / model / user /
              assistant keys; the route rejects the whole batch on the first
              malformed entry.
    `mode`  — only `append` is supported (records are appended to the active
              project's chat.jsonl); reserved for a future `replace` mode.
    """

    turns: list[dict[str, Any]]
    mode: Literal["append"] = "append"


@router.get("/health")
def chat_health() -> dict[str, Any]:
    """
    Cheap probe — the frontend uses this to decide whether to enable the
    ChatPanel. Never echoes the actual API key.
    """
    import os
    api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "ok": True,
        "anthropic_api_key_present": api_key_present,
        "default_model": chat_service.DEFAULT_MODEL,
        "confirmation_ttl_seconds": chat_service.CONFIRMATION_TTL_SECONDS,
    }


# ── U-1 — supplying the API key from inside the app ─────────────────────────
#
# These live on the CHAT router, not the admin one, and that is the whole point.
# `main.py` mounts `admin.router` behind `Depends(local_mode.reject_in_local_mode)`,
# so every `/api/admin/*` route 404s in the desktop app — which is precisely the
# deployment that has no `backend/.env` and therefore no key. Putting the
# setting there would have made it unreachable in the only place it is needed.
#
# The gate is `is_super_admin`, matching `solve_queue.clear_finished`: this key
# is one process-global environment variable shared by every organisation, so an
# ORG admin has no authority over it. Local mode seeds its single user with
# `is_super_admin=True` (`local_mode.py:132`), so the desktop app passes.


def _require_super_admin(user: User | None) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail=(
                "The Anthropic API key is shared by every organization on this "
                "instance, so only super-admins can change it."
            ),
        )
    return user


@router.get("/settings/api-key")
def get_api_key_settings(
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """Whether a key is configured, where it came from, and a 4-char hint."""
    _require_super_admin(user)
    return app_secrets.status("ANTHROPIC_API_KEY")


@router.put("/settings/api-key")
def put_api_key_settings(
    payload: ApiKeyRequest,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """
    Store an Anthropic API key and apply it to this process immediately.

    Applying it in-process is not a convenience: a packaged `.app` gives the
    user no way to restart the backend, so a key that only took effect on the
    next launch would read as "saving did nothing".
    """
    _require_super_admin(user)
    try:
        return app_secrets.set_secret("ANTHROPIC_API_KEY", payload.value)
    except app_secrets.SecretValueError as exc:
        # 422, not 400: this is a body-validation failure in the same class as
        # the pydantic ones, and the message is written to be shown verbatim.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/settings/api-key")
def delete_api_key_settings(
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """Forget the stored key — from the file and from this process."""
    _require_super_admin(user)
    return app_secrets.clear_secret("ANTHROPIC_API_KEY")


def _recover_pending_turn(ctx: Any) -> dict[str, Any] | None:
    """
    Resolve the WAL record left by an interrupted turn (#20), or None.

    Two cases have to be told apart, and the file alone cannot do it — a
    pending record looks identical whether the turn died with the process or
    is streaming right now in another tab:

      * The owning session is in this process AND has a turn in flight. The
        turn is alive. Report nothing and — critically — leave the file
        alone: deleting it here would strip a running turn of the protection
        it is currently relying on.
      * Otherwise (the usual post-restart case, where SESSIONS is empty).
        Nobody is going to finish this turn. Report it, then clear it, so one
        interruption produces one notice rather than a permanent banner.

    The turn is deliberately NOT promoted into `turns`. It was never
    answered; writing it into the transcript would fabricate a record of a
    conversation that did not happen. Reporting it lets the panel say "this
    message was interrupted" and let the user decide whether to resend.
    """
    pending = chat_service.read_pending_turn(ctx)
    if pending is None:
        return None
    owner = chat_service.get_session(str(pending.get("session_id") or ""))
    if owner is not None:
        with owner._lock:
            if owner._turn_in_flight:
                return None
    chat_service.clear_pending_turn(ctx)
    return pending


@router.get("/history")
def chat_history(limit: int = 200) -> dict[str, Any]:
    """
    Replay the active project's chat history (the on-disk `chat.jsonl` and
    its rotation backup) so a frontend reload can hydrate the message list
    AND reuse the most recent `session_id`.

    Reuse of the prior session_id is the difference between paying the
    Anthropic prompt-cache write premium on every reload (one fresh session
    per refresh — cache miss) versus benefiting from the cache on
    subsequent turns. Sessions are server-side in-memory only, so we recreate
    the matching ChatSession entry here on demand.

    Returns:
      * `turns`: list of turn records, oldest first.
      * `last_session_id`: id of the most recent turn (or None if empty).
      * `bound_project`: the project name we read from, or None when unbound.
      * `history_gap`: how many on-disk records were unreadable (QA #10). Zero
        on a healthy transcript. Non-zero means the list above is INCOMPLETE,
        which the panel must say rather than render a quietly shorter
        conversation.
      * `pending_turn`: a turn that started and never finished — recovered
        from the WAL (#20), reported ONCE, then cleared. None normally.
    """
    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    # Shared two-source (rotation + current) read — also used by the #9 daily
    # cap and the #27 export route. read_all_turns returns ONLY parsed turns
    # (no session rebuild side-effect); the rehydration below stays local.
    turns, history_gap = chat_service.read_all_turns_with_gap(ctx)
    pending_turn = _recover_pending_turn(ctx)
    if not turns:
        return {"turns": [], "last_session_id": None,
                "bound_project": ctx.loaded_project,
                "history_gap": history_gap,
                "pending_turn": pending_turn}
    if limit > 0:
        turns = turns[-limit:]

    # Recreate or reattach the session record. The Phase 2 SESSIONS registry
    # is process-lifetime; after a backend restart the session is gone in
    # memory but the chat.jsonl is the durable record.
    last_session_id = None
    if turns:
        last_session_id = turns[-1].get("session_id")
        if last_session_id:
            sess = chat_service.get_or_create_session(
                last_session_id,
                model=turns[-1].get("model") or chat_service.DEFAULT_MODEL,
            )
            # Rebuild the in-memory message history so the next turn can
            # thread the prior conversation into the Anthropic SDK (INT-001
            # threading fix). Best-effort: skip turns missing required keys.
            with sess._lock:
                sess.messages.clear()
                for rec in turns:
                    user_text = rec.get("user")
                    assistant_blocks = rec.get("assistant")
                    if user_text is not None:
                        sess.append_history_message(
                            {"role": "user", "content": user_text}
                        )
                    if isinstance(assistant_blocks, list):
                        sess.append_history_message(
                            {"role": "assistant", "content": assistant_blocks}
                        )

    return {
        "turns": turns,
        "last_session_id": last_session_id,
        "bound_project": ctx.loaded_project,
        "history_gap": history_gap,
        "pending_turn": pending_turn,
    }


@router.get("/metrics")
def chat_metrics() -> dict[str, Any]:
    """
    Observability snapshot for the chat subsystem (#20): turn count, retry
    count, per-error_kind counts (turn-terminal only), p50/p95 turn latency in
    ms, and process-lifetime cumulative input/output tokens. Cheap — reads a
    bounded in-memory deque under a lock; no disk, no SDK. Counters are
    process-lifetime (they reset on backend restart, NOT per request).
    """
    return chat_service._metrics_snapshot()


@router.get("/export")
def chat_export() -> dict[str, Any]:
    """
    Export the active project's chat transcript as a portable envelope (#27).

    Reuses the same two-source (rotation + current) read as GET /history via
    `read_all_turns`. Records are returned as-stored — already redacted on
    write (#14), so no re-redaction here (the patterns are idempotent anyway).
    Unbound context → an empty `turns` list with `project: null`.
    """
    from services.pypsa_service import PyPSAService
    import time as _time
    ctx = PyPSAService.get_active_context()
    turns = chat_service.read_all_turns(ctx)
    return {
        "schema": "pypsa-gui-chat-export/1",
        "exported_at": _time.time(),
        "project": ctx.loaded_project,
        "turns": turns,
    }


@router.post("/import")
def chat_import(body: ImportRequest) -> dict[str, Any]:
    """
    Import a chat transcript into the active project's chat.jsonl (#27).

    Whole-batch validate-then-apply (matches the CLAUDE.md bulk-validate-up-
    front doctrine): every turn must be a dict carrying the required keys
    (ts / session_id / model / user / assistant). The FIRST malformed turn
    rejects the ENTIRE batch with 422 `error_kind='invalid_transcript'` — no
    partial application. Imported turns are passed through `_redact_for_persist`
    (safe-by-default: a leaky/malicious transcript can't seed secrets into
    chat.jsonl that would then propagate into snapshot/copy bundles).

    Unbound context (no persist path) → `{imported: 0}` (append_turn is a
    silent no-op when unbound).
    """
    from services.pypsa_service import PyPSAService
    required = ("ts", "session_id", "model", "user", "assistant")
    for i, turn in enumerate(body.turns):
        if not isinstance(turn, dict) or any(k not in turn for k in required):
            raise HTTPException(
                status_code=422,
                detail={
                    "error_kind": "invalid_transcript",
                    "message": (
                        f"turn at index {i} is not a valid transcript record "
                        f"(must be an object with keys {', '.join(required)})"
                    ),
                },
            )
    ctx = PyPSAService.get_active_context()
    # Unbound context → append_turn is a silent no-op; report 0 so the count
    # reflects what actually landed on disk (rather than claiming N written).
    if chat_service.get_persist_path(ctx) is None:
        return {"imported": 0, "project": ctx.loaded_project}
    imported = 0
    for turn in body.turns:
        redacted = chat_service._redact_for_persist(turn)
        chat_service.append_turn(ctx, redacted)
        imported += 1
    return {"imported": imported, "project": ctx.loaded_project}


# How often the disconnect watcher asks whether the client is still there.
# The poll is cheap (a non-blocking drain of the receive channel), so the
# interval is set by how long we are willing to keep paying for a turn nobody
# will read, not by cost. Module-level so a test can shorten it.
DISCONNECT_POLL_SECONDS = 2.0


class _DisconnectWatcher:
    """
    Abort a turn whose client has gone away (QA #14).

    Until now the only early exit was an explicit POST to `/{id}/abort`,
    which the panel sends when it closes cleanly. A tab that is killed, a
    laptop that sleeps, a dropped connection and a quit app all send nothing,
    and the turn ran to completion — more model tokens, and every remaining
    tool in the agent's plan actually executed against a network nobody was
    watching.

    Lives here rather than inside `_gen()` because `_gen()` is a SYNC
    generator handed to `StreamingResponse` and run in a worker thread: there
    is no event loop in there to await anything on. The async handler has
    both `request` and the running loop, so the watcher is a task on that
    loop, and the only thing it ever touches is `session.abort_event` — the
    same thread-safe primitive `/abort` sets. `_gen()` is unchanged.

    Reliability note, because the obvious reading says this cannot work:
    Starlette runs its own `listen_for_disconnect` alongside the response
    body whenever the server advertises ASGI spec_version < 2.4 (uvicorn's
    HTTP protocols say 2.3), and that listener is parked in `await receive()`
    so it wins every delivery race against our opportunistic poll. It does
    not matter: uvicorn's `receive()` is LEVEL-triggered — once the
    connection is gone it returns `http.disconnect` to every later call
    rather than once. Both observers therefore see it. A server with an
    edge-triggered `receive()` would starve this watcher, so it is written to
    be harmless when it never fires: the pre-existing `/abort` path and
    `_gen()`'s own teardown remain the guaranteed stops, and this is the one
    that makes them prompt.
    """

    def __init__(
        self,
        request: Request,
        session: Any,
        *,
        finished: threading.Event | None = None,
        poll_seconds: float | None = None,
    ) -> None:
        self._request = request
        self._session = session
        # Set by `_gen()`'s finally BEFORE it disarms, so a watcher that
        # wakes in that window can tell "the client vanished mid-turn" from
        # "the response is simply over".
        self.finished = finished if finished is not None else threading.Event()
        self._poll = (
            poll_seconds if poll_seconds is not None else DISCONNECT_POLL_SECONDS
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while not self.finished.is_set():
            await asyncio.sleep(self._poll)
            if self.finished.is_set():
                return
            try:
                gone = await self._request.is_disconnected()
            except Exception:  # noqa: BLE001 — never break the stream over a probe
                return
            if not gone:
                continue
            # Re-check under the same condition that gated the sleep. Without
            # it, a watcher that wakes just after the response completed sees
            # a (correctly) disconnected client and flips an event that is
            # SESSION-scoped — aborting whatever turn that session runs next.
            if not self.finished.is_set():
                self._session.abort_event.set()
            return

    def arm(self) -> None:
        """Start watching. Must be called from the event loop's own task."""
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run())

    def disarm(self) -> None:
        """
        Stop watching. Called from `_gen()`'s finally — i.e. from a WORKER
        THREAD — so the cancel has to be marshalled onto the loop. Never
        raises: it runs in a finally, where an exception would replace
        whatever real failure is already in flight.
        """
        self.finished.set()
        task, loop = self._task, self._loop
        if task is None or loop is None:
            return
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            # Loop already closed — the task died with it.
            pass


@router.post("/stream")
async def chat_stream(
    body: StreamRequest,
    request: Request,
    acting_session: SessionRow | None = Depends(current_session),
) -> StreamingResponse:
    """
    Open an SSE stream for one chat turn. The Phase 2 stub drives the
    script-provided frame sequence; Phase 3 will replace the inner loop
    with an Anthropic Messages-API call but keep this entry shape stable.

    Abort handling (M8): the SSE generator (and the agent_loop_stub it
    drives) check `session.abort_event` between steps. Clients abort a
    stream by POSTing to `/api/chat/{session_id}/abort` (set by the
    ChatPanel on close or by an explicit Cancel button).

    QA #14: a client that never gets to send that POST — a killed tab, a
    sleeping laptop, a dropped connection — is covered by
    `_DisconnectWatcher`, which polls `request.is_disconnected()` from THIS
    handler's event loop and flips the same `abort_event`. `_gen()` stays a
    plain sync generator running in the standard threadpool; nothing here
    needs `asyncio.run` from a worker thread.
    """
    # Bind the acting identity for this turn's tools (Step 0a). The project
    # tools call `routers.projects` handlers in-process and must authorize
    # against the same user the request did; `chat_tools` reads this contextvar
    # and opens its own short-lived DB session per call, because this request's
    # session is closed the moment the handler returns and the SSE generator
    # outlives it. STEP 0b: this becomes the session row's own identity.
    #
    # THIS HANDLER MUST STAY `async def`. As a sync `def` FastAPI runs it via
    # `run_in_threadpool`, which copies the context per submit, so the bind
    # below lands on a worker-thread copy discarded when the handler returns —
    # and `_gen()` (driven by `iterate_in_threadpool`, which copies the task
    # context afresh for EVERY yielded item) then reads the default `None`, so
    # every project-scoped tool answers 401 `no_acting_user`. Binding from the
    # event-loop task instead mutates the context those per-item copies descend
    # from, so it survives all of them. Corollary: nothing blocking may be added
    # to this handler body — it runs on the event loop now. `_gen()` itself is
    # still sync and still runs off-loop in the threadpool.
    from services import chat_tools as _chat_tools
    _chat_tools.set_acting_user(getattr(getattr(request.state, "auth_user", None), "id", None))
    # STEP 0b: the SESSION too, so a chat-driven save or activate moves the
    # active-project pointer exactly as the UI path does. Bound as an ID only —
    # `_route` re-fetches the row inside its own short-lived DB session, since
    # the row `current_session` returns belongs to the DB session this handler
    # closes on return. `current_session` is a SYNC dependency, so FastAPI has
    # already resolved it in the threadpool: no I/O is added to this body, which
    # the note above requires. None (local mode issues no cookie) is legal and
    # is what the HTTP path passes there too.
    _chat_tools.set_acting_session(getattr(acting_session, "id", None))

    session = chat_service.get_or_create_session(
        body.session_id, model=body.model or chat_service.DEFAULT_MODEL,
    )

    # #26 — in-memory token-bucket rate limit, keyed per session_id (a session
    # is one conversation = one rate-limit subject). Disabled by default
    # (STREAM_RATE_CAPACITY <= 0) so the existing SSE suite never trips. When
    # tripped we 429 HERE, before the SSE opens — distinct from the SDK's
    # rate_limited frame inside the stream. `request` is accepted for FastAPI
    # to inject; we key strictly on session_id (NOT request.client.host, which
    # is the constant 'testclient' under TestClient and the proxy IP behind a
    # reverse proxy — both meaningless as a rate subject).
    rate_key = body.session_id or session.session_id
    allowed, retry_after = chat_service.check_rate_limit(rate_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error_kind": "rate_limited_local",
                "message": (
                    "too many chat requests for this session; retry after "
                    f"{int(retry_after) + 1}s"
                ),
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    # Honour an explicit per-turn model switch on an EXISTING session.
    # get_or_create_session returns a known session as-is, so without this the
    # UI's model picker would only take effect for a brand-new session and be
    # silently ignored on every subsequent turn. Messages are model-agnostic,
    # so switching models across turns within one session is safe.
    if body.model:
        session.model = body.model

    # Phase 3 routing: an EXPLICIT script in the body → Phase 2 stub path
    # (used by SSE protocol tests). Otherwise drive `run_turn` with the real
    # Anthropic SDK. A user-typed message NEVER routes to the stub.
    # Phase 4 QA fix: previously a body.message would mutate script and
    # accidentally trip the stub path, bypassing the LLM entirely.
    has_explicit_script = bool(body.script)

    # QA #14 — armed HERE, on the event loop, because `_gen()` below has no
    # loop to await `request.is_disconnected()` on. Disarmed in `_gen()`'s
    # finally so no polling task outlives its stream.
    watcher = _DisconnectWatcher(request, session)
    watcher.arm()

    def _gen():
        try:
            if has_explicit_script:
                events = chat_service.agent_loop_stub(session, list(body.script))
            else:
                events = chat_service.run_turn(
                    session, body.message or "",
                    attachment_file_ids=body.attachment_file_ids,
                    ui_context=body.ui_context,
                )
            for event_name, payload in events:
                yield chat_service.sse_frame(event_name, payload)
        except Exception as exc:  # noqa: BLE001 — surface as error frame
            yield chat_service.sse_frame(
                "error",
                {"error_kind": "internal_error",
                 "message": chat_service._redact_for_log(exc)},
            )
        finally:
            # Covers every exit: normal completion, the error frame above, and
            # GeneratorExit when the consumer stops pulling.
            watcher.disarm()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        # Disable proxy buffering hints — the chat panel's SSE consumer
        # depends on flushing each event individually.
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{session_id}/confirm")
def chat_confirm(session_id: str, body: ConfirmRequest) -> dict[str, Any]:
    """
    Resolve a pending confirmation token. v4-MINOR-3: the lookup + pop run
    under `ChatSession._lock`, so two concurrent POSTs (two tabs, fast
    double-click) cannot BOTH succeed. One returns 200 with the decision;
    the other returns 404 `error_kind='unknown_confirmation_token'`.

    Expired tokens return 409 `error_kind='confirmation_expired'`. Invalid
    decisions return 400 `error_kind='invalid_decision'` and keep the token
    alive so the caller can retry with the correct value.
    """
    session = chat_service.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_kind": "unknown_session",
                "message": f"no chat session bound to {session_id!r}",
            },
        )
    pc = session.record_decision(body.token, body.decision)
    return {
        "ok": True,
        "token": pc.token,
        "tool_name": pc.tool_name,
        "decision": body.decision,
    }


@router.post("/{session_id}/abort")
def chat_abort(session_id: str) -> dict[str, Any]:
    """
    Set the session's abort_event so its SSE generator + any cooperating
    worker shut down. Idempotent: safe to call on an unknown session
    (returns ok=false rather than 404, so a quick double-click is harmless).
    """
    session = chat_service.get_session(session_id)
    if session is None:
        return {"ok": False, "reason": "unknown_session"}
    session.abort_event.set()
    return {"ok": True}
