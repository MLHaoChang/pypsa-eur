<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P0: output contracts and complete coverage

Status: in progress — A0.1–A0.3 done in #796; A0.4 in #1113, blocked only on GK B's OK (AA-A04-REVIEW-1)
Progress: A0.1–A0.4 executable and passing; every tool/tab/panel/output channel mapped to a v1 adapter (`harness/assurance_coverage.py`); v1 request/profile/metric/computed/guard contracts frozen in `harness/assurance_contracts.v1.json`
Type: task
Blocked by: None

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Freeze version-1 draft/approved-answer/evidence/receipt/event payloads at public seams. Map every existing tool, result tab/panel and output channel to effects/evidence/validity/progress/artifact adapters. Reuse the source inventory; declare pending real-domain fixtures and controls without tools. Strict unknown factual sources fail closed. Add fixtures/schema negotiation before editing the loop.

## Test-first acceptance

Required case groups: **A0.1–A0.3** in the shared test matrix. Start with a failing
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

P0 also requires A0.4 and reviewed request/profile/metric/computed/guard schemas. Coordinate tickets 09–11; compatible additions or protocol version changes must be decided before fixtures.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

Version-1 compatible fields now define explicit scope, required-slot dispositions/request completeness, retained/comparison IDs and qualitative interpretations. Capability/schema endpoints and the documented API make the exact implemented bounds reviewable. The user explicitly authorized implementation; prior attached-plan review instructions were design context, not a new user approval gate. This extension has editing-agent review and deterministic regression coverage, but no inherited independent domain approval. Arbitrary request completeness and whole-phase coverage remain pending.

See [implementation status](../implementation-status.md) and [contracts](../harness-contracts.md). Whole ticket remains open.


### 2026-11-04 — A0.4 contract freeze

What A0.4 needed from 09–11 was their contracts as shipped, not their completion.
`harness/assurance_contracts.py` builds one document from the live code: the
`RequestScope` schema, every profile with its metrics and definitions, the
observed and computed draft schemas, the effect guard (`effects.EffectContract`,
now enforced inside `effects.validate`) and the input-schema hash of each checked
tool. `assurance_contracts.v1.json` is the snapshot. `tests/test_assurance_contracts.py`
fails CI when the contracts drift from the snapshot. A changed checked-tool parameter,
a changed metric definition, a profile missing from the scope enum, or a metric with
no formula all fail. `--write` refuses a non-additive update.
`tests/assurance_acceptance/test_p0_server_owned_contracts.py` covers spoofed
intent and ownership. A scope for another project is limited (`wrong_project`).
Forged owner/authorized/verified fields, an unregistered profile version, a bool
version and forged effect guards (tool, confirmation flag, class, bool or unlisted
attribute, revision) all get a 422 before any model call. A metric outside the
registry is never answered.

Owner decisions (2026-11-04): AA-V1-CHANGE-1 adopts the additive-only rule within v1 as built. A removal, a rename or a changed metric or profile needs a new profile version beside the old one, and only breaking draft or receipt changes bump the protocol version. AA-A04-REVIEW-1 makes GK B's OK on the frozen snapshot the P0 contract review. `independent_domain_approval` stays false.

Named blocker before Status done: GK B's OK on #1113.

The snapshot's `pending` list names what is not frozen and who owns it: arbitrary
scope (09), action/proposal blocks (09–11), guards for other effectful tools (11),
evidence/receipt identity (02) and independent domain approval (10). Under the
rule built here, each can be added later without breaking v1.
