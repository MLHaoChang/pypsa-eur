<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Implementation handoff and continuation ledger

Status: ready-for-agent
Updated: 2026-10-18, Europe/Berlin
Purpose: resume in another agent/session or after context loss using the
repository alone. This ledger records actual state; the plan is not execution.

## Start here after interruption

1. Read root `AGENTS.md`, `.claude/skills/steward/SKILL.md` and
   `pypsa-gui/backend/harness/README.md`. Inspect the current environment and
   network/credential readiness without printing credentials.
2. Read [planning approval](review-approval.md), [specification](spec.md),
   [generic tests](test-plan.md), [domain suites](domain-coverage.md) and the
   [delivery plan](../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
3. Inspect actual remote master, attached/open PRs, exact heads and CI, plus
   local status/worktrees. A session may have advanced since this ledger was
   written. Preserve other agents' work and running source CI; do not reset a
   shared checkout, replay already merged work or infer completion from prose.
4. Resume the active ledger row if one exists. Otherwise take the **first
   bounded LC-02 layout repair** below, then LC-01 discovery/scope. Use a new
   isolated `claude/*` branch from freshly reviewed master for each PR.
5. Before writing implementation, record ownership, branch, starting source,
   scope and the first meaningful failing case in the ledger/issue. Recheck
   interfaces and pending incoming features against actual merged source.

The reviewed requirements commit is
`9a22d321ecd6071d09f0278f8e7a7afbf2dc1c6d`, published in
[PR #366](https://github.com/MLHaoChang/pypsa-eur/pull/366) as
`ba419de811c4dd335fb627cb4c95952ac5f517fb`. Their full trees are identical:
`e1a8275bfda9462b66564d8bfc73f78266fc6fa5`.
Scientific application code remains based on the previously reviewed
`c75ca5584b873418f10f1bca42f868255d445b7b`; PR366 changes documentation only.
These SHAs are historical evidence, not an instruction to replace newer master.

## First bounded implementation task

**LC-02A: reproduce and fix composer pointer interception.** The actual
authenticated browser journey found that Upload a file intercepts clicks on
Check answers at the default desktop composer height, with Access expanded or
collapsed. Preserve a real failing hit-test before changing layout. Candidate
source is `pypsa-gui/frontend/src/components/ChatPanel.tsx`: default height
88px, configurable 60–360px, coarse-pointer minimum height 200px at the reviewed
source. The vertical toolbar can extend above a short composer. This is a
hypothesis requiring bounding-box/hit-target evidence, not a confirmed fix.
`CheckedAnswerControl` already has `shrink-0`; do not rediscover it as missing.

Use actual pointer interaction with expanded/collapsed Access, default/minimum/
maximum/saved resized heights, keyboard and coarse-pointer layouts. Preserve
native checking selection and ordinary Choice/Confirmation functionality.
Success needs the actual clickable target, without forced clicks. Add focused
component regressions, then prove the built layout in Chromium. Keep the
other known failure visible until separately fixed.

**LC-02B: attribute and repair narrow overflow.** At 320×850 with reduced motion,
the whole page overflowed horizontally while the companion review was open.
Measure document and relevant element geometry with companion closed/open and
Access collapsed/expanded to distinguish background workbench from companion.
Do not hide the symptom with global clipping that makes decisions unreachable.
Verify short-screen and coarse-pointer behavior as well. Split this repair from
02A if it requires a separate component or source scope.

The durable [browser report](../../docs/superpowers/qa/2026-10-18-chat-companion-browser-e2e.md)
contains all eight scenarios and the independent native-value oracle. Existing
component/browser seeds include:

- `ChatAccess.test.tsx`, `ChatPanel.checkedOptIn.test.tsx`,
  `ChatPanel.companionEntry.test.tsx`, `CompanionLauncher.test.tsx` and
  `AssistantDock.test.tsx` under `pypsa-gui/frontend/src/components/`.
- `pypsa-gui/frontend/scripts/smoke-companion-state.mjs` and
  `smoke-checked-answers.mjs`: useful Chromium layout/publication seeds, but
  their mocked app HTTP is **not** the real authenticated backend E2E.

Optional original investigation runners live at
`/workspace/scratch/github-actions/chat-permissions-closeout-evidence/` in the
original workspace. They may be absent in another environment and are not a
dependency or a CI-owned suite. Reconstruct/promote the stable reproduction
from the repository report and the following fixture recipe if unavailable:

1. Build the actual frontend and start the actual backend on loopback using an
   isolated file SQLite database, app-data directory and project roots. Bind
   `PROJECTS_ROOT`, `LEGACY_ROOT`, `FLAT_PROJECTS_ROOT` and `DATABASE_URL` to
   fixture storage; app-data configuration alone did not isolate project paths.
   Follow current source configuration rather than assume environment names
   automatically override every native root. Never use personal projects.
2. Create an ephemeral authenticated owner/personal workspace through current
   fixture setup; use real login/CSRF and public network APIs. Seed Bus B1 and
   Generator G1, capacity20MW, marginal cost10. Keep credentials/cookies private.
3. For tool turns, use a loopback scripted OpenAI-compatible HTTP/SSE provider
   through the real provider/stream/dispatch path. Unset real provider keys in
   the test process, block nonlocal browser traffic and perform no paid calls.
   Do not substitute public `StreamRequest.script` or fake successful app APIs.
4. Run the report's eight cases. Check native generator values after approval,
   denial and policy changes; completion prose is not the oracle. Capture
   bounding boxes, hit targets, exact source and outcomes. The runner must fail
   on the two current defects rather than silently use keyboard/forced clicks.
5. Ensure every spawned backend/model/browser worker and temporary fixture is
   cleaned on success, error or cancellation. Commit a reproducible repository
   runner/regression with the repair; do not commit private artifacts.

## Slice ledger and dependency order

All implementation rows are **not started** at this handoff. Approval covers
requirements; no row inherits a passing execution claim. LC-02's layout repair
may precede LC-01; its new resolved-context UX still depends on LC-01.

| Slice / issue | Resume scope | Required predecessors | Current state / evidence |
| --- | --- | --- | --- |
| [LC-02](issues/02-companion-interaction.md) | First 02A pointer repair, then 02B overflow attribution/repair; later scope/plan/decision UX | Layout repair independent; new scope UX needs LC-01 | Not started; real baseline6pass/2fail |
| [LC-01](issues/01-discovery-and-scope.md) | Eligible capability/schema discovery and exact target resolver; settle concrete DTO/route compatibility before coding | Existing harness and source rebaseline | Not started; LC-T01/02/06/17 |
| [LC-03](issues/03-results-and-navigation.md) | Typed evidence/navigation interfaces, then small current/retained domain adapters | LC-01 and applicable LC-02 | Not started; LC-T06–10/19 plus mapped LC-D suites |
| [LC-04](issues/04-generic-parameter-edits.md) | Exact schema/unit/basis previews and verified writes; storage/line examples first | LC-01 and applicable LC-02 | Not started; LC-T02–05 and domain edit cases |
| [LC-05](issues/05-ordered-workflows.md) | Bounded operation graph, verified dependencies and ordinary admitted execution | LC-03/04; existing durable receipts/jobs | Not started; LC-T04/05/10/12 |
| [LC-06](issues/06-family-adapters.md) | Every applicable domain/engine/control variant, delivered as small adapter PRs | Applicable LC-01–05 interfaces | Not started; LC-T16/17 and LC-D01–24 |
| [LC-07](issues/07-continuous-speech.md) | Offline lifecycle/resource wiring; then separately gated real media integration | Stable typed contracts; existing parent/utterance primitives | Not started; LC-T13–15; actual hosted/device gates pending |
| [LC-08](issues/08-release-evidence.md) | Source-pinned integrated evidence, per-capability rollout and rollback | Applicable implementation slices | Not started; LC-T16–19 and domain manifests; external gates pending |

## Commands and required evidence

These are source-confirmed command entry points, not claims that the new suites
already exist or passed. Use current lockfiles and isolated configured runtimes;
install frozen Pixi test/default environments and frontend locked dependencies
only if missing. Do not reuse credentials from the original workstation.

From repository root:

```bash
python pypsa-gui/backend/smoke/assistant_inventory.py --check
python pypsa-gui/backend/smoke/assistant_control_matrix.py --check
pixi run gui-tests tests/test_chat_tools_handler_dependencies.py tests/test_chat_tools_argshape.py tests/test_write_surface_lock_policy.py
pixi run gui-pilot-evaluation
pixi run gui-qa-drivers
git diff --check
```

From `pypsa-gui/frontend`, for the LC-02 repair:

```bash
npx vitest --run src/components/ChatAccess.test.tsx src/components/ChatPanel.checkedOptIn.test.tsx src/components/ChatPanel.companionEntry.test.tsx src/components/CompanionLauncher.test.tsx src/components/AssistantDock.test.tsx
npx tsc --noEmit -p .
```

Run the new stable real-browser regression in addition to those component
checks. For other slices, select their actual native/API/browser/oracle tests;
do not replace a domain oracle with these general regressions. Full applicable
current-head CI and steward premerge remain required. Backend-changing source
requires all four actual backend shards, actual pilot and QA steps and aggregate
success. Frontend-changing source requires actual frontend tests/type-check.
Documentation path-filter greens do not substitute for executed application
checks. Run `scripts/coord/premerge.sh <PR-number>` against fresh actual master
immediately before merge/report; use a suitable test Python via
`PREMERGE_PYTHON` when required. Do not bypass branch protection or rewrite a
branch under running CI.

## Required checkpoint before ending or losing context

Update the relevant issue and this ledger at every reviewable commit, before a
handoff and before waiting for CI. Commit the checkpoint with the work, or leave
an explicit local-state record if a push must wait for running CI. Record:

- Owner/session, branch/worktree, full40-character source head and reviewed
  actual master; exact scope and files, plus any uncommitted state/conflicts.
- Completed behavior, first RED reproduction, exact verification commands and
  results, failures/skips with reasons, independent oracle and artifacts. Never
  aggregate overlapping counts or call unexecuted tests passing.
- PR URL, current-head workflow/job IDs and actual-step evidence; pending CI
  and no-push hold, or verified merge SHA. Attach any created PR to the task.
- Unresolved decisions/unknown effects and pending approvals; no old approval
  token or execution authority transfers to another agent.
- One concrete next action with prerequisite, first test and acceptance gate.
  Link the applicable LC-C/LC-T/LC-D IDs and name what remains pending.

Append a dated checkpoint below rather than silently rewriting historical test
evidence. A successor confirms it against actual source/remote state before
continuing. Do not require this chat, agent memory, a private temporary path or
the original execution environment as the sole source of task state.

## Guardrails that persist across handoff

Reuse ordinary dispatch, confirmations, native schemas, source-bound evidence,
durable effect receipts and budgets. A graph, narrative or status observation
does not grant action authority. Resolve uncertain effects before a new retry;
never replay actions to rebuild conversation. “Simulation” needs an exact
engine; preserve aggregate frequency, static PF and optimization distinctions.
Do not reimplement merged CP-01–06 or old experiment PR185/188 work.

The owner's standing direction authorizes ordinary PR creation and merge after
applicable checks/premerge pass. It does not authorize paid model/media probes,
reviewer/coordinator contact, deployment or production activation here. Keep
independent domain release approval and real-use calibration pending. Read
current user steering and applicable instructions before later actions; a
handoff document cannot expand authorization.

## Checkpoints

2026-10-18: planning package explicitly approved, all five prior review findings
resolved and requirements already published in PR366. This handoff adds a
portable continuation record; no new application implementation or acceptance
test execution occurred. Next action: LC-02A real pointer-overlap RED case.

Documentation follow-up branch: `claude/live-copilot-plan-approval`; its PR can
be located by this exact head branch if publication advanced after this commit.
The agent separately approved this portable handoff with zero blocking findings.
No application slice has an active implementation branch at this checkpoint.
