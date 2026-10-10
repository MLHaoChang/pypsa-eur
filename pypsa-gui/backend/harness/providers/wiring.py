"""
Provider wiring: which profile a turn runs as, the provider built for it,
the tools payload offered, and the history-portability filter between wires.
This is the one place above the adapters where a wire is named on purpose.

Moved from harness/loop.py (the former services/chat_service.py), chat
harness issue 08, by AST selection of whole top-level nodes; the loop
re-imports every function and class, so `chat_service.<name>` is the same
object, and forwards every tunable (PEP 562). A tunable or a patched
function that lives here is patched on THIS module (harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlsplit

from harness.providers import anthropic as llm_anthropic
from harness.providers import openai_compat as llm_openai_compat
from harness.providers.anthropic import (  # moved 2026-08-13 (provider seam)
    # `_build_anthropic_client` is NOT test-only: it has a production caller
    # (chat_tools.reconstruct_network_from_image's vision sub-call) and
    # app_secrets.py documents it as the call-time surface that picks up a
    # freshly-saved API key without a restart. This alias — and the compat
    # surface below — is a caller/patch indirection, not dead re-export.
    build_client as _build_anthropic_client,
    # Task 5: no longer called from this module (translation now lives in
    # AnthropicProvider.stream) — these three aliases are kept as a
    # backward-compat re-export surface for test_chat_thinking_blocks.py
    # (calls `_map_sdk_exception` directly) and
    # test_chat_service_seam_aliases_point_at_llm_anthropic.
    map_sdk_exception as _map_sdk_exception,  # noqa: F401
    serialise_block as _serialise_for_anthropic,  # noqa: F401
    with_history_cache_breakpoint as _with_history_cache_breakpoint,  # noqa: F401
)
from typing import Any
import os
from harness.catalogue import TOOLS
from harness.history import logger
from services.redaction import redact_for_log as _redact_for_log
from harness.session import ChatSession


def llm_config_module():
    """The `llm_config` module, imported lazily like every other use here."""
    from services import llm_config  # noqa: PLC0415
    return llm_config


def _resolve_turn_profile(session: ChatSession) -> Any:
    """
    The `LLMProfile` this turn should use for provider construction, the
    per-turn token cap, and the A8 fallback (Task 7).

    `session.profile_id` wins when the router has bound one. Otherwise falls
    back to `llm_config.resolve_legacy_model(session.model)` — the SAME
    translation `resolve_legacy_model` documents for a pre-profile session,
    so a `ChatSession` built directly (every existing e2e test does this —
    `ChatSession()` / `ChatSession(model=OPUS_MODEL)` — with no profile_id)
    keeps resolving exactly the profile its `model` string always implied:
    `DEFAULT_MODEL` -> the built-in sonnet profile, `OPUS_MODEL` -> the
    built-in opus profile (fallback_model=DEFAULT_MODEL — this is what keeps
    the pre-Task-7 A8 test, which sets `model=OPUS_MODEL` and never touches
    `profile_id`, passing unmodified). Deliberately NOT
    `llm_config.resolve_profile(None)` (-> the ACTIVE profile) — that would
    let a user's active-profile choice silently override what an unbound
    session's own `model` field says, which is a behaviour change zero-config
    must not have.
    """
    from services import llm_config
    if session.profile_id is not None:
        return llm_config.resolve_profile(session.profile_id)
    return llm_config.resolve_legacy_model(session.model)


# Block types that only make sense on the wire that produced them: Anthropic
# extended-thinking's signed `thinking`/`redacted_thinking` blocks, and
# Anthropic's own `image`/`document` content-block shapes. None of the four
# has an openai-wire equivalent the translation layer can replay.
_NON_PORTABLE_BLOCK_TYPES: frozenset[str] = frozenset(
    ["thinking", "redacted_thinking", "image", "document"]
)


def _filter_non_portable_blocks(content: Any, wire: str) -> Any:
    """
    Drop content blocks `wire` cannot replay (Task 7 history rehydration).

    A chat.jsonl transcript can carry turns recorded under a DIFFERENT
    profile than the one GET /history resolves the minted session to (the
    user switched wires by starting a new chat — Task 7's cross-wire guard
    is what makes that the only way). Replaying an anthropic-shaped thinking
    block into an openai-wire session's history is not merely wasted
    context; the openai-compat translation has no shape for it at all.

    Only `wire == "openai"` filters anything, and only when `content` is a
    list of blocks — a plain string (an ordinary text-only turn, the common
    case) or an anthropic-wire replay passes through unchanged, same object.
    """
    if wire != "openai" or not isinstance(content, list):
        return content
    return [
        b for b in content
        if not (isinstance(b, dict) and b.get("type") in _NON_PORTABLE_BLOCK_TYPES)
    ]


def _anthropic_client_for_profile(profile: Any | None) -> tuple[Any, str | None]:
    """
    `(anthropic SDK client, error_kind|None)` for an anthropic-wire profile.

    Extracted from `_provider_for_profile` (C-3) so the vision sub-call in
    `chat_tools.reconstruct_network_from_image` resolves its credentials the
    SAME way a turn does, instead of always reaching for the ambient
    `ANTHROPIC_API_KEY`. One source of truth, so the two cannot drift.

    `profile is None` means "no profile bound" — a direct call outside a turn —
    and takes the plain `_build_anthropic_client()` path, i.e. exactly the
    pre-profile behaviour.

      * the built-in `ANTHROPIC_API_KEY` slot -> the EXISTING
        `_build_anthropic_client()` call, reached through the module attribute
        so a test that monkeypatches `chat_service._build_anthropic_client`
        still sees its double. Byte-identical zero-config behaviour, including
        `missing_api_key` and `sdk_not_installed`.
      * any OTHER key slot -> `anthropic.Anthropic(api_key=<slot value>)`.
        The ONE sanctioned explicit `api_key=` kwarg in this codebase:
        `llm_anthropic.build_client` never passes the key explicitly (so a
        literal value cannot land in a repr or a log) because the SDK reads the
        one blessed env var itself; a custom slot has no SDK-known name, so
        passing it explicitly is the only way to honour it.
      * `auth == "bearer"` with an empty/unset slot -> `(None, "missing_api_key")`.
    """
    if profile is None or profile.key_env == "ANTHROPIC_API_KEY":
        return _build_anthropic_client()
    key_value = os.environ.get(profile.key_env) if profile.key_env else None
    if profile.auth == "bearer" and not key_value:
        return None, "missing_api_key"
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        return None, "sdk_not_installed"
    try:
        # Sanctioned explicit api_key= kwarg — see docstring above.
        built = anthropic.Anthropic(api_key=key_value)
    except Exception as exc:  # noqa: BLE001 — surface as typed error kind
        logger.warning(
            "chat: anthropic client init failed for profile %r: %s",
            profile.id, _redact_for_log(exc),
        )
        return None, "unauthorized"
    return built, None


def _provider_for_profile(
    profile: Any, client: Any | None = None
) -> tuple[Any, str | None]:
    """
    `(provider, error_kind|None)` for an `LLMProfile` (Task 6).

    Generalizes the inline construction `_run_turn_body` used to do
    (`llm_anthropic.AnthropicProvider(client)`) across both wires, keyed off
    the profile rather than a hardcoded Anthropic assumption. Kept in
    `chat_service` — not `llm_anthropic` — so the existing `client=`/
    `provider=` injection seams the whole chat test suite pins stay exactly
    where they are.

    Wiring:
      * anthropic wire + the built-in `ANTHROPIC_API_KEY` slot → the
        EXISTING `_build_anthropic_client()` path, reached through the
        module attribute (so a test that monkeypatches
        `chat_service._build_anthropic_client` sees its double here too) —
        byte-identical zero-config behaviour, including `missing_api_key`
        and `sdk_not_installed`.
      * anthropic wire + any OTHER key slot (a custom profile whose preset
        is not the built-in Anthropic one) → `anthropic.Anthropic(api_key=
        <slot value>)`. This is the ONE sanctioned explicit `api_key=` kwarg
        use in this codebase — `llm_anthropic.build_client` never passes the
        key explicitly (so a literal value can't land in a repr/log) because
        the SDK can read the one blessed `ANTHROPIC_API_KEY` env var on its
        own; a custom slot has no such SDK-known name, so passing it
        explicitly is the only way to honour it.
      * openai wire → `OpenAICompatProvider(<resolved base_url>, api_key=
        <slot value or None>)`. `llm_config` documents `base_url=None` on a
        profile as "use the preset's own endpoint" and "always fine, always
        the normal case" for a catalogued preset — so a `None` base_url is
        RESOLVED here (via `llm_config.load_presets()`), never passed
        through as-is (`OpenAICompatProvider` would crash on
        `None.rstrip("/")`, fix round 1). A `"custom"` preset, or any preset
        id not in the catalogue, has no endpoint to resolve `None` against —
        that is a genuinely unusable profile, so it returns `(None,
        "invalid_request")` rather than crashing or guessing.
      * `auth == "bearer"` with an empty/unset key slot → `(None,
        "missing_api_key")`, on either wire.

    `client`, when given, is an already-built Anthropic SDK client (or test
    double) — the production/test injection seam — and wins over building
    one, on the anthropic wire only.
    """
    if profile.wire == "anthropic":
        if client is not None:
            return llm_anthropic.AnthropicProvider(client), None
        built, err = _anthropic_client_for_profile(profile)
        if built is None:
            return None, err
        return llm_anthropic.AnthropicProvider(built), None

    if profile.wire == "openai":
        base_url = profile.base_url
        if base_url is None:
            # Resolution, not a guard (fix round 1, finding 1): None means
            # "use the preset's declared endpoint" for a catalogued preset,
            # never "pass None through and let the provider crash".
            base_url = None
            if profile.preset != "custom":
                from services import llm_config
                entry = next(
                    (e for e in llm_config.load_presets()
                     if isinstance(e, dict) and e.get("id") == profile.preset),
                    None,
                )
                if entry is not None:
                    base_url = entry.get("base_url")
            if not base_url:
                # "custom" (no catalogue entry to resolve against) or an
                # unrecognised/incomplete preset — genuinely unusable, not
                # something to guess at.
                return None, "invalid_request"
        key_value = (
            os.environ.get(profile.key_env) if profile.key_env else None
        )
        if profile.auth == "bearer" and not key_value:
            return None, "missing_api_key"
        return (
            llm_openai_compat.OpenAICompatProvider(
                base_url, api_key=key_value,
                # C-2 — server-derived from the preset, like `key_env`.
                # Exactly one completion-length parameter goes on the wire;
                # the provider retries once under the other spelling if this
                # endpoint refuses it by name.
                token_param=profile.token_param,
            ),
            None,
        )

    return None, "internal_error"


#: The one gridspine tool a session may use whatever project it is bound to:
#: creating a study is how a user gets a planning → dynamics project at all.
_GRIDSPINE_ALWAYS = frozenset({"gridspine_create_study"})


def _bound_project_kind(turn_ctx) -> str | None:
    """The kind of the project this turn is bound to, or None when unbound or
    unknowable. Never raises — tool selection must not fail a turn."""
    if turn_ctx is None:
        from services.pypsa_service import PyPSAService
        turn_ctx = PyPSAService.get_active_context()
    project_uuid = getattr(turn_ctx, "project_uuid", None)
    if not project_uuid:
        return None
    try:
        import uuid as _uuid

        from db.models import Project
        from db.session import SessionLocal
        from services.gridspine_service import kind_of

        with SessionLocal() as db:
            row = db.get(Project, _uuid.UUID(str(project_uuid)))
            return kind_of(row) if row is not None else None
    except Exception:  # noqa: BLE001 - selection must degrade, not abort the turn
        return None


def _tools_payload(turn_ctx=None) -> list[dict[str, Any]]:
    """The `tools` field of the neutral `LLMRequest`: chat_tools_schema.TOOLS,
    minus the project-scoped gridspine tools unless the bound project is a
    planning → dynamics one.

    The spec's "the agent gets the toolset matching the open study", done as a
    filter over ONE registry rather than a registry per kind: the registry
    invariants (`len(TOOLS) == len(DISPATCHERS)`, every tool routed) keep
    holding, and a study project still sees every ordinary tool — its
    capacity-expansion tools simply find no network to act on, which they
    already report. Only the gridspine tools are gated, because on any other
    project every one of them would 409 before doing anything.
    """
    tools = list(TOOLS)
    if _bound_project_kind(turn_ctx) != "planning_dynamics":
        tools = [
            t for t in tools
            if not t["name"].startswith("gridspine_") or t["name"] in _GRIDSPINE_ALWAYS
        ]
    return tools


_OPENAI_TOOL_LIMIT = 128
from harness.toolsets import CONTROL_TOOLS as _HARNESS_TOOL_NAMES, filter_tools
_SELECTION_STOP_WORDS = frozenset({"the", "and", "for", "with", "this", "that", "from", "then", "once", "using", "call", "please", "tool", "tools"})


@lru_cache(maxsize=512)
def _tool_vocabulary(name: str, description: str):
    return (frozenset(name.split("_")),
            frozenset(re.findall(r"[a-z0-9]+", description.lower())))


def _bounded_tools(tools: list[dict[str, Any]], query: str = "", history=None) -> list[dict[str, Any]]:
    """Rank a large catalogue while keeping original order for prefix caching.

    Explicit tool names always outrank generic description matches. Recent
    calls keep referential follow-ups useful; harness controls remain offered.
    Every declaration remains eligible on later turns, never deleted globally.
    """
    if len(tools) <= _OPENAI_TOOL_LIMIT:
        return tools
    query = query.lower()[:8000]
    words = {w for w in re.findall(r"[a-z0-9]+", query) if len(w) > 2} - _SELECTION_STOP_WORDS
    recent = []
    for message in list(history or [])[-32:]:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, list):
            recent.extend(b.get("name") for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
    recent = set(recent[-8:])
    from services import assistant_tasks, chat_tools
    session = chat_tools.chat_session()
    task_tool = None
    if getattr(session, "task_id", None):
        try:
            task_tool = assistant_tasks.get_task(session.task_id).get("next_step", {}).get("tool")
        except Exception:
            pass  # Ordinary dispatch rechecks authority; selection cannot grant it.
    def rank(pair):
        index, tool = pair
        name = tool["name"]
        explicit = bool(re.search(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])", query))
        name_words, description_words = _tool_vocabulary(name, tool.get("description", ""))
        return (int(name in _HARNESS_TOOL_NAMES), int(name == task_tool), int(explicit), int(name in recent),
                4 * len(words & name_words) + len(words & description_words), -index)
    chosen = {i for i, _ in sorted(enumerate(tools), key=rank, reverse=True)[:_OPENAI_TOOL_LIMIT]}
    return [tool for i, tool in enumerate(tools) if i in chosen]


def _tools_payload_for_profile(profile: Any, query: str = "", history=None) -> list[dict[str, Any]]:
    """
    The `tools` field of the neutral `LLMRequest`, honouring the profile's
    `tools` capability (Task 8).

    `profile.tools is False` -> `[]`, matching what the request actually
    carries — NOT `_tools_payload()` filtered after the fact, which would
    leave `session_init.tool_count` reporting a catalogue size nothing was
    sent. Single source of truth for both the `session_init` frame and the
    `LLMRequest.tools` field below, so they can never disagree.
    """
    if not profile.tools:
        return []
    tools = _tools_payload()
    from services import chat_tools
    session = chat_tools.chat_session()
    tools = filter_tools(tools, getattr(session, "toolset", "all"))
    if profile.wire != "openai":
        return tools
    base = profile.base_url
    if base is None and profile.preset != "custom":
        from services import llm_config
        entry = next((p for p in llm_config.load_presets() if p.get("id") == profile.preset), {})
        base = entry.get("base_url")
    if urlsplit(base or "").hostname == "api.openai.com":
        return _bounded_tools(tools, query, history)
    return tools
