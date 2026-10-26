<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Implementation plan and integration contracts

Status: needs-triage — proposed slices; no implementation started
Baseline: master `9988f9899a3bf8e80f51272529f18ea70599b90c`
Read with: spec.md, validation-matrix.md, HANDOFF.md and issues/01–08.

## Execution contract

Use [execution-guide.md](execution-guide.md) and [execution-manifest.yaml](execution-manifest.yaml) to assign bounded work. The manifest partitions SC-00 into N/B/P, SC-06 into layout/photo and SC-07 into foundation/bindings/results without adding user-facing modes or changing the eight tickets. Runtime units require accepted prerequisites and the applicable closed decisions in [decision-register.md](decision-register.md). Prototype units close decisions for acceptance; they do not require their own experimental outcomes before beginning. [fixtures-and-oracles.md](fixtures-and-oracles.md) and [implementation-record-template.md](implementation-record-template.md) specify expected evidence and a durable successor handoff. Initial source paths below are candidates; freeze a reviewed per-assignment allowlist against actual master before code.

## Architecture and ownership

Native model and results remain authoritative. A scene module owns physical features, groups, bindings and revisioned proposals. Adapters supply native geometry, optional Blender output or optional reconstructed reference geometry. The interactive Three.js viewer consumes the admitted scene and native result projection. No renderer calls a solver or grants permissions.

Keep the current Site sidecar and its serialized origin stable for unmigrated Sites. On explicit adoption, the pointed immutable scene generation becomes the sole authority for the enabled Site envelope and transforms; legacy GET is a projection and stale whole-document PUT is refused. Never dual-write independent placement documents. contracts.md defines the single commit point, concurrency and private-media behavior. Do not turn every mesh into an electrical component. Preserve the default library and old projects. A native feature has a simple geometric proxy even when its optional mesh cannot load.

### Proposed serialized records

These are design contracts, not current routes or classes:

- Scene: schema_version, site_id, revision, site_document_digest, coordinate_frame, sources, features, groups, bindings, artifact_refs, receipts.
- Source: owned ID, type, original hash, license/derivative permission record, acquisition time, original units/CRS and transform, quality observations.
- Feature: opaque ID, type, label, footprint, base_elevation_m, height_m, roof surfaces, geometry/mesh references, source ID and dimension evidence classification.
- Binding: ID, feature/group ID, native target reference/generation, semantic role, carrier/port/quantity, optional explicit allocation, revision validated against.
- Proposal: ID, owner/project/site, base scene/site/target digests, proposed changes, adapter/version, warnings, output hashes, validity/expiry and job state.
- Receipt: proposal ID, admitted revision, resulting revision, effect summary and status. A duplicate apply returns the established receipt, not another write.

Propose authenticated GET/PUT scene, create/read/cancel scene job, preview/apply proposal and create/read/download export operations under existing project/site routing. Before code, enumerate exact method/path/request/error contracts against current OpenAPI. Every new route gets a stated chat disposition in E's route coverage test; no new chatbot tool is silently implied. SC-07 explicitly specifies the proposed bounded scene-context and prepare/apply adapters, subject to frozen contracts and normal review. Reuse ProjectAccessDep, edit locks, upload guards and owned artifact delivery rather than importing their internals into worker math.

Scene persistence uses expected_revision. Initial read of a legacy project synthesizes an empty scene without writing or migrating it. Atomic apply verifies all revisions, ownership, target identity and artifact hashes before atomically switching the current-generation pointer. A half-written generation is not visible and cannot become a fallback source. Model mutations are separate native commands; there is no scene-to-network back door. A placement failure after model creation preserves the native asset and explains the unfinished placement instead of retrying creation.

The coordinate/boundary-only action supports a saved Project with an empty network and a Site with no member buses. Explicitly replace the new path's dependency on `siteReadiness` and bus-centred `SiteEmptyState.generate`; do not silently create a temporary Bus/branch or require an electrical connection first. Server `/flow` action verdicts own UI availability, source/computation disclosure and blockers. Submit and Apply recheck admission even after a green verdict; these scene actions never alter the electrical run verdict.

## Source rebaseline and likely touch points

Recheck current master and AGENTS before each slice. These paths are an initial map, not a blanket file allowlist:

- Existing Site geometry/store: frontend `site3d/types.ts`, `siteModel.ts`, `sitesStore.ts` (debounce/flush/unload and authority transition), `readiness.ts`, `layout.ts`, `placementCheck.ts`, `context.ts`, `geo.ts`; backend `services/site_service.py`, `site_context.py`; site routes in `routers/projects.py`.
- Scene UI: `pages/SiteCanvas.tsx`, SiteEmptyState, SiteOverlay and the current selection/properties host. Extract cohesive scene editing helpers rather than growing SiteCanvas indiscriminately.
- Model binding: existing network CRUD/rename/delete hooks, project sidecar carry, `models/process_unit.py`, `services/process_products.py`; use the existing model schema routes to enumerate targets/units.
- Results: `site3d/useSiteResults.ts`, resultStyle/results stores and shared timeline; `services/results/process_view.py` and actual Link/Product/Store series. Its current process-unit summary is not an hourly throughput payload.
- Exchange: new pure scene manifest/GLB normalization module; existing heroLoader/noCdn/bounds tests guide browser asset constraints but are not a general arbitrary-scene importer.
- Jobs: existing solve queue is electrically specialized. Decide a separate bounded scene job runner versus a properly typed adapter using measured prototype evidence; do not insert an arbitrary job kind that falls through to solving.
- Permissions: new routes in `services/not_via_chat.py` and `test_route_chat_disposition.py`; tool inventory only changes if a separately approved chat tool is actually added.
- AI guidance: existing harness compose/workflows/dispatch/eventlog/navigation and native edit-card contracts; companion per-turn UI context and flow-navigation return anchors. See ai-guided-project-flow.md; no second executor or provider stack.
- Flow authority: `services/flow_service.py`, `routers/flow.py` and frontend flow consumers; additive scene action verdicts, with existing run_ready behavior pinned unchanged.
- Original-media privacy: new app-data private store outside the project tree, authorized by Project UUID via existing registry/ACL; copy/snapshot/export/import/delete lifecycle tests in the current project paths. Do not inherit uploads/sites recursive export for originals.
- Enabled-scene export/copy: replace the recursive sites/ subtree copy with the locked-generation manifest-closure builder in contracts.md. Include only authorized referenced derived hashes, reject missing required artifacts, exclude marked orphan/superseded/failed state and rewrite destination-owned references. The current recursive copier does not provide these semantics.

Current outsourced G-a/G-b/H/I/J/K/L scopes do not include these new scene files. Shared projects route, result/prompt, upload and queue files still require a fresh overlap check with all open PRs. No changes to their branches are authorized by this plan.

## Build sequence

1. SC-00: prototype and freeze contracts in three independently accepted milestones: N (Native: empty-network generation, authority/CAS/privacy/flow and measured native limits), B (Blender: actual exchange/worker readiness and its limits), P (Photo: actual supported reconstruction/registration and its limits). Publish exact inputs, outputs, measurements and failures separately. Native completion unblocks SC-01 without waiting for B/P. No engine selection based solely on a marketing screenshot.
2. SC-01: native feature records and editable scene, depending only on SC-00 N. Red tests at owned scene HTTP/store, `/flow` and visible Site 3D seams precede code. Add polygons/heights/proxies, provenance, manual geometry correction and legacy read compatibility. Promote context buildings without duplicates. Prove coordinate-only generation on an empty network with no invented model components; adopt canonical scene writes and reject stale legacy debounces/unload PUTs.
3. SC-02: explicit bindings/groups and building-aware placement. Establish rename/delete/recreate identity before UI binding. Test Load per carrier, Process-unit Link and Inventory Store. Add groups, roof/ground placement, collision/service proxies and supported catalogue scale. Add model assets only through existing native controls. Require engine/UX plan review for the relevant seams.
4. SC-03: native result projection. Bind scenes to exact current run/time and existing selected snapshot. Add missing hourly process adapters only after their units/ports are reviewed; do not infer from summaries. Single authoritative aggregation/allocation path; unavailable values remain unavailable.
5. SC-04: outbound GLB/manifest work depends on native scene contracts, not a required Blender install. Advertise tested Blender compatibility only after SC-00 B's exchange evidence; a missing worker does not block building the native artifact. Deterministic metadata, native scale and re-openable result bindings. Exports do not imply source photo redistribution or current numerical validation.
6. SC-05: configured Blender worker and controlled return. Trusted pinned scripts, capability readiness, runtime limits, cancellation, export base IDs, preview and explicit apply. Desktop launch optional. Reference-only import for unmappable geometry; no arbitrary .blend execution.
7. SC-06: uploaded-layout adapters and photo reconstruction. The layout/manual-calibration portion depends on N and its own file-policy evidence, not B/P. Photo depends on P and the common proposal lifecycle, not necessarily Blender. Vector CAD/IFC extraction and photogrammetry have independent adapter/evidence gates. Use one proven reconstruction worker, not several heavy dependencies. Photo output is reference geometry until semantic proxies and metric registration are established.

8. SC-07: integrated AI guidance, context, typed scene adapters and recovery alongside SC-01–SC-03; do not defer it until optional engines. SC-00 N freezes these contracts and the native integrated release includes V61–V72. Optional engine guidance is enabled only with its own capability. See issues/08-ai-guided-project-flow.md and ai-guided-project-flow.md.

Do not combine all slices into one implementation PR. Each PR carries tests, the applicable release fragment, changed route dispositions, screenshots in Guided/Expert if UI changes, and a current handoff. Specs alone do not receive implementation pass/coverage claims.

## User-facing integration

Follow optional-site-workflow.md and workflow-assessment.md. SC-00 N freezes the site-anchor service and separate action/target verdicts. SC-01 exposes optional Create/Open/Resume by the native site coordinates, campus mapping and saveable buildings-only state. SC-02 projects existing2D/SLD native edges/ports and physical locations without duplicating topology; source-dependent cable/pipe layers distinguish logical connectivity, route geometry and result availability. SC-03 uses the native snapshot cadence, including subhourly/representative/multi-period cases; no forced hourly animation. Add V73–V90 before code.

Follow ai-guided-project-flow.md throughout: one project flow and one companion, server-derived completion, confirmed actions and durable read-back, manual/no-provider parity. Retain the existing Site 3D destination. An initial source choice (Location, Layout or Photos when available) leads to a common building preview. A source-specific deficiency opens calibration/correction in place. Show observed/estimated/proposed distinctions and an inspectable source line.

The backend `/flow` projection exclusively supplies action availability, blockers and the computation description. The frontend cannot enable Photos/Blender because a browser/device check says so. Loss of worker readiness, access or limits between verdict, submit and Apply is refused server-side, with an updated in-place state. Original input ingestion uses the private-media policy in contracts.md, not the generic project uploads directory.

Selection opens Building/Area associations or existing equipment properties. Carrier-specific loads and process units are distinct choices. A process group can span a hall and outside inventory; it does not imply a second energy connection. Results controls remain the shared timeline. Export/refine actions live in the existing scene action area; a worker failure does not hide native generation.

## Native model and result rules

- Default geometry/collision operations consume physical dimensions; generic fitting is not allowed for source-verified dimensions.
- Carrier values and product units come from the model. No energy/material conversion without an explicit native factor.
- A process group reports only an explicitly defined supported quantity. Internal flows are excluded from boundary totals by a native adapter, not guessed by frontend summation.
- All unresolved serialized numeric quantities have value null and explicit available=false plus reason; genuine resolved zero has available=true. Missing factor, missing process series and unsupported group quantity are exact JSON assertions, per ADR-0001.
- One component rendered as multiple units is counted once. An aggregate load has no invented building split.
- Input edits clear or mark stale native results using existing contracts; changing scenery cannot manufacture a fresh result.
- Geometry-derived electrical lengths remain behind the current opt-in, preserve established user routes and use native write/read-back semantics. Initially omit automatic length derivation from imported arbitrary meshes entirely.

## Prototype decisions to close before implementation

1. Supported photo worker: evaluate OpenDroneMap for geolocated aerial sets; COLMAP-based pipeline for general images only with a demonstrated dense/texturing stage and GPU availability. No promise of dense CUDA work on unsupported Mac hardware.
2. Worker packaging: configured executable/container versus managed optional worker. No mandatory Blender/photogrammetry install for the base app. Native, Blender and photo capabilities have independent readiness states.
3. Exact public contracts, revision strategy and model target lifecycle hooks. An absent stable model identity mechanism must be solved explicitly in SC-02.
4. Maximum photo count/decoded pixels, worker CPU/RAM/time, artifact size/triangles/textures and scene latency/frame-rate budgets. Freeze measured thresholds and positive/negative fixtures before enabling uploads or claiming performance.
5. A representative existing site and greenfield site with redistributable/licensed data, a scale/control reference, and a native multi-energy/process fixture. No customer photos are published in Git by default.

## Verification and release

Use validation-matrix.md. Public seams are owned HTTP operations, Site 3D user journeys, model mutation/result outputs and external exchange artifacts. Only external worker execution and remote acquisition are substituted in fast tests; algebra and native result projection stay real. Execute a separate genuine Blender/photo integration on supported resources before exposing those capabilities.

Full applicable frontend/backend tests, inventory/control/route contracts and current-head CI remain required per repo rules. Existing 544-test evidence is baseline only. Performance/visual checks include a real packaged-device run, not jsdom alone. Plan approval and later independent review remain open; no approval is implied by this document.

## Interactive configuration delivery

SC-00 N freezes the supported scene/model/display intent map and SC-07 orchestrates it through existing task/dispatch/native edit cards and the shared scene prepare/apply path. SC-01 supplies geometry changes, SC-02 exact asset/Load/facility/port associations and SC-03 supported result-channel projections. V91–V98 exercise initial guidance and iterative adjustment with exact approvals and read-back. Model edits remain independently validated native commands; an unsupported field/port/tool is disclosed rather than routed through geometry or invented generic execution.
