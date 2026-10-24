<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02 — multi-energy buildings, process complexes and equipment

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: SC-01; SC-00 target-lifecycle contract
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Before code, obtain the required engine/UX plan review for native lifecycle/mutation seams. First prove rename/delete/same-name recreation behavior; do not use loose display-name matching or infer a stable UUID the model does not provide.

Write binding and native-model seam tests before implementation. Associate existing Loads per carrier, Process-unit Links (eh_process) and Inventory Stores with a building/group. Support acyclic complexes covering several buildings and outside equipment. Associate multiple visible enclosures with one logical component without multiplying capacity. Do not create a duplicate fixed electricity Load for a process Link's demand. Display unsupported conversions/allocations as unavailable.

Integrate real building footprints, roof eligibility, functional terminals and service envelopes into placement. Preserve exact physical scale for source-verified catalogue equipment; keep generic fitted equipment visibly approximate. Place existing is scene-only; Add model asset uses the existing native editor/approval path and separate receipt. If placement fails after creation, do not retry creating the asset.

Keep optional geometry-derived length behavior behind the current setting and existing native routes, preserving user routes. Test access revocation and lifecycle failure before Apply. No new energy/process physics or globally overwritten equipment library.

Done: V11–21, domain fixture bindings, lifecycle correctness, native mutation regression and Guided/Expert review evidence.

## Validation

V11–21, V37–38, V45–47, V50–51; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.

## Optional coordinate-first workflow clarification

Map existing native2D/SLD targets only, with per-target prerequisites and in-place parameterization/return. Resolve PoC from native Bus.eh_poc and existing role/config import/export Links (no new PoC class); preserve multiple/ambiguous PoCs and separate Transformer terminal references and typed multi-carrier connection projections; distinguish known logical edge, known physical route and available flow. Unknown routes stay explicitly schematic, buried layers hidden until requested, and no geometry supplies missing electrical/pipe parameters. Cover V80–V86 and applicable process fixtures. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).
