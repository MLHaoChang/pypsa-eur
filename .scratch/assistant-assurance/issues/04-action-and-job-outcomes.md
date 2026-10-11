<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P3: verified action outcomes and real progress

Status: ready-for-agent
Type: task
Blocked by: 01, 02; live-voice issue 11 for nonblocking jobs

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Map handler results to submitted/running/completed/partial/denied/cancel_requested/cancelled/failed/uncertain. Capture requested versus actual postconditions; unknown effects require reconciliation. Remove generic completion checkmarks for queued/failed payloads. Publish only actual task/job/check transitions and retain ordinary confirmations; no model polls.

## Test-first acceptance

Required case groups: **A3.1–A3.4** in the shared test matrix. Start with a failing
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

P3 also requires A3.5–A3.6: required guards separate from courtesy checks, exact confirmed scope, real effect read-back and ambiguous-operation reconciliation. Coordinate ticket 11; no completion from model-supplied result JSON alone.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.
