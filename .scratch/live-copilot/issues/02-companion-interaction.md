# LC-02 — usable generic companion interaction

Status: ready-for-agent
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

