<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-01 — native campus generation and editable buildings

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: accepted SC-00 N only; Blender/Photo not required
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Write a public scene HTTP/store red test before implementing its behavior. Preserve legacy sites.json authority for unadopted Sites and the fixed origin. For adopted Sites, implement the single canonical generation/pointer authority, compatibility projection and stale legacy-save refusal and manifest-closure export/copy in contracts.md. Add native building/zone proxies, source/geometry records and compare-and-swap save/apply. Render imported footprints with source-derived or visibly estimated heights; promoting context suppresses its duplicate background mesh.

Implement location/2D preview -> Generate -> correction in the existing Site plan view, using current style/selection controls. Native generation works without Blender or remote data, with manual footprints as fallback. A saved Project may have an empty network: generate from coordinate/boundary, with buses=[] and no dummy component creation. Replace the new action's model-first client checklist with the additive server /flow action verdict, and recheck at submission. Change no electrical inputs when editing building geometry. Add numeric geometry/placement controls and explicit unit/scale handling; do not imply engineering precision from decimal display.

Tests cover legacy reads with no write, source classification, independent metric oracle, overlap/boundary geometry, concurrent edits, ownership/lock, failed apply preserving the old scene, reload and scenario/snapshot/bundle behavior. Show Guided and Expert correction, missing-data, read-only and offline states.

Done: V01–10, V53–60 and native/fallback portions of V45–52, actual UI screenshots and exact-head checks. No bindings, Blender/photo engine installation or solver changes in this PR.

## Validation

V01–10, V45–49, V51–60; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.

## Optional coordinate-first workflow clarification

Implement optional Create/Open/Resume in the existing coordinate/site-configuration context. Server admission needs a confirmed valid site anchor to open mapping and a valid confirmed boundary before generating/materializing a Site. No automatic creation on load/coordinate entry; no model component is required. Deliver campus review/correction/explicit regenerate, previous-scene preservation and buildings-only save/exit/resume. Cover V73–V79/V84 and relevant V89–V90. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).
