<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant assurance continuation handoff

Status: first local publication/static-input pilot implemented; current reliability
additions proposed and pending independent review. Publication pending. Read
[implementation status](implementation-status.md) for current supported behavior.
Historical tranche details follow; current continuation is below.
Baseline: master d43809ae. Dictation PR 106 has merged; existing Claude/tool
behavior remains the baseline. Do not reuse the old failed environment.

Read [spec](spec.md), [tests](test-plan.md), [implementation plan](../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md),
and harness README. Use existing immutable-run/durable-thread/job/UI designs in
`.scratch/live-voice/`; assurance is a shared publication layer over those services.

## Current source findings and observed controls

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

## Next actions

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
