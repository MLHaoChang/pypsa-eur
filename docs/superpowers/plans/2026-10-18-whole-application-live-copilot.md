<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application live copilot — remaining implementation plan

Status: ready-for-agent
Date: 2026-10-18, Europe/Berlin
Reviewed master: `c75ca5584b873418f10f1bca42f868255d445b7b`
Plan only: remaining functionality is not implemented by publishing this file.

## Goal and relationship to existing work

Deliver the owner's generic live engineering companion: discover the authorized
model and its parameters/tools; explain and navigate supported result families;
make confirmed edits; and execute ordered commands such as “update storage
costs” or “update these line parameters, then start simulation.” Load flow and
specific component classes are test examples, not the product boundary.

Read the [gap assessment](../../../.scratch/live-copilot/gap-assessment.md),
[specification](../../../.scratch/live-copilot/spec.md) and
[acceptance matrix](../../../.scratch/live-copilot/test-plan.md).
The owner-requested independent review adds the normative
[24 domain suites and 14-family mapping](../../../.scratch/live-copilot/domain-coverage.md).
The generic cases alone were insufficient: capacity planning, static electrical
analysis, distinct reliability engines, controller models and actual aggregate
time-domain frequency simulation need separate oracle-backed coverage.
This plan refines remaining CP-03/06–14 and live-voice issues 01–11 in the
[existing plan](2026-10-14-chat-companion-implementation.md); do not reimplement
merged conversation, navigation, permission, case or recovery primitives.

## Delivery sequence

| Slice | Concrete deliverable / principal files | Dependencies | Done when |
| --- | --- | --- | --- |
| [LC-01](../../../.scratch/live-copilot/issues/01-discovery-and-scope.md) | Eligibility-aware discovery of models/entities/parameters/tools using catalogue/toolsets, asset/service schemas and actual feature revisions; exact project/case/run resolution before local shortcuts | Existing harness/ACLs | LC-T01/02/06/17 pass; ambiguous/foreign names clarify/refuse; “open project X” and named-result tours cannot silently use previous scope |
| [LC-02](../../../.scratch/live-copilot/issues/02-companion-interaction.md) | Companion context/intent/plan/decision UX; repair actual composer checkbox overlap; preserve dock/floating parity. Files: AssistantDock, ChatPanel, CompanionLauncher, ChatAccess, CheckedAnswerControl, navigation handlers | LC-01 for scope; layout fix can land first | LC-T10/11 and ordinary/checked outcome regressions pass in a real browser; all decisions usable on both surfaces; no false voice/readiness indicator |
| [LC-03](../../../.scratch/live-copilot/issues/03-results-and-navigation.md) | Typed result-adapter registry, immutable retained-section capture and source-bound UI ACKs; native publishers, run_evidence, result DTOs/pages and navigation | LC-01/02 | LC-T06–10 pass for each released adapter. Start with shared core time-series/asset/branch infrastructure, then one adapter per result family; every unsupported adapter remains explicit |
| [LC-04](../../../.scratch/live-copilot/issues/04-generic-parameter-edits.md) | Generic parameter selection over native schemas; adapter-specific basis validation, exact preview and verified native postcondition/effect receipt | LC-01/02 | LC-T03/04/05 pass for storage costs and line parameters; map the rest of the applicable schema families without granting unknown fields |
| [LC-05](../../../.scratch/live-copilot/issues/05-ordered-workflows.md) | Bounded durable operation graphs over ordinary tools/jobs; dependency/revision checks, leases/budgets, exact receipts and resume/reconciliation | LC-03/04 and existing experiments/jobs/parent stores | LC-T04/05/10/12 pass. Dependent solve starts once after verified edits and its own admission; partial/unknown writes stop advancement; browser loss does not replay work |
| [LC-06](../../../.scratch/live-copilot/issues/06-family-adapters.md) | Domain and operation adapters using the common interfaces, with all 24 domain suites mapped to actual engine/control variants | LC-01–05 | LC-T16/17 and applicable LC-D01–24 pass per released capability; matrix links actual tool/parameter/control variants to independent oracles and executed journeys; pending variants remain pending |
| [LC-07](../../../.scratch/live-copilot/issues/07-continuous-speech.md) | Mocked media lifecycle and durable usage wiring, then actual hosted vendor/LiveKit adapters through the same harness; finalized utterances, captions/playback, interruption and fallback | Stable typed paths; existing parent/utterance/usage primitives | LC-T13–15 pass offline first; then independently authorized real provider/device/resource tests. Continuous voice remains unavailable until actual readiness gates pass |
| [LC-08](../../../.scratch/live-copilot/issues/08-release-evidence.md) | Integrated whole-family journeys, prolonged/recovery/provider parity, measured speech UX and staged rollout/rollback | Applicable LC-01–07 slices | LC-T16–18 have source-pinned evidence for every released claim. Independent domain review/calibration and production enablement remain separately recorded |

LC-03, LC-04 and LC-06 are series of small reviewable adapter PRs, not one large
all-domain patch. LC-07 lifecycle mocks can progress independently once its
interfaces are fixed; real speech should operate the proven typed commands.
Each slice uses one isolated branch and its own issue/evidence record.

## First runnable scope

First fix and retain the actual browser pointer-overlap reproduction. Then
implement LC-01's generic discovery/scope seam, including storage/line/result
examples, with public API and browser RED/GREEN cases. No full-network prompt
dump, inferred parameter names or a new separate voice agent.

The first functional milestone is **generic typed interaction**: discover and
read eligible models/parameters, clarify exact targets, edit storage costs with
verified read-back, and update line parameters then start one admitted native
simulation. That milestone includes result/source context and basic workflow
status; it does not wait for hosted audio.

The second is **generic evidence-bound explanation/navigation** across the
released result families, including retained cases where full evidence exists.
Retained load-flow capture is one early adapter because the owner's example
currently cannot be certified from summary-only run records. Missing legacy
sections remain unavailable; do not fabricate backfilled history.

The third is **continuous speech over the same capabilities**, preserving the
[approved architecture](../assessments/2026-10-10-hosted-assistant-architecture-decision.md).
Use existing durable reservations and utterance leases; do not rewrite them or
select new vendor/model IDs without validating their actual contracts.

## Common implementation design

1. **Discover:** derive an eligible capability/parameter catalogue from actual
   server schemas, source revisions and native adapters. Keep provider offers
   bounded, and fetch scoped model values on demand.
2. **Resolve:** turn natural labels into exact authorized IDs and explicit
   units/basis. Display the interpretation; ask a Choice question for ambiguity.
3. **Plan:** store a finite typed graph referencing native operations and
   revisions. The model proposes a plan; server policy determines admission.
4. **Confirm and apply:** ordinary dispatch checks ACLs, locks, personal rules,
   checked support and exact arguments. Native adapters validate previews and
   effects. Waiting/queued work is rechecked before pickup.
5. **Verify and advance:** dependent work requires native read-back/receipts and
   current preconditions. Unknown or partial effects stop, with explicit review
   or reconciliation. A tool result sentence never proves completion.
6. **Explain and show:** obtain typed fact/evidence envelopes, render the same
   numerical source in UI/chat/export and later speech, and acknowledge exact
   displayed data. Manual navigation wins.
7. **Observe and recover:** native status adapters and durable cursors publish
   meaningful changes without model polling; reconnect refreshes refs/credentials
   and never replays an approved effect merely to reconstruct conversation.

LC-T19 applies to every released engineering summary: the model cannot add
unsupported numerical claims to an otherwise valid fact set. Extend existing
checked publication/receipt contracts per adapter and keep unavailable claims
explicit. Ordinary legacy chat is not a substitute for that numerical gate.

## Coverage and incoming features

Use the current 14-family matrix as the work queue, expanded by LC-D01–24.
One representative family journey cannot close multiple mathematical engines,
control models or edit/action variants. For each applicable family,
record discovery, parameter/units/editability, native reads, supported edits,
previews, effects/jobs, result/source adapters, UI actions, exports and recovery
with specific fixtures. A family with no editable capability says not applicable
with a reason. No family becomes validated merely because another uses the same
wrapper. Preserve Guided/Expert and ordinary/checked distinctions throughout.

Prioritize LC-D04–07 (capacity, multi-period, solver and load flow), D09
(storage/edit economics), D11–13 (separate reliability engines) and D14–17
(static electrical, controls and actual time-domain screening). Each adapter
PR records the annex's source/operation/variant manifest and negative cases.
These are small independently tested slices within LC-03–06, not a new monolith
or permission to rewrite scientific engines. Resolve the intended simulation
engine before the owner's edit-then-run workflow; native optimization does not
establish that an AC or dynamic property was assessed.

Actual frequency screening already integrates an aggregate ODE at sub-second
resolution. Expose that supported capability while preserving its absence of
voltage dynamics, inter-unit oscillations and detailed current-limit dynamics.
General EMT/RMS/PLL certification remains unsupported. Similarly, adequacy
adapters expose each engine's own topology, chronology and uncertainty limits;
one reliability label must not erase those distinctions.

Current pending renewable-profile PRs362–365 are engine/cache/source/preset work;
their planned generate/apply tools require ordinary admission, pending-artifact
identity, provenance and exact apply receipts when they actually merge. Process-
unit schemas and Guided result/cost/report changes also require fresh mapping.
Inspect actual master and open-PR heads at the start of every slice. Do not
blindly merge pending branches or advertise them as existing capabilities.

## Verification and release discipline

Write meaningful tests at the agreed public API/SSE/browser/native-worker seams
before implementation. Reproduce one missing behavior at a time and preserve
negative/refusal cases. Exact expected numbers come from independent fixtures,
not the implementation under test. Consistency across shared native UI/chat/
export facts does not replace a separate analytical or benchmark oracle.
Keep the real browser failure recorded until
fixed; keyboard success does not certify pointer/touch accessibility.

The local E2E uses a real authenticated backend/database, native handlers and a
loopback-only OpenAI-compatible model fixture. This proves more of the path than
mocked HTTP UI tests, but does not verify hosted model intent accuracy or speech.
Require actual current-head CI steps for application changes and fresh-master
premerge under steward rules; documentation-only validation is separate and
does not create fresh application-test evidence.

Expose readiness per capability: implemented, exercised with a wire fixture,
exercised with an actual provider/device, independently reviewed, calibrated and
production-enabled are separate states. Define and measure acknowledgement,
first grounded answer, navigation, first audio and interruption latency at the
real-use gate rather than promising unmeasured latency. Use finite-suite false
acceptance/rejection, wrong-source, duplicate-effect and incomplete-task metrics
with denominators; no zero-hallucination or population-error claim from unit CI.

Paid provider/media probes, external reviewer contact and deployment remain
outside current authorization. Keep independent review and calibration pending
as requested. Publish separate, concrete readiness/budget proposals when those
external gates become the final remaining step.
