# Visual layers, plan 3 of 3 — landing the 3D site view and making placement mean something

**Date:** 2026-10-06
**Parent:** `docs/superpowers/assessments/2026-10-06-campus-visual-layers-assessment.md` (§4, §5, §7 and the owner decisions of 2026-10-06); the 3D session's own documents on `origin/claude/3d-site-visualization-gatc5z` (`docs/superpowers/{assessments,specs,plans,notes}/*3d-site-view*`)
**Siblings:** plan 1 (abstract canvas), plan 2 (map view)
**Status:** plan, written before the code
**Target:** `master`; S0 is one PR, every later increment its own

## The goal

The 3D site view is built: two phases, spike to animated results, on a branch forked 2026-09-28 that is now 375 commits behind master and has no pull request. The first job is to land it before it rots. The second is the owner's new direction (2026-10-06): **placement carries meaning**. A transformer stands where a transformer stands, between the yards of its two buses, not behind the hall; two transformers are two objects; what the user arranges is plausible and checked, and the distances it implies can feed the model. The third is the owner's generation rule: the 3D site is **generated on demand from the assets**, through a library, when the user asks for it after the model has connections and coordinates; never at project or template creation. The view's purpose is visualisation and user experience; where it can add value beyond that, the plan says where and keeps it optional.

## What exists (the branch, surveyed 2026-10-06)

- `pages/SiteCanvas.tsx` (949 lines) plus ~30 pure modules under `frontend/src/site3d/`: tangent plane and tiles (`geo.ts`), the packer (`layout.ts`: one yard per member bus, components shelf-packed into zones north / west / east / northeast / south / roof around it, a placed object drawn at its placement verbatim), the data-driven asset library (`assetLibrary.ts`: 17 asset + 4 infrastructure types; match by class, carrier, far-side carrier, port; sizing rule; template; land rule; zone), templates (`templates.ts`: unitGrid, tankArray, turbineArray, pvField/pvRoof, hall, yard, bay, transformer, reservoir, manifold, composite; each declares animation anchors), hero models (`heroes.ts`, `heroLoader.tsx`: five Kenney CC0 GLBs, manifest with bounds, pivot, fit mode, instanced), results (`useSiteResults.ts`, `useDispatchFresh.ts`, `resultStyle.ts`, `resultsLayer.tsx`, `motion.ts`), the sidecar store (`sitesStore.ts`), boundary maths (`boundary.ts`), site drawing on the Leaflet map (`useSiteDraw.ts`).
- Backend: `services/site_service.py` (document, validation, rename, prune), `services/site_context.py` (Overpass footprints / roads / fences / landuse, Terrarium terrain, cache, budgets), routes under `/api/projects/{name}/sites`, `sites.json` in `_BUNDLE_FILES`, `sites/` in `_BUNDLE_DIRS`, `_carry_sidecars_on_move` extended to every sidecar (D15).
- Already true of the branch and matching the owner's 2026-10-06 wishes: every component is its own object (two transformers → two transformer bays, each sized from its own `s_nom`); geometry regenerates from the model on every edit; the site is created only when the user draws a boundary or clicks "Create a site around this bus" (`components/SiteEmptyState.tsx`) — templates create none; a parameter change re-derives the scene with no 3D-specific plumbing.
- Not true yet: the packer knows **zones**, not **adjacency**. A transformer is packed in its owner bus's yard zone regardless of where its other bus is; a feeder bay faces north; nothing says "this is implausible". Positions are cosmetic by the 2026-09-28 decision 6 and feed nothing.
- QA on the branch: 19/19 and 21/21 headless steps, 2,490 frontend tests, backend suite green but for one pre-existing packaging test; `SiteCanvas` is a separate 311 kB gzipped chunk; main chunk growth +5.3 kB over both phases. Never seen on a real GPU or in the packaged macOS app; Overpass never exercised live.
- Trial merge onto `master@9939b7b` (2026-10-06): three conflicting files — `routers/projects.py` (`_BUNDLE_FILES`: master added `library_refs.json`, branch added `sites.json`; `_BUNDLE_DIRS`: master `reports`, branch `sites`; both unions), `pypsa-gui/CONTEXT.md` (additive), `package-lock.json` (regenerate). 142 files auto-merge.

## Owner decisions (2026-10-06)

| # | Decision |
|---|---|
| O1 | Placement carries meaning. The arrangement must be physically and electrically plausible, roughly represent the real thing, and reflect every component (two transformers are two objects). |
| O2 | One improvement plan per layer; the aim is `master`. |
| O3 | The 3D site is generated from the assets through a library, on the fly, when the user opens the visualisation after connections and coordinates exist; not at project or template creation. |
| O4 | The purpose is visualisation and user experience; additional value is welcome where it falls out, not the driver. |

Decisions 1–11 of 2026-09-28 stand except decision 6 ("positions cosmetic in v1"), which O1 supersedes in S2–S3 below.

## Increments

### S0 — Land the branch

- Merge `master` into the branch (a merge commit, not a rebase: the branch is shared history with its session). Resolve the three conflicts as unions; regenerate `package-lock.json` with `npm install`. Reconcile D15 with master's current `_carry_sidecars_on_move` (`routers/projects.py:1595`) so there is one carry loop over `_BUNDLE_FILES` and `_BUNDLE_DIRS`, not two.
- Repair what auto-merge broke: the branch touches `CanvasResultsContext.tsx` (an additive chunk variant), `useAssetDrag.ts` (fourth hit-test branch), `CreationForm.tsx`, `Sidebar.tsx`, `App.tsx`, `StatusBar.tsx`; master changed the shell, palette and creation form in #78, #83, #84 and #88. Run `tsc -b`, `vitest run`, the backend suite; fix forward.
- Re-run the branch's two headless QA drivers (Phase 1: 19 steps; Phase 2: 21 steps) against the merged tree, with the Overpass stub; record results in a new QA note.
- **Real hardware.** Build the macOS app (`pypsa-gui/build-macos.sh`) and open a campus site: imagery, context, gizmo, a drop, playback. Note frame rate and shadow quality. One **live Overpass** fetch for a real campus (a Dutch or German one, where OSM is dense). Both are runbook steps the branch itself left open.
- Vocabulary: `pypsa-gui/CONTEXT.md` keeps **Site** (geometry) and gains a line that a **campus study** (gridspine's electrical analysis, `routers/campus_electrical.py`) *runs on* a Site's buses; neither word replaces the other. Record it as an ADR if the `domain-modeling` skill asks for one.
- Open the PR with the branch's own assessment, specs and QA notes linked; carry the four product defects it found (A–D, `notes/2026-09-29-3d-site-view-phase2-qa.md` §7) into `OPEN-ITEMS.md` as separate items — **A–C are a data-loss class and are not this PR's to fix**, the patch beside the note needs a port to today's `routers/io.py`.
- **Estimate:** one to two weeks.

### S1 — The shared type module

The pure half of `assetLibrary.ts` (match rules, labels, colours, icons; no geometry) moves to `frontend/src/utils/assetTypes.ts` so plan 1 A2 and plan 2 M5 draw the same type with the same colour. The library imports it; `templates.test.ts`'s "layout names no type" guard stays. Half a week; may ride in S0's PR if plan 1 A2 has not created the module yet.

### S2 — Placement that is plausible (O1)

The packer stops packing by zone alone and starts packing by **relationship**, and the view says when an arrangement is implausible. Rules are data in the library entry, never code in the packer, so a norm is a data change.

- **Library entries gain a `placement` rule:**
  ```ts
  placement?: {
    anchor?: 'yard' | 'between' | 'far'   // where the object's origin wants to be: in its owner's yard, on the segment between owner and far yard, or at the far side
    adjacentTo?: Array<'yard' | 'transformer' | 'hall' | 'bess'>  // types it should touch or face
    clearanceM?: number                  // minimum distance to any other object's footprint
    keepOutM?: { from: string[]; m: number }[]  // e.g. gensets 30 m from the hall, H2 storage 50 m from everything
    orientation?: 'faceFar' | 'north' | 'any'
  }
  ```
  Defaults by type: **transformer** `anchor: 'between'`, `orientation: 'faceFar'` (it sits on the boundary between its two buses' yards, facing the lower-voltage bus); **feeder / cable bay** `anchor: 'yard'`, `faceFar`; **BESS** `adjacentTo: ['transformer', 'yard']`; **gensets** `keepOut` from halls; **H₂ storage** the largest `keepOut`; **PV ground** anywhere, `clearance` from the hall shadow line; **data hall** the anchor other things arrange around. Numbers are placeholders to be set from one published layout norm per type and recorded in the entry's comment with its source, as the branch did for MWh per container.
- **The packer honours the rules**: anchors first (transformers on the inter-yard segment, which also fixes "a transformer on the back side"), then adjacency by greedy nearest-free-slot, then clearance and keep-out as hard constraints during packing. Placed (user-moved) objects are respected as today. `Arrange` applies the rules to everything unplaced; `Arrange all` to everything, after a confirm.
- **Validation, never a block.** A pure `site3d/placementCheck.ts` returns findings per object: outside the boundary (exists), overlapping (exists as a ring, becomes a finding), keep-out breached, clearance breached, transformer not between its buses, feeder not facing its far bus. Findings show as the existing red / amber outline, in the overlay's readout, and as a section in the Issues panel with deep links (the same shape plan 2 M5 uses). The user may leave an implausible layout; the view says so.
- **Owner check on the first result:** one real campus arranged by the rules, screenshot in the QA note, before the numbers are tuned further.
- **Tests.** Every rule class on a fixture (a transformer between two yards within tolerance; a genset inside the hall keep-out flagged; determinism; placed objects untouched; `layout.ts` still names no type).
- **Estimate:** two weeks.

### S3 — Distances that reach the model (O1, optional per project)

Decision 6 of 2026-09-28 stored positions server-side precisely so this could follow without a migration.

- **Site distances.** For every branch whose two buses are members of the site: the distance between the owner object's connection anchor and the far object's, along a route if plan 2 M1 holds one for that branch, else Manhattan on the site grid × a routing factor (1.2, a library constant). Shown on the branch's flow-path label ("TR1 → Campus 33 kV · 86 m").
- **Writing lengths.** Behind plan 2 M2's project setting "Derive lengths from geometry": a site distance writes `length` on the Line or Link (km) exactly as a map route does, with the same rescale preview, with the same discrepancy badge when the setting is off. The map and the site never disagree because the site distance is the route's length whenever a route exists; when it does not, the site writes a straight route of two points into `map_layout.json` so the map shows what the site measured.
- **Reaching the campus study.** `gridspine/producers/campus.py` already reads `length` (falling back to 1 km at zero) when it drafts cables, so a derived length flows into `length_km` with no gridspine change. One end-to-end test: arrange a site, enable the setting, draft the campus electrical network, assert the cable length.
- **Estimate:** one week after plan 2 M1–M2.

### S4 — Generated on demand, and told what is missing (O3)

- **No site is ever created implicitly.** A guard test over every project template and every project-creation route asserts `sites.json` is absent or empty afterwards.
- **Readiness.** The Site 3D view's empty state becomes a short checklist computed from the model: *at least one bus has coordinates* (plan 2 M4's unplaced flow fixes templates), *at least one branch connects two buses*, *a site boundary exists or can be made around a bus*. Each unmet line deep-links to where it is fixed (the map, the connect tool, "Create a site around this bus"). Only when every line is met does **Generate 3D site** appear; it draws the default rectangle if no boundary exists, runs Arrange with S2's rules, fetches context, and flies the camera in from the map polygon's extent (the fly-in decision 3 promised and nobody built).
- **Regeneration.** Already reactive to model edits; this increment adds a one-line status when the library has no type for a component ("`Carrier 'x'` drawn as a generic block") so a user knows the library, not the model, is the limit.
- **Library extension without code.** `models/README.md` already documents the hero pipeline. This increment adds a `site3d/library/README.md` stating how to add a type (one entry, one test row) and how to swap a hero (a GLB plus a manifest row), and a per-project override hook is **not** built (owner decision 9 of 2026-09-28: no business sign-off loop; a product-line library is a repo change).
- **Estimate:** one week.

### S5 — Visualisation and experience value (O4)

Each item is small, independent, and chosen because it falls out of what exists.

- **Site card in Guided mode** (UX assessment IA-6 / M7 proposed a read-only canvas thumbnail; not built): a static render of the site from the south, captured off the canvas once per fresh solve and cached in `sites/<id>/thumb.png`, shown on the hub-design page and in the Decision study's verdict.
- **Figure for reports.** The same capture, at report resolution, offered to the study report as a figure (the reports feature on master has a figures store under `reports/<id>/figures/`). The one place the 3D view enters a customer document.
- **Hero style review.** After S0's real-GPU look the owner decides whether the Kenney low-poly set reads as professional. If not: a second CC0/CC-BY set is a manifest change (the loader is model-agnostic); a parametric higher-detail transformer (tank, radiators, bushings) is a template change; both are data, neither touches the canvas.
- **2D ↔ 3D parity.** Plan 1 A3's moving flow uses the same speed bucket function as the 3D chevrons (`resultStyle.ts` is main-bundle-safe), so the two layers animate in step. Selection already syncs through `selectedComponent`.
- **Walk the campus.** A first-person camera mode (drei `PointerLockControls`, WASD, eye height 1.7 m) toggled from the overlay, with ground-height clamping from the terrain grid. Purely immersive; one to two days; ships only if S0's frame rate on real hardware allows it.
- **Accessibility carried through**: the readout lists every object's state in text; the Issues section from S2 is keyboard-reachable; reduced motion already honoured.
- **Estimate:** two weeks across the items; each is its own small PR.

### S6 — Parked (Phase 3 of the 2026-09-28 assessment)

Google Photorealistic / Cesium ion tiles (paid, online-only), national LiDAR heights, glTF / 3D Tiles export, CC-BY vendor-faithful transformer and switchgear models. Two to four weeks each; none is required by O1–O4.

## Acceptance

1. S0: the 3D view is on `master`; both headless drivers pass on the merged tree; the packaged macOS app shows a site; one live Overpass fetch returned real footprints; `CONTEXT.md` defines Site and campus study together.
2. S2: on a campus with two transformers, each stands on the segment between its two buses' yards facing the lower voltage; a genset dragged inside the hall keep-out is flagged in the overlay and the Issues panel; Arrange produces no finding on the fixture; the owner has seen one arranged campus and signed off the look.
3. S3: with "Derive lengths from geometry" on, moving the BESS changes its feeder's `length`, offers the rescale, and the campus electrical draft reads it; with it off, the badge shows and nothing is written.
4. S4: no template or creation route produces a site; the empty state lists what is missing with working links; Generate 3D site builds, arranges, fetches context and flies in.
5. S5: a site thumbnail appears on the hub-design page after a fresh solve and can be inserted into a report.
6. Throughout: `tsc -b`, `vitest run`, backend suite green; main chunk growth at most 5 kB gzipped per increment; the three-import bundle guard keeps three.js out of the main chunk; `test_packaging_requirements.py` passes.

## Out of scope, ledgered

- Generative (text- or image-to-3D) models for assets or buildings. Assessed in the parent §4.4; AI may classify components and propose arrangements through the existing chat tools (a later increment, not here), never produce meshes.
- Continuous zoom from the map; replacing Leaflet (decision 3 stands).
- Engineering hand-off, BIM/IFC, vendor-faithful equipment (decision 1 stands).
- Multi-site scenes; one site open at a time.
- Fixing defects A–C (scratch-slot network reset) inside this plan's PRs; they are ledgered on their own.

## Order and first step

S0 → S1 → S2 → S4 → S5 (each item), with S3 after plan 2 M1–M2 lands. S0 is the gate for everything and the most time-sensitive piece of work in the three plans.

First step: `git merge master` on `claude/3d-site-visualization-gatc5z`, resolve the three files as unions, `npm install`, `tsc -b`, and read the test failures before touching anything else.
