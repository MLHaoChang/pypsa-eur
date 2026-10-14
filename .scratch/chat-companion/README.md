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

Implementation continuation: [CP-01 progress/evidence](issues/01-truthful-state.md) records the first local slice, its newer source baseline and pending live/CI gates. CP-02 is next; the remaining spec/matrix cases are not implicitly implemented by CP-01.

The [Dots assessment](../live-voice/dots-design-assessment-2026-10-14.md) supplies the source-reviewed rationale. This folder contains an implementation specification, not an implemented feature or release certificate. Hosted setup, fresh paid-probe authorization, independent domain review and real-use calibration remain separate pending gates.
