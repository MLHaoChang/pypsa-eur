# SC-01 — native campus generation and editable buildings

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: SC-00 native contracts
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Scope and acceptance

Write a public scene HTTP/store red test before implementing its behavior. Preserve legacy sites.json, origin and placements. Add the additive revisioned scene document, native building/zone proxies, owned source/geometry records and compare-and-swap save/apply. Render imported footprints with source-derived or visibly estimated heights; promoting context suppresses its duplicate background mesh.

Implement location/2D preview -> Generate -> correction in the existing Site plan view, using current style/selection controls. Native generation works without Blender or remote data, with manual footprints as fallback. Change no electrical inputs when editing building geometry. Add numeric geometry/placement controls and explicit unit/scale handling; do not imply engineering precision from decimal display.

Tests cover legacy reads with no write, source classification, independent metric oracle, overlap/boundary geometry, concurrent edits, ownership/lock, failed apply preserving the old scene, reload and scenario/snapshot/bundle behavior. Show Guided and Expert correction, missing-data, read-only and offline states.

Done: V01–10 and native/fallback portions of V45–52, actual UI screenshots and exact-head checks. No bindings, Blender/photo engine installation or solver changes in this PR.

## Validation

V01–10, V45–49, V51–52; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.
