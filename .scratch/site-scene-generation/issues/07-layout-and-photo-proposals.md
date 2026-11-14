<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-06 — layouts and photo-based campus reconstruction

Status: ready-for-human (layout half done: #1405 merged 2026-11-12, record sc-06-layout/record.md; photo half ready-for-agent as a prototype, engine chosen Q-SS-2)
Type: research/prototype then bounded task
Dependencies: SC-00 N and SC-01 native proposal lifecycle for layout; independently accepted SC-00 P for photo; no Blender prerequisite
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

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

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage — specification proposed; not implemented`. Owner question Q-SS-1 (see issue 05). Under option (1) the layout half is buildable after SC-01 (02) lands; the photo half waits for an accepted SC-00 P. Splitting the file into 07a layout and 07b photo is advised when answered. Opus, reviewers GK B and UX; the type is research/prototype first, then a bounded task.

2026-11-05, owner answer Q-SS-1 (coordinator card): "Park until an engine is chosen (Recommended)". The layout half builds after site-scene 02 lands; the photo half waits for an engine choice.

### 2026-11-11: SC-06-layout plan GO (GK B 48), with conditions

Plan sent to GK B on master 701328bd9. D05 and D08 were checked closed first. GO given on these conditions, recorded here as asked:

- **Design.** Raster originals stay in the browser and the server decodes nothing; `POST .../scene/sources` stays deferred, with no D05 claim. The PR names that the image width/height (the 50 MP and side limits, "inside the image") are client-declared and bound only the declared frame.
- **L1.** The GeoJSON document's body is bounded before parsing (the route's `_json_body` cap). Tested with a body just over the cap.
- **L2.** The proposal stores only the used fields: foreign members and properties are stripped before storing. V55 asserts on the proposal record as well as on the building.
- **L3.** Tests assert vertices, not only dimensions:
  - raster with rotation, an anchor in pixels and a local anchor;
  - mutants for a missing y-flip and swapped E/N;
  - GeoJSON at 60°N with a known east offset, so a missing cos(lat) fails.
- **L4.** The scale unit goes through `frames.to_metres`, and m/mm/ft agree. Each of these refuses with its own reason and test: zero, negative or non-finite distance; a_px == b_px; non-finite rotation.
- **L5.** MultiPolygon parts, missing ids and an `f<index>` collision give unique ids or a named refusal, with a collision test.
- **L6.** Heights come from the height in m, then levels × 3.0 m (D08), then the fallback. A unit string, a negative or non-finite height, and levels ≤ 0 each have a test.
- **L7.** `native.generate` is byte-equal after the admission refactor, and master's native tests are unchanged. Each admission part has a killing test on `import_layout`, and a spy shows admission runs before geometry.
- **L8.** List the readers of `OPTIONAL_ENGINE_ACTIONS`. Only `process_layout` changes; `reconstruct_photos` and `generate_blender` are tested unchanged.
- **L9.** Lifecycle tests:
  - preview leaves the revision unchanged;
  - apply is explicit;
  - a retry returns the receipt;
  - stale → `revision_conflict`;
  - read-only is refused;
  - a foreign lock is refused;
  - V38: the network stays byte-equal.
- **L10.** New reasons ride on existing kinds, and the frontend consumers of 422/413 still read them (#1261).
- **L11.** Line count at PR open; over about 1,500 non-test lines, split into the backend seam first, then the frontend (PR-SIZE-1).
- **Mutants on layout.py:** flip, rotation sign, anchor, CRS refusals, the 10 km check, the hole refusal, pixel-in-image. Each killing test is listed in the PR body.

### 2026-11-14: engine chosen (Q-SS-2)

2026-11-14, owner answer Q-SS-2 (coordinator card): "option 1 (Recommended)". Engines chosen, see [research-engines.md](../research-engines.md) (#1490): Blender-free GLB+manifest by default (trimesh/pygltflib, MIT); Blender 5.2 LTS only as a separate pinned no-network container for optional `.blend` output; photos via OpenDroneMap/NodeODM, out-of-process and unmodified (AGPL). Change: the photo half uses OpenDroneMap through NodeODM as a separate unmodified service (AGPL-3.0 kept out-of-process), aerial set first; it needs OBJ-to-GLB conversion and a ground-level suitability check (UNVERIFIED in research-engines.md). COLMAP + OpenMVS is the alternative if a CUDA host appears. Photo capability still needs SC-00 P evidence (V39-46, V55-58).
