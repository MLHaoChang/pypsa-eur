<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assessment of the owner's optional campus workflow

Status: design gap assessment and specified corrections; no runtime implementation assessment pass
Date: 2026-10-24 (Europe/Berlin)
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c and PR711 publication e508bcd5506f9d89f79d728fd773ecec6bb27027

## Finding

The requested workflow is feasible within the existing site/map/native-model/results architecture. The earlier specs cover buildings, sources, explicit model associations, Blender/photo proposals, freshness and integrated guidance, but leave optional entry/admission and the source-of-truth for physical energy connections too implicit. The intended order must not be implemented using today's asset-first Generate action.

Current source evidence: `site3d/readiness.ts` requires located model buses plus a connected branch. `components/SiteEmptyState.tsx` chooses a Bus, derives an equipment-packed rectangle and seeds placements. `site3d/types.ts` stores member buses, a boundary and fixed origin, with Class:name placement keys. `SiteCanvas.tsx` packs native equipment around member buses; contextual buildings are separate scenery. Existing result hooks/shared timeline/native graph are reusable. This is not evidence that semantic building editing, generic process ports, identity-safe mapping or photo reconstruction is already implemented. No new runtime tests were run for this assessment.

## Requirement-to-gap mapping

| Owner requirement | Earlier design status | Required correction / owner slice |
|---|---|---|
| Optional action in site configuration;2D work unaffected | Intent present, UI/gates underspecified | One Create/Open/Resume action, opt-in only, no new required card; SC-01 and V73–75. |
| Coordinate required before opening creation | Coordinate/boundary mentioned; bus-first runtime remains | Confirmed native site anchor independent of Bus, coordinate validation and explicit0/0; SC-00 N/SC-01, V73–76. |
| Campus mapping before rendering | Geometry-first intent present | Separate entry from boundary-confirmed generation; no invalid boundary-less Site persistence; SC-01, V74/V77. |
| Review, tweak or regenerate | Proposal lifecycle covered | Explicit user-driven regenerate, preserve accepted scene and review changed bindings; SC-01, V78/V84. |
| Photos/layouts support generation | Covered as optional engine-gated inputs | Apply the same coordinate/mapping flow; native/manual fallback and source honesty; SC-06, V77/V90. |
| Stop after buildings if no model data | Geometry-first possible, resumption implicit | Buildings-only saved state, native editor return targets and no invented targets; SC-01/02/07, V79–81. |
| Return with configured loads/generation/storage | Binding contract covered | Both entry orders, per-target readiness and exact lifecycle mapping; SC-02, V80/V81/V83. |
| Use configured SLD/map connections | Current model authoritative, terminal/route projection underspecified | Same native graph projected to3D; distinguish logical connectivity from route geometry; SC-02, V82–85. |
| Known PoC/transformer and hidden energy routes | Partial physical-placement coverage | Explicit native terminal/role mapping; per-carrier source/route/result availability and underground layer controls; SC-02, V82/V85/V86. |
| Building/process material and energy inputs/outputs | Native units/ports/groups covered | Apply existing association/accounting contract to physical flow presentation; SC-02/03, V86/V88 and prior V12/V23–25. |
| Results at actual available granularity | Shared native timeline covered, wording hourly-centric | Cadence-aware playback; representative/period semantics and no synthetic high-rate data; SC-03, V87/V88. |
| One optional workflow with AI guidance | Cross-cutting contract already specified | Stage-aware explanations, native blockers and Resume; manual/no-provider parity; SC-07, V89 and V61–72. |

## Recommended implementation order

1. Freeze and test the native site anchor, per-action/per-target readiness, geometry-only state and native graph/terminal references in SC-00 N. Do not add engines to solve these app contracts.
2. Deliver the optional native campus path and review/regenerate/save/resume in SC-01, preserving ordinary 2D/model workflows and legacy scenes.
3. Deliver existing-target mapping, multiple carriers/processes and truthful logical/physical connection layers in SC-02. Missing model items send users to their existing editors and back; a global solve-ready gate is not a prerequisite to association.
4. Deliver native cadence/period-aware results and process-flow overlays in SC-03. Retain unavailable/freshness and numerical-oracle requirements.
5. Add optional Blender exchange and layout/photo refinement only under their existing measured resource/source/engine gates; none blocks the native path.

Use existing Three.js/R3F, map/context/terrain, native component/graph/result APIs and shared timeline. Explore OpenDroneMap/COLMAP/Blender only for the source/refinement tasks they serve; do not add a second electrical model, a mandatory image-to3D step or an unvalidated pipe/process simulator. Detailed normative behavior is in [optional-site-workflow.md](optional-site-workflow.md), with proposed V73–V90 in [validation-matrix.md](validation-matrix.md).

The assessment recommends GO for revising/publishing the design and executing the bounded native prototype. Actual release remains gated on implementation, tests, genuine optional-engine/device evidence and applicable official reviews. Prior advisory reviews apply to their recorded heads; this clarification requires a new delta verdict, recorded in review-verdict.md.
