# LC-08 — source-pinned whole-family and live release evidence

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: Applicable LC-01–07 implementation

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C10; LC-T16/17/18/19.

Exercise integrated real native/browser journeys for every released capability, prolonged/restart/provider cases and separately authorized real speech/device probes. Record denominators, source, skips and limitations. Keep implementation, fixture testing, vendor readiness, domain review, calibration and production enablement distinct.

Expected ownership: family journey/evaluation fixtures, release evidence/readiness and rollback documentation.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.

