<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P2: private drafts and approved answer publication

Status: ready-for-agent
Progress: S1 merged (#1154), S3 merged (#1171); S2 in progress; S4–S5 planned
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
| A2.8 | storage/generator slots, missing basis, wrong run/selection, ambiguous cost, unsupported slots limited, 20/21 blocks | Ambiguous capacity, `request_complete` false while a slot is unmapped → **S3 (passed)**; valid blocks keep scope → **S3 (passed at 12 blocks, the largest valid set; 20 slots pinned by `test_harness_reliability`)**; required warning slots → `_pending`, blocked: no warning disposition in the result-slot contract (ticket 13) |
| A2.9 | 17 MWh and energy vs objective weights; 51 EUR bridge; window joins; signed baseline; zero denominator | Percentage points, near-zero denominator, rounding cannot widen tolerance, formula DAG refs → S2 |
| A2.10 | exclusion masks; hostile labels/notes; reviewer spoof | all/none/top-k, ties, feasible incumbent vs optimum → S4; stochastic coverage pending: no checked stochastic profile exists (ticket 10) |

Slices, one PR each and test-first: S1 failure modes; S2 derived claims
(`harness/derived.py`); S3 request slots; S4 complete-population predicates
(`harness/predicates.py`); S5 interpretation markers, surface map and release
fixtures. The owner froze S2–S5 scope in AA03-FREEZE-1 (see Comments).

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

`tests/assurance_acceptance/test_p2_failure_modes.py`, 32 cases. The truncation
cases were red on master: a cut-off draft that parsed was certified `passed`.
`LLMEvent.truncated` (message_done) is an allow-list. The OpenAI-compatible wire
counts only `stop`, `tool_calls` and `function_call` as complete. The Anthropic
wire counts only `end_turn`, `stop_sequence` and `tool_use` as complete.
Anything else is truncated and fails closed: `length`, `content_filter`,
`max_tokens`, the context window, `refusal`, `pause_turn`, a missing reason or
an unknown one (GK B NOT OK @88cb8083, #1079). The Responses wire raises on
`response.incomplete`, and a test pins that. The loop skips review, and
`Turn.commit` refuses the draft. A provider error after a fragment ends as a
typed error with no retry. Before any text, it retries within the transport
budget. Each of these 7 mutants is killed by its own test: the commit refusal,
the loop hand-off, the review skip, a deny-list revert for each provider, the
event read, and the Responses incomplete branch. Normal completions still pass:
the wire case `stop`, the fake untouched draft, and the `stop`/`tool_calls`/
`end_turn`/`stop_sequence`/`tool_use` flag cases.

### Owner freeze AA03-FREEZE-1 (2026-11-05, ~14:45 CET)

Owner answer: "All (a) as listed (Recommended)".

1. Zero repairs: an invalid draft goes straight to the honest limited fallback.
2. S2 registry: difference, sum, percent_change (denominator named),
   percentage_point_delta, share_percent (basis named), unit_convert (kW/MW/GW,
   kWh/MWh; power↔energy refused). Tolerance rel 1e-9 plus a near-zero
   denominator eps, both owned by the formula.
3. S3 capacity meanings {configured, optimized, available}. An ambiguous request
   gets the choice card.
4. S4 includes every tie at the top-k boundary, labelled with the count. A
   feasible incumbent reads "feasible, not proven optimal".
5. S5 marker lists are English only, as listed. Other languages stay pending
   under ticket 09.

### GK B S3 verdict (2026-11-05): S3-CAP = (a)

GO with (a). A new bare form, "what is the capacity of <class> <name>?", gets the
existing choice card {configured, optimized, available}. The existing labelled
"capacity for" form keeps its behaviour. GK B's conditions, verbatim:

- C1. `configured_capacity` reads the input nominal per class with its own unit: Generator, StorageUnit and Link use p_nom in MW; Line and Transformer use s_nom in MVA; Store uses e_nom in MWh. Add a test per class. Never use p_nom_opt, and never sum mixed units. For an extendable asset, the label says it is the input value before the solve, and a test shows it differs from optimal_capacity.
- C2. The capacity/1 change is additive: every pre-existing metric's snapshot rows are byte-equal (AA-V1-CHANGE-1), shown by running master's capacity/1 test file unchanged in the PR worktree.
- C3. `available` stays unavailable (`available_capacity_not_checked`, request_complete false) and is never filled from p_max_pu or any derate. Add a test where p_max_pu < 1 still gives no number.
- C4. Unknown class or name, an ambiguous name, or an unsolved network for the optimized choice each give clarification or unavailable. Never a number, and never a 500.
- C5. The choice card reuses the `_needs`/pending_scope path, with a test that a card answered with a value outside {configured, optimized, available} is refused.
- C6. The rest of S3 as planned: the warning-slot, request_complete, 20-block receipt and mutant-per-branch items.

S3 plan (from the delta sent to GK B):
- First failing test: the bare form → choice card, no checked number, request_complete false, result slots needs_clarification. Then each choice → its slot.
- Configured → a new additive capacity/1 metric `configured_capacity`. Optimized → `optimal_capacity`. Available → an explicit unavailable slot.
- A required warning slot (a non-converged interval) cannot be dropped by extra metrics.
- request_complete is false while any required slot is unmapped.
- 20 valid blocks keep project/run/window in the receipt.
- Fail-closed: an ambiguous or unmapped slot becomes clarification or unavailable, never a number.

Code pointers: harness/assurance_conversation.py (`_needs`, `QUESTIONS`, `cost_choices`, the `resolve` pending branch), harness/assurance_scope.py (the `sized` capacity grammar, about line 225), harness/capacity_calculations.py (`METRICS`), harness/assurance.py (result_slots/request_complete, about lines 1270–1295), tests/test_capacity_cycles_assurance.py, tests/test_conversation_scope.py.

### S3 result

Merged as #1171. `tests/assurance_acceptance/test_p2_request_slots.py`, 30 cases: the bare capacity form gets the choice card; configured (`configured_capacity`), optimized and available (always `available_capacity_not_checked`) each map to their own slot; 16 mutants killed. Warning slots stay `_pending` under ticket 13.

### GK B S2 verdict (2026-11-06, GK B (26)): GO WITH CHANGES

Q answered (a): one run per derived entry; a cross-run difference stays with cost_delta. Test: operands from two runs give mixed_scope.

- D-1 Stored format: a draft or record without `derived` serializes byte-equal to master (omit the empty list on dump, or strip it on read), with a pin test, as for the run manifest MAC rule. Master's p2 tests running unedited is necessary but not enough.
- D-2 Operands: only blocks that were published as a checked number, or earlier derived entries. A block whose slot is unavailable, needs_clarification or uncertain is operand_not_published. Test it.
- D-3 Denominator and basis: percent_change's denominator must be one of its two operands (the baseline). share_percent's basis must share the part's unit and scope. Any other id is refused. Test a denominator that is published but a third block.
- D-4 Unit kinds: unit_convert only within one dimension and kind. MW↔MVA and MWh↔MW are refused (apparent versus active power; capacity/1 emits MVA for lines). difference/1 or sum/1 on two % operands is refused, since percentage_point_delta is the only percent-on-percent formula. Test each.
- D-5 Prose: every number in the prose traces to a published value, now also to derived entries by id. A prose number that matches neither is still refused, and a derived value can't legitimise a different number in the prose. Test a sentence that quotes a model-rounded value instead of the software value.
- D-6 Tolerance: strict (1e-9 relative, rounding not widened); the PR says a rounded model claim limits the draft. Count how often the existing assurance acceptance fixtures would hit it and report the number. No rule change without an owner decision.
- D-7 Receipt: derived entries show in the receipt with formula_id, operand ids and the software value. A truncated receipt keeps the derived block or marks the draft limited, never drops it silently (#927 / `_assurance` first).
- D-8 Every refusal has its own test and a mutant it kills. Add one mutant on the DAG order check (a forward reference accepted) and one on the scope comparison (window ignored).
