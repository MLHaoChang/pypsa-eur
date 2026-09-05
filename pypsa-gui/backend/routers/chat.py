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
import logging
import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import StreamingResponse

from db.models import Session as SessionRow
from db.models import User
from deps import current_session, optional_user
from services import app_secrets, chat_service, llm_config

logger = logging.getLogger("pypsa_gui.chat")

router = APIRouter()


class StreamRequest(BaseModel):
    """
    Body for POST /api/chat/stream.

    `session_id` — caller-provided so /confirm and /abort can resolve the
    same session. Server creates one if omitted.
    `model`     — Legacy Anthropic model identifier. Still honoured (Task 7:
                  translated via `llm_config.resolve_legacy_model`) for
                  callers that predate profiles; `profile_id` below is
                  preferred and wins when both are given.
    `profile_id` — Task 7. The `LLMProfile` id (see `services/llm_config.py`)
                  this turn should use. `None` resolves via the legacy
                  `model` translation (itself falling back to the active
                  profile). Switching to a profile on a DIFFERENT wire than
                  one this session is already bound to is refused with a
                  typed `error` frame — see `chat_service.run_turn`'s
                  `wire_conflict` parameter.
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
    profile_id: str | None = None
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

    NOT in `main._AUTH_PUBLIC_PATHS` — an earlier draft of this task's brief
    asserted this route was already unauthenticated and asked for that to be
    made explicit; that premise was checked against `main.py`'s global auth
    middleware (`undo_snapshot_middleware`, ~line 487) while implementing and
    found FALSE: `/api/chat/health` was not, and is not, in that allow-list,
    so a server deployment 401s an anonymous caller exactly like every other
    `/api/*` route (`test_health_requires_authentication_on_a_server_deployment`).
    Adding the exemption was reverted — it would have let any anonymous
    caller on a multi-tenant server read `chat_ready`, `default_model`, and
    the active profile's free-text `label` (an admin could name a profile
    after internal infrastructure). The desktop build still gets a
    login-free answer for the reason it always did: local mode's own
    middleware branch injects the seeded local user on every request,
    session or not (`test_health_answers_without_a_session_in_local_mode`).

    Payload, for an authenticated caller:
      * `anthropic_api_key_present` / `default_model` — unchanged, byte-for-
        byte the same semantics as before Task 9.
      * `active_profile` — `{id, label, wire}` ONLY. No `base_url`, no
        `key_hint`, no key-env NAME.
      * `chat_ready` — whether the active profile could actually be used
        right now (a bearer profile with no key configured is not), computed
        from `os.environ` membership only — never a network call, so this
        route stays cheap.

    Nothing else. In particular: no `profiles` list (that is
    `GET /chat/profiles`) and no `PYPSA_GUI_LLM_KEY__*` name ever appears in
    the body.
    """
    import os
    api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    active_profile = llm_config.resolve_active()
    if active_profile.auth == "bearer":
        chat_ready = bool(
            active_profile.key_env and os.environ.get(active_profile.key_env)
        )
    else:
        chat_ready = True
    return {
        "ok": True,
        "anthropic_api_key_present": api_key_present,
        "default_model": chat_service.DEFAULT_MODEL,
        "confirmation_ttl_seconds": chat_service.CONFIRMATION_TTL_SECONDS,
        "active_profile": {
            "id": active_profile.id,
            "label": active_profile.label,
            "wire": active_profile.wire,
        },
        "chat_ready": chat_ready,
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


# ── Task 9 — LLM settings + profiles routes, connection test ────────────────
#
# Same gate as the api-key group above (`_require_super_admin`) for every
# `/settings/llm*` route: a profile's `base_url`/`model`/capabilities are
# process-global configuration, not per-organization data, so an ORG admin
# has no more authority over them than over the Anthropic key itself.
#
# `GET /chat/profiles` (bottom of this section) is the one exception — it is
# the read-only, no-secrets menu every chat-using member needs to pick a
# profile for their own turn, so it is gated on "authenticated" only.

_BUILTIN_PROFILE_IDS = (llm_config.BUILTIN_SONNET_ID, llm_config.BUILTIN_OPUS_ID)


class ProfileIn(BaseModel):
    """
    Body for `PUT /settings/llm/profiles/{id}`.

    Deliberately does NOT carry `key_env` (or a key value) — `extra="forbid"`
    makes a client that sends one get a 422 rather than having it silently
    ignored. `key_env` is derived server-side from `id`/`preset`
    (`llm_config.derive_key_env`) precisely so a profile can never be saved
    pointing an attacker-chosen `base_url` at a well-known key slot (e.g.
    naming `ANTHROPIC_API_KEY` while `base_url` points elsewhere) — accepting
    a client-supplied `key_env` would turn this route into an exfiltration
    primitive for whatever secret already lives in that env var.

    No per-field validation beyond typing is duplicated here on purpose:
    `llm_config._validate_profile` (wire/auth enum membership, base_url
    shape, the preset/base_url lock) is the single source of truth for what
    makes a profile valid, and its `ProfileValidationError` is translated to
    422 below — the same "don't split one rule across two layers" doctrine
    `ApiKeyRequest`'s docstring states for `app_secrets.validate_value`.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    preset: str
    wire: str
    base_url: str | None = None
    model: str
    tools: bool
    vision: bool
    auth: str
    fallback_model: str | None = None
    max_output_tokens: int | None = None


class ProfileKeyRequest(BaseModel):
    """Body for `PUT /settings/llm/profiles/{id}/key`."""

    value: str


class ActiveProfileRequest(BaseModel):
    """Body for `POST /settings/llm/active`."""

    profile_id: str


def _profile_out(profile: llm_config.LLMProfile) -> dict[str, Any]:
    """
    `ProfileOut` — every `LLMProfile` field plus key STATUS, never a value.

    `key_required` is `auth == "bearer"` (an `auth == "none"` profile, e.g.
    a bare local endpoint, has no key concept at all — `key_present`/
    `key_hint` are forced False/None rather than probing a `key_env` that
    doesn't exist for it). For a bearer profile, status is read through
    `app_secrets.status`, the SAME accessor `/settings/api-key` uses — so
    the hint format (`"…" + last 4 chars`) and the "never the live value"
    guarantee are identical across both surfaces, not a second
    reimplementation that could drift.
    """
    key_env = profile.key_env
    if key_env is not None:
        status = app_secrets.status(key_env)
        key_present = bool(status["configured"])
        key_hint = status["hint"]
    else:
        key_present = False
        key_hint = None
    return {
        "id": profile.id,
        "label": profile.label,
        "preset": profile.preset,
        "wire": profile.wire,
        "base_url": profile.base_url,
        "model": profile.model,
        "tools": profile.tools,
        "vision": profile.vision,
        "auth": profile.auth,
        "fallback_model": profile.fallback_model,
        "max_output_tokens": profile.max_output_tokens,
        "key_required": profile.auth == "bearer",
        "key_present": key_present,
        "key_hint": key_hint,
    }


def _file_profiles_excluding(
    profiles: list[llm_config.LLMProfile], profile_id: str
) -> list[llm_config.LLMProfile]:
    """Every FILE (non-built-in) profile except `profile_id` — the base a save/delete edits onto."""
    return [
        p for p in profiles
        if p.id not in _BUILTIN_PROFILE_IDS and p.id != profile_id
    ]


def _get_llm_profile_or_404(profile_id: str) -> llm_config.LLMProfile:
    profiles, _active_id = llm_config.load_profiles()
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    raise HTTPException(
        status_code=404, detail=f"no LLM profile named {profile_id!r}"
    )


@router.get("/settings/llm")
def get_llm_settings(
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """Every profile (built-in + saved), which one is active, and the preset catalogue."""
    _require_super_admin(user)
    profiles, active_id = llm_config.load_profiles()
    return {
        "profiles": [_profile_out(p) for p in profiles],
        "active_profile_id": active_id,
        "presets": llm_config.load_presets(),
    }


@router.put("/settings/llm/profiles/{profile_id}")
def put_llm_profile(
    profile_id: str,
    body: ProfileIn,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """
    Create or replace a custom profile. A built-in id (`anthropic-sonnet`,
    `anthropic-opus`) is refused with 409 — those are synthesized in code
    (`llm_config._builtin_profiles`) precisely so they can't be repointed by
    editing the file this route writes to; letting this route "edit" one
    in-place would just silently create a same-id shadow that `load_profiles`
    already documents as impossible to distinguish from tampering.
    """
    _require_super_admin(user)
    if profile_id in _BUILTIN_PROFILE_IDS:
        raise HTTPException(
            status_code=409,
            detail=f"{profile_id!r} is a built-in profile and cannot be edited",
        )
    profile = llm_config.LLMProfile(
        id=profile_id,
        label=body.label,
        preset=body.preset,
        wire=body.wire,
        base_url=body.base_url,
        model=body.model,
        tools=body.tools,
        vision=body.vision,
        auth=body.auth,
        fallback_model=body.fallback_model,
        max_output_tokens=body.max_output_tokens,
    )
    profiles, active_id = llm_config.load_profiles()
    file_profiles = _file_profiles_excluding(profiles, profile_id)
    file_profiles.append(profile)
    try:
        llm_config.save_profiles(file_profiles, active_id)
    except llm_config.ProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_out(profile)


@router.delete("/settings/llm/profiles/{profile_id}")
def delete_llm_profile(
    profile_id: str,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """
    Delete a custom profile and clear its key slot.

    Refused (409) for a built-in id — same reasoning as the PUT route above.
    404s for an id that names nothing. If the deleted profile was active,
    active resets to `anthropic-sonnet` (the one profile guaranteed to
    always exist) rather than leaving `active_profile_id` dangling.

    Key-slot cleanup is scoped to a NAMESPACED slot
    (`PYPSA_GUI_LLM_KEY__<SLUG>`) only — never a shared provider key
    (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …). A custom profile using a
    cataloged bearer preset shares that preset's key with every OTHER
    profile on the same preset (including the built-ins, for
    `ANTHROPIC_API_KEY`); wiping it here on one profile's deletion would
    silently break every other profile still relying on it. Only
    `preset="custom"` (or an uncatalogued preset id) gets a private,
    per-profile slot — see `llm_config.derive_key_env` — and that is exactly
    the case this clears.
    """
    _require_super_admin(user)
    if profile_id in _BUILTIN_PROFILE_IDS:
        raise HTTPException(
            status_code=409,
            detail=f"{profile_id!r} is a built-in profile and cannot be deleted",
        )
    profiles, active_id = llm_config.load_profiles()
    target = next((p for p in profiles if p.id == profile_id), None)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"no LLM profile named {profile_id!r}"
        )
    file_profiles = _file_profiles_excluding(profiles, profile_id)
    new_active = (
        active_id if active_id != profile_id else llm_config.BUILTIN_SONNET_ID
    )
    llm_config.save_profiles(file_profiles, new_active)
    if target.key_env is not None and target.key_env.startswith("PYPSA_GUI_LLM_KEY__"):
        app_secrets.clear_secret(target.key_env)
    return {"ok": True, "active_profile_id": new_active}


@router.put("/settings/llm/profiles/{profile_id}/key")
def put_llm_profile_key(
    profile_id: str,
    body: ProfileKeyRequest,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """Store a profile's key and apply it to this process immediately (see `app_secrets.set_secret`)."""
    _require_super_admin(user)
    profile = _get_llm_profile_or_404(profile_id)
    if profile.key_env is None:
        raise HTTPException(
            status_code=409,
            detail=f"profile {profile_id!r} has auth=none and takes no key",
        )
    try:
        status = app_secrets.set_secret(profile.key_env, body.value)
    except app_secrets.SecretValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"key_present": bool(status["configured"]), "key_hint": status["hint"]}


@router.delete("/settings/llm/profiles/{profile_id}/key")
def delete_llm_profile_key(
    profile_id: str,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """Forget a profile's key — from the file and from this process."""
    _require_super_admin(user)
    profile = _get_llm_profile_or_404(profile_id)
    if profile.key_env is None:
        raise HTTPException(
            status_code=409,
            detail=f"profile {profile_id!r} has auth=none and takes no key",
        )
    status = app_secrets.clear_secret(profile.key_env)
    return {"key_present": bool(status["configured"]), "key_hint": status["hint"]}


@router.post("/settings/llm/active")
def post_llm_active(
    body: ActiveProfileRequest,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """Switch the active profile. 404s if `profile_id` names nothing (built-in or file)."""
    _require_super_admin(user)
    try:
        llm_config.set_active(body.profile_id)
    except llm_config.ProfileValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"active_profile_id": body.profile_id}


# Provider errors from `_provider_for_profile` that mean "there is no live
# connection to test at all" (missing/unusable configuration) map to
# `invalid_request` — the fixed vocabulary has no slot for "not configured"
# distinct from "the configuration we have is wrong", and conflating the two
# is honest: both mean the operator has something to fix before this profile
# will work. `unauthorized` is the one case worth keeping distinct, since it
# specifically means "a key IS present but was rejected".
_NO_PROVIDER_VERDICT: dict[str, str] = {"unauthorized": "unauthorized"}


def _connection_test_result(profile: llm_config.LLMProfile) -> dict[str, Any]:
    """
    Drive `chat_service._provider_for_profile` with a real `max_tokens=1`
    completion and report a FIXED verdict — never upstream exception text,
    never the full `base_url` (host:port at most, and only ever inside a
    server-side log, never in the response). `test_llm_settings_api.py`
    monkeypatches `chat_service._provider_for_profile` itself, so calling it
    by that name (not some local alias) is what makes that seam work.
    """
    provider, err = chat_service._provider_for_profile(profile)
    if provider is None:
        return {
            "verdict": _NO_PROVIDER_VERDICT.get(err or "", "invalid_request"),
            "latency_ms": None,
            "models": None,
        }
    try:
        verdict, latency_ms = provider.probe(profile.model)
    except Exception as exc:  # noqa: BLE001 — a probe must never 500 this route
        logger.warning(
            "chat: connection test probe raised unexpectedly (%s)",
            type(exc).__name__,
        )
        verdict, latency_ms = "invalid_request", None
    try:
        models = provider.probe_models()
    except Exception:  # noqa: BLE001 — best-effort only, per the contract
        models = None
    return {"verdict": verdict, "latency_ms": latency_ms, "models": models}


@router.post("/settings/llm/profiles/{profile_id}/test")
def post_llm_profile_test(
    profile_id: str,
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """
    Connection test: a real `max_tokens=1` completion through the profile's
    provider. Verdict is one of `ok|unreachable|unauthorized|
    model_not_found|invalid_request` — fixed strings, never upstream text.
    """
    _require_super_admin(user)
    profile = _get_llm_profile_or_404(profile_id)
    return _connection_test_result(profile)


@router.get("/profiles")
def get_chat_profiles(
    user: User | None = Depends(optional_user),
) -> dict[str, Any]:
    """
    The profile MENU every chat-using member can choose from — id/label/wire
    only, never `base_url`, never key status. Gated on "authenticated", not
    super-admin: this is what `StreamRequest.profile_id` (routers/chat.py's
    own `/stream`) expects a caller to pick from, and every org member sends
    chat turns.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    profiles, active_id = llm_config.load_profiles()
    return {
        "profiles": [
            {"id": p.id, "label": p.label, "wire": p.wire} for p in profiles
        ],
        "active_profile_id": active_id,
    }


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
        last_rec = turns[-1]
        last_session_id = last_rec.get("session_id")
        if last_session_id:
            # Task 7 — resolve the profile the LATEST turn was recorded
            # under (its `profile_id` when present, else the legacy `model`
            # translation). This is the profile a FRESHLY-MINTED session
            # adopts, matching the pre-existing single `model=` rehydration
            # this replaces.
            recorded_profile_id = last_rec.get("profile_id")
            if recorded_profile_id:
                try:
                    resolved_profile = llm_config.resolve_profile(
                        recorded_profile_id
                    )
                except llm_config.ProfileNotConfiguredError:
                    # C-4 — the profile that turn ran under has since been
                    # deleted. This is a READ of past turns, not a turn, so
                    # there is nothing to refuse: fall back to exactly what a
                    # record with no `profile_id` gets. The next `/stream`
                    # re-binds and, if the client still names the dead id,
                    # refuses it there — where a prompt would actually be sent.
                    logger.info(
                        "chat history: recorded profile is no longer "
                        "configured; falling back to the legacy translation"
                    )
                    resolved_profile = llm_config.resolve_legacy_model(
                        last_rec.get("model") or chat_service.DEFAULT_MODEL
                    )
            else:
                resolved_profile = llm_config.resolve_legacy_model(
                    last_rec.get("model") or chat_service.DEFAULT_MODEL
                )
            # Fix round 2 — existence-check and creation happen under ONE
            # `_SESSIONS_LOCK` acquisition (`get_or_create_session_reporting`),
            # not two. The round-1 fix's separate `get_session(...) is not
            # None` probe followed by a separate `get_or_create_session`
            # call left a microsecond gap where a concurrent `/stream` could
            # register-and-bind the session in between; `created` reported
            # here is never stale because it's observed atomically with the
            # registration itself.
            sess, session_was_freshly_minted = (
                chat_service.get_or_create_session_reporting(
                    last_session_id, model=resolved_profile.model,
                )
            )
            # Fix round 1 — GET /history must stay read-only w.r.t. an
            # ALREADY-LIVE session's profile binding, exactly like
            # `get_or_create_session`'s own `model=` kwarg is already a
            # documented no-op for an existing session (chat_service.py).
            # These three lines used to run unconditionally: a GET /history
            # racing a same-wire rebind mid-turn (two tabs sharing a
            # session_id, or a reload — the #19 in-flight guard's own
            # docstring anticipates exactly this) would resolve the STALE,
            # not-yet-persisted profile from chat.jsonl and revert
            # `session.model`/`profile_id`/`bound_wire` out from under the
            # running turn's next outer-loop pass. Only a session that was
            # NOT already registered (this GET minted it) adopts the
            # resolved profile; an already-live session is left exactly as
            # `/stream` bound it.
            if session_was_freshly_minted:
                sess.profile_id = resolved_profile.id
                sess.bound_wire = resolved_profile.wire
                sess.model = resolved_profile.model
            # Rebuild the in-memory message history so the next turn can
            # thread the prior conversation into the Anthropic SDK (INT-001
            # threading fix). Best-effort: skip turns missing required keys.
            # Task 7 — a transcript can carry turns recorded on a DIFFERENT
            # wire than the one this session now resolves to (the user
            # started a fresh chat on a new profile after this project's
            # last turn); blocks that wire can't replay (thinking /
            # redacted_thinking / image / document) are dropped here rather
            # than sent and rejected.
            with sess._lock:
                sess.messages.clear()
                for rec in turns:
                    user_text = rec.get("user")
                    assistant_blocks = rec.get("assistant")
                    if user_text is not None:
                        sess.append_history_message({
                            "role": "user",
                            "content": chat_service._filter_non_portable_blocks(
                                user_text, resolved_profile.wire,
                            ),
                        })
                    if isinstance(assistant_blocks, list):
                        sess.append_history_message({
                            "role": "assistant",
                            "content": chat_service._filter_non_portable_blocks(
                                assistant_blocks, resolved_profile.wire,
                            ),
                        })

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

    # Task 7 — resolve the profile this turn names, and bind/rebind/refuse
    # BEFORE branching into stub vs run_turn, so a script-driven protocol
    # test and a real turn go through the identical binding logic.
    # `profile_id` wins when given; otherwise the legacy `model` string is
    # translated the same way a pre-profile session's stored model always
    # was — `resolve_legacy_model` itself falls back to the active profile
    # on `None`/an unrecognized string, so an old client that sends neither
    # field keeps getting today's zero-config behaviour.
    # C-4 — an explicit id that names nothing is REFUSED, not quietly served
    # by the active profile. `run_turn` turns this into a typed error frame
    # (never an HTTPException: a non-2xx SSE body is discarded client-side,
    # so the frame is the only way the copy reaches the panel), and the
    # session is left completely unbound/unrebound.
    unknown_profile_id: str | None = None
    target_profile = None
    if body.profile_id:
        try:
            target_profile = llm_config.resolve_profile(body.profile_id)
        except llm_config.ProfileNotConfiguredError:
            unknown_profile_id = body.profile_id
    else:
        target_profile = llm_config.resolve_legacy_model(body.model)

    # C-8 / C-9 — a BOUND session whose caller named neither `profile_id` nor
    # `model` keeps the binding it already has.
    #
    # This branch used to fall through to the rebind below, which re-resolved
    # the INSTANCE-WIDE active profile and reassigned
    # `profile_id`/`bound_wire`/`model` on every single turn. Two documented
    # promises were broken by that one line:
    #
    #   * the A8 fallback lasted exactly one turn. The user was told "falling
    #     back to Sonnet" after Opus rate-limited, and the very next turn put
    #     them back on the rate-limited model (C-8). `master` avoided this by
    #     assigning `session.model` only `if body.model:`.
    #   * an admin's `set_active_profile` silently moved every chat already in
    #     flight (C-9) — the exact opposite of that tool's own promise, "This
    #     chat stays on the model it started with", and of the contract in
    #     `frontend/src/api/chat.ts` that omitting `profile_id` must not
    #     "re-assert a stale choice every turn".
    #
    # Naming either field is still an explicit choice and still rebinds; an
    # UNBOUND session still adopts the active profile, which is the
    # zero-config path.
    caller_named_a_target = bool(body.profile_id) or bool(body.model)
    wire_conflict = False
    if unknown_profile_id is not None:
        # Nothing to bind — the refusal is emitted by `run_turn` below and the
        # session keeps whatever binding it already had.
        pass
    elif session.profile_id is not None and not caller_named_a_target:
        pass
    elif session.profile_id is None or session.bound_wire == target_profile.wire:
        # Unbound session (first turn) or a same-wire rebind — both allowed.
        # Messages are model-agnostic WITHIN a wire, so switching models
        # across turns on the same wire (this used to be the `if body.model:`
        # line below, now subsumed) stays safe; this also updates `model` on
        # a same-wire rebind, which the old line only did for the legacy
        # `model` field.
        session.profile_id = target_profile.id
        session.bound_wire = target_profile.wire
        session.model = target_profile.model
    else:
        # Cross-wire switch on an already-bound session: prior turns may
        # carry blocks (thinking, tool-call shape) that don't replay on the
        # new wire. Refused — the session stays bound to its ORIGINAL
        # profile untouched; `wire_conflict` short-circuits `run_turn` below
        # into a typed `error` + `session_done`, never an HTTPException (a
        # non-2xx SSE body is discarded client-side).
        wire_conflict = True

    # F2 — bind the turn's profile for the tool layer FROM THE EVENT-LOOP
    # TASK, for the identical reason `set_acting_user` is bound up there
    # rather than inside `_gen()`: `iterate_in_threadpool` copies the task
    # context afresh for every yielded item, so a `set()` performed inside
    # `_run_turn_body` lands on a throwaway per-item copy and every later
    # tool dispatch reads the default `None`.
    #
    # `chat_service` sets it too, which is what makes a directly-driven
    # `run_turn` (every existing e2e test) work; that call is harmless here
    # and useless on its own. This is the one that reaches production.
    #
    # Resolving can raise if the session is bound to a profile that has since
    # been deleted (F4) — that is refused as `unknown_profile_id`, the same
    # typed frame an unknown `body.profile_id` gets, never a 500.
    if unknown_profile_id is None:
        try:
            _chat_tools.set_turn_profile(
                chat_service._resolve_turn_profile(session)
            )
        except llm_config.ProfileNotConfiguredError:
            unknown_profile_id = session.profile_id or "?"
            _chat_tools.set_turn_profile(None)
    else:
        _chat_tools.set_turn_profile(None)

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
            if unknown_profile_id is not None:
                # Never the stub, even with a script — the profile is refused
                # before either path would run.
                events = chat_service.run_turn(
                    session, body.message or "",
                    unknown_profile_id=unknown_profile_id,
                )
            elif wire_conflict:
                # Never the stub, even if the caller also sent a script — a
                # wire switch is refused before either path would run.
                events = chat_service.run_turn(
                    session, body.message or "", wire_conflict=True,
                )
            elif has_explicit_script:
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


class RewindRequest(BaseModel):
    """How many complete turns to drop from the session's API history."""

    turns: int = 1


@router.post("/{session_id}/rewind")
def chat_rewind(session_id: str, body: RewindRequest) -> dict[str, Any]:
    """
    Drop the last N turns so a retry / edit-and-resend is a real retry.

    Without this the client can only clear the screen, while the array
    replayed to the model still holds the answer being retried — so the model
    reads its own last answer and repeats it, and retry looks broken.

    Idempotent-ish and never 404s, matching /abort: an unknown session means
    there is nothing to rewind, which is the caller's desired end state
    anyway. `dropped: 0` with `ok: true` is also what a caller gets while a
    turn is in flight — see rewind_session for why refusing is the only safe
    answer there.
    """
    session = chat_service.get_session(session_id)
    if session is None:
        return {"ok": False, "reason": "unknown_session", "dropped": 0}
    dropped = chat_service.rewind_session(session, turns=body.turns)
    return {"ok": True, "dropped": dropped}


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
