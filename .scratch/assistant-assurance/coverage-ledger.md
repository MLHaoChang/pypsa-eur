<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Answer-assurance acceptance coverage ledger

The current continuation adds a [pilot evaluation catalogue and report gate](pilot-evaluation-runbook.md):
53 required cases, every default metric in the five bounded profiles, independent
230/300/51 EUR oracles and six model-draft defects plus stale/missing/unsupported
data and cost-basis clarification. Optional live generation covers only five
positive golden cases and remains a separate opt-in. Full P7 coverage,
qualitative/population calibration and independent domain review remain open.

Status: bounded shared-harness contracts implemented for scoped generator/network
and storage calculations, retained runs/comparisons, checked publication/artifacts, confirmed
static-input effects, mounted-view acknowledgements and live conversation carryover. See
[implementation boundary](implementation-status.md). Initial results below are
historical. Added request-slot, validity-mask and formula groups have partial
coverage through `generator-dispatch/1`, `generator-cost/1` and
`network-linear-cost/1` and `storage-unit-dispatch/1`; they remain pending independent review and full domain
mapping. Unmapped effects, complete arbitrary requests, independent
reviewer/browser/calibration and release activation remain open. The documentation
assessment restored documentation only; subsequent implementation restored and
reran the archived tests.
Source baseline: local origin/master `d43809ae` (dictation PR 106 already merged).
Reviewed transferred specification commits: `55ce946b`, `34dbb3f3`; publication
was blocked by GitHub HTTP 401 in that session. Current access is recorded in the
[handoff](session-handoff.md). No paid APIs called. Default skips are not coverage.

The independent reviewer approved design publication and this first acceptance
tranche after assertion-strength, public-contract and canonical-hash corrections.
That verdict is not runtime, full phase, scientific correctness or release approval.
The comprehensive [matrix](test-plan.md) remains the requirement source.

## Executable tranche by phase

Backend files live in `pypsa-gui/backend/tests/assurance_acceptance/`. Frontend
file: `pypsa-gui/frontend/src/components/ChatPanel.assuranceAcceptance.test.tsx`.
All model responses are scripted or HTTP-mocked; authenticated API, turn loop,
real tool dispatch, isolated project handlers, chart rendering and downloads run.
Frontend tests mount the real ChatPanel/store and inject stream callbacks.

| Phase | Backend cases | Initial result | Executable behavior |
| --- | ---: | --- | --- |
| P0 contract | 9 | 9 expected failures | Invalid admission/version/mode; strict session metadata; required public routes |
| P1 evidence | 5 | 3 expected failures, 2 controls pass | Real input fact/evidence scope and source/unit; cross-org access; anonymous access |
| P2 publication | 13 | 13 expected failures | Fragmented first/intermediate/final prose and thinking; value/sign/unit/entity mismatch; contradictory narrative; human rendering; bounded malformed-draft fallback; approved answer/receipt retrieval |
| P3 outcomes | 3 | 3 expected failures | Read receipts; fabricated completion; navigation submitted before ACK |
| P4 artifacts | 4 | 3 expected failures, 1 control passes | Exact known chart data/units and canonical hashes; delivered bytes; delivery chain; legacy sidecar fault injection; unsolved output refusal |
| P5 navigation | 5 | 4 expected failures, 1 control passes | Unknown/mismatched action/turn/target/generation refusal; CSRF |
| P6 history | 4 | 4 expected failures | Exact nonempty approved text and receipt in replay/export; repeated fetch never regenerates |
| P7 providers | 4 | 4 expected failures | Actual Anthropic/OpenAI/compatible/local HTTP adapter requests require the same limited nonempty approved fallback; bounded repair |
| UI P2/P6 | 4 | 3 expected failures, 1 availability control passes | No unapproved text/thinking/TTS; usable limited voice answer |
| UI P3 | 6 | 6 expected failures | Submitted/running/partial/failed/uncertain/cancel_requested must not show completed checkmark |
| UI P4/P5 | 2 | 2 expected failures | No unapproved artifact link; actual software checking stage visible |

Total: **47 backend cases: 43 expected failures, 4 existing controls pass**.
**12 frontend cases: 11 expected failures, 1 availability control passes**.
Missing new endpoints/frames, ignored strict options, unchecked publication and
unbound metadata explain the failures. There are no collection/setup errors.
Controls do not prove the new gate: anonymous refusals and CSRF happen in existing
middleware; unsolved chart refusal already exists; the voice availability control
can pass because current UI ignores the new approval frame.

Chart hash checks use the documented canonical encoding and an independent known
G1/p_nom = 20 MW dataset. The legacy-storage corruption case deliberately modifies
the current sidecar and observes `build_delivery`; that path is NOT a future public
contract. Replace its corruption fixture when storage changes. It currently proves
metadata tampering is accepted, while existing byte checks remain separate controls.
An aggregated TestClient response proves frame ordering and fetched identity,
not commit-before-first-byte under a crash/concurrent-revocation race.

## Historical passing regression checks

Relevant backend selection: **321 passed, 48 skipped** (47 opt-in acceptance plus
one deliberate golden-record generation helper), no failures/errors. Full default
frontend: **3,529 passed, 12 skipped** (the proposed acceptance suite), no failures.
Production TypeScript/Vite build passed. Ruff lint/format passed, documentation
links/whitespace passed, and the inventory matched 220 tools/14 tabs/17 panels.
Commands and limitations are in the [results assessment](../../docs/superpowers/assessments/2026-10-10-assistant-answer-assurance-tests.md).
These controls preserve existing behavior; skipped proposed tests remain unverified.

## Comprehensive requirement mapping and remaining work

The table describes the transferred tranche's requirement mapping. The current
implementation additions below extend specific rows; no complete phase verdict
is implied by the table or current passing counts.

A partial row means useful executable cases exist, with explicitly named pending
requirements. No row is a complete phase/release gate. References below are file
stems from the directories above; “UI” means the ChatPanel acceptance file.

| Matrix group | Existing executable coverage | Still required |
| --- | --- | --- |
| A0.1 | Partial: p0 admission; p2 malformed draft | Duplicate IDs, exact size/count boundaries, extra trust fields, complete block schemas |
| A0.2 | Existing inventory CLI matches 220 tools/14 tabs/17 panels | Adapter/family/output-channel behavior mapping; unknown-adapter refusal and stale inventory negative fixture |
| A0.3 | None | Explicit legacy/unvalidated state; disabled/shadow/pilot semantics and old clients |
| A1.1 | Partial: p1 project/entity/unit, cross-org; p2 entity mismatch | Run/section/metric/window identity, forged pointers and traversal, same-org ACL denial |
| A1.2 | Partial: p1 anonymous; p5 missing CSRF | Post-collection/review/cache revocation, disabled identity, publish/read races, invalid CSRF and cross-owner ACK |
| A1.3 | None | Real immutable historical run fixture, failed/newer run choice, current input invalidation |
| A1.4 | Partial: p4 legacy provenance corruption | Evidence retention/loss/corruption/restart; unsaved inputs, source/policy/cache versions |
| A1.5 | None | Capture-before-truncation/omission, complete aggregates vs page extrema |
| A1.6 | Existing unsolved chart refusal control only | Real partial/infeasible/nonconverged/missing/low-fidelity adapters and null semantics |
| A2.1 | Partial: p2 unsafe fragments; p6 replay/export; UI text/thinking/TTS/link | Caption/tool-summary scope, summary/compaction, cold reload and actual speech transport |
| A2.2 | Partial: p1/p2 L1/p_set input fact | All result units/run/window/metric attribution, same numeric value in different sources |
| A2.3 | Partial: p2 negative and MW/MWh mismatch | Locale, kW/MW conversion, percentages/denominators, rounding tolerance |
| A2.4 | None | Freeze additive computed-block schema, independent derived fixtures and zero denominator |
| A2.5 | Partial: p2 valid ledger plus contradictory prose | Caption/omitted claims/full-table/causal assertions and approved interpretation semantics |
| A2.6 | Partial: p2 malformed draft and max-one repair | Truncation, provider/checker errors, exhausted budgets, unavailable strict mode and timeouts |
| A2.7 | Partial: p7 real no-tool HTTP adapters; p2 retrieval call count | Tool prohibition during repair/review, fragmented tool IDs/pairing, stale callbacks/abort |
| A3.1 | Partial: p3 read/forged action/submitted; UI six noncompleted outcomes | Real queue receipts and successful-return failed/partial postconditions |
| A3.2 | Existing provider/confirmation/task regression controls only | Assurance-specific denial/expiry/locks/checkpoint/ambiguous timeout and no replay |
| A3.3 | UI cancel_requested display | Real selected-project/run cancellation and terminal job identity |
| A3.4 | None | Real write/undo/import/export partial effects and typed postcondition comparisons |
| A4.1 | Partial: p4 known static input chart | Time series, historical/current solved-result scope and all chart types |
| A4.2 | Partial: p4 legacy sidecar corruption and byte hashes; UI gated link | Actual manifest/data/render/bytes tampering, axes/captions, mixed runs and expired/unapproved downloads |
| A4.3 | Existing finite-data regression controls only | Assurance aggregation/window/filter/clipping/missing cases and render races |
| A4.4 | Existing report/numeric-audit regression controls only | Assurance-gated report narration, regeneration and disclosure-preserving exports |
| A4.5 | Partial: p4 delivery chart chain | Import assertions/mapping, mixed uploads, binary/table complete chain |
| A5.1 | Existing navigation schema inventory controls only | Real browser state journeys for every mapped view in Expert/Guided and exact historical sources |
| A5.2 | Partial: p3 submitted; p5 unexpected/mismatched ACK | Issue real expected action and matching success ACK; stale issued actions, owner/project/run/revision, closed tab/new navigation |
| A5.3 | UI unapproved artifact link refusal only | Evidence-card source/time/unit/limitation/download UX, external links/XSS/injected markdown |
| A5.4 | UI actual checking stage; p2 no thinking | Complete real stage lifecycle and retry/failure trail; no fake steps/percentages |
| A5.5 | None | Keyboard, screen-reader, mobile, status deduplication and focus behavior |
| A6.1 | None | Freeze read-only reviewer request/response seam; pass/reject/malformed/disagreement/timeout |
| A6.2 | None | Reviewer least privilege, hostile annotations, redaction and shared budget limits |
| A6.3 | Partial: p6 replay/export IDs/text; UI TTS | Summary/cold reload, private provider-pairing trace, retention and compaction |
| A6.4 | Partial: repeated owned-answer fetch no model call | Real restart/resume/provider switch with approved refs and ACL recheck; no effect replay |
| A6.5 | Frame ordering/record fetch only | Commit/emit fault injection, interrupted SSE GET recovery, dedup, incomplete writes and migration |
| A7.1 | Partial: p7 four HTTP-mocked wires; existing workflow provider parity controls | New Responses adapter case; fragmented tool-calling assurance and provider-switch parity |
| A7.2 | None for proposed assurance | Real service journey per family and create/configure/solve/compare/chart/export/reload |
| A7.3 | None | Prolonged mixed-domain/history/error/job/navigation conversations |
| A7.4 | None | Cold/warm/slow/reviewer-down/load measurements, queue/storage limits and cache behavior |
| A7.5 | None | Expert-adjudicated qualitative benchmark, false accepts/rejects, unresolved cases |
| A7.6 | None | Full rollout/rollback/old-client gates after every required mapped family passes |

All family rows in test-plan.md remain pending assurance-specific real fixtures.
Passing existing harness regressions preserves the baseline; it does not convert
those families into checked-answer coverage. Shared source inventory is discovery
coverage, not complete tool/UI or engineering validation.

## Original implementation continuation

Before implementation, review/freeze the [reliability contract](reliability-contract.md)
and map its added case groups to fixtures. Original independent review and RED
counts apply only to the transferred tranche. New request-slot, numerical-validity
and mandatory-guard requirements cannot inherit those approvals/counts.

1. Once GitHub authentication works, publish reviewed specification to master.
   If these test files already exist locally, keep them isolated and then publish
   their own PR on the resulting master; avoid duplicate docs under squash merge.
2. Implement one vertical tracer first: reject unsupported strict negotiation;
   then buffer unsafe fragmented provider prose and publish an owned limited
   software fallback. Make its focused RED case GREEN and run relevant regressions.
3. Add positive evidence-backed L1 input publication and authorization; then
   progressively implement P1–P7 tickets and their pending fixtures above.
4. Keep acceptance opt-in until implemented; never make tests pass by weakening
   assertions, silently bypassing strict mode, xfail, or treating skips as passes.
5. Do not claim user confidence guarantees or release readiness until all required
   phase/family/race/domain/browser/calibration gates have evidence.

## Historical static-publication implementation results

Branch: `feat/assistant-assurance-publication`, documentation base `f533feda`.
Supported scope and open release requirements are in
[implementation status](implementation-status.md). No new independent review.

| Selection | Result | Boundary |
| --- | --- | --- |
| Original 47 backend acceptance cases, strict opt-in | 44 passed; 3 artifact cases remain RED | P4 manifests/provenance/delivery chain unimplemented; other passes do not complete whole phases |
| Final default backend assurance selection | **68 passed, 3 skipped** | 44 implemented transferred cases plus 24 new cases; only the three P4 cases skip |
| Broad existing chat/harness/report selection plus initial 19 new cases | **1,851 passed, 2 skipped** | Deliberate golden-record helper and empty known-broken-import sentinel skip; ran before final strict-only numeric rendering/extra fixtures |
| Full frontend before final stale-stream guard | **3,543 passed / 299 files** | Includes the first 14 UI boundary cases |
| Final affected ChatPanel/AssistantResultCard selection | **574 passed / 23 files** | Includes all 17 assurance UI cases; preserves legitimate ordinary-chat project rebinds |

Production TypeScript/Vite build, new Python lint/format, inventory and local
documentation links pass. No paid model calls. Existing framework/numerical
warnings remain; no setup/collection errors. The final build is recorded in the
handoff. Do not count skipped or expected RED artifact cases as coverage.

New default backend cases add disabled-server/script/null admission boundaries,
handler-value disagreement, raw nonfinite values, full float precision,
missing/changed/corrupt evidence across fetch/history/export, storage failure,
required-guard exceptions, blocked effects, publication-time source changes,
identity disablement, quota reclamation, strict history seed revalidation and
schema/trust-field spoof rejection. UI adds exact digest mismatch, wrong/missing
turn identity, aborted callbacks and user project switches. These extend A0.1,
A0.3, A1.2/A1.4, A2.1/A2.2/A2.6/A2.7 and A6.3/A6.5 in the narrow pilot.
They do not establish proposed A1.7 result validity masks or any full added group.

Default command from `pypsa-gui/backend`:

```bash
PYPSA_GUI_ANSWER_ASSURANCE_MODE=disabled python -m pytest -o addopts='' \
  tests/test_answer_assurance.py tests/assurance_acceptance -q --tb=short
```

The explicit disabled server tests prove admission refusal; remaining implemented
tests set strict within their fixtures. The pending artifact RED cases remain
available with `PYPSA_GUI_TEST_ASSURANCE_ACCEPTANCE=1`.

## Numerical and chart-integrity continuation

Branch: `feat/assistant-assurance-calculations`, based on `eaeae8ad`.
No new independent/domain review or paid model calls. Default mode stays disabled.
The original three P4 failures now pass and no longer skip. New chart tests reject
altered data, render parameters, units, absent sidecars and changed bytes; the
same validated provenance is copied into bundles. Inputs changed during rendering
refuse publication. This covers bounded data/render/byte integrity, not engineering
certification or arbitrary artifact/report/import correctness.

`tests/test_assurance_metrics.py` exercises an independently specified real LP:
17 MWh, 170 EUR marginal expenditure and 7 MW peak; separate objective weights
produce 120 EUR with unchanged 17 MWh. It checks nonfinite/out-of-bounds dispatch,
metric-specific masks, invalid weights, overflow, missing selection, wrong entity,
unsupported/misaligned axes, selected UI context, stale solve/input/output provenance,
provider fabrication/omission and public replay invalidation. Coverage is partial
for A0.4, A1.7, A2.8–A2.10 and A7.7, limited to the explicit three-slot profile.
The proposed 230 EUR same-basis total-cost example remains unimplemented.

Validation uses the repository's locked Pixi `test` environment. Initial full
backend run: **11,028 passed, 4 failed, 269 skipped**. All four failures were
corrected without weakening assertions: classify nine emitted error kinds in the
shared manifest and add arguments for both existing mutating tools to the sweep
refusal matrix. Targeted rerun: **37 passed / 28 deselected**. Every formerly
failing backend case passes. Skips in the full run are existing optional/platform
coverage and are not counted as assurance coverage. A fresh complete backend result is required before merge; the final local
result and GitHub Actions limitation are recorded below.

Full frontend: **3,546 passed / 299 files**. After adding manifest-required banner
routing/copy, affected ChatPanel selection: **582 passed / 22 files**. Production
TypeScript/Vite build passes. Upstream unit suite: **36 passed**. Final assurance suite: **106 passed**, with no P4 skips. All **31 QA drivers
passed** after binding the authenticated identity in the phase-0 in-process
export journey. Exact commands and limitations are recorded in the handoff. Inventory:
**220 tools / 14 tabs / 17 panels**. New Python modules and fixtures pass Ruff;
local documentation links and `git diff --check` pass.

Final full rerun on the implementation source: **11,037 passed / 269 existing
skips**, no failures. Final full frontend after banner/manifest changes:
**3,559 passed / 299 files**. All **31 QA drivers** and **36 upstream unit tests**
pass. The **106-case assurance selection** has no artifact skips. GitHub reports
Actions disabled for this repository and refused dispatch with HTTP 422; normal
merge rules apply and the complete locked-environment local run is the validation
gate. These results do not complete pending domain/reviewer/release requirements.

## 2026-10-11 — shared harness reliability contracts

Current branch `feat/harness-reliability-contracts`; default deployment unchanged.
The explicit-profile implementation now covers required scopes/slots/windows,
retained immutable runs and same-basis comparisons, server-owned cost formulas
and physical masks, durable confirmed static-input updates/reconciliation,
issued mounted-view ACKs, optional bounded read-only qualitative review,
authenticated checked-answer JSON/ZIP parity and profile/effect/shadow controls.
These are bounded slices of A0.4, A1.7, A2.8–A2.10, A3, A4, A5, A6 and A7;
all-domain/release tickets remain open. Earlier statements that 230 EUR total,
positive ACKs, retained runs or every effect/reviewer are unimplemented describe
the preceding pilot and are superseded for the documented narrow contracts.

New independent/adversarial regressions use real HiGHS fixtures, fake providers
and five real adapters with HTTP mocks: 230 EUR vs fabricated 999; exact-window
7 MWh; same-basis 260-to-230 signed delta; missing/unsupported quantities;
stale/corrupt retained/current evidence; confirmed exact effects, wrong success
JSON/unintended mutation and late workers without repeat; issued visible versus
stale/forged navigation; review tooling/schema/usage/timeout and quantitative
prose refusal; cross-process quota/atomic files; JSON/ZIP value/byte parity;
profile/effect rollback and unchanged ordinary shadow output. No paid APIs.

Final focused selection: **197 passed**, no assurance skips. Full frontend:
**3,567 passed / 301 files**; production TypeScript/Vite build passed. Upstream
unit tests: **36 passed**. Ruff, inventory and diff checks pass. Commands are in
[handoff](session-handoff.md). Final full backend and 31-driver results will be
recorded before PR/merge. No independent expert approval, measured production
false-acceptance rate or distributed project-runtime test is claimed.

Integrated master `b8bc079f` and preserved both companion and reliability view
markers. Supplementary passive-branch oracle: **3 passed**, independently checking
80 EUR/year / 250 EUR total, branch overload and in-capacity imbalance. Production
build and integrated upstream **36 tests** pass. The frontend's initial integrated
run had **3,604 passed / 1 five-second DOM timeout** under concurrent load; the
unchanged affected rerun passes **98 tests**, and a fresh full run is required.
Ruff passes new modules/records/tests; loop/session match the same seven findings
already present on master and routers add none. GitHub dispatch recheck returns
422 Actions disabled. Final full results follow before merge.

Final integrated frontend rerun: **3,605 passed / 307 files**, unchanged timeout
and assertions, 501.87 seconds. The initial concurrent timeout is resolved. Full
backend and fresh integrated QA remain running.

Final focused backend selection on the integrated source: **200 passed**
(140.27 seconds), including all passive-branch cases and explicit static-setting/
time-series effect disclosure. Final receipt-parser tightening refuses array-shaped
verdict/review-status enums; its focused UI selection passes **26 tests**. New
module/fixture Ruff checks pass. A fresh final full frontend/build follows this
last parser change; no broad quantitative-prose or reviewer-proof claim is made.

A new networkless-study navigation case initially failed because ACK issuance
called the PyPSA fingerprint helper with no network. The shared navigation guard
now keeps the UI action submitted without inventing a revision or aborting
navigation. Final navigation selection: **9 passed / 52 deselected**, 7.83 seconds,
including the new case and all stale/forged ACK cases. This supplements the
**200-case** focused gate with one new acceptance case. Fresh integrated QA:
**all 31 drivers passed**; final TypeScript/Vite build passed. Full backend and
final frontend remain in progress.


### Final harness regression gate

Shadow preparation is now failure-isolated: a malformed declared price year
previously interrupted ordinary chat before provider output in shadow mode.
The new authenticated stream regression first failed, then passed after the
shared loop catch; shadow failure cannot create an approval or alter ordinary
text. Final focused selection includes `tests/test_chat_stream_attempt_seam.py`
in addition to the command above: **213 passed / 65 warnings**, 93.70 seconds.
It covers the final loop, static-write wording, networkless navigation and all
new cases added after full backend collection; these overlapping selections
must not be summed as independent test counts.

Final frontend: **3,606 passed / 307 files**, 501.95 seconds. Final production
TypeScript/Vite build passed (existing chunk-size warning). Integrated QA:
**all 31 drivers passed**. Upstream units: **36 passed**. No frontend changes
followed those final gates. Ruff passes new modules/records/tests; modified
legacy loop/session retain baseline findings only. Inventory and diff checks
pass. Full backend completion is recorded separately below.


### Backend completion and corrected failures

Full integrated backend run completed: **11,103 passed / 269 skipped / 5 failed**,
8856 warnings, 4186.05 seconds. Four failures were stale prompt hashes/one stance
paragraph from companion PR 113: all four reproduce on an untouched detached
master `b8bc079f` worktree (**4 failed**, 1.24 seconds). Production prompt text
is unchanged by this branch. Only the two stance hashes and the tools fixture's
stance paragraph are re-pinned from that independently loaded master text;
all byte-equality assertions remain. The fifth failure caught the new checked
export interpolating a filename header; it now uses the existing
`services.http_filenames.content_disposition()` helper.

Fresh correction gate on final source: **348 passed / 65 warnings**, no skips,
99.99 seconds. It runs every affected file and the entire final harness selection:

```sh
# from pypsa-gui/backend, with the cache variables described above
../../.pixi/envs/test/bin/python -m pytest tests/test_chat_profile_binding.py tests/test_guided_mode_prompt.py tests/test_harness_prompts.py tests/test_download_headers.py tests/test_assurance_metrics.py tests/test_answer_assurance.py tests/test_harness_reliability.py tests/test_reliability_transport.py tests/test_assurance_storage.py tests/assurance_acceptance tests/test_harness_layout.py tests/test_chat_stream_attempt_seam.py -q -o addopts='' --tb=short
```

No failures remain in the correction gate. The full run predates the final
localized shadow/navigation/header corrections and added cases; this fresh gate
covers them. This is a full regression run followed by a successful correction
gate, **not a second clean full backend run**. Overlapping counts are not summed.
Final frontend/build/31 QA drivers/36 upstream tests remain passed as above.
All 19 assurance documents / 128 local links and inventory/diff checks pass.
Ruff passes new modules/records/tests/router; legacy loop/session add no findings.
GitHub master was rechecked at `b8bc079f`; dispatch still returns 422 Actions
repository-wide disabled. Default deployment and open domain/release limitations
remain unchanged.


## 2026-10-11 — live conversational scope continuation

Branch `feat/harness-conversation-scope`, based on merged PR 119 (`9b987371`).
The user authorized continuing the next implementation slice and retained the
instruction to create/merge a PR after validation. Attached design review notes
are context, not new instructions requiring another approval.

Added `harness.assurance_conversation` and private bounded session state. Only
published checked results seed exact selectors and two distinct immutable raw
run references. Repeats retain entity/window/metrics/basis/current-vs-retained
selection; snapshot growth cannot silently expand the original window. Cost
comparisons choose the previous distinct compatible checked source, not a prior
repeated answer. Software cost-quantity/basis questions call no provider, keep
ordinary budget/auth/history/cancellation gates and assign needs-clarification
slots with no checked facts. The existing choice frame is admitted only for
that exact software-generated question. Frontend rejects contradictory receipts,
labels clarification and hides checked-value downloads. No SDK, new frame name,
effect grant or deployment activation was added.

Initial public-stream tracers: **14 failed**, then **14 passed**. Expanded final
scope cases: **30 passed** / 34 warnings, 27.89 seconds. Initial broad gate exposed
one network-cost basis omission (fixed in the resolver) and two test isolation/
old-behavior assumptions: the corruption fixture now restores its own bytes, the
new shadow fixture uses isolated app data, and the prior missing-basis case now
asserts its stronger typed clarification/no-provider-call contract. Assertions
for checked values, false completion, provider neutrality and byte equality were
not weakened. Final focused gate: **274 passed / 1 existing deliberate frame-
recording skip**, 96 warnings, 119.58 seconds. It includes scope, calculation,
publication, storage, acceptance, real adapter HTTP mocks, layout, stream attempt,
rewind, history-owner, live-history race, budget and frame-contract files.
Final UI selection: **27 passed / 3 files**; TypeScript/Vite build passed (existing
chunk-size warning). New module/assurance/tests pass Ruff; legacy loop/session/
history add no findings. Inventory matches **220 tools / 14 tabs / 17 panels**;
**19 docs / 129 local links** and diff checks pass.

The final source is frozen for broad checks. Full backend collection reports
**11,412 cases / 527 files**. To use the managed environment's two CPU cores
without installing a test plugin, all collected files are partitioned into two
disjoint 5,706-case subprocess selections; their union equals the collection.
Each pytest process has its own conftest-created app-data directory/DB, runs from
backend cwd, and uses the locked `.pixi/envs/test/bin/python`. Threads are bounded
with OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=1. The ordinary `-q -o
addopts='' --tb=short` flags are used. Counts/exit codes will be recorded when both
shards finish; pending runs are not treated as passed. Full frontend and 31 QA
drivers are also running. No paid model calls are used.

Boundaries: live-session state only; restart/new chat requires explicit scope or
owned retained IDs. Arbitrary language and durable constraint recovery, additional
domains, independent expert/calibration/browser journeys and release activation
remain pending. Existing storage TTL/byte/quota limits apply to private snapshots;
active project runtime remains one application worker. Issue 09 is still open.


### Broad-validation checkpoint: conversation continuation

The 31 QA drivers (`/tmp/conversation-qa.log`) and all 36 upstream unit tests
(`/tmp/conversation-upstream.log`, 30.90 seconds) passed. The first full frontend
run passed 3,609 assertions across 307 files but exited unsuccessfully because an
uncaught tagging-tour polling timer accessed the global document after teardown;
that run is not a passing gate. A regression reproduced the teardown failure
before the fix. Capturing the original document resolves it; the final targeted
selection passed 41 tests across three files (11.70 seconds). The production
TypeScript/Vite build passed on the fixed source (19.19 seconds, existing chunk
warning). The fresh complete frontend run and both complete backend selections
are pending; no pending result is treated as passed. Source is `a7b0e3ee` (backend
implementation `24023b81`); only documentation is being updated during those runs.

A fresh `gh workflow run assistant-inventory.yaml --ref master` returned HTTP 422:
Actions has been disabled for this repository. Remote CI is unavailable; this is
not a failed remote test run or an approval-rule bypass.


The fresh full frontend rerun on the fixed source finished cleanly: **3,610 passed
across 307 files**, exit 0, no uncaught errors (729.68 seconds). Command:
`cd pypsa-gui/frontend && npm test -- --run`; log:
`/tmp/conversation-frontend-final.log`. Final production build also exited 0.
The complete backend selections remain pending at this checkpoint.


### Final conversation-continuation validation (2026-10-11)

All required local gates passed on frozen source `a7b0e3ee`; backend source is
unchanged from `24023b81`. The fresh complete backend run is **11,143 passed /
269 skipped / zero failures**, covering all **11,412 collected tests in 527
files**. Both isolated subprocess selections exited 0:

| Selection | Passed | Skipped | Warnings | Duration |
| --- | ---: | ---: | ---: | --- |
| Backend shard 1 | 5,694 | 12 | 3,020 | 2,573.09 s |
| Backend shard 2 | 5,449 | 257 | 5,885 | 2,858.17 s |

Total orchestration wall time: 2,874.81 seconds. Warnings are recorded, not counted
as passes. Skipped cases are not claimed as coverage. Collection uses
`python -m pytest tests --collect-only -q -o addopts=''` from backend cwd. Whole
files are assigned greedily in descending collected-case count (filename tie
break), choosing the lower-count shard (first shard on ties). Both contain 5,706
cases; their file sets are disjoint and their union equals the collection. Each
subprocess runs the locked `.pixi/envs/test/bin/python -m pytest <selected files>
-q -o addopts='' --tb=short` with independent conftest app data and
OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=1. To reproduce the complete
selection serially, use the same interpreter/flags with `tests` as the selection.

Final evidence: `/tmp/conversation-backend-collection.log`,
`/tmp/conversation-backend-shard-1.log`, `/tmp/conversation-backend-shard-2.log`,
`/tmp/conversation-backend-manifest.json`, `/tmp/conversation-validation-result.json`.
The fresh complete frontend is **3,610 passed / 307 files**, exit 0, no uncaught
errors. Final production build, **31 QA drivers**, **36 upstream unit tests**,
**274-case focused gate** (one deliberate recording skip), **30 conversation
cases**, **41 tour regression cases**, inventory and documentation checks pass.
The focused selections overlap the full suites and are not added to their totals.

This clean full run supersedes the earlier PR 119 full-run-plus-correction result
as current validation. No paid model calls or remote CI success is claimed:
GitHub workflow dispatch confirms Actions is disabled (HTTP 422). Context remains
live-session, bounded and opt-in; durable recovery, arbitrary-language scope,
unmapped domains and independent domain/release certification remain pending.


## 2026-10-11 — bounded checked conversation recovery

The continuation integrates master `c6b9128f` / PR 121 with the checked scope
from PR 120. Earlier complete-suite counts above are historical. The editing
agent reviewed this change; no independent domain certification is claimed.

New `tests/test_conversation_recovery.py` has 55 cases. Public authenticated
history and stream tests use real HiGHS fixtures and fake providers, asserting
7 MWh exact-window dispatch, 230 EUR declared-basis cost and the distinct
260 EUR comparison baseline (−30 EUR / approximately −11.54%). Cases cover
pending quantity/basis clarification, all three released profiles, retained
historical sources after input edits, one new generation after recovery,
corrupt/expired/unknown-version/overlarge/duplicate/mismatched checkpoints,
retained-source recalculation and adapter versions, imported/copied/gapped/
older/peer history, rewind, abort, publication and storage failures, inactive
modes, private-store exposure and history-versus-stream interleavings.

Fourteen cases exercise parent resume/rebind, absent runtime episodes, revocation
write failures and busy/foreign actor/project boundaries. New episodes start
without checked scope. Parent refs/goals and user-supplied checkpoints grant no
assurance authority. These seams do not certify distributed resume, arbitrary
language or general task/effect/billing memory.

Before implementation, the initial recovery selection failed five positive
cases (six refusal cases already passed). It then passed all 11. After merging
PR 118, four new parent-control cases failed before the integration and passed
afterward. Intermediate selections overlap the complete suite; no skipped test
or pending process is counted as a pass.

The integrated harness/history/parent/admission/progress gate passes **474 cases
with one recording skip** (151 warnings, 366.90 seconds). All four error-registry
checks pass after adding the new persistence refusal and the inherited worker
refusal. The inherited Live test fixture now uses the typed `null` no-mismatch
state; its four behavioral assertions pass. No assertion was relaxed. Earlier
complete runs were stopped before completion for these corrections; their
partial results are not counted.

The earlier frontend run passed **3,623 tests / 308 files** before PR 121 and is
historical. The updated-master focused UI gate passes **21 tests / four files**;
the production build passes after correcting the same typed `null` no-mismatch
state in the new detached-progress fixture. The first full updated-master UI run
had **3,626 passes / one failure**: the existing 1,200-row select-all test exceeded
its 5-second timeout under concurrent backend load, without reporting an incorrect
selection. The unchanged fresh complete frontend rerun without those backend jobs
passes **3,627 tests / 310 files**, exit 0, with no unhandled errors (419.28
seconds). No assertion or test timeout was relaxed. Its log is
`/tmp/recovery-integrated-frontend-final.log`; the failed earlier run remains
recorded in `/tmp/recovery-integrated-frontend-full.log`.

All **31 post-correction QA drivers** and **36 upstream unit tests** pass. Inventory
matches **220 tools / 14 tabs / 17 panels**; the control matrix matches **165 controls
/ 14 families**. Documentation has **21 files / 152 local links / zero broken links**.
Ruff across all **14 touched Python files** adds **zero findings** relative to
master `c6b9128f` (**45 inherited findings**, including legacy test fixtures).
The comparison normalizes moved line numbers in diagnostic messages; it retains
file, rule, diagnostic text, source line and multiplicity.

The first complete backend attempt then exposed the inherited solver-handoff
regression: sequential tasks advanced chart export before solve completion;
standalone native-file tests also assumed the earlier synchronous contract.
Those numerical assertions remain intact: standalone tests now join the real
solver and verify terminal success before reading source values. Sequential
durable tasks retain a bounded outcome wait; ordinary foreground solves remain
detached. Nine new task-outcome refusal cases cover both solver kinds,
contradictory receipts, deadline, missing queue and cancellation. The focused
provider/task/file/progress selection passes **58 cases** (25 warnings, 44.23
seconds), including original real-HiGHS four-provider delivery assertions.
Partial stopped runs and setup mistakes are not counted as final validation.

Final collection at `75e6afd1` contains **11,536 backend cases / 540 files**.
Coverage is **multi-revision**, with affected seams rerun on updated master; this
is not a fresh complete run at the final head. Original collection at `3c24ba5d`
had 11,530 cases / 539 files, balanced into two disjoint whole-file selections
(descending case count and filename tie-break). The first selection stopped on
the detached-solver conversation oracle; its entire failing file and remaining
files were rerun after waiting for the real terminal worker. The old failure and
three partial passes from that file are excluded from final totals.

| Completed selection | Source | Passed | Skipped | Cases |
| --- | --- | ---: | ---: | ---: |
| Shard 1 whole-file prefix before the corrected conversation file | `3c24ba5d` | 3,499 | 9 | 3,508 |
| Shard 2, complete, exit 0 | `3c24ba5d` | 5,509 | 256 | 5,765 |
| Corrected conversation file and remaining shard-1 files, exit 0 | `75e6afd1` | 2,253 | 4 | 2,257 |
| Six added owner/foreground cases in the updated-master gate, exit 0 | `a7556fcd` | 6 | 0 | 6 |
| Unique final collection accounted for | Multiple revisions | **11,267** | **269** | **11,536** |

The coverage check compares final collected node IDs to those three disjoint
file selections plus six additions: two foreign-owner resume/rebind refusals and
four foreground-job wait tests. **No final collected case is missing.** Skips
remain exclusions, not correctness coverage. The baseline runner warned that
`durable_conversation.py` changed during its run for the owner correction; all
55 recovery cases and affected parent seams were subsequently rerun serially at
the integrated head. The updated-master recovery/history/parent/admission/task/
foreground/workflow gate passes **587 cases / two skips** (454 warnings, 598.70
seconds), including all additions. The corrected conversation oracle file also
passes **five cases / four paid-live skips**. Focused gates overlap the table and
are not added to its totals.

Evidence: `/tmp/recovery-backend-manifest.json`,
`/tmp/recovery-remainder-manifest.json`,
`/tmp/recovery-integrated-final-collection.log`,
`/tmp/recovery-final-validation-result.json`,
`/tmp/recovery-integrated-backend-selection.json`, and corresponding logs.
The remainder took 438.08 seconds; shard 2 took 2,852.59 seconds. Earlier stopped
runs, diagnostic/setup failures and the superseded oracle failure are retained
in logs and excluded from passed totals.

Reproduce from `pypsa-gui/backend` with the locked Pixi interpreter:

```sh
XDG_CACHE_HOME=/workspace/scratch/assurance-cache \
XDG_DATA_HOME=/workspace/scratch/assurance-data MPLCONFIGDIR=/tmp/assurance-mpl \
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
/workspace/pypsa-eur/.pixi/envs/test/bin/python -m pytest tests -q -o addopts='' --tb=short
```

Run frontend `npm test -- --run` and `npm run build` from `pypsa-gui/frontend`.
No paid model calls, remote CI success, deployment activation or independent
domain certification is claimed. Actions dispatch returns HTTP 422 because
Actions is disabled. General language/domain assurance and asynchronous task
resumption remain open.

### Final master integration: PR 122 and queued correction guard

Master advanced to `1970db27` / PR 122 after the completed PR-121 validation.
Merge `02646474` preserves new live-media/admission auto-drain, progress cursors,
study/report adapters, recorded-run tools and load-flow summaries. The integrated
gate reruns all changed backend test modules and the existing recovery/parent/
task/progress/workflow gate: **869 passed / six skipped**, exit 0 (461 warnings,
342.38 seconds). All **47 affected frontend cases / six files** pass, exit 0
(15.87 seconds), and the production build passes. The complete **3,627-test**
frontend run above precedes this merge; no new full frontend run at this head is
claimed.

Review found the new auto-drain reusing the preceding utterance's explicit scope:
a queued "Show those results again, but only for 2025" could receive a checked
2026 answer. Two authenticated stream cases assert independent values and scopes,
lease release and private checkpoint state. Before the fix, the constrained case
fails and safe repeat passes. Setup/fixture corrections are excluded from this
missing-guard proof. `cd1ba70e` drops the prior explicit scope at promotion so the
existing grammar resolves validated context afresh. The constrained correction
now clarifies without a second model call, checked blocks, a numerical answer or
a retained checkpoint; the safe repeat preserves exact scope and its new approved
anchor. The post-fix admission/recovery/scope/task/frame selection passes **107
cases / one deliberate skip**, exit 0 (97 warnings, 83.26 seconds).

The new tool catalogue also expands generated test parametrizations. All 36
additional contract cases run: **32 passed / four deliberate paid-live skips**
(2,052 deselected, 4.45 seconds). A handler-signature node includes a source line
number in its identifier; that one old node retires and its complete module
passes **176 cases**, exit 0 (16.16 seconds). These generated cases are explicitly
included in final collection coverage instead of assuming unchanged file lists.

Final collection contains **11,751 cases / 551 files**. Compared with the prior
11,536-case checkpoint, PR 122 adds 213 cases and this change adds two drain
cases. One old passed line-based node is replaced, leaving 216 added/renamed node
IDs; all are covered by the above selections. Four additions are paid-live skips.
Unique final accounting is therefore **11,478 passed / 273 skipped / no missing
collected cases**. Selections overlap; their totals are never summed. This is
multi-revision baseline-plus-correction coverage, not a fresh full backend run at
the final head. The prior 31 post-correction QA drivers and 36 upstream tests
remain passed historical results; latest affected paths have their fresh gates.

Inventory now matches **224 tools / 14 tabs / 17 panels**. The control matrix
matches **165 controls / 14 families**, with complete behavioral coverage still
not established. Documentation remains **21 files / 152 local links / zero broken
links**. Ruff adds **zero findings** across **15 touched Python files** relative
to `1970db27` (**46 inherited findings**). Diff checks pass. Evidence adds
`/tmp/recovery-pr122-{backend-gate,frontend-gate,build,final-collection,argshape,new-tool-contracts}.log`,
`/tmp/recovery-pr122-backend-selection.json`, and
`/tmp/recovery-checked-drain-{red,final}.log`. The final JSON accounting artifact
is updated with these sources, generated node IDs and exclusions. No paid model
calls, remote CI success or release activation occurred.

## StorageUnit continuation validation (2026-10-11)

Implementation source: `9f6a4363`, based on merged PR 123/master `fabda836`.
The profile supports six metrics for named or currently selected noncyclic,
zero-inflow/spill StorageUnits. Exact windows preserve the predecessor state
chain. Asset class is bound to the profile: a generator named `network` cannot
become a network-wide cost request or acquire a network header. Clarification
also names StorageUnits. The existing error manifest's duplicate worker-pin
entry is removed. Contract draft edits capture their typed value before deferred
updates; the pricing test exercises successive edits in application StrictMode
and still requires exactly 50,000 before saving, with no participant auto-save.

- The initial 11 storage acceptance cases fail before implementation. The
  completed storage file has **40 cases**, covered by the final gate. Independent
  LP and hand-worked fixtures cover 10/4/−6 MWh grid-side energy, states 8/4/0,
  standing loss and simultaneous operation. Missing/invalid trajectories,
  model fabrication, current-source drift, retained JSON/ZIP and native restart
  recovery are checked through authenticated public routes.
- Selected-StorageUnit admission has a genuine one-case red run, then the
  storage/generator/scope/recovery gate passes **153 cases**. The reserved-name
  generator case separately fails on the wrong profile before correction and
  passes after it. No independent domain approval or production error-rate
  measurement is claimed.
- Complete backend baseline `58a4b5a4`: **11,515 passed / 273 skipped**, covering
  all **11,788 collected cases in 552 files**. Two disjoint whole-file shards
  exit 0: 5,875/19 and 5,640/254 (passed/skipped). Subsequent source changes use
  the final **54-file gate at `9f6a4363`: 912 passed / six skipped**, exit 0.
  Final collection has **11,792 cases**; the four added cases and final affected
  paths account for **11,519 passed / 273 skipped**, with no uncovered nodes.
  This is complete-baseline-plus-affected-path coverage, **not a fresh full
  backend run at the final head**. Skips are not coverage; gate counts overlap.
- Fresh complete frontend at `9f6a4363`: **3,637 passed / 310 files**, exit 0,
  473.71 seconds and no unhandled errors. The first full run had the inherited
  duplicate-manifest failure (3,635 passed / one failed); the next had an
  intermittent immediate button-state assertion (3,636 passed / one failed).
  The final run includes the manifest correction, captured input value and
  asynchronous enabled-button check, preserving the exact price/API assertions.
  Corrected manifest/storage UI gate: **200 passed / three files**; pricing
  gate: **11 passed / one file**. The rapid-edit/StrictMode test also passed
  before the input capture change; it is not claimed as a failing red oracle.
- **All 31 QA drivers accounted for**: 30 pass initially; the self-hosted phase-4
  driver initially times out during server startup under contention and then
  passes unchanged across two scenarios at normal priority. **36 upstream unit
  tests** pass. Final TypeScript/Vite build passes (existing chunk-size warning).
  Frontend source is identical between its build source `19351c9e` and `9f6a4363`.
- Inventory matches **224 tools / 14 tabs / 17 panels**. Ruff adds no findings
  across **eight touched Python files**. Documentation links and diff checks
  pass. Providers are scripted or HTTP-mocked; no paid model calls. Repository
  Actions remains disabled, so these are local results, not remote CI success.

Current reproducible evidence is in `/tmp/storage-backend-manifest.json`,
`/tmp/storage-final-backend-gate-manifest.json` and
`/tmp/storage-final-validation-result.json`. Baseline/final collections and logs
use the `/tmp/storage-` prefix; `finalize-storage-coverage.py` checks the exact
node union and equivalent backend source. These executor-local files are not
committed artifacts and should not be assumed to survive a new environment.
The source tests and commands below are the durable reproduction path.

## StorageUnit direct-cost continuation validation (2026-10-11)

Base master `b51563e9` includes PRs 124/125. The user authorized this bounded
continuation. The [plan](storage-economics-plan.md) defines direct asset accounting
and independent literal arithmetic; no independent engineering/domain review
or broader production release is claimed.

- Initial public 51-EUR oracle: **one failed / 32 deselected**, because
  `storage-unit-cost/1` was not yet an admitted profile. Initial implemented
  cost suite: **33 passed**, 65.22 seconds, exit 0.
- Seven additional acceptance cases cover static-price fallback, simultaneous
  operation, zero-baseline percentage refusal, operating-cost clarification,
  declared-basis key-results grammar, previous distinct runs and assets named
  `network`. Defaults are 12 metrics; quantities/units/formulas are independently
  specified, not copied as expected values from service aggregates.
- The wider backend gate at `50499693` passes **1,029 / six skips**, 538.29
  seconds, exit 0, with 63 selections (including the acceptance directory).
  It includes common provider transports, authentication, tool schemas, history,
  native recovery, budgets, frame contracts and master PR 124's run-evidence
  domain regressions. Paid-live and deliberate frame-recording skips are not
  coverage. This is a selected gate, not a fresh full backend run.
- An isolated metadata assertion fails **one / 39 deselected** before correction:
  unavailable cost dependencies incorrectly borrowed `3/3` physical-trajectory
  coverage. The final fix omits that count for unavailable cost/unsupported
  slots. **All 40 cost cases pass**, 74.13 seconds, exit 0 at `360b8cea`,
  including all nine invalid-cost dependency cases with the stronger assertion.
  The original checkout stayed frozen for the wider run. The only subsequent
  Python differences are these metadata/assertion changes. The 40 cases overlap
  the 1,029-pass gate; do not sum them as unique tests or claim a full fresh run
  at the final head.
- Fresh complete frontend: **3,641 passed / 310 files**, 342.16 seconds,
  exit 0, no failures/unhandled errors. Source frontend tree
  `12109ddca0ab043958b0d15f547fcef2bbea3eaa` is unchanged by the metadata fix.
  Two new UI cases cover checked direct-cost text, voice, declared basis and
  unsupported-NPV incompleteness. The base's expanded error-kind manifest also
  contributes cases relative to earlier historical frontend totals.
- Production TypeScript/Vite build passes with its existing chunk-size warning.
  Inventory matches **224 tools / 14 tabs / 17 panels**. All eight touched Python
  files pass Ruff without findings. Documentation/local links and diff checks
  pass. Previous complete-backend, QA and upstream results stay historical;
  they are not claimed as fresh storage-economics validations.

Exact selection, source hashes, commands, environment and numerical counts are
committed in [storage-economics-validation.json](storage-economics-validation.json).
Executor-local detailed logs use `/tmp/storage-cost-` (red, first-green, metadata
red, final-focused, backend-gate, frontend and build); they are not durable
artifacts. No paid model calls or remote CI success. Deployment stays opt-in;
Stores/Links, charging purchases/revenues, overnight/FOM derivation, multi-period
economics/NPV, other domain adapters and independent calibration remain open.

## User-facing checked pilot validation (2026-10-11)

The user explicitly selected the user-facing opt-in scope. Initial base is
`73e11fdc`; final source `38404196` integrates master `baa3a95e` (PRs 126/127/129).
The [pilot plan](checked-opt-in-plan.md) and [activation/rollback guide](checked-opt-in-runbook.md)
describe the bounded contract. Global production deployment is not activated.

- The first new UI invocation had an incomplete greeting fixture and was not
  counted as feature-red evidence. After correcting it, the isolated master
  checkout fails **one / 17 filtered-out cases** because the opt-in checkbox is absent.
  Initial implemented UI runs exposed test-fixture mismatches (selection `type`,
  retry-button identity, project-effect timing and simulated server capabilities),
  corrected without relaxing checks. The focused six-file gate passes **81**;
  the original five backend pilot cases pass with the independent 51-EUR LP and
  real authenticated publication/history paths. These overlap larger suites.
- An additional round-trip project change during pending rewind has a genuine
  red: **one failed / one passed / 27 filtered-out cases**, clearing/re-asking the wrong
  conversation. The final guard rechecks the monotonic browser generation,
  alongside project/chat binding; the final complete frontend covers the stronger
  38-case pilot/API collection. Checked retries, edit, clarification, queue/Live,
  failure/rollback, acknowledgement and ordinary companion behavior all pass.
- Initial checkpoint `428be5f9`: selected backend **1,045 passed / seven skips**,
  669.24 seconds, exit 0; complete frontend **3,678 / 312 files**, 431.70 seconds,
  exit 0. Master subsequently advanced; these are historical overlapping results,
  not final-source proof or additive unique coverage.
- Final `38404196`: selected **71-entry** shared backend gate **1,097 passed /
  seven skips**, 660.77 seconds, exit 0. This covers common provider transports,
  auth, history/recovery, tool schemas, budgets, frames and imported master's
  run discussion/export, experiment-plan and credential-alias regressions.
  Skips are five opt-in paid-live cases, deliberate frame recording and an empty
  `KNOWN_BROKEN` delegation list; skips are not coverage. No fresh full backend
  suite at final head is claimed.
- Fresh complete integrated frontend **3,686 passed / 313 files**, 436.42 seconds,
  exit 0, without failures/unhandled errors. Source `src` tree
  `750426e67a60c289cd14467e472619d8439f72a0` remains unchanged after final checks.
  Production TypeScript/Vite build passes with its existing chunk-size warning.
- Native Chromium smoke passes at the integrated source, **three requests**,
  zero model calls, keyboard opt-in, actual fetch/SSE/native digest, withheld
  pre-ack and unapproved text, HTTP rollback with retained choice/draft and
  explicit ordinary opt-out. Earlier probe attempts failed because the mock
  route matched Vite `/src/api/*.ts` module imports; the final route matches
  actual `/api/` paths and the upload fixture matches the array contract.
  The real UI runs with mocked HTTP responses, not a paid provider or real
  backend auth/evidence stores. Numerical/auth backend gates are independent.
  The editing agent inspected its 380-pixel screenshot; this is not all-device
  or independent accessibility/domain certification.
- Inventory matches **229 tools / 14 tabs / 17 panels**; both touched Python
  files pass Ruff with no findings. Local documentation/link and diff checks
  pass. Release notes, contracts, handoff and issue comments are updated.

Reproduce with [checked-opt-in-validation.json](checked-opt-in-validation.json)
and `frontend/scripts/smoke-checked-answers.mjs`. Detailed logs/manifests use
`/tmp/checked-opt-in-`; browser artifacts are executor-local. Commands/selections
are committed. Only docs/validation change after the final gates. No paid model
calls, remote CI success or independent domain approval. Full backend, broader
browser/device/domain/calibration and production enablement remain separate scopes.


### Final backend-only master integration

Master subsequently advanced to `8a4927f9` / PR 130 (discussed-run resume).
It changes durable resume, its tests and docs; the tested frontend `src` tree
remains `750426e67a60c289cd14467e472619d8439f72a0`. Integrated source `693257be`
passes the 15-entry resume/recovery/parent/run/history/pilot/storage gate:
**220 passed / zero skipped**, 195.32 seconds, exit 0. These cases overlap the
1,097-pass gate; no counts are summed and no fresh full backend result is claimed.
The 3,686-case complete frontend, build, browser and 229-tool inventory retain
identical relevant source. Only documentation/validation records change afterward.
Exact final base/source/selection are in [validation](checked-opt-in-validation.json).
