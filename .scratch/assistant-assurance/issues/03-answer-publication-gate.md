<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P2: private drafts and approved answer publication

Status: ready-for-agent
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

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### Proposed reliability revision

P2 also requires A2.8–A2.10: request-slot fulfillment, versioned formulas with independently computed values, and complete-population predicates. Coordinate tickets 09–10; valid facts answering a different question fail.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.
