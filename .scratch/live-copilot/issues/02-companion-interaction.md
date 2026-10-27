# LC-02 — usable generic companion interaction

Status: done (#369, #370, plan card PR below)
Progress: Composer pointer (#369), narrow layout and in-surface Choice/Confirmation (#370) merged; the plan card lists steps, states and applied changes (LC-02C).
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: LC-01 for resolved scope; browser layout repairs may land first

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C2/C7; LC-T10/11.

Fix recorded pointer interception and narrow review overflow without forced-click tests. Show understood scope, plan, sources, exact Choice/Confirmation controls and actual states in both surfaces. Preserve existing chat identity, checking selection, keyboard/touch and running work.

Expected ownership: AssistantDock, ChatPanel, ChatAccess, CheckedAnswerControl, CompanionLauncher and layout/browser tests.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.

## LC-02A implementation checkpoint — 2026-10-18

Owner/session: current Codex implementation session. Branch
`claude/chat-composer-pointer`, isolated worktree `/workspace/chat-composer-pointer`.
Starting actual master: `6691bd671887210a84e6891a513835cad1723abd`.
Scope: composer pointer interception only; LC-02B whole-page overflow remains
separate. First RED: real authenticated built-app Chromium hit testing and
normal Check answers clicks with expanded/collapsed Access and saved heights.
Native backend, isolated database/project and no provider/media calls. Promote
the reproduction to a portable repository browser runner before fixing layout.

LC-02A local result: intrinsic desktop content floor fixes pointer interception
without changing saved heights; bottom-aligned dictation settings fixes the
additional tall-touch Close failure. Portable real-backend runner32/32pass,
including16short-height cases;
component regressions111pass, production build/type-check and helper static
checks pass. See [source-pinned QA](../../../docs/superpowers/qa/2026-10-18-chat-composer-pointer.md)
and [native geometry evidence](../evidence/composer-pointer.json).
PR/remote checks pending. LC-02B overflow and remaining generic-context UX are
not complete. Next action: review, exact-head CI and fresh-master premerge.

Review follow-up: Standards had no blockers; its dotenv caveat is resolved by
refusing `backend/.env` before fixture startup (CLI refusal verified). Spec's
one short-height coverage finding is resolved by32passing browser cases across
normal/600px heights. No production source changed after the16-case pass.

## LC-02B implementation checkpoint — 2026-10-18

LC-02A merged in [PR369](https://github.com/MLHaoChang/pypsa-eur/pull/369) as
`dbc1fa7003a6a005b5b419e67c33920a341afe58`; post-merge Test, CodeQL and image
workflows passed. LC-02B begins from that freshly verified master in branch
`claude/chat-narrow-layout`, worktree `/workspace/chat-narrow-layout`.
First RED: real authenticated built-app document/element geometry at320px,
measured with dock/companion closed/open and Access collapsed/expanded.
No production layout change yet. Do not hide overflow with global clipping or
make approval/restriction controls unreachable; source/CI/PR evidence pending.


LC-02B local repair checkpoint: mode-switch descriptions anchored locally;
narrow dock fits over the relative workbench body, and desktop dock can shrink
without rewriting its saved width. Native Guided approval review also exposed
persistent ancestor autoscroll; transcript-only scrolling fixes that cause.
Initial five-state RED0/5, anchor-only3/5, overlay5/5; expanded68/70 exposed
ancestor movement. The640px breakpoint RED failed all three open-dock states.
These are overlapping iterations, never sum. Final98-case browser matrix and
ordinary32 composer cases are in progress; component135pass. See
[LC-02B QA](../../../docs/superpowers/qa/2026-10-18-chat-narrow-layout.md).
No PR/push yet. Finish real browser verification, two-axis review, then exact
source CI and fresh-master premerge. Next isolated implementation is LC-01;
remaining LC-02 generic scope/plan/source UX depends on it.

LC-02B expanded repair: actual header/status overflow owners now fit or scroll
locally, and companion content is bounded to the workbench body. Short-screen
dictation settings use available ChatPanel height. Native Choice RED also found
unreachable options; one mounted ChoiceCard now portals between surfaces with
controlled picks retained and an explicit allowed free-text/return route. Narrow
waiting cards reclaim composer space without unmounting state. Component172pass
across15suites; final126native-browser states and32composer regressions running.
No paid calls, global clipping substitution, forced clicks or policy changes.
Two-axis final review, PR, remote checks and premerge still pending.


LC-02B accepted local checkpoint: final126/126native-browser states and
32/32composer regressions pass, including native denial/read-back, multi-select
and free-text history, state-preserving surfaces, phone Properties reopen and
seven cancel-only Expert canvas-dialog checks. Component188pass/18suites;
production build/type-check/helper static checks pass. Final production source
fc28935e2dc8d215c65cf1cdf3570125dff606b0, browser harness sourcefc6940d0;
retained source hashes verified. See QA and `../evidence/narrow-layout*.json`.
Both source review axes zero findings. PR/remote CI/fresh-master premerge remain
pending; this does not complete generic-context UX or engineering validation.

## LC-02C plan card — 2026-10-27

Rebaselined on master `926b07d6d`. Already on master: understood scope and
sources for checked answers (`CheckedEvidenceCard`: run, entity, profile,
window, cost basis; `ProvenanceChip`), Choice/Confirmation in both surfaces
(#370), goal strip and rounds (`GoalStrip`), detached job progress. Missing:
the plan card (`AssistantResultCard`) showed only `status · n / m steps` and
the next tool, although each step's `_task` view carries the step list, the
brief phase, the waiting job ID and the applied edit receipts (`changes`).

RED: `AssistantResultCard.test.tsx` LC-02 cases (2 failed). GREEN: the card
lists each step with its state in words (to do, running, waiting for job
<job ID>, done, failed, needs review), marks the next step `aria-current`,
shows the brief's objective and phase, says when steps are left out, and lists
what was applied. Guided names steps with `guidedToolPhrase`; Expert by tool.
Both surfaces mount the same `ChatPanel`, so dock and companion agree.
Checks: AssistantResultCard, ChatPanel, continuation, sendRequest and
usePlanContinuation suites 215/215; `tsc -b` clean. No backend change.

Left for other packages: a generic "what I understood" line for unchecked
turns has no backend frame to show (LC-C7 residual); navigation/rebind races
(LC-T10) belong to LC-03/LC-10.

