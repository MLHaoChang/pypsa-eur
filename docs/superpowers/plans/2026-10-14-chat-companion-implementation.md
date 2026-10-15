<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Persistent engineering companion — implementation plan

Status: ready-for-agent
Date: 2026-10-14, Europe/Oslo
Baseline: master `4ac218cc98a1437950dd20c3f46233efac6cc159`
Implementation: tracked per slice below; this plan alone does not certify implementation, tests or readiness

## Start here

Read the [specification](../../../.scratch/chat-companion/spec.md), especially its authority/publication invariants and CC-01–CC-16 requirements. The [acceptance matrix](../../../.scratch/chat-companion/test-plan.md) is the pending evidence checklist. The [assessment](../../../.scratch/live-voice/dots-design-assessment-2026-10-14.md) explains the current gaps and reference sources.

First implement **CP-01**, then **CP-02**. These deliver truthful in-page chat and readable outcomes without new hosted media. Do not start a broad avatar rewrite or background effects to complete either slice.

Current continuation: CP-01 (#235) and CP-02 (#241) are merged. [CP-01 evidence](../../../.scratch/chat-companion/issues/01-truthful-state.md), [CP-02 evidence](../../../.scratch/chat-companion/issues/02-canonical-outcomes.md), and [bounded CP-03 implementation/evidence](../../../.scratch/chat-companion/issues/03-source-navigation.md) record tested boundaries and remaining gates. CP-03 (#268) covers retained native evidence and the current Expert branch-loading adapter; other current-tab adapters and historical harness completion ACKs remain follow-ups. CP-03 is merged at `5711a6fa0116731ab61cfa2f757ef2d2a5540703`. [CP-04a Activity observer](../../../.scratch/chat-companion/issues/04-activity-needs-you.md) is merged in #309 at `07f90f2316ebdc1f9e42e1fffc17ea22d3b30e6a`: read-only owned snapshots and a reconnecting panel. [CP-04b retained job observations](../../../.scratch/chat-companion/issues/04b-retained-job-observations.md) is merged in #311 at `76835bb3fcf3a66a949a8cca66099a83f545b8ae`. The current bounded slice is [CP-04c retained decision observations](../../../.scratch/chat-companion/issues/04c-retained-decision-observations.md): persistent question/confirmation inspection without restored authority. Wider source-gap/effect decision adapters, unobserved completion capture and wider native worker journeys remain pending. This plan remains the sequence; the linked issues are the execution records.

This plan supplements the [earlier assistant plan](2026-10-13-assistant-remaining-work.md). Its bounded prerequisites and first-network-case milestones are already merged in #185/#188. Reuse them; their residual two-case/reconciliation work is CP-05. Existing Live issues 01–12 keep their acceptance scope and remaining external gates.

CP-09 follows the [approved hosted speech architecture](../assessments/2026-10-10-hosted-assistant-architecture-decision.md): LiveKit Cloud WebRTC, Deepgram recognition, OpenAI hosted synthesis and the configured existing reasoning Provider through one scoped Harness bridge. Validate current adapter/model contracts before production selection. Realtime alternatives need a separately justified architecture decision; this plan does not silently replace the approved initial path.

Before each slice, fetch current master, read current AGENTS/Harness/ADRs and compare owned files with the pinned baseline. Reassess new source, preserve unrelated work and publish one reviewable slice at a time. A historical green result does not certify a replacement head or expanded capability.

## Milestones and dependencies

| Milestone | User-visible result | Slices | Release boundary |
| --- | --- | --- | --- |
| A — Truthful companion | Correct entry/state, readable source-bound answers and result navigation | CP-01–03 | Existing typed chat and reviewed dictation; continuous voice unavailable |
| B — Observable work | Activity/Needs-you, job recovery, isolated two-case comparison and effective permission visibility | CP-04–06 | Explicit ordinary work only; no unattended runner |
| C — Continuous voice | Mocked lifecycle and accounting, then configured hosted speech on the shared Harness | CP-07–09 | Real readiness requires separate configuration, probes and device evidence |
| D — Scoped responsibilities | Durable follow-through, bounded read-only schedules/notifications, inspectable preferences and external-tool readiness | CP-10–13 | Per-capability opt-in; hosted/remote authority remains constrained |
| E — Release evidence | Required family journeys, Provider compatibility and measured device/recovery behavior | CP-14 | Full coverage, live readiness and checked-domain approval have distinct gates |

CP-03 and CP-04 may proceed after their prerequisites without waiting for one another. CP-05/06 can proceed alongside Activity work. CP-07/08 can progress with mocks while deployment inputs are pending. CP-10 requires durable recovery, effective policy and resource admission, but not real audio. Parallel work must have explicit file ownership; this plan creates no sessions or delegation.

## Reviewable implementation slices

| ID | Scope | Depends on | Spec / acceptance | Completion criterion |
| --- | --- | --- | --- | --- |
| CP-01 | Truthful mode labels and orthogonal media/work state | Existing companion | CC-01,16 / CT-01–03,28 | Opening in-page text does not light a recording/speaking state; disabled voice is explained; existing Compose/Dictate/dock behavior and keyboard/touch/reduced-motion cases pass |
| CP-02 | Closed outcome and exact source/checking card | CP-01 | CC-02,03,11 / CT-04–07 | Closed companion delivers the actual bounded answer, source/check status and authenticated actions; missing, unsupported and stale evidence refuse honestly; checked text/projection never inherits an unrelated receipt |
| CP-03 | Source-bound walkthrough and shared current load-flow agreement | CP-02 | CC-03 / CT-08–10 | One controller navigates eligible views; matching displayed data is ACKed; manual navigation wins; current load-flow UI/API/chat share an oracle and convention; unavailable historical sections remain unavailable |
| CP-04 | Activity/Needs-you read model and cross-family monitoring recovery | CP-01,02 | CC-05,06 / CT-11–14 | Authorized snapshots and cursor recovery cover foreground/study/report/queue/GridSpine, including real-worker fixtures; decisions and job controls remain distinct; ordinary reads stay responsive |
| CP-05 | Explicit uncertain-case reconciliation, then two-case comparison | #185/#188; integrate CP-04 views when available | CC-06,07 / CT-15–18 | Reconciliation checks worker quiescence and actual effects; safe retry needs new ordinary admission; two-case success/failure policies and compatible recorded comparison/export pass public authenticated numerical cases; baseline unchanged |
| CP-06 | Effective-permission and decision UI | CP-02; CP-04 for inbox integration | CC-04,06,15 / CT-19–20 | Display current scope, blockers, limits and exact previews; denial/expiry/revocation are enforced by the backend; extra restrictions cannot lower native/MCP confirmation floors |
| CP-07 | Mocked continuous media lifecycle | CP-01,02,06 | CC-09–11,16 / CT-21–25,28 | Capture/playback events determine state; partials never execute; captions/barge-in/fallback and generation cleanup pass browser/API tests; mocks do not enable production voice readiness |
| CP-08 | Durable model/media/job resource reservations | Existing parent budgets; CP-07 contracts | CC-12 / CT-26–27 | Concurrent/duplicate/late usage settles once, unknown usage remains reserved and caps survive restart/reconnect/profile changes; no implicit paid permission |
| CP-09 | Hosted speech Provider and scoped bridge | CP-07,08; existing hosted architecture | CC-09–12 / CT-21–29 | Actual Provider contract implemented behind existing interfaces; scoped credential routes and failure modes pass mocks; real-live readiness stays pending until separately authorized capped probes, compatible devices and routing pass |
| CP-10 | Bounded durable task follow-through | CP-04–06,08 | CC-08,12 / CT-30–32 | Worker claims and resumes admitted work independently of the browser, checkpoints actual results and stops at decisions/uncertainty/budgets; no replay after crash/late worker and no hidden project rebinding |
| CP-11 | Read-only watchers, schedules and meaningful notifications | CP-04,06,08,10 | CC-13 / CT-33–35 | Scoped finite schedules with explicit zone/DST behavior, deduplication and expiry survive restart; unchanged states stay quiet; no new solve/send occurs without its own authority |
| CP-12 | Inspectable scoped preferences and context handoff | CP-02,06; integrate CP-10 | CC-14 / CT-36–37 | User can inspect/correct/delete owned preferences and explicitly share bounded context; assumptions retain source/approval; revocation/deletion and cross-project isolation pass |
| CP-13 | External-tool readiness/admin lifecycle and domain assurance adapters | CP-02,06; domain/Provider-specific contracts | CC-04,15,16 / CT-38–39 | Admin-listed external tools expose safe readiness/revoke/reconnect behavior; frozen catalogues and mandatory external-write confirmation remain enforced; each newly checked result/effect/artifact gets a separate independently tested adapter before admission |
| CP-14 | Family journeys, prolonged conversations and release evidence | Relevant prior slices | CC-16 / CT-40–42 | Every required released family/capability has source-pinned executed journeys; prolonged recovery and measured live-device results pass; pending external/domain gates remain explicit |

CP-05 can be two PRs: reconciliation first, then two-case advancement/comparison. CP-09 and CP-13 are series: one Provider/domain/artifact adapter per PR. CP-14 is a validation series per family; it is not one huge test commit. A slice is complete only for its declared implemented/verified capability, not for all descendants or external gates.

## Implementation ownership and existing seams

The following paths exist at the baseline; new service/DTO names are chosen during their slice and labelled proposed until implemented.

| Area | Primary seams and files | Change discipline |
| --- | --- | --- |
| Companion UI (CP-01/02/07) | `pypsa-gui/frontend/src/components/AssistantDock.tsx`, `CompanionLauncher.tsx`, `CompanionOutcome.tsx`, `ChatPanel.tsx`, `CheckedEvidenceCard.tsx`; chat/UI stores; `index.css` | Extend current surfaces; keep emitted state and authoritative publication separate; exact checked text is a safe first card implementation before a new compact projection renderer |
| Context/navigation (CP-03) | `components/liveWalk.ts`, `uiNavigate.ts`; `pages/results/LoadFlow.tsx`; backend `services/loadflow_summary.py`, `run_evidence.py`, `harness/navigation.py` | Replace competing timed/model navigation with one acknowledged source-bound path; historical capture is a separately scoped extension before historical claims |
| Activity/control (CP-04/06) | Frontend chat API/store, native task/job rows; backend `harness/job_adapters.py`, `services/assistant_tasks.py`, `durable_conversation.py`, queue/report/study adapters | Add a bounded Activity projection service/API over existing authority; no second mutable numerical store and no model-generated status parsing |
| Experiments (CP-05) | `harness/experiment_execution.py`, `experiment_contract.py`; `services/experiments.py`, `workflow_tools.py`, `run_evidence.py`; ordinary dispatch seam | Extend reservations and verified postconditions; preserve exact candidate/baseline proof and strict checked allowlist |
| Media/budgets (CP-07–09) | `harness/providers/live_media.py`, existing dictation Provider/API, `harness/budget.py`, durable parent budgets; speech hooks/utils | Streaming interfaces and scoped bridge are prospective; Provider details remain isolated; reviewed recording/dictation stays a separate existing flow |
| Responsibility runner (CP-10/11) | Native assistant tasks, durable parent, jobs, admission/confirm/event-log seams | Introduce worker scheduling/control as a separate bounded service; keep model-visible history through Event log and native effect execution through ordinary admission |
| Preferences/external tools (CP-12/13) | Owned settings/storage, `harness/mcp_client.py`, current external-tool/confirmation wiring | Server-owned policy and credentials; saved rules may restrict, not grant broader authority; preserve frozen session lists |
| Assurance (CP-02/13/14) | `harness/assurance*.py`, `effects.py`, `publication.py`, calculation modules and finite evaluation | Each new profile/effect/artifact has its own contract/oracle; current five profiles and unknown-tool refusal remain unchanged until explicitly extended |

## Per-slice procedure

1. **Rebaseline.** Inspect current master and applicable ADRs; record exact source, current gaps and owned files. Done when conflicts/new overlaps are understood and scope still matches the spec.
2. **Contract and RED.** Select that slice's acceptance cases and finalize proposed routes/records/frame changes. Write a meaningful public-seam failure with external inference/transport mocked. Done when it fails for the missing behavior, not collection/setup errors.
3. **Implement.** Use ordinary Harness authority and existing stores/adapters. Done when every selected case is green, authority/publication invariants hold, and required unavailable/uncertain paths are retained.
4. **Regress and document.** Run appropriate neighboring backend/frontend/browser checks; recheck aliases/schema/route/toolset/frame parity and generated inventory/control mappings when affected. Shared Harness changes require the expanded offline checked-answer gate. Record actual source/counts/skips; overlap counts are not summed.
5. **Live verification classification.** For applicable chat changes, follow ADR-0002's live-path probe requirement. With no newly authorized paid budget/setup, report live verification pending and keep affected live readiness gated. Done means the report distinguishes offline implementation from live verification; it does not waive the ADR.
6. **Reviewable PR.** Use an isolated branch and release-note fragment; explain final behavior, limits and validation. Let all required current-head checks finish. Run current-master semantic premerge checks for route/tool changes; confirm actual executed backend shards and pilot/QA steps, not a path-filtered green substitute. Honor existing user merge authorization and ordinary protection; source replacements require fresh evidence.
7. **Handoff.** Update the relevant issue/capability evidence and current handoff with exact commit and remaining gates. Done when implemented, mocked, live-tested, independently reviewed and production-enabled statuses are distinguishable.

Documentation-only edits need link/consistency/diff validation rather than unrelated application suites. Implementation tests may be authored per slice; the matrix below is not a request to add failing placeholders for all future APIs.

## Release checkpoints

| Checkpoint | Required evidence | Safe rollback |
| --- | --- | --- |
| A | CT-01–10 and affected accessibility/browser/publication cases; Compose/Dictate/history unchanged | Hide new surfaces; retain text/history/receipts and original dock |
| B | Actual family status, read responsiveness, exact source/effects and two-case failures; CT-11–20 | Stop new work admissions; preserve native jobs, read views and completed/uncertain receipts |
| C mocked | CT-21–28 including zero leaked tracks/playback/subscriptions and durable caps | End media and use typed input; no approval/effect replay |
| C live | CT-29 with configured Providers, current API contract, separately authorized budget, representative device/language/latency/cost results and pinned hosted routing | Disable media capability; preserve parent state, checked policy and native jobs |
| D | CT-30–39 including restart, duplicate triggers, policy revocation, scoped memory and external-tool floors | Pause runner/schedules; stop new triggers; retain task/evidence and inspectable records |
| E | CT-40–42 and all applicable standing coverage blockers; recorded Provider/adapter/source identities | Roll back the failing capability independently; no silent checked-mode downgrade |

## Dependencies that stay pending

- Hosted media/app worker routing, STT/TTS/LiveKit configuration and actual supported devices/languages. A ready reasoning credential alone is insufficient.
- Fresh capped paid-model/media probe authorization. Earlier one-run approval does not cover these slices.
- Independent engineering review and representative real-use calibration, deliberately pending by owner choice. Finite goldens cannot supply them.
- Wider domain/parameter/checked-artifact adapters and required family journeys. Inventory agreement and UI polish do not establish all-application correctness.
- Distributed authority, generic remote computer control and unsolicited external-channel actions remain outside the initial implementation contract.

No new implementation, runtime endpoint, paid call, deployment or production enablement is performed by this planning artifact.
