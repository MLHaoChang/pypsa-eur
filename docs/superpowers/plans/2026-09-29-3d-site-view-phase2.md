# 3D site view — Phase 2 implementation plan (v1)

> **For agentic workers:** implement task-by-task, test first. Every task states its red tests before its green step; a red test that passes before any code is written is a plan defect and must be reported, not "fixed" by weakening the test. Tests marked **(pin)** are characterisation tests expected to pass immediately; they lock a behaviour the feature depends on. Each work package ends with a review gate and an integration commit; the gate is an agent that did not write the code.

**Goal:** Ship the Phase 2 design (`docs/superpowers/specs/2026-09-29-3d-site-view-phase2-design.md`): a table-driven asset library, merged per-object geometry, five CC0 hero models, sizes as built or optimised, and solved results animated in 3D from the existing timeline.

**Architecture:** Six work packages in dependency order. Pure decisions live in `pypsa-gui/frontend/src/site3d/` modules that do **not** import `three` (the library, the templates, the sizing rules, the result mapping); modules that need `three` (geometry merging, hero instancing, the results layer) are imported **only** by `SiteCanvas.tsx` and listed in `bundleBoundary.test.ts`'s `SITECANVAS_ONLY`. No backend change is needed; every endpoint exists (spec §6.1). No WebGL in any unit test.

**Tech stack:** React 19 + TypeScript 5.8 strict + vitest 4 + jsdom (`npx vitest run` from `pypsa-gui/frontend`); three 0.186, @react-three/fiber 9.8, @react-three/drei 10.7; `@gltf-transform/cli` 4.5 **run once, offline, not a dependency** (Task 3.1); backend suite unchanged (`/root/.venv-pypsa-gui/bin/python -m pytest -q -p no:warnings` from `pypsa-gui/backend`).

**Base:** `3f30cf6`-descendant on `claude/3d-site-visualization-gatc5z` (Phase 1 closed, Phase 2 spec committed).

---

## Process

| Stage | What | Gate |
|---|---|---|
| WP1 | Asset library as data; 17 + 4 types; cylinder parts; legend from the library | review agent → fix → commit |
| WP2 | One merged geometry per object | review agent → fix → commit |
| WP3 | Hero models (files, loader, instancing, fallback) | review agent → fix → commit |
| WP4 | Sizes: as built / optimised | review agent → fix → commit |
| WP5 | Results data: per-asset map, freshness gate, pure result styling | review agent → fix → commit |
| WP6 | Results rendering: fill, spin, glow, loading, flow, legend, reduced motion | review agent → fix → commit |
| QA | Full suites + build + bundle budget + headless run on a solved network; QA note | note in `docs/superpowers/notes/` |

**Per work package, in order:** red (tests fail for the stated reason) → green (smallest implementation) → refactor → `tsc -b`, the touched vitest files, then the **full** frontend suite (and the full backend suite once, at QA, since no backend file changes) → review gate (an agent that did not write the code reads the diff against the spec and this plan; every finding fixed or answered in the commit message) → browser smoke for the WP's visible change (Playwright on SwiftShader, canvas read through `window.__site3d.snapshot()`) → integration commit and push.

**Never:** skip or weaken a test; mount WebGL in vitest; import `three` from a main-bundle module; fetch a decoder or model from a CDN; commit a model file without its licence recorded; change a backend route.

### Spec decision coverage

| Decision | Task(s) |
|---|---|
| E1 library as data | 1.1, 1.2, 1.3 |
| E2 class + carrier + side matching | 1.2 |
| E3 the types | 1.2, 1.4 |
| E4 cylinder + hero shapes | 1.1, 2.1, 3.3 |
| E5 merged geometry | 2.1, 2.2 |
| E6 as built / optimised | 4.1, 4.2 |
| E7 CC0 heroes, no decoder | 3.1, 3.2 |
| E8 instancing, fallback, no LOD | 3.3, 3.4 |
| E9 which results | 5.1, 5.2 |
| E10 how results show | 5.3, 6.1–6.3 |
| E11 timeline, easing, reduced motion | 6.2, 6.4 |
| E12 freshness gate | 5.2 |
| E13 transformer flows | 5.1 |

---

## WP1 — Asset library as data

### Task 1.1 — Part shapes and the library types

**Files:** `site3d/assetLibrary.ts` (new, no three), `site3d/layout.ts` (types only in this task).

**Red (`site3d/assetLibrary.test.ts`):**
- `validateLibrary(DEFAULT_LIBRARY)` passes and the table is frozen (deep: entries and their numbers).
- Every class in `PLACEABLE_CLASSES` has exactly one fallback entry (no `carrier`), and it is the **last** entry of its class in table order.
- Ids are unique; every `geometry.template` is one of the known templates; every `hero` ref (none yet in WP1) is known.
- Rejects, naming the field: a zero unit rating, a NaN size, a duplicate id, a class with no fallback, an unknown template.
- `Part` accepts `shape: 'box' | 'cylinder'` (default box); a cylinder part's `size` is `[diameter, length, diameter]` with its axis on the part's local up (documented in the type and asserted by a template test in 1.3).

**Green:** the `AssetType`, `SizeRule`, `Template`, `Match` types of spec §4.1; `validateLibrary`; `DEFAULT_LIBRARY` with the entries of spec §4.2 (numbers moved from `assetRules.ts` into the entries that use them). `assetRules.ts` is deleted in Task 1.3 once nothing imports it.

### Task 1.2 — Matching

**Red (`site3d/assetLibrary.test.ts`, `describe('matchType')`):**
- A table-driven test **over the palette**: for each palette item id in `layout/Sidebar.tsx`'s `PALETTE_SECTIONS`, build the component its creation form would create (class and default carrier from `layout/CreationForm.tsx`'s `FIELD_MAP`/`COMPONENT_TYPE`; list them in the test as literal fixtures with a comment naming the source line, so a palette change breaks this test visibly) and assert the type id of spec §4.2: bus → switchyard (AC) / manifold (H2, heat); line → feeder; transformer → transformer; thermal (gas) → engine genset; renewable (wind) → wind; electrolyzer (Link H2, bus0 AC, bus1 H2) → electrolyser; fuel_cell (Link H2, bus0 H2, bus1 AC) → fuel cell; power_to_heat (each of the three carriers) → heat pump / e-boiler; chp (Link gas with bus2 heat) → CHP; battery → BESS; psh → pumped hydro; caes → compressed air; flywheel → flywheel; hydrogen (StorageUnit H2) → H₂ storage; thermal_storage (Store heat) → thermal store; load_elec → data hall; load_h2 / load_heat → offtake.
- Carrier cases beyond the palette: solar → PV ground; solar-rooftop → PV rooftop; CCGT, OCGT → gas turbine; onwind, offwind-ac → wind; diesel, biomass, coal → engine genset **with the carrier in its label** ("coal — generic plant block"); Link `datacenter` → data hall; Link `DC` → feeder; Store `battery` → BESS; Store `H2` → H₂ storage.
- Side rule: an `H2` Link seen from its **bus1** member is not an electrolyser object (it is drawn once, from bus0 — the Phase 1 invariant); a fuel cell is drawn from its electrical side (bus1).
- `matchType` is pure and takes `busCarrier: (name) => string | undefined` so the side rule can read the far bus's carrier.
- **(pin)** the component name never affects the match (same component, two names, same type).

**Green:** `matchType(cls, component, memberBus, busCarrier, library)`; first match in table order.

### Task 1.3 — Templates and the packer

**Files:** `site3d/templates.ts` (new, no three), `site3d/layout.ts` (becomes packer + interpreter), `site3d/assetRules.ts` (deleted).

**Red:**
- Phase 1's `site3d/layout.test.ts` stays green **unchanged in meaning**: exact BESS part count (10 × 4 MWh → 10 containers + 2 PCS skids = 12), the "each box = N" cap, PV land ≈ 2.5 ha/MWp, wind 3 × 5 MW = 15 parts with 9 `rotN`, transformer and feeder summaries, `{type, name}` on every object, no overlap within a bus, fits the half-size, determinism, multi-bus yards, a two-terminal component drawn once, placements and orphans, yard placement moves its unplaced assets, `objectKey`. Its "rules drive geometry" test is rewritten against a library entry (halving `mwhPerContainer` in a copied BESS entry doubles the containers and leaves PV unchanged), and the invalid-rules test against `validateLibrary`.
- New template tests (`site3d/templates.test.ts`): `tankArray` emits cylinder parts whose count is `ceil(MWh / unit)`; `turbineArray` marks the rotor parts (`anchor: 'rotor'`) and the tower is a cylinder; `unitGrid` emits the per-row extra part; `hall` returns an area ≥ its minimum; `reservoir` land = its basin; `pvRoof` land = 0 and it sits on the largest hall's roof when one exists in the same site (input: the hall footprints), else on a 4 m canopy.
- Every template declares its animation anchors (spec §4.3): fill (tanks, containers), rotor (wind), emissive (halls, skids, gensets), flow (bays, transformers) — asserted per template.
- `buildSiteLayout(input)` takes `library` as an input field and holds **no module-level mutable state** (a test runs two layouts with different libraries interleaved and gets both right).
- **Guard:** `layout.ts` source contains no carrier regex (`/\/[^/]*(solar|wind|electroly|h2|batter)/i` does not match its source) and no hex colour.

**Green:** move each `build*` into a template; `buildBus` asks `matchType`, calls the template, packs by the entry's zone.

### Task 1.4 — Legend, labels and colours from the library; cylinders drawn

**Files:** `pages/SiteCanvas.tsx`, `site3d/layout.ts`.

**Red:** `KIND_COLOR` / `KIND_LABEL` are gone; the legend's entries come from the types present (`legendFor(objects, library)` in `assetLibrary.ts`, tested: order = library order, one entry per type present, label and colour from the entry). `SiteObject.kind` becomes the type id (string); `fit.ts` and the placement code do not read it (pin: `fit.test.ts` unchanged).

**Green:** SiteCanvas renders `shape: 'cylinder'` parts with `cylinderGeometry` (radial segments 16) until WP2 replaces per-part meshes.

**WP1 browser smoke:** the campus fixture plus one of each palette item added on the 33 kV bus (a script that POSTs them through the network API) → every object's `window.__site3d.objects[].kind` equals the spec §4.2 type; `snapshot()` shows tanks as cylinders. **Gate focus:** no palette item falls to a wrong type; the Phase 1 invariants still hold; no three import in the library modules.

---

## WP2 — One merged geometry per object

### Task 2.1 — Merge

**Files:** `site3d/objectGeometry.ts` (new, **three**, SiteCanvas-only).

**Red (`site3d/objectGeometry.test.ts`):** `objectGeometry(parts)` returns one `BufferGeometry` whose vertex count = Σ(box 24 | cylinder 16-segment count) over parts, with a `color` attribute holding each part's colour (part override else object colour), positions transformed by each part's position/rotations (a rotated part's bounding box matches the rotated box — one test per rotation field), and **groups** recording each anchor's vertex range (`anchors: {fill?: [start, count], rotor?: …, emissive?: …}`) so WP6 can drive them. A 120-part object merges in < 30 ms in jsdom (budget, not a benchmark).

**Green:** `BoxGeometry`/`CylinderGeometry` per part, `applyMatrix4`, `mergeGeometries` from `three/examples/jsm/utils/BufferGeometryUtils.js`, vertex colours.

### Task 2.2 — Render one mesh per object

**Files:** `pages/SiteCanvas.tsx`.

**Red:** none new in vitest (WebGL); `bundleBoundary.test.ts` lists `objectGeometry.ts` in `SITECANVAS_ONLY` and passes.

**Green:** `SiteObjectMesh` renders `<mesh geometry={objectGeometry(obj.parts)}>` with `meshStandardMaterial vertexColors`; selection/hover/outside tint through the material's `emissive` as today; the geometry is memoised on the object's part list and disposed on change. The rotor parts stay a **separate** child mesh (the merged body excludes them) so WP6 can rotate them.

**WP2 browser smoke:** the debug hook exposes `gl.info.render.calls`; with the campus fixture, calls drop from the Phase 1 count (record both) to ≤ objects + context + 10; click-select, gizmo drag and the outside tint still work (reuse the Phase 1 e2e steps 4 and 7). **Gate focus:** selection and pivot semantics unchanged; geometry disposal; the rotor split.

---

## WP3 — Hero models

### Task 3.1 — Files and provenance

**Files:** `frontend/public/site3d/models/{windmill,shipping-container-a,solar-panel-landscape-group,detail-tank,building-s}.glb` + the shared texture, `frontend/public/site3d/models/README.md`, `frontend/public/site3d/models/LICENSE-Kenney.txt` (verbatim).

**Steps:** download Kenney *City Kit Industrial 2.0*; run `npx -y @gltf-transform/cli@4.5.1 optimize <in> <out> --compress false --texture-compress webp --join-named false --flatten false` per file (the exact command recorded in the README); keep PNG for the texture if WebP decoding cannot be confirmed in Chromium (it can) **and** note the pywebview/WKWebView check as a QA item.

**Red (`site3d/heroes.test.ts`, node fs):** each file in the manifest exists, is ≤ 64 kB, starts with the `glTF` magic; the README names the source URL, licence and command; the licence file exists; total ≤ 200 kB raw. `windmill.glb` contains a node named `blades` (parsed with `GLTFLoader.parse` in node — three's loader parses a GLB `ArrayBuffer` without a DOM; if it needs `ImageLoader`, the test stubs texture loading) whose parent chain has no scale ≠ 1 (the meshopt pitfall).

### Task 3.2 — Manifest and offline loading

**Files:** `site3d/heroes.ts` (new, no three: the manifest `{id, url, units: 'm', nativeSize: [w,h,d], anchors}` and `heroFor(typeId)`), `site3d/heroLoader.tsx` (new, three/drei, SiteCanvas-only).

**Red:**
- `heroes.test.ts`: every library entry's `hero` ref is in the manifest; URLs are relative to the app base (`import.meta.env.BASE_URL`), never absolute or `http`.
- **Guard (`site3d/noCdn.test.ts`):** no source file under `src/` contains `gstatic.com`, `draco/versioned`, `unpkg.com`, `jsdelivr` (outside comments naming the pitfall), and every `useGLTF(` call passes `false` as its second argument (a regex over the source).

**Green:** `useHero(id)` = `useGLTF(url, false, false)`; preload on site open.

### Task 3.3 — Instancing from template units

**Files:** `site3d/heroes.ts` (pure: `heroInstances(obj, hero) → Matrix-like {pos, rot, scale}[]` — no three), `site3d/heroLoader.tsx`.

**Red (`heroes.test.ts`):** for a 10-container BESS the instance list has 10 entries at the container part centres, scaled so the hero's native size maps onto `container20ft` (per axis), heading applied; a 3-turbine wind object yields 3 towers at the turbine positions scaled to hub height; PV yields one instance per table (≤ the part cap); an H₂ tank array one per tank; a hall one instance scaled to the hall footprint. Units a template marks `heroable: false` (PCS skids, stacks, BoP) are not instanced and stay parametric.

**Green:** `HeroInstances` renders `<instancedMesh>` per mesh of the hero scene (a GLB may hold several meshes/materials), matrices from `heroInstances`; the object's parametric parts that are heroable are **omitted** from the merged geometry while the hero is shown.

### Task 3.4 — Fallback and failure

**Red:** the parametric form is what renders while a hero is loading (Suspense fallback) and after a load error (an `ErrorBoundary` whose unit test renders a throwing child and asserts the fallback renders — jsdom, no WebGL, the boundary is plain React); `heroFor` returns `null` for a type without a hero.

**WP3 browser smoke:** snapshot shows the Kenney turbine, containers, tanks, tables and hall on the campus; with the `models/` path blocked (`page.route` abort), the parametric scene renders with no error; the network log shows **zero requests to any host other than the app's** during model load. **Gate focus:** licence and provenance recorded; no CDN; blade node usable; fallback.

---

## WP4 — Sizes: as built / optimised

### Task 4.1 — Sizing rule

**Red (`assetLibrary.test.ts` / `layout.test.ts`):** `sizeOf(component, rule, mode)` returns `p_nom` in `'installed'` mode; in `'optimised'` mode returns `p_nom_opt` **only** when the component is extendable (`p_nom_extendable`, `e_nom_extendable`, `s_nom_extendable`) and `*_nom_opt` is a finite number > 0, else the installed value; StorageUnit MWh = `p_nom(_opt) × max_hours`; the summary says "(optimised)" when the optimised value was used. `buildSiteLayout` takes `sizing` and a BESS with `p_nom 10 → p_nom_opt 40` draws 4× the containers in optimised mode.

### Task 4.2 — The switch

**Files:** `components/SiteOverlay.tsx`, `pages/SiteCanvas.tsx`, `store/uiStore.ts` (`siteSizing: 'installed' | 'optimised'`, per session, default installed).

**Red (`SiteOverlay.test.tsx`):** the "Sized: as built / optimised" switch renders **only** when `dispatchFresh` is true; toggling calls `setSiteSizing`; in optimised mode the fit line says "(optimised)". Not a mutation: allowed when read-only (pin).

**WP4 browser smoke:** solve the campus with an extendable BESS; flip the switch; the BESS object's part count changes; fit status updates. **Gate focus:** default unchanged; optimised only when fresh; no write.

---

## WP5 — Results data

### Task 5.1 — Per-asset series

**Files:** `components/CanvasResultsContext.tsx` (export `useChunkedSeries`, no behaviour change), `site3d/useSiteResults.ts` (new, no three), `api/simulation.ts` (a `getTransformerResults` wrapper if absent).

**Red (`site3d/useSiteResults.test.tsx`, react-query + mocked `resultsApi`):**
- For site objects `[Generator:PV, StorageUnit:BESS 1, Store:H2 tank, Load:Hall A, Link:Electrolyser, Line:L1, Transformer:TR1]` and a mocked chunk, the hook returns a `Map<key, AssetState>` for **the current `resultsSnapshotIdx` only**, with MW, share of capacity (denominator `*_nom_opt` else `*_nom`), SoC share for storage, fill share for stores, loading % and direction for branches.
- Transformers come from the transformer endpoint (the mocked `getTransformerResults` is called; the Lines map is never consulted for a `Transformer:` key — the schematic's bug, pinned as absent here).
- Only series the site needs are fetched (a site with no Store makes no store calls).
- Query keys equal the context's (`nk(project,'results',name,source,from)`), so a warm cache from another canvas is reused (assert one fetch across a remount).
- An object absent from the payload's columns maps to no state (not zero).

### Task 5.2 — Freshness gate

**Red:** the hook returns an **empty** map (and makes no chunk request) when `resultsOverlayEnabled` is false; when `simulationStatus.dispatch !== 'fresh'`; and it drops a populated map on the transition fresh → stale **before** any chunk refetch (test: status query flips, next render is empty with the cached chunk still in the cache).

### Task 5.3 — Result styling (pure)

**Files:** `site3d/resultStyle.ts` (new, no three).

**Red (`resultStyle.test.ts`):** `visualFor(type, state)`: storage → `fill` = SoC, colour band (<20 % red, <80 % amber, else green — the schematic's bands); charging vs discharging in the label; generators → `emissive` = output share, wind → `spin` rpm = rated rpm × output share (0 when output ≤ 0), PV → emissive only; loads → `emissive` = MW / site peak of that load; branches → `color = loadingColor(pct)` (imported from the context — same bands), `flow.dir` from the sign of p0 relative to the object's bus0→bus1, `flow.pct`; the hover label strings ("BESS 1 · discharging 18.2 MW · SoC 64 %", "L1 · 72 % of rating → Campus 33kV"). Nothing colour-only: every coloured state has a number in its label (asserted per branch).

**WP5 gate focus:** cache sharing, freshness, the transformer path; no three import.

---

## WP6 — Results rendering

### Task 6.1 — Anchors driven by refs

**Files:** `site3d/resultsLayer.tsx` (new, three, SiteCanvas-only), `pages/SiteCanvas.tsx`.

**Red (`resultsLayer.test.ts`, pure parts only):** `easeTowards(current, target, dtMs, tauMs)` converges and never overshoots; with `reduced = true` it returns the target. `fillPlane(objectBox, share)` returns the level plane's height inside the fill anchor's bounds (0 → bottom, 1 → top).

**Green:** `ResultsLayer` holds refs to each object's anchors (fill planes, rotor meshes, emissive materials, flow chevrons) and updates them in `useFrame`; the React tree does not re-render per snapshot (the map is read from a ref updated by the hook).

### Task 6.2 — Wind, fill, glow, loading, flow

**Green:** rotors rotate about the hub axis at `spin`; fill planes (translucent, the band colour) rise inside tanks and a bar along container rows; emissive intensity on halls/skids/gensets; feeders and transformers tinted with the loading colour and carrying chevrons along the flow anchor, pointing downstream, spaced by loading.

### Task 6.3 — Labels and legend

**Red (`SiteOverlay.test.tsx` / a `ResultsLegend` test):** the legend shows the loading bands and SoC bands **only** when results are shown; the hover label (Phase 1's DOM label) appends the result line from `visualFor`.

### Task 6.4 — Reduced motion and the timeline

**Red:** with `matchMedia('(prefers-reduced-motion: reduce)')` true, `useReducedMotion()` returns true and the layer is created with `reduced`; **(pin)** the 3D view reads `resultsSnapshotIdx` and never writes it (no second timeline).

**WP6 browser smoke:** solve `campus-year`; Eye on; step the picker by 12 h and by one day; `window.__site3d.visual(key)` (new debug field) reports the fill, spin and loading at each step and they change; `snapshot()` at two steps differs; reduced motion (`page.emulateMedia({reducedMotion: 'reduce'})`) → spin 0, instant fill. **Gate focus:** no per-tick React re-render (React profiler count or a render counter in the debug hook stays flat while playing); easing; colour + number everywhere.

---

## QA — end to end

1. Frontend: `tsc -b`, full `vitest`, `npm run build`; `SiteCanvas` chunk separate; main `spa` chunk growth ≤ 5 kB gzipped against the Phase 1 close (825.74 kB); model files present in `dist/site3d/models/`.
2. Backend: full `pytest` once (no backend change expected; the pre-existing packaging-spec failure is the only allowed failure).
3. Headless run on an **idle** machine (Phase 1 lesson), auth mode: open the campus site → every palette type present renders as its type → heroes visible, then blocked → parametric fallback, no page error → as built / optimised flip on a solved network → Eye on, play the timeline for 48 snapshots → fills, spin, loading change; hover label shows values → edit a component (results go stale) → the 3D results disappear immediately → reduced motion run.
4. Packaging: `dist/site3d/models/*.glb` served by the backend's static route (`static_gate.is_static_asset` covers any path with an extension; a backend smoke GET returns 200) — recorded; the macOS build remains a runbook step (WKWebView WebP check).
5. `docs/superpowers/notes/<date>-3d-site-view-phase2-qa.md`.

---

## Risks

| Risk | Where it bites | Mitigation in this plan |
|---|---|---|
| The layout refactor silently changes Phase 1 geometry | WP1 | Phase 1 layout tests kept unchanged in meaning; exact counts pinned |
| Palette defaults drift from the matching table | WP1 | the palette-driven test lists fixtures with source-line comments |
| `mergeGeometries` attribute mismatch (cylinder has different attributes than box) | WP2 | both built as non-indexed with the same attribute set; test on a mixed object |
| Kenney model axes/units differ per file | WP3 | `nativeSize` measured per file in the manifest; instance test per hero |
| GLTFLoader in node needs DOM for textures | WP3 | test stubs texture loading or parses JSON chunk only for the node check |
| Result series cost at 8760 snapshots × many assets | WP5 | the context's chunking reused; only needed series fetched |
| Per-tick React re-render stutters | WP6 | refs + `useFrame`; render counter in the debug hook |
