"""
Token usage from an OpenAI-shaped `usage` object, in the harness's usage
vocabulary (`harness.protocol.LLMEvent.usage`).

The harness meaning (owner decision, 2026-10-12, chat harness issue 20):
`input_tokens` is input that was NOT served from the provider's cache, on
every wire; cache reads and writes are reported beside it. Anthropic reports
it that way. Every OpenAI-shaped wire folds cached input into its prompt
count, under its own field names:

  chat completions  prompt_tokens, prompt_tokens_details.cached_tokens
                    (Kimi also .cache_write_tokens and a top-level
                    cached_tokens; DeepSeek prompt_cache_hit_tokens)
  Responses API     input_tokens, input_tokens_details.cached_tokens

so this subtracts them back out. Shared by `openai_compat` and
`openai_responses` so the two OpenAI paths cannot disagree.
"""
from __future__ import annotations

from typing import Any


def _count(value: Any) -> int:
    """A token count from a vendor field; anything not a non-negative int is 0."""
    if isinstance(value, bool):
        return 0
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def usage_from(u: dict[str, Any]) -> dict[str, int]:
    """All four usage keys with the harness meaning. An inconsistent report
    (cached > total) clamps uncached input at zero rather than going negative."""
    details = u.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = u.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    cache_read = (_count(details.get("cached_tokens"))
                  or _count(u.get("cached_tokens"))
                  or _count(u.get("prompt_cache_hit_tokens")))
    cache_create = _count(details.get("cache_write_tokens"))
    total_in = _count(u.get("prompt_tokens")) or _count(u.get("input_tokens"))
    out = _count(u.get("completion_tokens")) or _count(u.get("output_tokens"))
    return {
        "input_tokens": max(total_in - cache_read - cache_create, 0),
        "output_tokens": out,
        "cache_read_tokens": cache_read,
        "cache_create_tokens": cache_create,
    }
