<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant assurance continuation handoff

Status: expanded deterministic calibration and blind-review preparation on
`feat/harness-reliability-calibration`, following merged live-evidence PR 143.
Historical sections below describe earlier
states and commands. The current [implementation status](implementation-status.md)
and [API contracts](harness-contracts.md) supersede them. Independent domain/release
approval remains pending.
Baseline: master d43809ae. Dictation PR 106 has merged; existing Claude/tool
behavior remains the baseline. Do not reuse the old failed environment.

Read [spec](spec.md), [tests](test-plan.md), [implementation plan](../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md),
and harness README. Use existing immutable-run/durable-thread/job/UI designs in
`.scratch/live-voice/`; assurance is a shared publication layer over those services.

## Transferred baseline findings and observed controls

Ordinary text/thinking streams without review; tool-return success and navigation
acknowledgement are not equivalent to completed effects/view state. Reports have
a diagnostic value-matching audit, not admission. Charts already validate finite
data/units/current input provenance but need exact dataset/render manifests.

On baseline, relevant existing controls passed **60 tests**: stream-attempt seam,
pre-dispatch validation, numeric audit and report generator. Command:
`python -m pytest -o addopts='' -q backend/tests/test_chat_stream_attempt_seam.py
backend/tests/test_chat_pre_dispatch_validation.py backend/tests/test_number_audit.py
backend/tests/test_report_generator.py` (paths relative to pypsa-gui).
Those passes do not establish the proposed gate. No paid APIs needed for planning.

## Transferred next actions (historical)

The following numbered actions describe the transferred session. The current
continuation below supersedes its credential/worktree assumptions and adds
review requirements; do not treat archived paths as current worktrees.

1. Publish reviewed docs branch `docs/assistant-answer-assurance` (commits
   `55ce946b`, `34dbb3f3`) once GitHub access is restored. Last authenticated
   repository API check returned HTTP 401; git fetch has no usable credentials.
   A missing replacement `PYPSA_GUI_GITHUB_TOKEN` is not evidence of readiness.
   Do not repeat dictation PR 106, delete locks, or alter another worker's config.
2. Acceptance worktree `/workspace/assistant-assurance-tests` is on
   `test/assistant-assurance-acceptance`, based on the local reviewed docs branch.
   After docs merge, transplant only the acceptance commit onto fetched master
   (especially if docs squash-merge). Preserve local files and other worktrees.
3. Opt-in baseline: backend **43 expected failures / 4 controls pass** (47 cases);
   frontend **11 expected failures / 1 availability control passes** (12 cases).
   No collection/setup errors or paid APIs. These are new missing-feature tests,
   not evidence of regressions introduced by an assurance implementation.
4. Backend relevant regressions: **321 passed, 48 skipped**. Skips are the 47
   opt-in acceptance cases plus the deliberate golden-record generation helper.
   Existing provider/tool/confirmation/history/ownership/contracts remain green.
   Full frontend **3,529 passed, 12 acceptance skips**; TypeScript/Vite build
   passed. See the results assessment and coverage ledger. Do not count skips
   as coverage.
5. Publish tests separately with this ledger. Implementation starts the strict
   admission/unsafe-stream tracer, then verified input facts and progressive phase
   tickets. Use vertical red -> minimal implementation -> green -> regressions;
   complete the pending per-domain/reviewer/race/browser fixtures explicitly.
6. No master publication/PR is claimed until an authenticated push/API confirms
   it. Design and initial-test reviews do not establish runtime release readiness.

Credentials, recorded audio and usage ledgers must not enter commits. Preserve
configured proxy/CA and current shared spend caps for any later opt-in live work.

## Independent review completed

The separate reviewer approved publication/test creation after P2 public-channel
gating, frozen protocol-v1 and commit-before-emission/idempotent-fetch corrections.
Final caution: strict pre-approval tool cards cannot expose unchecked prose or
unapproved artifact links. Approval is a design verdict, not runtime certification.


## Test review resolved

Independent re-review approved the first RED tranche after requiring real provider
requests (no empty-stream pass), persisted nonempty approved history, event order,
positive mismatched ACK generations, exact canonical chart hashes and explicitly
labeling the legacy sidecar corruption fixture. Reviewer/restart/revocation races,
all-domain journeys, performance and real-device coverage remain requirements.

## Documentation assessment continuation (before implementation)

User requested assessment and improvement of reliability plans/specifications;
application implementation and publication were not performed in this tranche.
Restored 16 documentation files from the transfer into the repository, preserving
the archive as historical source. Current branch: `docs/assurance-reliability-review`
on `d43809ae`; transferred acceptance source remains in the archive, not installed.
The old docs/test branches and worktree paths above are not present here.

GitHub repository API and `git ls-remote origin HEAD` succeeded in this session;
API permissions include push. OpenAI `/v1/models` returned HTTP 200. These checks
supersede the transfer's HTTP401 blocker, without claiming publication or paid
generation capability. Runtime readiness metadata reported unknown; observed API
checks establish access for those requests only. Activation script:
`source /workspace/activate-assurance-env.sh`; no credentials saved to files.

Read [reliability contract](reliability-contract.md) and the
[source assessment](../../docs/superpowers/assessments/2026-10-10-assistant-assurance-reliability.md).
Added required request-slot/key-results checks, versioned metric/calculation
definitions, raw validity masks, independent numerical oracles and mandatory
effect guards/read-back/idempotent reconciliation. Tickets 09–11 and added
matrix groups are proposed, unimplemented and have no new acceptance coverage.
The original independent review does not approve them. Review/freeze proposed
observable contracts and compatibility before writing their fixtures.

Next implementation tracers: unsafe-publication buffering, then the worked
request-scoped energy/cost summary with positive and invalid-interval cases.
Keep first-tranche counts historical and new groups pending in the ledger.

Current validation (this session):

- `MPLCONFIGDIR=/tmp/chat-mpl /workspace/chat-compat-venv/bin/python -m pytest -o addopts='' -q backend/tests/test_chat_stream_attempt_seam.py backend/tests/test_chat_pre_dispatch_validation.py backend/tests/test_number_audit.py backend/tests/test_report_generator.py` from `pypsa-gui`: **60 passed**, two existing FastAPI lifecycle deprecation warnings.
- `/workspace/chat-compat-venv/bin/python pypsa-gui/backend/smoke/assistant_inventory.py --check`: **220 tools / 14 tabs / 17 panels** match; behavioral mapping remains pending.
- Local Markdown check: **21 documentation files / 125 local links**, no broken links/trailing whitespace; ten added case groups uniquely defined and pending coverage explicit.
- No application/test source changed and no paid model calls ran. These checks do not implement or certify the new guardrails.

## First implementation continuation

The subsequent user request authorized starting implementation as well as review.
Created `feat/assistant-assurance-publication` from documentation commit `f533feda`;
restored acceptance source without applying the archived patches. Implemented the
[static-input/publication pilot](implementation-status.md), with default mode
disabled. Whole P0–P7 and proposed reliability tickets remain open. No paid model
calls, deployment, push or pull request is claimed.

Observed baseline before implementation: **43 failures / 4 passes**, reproducing
the original 47-case backend RED result. After implementation the original suite
has **44 passes / 3 pending P4 failures**. The initial combined run also exposed a
quota-fixture isolation failure, corrected without weakening its reclamation check.

Final checks (venv `/workspace/chat-compat-venv`, local loopback networking enabled
for TestClient and frontend tooling):

- From backend: `PYPSA_GUI_ANSWER_ASSURANCE_MODE=disabled python -m pytest -o addopts='' tests/test_answer_assurance.py tests/assurance_acceptance -q --tb=short`: **68 passed, 3 skipped**. Only unimplemented P4 artifact cases are opt-in; implemented acceptance and 24 new boundary cases run by default.
- From backend: `PYPSA_GUI_ANSWER_ASSURANCE_MODE=disabled python -m pytest -o addopts='' tests/test_answer_assurance.py tests/test_chat_*.py tests/test_harness_*.py tests/test_number_audit.py tests/test_report_generator.py -q --tb=short`: **1,851 passed, 2 deliberate skips**. Before final strict-only precision formatting and extra fixtures; final assurance suite verifies those changes.
- From frontend: `npm test -- --maxWorkers=2`: **3,543 passed / 299 files**. Before final callback guards.
- Final frontend: `npm test -- ChatPanel AssistantResultCard --maxWorkers=2`: **574 passed / 23 files**, including 17 assurance cases. The new guard initially blocked legitimate multi-project rebinds; it now tracks server-issued ordinary-chat rebinds while rejecting callbacks after user changes, and the existing lock/rebind regression passes.
- `npm run build`: TypeScript and Vite pass; existing large-chunk warning. Final post-guard build also checked.
- Ruff lint/format on new assurance modules and restored acceptance files pass; source inventory remains **220 tools / 14 tabs / 17 panels**; local documentation links and `git diff --check` pass.

Next tracer: freeze/review the proposed computed-block and request-slot additions,
then implement independently checked weighted energy/cost summaries with raw
invalid-interval coverage. P4 manifests/download admission and positive UI ACKs
remain separate incomplete tranches. This editing-agent review does not replace
the required independent/domain/release reviews.

## Numerical/chart continuation and publication request

The user explicitly authorized creating a PR after tests pass and merging to
master after CI passes. `feat/assistant-assurance-calculations` builds on the two
local documentation/static-publication commits. This section records local
validation before publication; GitHub checks on the final PR commit govern merge.
No paid provider calls, independent/domain reviewer verdict or production-mode
activation is claimed. See the updated [implementation boundary](implementation-status.md).

Implemented a candidate `generator-dispatch/1` profile: server-owned three-slot
scope, weighted raw energy/marginal expenditure and peak, matching optimal LP
provenance, exact axis/finite/bounds checks, per-metric masks, explicit unavailable
full horizons and labelled valid subsets. Draft omission/fabrication fails closed.
Dynamic ledgers are fenced per-turn data. Changed outputs/inputs/solve status or
access revoke replay. The real-LP oracle is 17 MWh/170 EUR/7 MW; objective weights
[1,1] independently yield 120 EUR expenditure. This does not implement the proposed
230 EUR total-cost example or certify whole-network engineering validity.

Charts commit exact dataset/units/render/PNG and complete-provenance hashes before
release. Download/delivery refuse corrupt bytes/sidecars. Capture is rechecked
under the network lock after rendering. Bundles copy the same validated provenance
rather than reopening it; immutable captured-chart integrity is not current-run
scientific certification. Generic/legacy files have no checked chart assertion.

Locked runtime: `/workspace/scratch/assurance-pixi/pixi` v0.68.1;
`pixi install --environment test --locked`. Set `XDG_CACHE_HOME` and
`PIXI_CACHE_DIR` to `/workspace/scratch/assurance-cache`, `XDG_DATA_HOME` to
`/workspace/scratch/assurance-data` and `MPLCONFIGDIR` to a writable `/tmp` path.
Loopback/network permission is needed for TestClient and standalone QA servers.
The earlier lightweight venv lacks upstream runtime packages; its collection
errors are environment setup failures, not passes. No lockfile/dependency pins
were changed to install the repository's already-pinned environment.

Validation:

- Repository root: `pixi run --environment test --locked gui-tests`:
  **11,028 passed, 4 failed, 269 existing skips**. Corrected every failure:
  missing shared error-manifest entries and missing argument fixtures for both
  existing mutating tools in the derived sweep guard. Backend production logic
  was not weakened to satisfy these checks. A backend full-CI pass remains
  required on the final PR commit before merge.
- Backend directory: `pixi run --environment test --locked python -m pytest
  tests/test_tool_error_kind_manifest.py tests/test_live_network_untouched.py
  -k 'derived_chat_write_list or every_chat_network_write or seam_study_gate or
  manifest or forwarder or derivation'`: **37 passed / 28 deselected**.
  All four previously failing backend cases are included and pass.
- Backend directory: `pixi run --environment test --locked python -m pytest
  tests/test_assurance_metrics.py tests/test_answer_assurance.py
  tests/assurance_acceptance`: **106 passed**, no artifact skips. Includes raw
  axis faults, UI-selected Generator, unsupported periods and wrong entity.
- Repository root: `pixi run --environment test --locked unit-tests`:
  **36 passed**. `gui-qa-drivers`: **all 31 passed** after correcting the phase-0
  driver's authenticated identity binding; the initial run had one failure.
- Frontend: `npm test -- --run`: **3,546 passed / 299 files**. After shared
  manifest-required banner/copy changes, `npm test -- ChatPanel --maxWorkers=2`:
  **582 passed / 22 files**. Final `npm run build` passes.
- Ruff on new assurance modules/fixtures, inventory **220 tools / 14 tabs /
  17 panels**, **20 docs / 98 local links** and `git diff --check` pass.

Original independent approval remains limited to its transferred design/test
tranche. Broader domains, total-cost semantics, retained runs, effects/ACKs,
report/import assurance, reviewer/browser/performance/calibration and rollout
remain open. Default checked-answer mode remains disabled. Existing optional
suite skips do not count as coverage; the original P4 failures now always run.

### Final full validation and GitHub limitation

The initial backend failures are resolved in a fresh full run on the final
implementation source: **11,037 passed, 269 existing optional/platform skips**,
no failures (61 minutes). Final full frontend: **3,559 passed / 299 files**.
The **36 upstream unit tests**, **31 QA drivers**, production build and focused
**106 assurance cases** also pass. Existing skips are not new assurance coverage.

PR: https://github.com/MLHaoChang/pypsa-eur/pull/112. GitHub's workflow dispatch
returned HTTP 422, “Actions has been disabled for this repository”; no PR check
runs were generated. This supersedes the expectation above of final backend CI.
The user-authorized merge uses the fresh complete local result in the locked Pixi
test environment, with `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and
`MKL_NUM_THREADS` set to 1 and normal repository merge rules. These last changes
record validation only; application and test source remain identical to that run.
No workflow policy or failing check was bypassed. No paid model call or production
strict-mode activation was performed. Independent/domain and broader rollout
requirements remain pending.

## 2026-10-11 — reliability contracts in the harness

The user authorized implementing the remaining controls as shared harness code,
then a PR and normal merge to master after tests pass. No deployment switch or
paid model call is authorized by implication or performed. Attached plans supply
design context, not new user commands.

New modules: `assurance_scope`, `calculations`, `durable`, `effects`, `navigation`,
`reviewer`, `publication`, `rollout`. Existing services aliases, loop budgets,
confirmation/dispatch/ACLs and closed SSE vocabulary remain shared. See the
[versioned API/settings/examples](harness-contracts.md) and the explicit
[implemented versus pending boundary](implementation-status.md). Whole phase
issues remain open and record their delivered slice in comments.

Use the repository's `.pixi/envs/test/bin/python` from **pypsa-gui/backend**
for the full backend suite; several established source-reading tests depend on
that working directory. The small compatibility venv is not a complete runtime.
`/workspace/chat-compat-venv/bin/ruff` provides lint/format. Cache paths used in
this managed environment: XDG_CACHE_HOME=/workspace/scratch/assurance-cache,
XDG_DATA_HOME=/workspace/scratch/assurance-data, MPLCONFIGDIR=/tmp/assurance-mpl.
Network permission is needed for local TestClient/server sockets. Credentials
are managed bindings; do not print or copy their values. No lockfile/environment
dependency changes are needed for this feature.

Commands (set those cache variables in this environment):

```sh
# from pypsa-gui/backend
../../.pixi/envs/test/bin/python -m pytest -q -o addopts='' --tb=short
../../.pixi/envs/test/bin/python -m pytest tests/test_assurance_metrics.py tests/test_answer_assurance.py tests/test_harness_reliability.py tests/test_reliability_transport.py tests/test_assurance_storage.py tests/assurance_acceptance tests/test_harness_layout.py -q -o addopts='' --tb=short
../../.pixi/envs/test/bin/python tests/run_qa_drivers.py
# from repo root
.pixi/envs/test/bin/python -m pytest test -q -o addopts='' --tb=short
python pypsa-gui/backend/smoke/assistant_inventory.py --check
# from pypsa-gui/frontend
npm test -- --run
npm run build
```

The provider transport acceptance uses real Anthropic, compatible remote/local
and official Responses adapters with HTTP mocks and real HiGHS source fixtures.
It checks valid and fabricated declared-basis totals, not live model capability
or production error rates. Multiprocess storage tests verify quotas/atomic bytes;
active project/network state remains single-worker.

Final focused gate: **197 passed**. Full frontend: **3,567 passed / 301 files**;
production TypeScript/Vite build passed (existing chunk-size warning). Upstream:
**36 passed**. Ruff, inventory (220 tools / 14 tabs / 17 panels) and diff checks
pass. All 31 QA drivers passed on the continuation; fresh full backend/frontend
runs after integrating master are required before PR/merge. A root-directory backend attempt was stopped because
existing relative-source tests require backend cwd; it is not a passing gate.
GitHub Actions was previously refused with 422 because disabled repository-wide;
recheck remote policy and report local versus remote validation honestly.

### Current master integration and final checks

Integrated master `b8bc079f` (companion PR 113) into the reliability branch.
The Results merge preserves both its companion `data-active-results-tab` marker
and the reliability `data-assurance-tab` marker. Corrected upstream TypeScript
errors: optional walk-options access and a test fixture's nullable active-profile
ID against the declared string response type. Production build passes.
The integrated frontend initially had **3,604 passed / 1 timeout**, solely the
existing 1,000-row DOM test under concurrent build/QA load. No assertion or timeout
was changed. Its affected five-file rerun passes **98 tests**; a fresh full suite
is running. The independent two-bus feeder oracle additionally passes **3 tests**:
80 EUR/year capacity charges / 250 EUR total, overload refusal and within-capacity
power-balance refusal. The initial focused gate was 197; these three supplement it.

New modules/records/tests pass Ruff. Modified legacy loop/session retain exactly
**7 pre-existing findings**, verified against origin/master; routers add none.
No broad lint-clean claim is made. Upstream units after master integration:
**36 passed**. GitHub inventory dispatch was rechecked and still refuses with
HTTP 422 “Actions has been disabled for this repository”, despite workflow
metadata listing active definitions. Remote Actions is not the validation gate.

Full integrated frontend rerun: **3,605 passed / 307 files**, unchanged assertions
and timeout settings (501.87 seconds). This replaces the initial concurrency
timeout as the final frontend gate. Backend full run is still in progress; QA
is rerunning all 31 drivers on the integrated source.

Final focused backend gate: **200 passed** / 65 existing warnings, 140.27 seconds.
Static-write receipts now explicitly label static settings and their time-series
override boundary. Receipt enum fields require actual strings, refusing arrays
that JavaScript would otherwise stringify to valid enum values; affected UI gate:
**26 passed**. Final full frontend and build must follow that parser change.
No further functional changes are planned except fixing any observed failures.

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


## 2026-10-11 — checked conversation recovery continuation

Source `cd1ba70e` integrates master `1970db27` / PR 122. Bounded native-history
recovery now uses private owner/project/chat checkpoints, fresh authorization
and exact approved-anchor/source validation. Required revocation precedes
execution, rewind and parent resume/rebind; failed or aborted publication
cannot revive an older checkpoint. Resume starts a new episode without checked
scope; rebind requires fresh explicit scope. Broader language, domain coverage,
task/effect/billing memory and independent certification remain open.

New recovery tests contain 55 cases. Initial missing-feature cases failed, then
passed; merging PR 118 exposed four parent-control cases which failed before
revocation integration and passed afterward. Final collection has 11,536 backend
cases in 540 files. Disjoint completed baseline selections plus the corrected
remainder and six new cases cover that collection: 11,267 passed / 269 skipped.
This is multi-revision coverage, not a fresh full run at the final head; affected
recovery/parent/progress/task/workflow seams were rerun against updated master
(587 passed / two skipped). Both detached solver numerical oracles now wait for
the actual worker and preserve their original objective/source assertions.

All 31 post-correction QA drivers, 36 upstream tests, updated-master production
build, inventory and control checks pass. Ruff adds zero findings across 14 touched
Python files (45 inherited findings). The first updated-master frontend run had
one existing 1,200-row select-all timeout under concurrent backend load. The
unchanged full rerun without those backend jobs passes **3,627 tests / 310 files**,
exit 0, with no unhandled errors (419.28 seconds). Do not count the failed run or
the older 3,623-test run as current success. Read the latest coverage ledger for exact
sources, partitions and reproduction; evidence is `/tmp/recovery-*`.

The final PR-122 integration gate passes 869 / six skips; all 213 cases added by
that master change are covered by new-module and generated tool-contract checks.
One line-based signature node changed its identifier; its complete module passes
176 cases. Four newly generated paid-live cases are skipped deliberately. Two
additional checked auto-drain cases exposed the original scope being reused for a
queued correction (a 2025 constraint incorrectly received checked 2026 results).
The correction drops the prior utterance's explicit scope and resolves validated
carryover afresh. The affected admission/recovery/scope/task/frame gate passes
107 / one skip; safe repeat scope remains exact and unsupported constraints
clarify without another model call or an old checkpoint.

Final collection is **11,751 cases / 551 files**, accounted for across revisions:
**11,478 passed / 273 skipped / no missing cases**. Latest frontend affected paths
pass **47 cases / six files** and the production build passes. The complete
3,627-test frontend run precedes PR 122; no full run at the last head is claimed.
Current inventory has 224 tools / 14 tabs / 17 panels, controls remain 165 / 14
families, and Ruff adds zero findings across 15 files (46 inherited).
The latest ledger entry and `/tmp/recovery-final-validation-result.json` supersede
the earlier 11,536-case checkpoint above.

All local gates are complete. Remaining release steps: commit final docs,
fast-forward the feature branch to this validated integration, push/create PR
and merge normally after checking its head. The user already authorized
creation and merge after tests pass. GitHub Actions remains disabled (dispatch
HTTP 422); do not claim remote CI or bypass branch protection. Broader adapters,
arbitrary language, distributed runtime, asynchronous task resumption and release
certification remain future scopes; recovery remains opt-in.

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

Reproduction from repository root uses the locked test interpreter
`.pixi/envs/test/bin/python`, with `OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=1`
and writable XDG/Matplotlib cache directories. Backend commands run from
`pypsa-gui/backend`:

```sh
../../.pixi/envs/test/bin/python -m pytest --collect-only -q -o addopts=''
../../.pixi/envs/test/bin/python -m pytest tests/test_storage_assurance.py tests/test_assurance_metrics.py tests/test_conversation_scope.py tests/test_conversation_recovery.py -q -o addopts='' --tb=short
../../.pixi/envs/test/bin/python -m pytest tests -q -o addopts='' --tb=short
../../.pixi/envs/test/bin/python tests/run_qa_drivers.py
../../.pixi/envs/test/bin/python tests/qa_phase4_compare.py
```

The historical full execution partitions all collected files into two disjoint
selections; the equivalent single-process `tests` command above preserves the
collection and takes longer. The recorded final 54-file affected selection is
in the executor manifest; its groups include assurance acceptance, scope/history/
recovery, parent controls, tasks/progress/workflows, provider parity, load flow
and run evidence. Paid suites remain opt-in and were not enabled.

Frontend commands (`pypsa-gui/frontend`): `npm test` and `npm run build`.
Upstream (`repository root`): `.pixi/envs/test/bin/python -m pytest test -q -o addopts=''`.
Inventory: `python pypsa-gui/backend/smoke/assistant_inventory.py --check`.
Next scope remains storage economics/Stores/Links, other domain adapters,
arbitrary-language completeness and independent calibration/release enablement.
Deployment remains opt-in; whole phase tickets remain open.

## StorageUnit economics continuation (2026-10-11)

Implemented `storage-unit-cost/1` on `feat/harness-checked-storage-economics`,
base master `b51563e9` including PRs 124/125. The user authorized implementation,
a PR after required checks pass and merge to master. Attached plans remain
design context. Shared scope, calculations, publication, owned retained evidence,
same-basis comparison, native recovery, exports and rollout now cover bounded
direct StorageUnit economics; default deployment remains disabled.

Independent LP accounting gives 20 EUR discharge + 16 EUR holding + 15 EUR
explicitly allocated fixed charge = 51 EUR. The exact middle window gives
39 EUR; solved 5-MW extendable power gives 43.5 EUR. Charging purchases/revenues,
annuity/FOM derivation, Stores/Links and NPV remain unsupported. All cost claims
require the existing valid noncyclic zero-inflow/spill trajectory, matching solve
provenance and declared EUR price year/fraction. Missing/unsupported dependencies
cannot yield complete cost or borrow a full trajectory count as cost coverage.

Initial feature red: one failure for an unrecognized profile; initial green:
33 passed. Metadata red: one failure for misleading unavailable-cost `3/3`
coverage. The shared gate at `50499693` passes 1,029 / six skips. The final
40 overlapping cost cases at `360b8cea` pass after the isolated metadata fix;
only that metadata/assertion changed after the shared gate. No fresh full
backend result at the final head is claimed. Frontend: 3,641 / 310 files pass;
build, inventory, eight-file Ruff and documentation/diff checks pass. No paid
model calls, independent domain approval or remote CI success.

Reproduce the exact selected backend suite from
[storage-economics-validation.json](storage-economics-validation.json): from
`pypsa-gui/backend`, load `reproduction.backend_args` with the locked test Python
and listed cache/thread environment. Final focused command:
`/workspace/pypsa-eur/.pixi/envs/test/bin/python -m pytest tests/test_storage_cost_assurance.py -q -o addopts='' --tb=short`
(working directory is backend; adjust the executor-specific Python path as needed). Frontend:
`npm test -- --maxWorkers=2`; build: `npm run build`. The detailed
[coverage ledger](coverage-ledger.md) records overlap and source qualifications.
Temporary logs are executor-local, while selections and commands are committed.

Next scope is broader storage models/accounting, adequacy/FMEA/GridSpine,
checked charts/reports, arbitrary-language completeness and independent
calibration/release enablement. Whole phase tickets remain open.

## User-facing checked pilot continuation (2026-10-11)

The user selected “Add user-facing opt-in checked answers”. Implemented on
`feat/harness-checked-answer-opt-in`, integrating master `baa3a95e` (PRs
126/127/129) after initial base `73e11fdc` / PR 128. The persistent instruction
authorizes a PR after required checks pass and ordinary merge to master.

The chat **Check answers** control reads server capabilities and sends the
existing version-1 strict option in pilot mode. Server strict mode stays required;
new pilot chats/projects/reloads default off. Publication is armed before sending
and refuses missing/downgraded/duplicate acknowledgement, unapproved text/thinking
and invalid digest. Retry/edit/clarification/queued/Live paths preserve checking;
unavailable history retains only a conservative retry hint, never approval.
Rollback cannot silently send an ordinary request. Typed drafts survive a refusal;
queued refusals cannot replay on recovery. Monotonic binding generations protect
rewind, including project round trips. Writes/review remain separate switches.

Final source `38404196` passes **1,097 / seven skips** in the selected 71-entry
backend/provider/auth/history/recovery/run/experiment gate. Fresh integrated
frontend **3,686 / 313 files** passes, covering 38 new pilot/API cases. Native
Chromium with mocked API responses passes keyboard/SSE/digest/rollback at
380-pixel panel width; independent real backend checks include the 51-EUR LP.
Build, 229-tool inventory, two-file Ruff and docs/diff checks pass. Only docs and
validation change after these gates. No fresh full backend, paid model, remote
CI, independent review or all-device/domain certification is claimed.

Initial 1,045/7 backend and 3,678/312 frontend checkpoints are historical and
overlap the final gates. First fixture-error runs are not feature-red proof.
The absent checkbox has a genuine isolated-master red, and the round-trip rewind
case has a genuine wrong-conversation red before the generation check. Earlier
browser mock-route overmatching was corrected before counting its pass.

Read [activation/rollback](checked-opt-in-runbook.md) and
[exact validation/reproduction](checked-opt-in-validation.json). Set backend
`PYPSA_GUI_ANSWER_ASSURANCE_MODE=pilot`, restart and explicitly choose **Check
answers** to use it. The PR does not activate a global/production server.
From frontend: `npm test -- --maxWorkers=2`, `npm run build`, and
`CHROMIUM_BIN=/usr/bin/chromium node scripts/smoke-checked-answers.mjs`.
The JSON record contains the exact backend selection and cache/thread settings.
Broader economics, domains, arbitrary-language/interrupt recovery, full-device
journeys and independent calibration/release remain pending; whole phase tickets
stay open.


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


## Pilot evaluation validation (2026-10-12)

At `8e5a26d0`, integrated master `bf0b6b5f`, the standalone 53-case gate passes
with no skips: false acceptance 0/48 adversarial requests, false rejection 0/5
valid golden requests, incorrect calculations 0/66, supported-slot fulfillment
66/66 and unchecked publication 0/53. This is the finite labelled suite, not a
population estimate. Five positive-only cases deliberately selected together
still produce gate exit 1 with 48 missing required cases. Counter mutation
regressions require zero errors even when reason strings are empty.

The 79-selection shared/backend/current-master gate passes **1,362 / 12 skipped**,
800.47 pytest seconds, exit 0. Its evaluation/scorer cases overlap the standalone
run; it is not a fresh complete backend run. Fresh complete frontend passes
**3,687 / 313 files**, 451.24 seconds; TypeScript/Vite build passes. The incoming
master island integration passes 31 / zero skipped. Ruff, workflow/task parsing,
229-tool / 14-tab / 17-panel inventory, docs and diff checks pass. The backend
control-matrix fixture's generated-date-only rewrite was restored after testing.

See [validation](pilot-evaluation-validation.json) for exact commands, selection,
source hash, metrics and limits, and [the evaluation runbook](pilot-evaluation-runbook.md).
No paid inference ran. Live golden evaluation is `pending_opt_in`; qualitative/
population calibration, broader domain/surface coverage, independent review and
production release remain pending. The CI job is wired to run/upload the gate;
no remote CI success is claimed. Only documentation/evidence changes afterward.


## First approved live golden evaluation (2026-10-12)

The user explicitly approved one capped OpenAI run with the reviewed synthetic
payloads and potential charges. On pinned source `53f738ca`, model alias
`gpt-6.1-sol`, **58 cases passed / zero skipped**, 123.80 seconds: 53 deterministic
cases plus all five live golden profiles. The live run fulfilled **33/33** required
slots, with final incorrect calculations **0/33**, false rejection **0/5** and
unchecked publication **0/5**. Live adverse requests were not sampled, so live
false acceptance has denominator zero and rate null, not an estimated zero.

The meter reserved **6 HTTP attempts**, 55,684 serialized input bytes and 12,288
output tokens, within the fixed ceilings (10 / 512,000 / 20,480). No resource/time
cap was increased. Reservations are not actual usage or billed-dollar measures.
The extra attempt is not classified by this report; initial model-draft error or
repair rates are not measured. No demonstrated final failure needed a code fix.
No second live run was performed.

See [live validation](pilot-live-validation.json). These are finite synthetic
supported-profile publication results on the named source/model alias. They do
not evaluate later master app/domain additions, adversarial live behavior,
qualitative/population calibration or independent engineering certification.
Deployment remains opt-in; no production setting is enabled by this evidence.


## Expanded reliability evaluation and blind-review preparation (2026-10-12)

The user requested broader calibration and independent review. On clean source
`f7da26af`, the expanded standalone gate passes **133 / zero skipped**, 111.73
pytest seconds: 45 positive cases and 88 adverse cases. Supported-slot fulfillment
is **330/330**, incorrect calculations **0/330**, false acceptance **0/88**, false
rejection **0/45**, and unchecked publication **0/133**. Eight varied input
scenarios supplement the core suite. These are scripted synthetic publication
results, not live generation or a population error estimate.

The focused selection passes **314 / zero skipped**, 186.14 seconds. Final
calibration follow-up at `3a7361bf` passes **32 / zero skipped**, 3.89 seconds,
after adding per-profile reporting and offline import safeguards. These selections
overlap and must not be added as unique test counts. The only Python changes
after the numeric gate source are the offline review CLI and its tests. Nine-file
Ruff, diff checks and the unchanged 229-tool / 14-tab / 17-panel inventory pass.
No complete backend/frontend or remote CI run is claimed for this backend-only
scope; no new paid inference ran.

See [validation](calibration-validation.json), [the plan](calibration-plan.md),
[the runbook](calibration-runbook.md) and [the review package](domain-review/README.md).
The offline workflow rejects stale/duplicate/unknown records and requires two
independently attested agreeing reviewers and complete observations. It reports
errors, unresolved cases and separate acceptance/rejection denominators; synthetic
cases cannot produce population intervals. Complete attested IID samples may
receive conditional Wilson bounds, with external identity/sampling verification.
No report grants runtime authority or independent engineering approval.

The supplied package has forty worked profile expectations and ten blind
interpretations, with unresolved reviewer templates and no observed decisions.
Its calibration command intentionally exits 1 with `pending_independent_judgments`
and null rates. Actual expert review, representative population sampling and
provider observations remain required external inputs. A7.5 and whole-domain
release remain open. Deployment settings remain opt-in.


## Backend CI quota isolation (2026-10-13)

The full code-change CI run at `f1856446` passed the standalone gate but failed
33 later stress/transport cases when earlier tests exhausted the shared test
user's 512-record store. The public harness refused publication, as required.
A seeded full store reproduces that failure; a two-case regression exposes the
cross-test files. The fixture now clears only its explicitly created test
assurance subtree before/after each case. Production quota/retention/access and
within-case recovery remain unchanged. The default full-store refusal is pinned.

Local focused tests pass **323 / five paid opt-in skips**, 171.20 seconds;
standalone evaluation passes **133 / zero skips**, 110.49 seconds, with
**330/330** fulfillment and zero calculation/publication errors. These selections
overlap. No new Ruff findings or paid inference; three existing conftest findings
remain unchanged. Complete backend CI is pending before merge, rather than
inferred from the subsequent documentation-only green workflow. See
[diagnosis and commands](ci-store-isolation.md) and
[validation](ci-store-isolation-validation.json). Independent review/population
calibration remain pending external judgments and representative observations.


### 2026-10-19 — recovered source and reviewer usage gate

Published plans, phased tests and implementations were recovered from master, rather than recreated. Three new public-boundary cases reproduced reviewer approval with missing input/output or cache-only usage. The bounded fix requires explicit input/output counts, charges known usage and withholds interpretations while retaining facts. Fresh local validation: 65 harness tests, 54 phased acceptance cases, 133 expanded evaluation cases, 204 related regressions/four opt-in skips, and 62 frontend cases passed; production build and touched-file Ruff passed. Selections are not added as unique coverage. See `docs/superpowers/qa/2026-10-19-assurance-reviewer-usage.md` and `.scratch/assistant-assurance/reviewer-usage-validation.json` for commands/source hashes/limits. No paid calls or independent domain approval; whole-phase requirements remain open.

### 2026-10-27 — P0 output contracts and coverage (A0.1–A0.3)

Policy version 1. `harness/assurance_coverage.py` maps every catalogue tool (341), Results tab (21), slide panel (23) and output channel (9) to a declared adapter naming effects, validity, progress, artifact and status. Only `get_component`, `update_component` and `ui_open_panel` hold checked adapters, and `smoke/assistant_inventory.py --check` fails on an unknown adapter, an unplaced safety tier or channel, a checked label off `harness.assurance.CHECKED_TOOLS`, or a stale map. Unchecked turns now send `session_init.assurance = {version: 1, mode: "legacy", state: "unvalidated"}`; `/api/chat/history` marks legacy turns `assurance_state: unvalidated` (checked: `checked`, expired: `unavailable`) and drops imported `receipt`/`requires_checked`.

Red/green: `tests/assurance_acceptance/test_p0_public_contract.py` 5 red / 31 green before the change, 36 / 36 after; `tests/test_assistant_inventory.py` 20 passed. Regressions: assurance acceptance, `test_answer_assurance`, `test_checked_answer_pilot`, `test_chat_history_per_user` passed.

Remaining: A0.4 reviewed request/profile/metric/computed/guard schemas (tickets 09–11); per-family behavioral fixtures for every `*_unvalidated` adapter (P1–P5); summary/compaction channels (P6); the frontend does not render `assurance_state` yet. Next runnable tracer: issue 02 evidence identity against a `read_unvalidated` family.

### 2026-10-28 — P1 tracer: list_components page evidence (issue 02)

Policy version 1, adapter `static_input_page_evidence` (`static-input-page/1`). `list_components` joins `harness.assurance.CHECKED_TOOLS`. A page's evidence holds the exact static inputs (`INPUT_METRICS`) of the rows it showed, by entity name at RFC 6901 paths `/<name>/<metric>`, bound to owner/org/project and `records.page_source_digest` (project id, class, every row name and its `INPUT_METRICS` raw values; GK B ruled out the whole-network fingerprint at 1.5–4.3 s per call). The digest is taken in `tool_guard` before the handler and must match at capture under `network_guard`, and again on every `/api/chat/evidence` lookup (else 409 `evidence_unavailable`). `tool_guard` refuses limit outside 1..`chat_tools.MAX_PAGE_SIZE`, negative or non-int offset (`capability_unsupported`, handler not run). A shown value certifies only on exact numeric equality with a finite raw value. Answers read "(page rows a..b of N, not the whole class)".

Red/green: the first tracer `test_listed_input_row_has_page_scoped_project_evidence` was red (verdict `limited`) before the change; `tests/assurance_acceptance/test_p1_evidence.py` 34 / 34 after (`test_page_evidence_does_not_compute_the_network_fingerprint` red, then green, after the GK NOT OK). Disabling either digest check fails the edit-after-capture and edit-between-handler-and-capture cases. Regressions: assurance_acceptance, test_answer_assurance, test_checked_answer_pilot, test_assistant_inventory, test_openai_tool_selection, test_tool_error_kind_manifest, test_harness_reliability, test_assurance_storage, test_checked_results_dispositions, frame contract, spill, live-network, profile binding, exact fingerprint.

Cost: two class digests per checked `list_components` call and one per lookup, linear in the class's rows. An edit to another class or a time series leaves page evidence valid: the page asserts static inputs only, and says so.

Remaining for P1: retained/historical runs (A1.3 as-of), A1.2 ACL revocation before publish, A1.4 corruption/restart, A1.5 complete-source aggregates, A1.6/A1.7 validity and masks, every other `read_unvalidated` family, frontend `assurance_state`. Next runnable tracer: a second `read_unvalidated` family with a time-series source (`get_timeseries`), or A1.2 revocation between capture and publish.

### 2026-10-28 — P1 tracer: get_timeseries series steps (issue 02)

Policy version 1. `get_timeseries` is the third checked read (`series_input_evidence`), for `loads/p_set` and `generators/p_max_pu`. `routers.network_time_axis.resolve_series` and `index_labels` were extracted from the handler, and the handler and the evidence both read through them. The time-series route tests (b3_solver_timeseries, chat_tools_profiles, network_time_axis_surface, user_ts_tenancy, profiles_router, e1_set_growth_placeholders, line_rescale, profile_batch_apply) gave 213 passed after the extraction. The source digest hashes raw bytes (index int64 ns plus tz, values float64), never repr.

The guard refuses a series of more than 744 steps, or one whose facts would exceed 120 KiB, with the typed `series_too_long_to_check`. It is `inline` in `tool-error-kinds.d/claude-assurance-02-timeseries.json`; that kind's text is the only guard refusal the checked projection passes through.

`tests/assurance_acceptance/test_p1_timeseries.py` has 36 cases: 17 red and 19 green on master (the green ones are refusals, which master gives anyway), and 36 / 36 after the change. `test_p1_revocation.py` gains the `get_timeseries` read in its revoked-after-capture cases (membership, disabled). Run it with `python -m pytest -q -o addopts='' tests/assurance_acceptance/test_p1_timeseries.py`.

Mutants, one at a time, each red on its own case:
- digest over `repr(series)`: the middle-step edit (rows 32/64 and 372/744);
- plain `==` for `_certified`: non-finite ±inf;
- no comma check: the comma args case;
- subset instead of exact keys: the period and extra args cases;
- no period in the path: the multi-period case;
- no series re-check at lookup: the edit-after-capture and time-zone cases;
- no size guard: the long-name case.

Next: SERIES-WINDOW (issue 02) for full-year series. REVOKE-KIND stays open.

### 2026-10-28 — P1 tracer: A1.2 revocation (issue 02)

Policy version 1. The probes found A1.2 already enforced, except for one existence leak. The checks are `records.authorize` at commit (twice, the second under `network_guard`), `records.load` on every lookup, the acting-user/restriction check in `tool_guard`, and the `unavailable` history projection. The leak: `records.load` answered a revoked project with 404 `Project not found`, while an unknown id got `Record not found`. It now answers both with `Record not found`. `tests/assurance_acceptance/test_p1_revocation.py` (11 cases) makes all of this executable. A fixture removes the seeded user's `OrgMembership` or disables the user; its yield teardown restores both and asserts the restore, and the module's last test checks it again. The late-tool-call case first shows the same call returns values without revocation. `RevokingProvider` (a `FakeProvider`) revokes just before a chosen model turn.

Red/green: on master, 10 / 11. The red case was `test_reads_after_revocation_look_like_unknown_records[membership]`, where the bodies differed. After the fix, 11 / 11. Mutants, one at a time:
- Removing commit's first `authorize` alone: green.
- Removing commit's second `authorize` alone: green. Each covers the other.
- Removing both: 8 red. The evidence lookup during validation then fails with `internal_error`, and nothing is published.
- Removing `load()`'s project `authorize`: the membership read case goes red.
- Removing `load()`'s first `authorize(owner)`: green. Global auth and the project `authorize` that follows both cover it.

Regressions: assurance_acceptance, test_answer_assurance, test_checked_answer_pilot, test_assistant_inventory, test_project_route_acl_policy, test_assurance_storage: 183 passed.

Pending in A1.2: CSRF on a stateful ACK (no assurance ACK route before P3) and a reviewer cache hit (the reviewer is withheld, so no cache). Follow-ups REVOKE-TOCTOU and REVOKE-KIND are in issue 02.

Remaining for P1: A1.3 historical as-of runs, A1.4 corruption/restart, A1.5 complete-source aggregates, A1.6/A1.7 validity and masks, the other `read_unvalidated` families, frontend `assurance_state`. Next runnable tracer: `get_timeseries` (a time-series source) as the third checked read.

### 2026-10-28 — P1 tracer: REVOKE-TOCTOU (issue 02)

Policy version 1. Commit checked access under `network_guard` and then saved, so a revocation landing between the two left the answer record on disk; a later step reloaded it, got 404, and the turn ended with `internal_error` ("404: Record not found"). Commit now re-checks `records.authorize` after `records.save`, still under `network_guard`, and on refusal removes the file (`records.discard`, under the per-owner transaction lock) and re-raises, so the turn ends as `assurance_publication_failed` with nothing published. A revocation after that re-check is ordered after a completed commit; `load` refuses every later read.

`test_access_revoked_during_commit_publishes_nothing` (membership/disabled × get_component/list_components × removed/stuck) wraps `records.save` to revoke right after the answer lands. It checks the record file first, then that the stream has no `answer_approved`, no token and no value, and that no log record carries a value or the draft. "stuck" makes the removal fail (`TimeoutError` from the lock): the refusal still ends the turn, the warning names only the record id, and `load()` refuses the leftover. Red/green: on master the 8 new cases are red, 11 / 19 overall (removed: the record is kept; stuck: nothing is withdrawn or logged); after the fix 19 / 19 (`python -m pytest -q -o addopts='' tests/assurance_acceptance/test_p1_revocation.py`). Mutants: removing the post-save re-check gives 8 red, with the record kept in the removed cases and no warning in the stuck cases; removing the OSError catch in `discard` gives 4 red, the stuck cases with no warning.

Next runnable tracer: `get_timeseries` as the third checked read. REVOKE-KIND stays open.
