# LC-07 — continuous speech on the shared harness

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: Stable typed paths and existing parent/utterance/usage primitives

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C7/C8/C9; LC-T13/14/15.

Complete mocked lifecycle and resource wiring, then provider-isolated LiveKit/STT/TTS integration under the approved hosted architecture. Finalized speech alone may request admission; preserve exact quantities, captions, interruption/fallback, scoped credentials, real cleanup and native confirmation. Vendor/device/readiness needs separate authorized evidence.

Expected ownership: speech adapters, scoped bridge/media routes, parent budget integration and browser media lifecycle; no second tool agent.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.

