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

### 2026-11-07 — audit and slice plan: GK B (31) GO WITH CHANGES (H1–H10)

Audit against master 1af1369a6a; 49 existing P4-related tests pass.

| Case | State at audit | Slice |
|------|----------------|-------|
| A4.1 exact chart data/units/scope | input chart passed; checked chart passed; result chart refusal only, no positive test vs an independent fixture; no solve identity or scope in the manifest | S1 |
| A4.2 swapped/corrupt/mislabeled/mixed/expired | dataset/render/bytes/sidecar corruption passed; swapped manifest, axis/unit/caption vs dataset, mixed runs in a delivery, quarantine: red | S2 |
| A4.3 aggregation/window/NaN/partial/race | render race passed; non-finite refused; red: a selection mixing series and static names drew the static (shadowed) value | S1 |
| A4.4 report disclosures/claims/tables/regeneration | number audit flags unverified numbers; export keeps disclosures; table equality vs evidence untested | S4 |
| A4.5 imports; table/binary delivery hashes | inspect_import guesses nothing (untested); table exports carry byte hash only | S3 |
| A4.6 same semantics across surfaces | not frozen; owner card sent through the coordinator | S5 |

Slices: S1 chart data, S2 provenance and delivery, S3 imports and table
delivery, S4 reports, S5 A4.6 and close-out. Each is under PR-SIZE-1, with a
short plan note to GK B before S2–S5.

GK B (31) answers: Q1 (a) keep refusing non-finite values, naming the asset
and first bad snapshot; Q2 (a) quarantine, with H8; Q3 (a) flag inline and in
the appendix, with H9; Q4 A4.6 is testable once the freeze names the metric
set (ticket 10's ids), a per-surface field list for masks and required
warnings, and an independent raw-data oracle.

S1 conditions: H1 refuse a mixed series/static selection (input and result);
H2 a result chart against a hand-solved oracle; H3 scope keys absent together
read as legacy, and present-but-wrong keys are refused; H4 the stored format
is unchanged (pin test); H5 the solve stamp is the result-freshness
fingerprint pair, and a chart from another solve fails verification; H6 no
widening; H7 mutants. Carried to later slices: H8 (S2) durable quarantine
under `durable.file_lock`, never cleared, with mixed runs keyed on the solve
stamp; H9 (S4) a paragraph whose audit errors is withheld, and the human
Reports path stays byte-identical; H10 (S3) `input_assertion` until
`validate_network` passes on the current revision, with the table dataset
hash taken from the frame.

### 2026-11-07 — S1 build (branch `claude/aa05-s1-chart-data`)

`create_chart` refuses `chart_data_partial` for a selection that mixes
series and static names. Before this, the static value was drawn under a
series that overrides it. The non-finite refusal now names the asset and the
first bad snapshot. The manifest gains `scope: "current"` and
`solve_stamp` (the solve's input/exact fingerprints for a result chart,
null for an input chart). `verify_chart` reads charts without both keys as
`legacy` and refuses present-but-wrong keys. It also refuses a result chart
whose stamp differs from the loaded project's solve. When the project is not
loaded, the chart reads as `solve_not_rechecked`. The download sends the
status as `X-Chart-Scope`. Tests: `tests/assurance_acceptance/test_p4_chart_data.py`.
