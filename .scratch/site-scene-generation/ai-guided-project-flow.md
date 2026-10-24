<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AI guidance throughout the project flow

Status: proposed normative extension — owner requirement 2026-10-24; not implemented
Read with: [spec.md](spec.md), [contracts.md](contracts.md), [implementation-plan.md](implementation-plan.md) and [validation-matrix.md](validation-matrix.md).

## Product requirement

The complete location → site generation → building/process associations → equipment → selected analyses → results/refinement process is part of the existing project flow, with contextual GridSpine AI guidance throughout. It must not ship as a disconnected 3D wizard with a chat link attached at the end. One bottom-right companion follows the user's current project, section and selection; there is no second assistant or separate Blender/photo workflow rail. Text, dictate and live entry reuse their existing supported capabilities; this scope neither introduces a new voice stack nor claims those capabilities are universally available.

The companion offers a useful next action in context, explains missing information, prepares exact proposals, and reads back admitted outcomes. It does not invent the scene, engineering inputs, section completion or simulation evidence. A user can dismiss guidance and complete the same process through the normal controls; AI/provider availability never gates native site work. Opening a section or selecting an object must not itself initiate inference, start a worker or incur charges. The user explicitly requests assistance, or continues an already authorized bounded task under existing budgets and stop rules.

## Follow the existing project flow

Use the current section IDs from `services/flow_sections.py` and the approved [.scratch/project-workflow/design.md](../project-workflow/design.md). Labels follow the current registry, including subsequent wording fixes. Preserve the approved six-card model in [modular-cards.md](../project-workflow/modular-cards.md) and owner decision G1: the five analysis cards (energy/capacity, grid/load flow, reliability, commercial, controls) plus the sixth Hand-off card (Reports, Data exports, Specialist packet), each with at most three sub-options. They select work within this flow; they are not a second ordered rail. Site geometry does not implicitly select every analysis or force grid/control studies on all templates.

| Existing section ID | Integrated site work | Companion guidance and handoff |
|---|---|---|
| project | Choose an example or blank project, planning periods, coordinates/boundary; generate/review the campus in the existing Site plan destination | Explain example assumptions and horizon, distinguish observed/estimated/proposed buildings, offer correction in place. An empty electrical network is valid for geometry; defer connections/loads to their sections. |
| carriers | Declare energy/material carriers and process recipes; associate a building/complex with the intended process context | Explain Load versus process-driven consumption and ask about ambiguous products/units. Do not duplicate a process input as a fixed Load. |
| connection | Define native grid/supplier boundaries and their relation to scene features | Open the existing connection editor with the selected target and a return anchor. A visually plausible cable is not an electrical connection or verified rating. |
| loads | Associate independent native loads and profiles by carrier/building | Resolve exact targets and units; ask for absent demand or allocation instead of inferring it from a roof/photo. |
| assets | Add native assets or place existing assets, and show sizing/control/outage assumptions at their established homes | Separate asset creation from scene placement. Show native model edit cards, then a scene proposal; failed placement must not create the asset again. |
| profiles | Review native series, time basis, provenance and QA | Explain missing or assumed profiles and open their one owning editor in place; no second profile editor in chat or the 3D view. |
| commercial | Review tariffs/contracts, parties, cost and money-year assumptions in their established editors | Use native schemas/library provenance. Footprint/asset imagery cannot supply a price or contract term. |
| scenarios | Choose the analyses, variants, reliability objectives and relevant control assessment | Explain dependencies, time-domain versus hourly limitations and missing engine support; changing scope requires an ordinary explicit change, not an AI choice. |
| readiness | Show native preflight and scene-specific action verdicts with in-place fixes | Read `get_project_flow` and admitted scene context. Explain the first actionable blocker; do not mark complete or enable a run from conversation history. |
| results | Use the shared snapshot/period/run selection for overlays, results and report explanations; return to an input's home for changes | Cite native evidence, units, freshness and limitations. Offer a proposed revision/re-run through ordinary controls; historical/current results are distinguished. The Hand-off card retains the native reports/data-export/specialist-packet paths, availability and evidence limits. Optional refinement returns to the same Site plan context. |

Geometry is an optional visualization/enrichment activity in Project & site. Binding work is revisited at the model entity's owning section without leaving and returning through unrelated panels. A missing optional reconstruction or binding does not block an otherwise valid electrical study. `/flow` determines required work and `not_needed` for the selected scope. Unavailable/unknown state is not rendered as complete. Analysis applicability, thresholds and engineering verdicts remain native domain responsibilities.

## Context and truthful progression

The assistant's bounded per-turn context references owner/workspace/project identity, Site ID and scene revision, selected feature/group, model input revision/target identities, flow section and action verdicts, current analysis scope, investment period, exact run/snapshot window, and admitted pending proposal/job/receipt IDs. Only authorized, necessary summaries enter model context. Read current authoritative records at the start of an assisted action and again before admission; browser selection and client context are hints, not authorization or freshness evidence.

Keep changing context in the harness per-turn block; do not rebuild the stable system prompt or send the complete mesh/media/project on every turn. Source labels, OCR and photo text are untrusted data, never instructions. Do not send original photos, EXIF, private paths or full models to an inference provider by default; any future image-assisted inference needs a separately disclosed, authorized data path. Reuse the existing provider boundary and usage limits, not a parallel agent framework.

Native `/flow` remains the source of section state and scene-action availability. Assistant Workflow step position is guidance only; `advance_workflow` cannot mark a section complete. After edits or finished jobs, re-read authoritative state and present what is done, missing, stale or unavailable. Navigation success requires the existing browser acknowledgement where supported; issuing an action is not proof the intended view opened. Closed chat, a failed model call or unavailable live voice cannot prevent manual completion.

Persist native scene/model/proposal receipts and use existing owner-scoped event history for conversational continuity. Reload/reconnect reads current state and offers Resume with an honest summary; it never resubmits work, restores an approval, claims exactly-once inference or restarts a solve from chat history. Project/scenario switch clears visible old guidance and pending local targets before reading the new authorized scope. Missing or inaccessible history yields a fresh state summary, not invented progress.

## Assisted action contract and tool integration

Every action follows **inspect → explain/clarify → prepare → review/confirm → execute → read back → suggest next**. Read-only explanations need no mutation confirmation. Preparing persistent proposals or starting geometry/reconstruction workers is not classified as a free read: apply current access/restrictions, tool tier, resource/cost admission and bounded execution rules. Confirmation of a scene preview does not authorize a model edit, source-photo transfer, paid call or solve.

Use existing `get_project_flow`, schema/component reads, native edit cards and navigation first. For a multi-action request, reuse the existing `start_task` / study-brief machinery in `services/assistant_tasks.py`, with reviewed typed scene-step support where needed, existing per-step approvals, card-phase pauses, limits and uncertain-effect behavior. Do not build a second plan executor or widen existing runnable engineering cases; unsupported scene-step kinds get a named refusal/manual fallback until their adapter is tested. Freeze the following minimal candidate adapters in SC-00 N and deliver them in SC-07; these names are proposed, not current tools:

| Candidate | Allowed effect and required controls |
|---|---|
| get_site_guidance_context | Bounded owned scene/selection/provenance/availability summary, no mesh/original bytes and no job. Includes explicit unavailable fields and revision binding. |
| prepare_site_change | Typed allowlisted geometry/binding/placement action to the common scene proposal service. No raw Python, terminal, arbitrary URL/executable, direct file write or electrical mutation. Artifact/job preparation follows current write/resource policy; return exact preview, source/base hashes, warnings and owned job/proposal ID. |
| apply_site_change | One exact unchanged, current, authorized proposal through the normal native Apply path and audited confirmation in both modes. Bind approval to owner/project/site, proposal hash/base, policy/restriction revisions and intended effect. Return the durable native receipt; never claim success from model text. |

Use existing owned status/cancel adapters only if they support scene jobs explicitly; otherwise add typed adapters/dispositions after contract review. Never feed an unknown scene job kind into a solve queue. New adapters go through the one `dispatch_tool_call` path with catalogue/handler/route/tier/error/edit classification parity, provider offer-cap tests and generated inventory/control checks. A tool that is not shipped has a truthful unsupported-by-chat disposition and an in-place UI fallback. Scene tools are not automatically eligible for strict checked answers; unmapped calls refuse conservatively until explicit checked adapters and evidence gates exist.

For ambiguous names, missing dimensions, unit/basis mismatch or conflicting instructions, ask a focused question before preparing a write. An equipment request creates/edits a model target only with the existing native contract, records its receipt, and then separately proposes placement. No cross-store atomicity is claimed: partial completion is visible and recovery resumes the unfinished operation by receipt. Bindings cannot be approved until target lifecycle identity is enforceable.

After every mutation, inspect the native receipt/read-back. A timeout or disconnect with uncertain effect requires reconciliation before retry; no blind replay. Existing task retry limits and budget stops apply. A worker completion observation cannot automatically Apply, approve, advance a study or publish numerical claims. Optional Blender export/refinement/photo processing is launched from this same context, with resource/availability disclosure and explicit action; return by preview to the same Site and selection.

## Delivery, evidence and release condition

AI integration is a cross-cutting deliverable, not a final enhancement after Blender/photos. SC-00 N freezes context/tool contracts and exercises a scripted-provider native journey. Each SC-01–SC-06 slice includes its contextual explanation, exact action/fallback, native receipt/read-back and relevant tests. SC-07 owns the common harness/context/recovery and full integrated journey; its native portion develops alongside SC-01–SC-03 and does not depend on optional engines.

The native integrated release gate requires a complete geometry-first → model association → approved placement → selected native run → evidence-grounded result explanation journey, plus reload/reconnect, project switch, refusal and manual/no-provider journeys. Use deterministic scripted-provider tests with real native services and independent expected values in CI. They test orchestration and contracts, not natural-language model quality. A separate explicitly authorized, budgeted real-provider evaluation with recorded redacted prompts, versions and evidence is required before advertising demonstrated conversational quality. No paid or live model/media evaluation is authorized by this specification task. No prototype-only GO certifies release; use V61–V72 and the existing engine/device/domain gates.
