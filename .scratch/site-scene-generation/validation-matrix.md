<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Test and validation matrix

Status: proposed acceptance cases — none of the new cases is implemented or passed
Use the existing Site/model/results interfaces where possible. Exact new operation names are frozen by SC-00, before executable tests. Expected quantities use independent hand calculations or fixed native oracles, never an LLM or recomputation of the implementation.

| ID | Slice / seam | Case and required observation |
|---|---|---|
| V01 | SC-01 owned scene | Legacy project reads without write/migration; sites.json and fixed origin unchanged. |
| V02 | SC-01 persistence | Generate, edit, save/reload, scenario copy, snapshot restore and bundle round trip retain identity, source and dimensions. |
| V03 | SC-01 HTTP | Foreign owner, unknown project/site, revoked access and read-only lock refuse read/write at the appropriate native gate. |
| V04 | SC-01 HTTP | Concurrent expected_revision saves: one wins, one receives conflict; no lost update. |
| V05 | SC-01 geometry | Known 10 x 20 x 8 m building retains exact dimensions under heading changes and 2D/3D conversion. |
| V06 | SC-01 coordinates | Literal metre/mm/feet, east/north/up, glTF Y-up and fixed-origin fixtures agree; missing/unknown units/datum are not silently guessed. |
| V07 | SC-01 import | Context building promotion yields one visible/semantic building; namespace distinguishes equal numeric source IDs. |
| V08 | SC-01 provenance | Tagged height, level-derived height, fallback height and greenfield proposal have distinguishable evidence labels after reload. |
| V09 | SC-01 editing | Correct a footprint/height, cancel, undo/restore and switch Guided/Expert without duplicate records or lost work. |
| V10 | SC-01 geometry | Non-finite, degenerate/self-intersecting or oversized geometry is refused by named reason before persistence. |
| V11 | SC-02 binding | Building binds independent electricity/heat/cooling/H2 Loads using native units; a missing carrier or unmodelled cooling load stays unavailable. |
| V12 | SC-02 binding | A process Link with eh_process binds to a building; its production input remains the existing Link, no duplicate Load created. |
| V13 | SC-02 grouping | Complex spanning buildings, process units and Inventory Stores permits acyclic membership and rejects cycles/orphan targets. |
| V14 | SC-02 lifecycle | Rename preserves intended binding; delete breaks it; same-name recreation requires rebind; foreign/removed target cannot attach by label. |
| V15 | SC-02 allocation | One logical battery drawn as several enclosures counts once; one aggregate Load in several buildings has no invented local split. |
| V16 | SC-02 allocation | Explicit 0.4/0.6 split of a 10 MW native Load shows allocated 4/6 MW and total 10 MW; invalid weights refuse. |
| V17 | SC-02 geometry | Place BESS beside a real building, PV on an eligible roof and a transformer in a yard; imported buildings are obstacles. |
| V18 | SC-02 dimensions | Source-verified equipment is not non-uniformly stretched; visible dimensions, proxy, terminals and envelopes agree. |
| V19 | SC-02 findings | Overlap/boundary/service/keep-out cases identify actual objects and applicability; an unknown regulation is not a compliance pass. |
| V20 | SC-02 mutations | Place existing changes scene only; Add model asset invokes normal native controls/approval; placement failure never duplicates creation. |
| V21 | SC-02 lengths | Visual move with length opt-in off leaves electrical inputs untouched; opted-in supported route update preserves user/import/OSM route and requires fresh solve. |
| V22 | SC-03 result seam | Known selected hour displays exact native generator/Load/StorageUnit/Store/Line/Link/Transformer quantity and unit. |
| V23 | SC-03 process results | Electricity/heat/H2 input ports and product output use correct signs, units and native conversion; material t/h is never labelled MW. |
| V24 | SC-03 time basis | Instantaneous quantity versus weighted MWh/tonnes total is explicit; horizon total never repeated as an hourly observation. |
| V25 | SC-03 aggregation | Process group boundary excludes internal transfers; unsupported aggregate returns unavailable, not a plausible invented number. |
| V26 | SC-03 freshness | Unsolved, edited, cancelled/failed solve, wrong run/project and out-of-range window suppress current numerical overlays. |
| V27 | SC-03 periods | Multi-period snapshots, representative weeks, timezone display and vintage-effective quantities preserve native basis. |
| V28 | SC-03 UX | Selecting scene feature opens the intended native details; timeline does not trigger model regeneration or change result data. |
| V29 | SC-04 export | GLB/manifest has exact IDs, origin, axes, units, hashes/revision; equivalent export has stable semantic records. |
| V30 | SC-04 real exchange | A real pinned Blender import/export preserves a reference model's scale, origin, pivots and feature IDs; geometry-only success does not imply binding success. |
| V31 | SC-04 export | No Blender installed: outbound GLB still works; no source photos or unlicensed texture are included implicitly. |
| V32 | SC-05 readiness | Native generation works with Blender absent; configured Blender version/readiness is reported accurately; arbitrary executable path is not supplied by an upload. |
| V33 | SC-05 worker | Trusted script input produces owned proposal/output hashes; API stays responsive; changed source revision cannot be silently overwritten. |
| V34 | SC-05 jobs | Cancel/timeout/worker loss/late output/resource exhaustion never partially applies; state and outcome are honest. |
| V35 | SC-05 import | Stale export, foreign project/site, tampered manifest/extra, duplicate/unknown/stripped IDs and mirrored/non-finite transforms are rejected or kept reference-only. |
| V36 | SC-05 apply | Preview then explicit apply changes only permitted geometry/placement; retry returns existing receipt; unknown outcome is read back before retry. |
| V37 | SC-05 permissions | Reviewer/read-only user cannot apply; revocation between preview and apply is rechecked; old export does not grant access. |
| V38 | SC-05 model integrity | Imported scene cannot alter MW/MWh, costs, efficiencies, topology, production targets or result values through extras. |
| V39 | SC-06 drawing | Calibrated raster/GeoJSON layout yields expected dimensions; absent scale/CRS is named; unsupported DXF/IFC is not silently accepted. |
| V40 | SC-06 files | Traversal, archive bombs, decoded pixel/mesh limits, malicious PDF/SVG or external resource references fail before worker execution. |
| V41 | SC-06 photos | Real overlapping image fixture reconstructs on supported hardware; registration/coverage and scale/control residuals are reported. |
| V42 | SC-06 sparse data | Single image, no overlap, blurry/specular or unregistered images produce honest proposal/partial/failure status; no fabricated measured campus. |
| V43 | SC-06 metric use | Unscaled mesh remains viewable reference but cannot drive clearance/cable/physical-size claims. |
| V44 | SC-06 semantic use | Textured mesh does not automatically become a building/load/process; explicit proxies and bindings remain necessary. |
| V45 | Cross-cutting privacy | Worker/logs/bundle/scenario export obey ownership and selected media policy; private original photos and EXIF are not published by accident. |
| V46 | Cross-cutting recovery | Failed/partial artifacts, missing optional mesh, interrupted save and backend restart retain the previous valid scene and inspectable receipt. |
| V47 | UI accessibility | Keyboard selection, numeric edits, text alternatives, reduced motion and error states work in Guided/Expert; existing style tokens retained. |
| V48 | Real device | Packaged target-device GPU test demonstrates fly-in, selection, imported meshes, photos fallback, shadows, overlays and measured frame rate. |
| V49 | Performance | Measured limits for pixels/files/triangles/textures, queue runtime/memory and scene responsiveness enforced on both sides of each limit. |
| V50 | Route/harness parity | Every new method/path has a chat disposition; no phantom chat tool, unsupported autonomous geometry write or weakened approval path. |
| V51 | Existing regression | Existing Site/layout/length/context/bundle/thumbnail/report and unrelated native model/results behavior remains unchanged. |
| V52 | Native fallback | Offline/missing remote footprint or Blender/photo worker still permits manual/native generation with source/availability disclosure. |
| V53 | SC-01 geometry first | Empty network with zero buses/branches/loads generates from coordinates/boundary; Site has buses=[]; native model and solve readiness remain unchanged. |
| V54 | SC-01 authority races | Gizmo/debounce/legacy unload PUT races with adoption/Apply; one revision wins, stale writer gets conflict, local fallback cannot overwrite current generation. |
| V55 | SC-01/06 private media | Marked original bytes/hash, EXIF/path and live source handles are excluded from scenario/Save-As/default bundles; same-project restore reauthorizes or shows unavailable; revocation/delete/expiry cleanup is checked. |
| V56 | SC-01/05 flow authority | Server /flow alone determines native/layout/Blender/photo action states; UI agrees and cannot enable absent worker; submit/Apply reject a late lost capability; electrical run_ready unchanged. |
| V57 | SC-03 exact null contract | Missing process series/factor/unsupported group total serializes value=null, available=false and reason; resolved zero serializes value=0, available=true; UI never substitutes 0.00. |
| V58 | Independent milestone/feature gates | Native milestone and layout workflow complete with Blender/CUDA absent; optional actions remain explicitly unavailable without blocking native use. |
| V59 | SC-01 atomic generation | Crash before pointer commit preserves old state; crash after commit reads the receipt; concurrent snapshot/export contains one complete generation and no mixed bindings/envelopes. |
| V60 | SC-01 closure export/copy | Seed marked orphan/superseded/failed/cache artifacts under sites/; scene-enabled snapshot/scenario/Save-As/export includes only locked manifest closure, rejects a missing required artifact and rewrites destination-owned refs without exposing foreign originals. |

## Domain acceptance fixtures

| Fixture | Minimum end-to-end acceptance |
|---|---|
| Data center | Existing halls, critical/general electricity loads, UPS/battery, gensets, rooftop PV and transformer; no duplicate generic hall/load, selected-hour native results. |
| C&I / industrial | One building with electricity, useful heat and process Link; loads remain separate from process-driven demand. |
| H2 hub | Electrolyser/fuel-cell Links, electrical loads and H2 Inventory; LHV MWh versus mass conversion follows native factor. |
| Ammonia | Several buildings/areas containing H2 upstream and Haber-Bosch process units plus NH3 Inventory; product/input ports and group boundary conserved. |
| Mining | Crusher/mill process units, stockpile Store, multiple buildings and material Product units; no t/h-to-MW relabeling or fictitious fixed load. |
| Island microgrid | Hospital/residential building associations, diesel/BESS/PV, absent grid tie and unmet-load observations; optional worker absent. |
| IPP | Ground equipment and a small service building; no requirement for a data hall or an industrial process to generate a useful site. |
| Greenfield / staged | Proposed buildings remain distinguished from observed context; period-selected result quantities never imply an unimplemented construction phasing engine. |

Use redistributable synthetic/public fixtures for CI. Validate a real site privately with owner-provided suitable data before product claims. Do not infer blanket domain approval from one passing fixture or combine old overlapping suite counts.

## Release evidence categories

Fast native unit/component/HTTP tests; native model/result integration; genuine Blender exchange; genuine reconstruction; packaged-device visual/performance; and source/asset terms evidence are separate categories. Record exact source, engine version, fixture hashes, commands, counts and exclusions for each. Proposed test rows are not passing evidence. A missing engine skips only an explicitly optional engine job in ordinary development; it blocks release of that engine capability until genuine evidence exists.
