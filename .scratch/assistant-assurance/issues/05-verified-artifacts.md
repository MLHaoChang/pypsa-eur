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

### 2026-11-07 — A4.6 frozen (owner decision AA05-FREEZE-1); GK B (31) K1–K5

The owner froze A4.6 with three things named:

- **Metric set:** every key of `harness.calculations.DEFINITIONS` at
  1af1369a6a (69 metrics, 69 formula_ids), pinned. A later metric joins only
  by a ledger row.
- **Fields per surface:**
  - *Identity:* metric, formula_id, unit, entity_id, window (start, end,
    snapshot count, weight basis), evidence_id, source_sha256 and the solve
    stamp.
  - *Masks:* coverage, rows used and excluded, and the validity flags.
  - *Required warnings:* slot dispositions and limitations, carried on the
    answer, chart manifest, table sidecar, PDF/DOCX (only where a report table
    carries a checked fact) and download metadata.
- **Oracle:** raw fixture frames checked by an independent numpy calculation,
  never the service under test.

Coverage: identity, mask and warning agreement for all 69; the raw numeric
oracle for one metric per profile module (about 20).

GK B (31) adds K1–K5 for S5:

- **K1:** identity includes project_id; a fact from project B on project A's
  chart or table is refused.
- **K2:** unavailable (not_established, no_data, not-evaluated) reads the same
  on every surface, never 0 or blank; one test per surface.
- **K3:** machine surfaces carry the exact float and unit. Rendered surfaces
  equal the receipt value after the surface's pinned rounding, applied once.
- **K4:** every fixture uses non-unit snapshot weightings. A window split
  checks sums, and checks means and utilisation as weight-weighted means. The
  boundary is half-open, with a test that no snapshot is counted twice or
  dropped.
- **K5:** one plausible-but-wrong aggregate per family, named: an unweighted
  sum, a flipped Link/Store sign, or an excluded row included.

### 2026-11-07 — S1 GK OK @202d381c (#1224); S2 plan: GK B (31) GO WITH CHANGES (J1–J6)

S2 covers A4.2:

- **Quarantine:** a marker file `assistant_artifacts/quarantine/<file_id>.json`,
  written under `durable.file_lock`. It is never cleared; only a new file_id
  passes.
- **Labels:** the render spec is checked against the dataset and sidecar.
- **Swapped manifests:** tests only.
- **Mixed runs:** a delivery is refused unless every chart shares one solve
  stamp, or one input fingerprint.
- **Expired evidence:** the delivery item keeps evidence_status.

Q: a legacy chart with a stamped chart in one delivery is refused (a).

GK B's conditions:

- **J1:** a marker is written only on an integrity verdict (bytes, dataset,
  sidecar, manifest, project or labels). An IO error, lock timeout,
  unloaded project, solve_stale or solve_not_rechecked never writes one.
- **J2:** the file_id is validated against the upload store's id pattern
  before any path is built. Marker and lock stay under the owning project.
- **J3:** label checks and label quarantine apply only to manifests with the
  S1 scope keys; legacy manifests verify exactly as on master.
- **J4:** an unreadable or corrupt marker refuses; it never reads as "no
  marker".
- **J5:** solve_stale and current charts with equal stamps may share a
  delivery. The refusal names two file ids, capped. A legacy-only delivery
  still builds.
- **J6:** hand mutants for the verdict-only rule and the unreadable-marker
  refusal.

H8: GK B (32) (a), H8a–H8d. An identical redraw of a quarantined chart gets the
same content-hash file_id, so `create_chart` refuses it with
`artifact_quarantined` once the file_id is computed and before any blob, meta
or sidecar write (H8a). An unreadable marker also refuses (H8b). The kind is in
`tool-error-kinds.d/claude-aa05-s2-delivery.json` (H8c). Tests and the hand
mutant that removes the check are in `test_p4_delivery.py` (H8d). A marker
landing after the check is caught by `verify_chart` before download or delivery.

### 2026-11-07 — S2 GK OK @2729de42 (#1227); S3 plan: GK B (32) GO WITH CHANGES (R1–R9)

S3 covers A4.5: import assertions (H10), no guessing at inspection, and
dataset hashes for table exports. Answers: Q1 (a) only `export_to_csv` and
`export_to_excel` get manifests; Q2 (a) download re-parses the bytes, with
R5–R6; Q3 (a) import status in the import and validate results and in the
validity of artifacts drawn from an import, with R4. W1 (no-marker assertion
on the evidence-mismatch test) rides S3.

- **R1:** the validated fact lives project-side under `assistant_artifacts/`
  (durable.file_lock), never in network.meta; `import_uploaded_network` strips
  any `assistant_validated_*` key; a file pre-stamped with its own fingerprint
  imports as input_assertion.
- **R2:** validate_network stays read-tier: dirty flag, exact fingerprint and
  meta unchanged.
- **R3:** one exact fingerprint per call; unloaded or raising reads
  input_assertion; GUI-route imports stay not_imported (Skipped).
- **R4:** import_status enters a validity block only with an import stamp;
  otherwise manifests are byte-identical to master (H4 pin extended); a drawn
  artifact keeps its sealed status.
- **R5:** at write, re-parse the bytes with verify_table's parser and require
  an equal dataset hash before sealing, else refuse with a fragment kind;
  cases: timestamp index, NaN vs empty string, formula-escaped cell, 17-digit
  floats, Excel sheet name.
- **R6:** verify_table orders seal, byte hash, re-parse; seal/byte mismatch
  quarantines; a re-parse mismatch or exception on matching bytes refuses and
  logs, no marker. Hand mutants: re-parse mismatch quarantines; re-parse
  removed; R1 compare dropped.
- **R7:** delivery items carry dataset/render hashes only for verified table
  sidecars; legacy and the other five exports build as on master (pin test);
  the PR says whether tables join `_one_run`.
- **R8:** `quarantined()` before any write in the table export path (H8a).
- **R9:** `"delimiter": "comma (not detected)"`; inspect_import never returns
  inferred units or a mapping.
