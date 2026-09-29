# 3D site view — Phase 2 implementation plan (v2)

> **For agentic workers:** implement task-by-task, test first. Every task states its red tests before its green step; a red test that passes before any code is written is a plan defect and must be reported, not "fixed" by weakening the test. Tests marked **(pin)** are characterisation tests expected to pass immediately; they lock a behaviour the feature depends on. Each work package ends with a review gate and an integration commit; the gate is an agent that did not write the code.

**Goal:** Ship the Phase 2 design (`docs/superpowers/specs/2026-09-29-3d-site-view-phase2-design.md`): a table-driven asset library, merged per-object geometry, five CC0 hero models, sizes as built or optimised, and solved results animated in 3D from the existing timeline.

**Architecture:** Seven work packages in dependency order (WP0 is groundwork). Pure decisions live in `pypsa-gui/frontend/src/site3d/` modules that do **not** import `three` (library, templates, sizing, hero fitting, result mapping, easing); modules that need `three` (geometry merging, hero instancing, the results layer) are imported only by `SiteCanvas.tsx` **or by another SiteCanvas-only module**, and are listed in `bundleBoundary.test.ts`'s `SITECANVAS_ONLY`. No backend change; every endpoint exists. No WebGL in any unit test. Both `layout.ts` and `SiteOverlay.tsx` are imported only by the lazy `SiteCanvas` chunk (verified: `App.tsx:18`), so the library does not enter the main chunk.

**Tech stack:** React 19 + TypeScript 5.8 strict + vitest 4 + jsdom (`npx vitest run` from `pypsa-gui/frontend`; `vitest.setup.ts` patches `Element.prototype`, so **no** `@vitest-environment node` files); three 0.186, @react-three/fiber 9.8, @react-three/drei 10.7.9 (`useGLTF(path, useDraco, useMeshopt, extendLoader)`); `@gltf-transform/cli` 4.5.1 **run once, offline, not a dependency**.

**Base:** the Phase 2 spec commit on `claude/3d-site-visualization-gatc5z`.

**v2 (2026-09-29):** revised after two independent reviews (library/rendering/heroes; results/testability). Every finding and where it landed is in the last section.

---

## Process

| Stage | What | Gate |
|---|---|---|
| WP0 | Groundwork: types, bundle guard, palette data, debug hook | review agent → fix → commit |
| WP1 | Asset library as data; matching; templates; owner-after-match; rooftop pass | review agent → fix → commit |
| WP2 | One merged geometry per object; rotor per turbine; render isolation | review agent → fix → commit |
| WP3 | Hero models (files, manifest, fitting, instancing, fallback) | review agent → fix → commit |
| WP4 | Sizes: as built / optimised, gated on fresh dispatch | review agent → fix → commit |
| WP5 | Results data: own status poll, freshness rules, per-asset map, denominators, styling | review agent → fix → commit |
| WP6 | Results rendering: gauges, spin, glow precedence, flow, readout, legend, reduced motion | review agent → fix → commit |
| QA | Full suites + build + bundle budget + headless run on a solved network; QA note | note in `docs/superpowers/notes/` |

**Per work package, in order:** red (tests fail for the stated reason) → green (smallest implementation) → refactor → `tsc -b`, the touched vitest files, then the **full** frontend suite → review gate (an agent that did not write the code reads the diff against the spec and this plan; every finding fixed or answered in the commit message) → browser smoke for the WP's visible change (Playwright on SwiftShader, canvas read through `window.__site3d.snapshot()`, **on an otherwise idle machine**) → integration commit and push. The full backend suite runs once, at QA (no backend change).

**Never:** skip or weaken a test (a Phase 1 assertion that changes because the spec changed it is listed in Task 1.3 with its new value); mount WebGL in vitest; import `three` from a main-bundle module; fetch a decoder or model from a CDN; commit a model file without its licence recorded; change a backend route.

### Spec decision coverage

| Decision | Task(s) |
|---|---|
| E1 library as data | 1.1, 1.3, 1.5 |
| E2 rules, far carrier, owner after match | 0.1, 1.2, 1.4 |
| E3 the types | 1.1, 1.2 |
| E4 cylinder, axis, heroable | 1.1, 1.3, 2.1 |
| E5 merged geometry, rotor per turbine | 2.1, 2.2 |
| E6 as built / optimised, gated on fresh | 4.1, 4.2 |
| E7 CC0 heroes, no decoder | 3.1, 3.2 |
| E8 instancing, fit modes, blades, materials, fallback | 3.3, 3.4, 3.5 |
| E9 which results | 5.3 |
| E10 how results show | 5.5, 6.2 |
| E11 timeline, easing, reduced motion | 6.1, 6.5 |
| E12 freshness | 5.1, 5.2 |
| E13 transformer flows | 5.3 |

---

## WP0 — Groundwork

### Task 0.1 — API types

**Files:** `api/types.ts`.

**Red:** a type-level test (`api/types.site3d.test.ts`, compiled by `tsc -b`, one runtime assertion so vitest collects it) builds fixtures `{…generator, p_nom_opt: 40}`, `{…storageUnit, p_nom_opt, p_nom_extendable}`, `{…store, e_nom_opt, e_nom_extendable}`, `{…line, s_nom_opt, s_nom_extendable}`, `{…transformer, s_nom_opt, s_nom_extendable}`, `{…link, p_nom_opt, bus2: 'Heat'}` — fails `tsc -b` today on excess properties.

**Green:** optional `p_nom_opt?`, `e_nom_opt?`, `s_nom_opt?`, `*_extendable?` where missing, `bus2?: string` on `Link`. The API already sends them (`network_crud._serialize_component` serialises the whole frame).

### Task 0.2 — Bundle guard for `.tsx` and module chains

**Files:** `site3d/bundleBoundary.test.ts`.

This is a deliberate change to a Phase 1 test, required by the spec (SiteCanvas-only modules may be `.tsx` and may import each other); it is scheduled here so it is not a silent weakening.

**Red (new cases in the same file):** a fixture directory under `site3d/__fixtures__/boundary/` with `a.tsx` (SiteCanvas-only, imports three) imported by `b.ts` (SiteCanvas-only) imported by a fake `SiteCanvas.tsx` → the check accepts it; a main-bundle `c.ts` importing `a.tsx` → rejected. Fails today (the stem strip is `/\.ts$/`, so `.tsx` stems never match and chains are rejected).

**Green:** strip `/\.tsx?$/`; allowed importers = `pages/SiteCanvas.tsx` ∪ `SITECANVAS_ONLY`; the check is a function the real test and the fixture test both call.

### Task 0.3 — Palette data as a module

**Files:** `layout/paletteData.ts` (new: palette ids, labels, sections; each item's PyPSA class and default carrier/ports), `layout/Sidebar.tsx` and `layout/CreationForm.tsx` import from it (no behaviour change).

**Red (`layout/paletteData.test.ts`):** the exported ids equal the ids `Sidebar` renders (render the palette, read the `title`s); for each item, the class and default carrier equal what the creation form submits (render `CreationForm` for the item with a mocked create call; assert the posted class and carrier). **(pin)** the Sidebar and CreationForm suites stay green.

### Task 0.4 — Debug hook fields

**Files:** `pages/SiteCanvas.tsx` (`Site3dDebugHook`).

**Green (browser-verified, no unit test — WebGL):** `objects[]` gains `kind`; `project(key)` is computed from the object's **data** (centre of its first part through the object's origin and heading, lifted by the terrain), not from the first mesh's bounding box (which a merged body or a hero would move into the sky); `calls` = `gl.info.render.calls`; `renders` = a counter per component name (SiteCanvas, each object mesh, the results driver) for WP2/WP6's isolation checks. The Phase 1 e2e script is re-run against it (steps 3–7) to prove `project()` still clicks the right object.

**WP0 gate focus:** no behaviour change; the guard change is exactly the spec's rule.

---

## WP1 — Asset library as data

### Task 1.1 — Types, library, validation

**Files:** `site3d/assetLibrary.ts` (new, no three), `site3d/layout.ts` (types).

**Red (`site3d/assetLibrary.test.ts`):**
- `validateLibrary(DEFAULT_LIBRARY)` passes; the table is deep-frozen (entries, numbers, rule arrays).
- Every class in `PLACEABLE_CLASSES` has **exactly one** fallback rule (no `carrier`), owned by the last type matching that class — including **Bus** (switchyard) and **Load** (data hall).
- Ids unique; templates known; the ids of unchanged types equal Phase 1's (`switchyard`, `transformer`, `feeder`, `bess`, `pv`, `wind`, `electrolyser`, `h2store`, `load`).
- Rejects, naming the field: a zero unit rating, NaN, a duplicate id, a class with no fallback, two fallbacks for a class, an unknown template, a regex with the `g` or `y` flag (a frozen global regex throws on `.test`).
- `Part` accepts `shape: 'box' | 'cylinder'`, `axis: 'up' | 'east' | 'north'` (cylinders), `heroable?: boolean`, `anchor?: 'rotor' | 'fill' | 'emissive' | 'flow'`, `turbine?: number` (rotor parts: which turbine). `size` keeps (east, north, height).

**Green:** the types of spec §4.1; `validateLibrary`; `DEFAULT_LIBRARY` with the entries of spec §4.2, Phase 1's 13 numbers moved into the entries that use them, carrier regexes anchored (`/^h2$/i` — "H2 pipeline", "H2 fuel cell", "H2 electrolysis" exist and must not collide).

### Task 1.2 — Matching

**Files:** `site3d/assetLibrary.ts`, `site3d/layout.ts` (`SiteInput.busCarrier`), `pages/SiteCanvas.tsx` (both `buildSiteLayout` call sites pass `busCarrier` built from **all** network buses).

**Red (`describe('matchType')`):**
- **Palette-driven** (from `paletteData.ts`, so a palette change is seen): each item's default component maps to its spec type: bus → switchyard (AC) / manifold (H2, heat, gas); line → feeder; transformer → transformer; thermal (gas) → engine genset; renewable (wind) → wind; electrolyzer → electrolyser; fuel_cell → fuel cell; power_to_heat (each carrier) → heat pump; chp → CHP; battery → bess; psh → pumped hydro; caes → compressed air; flywheel → flywheel; hydrogen (StorageUnit H2) → h2store; thermal_storage → thermal store; load_elec → load (data hall); load_h2 / load_heat → offtake.
- Carriers beyond the palette: solar → pv; solar-rooftop → PV rooftop; CCGT, OCGT → gas turbine; onwind, offwind-ac → wind; diesel, biomass, coal → engine genset whose label names the carrier; Link `datacenter` → load; Link `DC` → feeder; Store `battery` → bess; Store `H2` → h2store; Link `H2 pipeline` → feeder (anchored regex).
- `farCarrier`: an `H2` Link AC→H2 is an electrolyser; H2→AC is a fuel cell.
- Unknown bus carrier counts as AC.
- **(pin)** the component name never affects the match.

### Task 1.3 — Templates and the packer; the Phase 1 assertions that change

**Files:** `site3d/templates.ts` (new, no three), `site3d/layout.ts` (packer + interpreter), `site3d/assetRules.ts` + `assetRules.test.ts` (deleted; their cases move to `assetLibrary.test.ts`).

**Phase 1 `layout.test.ts` assertions that change, and their new values** (every other assertion stays as written):

| Line | Today | Becomes | Why |
|---|---|---|---|
| 56–64 | `ccgt: 'thermal'` | `ccgt: 'gasTurbine'` | spec §4.2 gives CCGT its own type |
| 56–64 | `kind` values | unchanged strings for pv, wind, h2store, electrolyser, feeder | ids kept (Task 1.1) |
| 219–228 | rules object | a copied `bess` entry with half the MWh per container | E1 |
| 230–232 | `validateRules` throws | `validateLibrary` throws | E1 |

**Red (`site3d/templates.test.ts`):**
- `tankArray` emits cylinder parts, count `ceil(MWh / unit)`, vertical tanks `[d, d, h]` standing on the ground (centre height = h/2), horizontal bullets `[d, L, d]` with `axis: 'north'`; the packer's row pitch uses `size[0]`/`size[1]` (test on the resulting footprint).
- `turbineArray`: tower is a cylinder, each turbine's rotor parts carry `anchor: 'rotor'` and a distinct `turbine` index with the hub position recorded; **capped** at 120 turbines drawn (wind was uncapped in Phase 1), summary says "each = N".
- `unitGrid` emits the per-row extra part; `hall` area ≥ its minimum; `reservoir` land = its basin; every template declares its anchors (fill, emissive, flow) per spec §4.3, asserted per template; heroable units flagged.
- **Source guards** (comments stripped first): `layout.ts` has no regex literal containing a carrier word, no hex colour, and no top-level `let` (Phase 1's module-global rules `R` is gone).

**Green:** each Phase 1 `build*` becomes a template; `buildBus` asks `matchType`, calls the template, packs by the entry's zone; the switchyard's bay count and the overlay's asset count read `flags.bay` / `flags.infrastructure` instead of naming kinds.

### Task 1.4 — Owner after match, and three-port Links

**Red (`layout.test.ts`, new cases):**
- Electrolyser with both its AC bus and its H2 bus as site members → one object, drawn from the AC bus; fuel cell likewise from its AC bus (bus1); the Phase 1 invariant "a two-terminal component is drawn once" (lines 165–172) stays green.
- An electrolyser whose H2 bus is **not** a member → still drawn, from the AC bus.
- CHP (gas bus0, AC bus1, heat bus2) with only the heat bus as a member → drawn once, from the heat bus; with all three members → from bus0 (its rule's port).
- `bus2: ''` counts as absent.
- `SiteObject` gains `bus` (owner) and `far` (the other electrical end for branches), asserted for a line owned through bus1.

### Task 1.5 — Rooftop pass, legend, colours

**Red:**
- Rooftop PV: with a data hall on another member bus, the PV object's origin equals the hall's **final** origin (after its placement is applied), moves with the hall's placement, and is exempt from the no-overlap check (the test states the exemption); without a hall it stands in the south zone on a canopy; land take 0 (`fit.ts` land sum unchanged otherwise — `fit.test.ts` pinned).
- `legendFor(objects, library)`: one entry per type present, library order, label and colour from the entry; the Generator fallback's label names the carrier.

**Green:** `KIND_COLOR`/`KIND_LABEL` removed; SiteCanvas renders cylinders (16 segments) — per part until WP2.

**WP1 browser smoke:** a fixture script creates, via the network API, one component per palette item on the campus, **adding an H2 bus and a heat bus with coordinates and making them site members** (the creation form filters buses by carrier; unplaced buses are dropped). Every object's `window.__site3d.objects[].kind` equals the spec type; `snapshot()` shows cylinders. **Gate focus:** no palette item mis-typed; owner-after-match; Phase 1 invariants; no three in the library modules.

---

## WP2 — One merged geometry per object; render isolation

### Task 2.1 — Merge

**Files:** `site3d/objectGeometry.ts` (new, **three**, SiteCanvas-only).

**Red (`site3d/objectGeometry.test.ts`):**
- `objectGeometry(parts)` merges non-rotor parts into one **indexed** geometry: vertex count = 24 per box + 100 per 16-segment cylinder (measured, three 0.186), `color` attribute per part (override else object colour), positions transformed by each part's position, rotations and cylinder axis (bounding box of a rotated part = the rotated box, one test per rotation field and per axis).
- Rotor parts are returned separately, **one geometry per turbine**, in hub-local coordinates with the hub position as the group origin (3 turbines → 3 distinct pivots).
- An empty part list (all parts heroed, WP3) returns `null`, not a `mergeGeometries([])` error.
- 120 parts merge well within a frame (measured ~1.3 ms; the test asserts < 50 ms).

### Task 2.2 — One mesh per object, rotors per turbine, render isolation

**Files:** `pages/SiteCanvas.tsx`.

**Green:**
- `SiteObjectMesh` renders the merged body (`meshStandardMaterial vertexColors`) plus one `<group position={hub}>` + rotor mesh per turbine; geometry memoised on the part list, disposed on change.
- Selection: with vertex colours, `color '#fff'` no longer whitens the body; selection/hover/outside use **emissive** only, through `emissiveFor(selected, outside, result?)` (pure, in `site3d/resultStyle.ts`; precedence selected > outside > result).
- Render isolation (**Task 6.0 of the v1 review, moved here**): SiteCanvas subscribes with selectors (no bare `useUIStore()`), `SiteObjectMesh` is `React.memo` with stable callbacks.

**Red:** `resultStyle.test.ts` for `emissiveFor` precedence; `bundleBoundary.test.ts` lists `objectGeometry.ts`.

**WP2 browser smoke:** at a fixed camera with nothing selected, record `calls` before (Phase 1) and after: expect roughly **2 × (object meshes + rotor meshes) + context + ground** (the shadow pass counts in `info.render.calls`), and a ratio ≤ ⅓ of Phase 1's; click-select, gizmo drag and the outside tint still work (Phase 1 e2e steps 4 and 7); stepping `resultsSnapshotIdx` 10 times leaves the `renders` counters of SiteCanvas and every object mesh unchanged. **Gate focus:** selection and pivot semantics; disposal; render isolation.

---

## WP3 — Hero models

### Task 3.1 — Files, provenance, manifest

**Files:** `frontend/public/site3d/models/{windmill,shipping-container-a,solar-panel-landscape-group,detail-tank,building-s}.glb`, `models/README.md` (source URL, version, licence, exact command), `models/LICENSE-Kenney.txt` (verbatim), `site3d/heroes.ts` (manifest, no three), `site3d/glbJson.ts` (pure: read a GLB's JSON chunk — header 12 bytes, chunk length + type, then JSON).

**Steps:** `npx -y @gltf-transform/cli@4.5.1 optimize <in> <out> --compress false --texture-compress webp --join-named false --flatten false`, recorded in the README. Each GLB then embeds its own 2.6 kB WebP (`EXT_texture_webp`, required); WebP is supported by Chromium and by WKWebView on macOS 14 (the app's minimum, `pypsa-gui.spec`).

**Red (`site3d/heroes.test.ts`, node `fs` + `glbJson`, no GLTFLoader — it cannot parse in jsdom):**
- Each manifest file exists, starts with the `glTF` magic, is ≤ 64 kB; total ≤ 200 kB raw; README and licence present and naming source and command.
- `windmill.glb` has a node `blades`; its parent chain has scale 1 (the meshopt pitfall); the manifest's hub pivot equals the node translation (1.676 units) and its rotor axis equals the node's local X.
- For each hero, the manifest's recorded node matrix and native bounds equal what `glbJson` reads (container root scale 0.27 and long axis Z; tank root mirrored `[-1, 1, 1]` and long axis X), so a re-processed file that changes them fails here.
- URLs are relative to `import.meta.env.BASE_URL`, never absolute or `http`.

### Task 3.2 — Offline loading and the no-CDN guard

**Files:** `site3d/heroLoader.tsx` (new, three/drei, SiteCanvas-only), `site3d/noCdn.test.ts`.

**Red:** the guard fails on a fixture string containing `gstatic.com` / `draco/versioned` / `jsdelivr` / `unpkg` / `.wasm`, and on a `useGLTF(` or `useGLTF.preload(` call whose second argument is not `false` (the fixture proves it red; the real source passes). **Green:** `useHero(id)` = `useGLTF(url, false, false)`; `useGLTF.preload(url, false, false)` on site open.

### Task 3.3 — Fitting (pure)

**Files:** `site3d/heroes.ts`.

**Red:** `heroInstances(obj, hero)` returns per-unit transforms **whose world-space boxes** (computed in the test from native bounds × baked node matrix × base rotation × fit) equal the parametric units they replace, within 5 %:
- container, **long-axis stretch**: a 20 ft unit (east-west in the layout) → box 6.1 × 2.44 × 2.9 m, not mirrored, not 3.7× oversized;
- turbine, **uniform** scale so the **hub** (1.676 units) lands at the hub height; rotor facing south like the parametric turbine; one blades transform per turbine = instance × blades node × spin;
- tank, **uniform** scale to the tank length, un-mirrored, long axis north;
- PV table group and hall, **uniform scale + tiling**: tables tiled along each row (the model is already tilted — the part's `rotX` is not applied twice); a hall tiled to its footprint, height kept plausible (no 14× vertical stretch).
- Only `heroable` units are instanced; PCS skids, stacks and BoP stay parametric.

### Task 3.4 — Instanced rendering and per-object materials

**Green:** `HeroInstances` renders one `<instancedMesh>` per mesh of the hero (baked node matrices), keyed on the instance count (fixed at construction), bounding sphere/box recomputed after matrix updates; the turbine's blades are a second instanced mesh with a pivot per turbine; materials **cloned per object** (tint via `setColorAt` or the clone's colour; selection/outside via the clone's emissive), clones disposed with the object, **cached hero geometry never disposed** by an object. The object's heroable parts are omitted from its merged body while the hero shows.

### Task 3.5 — Fallback and failure

**Files:** `site3d/SceneErrorBoundary.tsx` (new, no three: a class boundary whose `fallback` prop is any React node, so it works inside the r3f tree — the app's DOM `ErrorBoundary` cannot).

**Red (`SceneErrorBoundary.test.tsx`):** renders `fallback` when a child throws; resets when its `resetKey` changes. **Green:** each hero under `<Suspense fallback={parametric}>` inside `<SceneErrorBoundary fallback={parametric}>`; a failed URL is cleared with `useGLTF.clear` when the site reopens.

**WP3 browser smoke:** snapshot shows the Kenney turbine, containers, tanks, tables and hall at the right scale and orientation; with `**/site3d/models/**` aborted, the parametric scene renders with no page error; **no request** matches `gstatic|draco|jsdelivr|unpkg|\.wasm` and every `.glb` request is same-origin (Esri imagery requests are expected and ignored). **Gate focus:** provenance, fitting, materials not leaking, fallback.

---

## WP4 — Sizes: as built / optimised

### Task 4.1 — Sizing rule

**Red:** `sizeOf(component, rule, mode)`: installed → `*_nom`; optimised → `*_nom_opt` only when extendable and finite > 0 (a myopic heat-pump Link's `-0.0` falls back to installed), else installed; StorageUnit MWh = `p_nom(_opt) × max_hours`; summary says "(optimised)". A BESS `p_nom 10 → p_nom_opt 40` draws 4× the containers in optimised mode.

### Task 4.2 — The switch, gated on fresh dispatch

**Files:** `site3d/useDispatchFresh.ts` (new: a `useQuery` on `nk(project, 'simulationStatus')`, polling every 3 s while the site view is mounted — the same key the status bar uses; see Task 5.1 for why this owns its own poll), `components/SiteOverlay.tsx` (`dispatchFresh` prop), `store/uiStore.ts` (`siteSizing`), `pages/SiteCanvas.tsx`.

**Red:**
- `SiteOverlay.test.tsx`: the switch renders only when `dispatchFresh`; toggling calls `setSiteSizing`; "(optimised)" in the fit line in optimised mode; **(pin)** enabled when read-only (not a mutation).
- `effectiveSizing(siteSizing, dispatchFresh)` = optimised only when both; a test that a stale dispatch draws installed sizes although `siteSizing` is `'optimised'`.

---

## WP5 — Results data

### Task 5.1 — The 3D view's own status poll

**Red (`site3d/useDispatchFresh.test.tsx`):** with the simulation store `idle` (a loaded, solved project; or a queue solve), the status is still polled; `dispatch` of `'fresh'`, `'none'`, `'stale'` and `undefined` map to fresh/not; **the status bar shows no toast and does not call `clearResults`** when this hook's polled data arrives while the store is `idle` (render both; spy on `toast` and the store).

### Task 5.2 — Freshness rules

**Files:** `components/CanvasResultsContext.tsx` (export `useChunkedSeries`; add `useChunkedSeriesMeta` returning `{data, dataUpdatedAt, isFetching}` — additive, the context unchanged), `site3d/useSiteResults.ts` (new, no three).

**Red (`site3d/useSiteResults.test.tsx`, react-query with an explicit `staleTime`, `vi.mock('../api/simulation')` with a **range-aware** mock: `{from:0,to:0}` returns columns + `range.total`, a chunk returns its `range.from`):**
- **(pin)** empty map and no chunk request while the Eye is off.
- Empty map while dispatch is not fresh.
- **Drop on edit:** a populated map empties as soon as a component list the site reads refetches (simulate: invalidate `nk(p,'generators')`), before the status query answers; **(pin)** a placement write (sites sidecar) keeps the map.
- **Reject pre-resolve chunks:** seed chunk A, flip status none → fresh with the refetch pending → empty map; when the new chunk lands → filled. On mount with a warm, valid cache and status already fresh → filled from cache (`freshSince` = 0 on mount).
- Index clamp: an index beyond the horizon uses the last row (the context's rule).

### Task 5.3 — Per-asset map

**Red (same file):**
- Inputs: the site objects **and** the component lists SiteCanvas already fetched.
- For `[Generator:PV, StorageUnit:BESS 1, Store:H2 tank, Load:Hall A, Link:Electrolyser, Line:L1, Transformer:TR1]`, the map for the current snapshot holds MW, share (via the denominator helper, 5.4), SoC/fill share, loading % and direction.
- Transformers come from `getTransformerResults` (exists, `api/simulation.ts:1071`); the Lines series is never consulted for a `Transformer:` key; **(pin)** the transformer query key does not collide with LoadFlow's.
- Only series the site needs are fetched (no Store → no store calls).
- **Cache sharing:** the query keys equal the context's (`qc.getQueryCache().find({queryKey: nk(p,'results','generators','lopf',0)})`); after a remount the map is filled from cache before any mocked promise resolves; the probe is not refetched (`staleTime: Infinity`).
- An object absent from the columns maps to no state (not zero); a stray `X@2030` column is ignored.
- **Chunk boundary:** while the next chunk is pending, the last state is held (no flicker to neutral); the next chunk is prefetched near `bounds.to`.

### Task 5.4 — Denominators

**Files:** `site3d/capacity.ts` (new, pure; the context's `effectiveCapForAsset` and period lookup move here and the context imports them — its behaviour pinned by a characterisation test first).

**Red:** `capOf(c)` = `*_nom_opt` if finite > 0, else `*_nom` if > 0, else `null` (no share); period-effective capacity with a `Solar2@2028` vintage fixture; load share = MW / (profile peak × max scaler) from `networkApi.getLoadProfiles()`, else |p_set|, clamped to [0, 1], with a chunk whose max is below the peak (the chunk max is not used).

### Task 5.5 — Result styling (pure)

**Files:** `site3d/resultStyle.ts` (no three), `components/CanvasResultsContext.tsx` (export `socColor` beside `loadingColor`; the SoC bands inline in `TopologyCanvas.tsx:722–727` move there — pinned first).

**Red (`resultStyle.test.ts`):** `visualFor(type, state, obj)`: storage → gauge share + `socColor` band + "charging"/"discharging"; wind → `spin` = rated rpm (library field on the wind entry) × output share, 0 when ≤ 0; other generators, electrolysers, heat pumps, CHP → emissive = share; Links' label says "MW in"; loads → emissive = load share, label shows MW; branches → `loadingColor(pct)` + `flow.dir = sign(p0) × (obj.bus is bus0 ? 1 : −1)`, label names the destination bus (a line owned through bus1 with p0 > 0 points to the yard); bidirectional DC links and loading > 100 % (ac_pf) handled. Every share's label names its capacity ("SoC 64 % of 160 MWh"); every coloured state carries a number.

**WP5 gate focus:** freshness rules, cache sharing, denominators, no three.

---

## WP6 — Results rendering

### Task 6.1 — Pure motion

**Files:** `site3d/motion.ts` (no three).

**Red:** `easeTowards(current, target, dtMs, tauMs)` converges, never overshoots; `motionFor(reduced)` → no easing and no spin when reduced; `useReducedMotion()` modelled on `hooks/useIsCoarsePointer.ts`: guards a missing `matchMedia`, subscribes to `change` (stubbed `matchMedia` in the test).

### Task 6.2 — Driver and layer

**Files:** `site3d/resultsLayer.tsx` (three, SiteCanvas-only), `pages/SiteCanvas.tsx`.

**Green:** `<ResultsDriver>` (a leaf inside the Canvas; r3f bridges the QueryClient context) calls `useSiteResults` and writes the map into a ref — it is the only component that renders per snapshot; `ResultsLayer` reads the ref in `useFrame` and updates: rotor groups / blade instance matrices (spin about each hub), **exterior fill gauges** (own material, beside tank arrays and container rows), emissive via `emissiveFor` on each object's own material (hero clones included), branch tint + chevrons along the flow anchor from `bus` towards `far`.

### Task 6.3 — Readout, legend, labels

**Files:** `components/SiteLegend.tsx`, `components/SiteResultsReadout.tsx` (new, no three), `pages/SiteCanvas.tsx` (label line updated imperatively like `LabelTracker`).

**Red:** the legend shows loading and SoC bands **with their thresholds as numbers** only while results show; the readout lists each site object with its current value (timestamp heading), only while results show; the hover label appends `visualFor(…).label`.

### Task 6.4 — Timeline accessibility (in scope: it is the 3D view's only time control)

**Files:** `components/SnapshotPicker.tsx`.

**Red (`SnapshotPicker.test.tsx`):** the slider has an `aria-label` and an `aria-valuetext` equal to the timestamp. **(pin)** the 3D view reads `resultsSnapshotIdx` and never writes it.

### Task 6.5 — Reduced motion wired

**Green:** the layer reads `useReducedMotion()`; reduced → instant targets, rotors still (speed shown in the label/readout).

**WP6 browser smoke:** solve `campus-year` (recipe in QA); Eye on; step the picker (`title="Next snapshot"` × 12, then set the range input) — the slider is enabled only with the Eye on; `window.__site3d.visual(key)` (new debug field) reports gauge, spin and loading, which change between steps; `snapshot()` differs; `renders` of SiteCanvas and object meshes flat while playing, the driver's +1 per snapshot, 0 per frame; `page.emulateMedia({reducedMotion: 'reduce'})` → spin 0, instant gauges. **Gate focus:** isolation, anchors, precedence, a11y.

---

## QA — end to end

1. Frontend: `tsc -b`, full `vitest`, `npm run build`; `SiteCanvas` chunk separate; main `spa` chunk growth ≤ 5 kB gzipped against the Phase 1 close (825.74 kB); `dist/site3d/models/*.glb` present.
2. Backend: full `pytest` once; the pre-existing packaging-spec failure is the only allowed failure.
3. Headless run, auth mode, **idle machine**:
   - fixture `campus-year` = the campus network with its time series tiled to 8760 hourly snapshots (the Phase 1 recipe, now a script in the QA note's appendix), plus an extendable BESS;
   - every palette type present renders as its type; heroes visible; heroes blocked → parametric, no page error;
   - solve **from the header** (Run LOPF); as built / optimised flip;
   - Eye on, play 48 snapshots → gauges, spin, loading change; readout and labels show values;
   - **re-solve** → no frame shows the previous solve's values (spec E12 rule 3);
   - edit a component → results vanish on the component refetch or within one poll (not "immediately");
   - reload a solved project, edit → results vanish (the store is `idle`: rule 1);
   - reduced-motion run.
4. Packaging: `dist/site3d/models/*.glb` served by the backend static route (a smoke GET returns 200); WebP on macOS 14 recorded as satisfied by the minimum OS; the macOS build remains a runbook step.
5. `docs/superpowers/notes/<date>-3d-site-view-phase2-qa.md`; a `CONTEXT.md` glossary entry for "site results" and the freshness rule.

---

## Review findings folded into v2

Reviewer L = library/rendering/heroes; reviewer R = results/testability.

| Finding | Severity | Where it landed |
|---|---|---|
| L1 Phase 1 layout assertions cannot stay unchanged | blocker | Task 1.1 (ids kept), Task 1.3 table of changed assertions |
| L2 GLTFLoader.parse cannot run in jsdom | blocker | Task 3.1 parses the GLB JSON chunk (`glbJson.ts`) |
| R1 no reliable stale signal | blocker | Spec E12/§6.3; Tasks 5.1, 5.2; QA 3 |
| L3 H2 side rule inexpressible; ownerBus; CHP; anchored regex; empty bus2 | major | Spec E2; Tasks 1.1, 1.2, 1.4 |
| L4 layout gets no bus carriers | major | Task 1.2 (`busCarrier` from all buses, both call sites) |
| L5 one `match` object; fallbacks; frozen `g` regex | major | Spec §4.1 `match: Match[]`; Task 1.1 |
| L6 cylinder size order | major | Spec E4; Tasks 1.1, 1.3 |
| L7 hero node transforms, axes, per-axis distortion | major | Spec §5.1, E8; Task 3.3 world-box tests |
| L8 / R5 rotors per hub; wind uncapped | major | Spec E5; Tasks 1.3, 2.1, 2.2, 3.3, 6.2 |
| L9 shadow pass doubles draw calls | major | WP2 smoke criterion |
| L10 debug `project()` after merging | major | Task 0.4 |
| L11 / R6 shared hero materials; emissive collisions; invisible fill | major | Spec E8, §6.4; Tasks 2.2, 3.4, 6.2 |
| L12 `dispatchFresh` source; order; sizing after an edit | major | Spec E6; Task 4.2 |
| L13 `*_nom_opt`, `bus2` missing from types; -0.0 | major | Task 0.1; Task 4.1 |
| L14 rooftop PV vs per-bus packer | major | Spec §4.2; Task 1.5 |
| L15 palette drift undetectable | major | Task 0.3 |
| L16 / R10 bundle guard `.tsx` and chains | major | Task 0.2 |
| L17 "zero foreign requests" vs Esri | major | WP3 smoke criterion |
| R2 previous solve shown after re-solve | major | Spec E12 rule 3; Task 5.2 |
| R3 "one fetch across remount" unsound | major | Task 5.3 cache assertions |
| R4 per-tick re-render; store subscription | major | Task 2.2 (isolation), Task 6.2 (driver leaf), WP2/WP6 smoke counters |
| R7 flow has no bus reference | major | Task 1.4 (`bus`, `far`), Task 5.5 |
| R8 load peak source | major | Task 5.4 |
| R9 multi-period denominators | major | Task 5.4 |
| R11 values hover-only; slider a11y | major | Tasks 6.3, 6.4 |
| L18 merge vertex counts | minor | Task 2.1 (indexed, 24/100) |
| L19 no-global test not red | minor | Task 1.3 source guard |
| L20 guard regex too broad | minor | Task 1.3 (comments stripped, literals only) |
| L21 noCdn is a pin; preload | minor | Task 3.2 (fixture proves red; preload covered) |
| L22 no shared texture; task order | minor | Spec §5.1; Task 3.1 merges manifest and files |
| L23 WebP on macOS | minor | Task 3.1, QA 4 |
| L24 DOM ErrorBoundary in r3f | minor | Task 3.5 `SceneErrorBoundary` |
| L25 instanced bounds and count | minor | Task 3.4 |
| L26 selection whitening with vertex colours | minor | Task 2.2 (emissive only) |
| L27 smoke cannot put every item on 33 kV | minor | WP1 smoke adds H2/heat buses as members |
| L28 attribution; bay/asset counts; spec contradictions | minor | Task 3.1 README + overlay credit (Task 3.4 renders "Models: Kenney (CC0)" in the attribution line); Task 1.3 flags; spec §1/§7 fixed |
| L29 bundle/packaging checked OK | — | recorded in Architecture |
| R12 denominator edge cases; label names capacity | minor | Task 5.4, 5.5 |
| R13 link semantics (MW in), DC, >100 % | minor | Task 5.5 |
| R14 `getTransformerResults` exists; LoadFlow key | minor | Task 5.3 |
| R15 legend/label/reduced-motion testability | minor | Tasks 6.1, 6.3 (extracted components) |
| R16 Eye-off test is a pin; fresh→none; index clamp | minor | Task 5.2 |
| R17 chunk-boundary flicker; 9 endpoints | minor | Task 5.3 (hold + prefetch) |
| R18 campus-year recipe; stepping; solve from header; re-solve | minor | QA 3; WP6 smoke |
| R19 stale-toast interplay | minor | Task 5.1 |
| R20 spec defects; `socColor` | minor | spec revised; Task 5.5 |
| R21 hook inputs | minor | Task 5.3 |
| R22 docs | minor | QA 5 |
