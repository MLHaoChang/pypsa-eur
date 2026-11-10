<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P4: charts, tables, reports and delivery provenance

Status: done (S1–S5b and Y merged; #1292 is the last PR; the readiness oracle's project_value/limit stays a recorded follow-up)
Progress: all slices shipped: checked chart (S1), delivery (S2), imports (S3), reports (S4a, S4b), fact identity (S5a), chart identity and fact-free tables/reports/headers (S5b), summary facts (Y)
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
- **R1 storage (a), app-data store, R1a–R1d** (supersedes the project-side
  location above): R1a a server-minted uuid4 import_id, the carried stamp and
  validated keys stripped; R1b a 32-hex id check before any path is built;
  R1c import_id, sha256 and exact fingerprint all equal, anything unreadable
  reads input_assertion, a hand mutant on the sha256 compare; R1d
  durable.file_lock with an atomic replace, growth named in Skipped; plus an
  import → validate → save → reload test that still reads validated.
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

### 2026-11-07 — S4 plan: GK B (32) GO WITH CHANGES (V1–V7)

S4 covers A4.4 (reports) with H9 and Q3 (a). Answers: Q1 (a) withhold with an
inline placeholder, under V1; Q2 (a) a sealed manifest for assistant report
exports, split into S4a (P1–P6) and S4b (the manifest, with its own plan note
to GK B first); Q3 (a) withhold in the job, so the stored version is already
withheld. Plan P1–P7: the assistant's generate and regenerate run strict; an
inline placeholder in docx, pdf and get_report, plus the appendix; tables
equal a hand oracle cell for cell; required disclosures survive generate,
regenerate and export (`report_disclosure_missing`, 409); regenerating one
section leaves the others byte-equal; the human routes are pinned
byte-identical; mutants.

- **V1:** the placeholder names the count and the reason only ("Withheld: 2
  numbers could not be checked against the study; see Numbers to check").
  Values appear only in the appendix, marked unverified (withheld), in docx,
  pdf and get_report's section view. Test: no withheld value string appears in
  any surface's body text.
- **V2:** the strict keyword is passed in-process only, never as a query, body
  or form field on routers/reports.py or report_jobs.py. Test: a client
  sending strict=true (or the keyword's name) to the human routes gets
  master's behaviour; a pin on the same report shows identical bytes.
- **V3:** fail closed. An audit exception, a missing audit, or an audit that
  does not cover a paragraph withholds it under strict, with AUDIT_FAILED as
  in run_report; reuse withhold_unverified, no second filter. Hand mutant: an
  audit exception that publishes.
- **V4:** a strict regenerate of one section of a human-generated report
  leaves the others byte-equal; a later human non-strict regenerate may
  un-withhold it (master behaviour), and the assistant's get_report reads that
  version as stored, with the appendix flags. Stated in the PR, one round trip
  tested.
- **V5:** the required disclosure set comes from the stored version's
  evidence, not the current project state; `report_disclosure_missing` goes
  in the fragment; a generate and a regenerate that drop a required callout
  are both tested.
- **V6:** the table oracle is computed from the fixture's raw numbers, not
  through EvidenceTable's code path; it covers a negative value, a NaN or
  missing value and a unit cell.
- **V7:** mutants: strict dropped, withhold-to-flag, disclosure check removed,
  the pin's flag default flipped, a placeholder that echoes the value (V1),
  an audit exception that publishes (V3).

### 2026-11-07 — S3 review: GK B (32) NOT OK @3dd893d7 (T1, T2), fixed

- **T1:** a refused identical re-export overwrote the earlier export's sealed
  sidecar, and the next download quarantined it. Now `_publish_sealed_export`
  runs the write-edge checks before any sidecar write, writes the sidecar and
  the upload's seal under the project upload lock, and keeps a sidecar that
  already seals the live upload (first seal wins). Charts use the same helper.
- **T2:** validate_network reads the exact inputs before and after the check
  and records only when the two agree; a hand mutant on the compare is killed.

### 2026-11-07 — S4a GK OK @36cf88f2 (#1234); S4b plan: GK B (32) GO WITH CHANGES (X1–X6)

S4b seals the assistant's report exports (S4 Q2 (a)). Answers: Q1 (a) seal in
the route under the in-process strict switch; Q2 (a) download re-checks the
seal, the bytes, the stored version's hash and, for Word, the disclosures;
Q3 (b) a source that is gone is noted and the bytes served, with X3. W1 from
#1234 rides S4b: `is_withheld_block` matches the full placeholder text. The
SH-07 wording in `docs/agents/review-gk.md` lands in the S4b PR.

- **X1:** strict path order: render, PDF conversion, `_check_disclosures`,
  `quarantined()`, `_publish_sealed_export`; no `add_upload` on it. The human
  path stays byte-identical with no sidecar (pinned).
- **X2:** `version_sha256` is one function at seal and verify, the hash of the
  stored file as written (basis recorded), tested against a load-normalised
  version.
- **X3:** a missing version, figure or template is served with `source_status`
  and a note, no marker; one present with another hash refuses
  `report_version_changed`, no marker.
- **X4:** J1 throughout: only a seal or byte mismatch quarantines; a disclosure
  re-read that fails or drops one on matching bytes refuses and logs (R6).
- **X5:** no `_one_run`; the delivery item carries dataset_sha256 and
  render_spec_sha256, as tables do.
- **X6:** hand mutants: seal skipped, version compare dropped, version change
  quarantines, quarantine check removed, missing version quarantines,
  disclosure drift quarantines, X1 order inverted; each with its killing test.

### 2026-11-07 — Y plan: GK B (33) GO WITH CHANGES (Y1–Y7), option (a)

GK B (33) restated GK B (32)'s summary as binding conditions; AA-05 (6)
adapted Y1 to the code (no summary declaration existed), and GK B (33)
confirmed it. It ships as its own PR after S4b.

- **Y1, scope:** an additive `Section.summarises` field, declared through
  `prepare_report_job(summaries=...)` (route body, `generate_report`, stage 7's
  optional argument). A declared summary admits the evidence payload facts of
  the sections it lists, one level only. It never admits their prose, quoted
  text, or withheld or unverified values. A declared or listed id the
  document does not have refuses `summary_section_unknown` (400) before any
  job starts; it is never skipped.
- **Y2, one path:** the widening lives only in `report_job.admitted_facts`,
  so the assistant's strict generate and stage 7 get the same set. (a): stage
  7 takes an optional caller argument, default none, so production stage 7 is
  unchanged. One test per caller.
- **Y3, tests:**
  - a listed section's fact is verified;
  - the same fact unlisted is withheld;
  - copied prose is withheld;
  - a non-summary gains nothing;
  - an unknown id refuses.
  qa_reports_phase2's FMEA step stays (its summary declares nothing).
- **Y4, mutants:** admit all to all, a summary admits every section, prose
  admitted, unknown-id refusal dropped, `summarises` dropped from the digest.
  Then mutants.py on the changed modules.
- **Y5, disclosure:** the strict summary's note names the listed sections, and
  the sealed manifest's `dataset.summaries` records them.
- **Y6, identity:** `summarises` is stored in the version file, so a
  different declaration gives a different `version_sha256`.
- **Y7, human route:** stored and validated there too, but a non-strict job's
  output is the same with and without a declaration.

### 2026-11-07 (~20:07 CET) — S5 plan: GK B (34) verdict (verbatim)

- Q1, scope: an OWNER question, so send it to the coordinator as an AskUserQuestion card. AA05-FREEZE-1 names five surfaces for the field lists: answer, chart manifest, table sidecar, PDF/DOCX, download metadata. Your (a) pins three of them as carrying no checked fact, which narrows what the owner chose. It is sound engineering, and my recommendation is (a); (c) leaves the chart without facts, and (b) is several PRs. But the owner has to confirm that "pinned absent" counts as frozen for table, report and headers. Put the finding (only two surfaces carry facts today) in the card. Build S5a meanwhile: it is the same under (a) and (b).
- Q2: GO on (a). window.weight_basis and solve_stamp {input, exact} as new keys. Readers see old records as legacy and byte-equal output (strip on read, stored-format ruling), with a pin test that the other MAC domains are unchanged. Rows used/excluded come from the slots' valid/requested snapshots, with a test on a slot that excludes snapshots.
- Q3: GO on (a). One test per surface that unavailable is never 0 or blank and keeps its reason code. A shared vocabulary is out of scope while no fact reaches reports.
- K1–K5 GO as written:
  - K3: pin "no rounding" as exact repr equality on every machine surface;
  - K4: non-unit snapshot_weightings that differ between the two halves of the split;
  - K5: each family's wrong-aggregate mutant gets its own killing test.
  The 16 oracles read raw component data only and never import the service under test (FREEZE-1 (iii)); add a grep-style test of the oracle module's imports.
- Split: S5a and S5b as you propose, one PR each.

The plan note (AA-05 (7)) found that only two surfaces carry a checked fact
today: the answer (its evidence record and receipt) and, in part, the checked
chart (evidence id, source hash, window; no metric or formula id). Table
exports, reports and download headers carry none. Q1 went to the owner as a
card through the coordinator.

### 2026-11-07 — S5a build (branch `claude/aa05-s5a-fact-identity`)

- The scoped evidence record stores `window` (start, end, snapshot count,
  `weight_basis` per requested metric) and `solve_stamp` {input, exact}.
  `assurance_records.public` strips both from evidence records only, so the
  evidence route and the checked export are byte-equal to before.
  `snapshots` is None for the investment and readiness profiles, whose index
  is years or a scorecard.
- `harness/fact_identity.WEIGHT_BASIS` declares the weighting of all 69
  metrics; a test holds its keys equal to `DEFINITIONS`.
- `test_p4_fact_identity.py`: all 21 profiles (`fact_profiles.DRIVERS`). The
  record, the evidence route, the receipt and the answer blocks agree on
  metric, formula_id, unit, entity_id, path, evidence_id, project_id, slots
  and the exact repr of the value (K3). Unavailable slots keep a reason on
  every surface (K2). Also tested: a legacy record, the strip on read, and
  the MAC domains.
- `test_p4_fact_oracles.py`: 16 oracles in `fact_oracles.py` (imports held to
  numpy, pandas and the standard library), non-unit weights that differ by
  column and by half (K4), adjacent window splits, and one wrong aggregate
  per family (K5).

### 2026-11-07 — S5a review: GK B (35) NOT OK @416a05b8 (F1), fixed; CI fix

- **F1:** a weighted metric declared `"none"` passed, because the K4 loop
  skipped every column for `"none"`. `test_p4_fact_oracles.holds_basis` now
  checks both directions for the 16 oracle metrics: a column basis gives the
  fact under that column and differs under the other two; `"none"` gives the
  same fact under every column. Killed: `dispatch_energy` → `"none"`,
  `optimal_capacity` → `"generators"`, `equity_npv` → `"generators"`
  (`test_a_wrong_weight_basis_is_caught` pins the first two).
- **CI:** `fact_identity.window` raised KeyError on a metric no profile defines
  (the pilot's `:unsupported` ask, `adequacy_certificate`), so 21 pilot cases
  and 3 harness-reliability tests failed. The window now leaves that metric
  out of `weight_basis`; its slot stays unavailable with its reason.
- **Uncompared inputs (GK B (35) ruling):** the readiness oracle
  (`fact_oracles.checked_pass`) takes each scorecard row's `project_value` and
  `limit` as given; neither is compared against the builder network.
  Follow-up: an oracle that computes `project_value` (and `limit`) for each
  row from the network.

### 2026-11-08 — S5 Q1 owner pick; S5b plan: GK B (35) GO WITH CHANGES (B1–B7, verbatim)

Owner card 2026-11-08 (~01:50 CET), S5 Q1: "Pin them as fact-free
(Recommended)", recorded as AA05-A46-SCOPE-1 (lands in owner-decisions.md with
#1268). S5 tests the field lists on the answer and the checked chart, adds the
metric identity (metric, formula_id, unit, disposition, warnings) to the chart,
and pins that table exports, reports and download headers never present a
value as checked.

GK B (35), as sent at about 01:10Z (resent 01:13Z):

> Q1: (a), metrics and warnings go inside `dataset`, so dataset_sha256 covers them and verify() re-derives them.
> Q2: not_drawn / "other_project" is right. render() runs under for_answer, which never fails the answer. assurance_context_changed is for the answer path when the active project changes mid-turn, and that path already raises it.
>
> B1. Unavailable identity comes from the evidence, never from the current code.
> - A slot does not store formula_id or unit (calculations.py:277 and :652). With the plan as written, verify() would read unavailable slots from today's `calculations.definition`. A later release that bumps a formula_id or unit would then make every stored chart with an unavailable slot fail verify (409), even though nothing about the evidence changed.
> - So: an unavailable metric carries {metric, disposition, reason}, with formula_id and unit None. That matches the receipt, which names only "metric: reason" for those.
> - Answered metrics take formula_id and unit from the record's fact.
> - verify() re-derives only from the unsealed record. Pin it with a test that monkeypatches `calculations.definition` (or DEFINITIONS) to a new formula_id after render, and shows verify() still returns "current".
>
> B2. Legacy branch.
> - Legacy applies only when BOTH `metrics` and `warnings` are absent from the stored dataset. One key without the other is a mismatch (False).
> - A new render always writes both, even when empty, with a test.
> - The legacy compare still checks series, window and the rest of the dataset.
> - Add a hand mutant "legacy taken when only one key is absent" next to "legacy always taken".
>
> B3. Warnings text.
> - One builder for "<metric>: <reason>" is shared by the receipt limitations and the chart. Do not restate the format.
> - A test compares chart warnings with the receipt's limitations for the same record.
> - Answered slots produce no warning.
>
> B4. The download-header pin is an exact allow-list per kind, not a word grep.
> - Charts legitimately send X-Checked-Chart ("evidence re-verified now") and X-Chart-Scope; reports send X-Report-Source (S4b). A grep for "evidence"/"checked" would hit those, and it would miss a neutral header name that carries a value.
> - Pin the exact header-name set for each kind (table, human report, assistant report_export, chart). Then pin that no header value contains a checked number from the record.
>
> B5. Download metadata includes the delivery zip.
> - P4 also pins that a delivery item for a table or report export carries no facts, metrics or checked value. Only `assurance`, dataset_sha256 and render_spec_sha256 are as today.
> - The report-export pin needs #1267 merged first.
>
> B6. K1.
> - render() compares record["project_id"] with the same id the manifest writes (str(project.id) from assistant_tools._project()). The check runs before _draw and before any file or sidecar write.
> - Test: no upload, no sidecar, and REASONS["other_project"] in plain words.
> - grep the frontend for consumers of chart reason codes (CheckedChart.tsx and its test): a new code must render its title, not a raw code.
>
> B7. Citation and survivors.
> - The PR cites the owner's S5 Q1 pick ("Pin them as fact-free", 2026-11-08) by its owner-decisions.md row. If the row has not landed yet, say so in the body.
> - mutants.py survivors are fixed or listed with a reason.

### 2026-11-08 — S5b build (branch `claude/aa05-s5b-chart-identity`)

- `checked_charts.dataset` carries `metrics` (one entry per evidence slot:
  metric, disposition, formula_id and unit from the record's fact, or None
  and the slot's reason when not answered; B1) and `warnings`
  (`calculations.slot_warnings`, shared with the receipt's limitations; B3).
  Both sit inside `dataset`, so `dataset_sha256` covers them.
- `verify` treats a dataset with neither key as drawn before S5b (B2); one key
  alone is a mismatch.
- `render` refuses `other_project` before drawing or writing (K1/B6). The
  frontend shows the reason's title, never the code.
- `test_p4_chart_identity.py`: identity against record, answer and receipt for
  all 21 profiles; drawn end to end for the four profiles with series; B1 on
  an all-answered chart and on one with unavailable slots; B2; changed
  identity; K1 (no draw, no upload, no sidecar; a moved manifest does not
  verify).
- `test_p4_fact_free_surfaces.py` (B4/B5): exact header names for checked
  chart, result chart and table downloads; no header value carries a checked
  number; a delivered table item has exactly today's keys and none that names
  a fact, metric or evidence. `test_p4_fact_free_reports.py` does the same for
  the human report and the assistant's sealed report_export (S4b, merged in
  from #1267's branch): base headers only, `X-Report-Source` only when a
  source is gone, no number in any header value, and exact delivery item
  keys (a human export carries no assurance at all).
- Mutants: `mutants.py` 17/17 killed (checked_charts 16, calculations 1;
  assurance.py's lines yield none). Hand mutants killed: legacy always taken,
  metrics dropped from verify, warnings from answered slots, K1 removed,
  reason dropped, unit dropped. "Legacy taken when only one key is absent" is
  equivalent: stripping both keys from the re-derived data still mismatches a
  manifest that holds one.

### 2026-11-09 — completion (AA-05 (10))

Issue 05 is done. Merged: S1 #1224, S2 #1227, S3 #1230, S4a #1234, S5a #1265,
S4b #1267, Y #1289 (64686f9d), and S5b #1292 (the last).

- S5 froze the field lists per AA05-A46-SCOPE-1: the answer and the checked
  chart carry a checked fact; table exports, reports and download headers are
  pinned fact-free (`test_p4_fact_free_surfaces.py`, `…_reports.py`).
- #1292's CI shards 1/4 and 4/4 failed on six header pins that listed `vary`
  as always present. The CORS middleware adds it only when an origin is
  configured, so CI had none. Fixed in b7abf519: the pins compare header names
  without `vary`; every other header stays pinned.
- Remaining (recorded, not blocking): the readiness oracle does not compute
  project_value/limit (S5a, GK B (35) ruling); SH-07c adds `revised_from` to
  sealed exports, which #1292's exact-key pin keeps green while it is only
  present when carried.
