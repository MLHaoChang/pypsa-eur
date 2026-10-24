<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Tools to reuse, integrate and explore

Status: design choices and bounded exploration; no new dependency installed or verified by this package

| Tool / component | Decision | Purpose | Evidence required before shipping |
|---|---|---|---|
| Existing Three.js / React Three Fiber and native geometry | Reuse for the default path | Interactive scene, simple buildings, catalogue equipment, proxies and result overlays | Model-free generation and all existing scene regressions; real packaged-device frame rate. |
| Current OSM/terrain/context adapters | Reuse and promote selected context | Site location, source footprints, estimated heights and terrain | Provenance, empty/sparse/offline fixture, approved source/derivative handling. |
| Native PyPSA/process/results interfaces | Reuse; authoritative quantities | Multi-energy Loads, eh_process Links, Products, Inventory Stores and selected-hour series | Lifecycle binding identity, native units/factors/ports and independent numerical oracles; no new process physics. |
| GLB/glTF plus a GridSpine manifest | Use for neutral exchange | Geometry/materials plus exact Site origin, axes, IDs, revisions and binding descriptors | True metric round trip; missing/stripped IDs reference-only; no extras authority over model inputs. |
| Blender | Prototype optional background worker and external refinement | Better shapes/materials, geometry refinement, renders and GLB/export generation | Pinned version, trusted scripts, package/executable readiness, bounded resources, offline behavior, genuine exchange evidence. No per-hour rebuild. |
| glTF-Transform (already documented asset pipeline) | Reuse only with pinned validated options | Mesh/texture preparation and bounded export optimization | Preserve IDs/anchors/native scale; existing no-CDN decoder constraints. Do not flatten away identity silently. |
| Khronos glTF Validator | Evaluate as an artifact-format gate | Structural GLB/glTF validity before browser loading | Offline pinned adapter, malformed/oversized fixtures. Passing format validation does not establish dimensions, model identity or source rights. |
| OSM2World | Explore only if native extrusion lacks sufficient visual value | Richer roofs, road/building context and detail levels | Representative scene quality, georeferencing, license/assets terms and output/bundle/performance cost compared with native path. |
| Overture Buildings | Explore source fallback, not a new renderer | Additional building footprints and optional heights/floors | Bounding-box acquisition, duplicate reconciliation, source/date/terms and actual target-site coverage. Missing height remains estimated. |
| OpenDroneMap | First aerial/campus photo prototype candidate | Georeferenced orthophotos, terrain/surface data and textured reference mesh | Suitable overlapping photo fixture, control/scale evidence, actual resources and local/authorized deployment. No automatic semantic asset identification. |
| COLMAP / PyCOLMAP | Alternative general multi-view prototype, not an additional default engine | Camera registration and reconstruction for non-aerial views | Demonstrated dense/texturing chain and supported hardware. Its documented dense stage needs CUDA; sparse point cloud alone is not a rendered campus. |
| Meshroom / further reconstruction engines | Not selected in the first package | Possible later comparison if chosen candidate fails the required use case | A specific unmet need and hardware/packaging advantage first; avoid shipping several heavyweight stacks. |
| IfcOpenShell / Bonsai | Later dedicated CAD/BIM adapter | IFC geometry and possible engineering handoff | Explicit requested format/semantic scope, units/CRS/identity tests and a separate implementation plan. No automatic BIM claim from GLB. |

## Experiments and decisions

N1: use an empty network and known coordinate/boundary to generate a 10 x 20 x 8 m building; prove no Bus/branch/Load creation, shared 2D/3D geometry and exact native dimensions.

N2: replay a stale legacy gizmo/debounce/unload save during a new scene Apply and snapshot export. Demonstrate one canonical generation, explicit conflicts, stable origin and no mixed-generation bundle.

N3: upload a marked original photo/layout into the private store, then snapshot, scenario-copy, Save-As and export/import a bundle. Assert original bytes/hash, EXIF/private paths and working foreign handles are absent in exported/copied state; derived content remains useful where permitted.

B1: export a tiny scene through the proposed artifact writer, open/refine/export it with a real pinned Blender, and validate returned dimensions/IDs against independent literals. Geometry-only success is not a binding pass. Record setup size, elapsed time and peak resources; test absence/failure separately.

P1: reconstruct one redistributable aerial set with a known reference and one sparse/bad set with the chosen photo engine. Record registered views, alignment/scale source, coverage, memory/runtime and output size, including failure. A ground-level set is a separate suitability check, not evidence that an aerial success generalizes.

Each experiment stores its own actual GO/NO-GO and unavailable states. Select one photo worker only after P1; select no paid service by default. Native N can pass independently of B/P. Freeze finite measured limits before enabling the relevant production action; this spec does not invent benchmark numbers.

## Primary references

- Blender export API: https://docs.blender.org/api/main/bpy.ops.export_scene.html
- Blender CLI: https://docs.blender.org/manual/ko/4.5/advanced/command_line/arguments.html
- glTF-Transform: https://gltf-transform.dev/cli
- Khronos validator: https://github.com/KhronosGroup/glTF-Validator
- OSM2World: https://osm2world.org/
- Overture building schema: https://docs.overturemaps.org/schema/reference/buildings/building/
- OpenDroneMap: https://cloud.opendronemap.org/odm/ and https://docs.opendronemap.org/flying/
- COLMAP: https://colmap.github.io/ and https://colmap.github.io/faq.html
- IfcOpenShell geometry: https://docs.ifcopenshell.org/ifcopenshell-python/geometry_processing.html

SC-00 records exact selected versions, applicable software/asset licenses and packaging obligations. Links and feasibility research are not evidence that an executable, commercial-source permission or hardware profile has been validated here.
