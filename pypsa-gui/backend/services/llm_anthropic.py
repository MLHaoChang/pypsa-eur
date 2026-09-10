"""
Everything Anthropic-SDK-shaped, behind the LLMProvider seam.

Moved verbatim from chat_service (2026-08-13 seam plan): build_client,
map_sdk_exception, serialise_block, with_history_cache_breakpoint — their
docstrings carry the incident history and stay with them. New here:
AnthropicProvider, which translates the neutral LLMRequest (stable
annotations) into the SDK call (cache_control) and SDK stream events into
the closed LLMEvent vocabulary. The `anthropic` package is imported lazily,
never at module import time.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from services import redaction
from services.llm_provider import LLMEvent, LLMRequest, ProviderError

logger = logging.getLogger("pypsa_gui.chat")


def with_history_cache_breakpoint(
    messages: list[dict[str, Any]], anchor: int | None
) -> list[dict[str, Any]]:
    """
    Improvement #18 — a third cache breakpoint, on the conversation history.

    The system prompt and the ~100-tool catalog are already cached, but they
    are FIXED size. The conversation is what actually grows, so on a long
    session it becomes the dominant uncached input.

    `anchor` is the index of the last COMPLETED history message, captured
    before the current user turn is appended. Anchoring there rather than at
    `messages[-1]` is the whole point:

      * The agentic loop appends assistant tool_use and user tool_result
        messages to `messages` between API calls. Marking the moving tail
        would write a NEW cache entry on every iteration — paying the 1.25x
        write premium repeatedly to cache bytes that are discarded when the
        turn ends.
      * Anchored to completed turns, the cached prefix only ever grows by
        whole turns, so each turn writes once and every later call in that
        turn reads.

    Returns a shallow-copied list; `messages` and the session's own deque are
    never mutated, because the retry path rebuilds this from the same input
    and must produce byte-identical output.

    No-op when there is no history (first turn of a session) — there is no
    stable prefix to cache, and a breakpoint on the user's own first message
    would only ever write, never read.

    Budget: Anthropic allows 4 breakpoints per request. This is the third
    (system, tools, history), so it stays inside the cap.
    """
    if anchor is None or anchor < 0 or anchor >= len(messages):
        return messages

    out = list(messages)
    target = dict(out[anchor])
    content = target.get("content")

    if isinstance(content, str):
        # A plain-string message cannot carry cache_control; promote it to a
        # single text block. Semantically identical to the SDK.
        target["content"] = [{
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        last = blocks[-1]
        if not isinstance(last, dict):
            # Nothing markable — leave the request untouched rather than
            # risk an API 400 on a malformed block.
            return messages
        last["cache_control"] = {"type": "ephemeral"}
        blocks[-1] = last
        target["content"] = blocks
    else:
        # Empty or unexpected content — nothing to anchor to.
        return messages

    out[anchor] = target
    return out


def build_client():
    """
    Construct the Anthropic SDK client. Returns the (client, error_kind) pair:
      * (client, None) — happy path; the caller drives messages.stream(...).
      * (None, "missing_api_key") — ANTHROPIC_API_KEY env var is unset.
      * (None, "sdk_not_installed") — anthropic Python package not on path.
      * (None, "unauthorized") — Anthropic returned 401 at client init.

    The constructor NEVER raises — error_kind lets the caller emit a typed
    SSE frame so the panel renders disabled rather than crashing.
    """
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "missing_api_key"
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        return None, "sdk_not_installed"
    try:
        # SDK reads ANTHROPIC_API_KEY from env by default — we DO NOT pass it
        # explicitly as a kwarg so the literal value can't accidentally
        # show up in __repr__ / logs. (v6 plan invariant.)
        client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 — surface as typed error frame
        logger.warning("chat: anthropic client init failed: %s", redaction.redact_for_log(exc))
        return None, "unauthorized"
    return client, None


def map_sdk_exception(exc: Exception) -> tuple[str, str]:
    """
    Map an Anthropic SDK exception class to a typed (error_kind, message)
    pair so the SSE writer can render a consistent error frame. Covers the
    matrix called out in the v6 plan:
      * AuthenticationError → unauthorized
      * RateLimitError → rate_limited
      * APIStatusError 429 → rate_limited
      * APIStatusError other 4xx → invalid_request (TERMINAL — see below)
      * Other APIStatusError → upstream_error
      * Anything else → internal_error

    `invalid_request` is deliberately absent from `_RETRYABLE_SDK_KINDS`. A
    4xx that is not a 429 means WE sent a request the API refuses; it is
    deterministic, so every retry is guaranteed waste. In the observed
    thinking-block incident the old `upstream_error` mapping burned four API
    calls and ~7 seconds of the user's time before surfacing the error.
    """
    # Lazy import so callers don't need anthropic installed at import time.
    try:
        import anthropic
    except ImportError:
        return "internal_error", redaction.redact_for_log(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return "unauthorized", "ANTHROPIC_API_KEY rejected by Anthropic API"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limited", redaction.redact_for_log(exc)
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", None)
        if status == 429:
            return "rate_limited", redaction.redact_for_log(exc)
        if isinstance(status, int) and 400 <= status < 500:
            # Whole-range on purpose, which does sweep in 408 Request Timeout
            # (arguably transient). Accepted: 408 is not observed from this
            # API, and a false "terminal" costs one avoidable user-visible
            # error, whereas a false "retryable" on a 400 costs every user
            # every turn — the failure this branch exists to stop.
            return "invalid_request", redaction.redact_for_log(exc)
        return "upstream_error", redaction.redact_for_log(exc)
    return "internal_error", redaction.redact_for_log(exc)


def serialise_block(content_block: Any) -> dict[str, Any]:
    """
    Coerce an Anthropic streaming content_block to a plain JSON dict the
    agent loop can stash in `session` and replay to the Messages API.

    Contract: EVERY public field the block carries survives the round-trip,
    except fields whose value is `None` (dropped, so an optional field the API
    does not expect is never sent as `null`). Dicts pass through unchanged.
    There is deliberately NO allowlist of field names — see below.
    """
    # WHY NO ALLOWLIST — do not reintroduce one.
    #
    # This function used to copy a fixed five-name list:
    #     ("type", "id", "name", "input", "text")
    # `claude-sonnet-5` returns `thinking` blocks by default (4-6 did not).
    # A thinking block carries its payload in `thinking` + `signature`; a
    # `redacted_thinking` block carries it in `data`. None of those three
    # names was on the list, so the block was serialised as a bare
    # {"type": "thinking"} and replayed with its required field missing. The
    # SECOND API call of every tool-using turn — the one replaying the
    # assistant turn plus tool results — then failed, verbatim:
    #
    #   400 invalid_request_error —
    #   'messages.1.content.0.thinking.thinking: Field required'
    #
    # An allowlist goes stale the moment the API grows a block type or a
    # field, and that staleness IS the outage. Copy what the block actually
    # carries instead.
    if isinstance(content_block, dict):
        return content_block
    # SDK content blocks are pydantic models — model_dump is the faithful,
    # forward-compatible dump. `anthropic` is NOT imported here on purpose:
    # this module must stay importable without the SDK installed.
    dump = getattr(content_block, "model_dump", None)
    if callable(dump):
        try:
            data = dump(exclude_none=True)
        except TypeError:  # pragma: no cover — pre-pydantic-v2 signature
            data = dump()
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
    # Fallback for plain objects (test doubles, non-pydantic SDK shapes).
    raw = getattr(content_block, "__dict__", None)
    if isinstance(raw, dict):
        return {
            k: v for k, v in raw.items()
            if not k.startswith("_") and v is not None
        }
    out: dict[str, Any] = {}
    for name in dir(content_block):
        if name.startswith("_"):
            continue
        value = getattr(content_block, name, None)
        if value is None or callable(value):
            continue
        out[name] = value
    return out


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: Any) -> None:
        # An already-constructed SDK client (or a test fake with the same
        # .messages.stream(**kwargs) shape). Construction stays OUTSIDE the
        # provider so chat_service._build_anthropic_client remains the one
        # monkeypatch/injection surface the test suite pins.
        self._client = client

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        # stable → cache_control, at the three request sites. The `stable`
        # key must not reach the SDK (unknown fields 400).
        system = []
        for blk in request.system_blocks:
            out = {k: v for k, v in blk.items() if k != "stable"}
            if blk.get("stable"):
                out["cache_control"] = {"type": "ephemeral"}
            system.append(out)
        tools = list(request.tools)
        if tools and request.tools_stable:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        messages = with_history_cache_breakpoint(
            request.messages, request.history_stable_anchor
        )
        try:
            with self._client.messages.stream(
                model=request.model,
                max_tokens=request.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", None)
                    if etype == "text":
                        yield LLMEvent(type="text_delta",
                                       text=getattr(event, "text", "") or "")
                    elif etype == "thinking":
                        yield LLMEvent(type="thinking_delta",
                                       text=getattr(event, "thinking", "") or "")
                    elif etype == "content_block_start":
                        blk = getattr(event, "content_block", None)
                        if getattr(blk, "type", None) == "tool_use":
                            yield LLMEvent(
                                type="tool_use_start",
                                tool_use_id=getattr(blk, "id", "") or "",
                                tool_name=getattr(blk, "name", "") or "",
                            )
                        else:
                            yield LLMEvent(type="ping")
                    else:
                        # Surface every other SDK event as ping so the
                        # harness's per-event abort check keeps its latency.
                        yield LLMEvent(type="ping")
                final = stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — mapped to neutral kind
            kind, msg = map_sdk_exception(exc)
            raise ProviderError(kind, msg) from exc

        usage = getattr(final, "usage", None)
        usage_out = {}
        if usage is not None:
            usage_out = {
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "cache_read_tokens": int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_create_tokens": int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0),
            }
        yield LLMEvent(
            type="message_done",
            blocks=[serialise_block(b)
                    for b in (getattr(final, "content", []) or [])],
            usage=usage_out,
        )

    def probe(self, model: str) -> tuple[str, float | None]:
        """
        `(verdict, latency_ms)` for the Task 9 connection test — a real
        `max_tokens=1` NON-streaming `messages.create`, mirroring
        `OpenAICompatProvider.probe`'s fixed, non-leaking vocabulary
        (`ok|unreachable|unauthorized|model_not_found|invalid_request`) and
        the same rule: only the exception CLASS NAME is ever logged, never
        `str(exc)` — the same invariant `routers/local_settings.py`'s own
        `probe_api_key` documents for the identical reason (an SDK message
        can echo request details that must not reach a log or a response).

        UNTESTED by this task's suite (`test_llm_settings_api.py` drives the
        openai-compat path exclusively via `httpx.MockTransport`): there is
        no anthropic-wire equivalent double here, so this method is
        implemented for completeness/symmetry but only exercised in
        production. See ADR-0002 / Task 11 for the live-probe follow-up.
        """
        import time
        try:
            import anthropic  # noqa: PLC0415
        except ImportError:
            return "invalid_request", None
        start = time.monotonic()
        try:
            self._client.messages.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
        except anthropic.AuthenticationError:
            return "unauthorized", None
        except anthropic.NotFoundError:
            return "model_not_found", None
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            logger.warning(
                "chat: connection test could not reach Anthropic (%s)",
                type(exc).__name__,
            )
            return "unreachable", None
        except anthropic.AnthropicError as exc:
            logger.warning(
                "chat: connection test rejected by Anthropic (%s)",
                type(exc).__name__,
            )
            return "invalid_request", None
        except Exception as exc:  # noqa: BLE001 — never let this escape as a 500
            logger.warning(
                "chat: connection test failed unexpectedly (%s)",
                type(exc).__name__,
            )
            return "invalid_request", None
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return "ok", elapsed_ms

    def probe_models(self) -> list[str] | None:
        """Best-effort model listing, or `None` on ANY failure. Never raises."""
        try:
            page = self._client.models.list(limit=20)
            ids = sorted({
                m.id for m in getattr(page, "data", []) if getattr(m, "id", None)
            })
            return ids or None
        except Exception:  # noqa: BLE001 — cosmetic, must never raise
            return None
