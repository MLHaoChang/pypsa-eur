<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P6: bounded reviewer, approved history and recovery

Status: ready-for-agent
Type: task
Blocked by: 03, 06; live-voice issue 09 for durable restarts

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Use a neutral read-only reviewer for configured interpretation risks with limited scoped evidence; no tools and no deterministic override. Share one repair limit and cumulative budget. Reviewer failure withholds interpretation and retains factual fallback. Enforce approved-only public reload/summaries/export/TTS, trace pairing/retention, context compaction/provider portability, permission-aware caches and atomic replay-safe publication.

## Test-first acceptance

Required case groups: **A6.1–A6.5** in the shared test matrix. Start with a failing
public-seam tracer, implement minimally, run green and relevant regressions.
Use independent numerical fixtures and isolated authorized projects; preserve
provider-neutral handlers, ordinary safety gates and cumulative budgets.
Record every required case as executable/passed/red/pending with a reason.
Opt-in skip or schema reachability is not behavior coverage. No paid APIs are
needed to start these deterministic slices.

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### Proposed reliability revision

P6 also requires A6.6: preserve request constraints and metric/formula/profile versions across compaction/provider switches; gate notifications/task completion and stale caches. Coordinate tickets 09–11.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

Immutable retained raw evidence now recalculates after disk reload and foreground edits, with fresh ownership/ACL checks and exact historical scope. A single optional bounded read-only reviewer can admit qualitative cited interpretations only after deterministic checks; numeric prose is withheld. Failures preserve checked facts. The configured model is not an independent expert, and unknown/late usage lacks a durable billing reservation. General conversational constraint recovery and independent domain review remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.
