<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 29 — Research step follow-ups: R3 fence, researched text on disk, stage-6 ranges

Status: ready-for-agent
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
3. **Stage-6 explore ranges.** A source-ledger row that carries a sourced range (low and high, the gap's unit and basis, both numbers stated in the quote) for the explore stage, in the shape issue 06 reads when it admits a case change. Shape agreed with SH-06's session.
4. **File-lock test (GK B, #1104 GK OK @cceed8ad "left for later").** No test pins the run's file lock on research ledger writes by itself; the lease check is the only tested guard against a stale writer. Add one: a research write while another process holds `runner._file_lock` waits for it and does not interleave.

## Acceptance criteria

- [ ] A research turn with a fetched page and a `record_source` call: the second send carries no `web_*_tool_result` block; the page text it carries sits inside the fence, and a closing fence tag inside the page cannot end the fence.
- [ ] Verification is unchanged: a row's quote is still checked against the retained fetched text of this turn.
- [ ] After a research turn, neither the session's event log file nor the project's `chat.jsonl` contains the fetched page's text or any `web_*_tool_result` content (test reads the files).
- [ ] The cleanup fails closed: when the read-back still finds page text (forced in a test), the run stops with an error that lists the gaps, and no later L1/L2 round is sent.
- [ ] `derive_messages(log) == session.messages` still holds after a research turn (`test_harness_eventlog_truth`).
- [ ] A range row for an explore gap: verified only when both bounds parse from the quote in the gap's unit; low ≤ high; another unit refused; `source_ledger` exposes it in the shape issue 06 agreed.
- [ ] A test pins the file lock alone (lease check left in place): a mutant without the lock goes red.
- [ ] Layout test still passes: no vendor word outside `harness/providers`.

## Likely files

`pypsa-gui/backend/harness/providers/research.py`, `pypsa-gui/backend/harness/research.py`, `pypsa-gui/backend/harness/eventlog.py`, `pypsa-gui/backend/harness/loop.py` (the turn record only), `pypsa-gui/backend/services/source_ledger.py`, `pypsa-gui/backend/tests/test_harness_research_followups.py` (new)

## Comments
