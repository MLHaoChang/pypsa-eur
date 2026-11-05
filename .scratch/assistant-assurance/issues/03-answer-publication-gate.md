<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P2: private drafts and approved answer publication

Status: ready-for-agent
Progress: audit 2026-11-05 (master ad12e8c7) below; S1 failure-mode pins in progress, S2–S5 planned
Type: task
Blocked by: 01, 02

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Begin with unsafe early text/thinking withheld at the authenticated stream seam while real software progress flows. Preserve tool-use/result pairing and execution gates. Admit constrained structured facts with software rendering and deterministic units/attribution/arithmetic checks; gate every public projection. One repair maximum, factual fallback, no repeat tools, atomic approved answer and receipt.

## Test-first acceptance

Required case groups: **A2.1–A2.7** in the shared test matrix. Start with a failing
public-seam tracer, implement minimally, run green and relevant regressions.
Basic current UI/history/summaries/exports/TTS must all use approved content in
this phase. Commit answer+receipt durably before tokens/links; interrupted SSE
recovers through idempotent fetch, not regeneration. Until P3, withhold unsupported
action-completion claims. Use independent numerical fixtures and isolated authorized projects; preserve
provider-neutral handlers, ordinary safety gates and cumulative budgets.
Record every required case as executable/passed/red/pending with a reason.
Opt-in skip or schema reachability is not behavior coverage. No paid APIs are
needed to start these deterministic slices.

## Case state

Audit against master `ad12e8c7` (2026-11-05). Baseline: `test_p2_publication`,
`test_answer_assurance`, `test_assurance_metrics`, `test_harness_reliability` and
`test_conversation_scope` give 165 passed, 0 skipped. Their strict fixtures need no
opt-in gate. Each case is executable and passing for the parts named. "Pending"
names the slice that closes it, or its external blocker.

| Case | Passing on master | Gap → slice |
| --- | --- | --- |
| A2.1 | p2 prose/thinking/intermediate; p6 history/export; p7 every wire; LC07 caption = speech | Rejected draft never queued for speech, unsafe text split at every offset → **S1 (passed)** |
| A2.2 | p2 wrong value/unit/entity/sign; wrong project/window | Same value under another metric → **S1 (passed)** |
| A2.3 | sign, MW/MWh, %-of-baseline, zero baseline, full precision, nonfinite | kW/MW conversion and raw-value tolerance → S2; locale strings rejected by the strict number schema → S2 pin |
| A2.4 | delta/cost oracles; invented formula id | Model-composed derived claims, known formula on wrong operands, arbitrary expressions → S2 |
| A2.5 | contradictory prose/narrative, omitted claims, invented entities | Whole-population quantifiers and causal wording → S5 |
| A2.6 | bad JSON/nonobject → limited; checker/guard exception; commit failure; reviewer failures | Truncated draft that parses (OpenAI-compatible `length`/`content_filter`, Anthropic `max_tokens`) → **S1 (red, now passed)**; provider error before/after a fragment → **S1 (passed)**; zero repairs pinned exactly → **S1 (passed)** |
| A2.7 | reviewer without tools; IDs/pairing; abort; generation change; stale ACK | Repair spends 0 tool executions (dispatch spy) → **S1 (passed)** |
| A2.8 | storage/generator slots, missing basis, wrong run/selection, ambiguous cost, unsupported slots limited, 20/21 blocks | Ambiguous capacity, warning slots, 20 valid blocks keep scope, `request_complete` false while a slot is unmapped → S3 |
| A2.9 | 17 MWh and energy vs objective weights; 51 EUR bridge; window joins; signed baseline; zero denominator | Percentage points, near-zero denominator, rounding cannot widen tolerance, formula DAG refs → S2 |
| A2.10 | exclusion masks; hostile labels/notes; reviewer spoof | all/none/top-k, ties, feasible incumbent vs optimum → S4; stochastic coverage pending: no checked stochastic profile exists (ticket 10) |

Slices, one PR each and test-first: S1 failure modes; S2 derived claims
(`harness/derived.py`); S3 request slots; S4 complete-population predicates
(`harness/predicates.py`); S5 interpretation markers, surface map and release
fixtures. S2, S4 and S5 wait on the owner's freeze (AA03-FREEZE-1).

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### First implementation continuation

Strict text/thinking buffering, typed static facts, software fallback, durable
answer/receipt and checked UI/history/export/TTS are implemented in the first
pilot. This ticket is not complete: derived claims, request fulfillment, complete
surface mappings and release fixtures remain required. See
[implementation status](../implementation-status.md).

### Proposed reliability revision

P2 also requires A2.8–A2.10: request-slot fulfillment, versioned formulas with independently computed values, and complete-population predicates. Coordinate tickets 09–10; valid facts answering a different question fail.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### GK B plan verdict (2026-11-05)

GO WITH CHANGES for S1 and S3. S2, S4 and S5 are GO in shape, and each starts
once its freeze items are settled; a short plan delta goes to GK B before each.
The freeze belongs to the owner, as asked in AA03-FREEZE-1. Conditions:

- S1: F1 the truncated fragment parses as valid JSON and still ends limited, red
  first. F2 nothing public, checked on SSE frames, history, export, answer record
  and speech input. F3 split at every offset, checked on the assembled stream.
  F4 exactly 1 provider request and 0 tool executions. F5 each new fail-closed
  branch has a mutant killed by its own test.
- S2: the `derived` block is additive (master's p2 tests run unedited); only the
  software value is rendered; power↔energy refused; operands share run, window
  and basis; each refusal kind has its own test and mutant; the ticket 10
  overlap is noted there, not built.
- S3: the clarification uses the existing choice mechanism. A warning slot cannot
  be dropped by adding metrics. `request_complete` stays false while any
  required slot is unmapped.
- S4: the population is every entity in the evidence fact set. A masked or
  unreadable member makes it incomplete. Labels never feed truth (hostile-label
  test). Stochastic stays a named pending row.
- S5: the unmapped-surface test enumerates surfaces from the code's own registry
  or consumers. Marker lists are English only, with the pending row under 09.
  The golden fixture requires zero false accepts and every valid golden answered.

### S1 result

`tests/assurance_acceptance/test_p2_failure_modes.py`, 16 cases. The truncation
cases were red on master: a cut-off draft that parsed was certified `passed`.
`LLMEvent.truncated` (message_done) is now set by the OpenAI-compatible wire on
`length`/`content_filter` and by the Anthropic wire on
`max_tokens`/`model_context_window_exceeded`/`refusal`; the Responses wire already
raises on `response.incomplete`. The loop then skips review, and `Turn.commit`
refuses the draft. Mutants killed: commit refusal, loop hand-off, review skip,
the OpenAI-compatible flag, the Anthropic stop set, the event read.
