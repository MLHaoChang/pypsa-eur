<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P5: evidence navigation and honest process UI

Status: ready-for-agent
Type: task
Blocked by: 03, 04, 05; live-voice issues 03/06 for exact historical UI

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Add confidence-scope evidence cards with source/run/time/entity/window/units and safe open/data-download controls. Implement CSRF-protected expected-action view acknowledgement including turn/project/run/revision/binding generation; timeout/stale/superseded views cannot claim opened. Show actual progress, accessible statuses and expanded check explanations, no provider chain-of-thought. Current view and discussed historic run remain distinct.

## Test-first acceptance

Required case groups: **A5.1–A5.5** in the shared test matrix. Start with a failing
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

P5 must visibly preserve required-slot limitations, per-metric validity and partial-scope warnings from tickets 09–10; a generic passed badge cannot conceal unavailable requested results.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

The evidence card now exposes requested metrics, coverage/unavailable reasons, retained/current scope, review limitations and authenticated downloads. Supported full-page navigation issues an owner/project/input/turn/generation-bound action; a visible mounted page/tab triggers an authenticated browser ACK, while stale/superseded actions remain submitted. Popup/complex navigation, displayed cached-data revision proof and full browser/device/accessibility journeys remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.

Networkless study contexts have no network revision adapter: their view action
remains submitted without issuing an ACK rather than failing navigation or
fabricating a fingerprint. The new regression and all eight existing navigation
cases pass.
