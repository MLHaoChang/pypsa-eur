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
The current continuation is [CP-06a access visibility](issues/06a-effective-permission-visibility.md).
Broader domain adapters and family journeys remain later work.

The [Dots assessment](../live-voice/dots-design-assessment-2026-10-14.md) supplies the source-reviewed rationale. This folder contains an implementation specification, not an implemented feature or release certificate. Hosted setup, fresh paid-probe authorization, independent domain review and real-use calibration remain separate pending gates.
