"""
Provider-neutral vocabulary for the LLM seam (spec 2026-08-05, plan 2026-08-13).

The harness speaks ONLY these types. No provider name, SDK import, or
wire-format detail may appear above the provider layer. Cache intent is the
`stable` annotation on blocks / `tools_stable` / `history_stable_anchor`;
"cache_control" is an Anthropic word and lives in llm_anthropic only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

# Neutral error kinds. Harness-owned; each provider maps its own exceptions
# into exactly these. `invalid_request` is deliberately non-retryable (see
# chat_service._RETRYABLE_SDK_KINDS) — a deterministic 4xx retried is pure
# waste (the thinking-block incident). `unreachable` is connect-level failure
# (refused / DNS / connect timeout): meaningful for local endpoints, never
# retryable within a turn.
ERROR_KINDS: frozenset[str] = frozenset([
    "missing_api_key", "sdk_not_installed", "unauthorized", "rate_limited",
    "upstream_error", "invalid_request", "internal_error", "unreachable",
])

# Closed event vocabulary. `ping` exists so providers can surface EVERY
# upstream stream event for abort-latency purposes (the harness checks
# session.abort_event per event) without the harness knowing event shapes.
EVENT_TYPES: frozenset[str] = frozenset([
    "text_delta", "thinking_delta", "tool_use_start", "ping", "message_done",
])


@dataclass
class LLMRequest:
    model: str
    max_tokens: int
    # [{"type": "text", "text": str, "stable": bool}] — `stable` marks a
    # prefix that does not change between turns of one session.
    system_blocks: list[dict[str, Any]]
    # Neutral tool triples {name, description, input_schema} — exactly
    # chat_tools_schema.TOOLS entries, unwrapped.
    tools: list[dict[str, Any]]
    tools_stable: bool
    # Message history in the harness's persisted block format (Anthropic
    # block dicts — the documented neutral history format; non-Anthropic
    # providers translate on the way out and must not mutate these).
    messages: list[dict[str, Any]]
    # Index of the last completed message before this turn, or None on a
    # session's first turn. The stable-history marker.
    history_stable_anchor: int | None


@dataclass
class LLMEvent:
    type: str
    text: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    # message_done only: serialised content blocks (plain dicts, replayable).
    blocks: list[dict[str, Any]] = field(default_factory=list)
    # message_done only: {"input_tokens", "output_tokens",
    # "cache_read_tokens", "cache_create_tokens"} — absent keys read as 0.
    usage: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # M5 — a typo'd/unrecognised event type used to fall through the
        # harness's if/elif chain silently, indistinguishable from a "ping".
        # Every provider maps into this closed vocabulary; a value outside
        # it is a bug in the provider, not a shape the harness should ever
        # have to defend against downstream.
        if self.type not in EVENT_TYPES:
            raise ValueError(
                f"unknown LLMEvent type {self.type!r}; must be one of "
                f"{sorted(EVENT_TYPES)}"
            )


class ProviderError(Exception):
    """A provider-side failure, already mapped to a neutral kind."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "internal_error"
        self.message = message


class LLMProvider(Protocol):
    name: str

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]: ...
