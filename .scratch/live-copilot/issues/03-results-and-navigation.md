# LC-03 — generic native evidence and navigation adapters

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: LC-01/02 and current native evidence stores

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C3/C4/C7; LC-T06/07/08/09/10/19.

Create typed common adapter interfaces and add one family per PR. Persist required immutable historical sections on completion; use matching UI/API/chat/export facts and source ACKs. Reuse existing selection actions. Legacy missing evidence refuses; never restore historical inputs or use current results for another run.

Expected ownership: run_evidence, native completion publishers, navigation, results DTOs/pages; domain-specific adapters reviewed separately.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.

