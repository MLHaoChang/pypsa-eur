<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P4: charts, tables, reports and delivery provenance

Status: ready-for-agent
Progress: PNG integrity and JSON/ZIP export shipped; checked chart, report, import and general-export adapters pending
Type: task
Blocked by: 02, 03

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Extend existing chart/report/import/export/delivery services to exact captured datasets and typed render specifications; atomically bind data/render/byte hashes and source identities. Validate indices/units/aggregation/axis/caption/scope, signed/finite/missing values and disclosure retention. Reject swapped manifests and quarantine invalid output. Models review presentation/interpretation, software checks numeric truth; downloads recheck access.

## Test-first acceptance

Required case groups: **A4.1–A4.5** in the shared test matrix. Start with a failing
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

P4 also requires A4.6: preserve the same metric/formula/raw-validity/coverage semantics in artifacts as in approved answers. Coordinate ticket 10 and use independent raw-data oracles.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

Authenticated checked-answer JSON/ZIP now exports the same approved facts, request scope, slots, limitations and revalidated evidence. ZIP binds answer JSON and spreadsheet-safe facts CSV by SHA-256/bytes. Existing PNG integrity is unchanged. Checked chart/report/import and other delivery adapters remain pending; an export does not establish scientific certification.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.
