<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Independent review of the whole-application assistant plan

Date: 2026-10-10.
Reviewer: separate agent `/root/assistant_plan_review`, explicitly requested by
the user. This is a design/source review, not production certification.

Initial verdict: **CHANGES REQUIRED**. The reviewer supported the hosted,
single-planner, shared-harness architecture but identified six implementation
gaps. The primary agent revised the documents and requested independent re-review.

Final verdict: **APPROVED FOR PHASED IMPLEMENTATION**. All six initial findings
are explicitly addressed. The reviewer independently checked source inventory,
schema digests, routes, toolsets, result tabs, panel IDs and local links. One
nonblocking request to state issue 02's integrated-acceptance dependencies was
also applied. No production code or paid API calls were used for this review.

## Findings and changes

| Initial finding | Required adjustment in the accepted plan |
| --- | --- |
| Coverage too focused on load flow/GridSpine | Mandatory whole-application tool/GUI/parameter/job/evidence matrix, every eligible tool reachable through discovery and bounded offering, all result tabs and parameter editors mapped, real journey per domain family; load flow is only an example |
| Foreground solves occupy the sole harness turn | Stable job/task handoff, detached progress and released lease; verify a read is answered before slow-job completion while mutation locks remain enforced |
| Process-local ownership unsafe across workers | One authoritative application worker initially, owner-pinned bridge and stream/confirm/abort/history/task controls, fail-closed wrong-owner routing and tested restart; distributed coordination required before multi-worker support |
| Transient sessions insufficient for prolonged conversation | Durable parent goals/exact constraints/references/tasks/checkpoints/profile lineage/accounting; guarded child rotation and compatible switching; restart reconciles uncertain effects and invalidates approvals |
| Monitoring and refresh incomplete across domains | All asynchronous families use neutral adapters, authenticated cursors/snapshot recovery, revision invalidation and acknowledged updates; newer user navigation wins and monitoring does not consume model turns |
| Hosted profile/latency reliability unmeasured | Exact model/language/device/resource probes, endpointing/STT/model/tool/TTS latency distributions and dollar/duration/token accounting; acknowledgment is not a grounded-answer pass |

Mandatory [coverage contract](../../../.scratch/live-voice/application-coverage.md)
and [source inventory](../../../.scratch/live-voice/capability-inventory.json):
220 unique tools, 14 result tabs and 17 SlidePanel IDs. The inventory correctly
records the existing adequacy/FMEA navigation enum gap and pending behavioral/
GUI-control mapping. AR remains an unresolved user label for the module audit;
its meaning is not fabricated. Issues 08–11 make the new work independently
assignable alongside the existing shared bridge/UI/speech/evidence tasks.

## Final recommendation and release limits

Proceed with [hosted speech over the shared harness](2026-10-10-hosted-assistant-architecture-decision.md):
managed LiveKit Cloud media, replaceable hosted streaming STT/TTS, and one selected
existing Claude/OpenAI reasoning profile. Text and speech use the same evidence-
grounded answer and authorized tool path. No local LLM/GPU is required. GPT-Live
is a later optional adapter if measured overlap quality warrants it.

Scope includes all authorized application functionality: create/open/import,
parameterize, validate, run, experiment/refine, monitor, inspect every result,
navigate/edit, compare, report/export and resume prolonged conversations.
Application domain validity, account/project eligibility and ordinary execution
approval remain enforced. Existing functionality lacking a tool needs a shared
typed interface; declared limitations cannot be hidden by a full-coverage claim.

Approval permits phased implementation behind a feature flag. It does not mean
the feature is deployed or meets response targets. Whole-application release
requires the completed matrix, per-family real numerical/state/artifact fixtures,
nonblocking-job and durable-recovery tests, hosted credential/resource smoke tests,
language/device/accuracy/latency gates and the relevant backend/frontend/build checks.
No live conversational audio benchmark was performed for this recommendation.

Primary-agent verification: all 220 source schema digests/input maps/normalized
routes/toolset memberships match the inventory, all 14 tabs/17 panels match,
relative documentation links resolve and whitespace checks pass. These checks
validate design handoff consistency; they do not substitute for runtime tests.

Accepted [implementation plan](../plans/2026-10-10-live-voice-conversation.md)
and [spec](../../../.scratch/live-voice/spec.md).
