<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-07 — integrated AI guidance and project-flow continuity

Status: needs-triage — owner-required specification; not implemented
Type: task
Dependencies: accepted SC-00 N contracts; develop native adapters with SC-01–SC-03, optional adapters only when their respective engine gates pass
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Implement [ai-guided-project-flow.md](../ai-guided-project-flow.md) through the existing companion and project flow. This is part of the native release scope, not a separate assistant/mode or a dependency on Blender/photo availability. Freeze context/tool/request/disposition contracts in SC-00 N; write failing owned-context, dispatch/approval and visible guided-journey tests before adapters or UI code.

Reuse get_project_flow, native model schemas/edit cards, workflow flow_section mapping, event log, existing start_task/study-brief execution, navigation acknowledgements and owned job/receipt services. Implement the bounded scene-context and typed prepare/apply adapters only after applicable harness/UX plan review; every route has an explicit chat disposition. Extend the existing assistant workflow content rather than building a parallel executor. Do not copy any stale workflow text that suggests Expert writes bypass current edit-card policy.

Provide Explain this, help with the current blocker, prepare a change and resume assistance at the existing section/selection. Server flow verdicts own availability/completion. Map the whole native journey into project/carriers/connection/loads/assets/profiles/commercial/scenarios/readiness/results without altering their order or adding duplicate input editors. No inferred energy demand/cost/efficiency from geometry. Preserve selected study scope and planning periods.

Show exact native receipts, distinguish partial model-create/scene-placement outcomes, reconcile unknown effects before retry, and resume by reading owned current state. An old approval, chat summary, finished-worker observation or emitted navigation frame is never evidence of current authority/success. Keep manual completion usable with no provider. Freeze a finite context budget in the prototype; no original-media transfer or unbounded model/mesh injection.

Done for native release: V61–V72 pass at the integrated seams with real native data/services and scripted-provider control; other applicable domain/device/CI gates pass; a separate authorized real-provider evaluation supports conversational-quality claims. Report absent optional-engine adapters as unavailable, never implemented. Independent GK/UX review and all ordinary repository gates still apply.

## Likely touch points and verification

Rebaseline before code: harness compose/workflows/catalogue/dispatch, typed chat handlers and owned scene/job services; server flow projection; frontend Site selection, existing companion/context and flow-navigation return anchors. This is a touch-point map, not authorization to overwrite other briefs. Keep proposals/applies in the common native scene path rather than implementing state changes inside prompt text.

Run relevant flow/workflow/navigation, edit-card/access/restrictions, provider offer-cap, tool-route/schema/error, event-log, replay/recovery and scene integration tests. Regenerate capability inventory/control artifacts only through their scripts. Capture Guided/Expert same-task screenshots and a native geometry-first full journey, including a failed/denied action and manual fallback. Record exact commands, head, evidence and exclusions in the PR and handoff; proposed tests are not completed evidence.

## Optional coordinate-first workflow clarification

Follow optional-site-workflow.md through optional coordinate-gated creation, campus review, buildings-only pause, native editor return/target mapping, logical/physical energy layers and actual-cadence results. Include V73–V90 with relevant stage-aware refusals and resumption; no extra wizard, diagram-only substitute, automatic solve or missing-data invention. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).

## Interactive configuration acceptance

Implement the example intent-to-effect contract in ai-guided-project-flow.md and V91–V98: guided creation, building/placement adjustments, exact multi-load/facility associations, PV/battery placement, supported native process/connection and parameter edits, and evidence-grounded channel mapping. Separate scene, model and display effects, ask about ambiguous output/connection wording, reuse normal native edit cards and exact scene proposals, and show truthful partial receipts. Unsupported tools/fields retain named refusal/editor fallback. This is a required guided adjustment path, not a read-only explainer.
