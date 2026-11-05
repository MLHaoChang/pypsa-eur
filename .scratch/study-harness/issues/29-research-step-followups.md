<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 29 — Research step follow-ups: R3 fence, researched text on disk, stage-6 ranges

Status: done (#1124)
Type: task
Blocked by: 09 (PR #1104 must merge first; it owns these files)
Gated: yes (GK B)
Model tier: opus
Owner decisions: MAX-BUILDERS-1 (plan first, no code until #1104 merges), SH-RESEARCH-1, AI-558 (sourced ranges per explore change)
Design: [design.md](../design.md) §8, §3 stage 6
Parent: [09-research-step.md](09-research-step.md)

The open items from #1104 ("Not in this PR" in its description, plus a risk no review covered). Issue 07's report chapter reading `run.research` stays with issue 07.

## What to build

1. **R3 fence inside the research turn.** GK B's plan ruling R3 on 09: "fetched content offered back to the model is fenced as untrusted data". #1104 keeps fetched pages out of every *later* request (`research._forget`, GK B ask 1), but inside the research turn each re-send after a `record_source` call carries the provider's own `web_*_tool_result` blocks verbatim, in the assistant role, framed only by the turn instruction. Fence them: on every send after the first of a research turn, the earlier provider-run search and fetch blocks are offered back only inside a harness-written untrusted-data fence.
2. **Researched text on disk.** The research turn's messages go to the session event log (`assistant/attempt`, `tool/result`) and its turn record to the project's `chat.jsonl` (`turn/record`, `append_turn`), each with whole fetched pages. `_forget` changes only the derived history; the raw pages stay on disk, where a fork, a replay to a mid-turn seq, `GET /history`, a snapshot or copy bundle, and the chat-history tool can bring them back. Remove them from what persists, and fail closed: after the write, read the files back and verify no page content remains; if any does, stop the run before any later round, as an error that lists the gaps.
3. **Stage-6 explore ranges.** A source-ledger row that carries a sourced range (low and high, the gap's unit and basis, both numbers stated in the quote) for the explore stage, in the shape issue 06 reads when it admits a case change. Shape agreed with SH-06's session (see Comments). A range row is confirmed on research's own `research_confirm` card, which shows the range; confirming it adds no write steps.
4. **File-lock test (GK B, #1104 GK OK @cceed8ad "left for later").** No test pins the run's file lock on research ledger writes by itself; the lease check is the only tested guard against a stale writer. Add one: a research write while another process holds `runner._file_lock` waits for it and does not interleave.

## Acceptance criteria

- [x] A research turn with a fetched page and a `record_source` call: the second send carries no `web_*_tool_result` block; the page text it carries sits inside the fence, and a closing fence tag inside the page cannot end the fence.
- [x] Verification is unchanged: a row's quote is still checked against the retained fetched text of this turn.
- [x] After a research turn, neither the session's event log file nor the project's `chat.jsonl` contains the fetched page's text or any `web_*_tool_result` content (test reads the files).
- [x] The cleanup fails closed: when the read-back still finds page text (forced in a test), the run stops with an error that lists the gaps, and no later L1/L2 round is sent.
- [x] `derive_messages(log) == session.messages` still holds after a research turn (`test_harness_eventlog_truth`).
- [x] A range row for an explore gap: verified only when both bounds parse from the quote in the gap's unit; low ≤ high; another unit refused; `source_ledger` exposes it in the shape issue 06 agreed.
- [x] Confirming a range row on the research card sets it `confirmed` and adds no write step; the card shows low–high with unit, source and quote.
- [x] A test pins the file lock alone (lease check left in place): a mutant without the lock goes red.
- [x] Layout test still passes: no vendor word outside `harness/providers`.

## Likely files

`pypsa-gui/backend/harness/providers/research.py`, `pypsa-gui/backend/harness/research.py`, `pypsa-gui/backend/harness/eventlog.py`, `pypsa-gui/backend/harness/loop.py` (the turn record only), `pypsa-gui/backend/services/source_ledger.py`, `pypsa-gui/backend/tests/test_harness_research_followups.py` (new)

## Comments

### GK B plan verdict (4 Nov, against #1104 head d992c26d)

GO WITH CHANGES: Q1 (a) rewrite re-sends behind a fence; Q2 (a) stub, read back, rewrite or stop; Q3 (a) a research turn writes no chat.jsonl record. Code after #1104 merges.

- F1. Rewrite only re-sends that follow a client tool result (`record_source` or a read tool). Builder's choice for `pause_turn`: the research turn ends there, as #1104 already does (no resume), so no server block is ever re-sent after a pause. Tests: a rewrite after a client tool result, and a `pause_turn` that ends the turn with no further send.
- F2. The fence escape is case-insensitive and covers `</untrusted_web_content` with any whitespace or attributes. The search urls and titles are inside the fence. Test: a page that contains its own closing tag.
- F3. A fake-provider test pins the rewritten request: no `server_tool_use`, no `web_*_tool_result` and no `citations` in any earlier assistant message. A live smoke runs only if a key is present; the PR does not depend on it.
- F4. Stub where the `assistant/attempt` event is built, not at flush, so no flush path writes a raw page. Test: a flush forced mid-turn (an exception after the provider returns) leaves no page text on disk.
- F5. The read-back check also covers a snapshot or copy bundle made after the turn, and the `list_chat_history` tool's output. One test each: no page text.
- F6. A fork or replay at a seq inside a research turn is refused with a typed kind (fail-closed test plus the base-class mutant from the standing rules).
- F7. `research_cleanup_failed` stops the run under the file lock with the lease check, and lists the gaps. It goes in the error-kind fragment.
- F8. With no chat.jsonl record, `GET /history` and the chat panel still render; the turn is absent, not an error. Run the chat history tests.
- F9. The attach/confirm card path refuses a range row (`value: null`) with a typed kind; it is never divided by 1000 or written. Verified needs both bounds read from the quote, low ≤ high, and the unit. A quote with one bound only is unverified. The shape is agreed with SH-06 and recorded in both issues.
- F10. In the file-lock test, the mutant drops only `runner._file_lock` and keeps the lease check, and the test goes red.

### Range shape agreed with SH-06 (4 Nov, GK B condition E11 on SH-06)

- **Gap.** A gap for stage `explore` declares `research: {unit, basis, shape: "range"}`. Its `target` is the lever's `{component, name, attr}` exactly, as eh_site@1's explore levers name them, so SH-06 matches a row to a lever by target with no key convention.
- **Row.** `{value: null, range: {low, high}, unit, basis, url, title, quote, turn_id, status, verification, …}`. It is verified only when both bounds are read from the quote in the gap's unit, low ≤ high, and the quote is verbatim on a page fetched in this turn.
- **Confirmation (SH-06 Q1 (b), GK B E2 on SH-06).** A range row is confirmed by the user on research's own `research_confirm` card. Case admission under an envelope is unattended, so only confirmed range rows are admissible. Confirming adds no write step. The attach path refuses a range row (F9).
- **Read.** `source_ledger.ranges(rows, gap_key)` returns the verified, non-rejected range rows with their `status`. SH-06 filters `status == "confirmed"` after its own re-read under the lock.
- **Case change (SH-06 Q2 (a)).** A case change holds `{component, name, attr, value, range: {low, high, unit}, source: "research:<row id>"}`. Admission checks that low, high and unit equal the row's. No central value or step count.
- **UI.** The research card shows a range row as low–high with its unit (RunMonitor), which needs a UX review.

### Reviewer

GK B (22) `session_01G7qCujP4y18TdvSNJMmqfu` replaces GK B (21) (4 Nov). The plan stays GO WITH CHANGES (Q1–Q3 (a), F1–F10).
