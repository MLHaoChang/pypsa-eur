<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Editable campuses, processes and 3D exchange

Status: needs-triage — concrete design proposal; no new capability implemented or independently approved
Type: specification
Baseline: master `9988f9899a3bf8e80f51272529f18ea70599b90c`, fetched 2026-10-26 (after #712)

## Problem and intended outcome

The user wants to start with a real location or an uploaded layout, generate a visually appealing approximate campus, associate buildings and areas with multi-energy loads and industrial processes, place equipment, and play native simulation results through the scene. They also want an integrated Blender option, external refinement, and exploration of reconstruction from site photographs.

The ordinary workflow remains in GridSpine's existing Site 3D view. It works without Blender. Blender can run behind a bounded generation/export job, or receive an exported scene for advanced editing. Photo reconstruction is a separate optional worker, because Blender alone does not infer a metrically reliable site from photographs.

Geometry-first generation explicitly replaces the current electrical-model-first prerequisite for this new path. A confirmed coordinate/boundary and an authorized saved Project suffice: its network may contain zero buses, branches, Loads and process units. The new Site may have `buses: []`. No dummy Bus or branch is created. The old model-driven generation path remains available, but its `siteReadiness` checklist cannot gate the new geometry-only action. SC-01 must update SiteEmptyState, readiness and rendering to support this case, using the server verdict below.

This request expands the earlier presentation-only scope. Project scene geometry and imported reference models are proposed here; the repo-owned equipment library is still the default. Earlier exclusion of per-project library overrides does not imply permission to overwrite that global catalogue. Photos and meshes never supply electrical ratings, process efficiencies, capacities or numerical results automatically.

## Current implementation to retain

PRs #93 and #139 are merged. Site boundaries, member buses, local coordinate frames, placements, context acquisition, parametric/GLB equipment, packing, findings, native result overlays, shared timeline, thumbnails and report figures already exist.

Imported OSM buildings currently form background meshes. The packer consumes model assets and bus yards, not these building polygons. Placement persists east/north/heading; it is not a general building record. Hero models can stretch to parametric boxes. `useSiteResults` already reads native series; process summaries exist separately and must not be mistaken for hourly process series.

## User journeys

1. Enter coordinates, inspect a 2D preview, confirm the boundary and Generate campus.
2. Review buildings whose heights are source-reported, estimated or proposed; correct a footprint, roof or height without changing electrical inputs.
3. Upload a georeferenced layout, preview alignment and adopt selected buildings; uncertain features remain proposals.
4. Upload a scaled drawing, identify reference points and trace missing buildings in the same view.
5. Select a building and associate existing electricity, heat, cooling, H2 or other modelled loads. Retain each carrier's native unit.
6. Associate a process unit with a building, or create a named scene group covering several buildings, process units and inventory stores.
7. Place an existing battery, electrolyser, transformer, genset or other supported model asset beside a building, on an eligible roof or within a service area.
8. Choose Add model asset, use the normal model editor, then place the admitted asset. A decoration never silently creates a model component.
9. Inspect boundary/obstacle/service-envelope findings before accepting a layout. Estimated rules are named as such.
10. Select a native snapshot and see native loads, dispatch, storage state or process quantities at the bound object; select it to open existing model/results controls.
11. Export an exact scene snapshot for Blender, refine its geometry there and return allowed visual edits through a preview.
12. Submit overlapping photographs to a configured reconstruction worker, inspect registration/scale/coverage and adopt the resulting reference scene explicitly.
13. Close, reload, copy a scenario, restore a snapshot or export/import a project without losing geometry, provenance or binding integrity.
14. Work offline with the saved scene and parametric fallback; missing workers or remote sources explain their unavailability.

## Integrated project flow with AI guidance

The entire process is part of the existing project flow with the single GridSpine companion, as specified in [ai-guided-project-flow.md](ai-guided-project-flow.md). Guidance follows the current section, selected building/model target, planning period and study scope through site generation, parameterization, approved execution and native results. It explains blockers, prepares typed proposals and reads back outcomes; native /flow and receipts determine progress. Manual controls remain usable without a provider. This integration is a required native release deliverable, not an optional chat link or a separate 3D wizard.

## Optional creation and staged availability

[optional-site-workflow.md](optional-site-workflow.md) is normative for the owner's coordinate-gated optional action, campus mapping/review, buildings-only pause/resume, existing native2D/SLD associations and multi-carrier connections, followed by actual-cadence results playback. It does not make3D a study prerequisite. Read [workflow-assessment.md](workflow-assessment.md) for the source-based gaps and assigned slices.

## Domain model

Keep Site and campus study distinct. A Site owns geometry; the campus study remains the native electrical analysis on the Site's buses. The scene is a view and placement record, not a solver or engineering certification.

### Scene and feature records

Preserve `sites.json` version 1 and existing behavior for unmigrated Sites. For a scene-enabled Site, use immutable scene generations under `sites/<site_id>/scene/` and one atomically replaced `current.json` pointer carrying revision and manifest hash. That pointed-to generation is the sole authority for the enabled Site's boundary, origin, membership and all transforms, including model-equipment placements. It is not a second independent placement store. Existing legacy records are captured on explicit adoption and retained as historical input; opening a project alone does not adopt it.

GET sites composes a compatibility projection from the canonical generation for enabled Sites and the legacy document for other Sites. All new gizmo, Arrange, boundary, metadata and proposal writes for enabled Sites go through one project-scoped locked compare-and-swap write module. Legacy whole-document PUTs without the current revision may not change, delete or replace enabled Sites; the backend returns an explicit refresh/upgrade conflict rather than accepting a stale browser-unload write. The updated sitesStore cancels old debounces on adoption and routes enabled writes/flush/keepalive through that module. Do not dual-write scene transforms and legacy placements. The compatibility projection is regenerated, not independently committed. Snapshot/export captures the pointed generation under the same lock, never a mixture of generations. See contracts.md for races and crash behavior.

Owned derived assets can use the existing `sites/` bundle carry only after quota and confidentiality tests pass. Original media is stored outside all recursively copied/exported project directories as specified below.

The scene records its schema/revision, source Site-document digest, fixed WGS84 origin, local east/north/up frame in metres, horizontal/vertical datum information, source records, feature records, groups, bindings and reconstruction/export receipts. A changed boundary must not silently recenter existing placements.

A feature has an opaque stable ID; kind (building, zone, equipment, inventory or reference); optional parent group; footprint; base elevation; height; optional roof surfaces and functional anchors; and a source reference. A shape or mesh can be observed, source-reported, estimated or proposed. Record that classification per relevant dimension, not a fabricated percentage confidence. The visible shape, collision proxy and service envelope have distinct roles.

Promoting a contextual building to an editable feature suppresses its original background copy. It does not create a second building or load. Source IDs include the source namespace and object type, not only an OSM integer.

### Buildings, loads and processes

A building may reference several Loads/carriers and contain several process units. A scene group may span buildings and outside equipment. Group membership is acyclic and has no scheduling or optimization authority.

Reuse native process concepts: a process unit is a Link with `eh_process`; Products live on their existing buses; Inventory is an existing Store. A building is not itself a process unit, and a plant/complex may contain many units. A process association does not add a second fixed Load for electricity already drawn by that process.

Bindings record a scene feature/group, native target identity, role, selected port/quantity where needed, and result provenance. The current code uses `Class:name`; do not pretend a persistent model UUID already exists. SC-02 must demonstrate rename, deletion and same-name recreation handling through native lifecycle hooks before bindings are admitted. A removed/recreated target requires explicit rebind, even if its name is unchanged. If existing hooks cannot establish that identity, stop that slice for a named scope amendment rather than matching by display name alone.

A logical model asset may have multiple visible enclosures. Those instances collectively represent one capacity and one result source. Conversely, one building may contain multiple independent components. Default to no per-building disaggregation when an aggregate component covers several buildings. A user-declared allocation is labelled allocated, uses explicit weights/basis, and conserves the aggregate; it is not measured building demand.

### Numerical contract

All quantities come from native schemas/results and carry their units, time window, run/source and availability. MW, MWh, thermal energy, H2 LHV energy, tonnes, t/h and currency are not interchangeable. No MW-to-tonnes conversion without the native factor and declared basis. Annual or horizon process totals must never be repeated as hourly values.

Every unresolvable numeric field serializes as `value: null`, `available: false` and a reason/availability flag. Resolved zero serializes as `value: 0`, `available: true`. Pin both shapes in the result contract and tests for missing process data, missing conversion factors and unsupported group totals, following ADR-0001; the UI renders unavailable, never a default 0.00.

For group summaries, define a supported quantity and boundary before aggregating. Do not add internal and external flows or product and energy ports together. Unsupported group totals stay unavailable while individual native quantities remain inspectable.

## UX and behavior

Use the existing Site plan workspace, selection/properties pattern and Guided/Expert vocabulary. Do not add a new top-level workflow card or palette. Guided shows the next relevant task; Expert exposes numeric dimensions, datum, source and binding details in the same scene.

Per CODING_STANDARDS.md UI rule 1, extend the server's project `/flow` verdict with source/scene action readiness, blockers and what will be computed for native generation, layout/photo processing, Blender/export and Apply. These action verdicts do not change electrical `run_ready`. The UI renders this server projection only; no browser worker/platform/format table decides capability availability. Server admission rechecks capability, source/revision, permissions and current limits at submit and Apply, including loss of readiness after a previously green preview. Current client-only siteReadiness is legacy evidence to replace for the new action, not an approved availability authority.

The optional sequence is confirmed site coordinate -> campus mapping/boundary -> native generation/review -> save buildings only or associate configured native targets -> native connection/route layers -> available results playback. Opening creation requires a coordinate; geometry submission additionally requires accepted mapping inputs. Users may stop after generation. Missing data is a correction task, not an automatic model guess. Show an in-place distinction between Place existing, Add model asset and Reference/decorative object.

Numeric east/north/elevation/heading, metric dimensions, measurement/alignment tools and visible envelopes support precision placement. A rated catalogue model preserves physical scale/aspect; it must not use the current arbitrary per-axis fit. A stylized generic model may retain fitting but is labelled approximate.

Visual edits do not by themselves invalidate a solve. Any consequential model edit, including opted-in geometry-derived length, follows the existing native mutation path and invalidates results normally. Preserve user/import/OSM routes. Display the resulting re-solve requirement. Scene application and native model writes are separate operations with separate outcomes; failure of placement must not repeat asset creation.

## Generation and import paths

| Input | First deliverable | Limits |
|---|---|---|
| Coordinates, footprints, terrain | Native extrusion and coherent materials/roofs | Missing heights remain estimated; no demand inferred |
| GeoJSON layout | Validated polygons in explicit WGS84 or a reviewed transform | Reuse existing geometry import preview where possible |
| PNG/JPEG or rasterized PDF layout | Calibrated reference plane plus manual footprint tracing | Require scale and orientation before dimensional claims; PDF extraction is not automatic vector understanding |
| GLB from external editing | Bounded visual geometry plus reviewed identity/transform mapping | Metadata is untrusted; it does not authorize model changes |
| Multi-view photos | Optional reconstructed reference mesh and registration report | Quality/coverage/scale dependent; not automatically semantic buildings |
| DXF/IFC or point clouds | Later dedicated adapters | Do not advertise unsupported CAD/CRS/semantic conversions |

The 2D preview and 3D campus share normalized geometry. Generating a pretty 2D image and reconstructing it is not required when footprint data already exists. Image-assisted extraction is a proposal path with user correction. Check source terms for extraction, storage and derivatives; permission to display a tile is not evidence that derivative export is licensed.

## Blender integration

Two supported design directions, delivered separately:

1. Native generation is the default. A configured Blender worker can build/refine geometry and presentation renders from normalized, server-produced scene input. It uses a pinned trusted script and bounded runtime, outside the API process. Blender is not imported into FastAPI's interpreter or embedded as a web UI.
2. Export for Blender produces GLB and a versioned manifest with scene/model revisions, stable feature IDs, component references, units, axes, origin and hashes. An optional configured Blender worker may also produce a `.blend` for download. Browser users can export regardless of a desktop launch capability. Open in Blender is a later desktop-only convenience with an explicitly configured executable.

Returned GLB/manifest changes are proposals. Whitelist geometry and placement fields; retain provenance; compare against the exact exported base and current scene/model state; preview changed/unknown/deleted IDs; require explicit Apply. Refuse stale or ambiguous mappings. No input rating, efficiency, cost, topology, production setting or numerical result may be changed through mesh extras. If IDs are stripped, accept only a reference object until the user re-associates it.

Do not accept arbitrary uploaded `.blend` scripts for server execution. Worker-generated `.blend` output is not an inbound execution contract. Native fallback remains available when Blender is absent or fails. Heavy geometry is generated once, never once per result hour.

## Photos and reconstruction

Blender can clean, simplify, texture and export a mesh. Photo-to-geometry needs a photogrammetry engine such as OpenDroneMap or COLMAP. Select one worker after a representative prototype; do not ship all engines.

SC-06 first compares an aerial/campus image set and a ground-level building image set. Record overlapping registered views, missing coverage, camera reconstruction, scale/georeferencing source, alignment residuals, compute/memory/storage and mesh size. A single image supports a proposed footprint or stylized estimate, not a measured reconstruction. More images without overlap/viewpoint coverage do not establish better geometry.

Known distances, control points, geolocated photographs or a surveyed layout anchor scale/location; document how the selected engine uses them. An unscaled reconstruction may be viewed as a reference but cannot feed dimensional/clearance/cable claims. Reconstructed facades do not automatically identify buildings, loads, process units or equipment ratings: add/select semantic proxies for bindings and collisions.

COLMAP's documented dense reconstruction requires CUDA; sparse processing alone is not a textured 3D campus. Do not claim dense support on all desktop platforms. Worker capability readiness and resource ceilings are visible before submission. No paid hosted reconstruction or external photo transfer is implied by enabling the ordinary app.

## Job, storage and access contract

Use a deep scene-generation module: normalized owned inputs -> bounded proposal artifact -> explicit apply. Native and optional worker adapters share this contract. Scene revisions use compare-and-swap, not last-writer-wins.

Job states: queued, running, proposal_ready, applying, applied, failed, cancelled, expired. A completed worker is proposal_ready, not an automatically changed project. Record owner/project, input hashes/revisions, adapter/version, output hashes, resource observations and result. Late/cancelled/stale outputs cannot apply. Retries are explicit and cannot duplicate a successful apply. An unknown apply outcome is not reported as a failure safe to retry; read its durable receipt first.

Authorize uploads, preview, jobs, apply, export and artifact download independently. Read-only collaborators cannot persist scene changes. Workers receive only the admitted input snapshot and owned output directory, no provider secrets or arbitrary paths. Restrict archive traversal, file/pixel/triangle/texture counts, external resource references, decoded size and runtime. Production quota values are set from SC-00 benchmarks and are a release gate, not silently unlimited defaults. Keep the native API responsive; do not overload the existing electrical solve queue with arbitrary kinds without a reviewed adapter and persistence tests.

Keep original photos/layout uploads outside the project tree: an opaque app-data private-media store keyed by authorized organization, Project UUID and media UUID. Do not put originals or EXIF in `uploads/`, `sites/`, reports or history: current project routines recursively copy/export those directories. Scene records contain only opaque source handles and reviewed non-sensitive provenance, not original paths or EXIF.

Scenario/Save-As receives a new Project UUID and copies only the semantic scene and selected sanitized derived assets; originals are not copied or implicitly shared. Same-Project snapshots may retain opaque original handles, but restore rechecks current ownership, existence and retention and can show Original unavailable. Cross-project bundle restore never resolves a source handle against another Project. Original download and deletion recheck native access. Project deletion marks its private media for bounded cleanup after live jobs release their references; operator-configured retention is visible at upload, and expiry produces an unavailable source, not a corrupt scene. No delete/copy job scans an arbitrary uploaded path.

Bundles include only the pointed scene and selected derived assets. Derived thumbnails/meshes/textures are sanitized for EXIF/paths and source redistribution terms before becoming exportable; lack of rights produces named omission, not a hidden copy. Any future export of original photos is a separate explicit authorized action, outside the default bundle. A missing optional mesh retains semantic proxies and bindings. Restore never grants a former user's permissions. contracts.md pins this storage/export policy.

## Scope and sequencing

SC-00 has independent Native, Blender and Photo milestones, each with its own evidence/status. Native contract completion alone unblocks SC-01; absent Blender/CUDA does not prevent that milestone being done. SC-01 ships editable native geometry; SC-02 adds building/process bindings and placement; SC-03 adds native result projection. SC-04 outbound artifact work depends on native scene contracts; advertising Blender compatibility additionally requires the Blender exchange milestone. SC-05 requires its Blender worker evidence. SC-06 layout/manual calibration depends on Native only; photo reconstruction depends separately on Photo evidence. No aggregate parent status may hold those independent native slices behind optional engines.

This is an implementation specification proposal, not authorization to skip existing engine/UX review. No new engine physics, automatically discovered electrical networks, construction certification, rack-scale digital twin or live telemetry ingestion is included. Full BIM/IFC authoring remains a separate future capability.

## Research and evidence

- Blender export API: https://docs.blender.org/api/main/bpy.ops.export_scene.html — GLB/glTF, metadata and axes.
- Blender CLI: https://docs.blender.org/manual/ko/4.5/advanced/command_line/arguments.html — background/Python operation.
- OSM2World: https://osm2world.org/ — optional richer OSM geometry, export and deployment choices.
- Overture building schema: https://docs.overturemaps.org/schema/reference/buildings/building/ — optional height/floor data.
- COLMAP: https://colmap.github.io/ and https://colmap.github.io/faq.html — reconstruction pipeline and dense GPU limitation.
- OpenDroneMap: https://cloud.opendronemap.org/odm/ and https://docs.opendronemap.org/flying/ — aerial-image outputs and capture overlap.
- IfcOpenShell: https://docs.ifcopenshell.org/ifcopenshell-python/geometry_processing.html — later IFC geometry adapter.

The prior assessment's 544 existing unit/component tests passed on master-equivalent 3D files. They do not certify this proposed feature. No Blender/photogrammetry executable was installed or run, and no uploaded layout/photo was reconstructed in this specification task.

## Required interactive copilot configuration

The single companion supports guided initial configuration and subsequent adjustment, including geometry, asset placement, Load/facility mapping and supported native process/connection/parameter edits, through the existing native approval and scene proposal paths. It must distinguish model topology, scene association and result-display requests. The concrete conversation/action contract is in [ai-guided-project-flow.md](ai-guided-project-flow.md), with V91–V98; it is part of native release acceptance, not merely contextual explanations.
