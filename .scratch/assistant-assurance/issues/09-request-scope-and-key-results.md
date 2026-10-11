<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Request scope and complete key-results answers

Status: needs-triage
Type: task
Blocked by: reliability contract review; 01 for schema freeze; 02 for evidence

## Scope

Implement [reliability contract](../reliability-contract.md) section 1 across
P0/P2/P7. Define server-owned request scope/slot dispositions and versioned domain
summary profiles. Resolve authorized IDs and explicit application defaults;
clarify material ambiguity. Preserve user constraints across turns. Verify
required requested metrics and warnings; missing slots produce a limited answer.
Coordinate with [10](10-metric-calculation-and-validity.md) for metric definitions.

## Test-first acceptance

Required groups: **A0.4, A2.8, A6.6, A7.7** in [test matrix](../test-plan.md).
Use real selected projects and independently specified metrics. Include wrong
run/metric/window with numerically valid data, ambiguous objective versus cost,
omitted failed intervals, unsupported slots and a fully valid useful companion.
Bound profiles to the draft/claim limit with approved section/table behavior.
No golden request may pass by always returning unavailable.

## Completion and handoff

Done only when reviewed contracts, required cases and affected public consumers
pass, mappings cover every released domain profile and required slot dispositions
appear in receipts. Record exact commands/counts/dependencies in [handoff](../session-handoff.md)
and [ledger](../coverage-ledger.md). The full added requirement groups remain incomplete; the bounded generator profile has executable coverage.

## Comments

Original plan review does not approve this added ticket. Freeze contracts before
coding acceptance fixtures; do not weaken the transferred v1 tests.

Implementation continuation (2026-10-10): The generator-dispatch/1 candidate now assigns all three required result slots, rejects omitted available metrics, and records bounded project/entity/full-current-horizon scope. Arbitrary questions, retained runs, conversational constraints and other domain profiles remain pending. See [implementation status](../implementation-status.md). This is editing-agent prototype coverage, not inherited independent approval or whole-ticket completion.

### 2026-10-11 — shared harness implementation

Version-1 scope now binds project/entity/profile/required metrics, exact inclusive snapshot boundaries, declared cost basis and explicit retained/comparison evidence IDs. All requested slots receive a disposition and missing slots prevent request-complete claims. Whole-message generator/network cost grammar is bounded. This closes the explicit-profile slice, not arbitrary natural-language completeness, material clarification, broad conversational carryover or all-domain summaries.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.


### 2026-10-11 — bounded conversational scope continuation

Implemented live-session checked-scope carryover for whole-message repeats,
explicit window/basis follow-ups and previous-distinct-run cost comparisons.
Only successful checked results seed two immutable raw-source references;
project/generation/selection changes, rewind, missing/expired context and
incompatible baselines require software clarification. Historical repeats keep
the retained selection. Cost-quantity/basis questions do not call a model and
carry explicit needs-clarification slots, with no checked-result UI badge.

The initial 14 public-stream acceptance cases failed before implementation and
then passed. Further cases cover actual LP source changes, window growth,
corrupt/expired baselines, history isolation, budgets, shadow behavior and UI
choice publication. Final regression counts are recorded in the handoff/ledger.
Whole issue remains open: arbitrary language, durable session recovery and all
domain profiles are not completed. See [contracts](../harness-contracts.md).


### 2026-10-11 — durable checked conversation recovery

The shared harness now checkpoints the bounded scope and two distinct retained
sources privately per owner/project/chat. Only a freshly recreated session's
latest native approved history anchor may recover it; imported/copied/stale/gapped
history and unavailable records require explicit scope. Recovery revalidates the
exact source/adapter/basis/window and adopts a new browser generation once.
Strict admission and rewind durably revoke the previous checkpoint; interrupted
or failed publication cannot restore an older scope. Existing live history reads
cannot replace in-flight or newly completed stream state. No public protocol or
provider SDK path changes. Scope remains bounded to the released numerical
profiles and software clarification; broader language and task/effect memory
remain open. See the checkpoint contract and final validation ledger.
