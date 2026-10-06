# 20 — Provider parity gaps: cache token accounting and reasoning on the OpenAI wire

Status: ready-for-agent
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
