<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Optional site creation, model mapping and result playback

Status: proposed normative clarification — owner requirement 2026-10-24; not implemented
Read with: [spec.md](spec.md), [contracts.md](contracts.md), [ai-guided-project-flow.md](ai-guided-project-flow.md) and [validation-matrix.md](validation-matrix.md).

## Product decision and entry point

3D site creation is an optional enrichment of the existing project, not a required onboarding task, a single-line diagram (SLD), or a seventh analysis card. All ordinary 2D map/topology, model editing, parameterization, study and reporting work remains usable without it. Use one reusable Create 3D site action in the existing Project & site coordinate/configuration area; the existing Site 3D view shows the same task/context rather than a second independent builder. Both Guided and Expert share the same native records and gates. After a scene exists, the action becomes Open 3D site / Resume site setup as appropriate (see the SC-00 N UI contract below).

Before the user supplies or confirms a valid native site coordinate, show the creation action disabled with the reason Add site coordinates to create a 3D site and an in-place link to the coordinate field. Do not hide it, enable it from a component-count heuristic or trigger generation on project/template load, coordinate entry, a view toggle, hover or chat opening. A selected view/toggle only opens/closes the workspace; it is not an execution/approval control. Closing 3D never deletes the site, clears bindings, cancels a job implicitly or changes the analysis scope.

Creation-entry admission requires an authorized saved Project, edit access and a confirmed finite WGS84 site anchor (latitude -90…90, longitude -180…180) from the native site configuration. No Bus, branch, asset, profile, solve, Blender or photo engine is required to open campus mapping. An unsaved project shows Save project first, pointing to Save in the header. Geographic/projection limits are separately disclosed as unsupported where applicable; never clamp a valid coordinate to fit a map/worker. A default bus at 0/0 is not a confirmed site location; an explicitly confirmed real 0/0 coordinate is valid. Imported coordinates require known CRS/transform and confirmation; a layout/photo with unknown georeferencing cannot bypass this requirement.

The new native site anchor must have an explicit owner/project/site-or-creation-context identity and revision. SC-00 N must inspect the current Project & site location seam and freeze its actual settings DTO/persistence/ACL path, including multiple Sites, before implementation; do not assume a project-level coordinate field already exists or create a dummy Bus to supply one. Proposed anchor fields are lng, lat, source, confirmed and revision; exact wire names are frozen by that milestone. The anchor locates campus mapping; an admitted Site's fixed rendering origin remains its established origin. A new draft cannot silently recenter an existing scene. Clearing a draft anchor disables new creation; an accepted scene remains viewable with its stored origin. Replacing an accepted location requires an explicit migration/re-registration preview, not automatic relocation of buildings and model assets.

### SC-00 N UI contract

Mirror of the same section in [issues/01-prototype-and-contracts.md](issues/01-prototype-and-contracts.md); a change to one changes both. SC-00 N freezes these UI rules; SC-01 builds them.

1. **Current UI names.** The view is "Site 3D": the label of the `site` entry in `MapModeSwitcher` (`pypsa-gui/frontend/src/components/MapModeSwitcher.tsx`). The package uses "Site 3D" for it everywhere. A rename of that switch label is frozen in the same slice that makes it, never as a second name for the same view.
2. **One entry point, including the view switch.** "Create 3D site" in Project & site is the one creation action. Site 3D with no scene shows an empty state: the current action state from rule 3 (the disabled reason, or the enabled action's label) and a link to that one "Create 3D site" action. The empty state is not a second creation control. Today's `SiteEmptyState` (`components/SiteEmptyState.tsx`) has its own "Generate 3D site" button and points to "New site" on the Satellite map; SC-01 folds both into the one action rather than keeping them beside it.
3. **One visible action label per state.**

   | State | Visible action | Reason and link |
   |---|---|---|
   | No confirmed anchor | "Create 3D site", disabled | "Add site coordinates to create a 3D site" › the coordinate field |
   | Unsaved project | "Save project first" | Points to Save in the header; no second save button |
   | Anchor, no Site | "Create 3D site" | — |
   | Site with unfinished parts | "Resume site setup" | — |
   | Accepted scene | "Open 3D site" | — |

4. **Homes and return, in both modes.** Each link a part uses has one Guided home and one Expert home (section ids and panels from `layout/flowSections.ts`, `SECTION_OPEN` and `sectionForComponent`):

   | Link | Guided home | Expert home |
   |---|---|---|
   | Coordinate field | Project & site card (`project`), field drawer | `horizon` panel. D01 freezes the field; if it lands elsewhere, both homes are re-frozen in that slice |
   | Loads | Loads & demand card (`loads`), the Load's drawer | Loads table, the Load selected |
   | Generation, storage | Assets & sizing card (`assets`), the component's drawer | `capacityBounds` panel / the component's Properties |
   | Process | Carriers & processes card (`carriers`), the unit's drawer | Carriers table / the unit's Properties |
   | Connection/PoC, transformer | Connection & grid card (`connection`), the field's or Transformer's drawer | `campusElectrical` panel / the Transformer's Properties |
   | Native result selection | Results → reports (`results`) | `results` panel, the run tab via `showResultsTab` |

   Every jump that leaves Site 3D goes through `withReturn(...)` (`layout/flowNavigation.ts`, via `openFlowTarget` or `openFlowTargetInPlace`), shows "Back to 3D site" and lands on the same Site, part and selection. Today's `FlowReturn` records mode, section, panel, drawer, selected component and scroll, but not the map mode, Site id or part; SC-00 N freezes that extension and SC-01 adds it. A deep link to a block uses request-then-scroll (as `requestGridReadiness` does), never a bare `setSlidePanel`. In Guided the jump opens in place (`openFlowTargetInPlace`) and never switches to Expert.
5. **The four parts are not a stepper.** Part progress shows inside the Site 3D context only. It adds no rail, card or section-completion signal to the flow sidebar.
6. **Wording.** A colon, not an em dash: "Buildings only: configure model elements to map them". Every unavailable layer or target shows a visible reason naming the missing input, with one "›" link to its home from rule 4. Never a tooltip alone, never one global "not ready".
7. **Evidence.** Any UI SC-00 ships, even a prototype panel, needs real-app screenshots of each action state in rule 3, in Guided and in Expert.

## Four resumable parts of one workflow

| Part | User task and native prerequisite | What is available / what waits |
|---|---|---|
| 1. Map campus | From the confirmed anchor, inspect the existing map, draw/confirm the campus boundary and review source footprints or proposed greenfield buildings | No electrical model needed. Boundary/footprint correction and optional layouts/photos inform generation. At least a valid confirmed boundary is required before materializing a Site; do not save an invalid boundary-less legacy Site. |
| 2. Generate and review | Submit the selected native/source inputs, preview the actual 3D geometry and source/scale labels; adjust geometry or explicitly regenerate, then accept | Native extrusion/manual building proxies work without optional engines. A render image is a preview of the scene, not evidence that photo reconstruction or a semantically bound model exists. A bare greenfield plot may be saved with no buildings/assets. |
| 3. Map configured model | List and explicitly associate the project's existing Loads, generation, storage, native process units, connection/PoC/transformer and topology with buildings/areas/terminals | If no relevant native targets exist, save the buildings and pause here. Configure those entities in their normal 2D/native editors and return. Partial mapping is allowed for valid existing targets; missing unrelated inputs do not block it. No synthetic load, asset or connection is inferred from scenery. |
| 4. Play results | Select an available native run/period/snapshot window whose target identities and inputs match the bindings | Play native values on the shared timeline at their actual resolution. Missing, unsolved, stale or unsupported quantities remain unavailable; no animation invents results or substitutes for a simulation. |

These are tasks inside the existing project flow, not another required rail or an independent completion authority. A user can stop after Part2, continue ordinary parameterization, then resume Part3 with the same Site/features, manual corrections and camera/selection context where available. Switching away/reloading reads authoritative scene/model state, never re-creates assets or restarts jobs. A missing optional part does not mark unrelated project sections incomplete or disable a valid native run. Study scope is still the approved six-card model and its server projection.

For a new location, campus boundary confirmation materializes the valid empty Site through the ordinary owned write path; geometry/proposals follow the canonical generation contract. Optional layout/photo ingestion in this first slice occurs after that valid envelope exists, while refining the campus before generation. Do not silently add a separate pre-Site media/draft API. If future pre-boundary file ingestion is needed, scope its owner/draft lifecycle explicitly before code. An initial boundary inferred around the anchor is only a visible proposal until confirmed; it is not a measured parcel or property-rights boundary.

## Native generation, review and regeneration

Reuse current map/context/terrain sources, shared 2D polygons, Three.js/React Three Fiber, existing parametric assets and thumbnails as the default. Missing footprints can be drawn manually or entered as explicitly proposed greenfield buildings. Keep source height, estimated height, dimensions and alignment inspectable. Native generation constructs scene geometry directly; generating a pretty 2D image and trying to recover 3D from it is not a mandatory step.

Layouts and suitable photographs are optional source evidence for alignment, dimensions and appearance under their existing engine/file/privacy/rights gates. A single photo may guide appearance or a reference plane but cannot establish a measured metric campus. Explain unsupported source/worker states in place; do not hold native/manual creation for a missing photo engine. Blender remains optional refinement/export, not the required UI or renderer.

A generation/refinement request produces a proposal with the exact base revision, inputs, source IDs, parameters, warnings and output hashes. Preview, correct and accept through the normal Apply gate. Regenerate is an explicit new request with bounded resources; there is no automatic model/worker retry loop based on a vague quality score. Keep the previous accepted generation intact if the new proposal fails, is cancelled or is rejected.

Preserve known stable feature IDs and user-locked corrections only where identity is proven. If regenerated geometry splits/merges/replaces a building, preview every affected binding and require explicit rebind or an unbound state; do not guess by label or nearest position. A regeneration must never silently remove an asset, move an electrical connection, restore stale results, or double-count logical capacities. Images/material changes are visual effects; model changes remain separate approved commands.

## Map model targets and connectivity, not a second network

Part3 consumes the same configured network/SLD/topology that the 2D map, native tables and existing editors use. The physical 3D view associates that graph with real/proposed locations; it is not an independent topology store. Existing model parameters and native data remain authoritative. Users may have already parameterized the model before creating3D, or build the campus first: both entry orders converge on the same mapping service.

Admission is per requested target/quantity, not a requirement that the entire project is run-ready. A target must exist in the current authorized model, have enforceable lifecycle identity and valid relevant native references/ports/carrier semantics before it binds. Missing load profiles can block result playback without blocking a structurally valid load association. If no targets exist, show Buildings only: configure model elements to map them with in-place links to the owning section and a return anchor. Do not ask the assistant to manufacture missing entities just to complete 3D. A decorative/reference object is clearly separate from a bound model asset.

Support multiple independent loads/carriers in one building; generator/PV fields at actual configured locations; a battery's logical identity and visible enclosures; and process-unit/Inventory associations inside or outside buildings. Show aggregate versus explicitly allocated building demand. Reuse reviewed native material/energy factors; process inputs, useful outputs and inventories have different roles and units.

The PoC is a connection role resolved from the existing native model, not a new standalone component class. The adapter reads Bus.eh_poc, role-tagged import/export Links and the existing commercial poc_link/export_link configuration according to their native scope; related Transformer terminals remain their own native references. Preserve multiple PoCs and import/export links, including declared direction and applicable configuration, without arbitrarily collapsing them to one. Missing, conflicting or ambiguous role/terminal relations remain unavailable for that projection and link to the native editor. Show the actual relation; do not infer it from proximity or call the nearest transformer the PoC. Geographic coordinates supplied for a physically external asset remain external map/reference context; do not clamp it inside the campus. A commercial PPA does not by itself authorize drawing a physical cable.

Represent electricity, heat, hydrogen and other supported energy/material connections as typed scene projections of current native edges/ports, using shared lifecycle references. A proposed connection visual contains native edge/port IDs, carrier/role, scene terminal references, route source/mode, source revision and availability. It carries no duplicate editable bus0/bus1 or process recipe authority. Reuse the native 2D topology/result adapters and add narrowly scoped port/scene mapping adapters, not another solver or hydraulic/EMT engine.

Connection rendering has three independent availability facts: native logical connectivity, physical route geometry, and result value. Known logical connectivity can use a visibly schematic connector when a route is absent, labelled Route not configured; it cannot claim actual cable/pipe length, burial or engineering clearance. A known route can be shown without a solved flow; geometry does not imply current flow data. Underground cable/pipe layers are hidden by default and revealed through existing layer controls when the corresponding data exists. Do not invent cable size, burial depth or pipe physics. Connectivity gaps show source/target and the native owning editor to fix. Visual route edits do not modify topology; any opted-in electrical length change retains the separately approved native write/invalidation contract.

A native graph change revalidates affected bindings/ports/routes. Rename preserves only proven lifecycle identity, delete breaks it and same-name recreation requires rebind. Invalid mappings do not gain an implicit nearest-name or nearest-location fallback. Legacy asset-driven scenes keep their established behavior; the new geometry-first path never requires assets before buildings.

## Results and AI guidance

Use the current native run, period and snapshot selection, units, weighting and freshness gates. Playback follows actual native cadence: hourly, quarter-hourly or another supported source interval; do not hard-code 8760 steps or assume every snapshot represents one hour. Representative samples and period boundaries retain their native labels/weights and are not presented as continuous measured telemetry. A smoother camera/arrow animation is rendering only; it cannot imply additional sub-step observations, transient response or a new dynamic simulation. Heavy geometry/reconstruction is generated once per accepted revision, not once per result tick.

Partial mapping or missing series disables only the affected overlay; other substantiated quantities remain playable. Block the whole playback action only when no eligible fresh native data can be read. At each bound building/equipment/process, show only native quantities the adapter can substantiate: load/supply/charging state or supported process input/output with exact port/sign/unit, timestamp and source. Process boundary totals avoid internal-transfer double counting. Unknown data returns value:null / available:false / reason; zero remains a real zero. Whole-site operation can be inspected through existing result summaries and visible supported flow layers without replacing the 2D/native result tools.

The single companion follows the current part, selected feature/model target, project flow and native result window. It explains why Create/Assign/Play is unavailable, proposes corrections/associations through typed approved tools, and offers Resume after ordinary parameterization. No provider is needed for the manual path. It cannot invent input data, turn geometry into an engineering verdict, certify an unavailable result or automatically Apply a completed worker proposal. Preserve ai-guided-project-flow.md's context privacy, confirmations, ordinary task execution and receipt/reconciliation rules.

## Server readiness and delivery

Add distinct `/flow` scene-action observations for open_site_creation, create_plot, generate_native, assign_native, show_connectivity, play_results and the existing optional-worker/export/Apply actions. Freeze exact DTOs in SC-00 N. Return action availability, named missing inputs and a home/return target; show per-target and per-layer partial availability rather than one global3D-ready boolean. The frontend may validate coordinate syntax but may not establish availability independently. Submit, bind and Apply recheck current authorization, coordinate/source/model identities, revisions, policy and limits.

SC-00 N freezes anchor persistence, staged action verdicts, native graph/terminal projection and return-state contracts with a scripted native journey. SC-01 delivers optional coordinate-first campus mapping/review/save/resume; SC-02 delivers valid target/PoC/multi-carrier route projection; SC-03 delivers cadence-aware native playback. SC-04–SC-06 remain independent optional enrichment. SC-07 accompanies every part. Use V73–V90, earlier invariants and domain fixtures. No new creation runtime is implemented by publishing this clarification.

## Companion-driven configuration is required

The companion must guide and adjust the site through supported admitted actions: campus geometry, placements, exact Load/facility associations, process/asset ports and result overlays. Underlying network/parameter changes use their existing native edit/approval paths, separate from scene changes. [ai-guided-project-flow.md](ai-guided-project-flow.md) specifies the conversational intents, effect classification, clarification and receipt contract; V91–V98 validate actual orchestration rather than a help-only chat surface.
