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
| V61 | Integrated native journey | Blank/template project, horizon, geometry-first Site, carriers/processes, native loads/assets and approved placement, selected native analysis and evidence-grounded results execute through existing flow/companion with no duplicate editor or model entity; both Guided/Expert preserve the six-card scope including Hand-off and its existing outputs. |
| V62 | Per-turn scope and selection | Feature/section/run/period change refreshes bounded authorized context; tampered client owner/site/target hints cannot disclose or mutate foreign scope; no mesh/original bytes injected. |
| V63 | Native progression | Assistant advance/completion claim, stale history and finished job cannot mark native sections complete; fresh /flow controls complete/warning/incomplete/not_needed/unknown and blockers. |
| V64 | Exact assisted confirmation | Prepare is not a read exemption; changed preview/hash/base/policy/restriction, denial, expiry or revoked access refuses Apply; scene approval never authorizes native model write, solve or media transfer. |
| V65 | Partial effects and retries | Asset creation succeeds but placement fails; receipt retains one asset, resume proposes placement only. Disconnect after Apply reconciles the receipt before retry; no duplicate write/job or unbounded retry loop. |
| V66 | Resume and project switch | Reload/reconnect/session loss reads native state and offers Resume without re-execution/approval restoration; switching scenario/project clears old guidance/targets; inaccessible history stays unavailable. |
| V67 | No-provider/manual parity | Provider absent/down, closed companion, unsupported voice and optional workers absent still permit full native/manual flow; opening/selecting alone triggers no inference/worker/charges. |
| V68 | Ambiguity and untrusted inputs | Ambiguous building/model label, unknown units and missing cost/load require clarification; hostile OCR/source text cannot change instructions, invoke arbitrary code or supply fabricated model parameters. |
| V69 | Evidence-grounded explanations | Current/historical/wrong-period/unsolved/stale/missing results produce exact native values/units/source or explicit unavailable reason; process mass/energy and allocated demand are distinguished, no compliance claim from scene appearance. |
| V70 | In-place navigation parity | Help opens the entity's existing editor with return anchor/selection retained; missing/negative browser ACK is not reported as success; Guided/Expert use the same native controls and approvals. |
| V71 | Tool and checked-path parity | New scene adapters satisfy catalogue/handler/routes/safety/edit/error and provider bounded-offer regressions, generated inventory/control and route disposition checks; unmapped strict-checked tools refuse and UI fallback is truthful. |
| V72 | Bounded jobs and quality evidence | Scene worker cancel/timeout/late completion never auto-Applies/retries/runs a study; task/context limits stop work. Scripted CI validates contracts with real native services; conversational-quality claims require separate authorized recorded real-provider evaluation. |
| V73 | Optional coordinate entry | No confirmed location: visible Create 3D action is disabled with native blocker/coordinate-field target; empty/NaN/infinite/out-of-range inputs refuse; a swapped latitude outside its range refuses, while numerically valid ambiguous pairs require confirmation rather than an invented axis correction. Explicit confirmed 0/0 is valid; unconfirmed bus placeholders do not enable creation. |
| V74 | Open versus generate | Saved owned project with confirmed coordinate and no Bus/branch opens campus mapping; Generate still needs confirmed valid boundary/inputs. Unsaved/read-only/foreign contexts refuse mutation; no invalid boundary-less Site or dummy model entity persists. |
| V75 | Opt-in and ordinary workflow | Template/project load, coordinate entry, view toggle/hover and opening chat trigger no generation/worker/LLM call. Declining/closing 3D preserves 2D editors, study scope/run_ready, saved scene and ordinary parameterization. |
| V76 | Anchor and existing origin | Multiple-site contexts resolve the intended anchor; clearing an uncommitted anchor disables new creation but accepted scene stays viewable. Changing an accepted location requires explicit preview/re-registration and never silently moves native/bound geometry. |
| V77 | Campus mapping first | Confirm a source/manual boundary and footprints, including a greenfield plot; distinguish proposed extent from measured parcel. Optional layout/photo refinement uses the valid Site and known transform/scale; no coordinate/CRS bypass. |
| V78 | Review and regenerate | Preview/correct/regenerate/accept is explicit; failed/cancelled/rejected regeneration preserves previous accepted geometry and receipts, with no automatic model mutation or retry loop. Image preview alone is not reported as a completed bound3D model. |
| V79 | Buildings-only continuation | With no Loads/assets/processes, save accepted buildings, reload/switch sections, parameterize in normal native/2D editors and Resume the same Site without recreating geometry or model entities. Assign controls explain absent targets. |
| V80 | Per-target mapping readiness | Valid existing Load/battery/generator can map while unrelated project inputs remain incomplete. Missing target or relevant bus/port/carrier identity refuses that binding; missing profile blocks numeric playback rather than a structurally valid association. |
| V81 | Both entry orders | Geometry-first and already-configured-model projects yield the same explicit feature/target associations, multiple loads per building and conserved logical equipment count; no approximate-name/proximity binding. |
| V82 | Native graph/PoC projection | 3D connectivity matches the authoritative 2D/native graph, existing Bus.eh_poc, role/config-selected import/export Links and separate Transformer terminal references. Multiple PoCs and conflicting roles remain distinct/ambiguous; no new PoC class is created. A nearer unrelated transformer is not selected; missing native edge/role is unresolved. Visual route edits do not rewrite topology. |
| V83 | Model lifecycle and return | Rename/delete/recreate, native editor return and graph revision changes preserve only proven identity; invalid/stale target/port bindings are disclosed before Apply/playback, no silent rebinding. |
| V84 | Regenerated feature identity | Splitting/merging/replacing a bound building previews affected mappings and requires explicit rebind/unbound state; locked corrections persist only with proven identity; no duplicated capacities. |
| V85 | Logical versus physical route | Known native edge with no route is labelled schematic/Route not configured, without metric cable/pipe/burial claims. Known route with no results has geometry but no invented flow. Underground layers default hidden; per-layer availability is honest. |
| V86 | Multi-carrier/process placement | Electricity, heat and H2 ports map to a native process in the selected building/facility, with correct roles/units. External physical generation keeps known external location; a financial PPA creates no physical cable. Unsupported carrier/port quantity stays unavailable. |
| V87 | Native temporal cadence | Fixed 15-minute and hourly fixtures, irregular/representative samples and multi-period boundaries use exact native timestamps/weights. No hard-coded 8760/one-hour index; rendering interpolation adds no numeric observations or transient claims. |
| V88 | Native operational view | Known snapshot load/PV/battery/process input/output and supported connection flows match independent native values, signs and units; stale/unsolved/wrong-run/missing-port data stay unavailable. No per-tick reconstruction or solver call. |
| V89 | Guided companion stages | One companion explains coordinate, campus, target and result blockers, offers native editor return/Resume and exact confirmed changes. No-provider manual path reaches the same states; it cannot force 3D or fabricate missing model inputs. |
| V90 | Optional sources and engines | Layout/photos/Blender are absent, unsupported, revoked or fail; native/manual campus creation, saved buildings and ordinary model work remain usable. A lone image never certifies metric geometry or semantic energy mapping. |

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
