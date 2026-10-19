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

LC-02A/B layout repairs are merged; LC-01A schema discovery is active as recorded below. Other implementation rows are not started.
Approval covers requirements; no row inherits a passing execution claim. LC-02's layout repair
may precede LC-01; its new resolved-context UX still depends on LC-01.

| Slice / issue | Resume scope | Required predecessors | Current state / evidence |
| --- | --- | --- | --- |
| [LC-02](issues/02-companion-interaction.md) | Later scope/plan/decision UX; layout repairs complete | New scope UX needs LC-01 | Layout PR369/dbc1fa7 and PR370/97867ad merged; PR370 native126pass/composer32pass/component188pass, remote/premerge and post-merge CI passed |
| [LC-01](issues/01-discovery-and-scope.md) | Eligible capability/schema discovery and exact target resolver; settle concrete DTO/route compatibility before coding | Existing harness and source rebaseline | LC-01A active in `claude/copilot-discovery`, base97867ad; paged component-schema tool first; LC-T01/02/06/17 remain partially pending |
| [LC-03](issues/03-results-and-navigation.md) | Typed evidence/navigation interfaces, then small current/retained domain adapters | LC-01 and applicable LC-02 | Not started; LC-T06–10/19 plus mapped LC-D suites |
| [LC-04](issues/04-generic-parameter-edits.md) | Exact schema/unit/basis previews and verified writes; storage/line examples first | LC-01 and applicable LC-02 | Not started; LC-T02–05 and domain edit cases |
| [LC-05](issues/05-ordered-workflows.md) | Bounded operation graph, verified dependencies and ordinary admitted execution | LC-03/04; existing durable receipts/jobs | Not started; LC-T04/05/10/12 |
| [LC-06](issues/06-family-adapters.md) | Every applicable domain/engine/control variant, delivered as small adapter PRs | Applicable LC-01–05 interfaces | Not started; LC-T16/17 and LC-D01–24 |
| [LC-07](issues/07-continuous-speech.md) | Offline lifecycle/resource wiring; then separately gated real media integration | Stable typed contracts; existing parent/utterance primitives | Not started; LC-T13–15; actual hosted/device gates pending |
| [LC-08](issues/08-release-evidence.md) | Source-pinned integrated evidence, per-capability rollout and rollback | Applicable implementation slices | Not started; LC-T16–19 and domain manifests; external gates pending |
| [LC-09](issues/09-vertical-slice-loadflow.md) VS-1 | Inside LC-03: “Show me the load flow results of the latest calculation” end to end | LC-01; LC-03 interfaces | Added 2026-10-19 ([vision review](vision-review-2026-10-19.md)); not started; OJ-01 closes LC-03 with OJ-02/03 |
| [LC-10](issues/10-focus-and-recency-resolution.md) | Inside LC-01: focus/recency ladder for “this”, “the”, “latest” (LC-C11); Q4 no-key shortcuts | LC-01B | Added 2026-10-19; not started; LC-T21 |
| [LC-11](issues/11-narrated-walkthrough.md) VS-2 | Inside LC-03: narrated walk-through, one read per tab, “something else” | LC-09 | Added 2026-10-19; not started; OJ-02/03 |
| [LC-12](issues/12-job-completion-continuation.md) VS-4 | Inside LC-05: 25 s wait limit, job completion into chat, stale labels after edits (LC-C12) | LC-03, LC-04 | Added 2026-10-19; not started; OJ-07/08/10 close LC-05; LC-T22 |
| [LC-13](issues/13-owner-journey-suite.md) | Owner-journey suite: Tier A in CI, Tier B manual capped live model (Q2) | First layer that closes a journey | Added 2026-10-19; not started; LC-T20/23 |
| [LC-14](issues/14-navigation-reach.md) | Inside LC-03: small navigation-reach fixes (snapshot, selection, edit card, Guided step, sub-sections) | None | Added 2026-10-19; not started |

### Owner decisions — 2026-10-19

Owner decisions, 2026-10-19 (relayed by the coordinator):
Q1 = keep the Codex order (LC-03 → LC-04 → LC-05); the vertical slices become
the acceptance targets that close those layers, not a reordering.
Q2 = a manual-trigger live-model tier with a hard cost cap, reported per
journey, never a merge gate. Q3 = an old→new card before every chat edit, in
both modes. Q4 = with a model available every sentence goes to the copilot;
the local tour and project picker work only without a key.
Layer closure: LC-01 on OJ-09 and LC-T21; LC-03 on OJ-01/02/03 (VS-1, VS-2);
LC-04 on OJ-05/06 (VS-3); LC-05 on OJ-07/08/10 (VS-4); LC-06 per adapter.
LC-01B owner: Q4 narrows the local results tour and project picker to the
no-key path. Details in [the vision review](vision-review-2026-10-19.md).

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

2026-10-18 LC-02A started in isolated branch `claude/chat-composer-pointer` from
actual master `6691bd671887210a84e6891a513835cad1723abd`. The older not-started
checkpoint above is historical. Next action: portable real-browser RED for the
composer hit target; no application code changed yet. LC-02B remains pending.

2026-10-18 LC-02A local implementation complete: intrinsic desktop content floor
and bottom-aligned dictation settings. Native browser RED8pass/8fail, then
14pass/2fail, finally16pass/0fail after both fixes; overlapping runs, never sum.
Component111pass and production build/type-check/static checks pass. Source
hashes, portable command and limits in the
[LC-02A QA record](../../docs/superpowers/qa/2026-10-18-chat-composer-pointer.md).
Private fixture data was removed. Native HTTP/auth/backend were real; no
provider/media calls. Current branch `claude/chat-composer-pointer`, starting
master `6691bd671887210a84e6891a513835cad1723abd`; review/PR/exact-head CI pending.
Locate its PR by exact branch and preserve running CI. Next action: finish
review and merge gates; next implementation after that is LC-02B overflow
attribution, followed by LC-01 discovery. Do not claim all LC-02 complete.

2026-10-18 review correction: added16required short600px browser cases; final
expanded runner32pass/0fail. Fixture refuses `backend/.env` before startup to
prevent native dotenv reintroducing provider keys; CLI refusal verified. Both
review findings addressed without further production changes; final review
confirmation/PR/exact-head CI pending. Historical16-case counts above remain
accurate for their iteration, not the final denominator.

2026-10-18 LC-02A verified merged as `dbc1fa7003a6a005b5b419e67c33920a341afe58`
in PR369; actual frontend tests/type-check, Unit, CodeQL/release and fresh-master
premerge passed, and all post-merge workflows subsequently passed. LC-02B now
active in isolated branch `claude/chat-narrow-layout` from that master.
Next action: native browser320px document/element geometry in five dock/
companion/Access states before choosing a layout repair. No product change yet.

2026-10-18 LC-02B repair checkpoint: active branch `claude/chat-narrow-layout`,
worktree `/workspace/chat-narrow-layout`, base `dbc1fa7003a6a005b5b419e67c33920a341afe58`.
First five-state RED attributed to unanchored screen-reader descriptions; dock
bounds then required a narrow overlay, preserved desktop width and flex shrink
at the640px breakpoint. Native Guided approval review found persistent ancestor
autoscroll, now bounded to the transcript. Tagged diagnostics removed.
Component135pass; final98 narrow/native decision cases plus32 existing composer
cases in progress. Source/commands/limits in the
[LC-02B QA record](../../docs/superpowers/qa/2026-10-18-chat-narrow-layout.md).
No branch push/PR yet; review/remote CI/premerge pending. Preserve actual state
rather than treating this intermediate checkpoint as completion. Next: complete
those gates, then start LC-01 in a fresh isolated branch from reviewed master.

2026-10-18 LC-02B expanded-layout checkpoint (supersedes initial five-state fix):
source remains unpushed in `/workspace/chat-narrow-layout`, current committed
implementation0a6c8326ef7bccfd99166c581b42f458d7dd01e1 plus uncommitted repairs.
Final98-case attempt97pass/1fail exposed native hidden-frame scrolling; a
focuspreventScroll experiment failed and was reverted. The actual overflow
owners are now repaired: responsive header wrapping/account alignment and a
keyboard-scrollable nonwrapping status row. The frame's existing clipping is
unchanged. Companion is bounded to the positioned workbench body so retained
answers cannot intercept header controls. Wrapped headers also required a
ChatPanel-height-bound dictation popover (old70vh Close failed3/32 cases).
Narrow pending confirmations temporarily hide the disabled composer with CSS,
keeping its state mounted and making room for reachable native decision buttons.
Current focused components141pass, separate dictation14pass; browser gates NOT
complete. No PR/push/CI yet. Temporary diagnostic case filters may be active:
before final verification require both Guided/Expert × all7viewports ×8states
(112cases), and restore the original32composer scenarios/denominator. Never
publish a filtered run as the complete suite. Portable sources and commands
remain in the QA record; final evidence, two-axis re-review and merge gates are
pending. Next implementation after this bounded fix remains LC-01.

2026-10-18 LC-02B final-source candidate: all temporary case filters removed.
Require Guided/Expert ×7viewport/pointer variants ×9states =126native-browser
cases, plus the original32composer cases. The added Choice state uses actual
ask_user, preserves picks bidirectionally through one mounted card/DOM portal,
verifies native multi-select/free-text turns and preserves an unsent draft.
Exact-choice-bound reveal/return state and allow_free_text remain authoritative
for this UI; dispatch stays the existing sendRequest path. A native9-state
short-touch probe passed. Final focused172tests/15suites and production build
pass. Full browser regressions are in progress; no PR/push/CI yet. The old
"filtered" warning above is historical, but verify source loops and denominators
before claiming acceptance. Next gates: final browser evidence, two-axis
source-pinned review, then PR/exact-head CI/fresh-master premerge/ordinary merge.


2026-10-18 LC-02B latest source checkpoint: branch `claude/chat-narrow-layout`,
worktree `/workspace/chat-narrow-layout`, committed source
`fc6940d06e6fe76c328920bb36ccaadaf88f6770`, still unpushed/no PR. Phone open
PropertiesPanel now overlays instead of forcing a540px workbench. A rejected
whole-canvas isolate would trap native dialogs; final source layers only the
MapModeSwitcher/SnapshotPicker widgets. Real Reset-dialog Cancel RED caught
that defect, then passed all seven Expert contexts. Guided opens Hub Design,
so extra canvas setup is Expert-only, while every126 Guided/Expert acceptance
state is still required. Current full run is
`/workspace/scratch/github-actions/narrow-final-native.log` (126pass/0fail; all seven Expert native Reset-dialog Cancel setups also pass).
Production source unchanged sincefc28935e:188component tests/18suites and
32/32ordinary composer browser tests pass; byte hashes verified. Both internal
source review axes approvefc6940d0 with zero findings. Retain final browser
reports/source hashes, finish evidence review, batch push once, create draft PR,
require all exact-head CI plus actual frontend Type-check/tests and Unit, then
fresh-master premerge and ordinary expected-head squash merge under owner's
direct authorization. No external contacts/paid calls/activation. Starting master
remainsdbc1fa7003a6a005b5b419e67c33920a341afe58. Next isolated task stays LC-01;
independent domain review and real-use calibration remain pending.


2026-10-18 LC-02B accepted local evidence:126/126native browser states at
fc6940d06e6fe76c328920bb36ccaadaf88f6770,32/32composer browser states at
fc28935e2dc8d215c65cf1cdf3570125dff606b0, zero browser errors in both. Every
retained SHA256 matches current application/build/runner bytes; production is
unchanged between those sources. Final component188pass/18suites. Raw reports
(without private owner/project/cookie data) are `evidence/narrow-layout.json`
and `evidence/narrow-layout-composer.json`; QA records commands, historical
failures and limits. Internal source review axes each zero findings. Latest
commit adds evidence/handoff only; no PR/push yet. Locate future PR by branch
`claude/chat-narrow-layout` and preserve running source CI. Next action: final
data-only review, one batch push/draft PR, require actual frontend Type-check
and tests plus Unit/all applicable checks, fresh actual-master premerge, then
ordinary expected-head squash under owner's direct authorization. Keep native
backend/path-filter skips distinct from executed backend/domain tests. No paid
calls, reviewer contact, activation or full-coverage claim. After this bounded
merge, stop; next isolated implementation remains LC-01 discovery.


2026-10-18 LC-01A active checkpoint: owner said “go” for generic discovery.
Fresh actual master97867adecd46098cd58c120728cf1eed693ed265, isolated worktree
`/workspace/copilot-discovery`, branch `claude/copilot-discovery`. PR370 merged
and all three post-merge workflows passed. Current open profile-generation
PR362–365/371 and process-unit PR338 remain unmerged; no installed adapters
are inferred from them. Reuse existing find_capabilities and on-demand value
reads. First new slice adds a bounded read-tier component-schema tool using
native PyPSA/request/asset metadata, with no new HTTP route or write grant.
Concrete interface, scope limits and approved public test seam are recorded in
`issues/01a-component-schema.md`. First ordinary-dispatch RED is running; no
production implementation yet. Finish that RED→green slice and appropriate
regressions/reviews/PR gates. Exact project/case/run/entity resolution, unsafe
named shortcuts, broader capability eligibility and service-schema families
remain separate LC-01 work; do not mark all LC-01 complete from this slice.
No paid calls, external reviewer contact, deployment or domain approval claim.


2026-10-18 LC-01A implementation candidate: paged read-tier get_component_schema
now uses actual request/PyPSA/asset metadata in every toolset. First ordinary
RED returned unknown_tool; green then passed43discovery tests including native
HTTP/SSE, all11 classes,4mocked external wire variants, paging/bounds/units,
strict refusal, personal rule denial and networkless cross-project value
isolation. Earlier1205neighbor tests pass; overlapping counts, never sum.
Details/commands and accurate pending gates are in
`docs/superpowers/qa/2026-10-18-copilot-component-discovery.md`. Inventory256,
15tabs/19panels; matrix170/14families, full_coverage not_established. Environment
briefly restarted; source and completed logs survived and were inspected.
Next action: final semantic/provider regressions and two-axis exact-source
review, then batch push/PR/all actual backend shards+pilot/QA, CodeQL,
inventory/control/release checks, fresh-master premerge and ordinary expected-
head squash under owner's standing authorization. No PR/push yet. LC-01 exact
resolver, named shortcuts, capability eligibility and service/domain schemas
remain pending; LC-02 generic-context UX still depends on them. No paid calls,
external contacts, activation or domain release certification.


2026-10-18 LC-01A source approved: cfd7e6cfb71f668b08f63e039d4f096670f825f9.
Both internal Standards/Spec reviewers approved with zero findings. Final local
combined semantic/provider632pass/2existing skips,124.12s; focused discovery
43pass. Never sum overlapping runs. Source/log byte hashes and commands are
in `evidence/component-discovery.json`; QA explains actual native/provider
seams and scope limits. This latest commit changes evidence/docs only.
Next: batch push once, draft PR against fresh master, require all exact-head
checks and actual four backend shards plus pilot/QA steps, then fresh-master
premerge and ordinary expected-head squash under direct owner authorization.
No push/PR or merge is claimed here. Do not rewrite the branch during CI.
LC-01B exact resolution/named shortcuts, richer eligibility and service schemas
remain pending; this slice does not close LC-01 or certify any domain result.


2026-10-19 LC-01A CI follow-up: PR372 remains draft/unmerged. Actual head
11f903a84b59cb5678fe331433d20250bd143027 and master97867adecd46098cd58c120728cf1eed693ed265
are unchanged. Full Test39158165700 failed shard1's shared error-kind manifest
guard because schema_field_too_large lacked a classification. The other three
shards, actual pilot/QA, CodeQL/inventory/control/release passed, but cannot
certify a replacement. No failed-source rerun or merge was attempted. Exact
existing guard reproduced in3.05s; one inline JSON classification fixes the
missing contract decision without changing emitter, service, limits or tests.
Backend manifest+discovery47pass/0skip; frontend manifest/profile260pass/2files.
Evidence/QA record commands/hashes/failure and scope. Next: refreshed two-axis
review, one normal batch push, require all new-head CI with actual backend
steps, fresh-master premerge, ordinary expected-head squash if every gate passes.
No paid calls, external contacts, deployment or new implementation scope.
