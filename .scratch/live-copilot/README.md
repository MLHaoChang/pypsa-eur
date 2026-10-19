<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application live engineering copilot

Status: ready-for-agent
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Implementation: this folder specifies remaining work; it does not enable it.

Planning review: [explicit agent approval](review-approval.md), with no
unresolved blocking findings for the reviewed plan/specification/test
requirements. Implementation tests and domain release gates remain pending.
Continuation: [implementation handoff and progress ledger](implementation-handoff.md)
contains the first task, dependencies, commands and checkpoint requirements for
another agent or a session resumed after context loss.

The owner wants one in-page companion that understands and operates the whole
authorized application: models, tools, editable parameters, jobs and results.
Load flow, storage costs and “update lines, then run” are representative journeys.

1. Read the [source-reviewed gap assessment](gap-assessment.md).
2. Follow the [specification](spec.md), [acceptance matrix](test-plan.md) and
   [independently reviewed domain suites](domain-coverage.md).
3. Execute the [implementation plan](../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md),
   beginning with [LC-01 discovery and scope](issues/01-discovery-and-scope.md).

2026-10-19 addendum: a [review against the owner's vision](vision-review-2026-10-19.md)
adds [owner journeys](owner-journeys.md) OJ-01–12 and issues 09–14. The owner kept
the Codex order; each layer now closes on its journeys (see the review's decisions).

This is an execution amendment to the existing companion/live-voice plans, not a
second agent or replacement media architecture. Merged CP-01–06 prerequisites
remain in place. Current evidence supports bounded typed functionality; generic
results coverage and continuous voice remain incomplete. The permission browser
journey has recorded pointer-accessibility and narrow-layout failures; do not
call it fully green.
