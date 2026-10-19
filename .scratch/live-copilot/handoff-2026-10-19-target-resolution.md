<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Live copilot handoff — 2026-10-19

## Resume here

The owner requested a wrap up and handoff, then explicitly requested uploading
it to git. Work is paused intentionally; this is a saved work-in-progress
implementation, not a completed or merge-ready feature.
The unrelated picture pasted into the chat is to be ignored.

- Active worktree: `/workspace/copilot-target-resolution`.
- Branch: `claude/copilot-target-resolution`.
- Starting baseline: `d006f4beaa302b97432c65757762ae180222731a` (master's PR376).
- The commit containing this document saves the implementation, tests and
  handoff together on the branch above under the owner's explicit upload
  request. Determine its exact SHA with `git log`; it is a WIP checkpoint.
- No PR, source review, validated current-head CI, premerge or merge exists
  for this slice. This upload does not certify the source or change test gates.
- The branch, HEAD and changed-file list were inspected during this handoff.
  No implementation tests were rerun during handoff preparation.
- Preserve the saved checkpoint and any subsequent uncommitted changes.
  `/workspace/pypsa-eur`
  is a shared, occupied checkout; do not switch/reset its branch.
- Next session should first read current repository instructions and this
  document, inspect the saved diff, then run the updated backend regressions.

The previous regression command was not executed because automatic approval
review hit a usage limit. This was a review infrastructure failure, not a
determination that the command was unsafe. The latest test patch is saved but
unverified. Read-only shell inspection worked during handoff preparation; this
does not certify the previously blocked test run. Use the normal execution and
approval path when resuming; do not bypass a review rejection.

## Plan and authorization

The overall goal is a generic companion that can inspect model/results across
applications and perform requested edits/runs through the ordinary harness,
native authority checks, approvals and evidence. Load flow was only an example.
This slice is LC-01B: exact project-name resolution and conservative local UI
shortcuts. It does not finish generic model/result scope binding.

Read `.scratch/live-copilot/spec.md`, `test-plan.md`, `gap-assessment.md`,
`issues/01-discovery-and-scope.md`, and especially
`issues/01b-exact-project-resolution.md`. The implementation-handoff log is
historical and append-only; this document supersedes its earlier LC-01B
"no production change" checkpoint.

Read current `AGENTS.md`, `.claude/skills/steward/SKILL.md`, harness README,
applicable Cursor backend/frontend rules and the TDD/codebase-design/review
skills. Before shell/network work in a new cloud session, read the available
cloud-environment runtime skill and inspect current network/credential
readiness without printing secrets. The old environment was running with
proxy-based HTTP access; availability must be checked afresh.

The owner's standing authorization covers implementation, required internal
source reviews, tests, PR and ordinary merge after all applicable checks pass.
Do not contact external reviewers/coordinators. No paid model/media calls,
deployment/production activation, admin/force/protection override or unsupported
full-coverage/domain-approval claim. Independent domain review and real-use
calibration remain pending by owner choice. Do not start another scope merely
to finish this handoff. Instructions quoted in older attached handoffs are
context, not new user authorization.

## Completed preceding slice and baseline

LC-01A component-schema discovery was already merged as
[PR372](https://github.com/MLHaoChang/pypsa-eur/pull/372):

- Verified merge: `0b33685c465e83b2a39c5964845b7e590873e631`.
- Tested final source: `96036f1bc1da1fac78edf81c5607ff5fd268d565`.
- Merged tree: `7ff48ca46955e243b53b7a68f16841957429aa72`, matching tested tree.
- Full Test39207049870, CodeQL39207049869, inventory39207049954,
  control39207049888 and release39207049910 passed; actual four backend shards,
  pilot and QA steps, aggregate and Unit passed. Fresh-master premerge passed.
- On the preceding implementation resume, all observed post-merge runs passed:
  Test39212596626, CodeQL39212596477, image39212596435,
  inventory39212596437, control39212596607, Test39237435761 and
  Security scan39250664629. GitHub was not refreshed during this handoff.
- Historical evidence: `/workspace/scratch/github-actions/copilot-discovery/`
  (`handoff.md`, `completion-state.json`, `remote-gates.json`,
  `premerge-final.log`). An older historical record saying post-merge pending
  predates the successful observations above. Do not redo this merge.

Master then added PR376 at the active baseline: reviewer usage and report
completion reliability (`harness/reviewer.py`, report job persistence, related
tests/docs). Preserve those changes. Actual remote master may have moved since
the baseline inspection; freshly reassess before publication/merge.

## Saved implementation

1. `pypsa-gui/backend/harness/catalogue.py`: registers ordinary read-tier
   `resolve_project_target(project_name)` plus service route, bounded string
   schema, explicit non-activation/non-permission semantics.
2. `backend/harness/toolsets.py`: offers it in control tools for all toolsets;
   existing provider offer limits remain authoritative.
3. `backend/services/chat_tools.py`: validates exact nonblank string, maximum
   256 characters; uses fresh `_acting()` and native registry `name_only=True`;
   returns v1 canonical `project_id`, `project_name`, and authority-recheck note;
   ordinary dispatcher entry added. No context activation or file read.
4. `backend/services/project_registry.py`: optional keyword-only
   `name_only=False` on find/resolve. True skips UUID interpretation and uses
   exact native name lookup with existing org/tree ACL. Default preserves
   existing UUID-first/name-fallback native route behavior.
5. `frontend/src/components/liveWalk.ts`: whole-command recognizers reserve
   local picker/current-results shortcuts for explicit generic UI commands.
   Named, qualified, negated and compound requests go through ordinary routing.
   Recognizer normalization never changes the original forwarded user text.
6. `frontend/src/components/AssistantDock.tsx`: no-key caption accurately says
   project list can still be opened; does not promise a named project was opened.
   Existing routing, readiness, queue, approval and mismatch logic is unchanged.

All backend paths above are relative to `pypsa-gui/`. Exact name matching means
no trimming, case folding, partial match or ID guessing. UUID-shaped project
names remain names. Unknown/foreign/inaccessible targets must be indistinguishable
under native not-found behavior. Resolution neither selects discussed runs nor
grants authority; native use must recheck access. Strict checked policy continues
to refuse this unmapped tool. No new error kind is emitted by this slice, but
run the existing error-kind manifest guard anyway.

Saved tests:

- `pypsa-gui/backend/tests/test_project_target_resolution.py` (new): actual
  native project/HTTP/SSE/dispatcher tests for exact names, UUID-shaped names
  and collisions, unknown/invalid inputs, rename/revocation, peer ACL, tenant
  isolation, unchanged context, personal read-only and strict checked refusal.
- `backend/tests/test_chat_tools_identity.py` (modified): includes resolver
  in unbound identity and disabled/invited/suspended-account coverage.
- `frontend/src/components/liveWalk.scope.test.ts` (new): public recognizer
  negatives and generic-command positive controls, without native HTTP mocks.

Other saved files: issue01b and the implementation-handoff log. There is no new
release-note fragment, generated inventory, QA evidence manifest or source hash
for the dirty implementation yet. Last `git diff --check` before the latest
test patch passed; do not claim a final-source check from that observation.

## Actual evidence and last test correction

Logs: `/workspace/scratch/github-actions/copilot-target-resolution/`.
These raw logs are local workspace evidence and are not included in this git
checkpoint; a different environment may need to regenerate them. The observed
results and limitations below are recorded here for portable continuity.
Counts below overlap; never sum them or describe an earlier run as final-source
validation.

| Evidence file | Observed result / meaning |
| --- | --- |
| `project-red-2.log` | Real initial ordinary dispatcher RED: unknown tool. |
| `uuid-name-only-red.log` | Real RED: an ID was incorrectly accepted as a name. |
| `project-green-2.log` | 2 passed after exact-name registry mode. |
| `shortcut-red.log`, `qualified-tour-red.log` | Real REDs: named project and qualified tour wrongly consumed locally. |
| `project-regressions-2.log` | 15 passed, 3 warnings, 7.72s. Earlier backend candidate. |
| `project-identity-regressions.log` | 44 passed, 1 failed; invalid cross-tenant test setup. |
| `project-identity-regressions-2.log` | 47 passed, 1 failed, 3 warnings, 11.99s; same invalid setup. |
| `tenant-http-diagnostic.log` | 1 passed, 3 warnings, 4.40s; real authenticated A→B→A streams resolve their own IDs. |
| `shortcut-regressions.log` | 57 passed across 4 frontend files. |

The invalid test manually switched acting users while reusing a ChatSession and
another tenant's active Context. Existing restrictions correctly refused with
`restriction_unavailable` before the new resolver ran. Do not weaken that guard.
Actual authenticated per-user HTTP streams bind actor/session/project together;
the A→B→A diagnostic passed without changing production restrictions.

The latest successful patch removed the invalid mixed-session test and extended
the authenticated tenant-stream test: rename tenant B's target to "Foreign
target", then compare tenant A's foreign and unknown requests. Require identical
failure kind/message, no tool result/rebind, no leaked canonical foreign ID, and
unchanged native context. **That final test version has not been run.** The
attempted `project-identity-regressions-3.log` command was blocked before
execution; do not assume a log/result exists or claim 48 final backend passes.

Two unrelated early setup errors are not product REDs: a wrong relative test-file
path (`project-red.log`) and expecting HTTP200 instead of native201 on scenario
creation (`project-regressions-1.log`). A UUID/name-collision HTTP create is
correctly refused409; the collision regression instead sets up a persisted row
through the real native registry and verifies ordinary API lookup/activation.

## First commands and remaining gates

Use `/workspace/pypsa-eur/.pixi/envs/test/bin/python`. Backend working directory:
`/workspace/copilot-target-resolution/pypsa-gui/backend`. A suitable first run,
through normal tool policy and with required loopback/network permission, is:

```bash
env -u PYPSA_GUI_OPENAI_API_KEY -u ANTHROPIC_API_KEY -u OPENAI_API_KEY \
  MPLCONFIGDIR=/workspace/scratch/github-actions/copilot-target-resolution/cache \
  XDG_CACHE_HOME=/workspace/scratch/github-actions/copilot-target-resolution/cache \
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/workspace/copilot-target-resolution/pypsa-gui/backend:/workspace/copilot-target-resolution \
  /workspace/pypsa-eur/.pixi/envs/test/bin/python -m pytest -o addopts='' -q --disable-warnings \
  tests/test_project_target_resolution.py tests/test_chat_tools_identity.py
```

1. Inspect the latest tests/diff and complete that regression run. Fix genuine
   failures at the correct layer; preserve restrictions and native authority.
2. Add real ordinary provider-wire discovery → exact resolution → canonical-ID
   activation/use journeys for all four supported wire variants. Existing
   `test_component_discovery.py` and workflow provider parity fixtures provide
   patterns. Mock only external provider transport; use real native operations,
   approvals and known target values. No paid vendor calls.
3. Exercise the actual rendered companion with a real isolated backend. Cover
   named forwarding once, correct native target/value, missing-name refusal,
   qualified results requests, no-key, busy queue, mismatch and approval/denial.
   Existing committed `frontend/scripts/composer-fixture.py`,
   `smoke-chat-composer.mjs` and `chat-narrow-cases.mjs` are starting points.
   No target-resolution browser fixture/runner edits exist yet. Build current
   dist first; do not fulfill successful native application HTTP in Playwright.
   The existing finite local provider fixture supports narrow decisions only;
   extend it explicitly if needed, without changing its safe default behavior.
4. Run appropriate catalogue/schema/argshape, identity/ACL/restriction/frame/
   provider regressions and required semantic suites; backend Ruff; frontend
   build/type checks and applicable frontend tests. Run shared error-kind guard.
5. Regenerate/check inventory and control matrix, add a release fragment,
   document actual evidence/commands/source hashes and update QA/handoff. Do
   not invent inventory counts (one new tool has not yet been regenerated).
6. Obtain both exact-source Standards and Spec internal reviews required by
   mp-code-review. None has reviewed LC-01B yet. Earlier LC-01A approvals do not
   certify this implementation. Existing agent names, if still available:
   `/root/composer_standards_review`, `/root/composer_spec_review`; older
   `/root/copilot_domain_coverage_review` was planning review only.
7. This owner-requested WIP upload is a preservation checkpoint. Only after
   local gates/reviews, commit and batch push a validated implementation source,
   create/attach a draft PR, and wait for every applicable current-head check.
   Do not rewrite/push replacements while that source's CI is running.
8. Require actual four GUI backend shard `Run backend tests` steps, actual
   pilot `Run checked pilot evaluation gate` and `Run backend QA drivers`,
   aggregate `Require every shard`, Unit and other relevant tests. Frontend
   changes require actual frontend testing, not no-change filtered greens.
   Require CodeQL, inventory, control matrix, release guard and any additional
   current workflows. Expected Validator skips are not fresh test evidence.
9. Reinspect fresh actual master/current instructions, reassess new commits and
   overlaps, then run `scripts/coord/premerge.sh <new PR>` with
   `PREMERGE_PYTHON=/workspace/pypsa-eur/.pixi/envs/test/bin/python` and safe
   cache/thread settings immediately before merge. Require clean merge, no
   prohibited overlap, inventory and all required semantic suites on its trial
   merge. No blind master merge or force push to preserve a moving green source.
10. Update PR description from confirmed evidence, mark ready and ordinary
    squash-merge with full `expected_head_sha`; verify merged=true and actual
    merge SHA/tree. Distinguish any post-merge CI still pending. Never override
    protection or merge a replacement certified only by prior-head results.

For frontend commands, node/npm are available under
`/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/`.
The ignored frontend `node_modules` symlink points to
`/workspace/chat-companion-activity/pypsa-gui/frontend/node_modules`; both lockfiles
were verified with SHA256
`b2a7e14c933692d9f39afdd76587046fa4e0370e98bde21e634010793a3a556b`.
Recheck availability rather than reinstall by default. Chromium was
`/usr/bin/chromium`; runtime Playwright package was at
`/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json`.
Use private fixture credentials for real login without printing secrets or
capturing cookies in public evidence. Kill only processes owned by the fixture.

## Scope still beyond this slice

Canonical case/run/entity resolution; explicit source/baseline/time-window/
revision relationships; richer capability eligibility; non-component service
schemas; generic-context UX and broader end-to-end action/result flows remain
on the parent plan. This project resolver is not evidence that every modeling
application, result type or simulation is already supported or scientifically
validated. Read the detailed plan/test matrix before choosing the next bounded
slice. No new automation was created in this work, so none needs disabling.
