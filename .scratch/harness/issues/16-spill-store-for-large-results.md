# 16 — Spill store: keep the whole tool result, give the model a locator

Status: ready-for-agent
Type: task
Blocked by: 08 (done)

Source: assessment design 3; DeepSeek Harness `docs/capability-seams.md`
(`ctx.spillStore`, "spill storage seam: plugins store oversized tool results")
and `docs/subsystems/spill.md` (branded locators).

## What we have

`harness/results.py`: `_truncate_result` cuts a result at
`_RESULT_CONTENT_CAP` (4,000 chars) with an explicit marker, and
`_apply_turn_tool_result_budget` caps the turn at
`MAX_TOOL_RESULT_CHARS_PER_TURN` (40,000). Hourly series, cost tables and
sweep outputs lose everything past the cut; the model cannot ask for the rest.

## What changes

1. `harness/spill.py`: when a coerced result exceeds the cap, write the full
   JSON (after `_redact_for_persist`) to
   `<app data>/chat-spill/<session6>/<tool_use_id>.json`; return the preview
   plus `spill: {locator, total_chars, shape}` where `shape` is a one-line
   description (`list[1200] of {bus, hour, p}` or `dict with keys ...`).
   Locators are opaque ids resolved ONLY inside the session's directory
   (no path from the model is ever joined). Per-session cap 50 MiB with
   oldest-first deletion; the directory is removed with the session.
2. A read-tier tool `read_result(locator, pointer?, offset?, limit?)` in the
   catalogue: `pointer` is a JSON pointer, `offset`/`limit` slice a list; the
   response is itself capped and may spill again. The description tells the
   model to page, not to request everything.
3. The truncation marker names the locator so the model knows the rest exists.
4. The `tool_result` frame is unchanged for the panel; a later panel affordance
   ("open full result") is out of scope.
5. With issue 13, the `tool/result` event records the locator.

## Done when

A FakeProvider script whose tool returns 100k chars yields a preview with a
locator, `read_result` pages it, a traversal attempt in a locator is refused
(`invalid_tool_args`), the per-session cap evicts oldest first, and the
frame recording is unchanged.
