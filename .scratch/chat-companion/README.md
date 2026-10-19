<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Persistent engineering companion

Status: ready-for-agent
Planning baseline: master `4ac218cc98a1437950dd20c3f46233efac6cc159`

For companion outcomes/state, Activity/Needs-you, continuous voice or bounded background responsibility work:

1. Read [spec.md](spec.md) for authority, state and publication requirements.
2. Use [the implementation plan](../../docs/superpowers/plans/2026-10-14-chat-companion-implementation.md) for ordered slices/dependencies/ownership.
3. Select pending cases from [test-plan.md](test-plan.md), then record actual source-pinned execution during implementation.

Start with CP-01 truthful in-page chat state, then CP-02 readable source-bound outcomes. Core prerequisites and one network case are already merged; this plan extends their residual recovery/comparison behavior and the existing Live design.

Implementation continuation: CP-01 (#235), CP-02 (#241) and bounded CP-03 (#268) are merged. [CP-04 progress/evidence](issues/04-activity-needs-you.md) records the bounded Activity observer and the remaining durable-history/decision work. Linked issue records distinguish implementation, mocked tests, current-head CI and pending live/external gates.

Retained jobs (#311), decisions (#319), source gaps (#327) and explicit checked
source-gap follow-ups (#333) are merged. Bounded
[CP-04f native completion observation](issues/04f-native-completion-observation.md)
is merged in #351. [CP-05a uncertain-case reconciliation](issues/05a-uncertain-case-reconciliation.md)
is merged in #352, preserving the original submission reservation.
[CP-05b1 comparison/export](issues/05b1-case-comparison.md) is merged in #353.
[CP-05b2 shared two-case admission](issues/05b2-shared-two-case.md) is merged in
#354 (`fae95896500d6d74625e94e9f3a546d2d6b405af`), with common caps and explicit
stop/continue policy around separately approved native cases. All five post-merge
workflows (Test, CodeQL, inventory, control matrix and image build) passed.
The [CP-06a access visibility](issues/06a-effective-permission-visibility.md) slice
is merged in #356 (`e7777aa2e2c086789f1c802d387410a5280dc0cc`); its post-merge
Test, CodeQL and image workflows passed.
[CP-06b bound confirmations and complete engineering previews](issues/06b-bound-confirmations.md)
is merged in #357 (`3f45c0e7d9ad66ee74e6bd918d655419152f3e8f`).
[CP-06c saved personal project restrictions](issues/06c-saved-restrictions.md)
is merged in #358 (`8723578028bcb1952a36948dede20ebd09eb2b91`).
[CP-06d in-page access summary](issues/06d-companion-access-summary.md) is merged
in #361 (`c75ca5584b873418f10f1bca42f868255d445b7b`); its post-merge Test,
CodeQL and image workflows passed. The bounded CP-06a–d package is closed out in
the [permission handoff](../../docs/superpowers/handovers/2026-10-18-chat-permissions-closeout.md).
Durable policy history and wider effect previews remain separate CP-06 work;
broader domain adapters and family journeys also remain later work.

The owner's 2026-10-18 continuation prioritizes a generic whole-application
copilot: model/tool/parameter discovery, supported edits, ordered execution and
result explanation/navigation, then continuous speech over the same harness.
Use the [current assessment/spec](../live-copilot/README.md) and
[execution amendment](../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
The later real-browser journey's pointer/overflow failures are open UI work;
historical passing CI does not certify a complete generic companion.

The [Dots assessment](../live-voice/dots-design-assessment-2026-10-14.md) supplies the source-reviewed rationale. This folder contains an implementation specification, not an implemented feature or release certificate. Hosted setup, fresh paid-probe authorization, independent domain review and real-use calibration remain separate pending gates.
