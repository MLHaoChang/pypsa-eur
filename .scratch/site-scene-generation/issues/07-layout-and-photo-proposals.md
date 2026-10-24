<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-06 — layouts and photo-based campus reconstruction

Status: needs-triage — specification proposed; not implemented
Type: research/prototype then bounded task
Dependencies: SC-00 N and SC-01 native proposal lifecycle for layout; independently accepted SC-00 P for photo; no Blender prerequisite
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Scope and acceptance

Implement source adapters independently. First accept a calibrated raster/GeoJSON layout reference and manual footprint correction through the common preview. PDF rasterization is bounded; it is not automatic CAD/vector understanding. DXF/IFC adapters have their own units/CRS/semantic tests before they are advertised.

For multi-view photos, compare a representative aerial set and a ground-level set on one selected reconstruction stack. OpenDroneMap is a candidate for geolocated aerial data; COLMAP-based dense reconstruction requires a demonstrated compatible GPU and texturing path. Do not ship several engines or imply sparse SfM is a textured campus. Record overlap/registration, control/scale source, residuals, missing surfaces and resource use.

A single photograph can support a proposed approximate building, not a measured 3D reconstruction. The output is a reference mesh until aligned and given semantic proxies. Without metric anchoring it cannot drive physical length/clearance claims. Do not infer loads, process units or equipment capacities from textures.

Reject unsafe/oversized/remote-linked inputs before execution. Use contracts.md's private-original store outside all recursively copied/exported project directories, with explicit scenario/Save-As/snapshot/expiry/deletion behavior and V55 assertions. External processing requires a separately configured authorized destination. Ready output is a preview proposal, never an automatic replacement of the site.

Done independently: layout cases can close on Native evidence with no photo/Blender worker. Photo capability requires V39–46, V55–58 and actual engine evidence. Missing hardware or poor source data remains a disclosed unavailable/partial state. Native generation continues to work.

## Validation

V39–46, V06, V08, V34–38, V48–49, V52, V55–58; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.

## Optional coordinate-first workflow clarification

Refine the optional coordinate-first campus workflow through the existing valid Site envelope; layout/photo sources do not bypass location, scale, ownership or worker gates. Keep manual/native mapping available. Pre-boundary media APIs are outside the first slice unless separately specified. Cover V77–V78/V90. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).
