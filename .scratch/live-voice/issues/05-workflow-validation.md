# Engineering conversation and release gates

Status: ready-for-agent
Type: task
Blocked by: 01, 02, 03, 04, 06, 07, 08, 09, 10, 11

Implement step 6 of [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Validate project creation/parameterization/solve/evidence/sensitivity/rerun/export
and GridSpine ranking/connection assessment with deterministic small real cases.
Add multilingual/correction/noise/interruption/provider-error cases and assert
real state/results, numerical fidelity and no duplicate writes. Run backend,
frontend, build and limited live fixtures; record sanitized evidence and blockers.
Validate the adopted hosted chained implementation first, including historical-run
questions, load-flow criticality, detailed follow-ups, correct tab/source/window/
asset selection and run-specific export. Optional native adapters later reuse
the same fixtures; they are not required to finish the first implementation.
Load flow and GridSpine are illustrative fixtures, not the scope. Use the full
coverage matrix: real journeys per family and exhaustive eligible tool/discovery/
navigation/parameter contracts. Include prolonged mixed-domain conversation,
rotation/compaction/restart/rebind/profile switching with exact references and
cumulative budgets, nonblocking reads during jobs, all domain monitoring adapters,
cursor recovery, baseline-isolated experiments, partial failures and results refresh.

Done when feature-flag release criteria in [spec](../spec.md) are satisfied. A
catalog listing, mocked transcript or successful dictation alone does not qualify.

## Comments
