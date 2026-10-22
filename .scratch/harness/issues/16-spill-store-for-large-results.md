# 16 — Spill store: keep the whole tool result, give the model a locator

Status: done (e1adb0db, no own PR)
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

## Comments

2026-10-12 (re-scoped against master after 143 commits; nothing of this issue exists there yet):
`harness/results.py` is unchanged (4,000-character cut, 40,000 per turn).
Partial substitutes now exist and come first: domain paging
(`get_study_evidence` cursors), immutable run evidence (`get_run_evidence`),
task-step results up to 100k characters via `$step.path`, and file IDs from
`build_delivery` / `get_file_delivery`. Changes to this issue:
- The truncation marker names the domain's own paging tool when there is
  one; the spill locator is the fallback.
- Never spill, and never let `read_result` read, during a checked turn:
  `Turn.tool_guard` (`harness/assurance.py`) allows only mapped tools and
  `result_projection` replaces raw results there.
- Add `read_result` to `toolsets.CONTROL_TOOLS`, or the OpenAI 128-tool
  selection in `providers/wiring` can drop it.
- Store under the owner-scoped app-data pattern of
  `services/assurance_records` (uuid-validated ids, mode 0700,
  `harness/durable.transaction`); bind locators to owner and session; delete
  on session eviction and a TTL.
- `smoke/assistant_inventory.py --write` after adding the tool (CI checks it).

2026-10-12 (done). `harness/spill.py` keeps a result whose model-facing JSON
exceeds the 4,000-character cut, at
`<app data>/chat-spill/<sha(owner)>/<sha(session)>/<locator>.json` (0700
directories, 0600 files written atomically under `harness/durable.transaction`,
redacted with `history._redact_for_persist` and scrubbed of identifying keys).
The model gets `{spill: {locator, total_chars, shape, read_with}, preview,
note}` instead of the cut; the panel's `tool_result` frame is unchanged.
Not spilled: a checked turn, a non-dict/list result, a result over 20 MiB,
and any tool whose schema has `offset`, `page` or `cursor` (derived from the
catalogue, so `read_result` itself never spills): its cut now names that
argument ("call list_components again with its `offset` argument …").
Bounds: 50 MiB per session, oldest first; session directories untouched for
24 h are swept on the next write; `drop_session` removes its own. Locators
are 32 hex characters checked before any path is built; another owner's or
chat's locator, an expired one and any read during a checked answer are the
new inline kind `result_unavailable`. `read_result(locator, pointer, offset,
limit)` takes a JSON pointer, pages lists (shrinking a page to fit), describes
large objects by key and slices long strings. It is a control tool
(`toolsets.CONTROL_TOOLS`); the capability inventory is regenerated (230
tools). Tests: `tests/test_harness_spill.py` (23, red first). The parity
battery is unchanged: no battery prompt produces an oversized result, and
the FakeProvider tests drive the real loop.
Gates: the whole backend suite in three shards, 11,788 passed and 1 failed:
`test_every_unguarded_third_party_import_is_pinned_for_the_build`, which
fails identically on clean master (PR #137 put a pytest plugin,
`tools/pilot_evaluation_plugin.py`, where the scan treats code as shipped;
merged while Actions was not running). Frontend 314 files / 3,695 tests, tsc
clean; inventory and control-matrix checks pass.
