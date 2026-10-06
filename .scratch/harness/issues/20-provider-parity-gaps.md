# 20 — Provider parity gaps: cache token accounting and reasoning on the OpenAI wire

Status: ready-for-agent (done 2026-10-06: usage with Anthropic meaning on the OpenAI wire; W4 and per-turn usage in the parity battery)
Type: task
Blocked by: 07 (done)

Source: assessment design 8; DeepSeek Harness `docs/subsystems/llm-streaming.md`
(reasoning as a first-class content block on every adapter; `TokenUsage`
with disjoint uncached input, cached read, cached write and output counts;
provider-neutral failure codes).

## What we have

`harness/protocol.py`: `LLMEvent.usage` already names `cache_read_tokens`
and `cache_create_tokens`; `harness/providers/anthropic.py` fills them.
`harness/providers/openai_compat.py` passes `reasoning_content` /
`reasoning` through passively and drops thinking blocks on replay (by
design: no wire equivalent). The metrics and the daily spend count what the
adapters report.

## What changes

1. `openai_compat`: fill `cache_read_tokens` from
   `usage.prompt_tokens_details.cached_tokens` when present (OpenAI, DeepSeek
   and Kimi all report it); `cache_create_tokens` stays 0. The metrics
   snapshot and `_today_token_spend` are checked to count uncached input
   consistently on both wires (a red-first test per wire with the stub).
2. `openai_compat`: emit `thinking_delta` events from streamed
   `reasoning_content` deltas so the panel's thinking affordance works on
   both wires; replay still drops them (the existing rule), and the
   history sanitiser is unchanged.
3. The stub endpoint gains a reasoning branch; the parity battery's summary
   prints usage per prompt per wire so a gap is visible in the runbook table.

## Done when

The parity battery reports usage on both wires, thinking shows on the
OpenAI wire in the panel, and the frame recording is unchanged.

## Comments

2026-10-06 (done): two findings changed the plan. Reasoning already streamed
as `thinking` on this wire (`_REASONING_KEYS`, pinned by
`test_openai_compat_streams_text_tools_reasoning_and_usage`), so item 2 became
a test that it reaches the panel and is never replayed. The usage gap was
larger than "cache reads read as 0": every OpenAI-compatible vendor counts
cached input inside `prompt_tokens`, so this wire charged a cached prefix as
fresh input to the daily cap and the metrics. `_usage_from` in
`harness/providers/openai_compat.py` now reports all four keys with the
Anthropic meaning, reading OpenAI's and Kimi's
`prompt_tokens_details.cached_tokens` (Kimi's `cache_write_tokens` and
top-level `cached_tokens` too) and DeepSeek's `prompt_cache_hit_tokens`, each
checked against the vendor's API reference, and clamps an inconsistent report
at zero. The stub reports a cache read on every reply and gained a reasoning
branch; the battery gained W4 and fails a wire that reports no usage.
Tests: `tests/test_harness_provider_parity.py` (13, red first), two stub
tests, one seam pin updated to the four-key shape. Parity: stub 4/4, live
Anthropic 4/4 (runbook). Gate: the full chat regression, 3822 passed, 7
skipped, 0 failed.
