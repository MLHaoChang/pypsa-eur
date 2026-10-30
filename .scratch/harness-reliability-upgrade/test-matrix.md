<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Qualification and cross-slice test matrix

This is a proposed test plan. No application tests or new behavior were executed while writing specs. Exact case IDs/fixtures in each ticket are normative; implementation reports must distinguish unit tests, parameter expansions, transport simulations, native engine fixtures, live probes and independent domain review. Do not sum overlapping counts as independent tasks.

## Profiles and coverage ledger

- `foundation-characterization/1`: HH-05A pins current guards/aliases/parent/provider/receipt behavior and identifies future limitations. It cannot qualify new feature implementation or the complete autonomous workflow.
- `foundation-implementation/1`: HH-01A, HH-02, HH-03A/B core lifecycle, HH-04, HH-09A authoritative acceptance store and the fixed HH-05 foundation acceptance cases. It is not dynamically filtered to installed slices. Unsupported initial operations remain explicit; flag-off compatibility is tested.
- `workflow-qualification/1`: **all initial-release HH-06/07/08/09B/10 implementation cases and integrated journeys**, plus HH-03B controller call coverage and the foundational safety cases. No missing required initial file, source adapter, native job outcome or billable-bound capability can be silently dropped from this denominator. Explicit roadmap positive extensions remain outside `/1`, with their unsupported-request prevention cases mandatory and exclusions reported; see HH-05’s fixed profile definitions. In particular HH-06:C12 tests native-loop refusal in `/1`, not an unimplemented positive native margin/coupling loop. A required unavailable capability makes the report incomplete, not passed.

Versioned manifest records each required case/configuration's source ticket, acceptance IDs, domain/adapter, actor/mode/provider, native fixture/checkpoint digest, oracle/checker version and expected outcome. One logical scenario can have multiple parameter expansions; report both without inflating independent sample claims. Diagnostic filtered runs are non-qualifying. Missing/duplicate/unexpected rows, altered manifest/oracle or wrong source commit fail qualification.

## Slice acceptance map

| Slice | Required detailed source | Key independent oracle | Regression families |
|---|---|---|---|
| HH-01 | HH01-A01..A19; B-only cases now consumed by HH-10 | Exact under-lock before/after native values/undo/revision and task/envelope receipts; deterministic barriers | Existing edit-card, runner/envelope, dirty state, undo, task/effect and restriction tests. |
| HH-02 | HH02-T01..T20 | Confirmed typed record/CAS plus current native candidate predicates; strict unsupported guard | Parent/checkpoint/rebind, confirmation/card, task resume, source units, frontend attachment tests. |
| HH-03 | All15original numbered groups plus HH-03B additions | Fake transport actual-send count; durable parent/project-day held/settled totals, injected crash points | Budget gate, stream retries/fallback/cache accounting, reviewer late receipt, direct report/stage/worker sends. |
| HH-04 | R01..R20 | Native owner/source/ref and exact unchanged public read payload; stale ref/candidate rejection | Reference ladder, edit preview, native run evidence and alias/dispatch/provider parity. |
| HH-05 | E01..E24, D01..D10, HH05-Q01..Q06 and expanded journeys for the exact fixed profile | Independent expected native states; planted broken manifests/oracles/fake success | Replay/pilot/history/provider suites remain unchanged and passing. |
| HH-06 | Every Cxx case and J06-A..D defined in issue06 | Exact isolated candidate attempt/job/input/result and frozen rule/objective readings | Existing study04/05 input/result gates, profile inheritance, no-progress, native engine-loop caps. |
| HH-07 | Every Rxx case and J07 journey defined in issue07 | Actually fetched canonical source/quote/version/unit and approved value receipt | Fences, research-only offered subset, source-ledger rejection, cost reservation, native value apply. |
| HH-08 | Every Axx case and integrated five-comment journey defined in issue08 | Exact base/new artifact hashes, semantic untouched parts, native claim/cell checks, shared acceptance rows | Report versions/roundtrip/jobs/number-audit, workbook exports/charts/formula escaping, authenticated downloads. |
| HH-09 | Every HH-09A store case and HH-09B coordinator case in issue09 | Child packet origin/scope/digest, shared budget totals, no child native effect; all intent rows retained | Session/task/eventlog, actor propagation, source/guard, workflow/card selection, UI Activity/acceptance. |
| HH-10 | All immutable-solve cases and real two-solve fixture in issue10 | Strict queue fence + native job/immutable input/read-back/evidence; source baseline unchanged | Native queue/store/snapshot/source saves, task lease/attempt, worker failure/cancel, isolated candidate ACL. |

Ticket-local prefixes are interpreted with the ticket ID (e.g. HH-04:R01 differs from HH-07:R01). Implementation must export and review the concrete unique manifest IDs before evaluator code/qualification, with no wildcard range that can silently expand/shrink after review.

## Required cross-slice assertions

| ID | Interleaving / journey | Required result |
|---|---|---|
| X01 | Requirement changes while HH-01 worker is between validation and mutation | One consistent requirement revision guards the actual effect; stale permit cannot mutate; no inverse-lock deadlock. |
| X02 | Native config changes during before/write/after capture | HH-01coherent identity and settlement exclude external changes, with valid unrelated-project concurrency. |
| X03 | Parent checkpoint races requirement activation and model usage settlement | No lost typed record, accounting state or generation; client cannot write server ledger/approval fields. |
| X04 | Accounting-only parent auto-created then ordinary chat attaches a requirement scope | No implied consent/old fact activation; unique canonical binding and quotas preserved. |
| X05 | Request intent amended with workers and solve underway | Old packets/admissions become non-authoritative for new intent; honest old native outcomes remain historical; no silent superseding required item. |
| X06 | Research value is offered inside a live execution envelope | Explicit user value card is still mandatory; source and unit check repeated at commit; false source never admitted. |
| X07 | Child proposes modifying demand/threshold to make result pass | Coordinator rejects outside tunables; original question and failed rule remain in history; no approval from majority/LLM confidence. |
| X08 | Correctness vs economic goal | Real HH-10 fixture760EUR→130EUR is physically balanced in both runs; frozen cost criterion improves, not a fabricated plausibility verdict. |
| X09 | Queue process crashes after submit but before caller receipt; browser closed | Exact durable fence finds same job or unresolved outcome; no duplicate physical solve; Activity recovers independently. |
| X10 | Live source project changes while isolated candidate solves | Candidate source identity remains exact; no baseline overwrite/current-result claim or automatic adoption. |
| X11 | Report generation indirectly calls an otherwise excluded provider | HH-03Baccounting required; no autonomous hard-capped task may send uncovered inference or presume fee zero. |
| X12 | Three report comments +two workbook fixes, one unsupported/unresolved | Four may pass their checks; parent cannot report all five fixed; exact unresolved item and next decision remain visible after restart. |
| X13 | User edits artifact concurrently with automatic revision | Exact base conflict; neither overwrite nor mix versions; old copies preserved; final pointer unchanged until proper closure. |
| X14 | Worker late result after cancellation/rebind; held spend unknown | Late usage settles original attempt at most once; packet cannot publish or mutate newer scope; no refund due to timeout. |
| X15 | Missing domain adapter or native evidence, assumed grid/reliability basis | Explicit unsupported/not-evaluated/screening disposition; no full engineering checked badge, compliance or convergence claim. |
| X16 | Same goal across provider transports and Guided/Expert | Same authority/source/outcome invariants; each actual offer set and each intended mode-specific confirmation policy honored. |
| X17 | Policy preauthorizes deterministic artifact formatting only | Allowed copy fixes need no repeated decision; factual/parameter/formula/publication changes require their separate policy/card; no general authorization bypass. |
| X18 | Rollback/flag disable with enforced requirements, unknown spend and partial native effects | Records retained; protected work stops rather than falls back; no replay/free cap or newly created final artifact. |
| X19 | Parent near total byte cap while acceptance/outbox/requirements grow and a stopped worker’s receipt arrives late | All writers preserve HH-03 terminal-growth reserve and exact `_LOCK`→directory order; growth refuses before replace and exact late settlement remains possible once without unrelated acceptance growth or a new send. |

These assertions may be implemented by linking/parameterizing existing detailed cases; the evaluator records their mapping, not an invented additional independent-user denominator.

## Deterministic end-to-end controls

1.Positive study: a configured supported case completes native input checks/solve/result criteria, preserving source and every acceptance item; bounded correction can improve a frozen design goal without changing demand/thresholds.
2.Negative study: infeasible/unconfigured/out-of-budget/no-progress case preserves all failed criteria, versions, corrections and resources and ends needs-user/incomplete; cannot be forced to pass.
3.Research-to-model: authentic two-source proposal, conflicting range, wrong unit and injected instruction; only a verified explicit user-approved value commits.
4.Artifact revision: report/workbook copies pass exact supported acceptance checks; deny, concurrent edit, formula injection and one subjective/unresolved feedback item cannot produce final-complete.
5.Delegation: two workers return current/conflicting/stale/malformed/late packets; coordinator remains sole effect authority and shares every call's parent budget; no majority pass or unbounded spawn.
6.Full journey: source template and selected modules →intent/plan→parallel research/synthesis→confirmed candidate→native result/correction history→report/workbook comments→decision packet with all rows. Browser closure/another project/worker restart must not reset source, quotas, review needs or outstanding items.

## Commands and remote evidence

Per-slice tickets name proposed focused test files and existing regressions. Run from pypsa-gui/backend with correct repository PYTHONPATH and installed test environment; use one BLAS/OpenMP thread for heavy native solves and no more than two heavy local suites simultaneously. Code changes to shared dispatch/catalogue need repository semantic/route/control/inventory checks; tool changes also need actual ChatPanel tool-list tests. GUI changes need meaningful focused Vitest tests, tsc and required frontend checks. Preserve existing tests/skips; baseline failures require reproduced evidence, never a widened assertion.

Exact-head CI must execute all four backend shard test steps, native pilot/QA and new trajectory qualification steps with aggregate dependencies, not just path-filtered green jobs. Inspect all applicable CodeQL/inventory/control/release checks and official review verdicts; normal coordinator premerge runs on fresh master. Docs-only checks of this package do not certify runtime tests.

No live paid model/media/source-provider call is needed for initial deterministic qualification. Later real-model A/B evaluations require explicit budget, frozen corpus/decoding/tools/model version, holdout/repeats and complete usage. Representative-use calibration and independent domain reviewers remain pending until separately commissioned; paper statistics and synthetic zero errors do not replace them.
