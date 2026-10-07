# 3D site view — feasibility assessment

Date: 2026-09-28
Scope: a third canvas beside the network view (`pypsa-gui/frontend/src/pages/TopologyCanvas.tsx`) and the map view (`pypsa-gui/frontend/src/pages/MapCanvas.tsx`): a 3D model of a *site* (data-centre campus, hydrogen campus, substation) built from map data, on which the user places default 3D models of assets, selects them, and edits them.
Method: the app was read at the seams a third view would touch (view switch, selection, creation, layout persistence, results overlay, packaging); the rendering stacks and the open geodata / 3D-model landscape were researched on the web the same day. Every version, price and licence below was checked against the vendor's own page on 2026-09-28; every code claim was read at the cited line.

This is an assessment, not a design and not a plan. Its purpose is to say whether the idea is buildable, what it costs, where the real difficulty is, and which decisions only the owner can make. The questions in §8 are the ones that change the design; they should be answered before a spec is written.

## 0. Verdict in one paragraph

Feasible, and cheaper than it sounds on the graphics side — the hard part is not 3D. The 3D rendering of a ~1 km² site with a few hundred clickable models is a well-trodden path in the open-source web stack, and the app's selection and properties machinery can be reused unchanged, so "click a transformer in 3D and edit its `s_nom`" is a matter of weeks. Two things are genuinely hard and are *data-model* problems: (1) the app has no notion of a site, campus or substation — everything above a PyPSA bus is synthetic — so a "campus" entity, a boundary polygon, and per-asset physical placement all have to be invented and persisted; (2) "realistic" site context from map data is good in the UK, NL, DE and much of the US, and blocky elsewhere, and the photoreal alternative (Google 3D Tiles) is paid, online-only and cannot be edited. The recommended shape is a **portal**, not a continuous zoom: the Leaflet map stays as it is, a site polygon on it opens a self-contained 3D scene built on a local tangent plane, rendered with three.js / react-three-fiber, with terrain and building footprints fetched once by the backend and cached in the project bundle. A credible v1 is roughly 2.5–4 engineer-months; a demo-grade spike that proves the integration is 1–2 weeks.

## 1. What exists today (calibration)

Reviewers should not re-derive these; they were read.

- **The view switch is one ternary.** `App.tsx:610-612` renders `canvasView === 'blank' ? <TopologyCanvas/> : <MapCanvas mode={canvasView}/>`. `CanvasView` is `'blank' | 'satellite' | 'hybrid'` (`store/uiStore.ts:51`), persisted in localStorage, and `MapModeSwitcher.tsx:6-10` hard-codes the three buttons. Adding a fourth value and a fourth branch is trivial; both canvases are imported statically (no code splitting — `App.tsx:12-13`), which a 3D view must not copy.
- **Selection and editing are view-independent.** Both canvases call `setSelectedComponent({type, name})` (`uiStore.ts:550`) and `PropertiesPanel.tsx:2592-2600` switches on `type` to a per-class editor. A 3D view that sets the same state gets the full parameter editor, the results deep-link (`requestAssetDetail`, `uiStore.ts:630`) and undo for free. This is the single biggest reason the idea is cheap.
- **Creation is a pointer-drag into a hit-test.** `hooks/useAssetDrag.ts:15-60` hit-tests `[data-bus-name]`, then `.react-flow`, then `.leaflet-container`. A third branch for a 3D canvas fits the existing shape (the code comments call this out as the one place both canvases share, spec D25).
- **There is no site, campus, plant, or hierarchy.** The only groupings above a bus are the free-text `Bus.sub_network` label (`api/types.ts:2-3`, used as a clustering key in `backend/routers/clustering.py:149-160`), `Bus.country`, and the synthetic per-bus × category "AssetGroup" bubble (Thermal / Renewables / Storage / Load, `MapCanvas.tsx:73-98`). No polygon, footprint, elevation, or building data exists anywhere.
- **Physical layout has a precedent for living outside PyPSA.** The blank canvas saves node positions to a server-side `layout.json` that is explicitly "NEVER part of the network model" (`backend/routers/projects.py:72-77, 89`). The map view's bubble offsets and line waypoints are localStorage-only, with server persistence noted as "a deferred follow-up" (`MapCanvas.tsx:110-112`). A 3D placement sidecar should follow the `layout.json` pattern, not the map's.
- **Asset defaults are hard-coded in the frontend.** `CreationForm.tsx:89` `FIELD_MAP` (mostly `capital_cost '0'`), carrier lists in `utils/carrierCatalog.ts`. `useCatalog` serves PyPSA's attribute defaults, not asset templates. There is no "a 100 MWh BESS is N containers" knowledge anywhere; the 3D view would be the first consumer of such a thing.
- **Results overlay is shareable.** `CanvasResultsContext.tsx` is one provider, mounted by whichever canvas is active, exposing per-bus, per-line, per-link and per-asset-group results for the selected snapshot (`byAssetGroup`, `byAssetGroupSoC`, `byAssetGroupCapacity`, keyed `${bus}|${category}`). A 3D view can animate state of charge, loading and flows from this without new endpoints.
- **Neither canvas enforces read-only.** `readOnly`/`readOnlyReason` (`lockState.ts:21`) gate the header, Sidebar and panels; the canvases rely on the backend's 409 `project_locked`. A 3D editor with drag gizmos must at least disable the gizmos when locked, or the user drags something that silently does not save.
- **Packaging.** The desktop build is PyInstaller (`pypsa-gui/pypsa-gui.spec`) serving `frontend/dist` inside a **pywebview** shell (`backend/desktop/gui.py`), i.e. WKWebView on macOS. WebGL2 is fine there; WebGPU is available from Safari 26. The spec deliberately excludes heavy geo backends (`rasterio`, `boto3`, `cfgrib`) that xarray drags in — a site-context service must not reintroduce them casually. Map tiles need internet today and there is no offline handling (`MapCanvas.tsx:215-233`).
- **Tests.** vitest + jsdom, no WebGL mock; canvas tests exercise exported pieces rather than mounting a whole canvas (`MapCanvas.busicon.test.tsx`, `TopologyCanvas.busnode.test.tsx`). The 3D view must keep its scene logic pure (as `utils/geo.ts` does) or it will be untestable.

## 2. Decomposing the ask

The request bundles five capabilities of very different difficulty. Separating them is most of the assessment.

| # | Capability | Difficulty | Why |
|---|---|---|---|
| A | A 3D canvas as a third view: camera, lighting, click-to-select, wired to `selectedComponent` and `PropertiesPanel` | Low | Standard R3F; selection/editing machinery already exists |
| B | Default 3D models per asset class, scaled by PyPSA parameters | Low–medium | Parametric primitives from `p_nom`/`e_nom`; a handful of curated hero models |
| C | A **site/campus entity**: boundary, member buses, per-asset placement, persisted per project and per scenario | **Medium–high** | Does not exist; touches the project bundle, scenario copy, undo, locks |
| D | Realistic site context from map data: terrain, building footprints with heights, imagery on the ground | Medium; **quality is region-dependent** | Open data is good in UK/NL/DE/US; elsewhere blocky. Backend fetch + cache |
| E | "Operate" the assets in 3D: animate results over snapshots, edit in place | Low–medium if it means results + parameters; **out of scope** if it means anything SCADA-like | `CanvasResultsContext` already exposes what an animation needs |

A and B are the demo. C is the product. D is the thing the owner will be judged on visually and is the least controllable. E needs a definition (§8, Q5).

## 3. Rendering stack

Six options were assessed; the full comparison is in §9. Verified as of 2026-09-28:

| Stack | Version / licence | Terrain + buildings + imagery | glTF place / pick / edit | Bundle (gz) | Offline in packaged app | Run cost |
|---|---|---|---|---|---|---|
| three.js + react-three-fiber + drei (+ `3d-tiles-renderer`) | three 0.186, R3F 9.8, drei 10.7 — MIT; 3d-tiles-renderer 0.5.3 Apache-2.0 | Heightmap + imagery texture you build; Google / Cesium ion tiles via `3d-tiles-renderer` | **Best**: `TransformControls`, `PivotControls`, `Outline`, multi-select come from drei | ~0.2–0.4 MB | Yes — the scene is files | Free |
| CesiumJS + Resium | 1.145 Apache-2.0; Resium 1.26 MIT | **Best**: World Terrain, OSM Buildings, Google 3D Tiles, Esri imagery out of the box | Entities, `scene.pick`, clamp-to-ground; no gizmos | ~1.7 MB + ~30 MB static assets to copy into the Vite build | Yes with self-hosted terrain/imagery | Cesium ion is paid once the org is funded (Commercial $149/mo, Team $524/mo); Google $6/1k sessions |
| MapLibre GL JS v6 + three.js custom layer | 6.11.2 BSD-3 | Terrain from free DEM, OSM `fill-extrusion`, Esri/MapTiler imagery | All hand-rolled inside a custom layer; **no native model layer** | ~0.3 MB + three | Best (PMTiles + local DEM) | Free |
| deck.gl 9.4 (+ MapLibre) | 9.4.0 MIT | Via MapLibre or `Tile3DLayer` / `TerrainLayer` | `ScenegraphLayer` instancing + picking; no gizmos | ~0.55 MB | Yes | Free |
| Mapbox GL JS v3 | 3.31 proprietary (account-locked) | Standard style 3D, native `model` layer (experimental) | Style-driven models | ~0.5 MB | **No** (terms) | 50k loads free, then $5/1k |
| Esri ArcGIS Maps SDK for JS SceneView | 5.1.26 proprietary, free with a Location Platform key | Esri 3D basemaps — **not on the free tier**; needs ArcGIS Online | `ObjectSymbol3DLayer`, Sketch widget | Large, chunked | Limited | 3D basemaps need an AGOL subscription |

Notes that matter for this app:

- **Esri imagery is fine in any renderer.** Esri explicitly supports its basemaps in MapLibre, Leaflet, OpenLayers and CesiumJS with a key and attribution. The tiles already used (`server.arcgisonline.com/.../World_Imagery`) can be draped on a 3D ground plane. **Persistent offline caching is not allowed on the free tier**; "World Imagery (for Export)" needs an ArcGIS Online subscription. This is the one licensing fact that decides the offline story (§6).
- **Google Photorealistic 3D Tiles** are usable from CesiumJS, deck.gl or three.js: 1,000 free root-tile requests/month, then $6.00 per 1,000 (a root request opens a ~3 h session). Policies: no caching beyond `Cache-Control`, **offline use prohibited**, Google logo plus per-tile provider credits must be shown, overlaying your own models is allowed if it is clear which content is Google's, and the tiles may not be paired with a non-Google geocoder. The photogrammetry mesh is baked: a new BESS yard sits *on top of* whatever is there today, which is wrong for a greenfield planning view.
- **MapLibre still has no `model` layer** (style-spec layer types verified; "MLT 3D" is a research item in the April 2026 newsletter). A "continuous zoom" from the map into 3D on MapLibre would mean replacing the 1,329-line Leaflet map *and* writing the model layer in three.js anyway.
- **WKWebView** (pywebview) runs WebGL2 without issue; deck.gl 9.4's WebGPU path is optional and WebGL2 stays the default.

**Recommendation: three.js + react-three-fiber + drei, as a portal.** It is the lightest bundle, the only option with editing gizmos and selection outlines off the shelf, inherently offline-capable, MIT-licensed, and it has a first-class React reconciler that fits a Vite/React 19 app. The cost is that georeferencing and tile plumbing for a ~1 km² tangent-plane scene are ours — fine at site scale, painful if a "site" ever becomes a region. **Keep CesiumJS as the named alternative** for the case where photoreal context (Google tiles) or regional scale becomes a requirement; `3d-tiles-renderer` also lets the R3F scene load Google/ion tilesets later without switching engines. MapLibre + deck.gl is the middle path only if the owner insists the 3D view be visually continuous with the map (Q3).

## 4. Site context from map data

What "realistic" can mean, region by region, with open data only:

- **Building footprints** — Overture Maps (release 2026-09-23.1, ODbL) is the single pragmatic source: a conflation of OSM, Esri Community Maps, Microsoft ML, Google Open Buildings and national cadastres, with `height`, `num_floors`, `min_height`, `roof_shape` merged across sources. Queried by bbox from Python with DuckDB over GeoParquet on S3 (predicate pushdown on the bbox columns), or the `overturemaps download --bbox` CLI. Overpass is the fallback and the source for what makes a site read as a plant: `power=*`, `man_made`, `landuse`, `barrier=fence`, roads, rail. The public Overpass instance rate-limits and load-sheds; results must be cached server-side.
- **Heights** — the weak link. Explicit heights exist on under 5% of OSM footprints globally; 15–25% in well-mapped European centres. The real answer is national LiDAR: England (EA 1 m DSM/DTM, OGL), Netherlands (AHN5 0.5 m via PDOK), Germany (LoD2 CityGML open in most Länder), US (USGS 3DEP 1 m). Height = zonal max of DSM − DTM per footprint. GlobalBuildingAtlas (TUM 2025) is complete and global but **CC BY-NC**, unusable commercially without a deal.
- **Terrain** — Copernicus GLO-30 (free, attribution) or AWS Terrarium tiles (no account). At 1 km² an industrial site is near-flat; 30 m is enough for a background mesh. Terrarium PNG tiles decode with PIL, which avoids reintroducing `rasterio` into the PyInstaller bundle.
- **Imagery** — the Esri World Imagery tiles already in use, stitched into a ground texture for the bbox at z17–19.

Expected quality:

| Region | Footprints | Heights | Verdict at 1 km² |
|---|---|---|---|
| UK, NL, DE, parts of US and Spain | Complete | ±1 m from LiDAR / LoD2 | Believable LoD1–LoD2 |
| Rest of EU / US | Complete | 20–60% measured, rest estimated from floors or landuse | Plausible but generic |
| Rest of world | Google / Microsoft ML footprints | Mostly guessed | Blocky context on imagery |

What makes a site believable at this scale is correct footprints with plausible heights, fences and roads from OSM, and imagery draped on the ground — not terrain fidelity and not photogrammetry. The industry calibration point (§9, precedent) is that *planning* tools (PVcase Prospect, Gilytics Pathfinder, CityEngine) render blocky parametric masses on real terrain and imagery; only *engineering* tools (Bentley OpenUtilities Substation, Cadence Reality DC) render vendor-accurate equipment, and they charge enterprise prices for it. The target here is the former.

Licensing to clear before shipping: Overture and OSM are ODbL (attribution; share-alike applies to derived *databases*, so whether a cached per-site bundle is a "produced work" or a "derivative database" is a question for legal, not for this document); Copernicus and Esri require attribution; Google tiles carry the constraints above.

## 5. Default 3D asset models

There is no curated open library of substation / BESS / electrolyser models; every team rebuilds this. Sketchfab has usable CC-BY transformer-substation and wind-turbine models (each licence must be checked; many are NC), Poly Haven has CC0 industrial tanks and pipes, Kenney's City Kit (Industrial) and Factory Kit are CC0 glTF.

**Parametric first, curated second.** Nearly every asset in scope scales with a PyPSA parameter that a fixed model cannot express:

| Asset | Driven by | Geometry |
|---|---|---|
| BESS | `e_nom` (≈3–5 MWh per 20 ft container in 2025 products) | N containers in rows + PCS skids |
| PV | `p_nom` (~2–3 ha/MWp) | rows × tables, tilt, pitch |
| Wind | `p_nom` → hub height, rotor diameter | tower cylinder + nacelle + 3 blades |
| Gas genset / turbine | `p_nom` (~2–3 MW per 40 ft enclosure) | N enclosures + stack |
| Electrolyser | `p_nom` (~5–10 MW per container) | stacks + compressor building |
| H2 storage | `e_nom` | bullet tanks or tube trailers |
| Data hall | IT MW | footprint × height |
| Transformer bay | `s_nom` | tank + radiators + bushings |

All of it composes from boxes, cylinders and extrusions: three.js `BufferGeometry` / `InstancedMesh` on the client for instant feedback when a parameter changes, and an equivalent `trimesh` generator in FastAPI if GLB export is ever wanted. Curated GLBs earn their place only for 5–8 "hero" objects whose silhouette carries recognisability (power transformer, GIS hall, HV lattice tower, nacelle, cooling tower), vendored in the repo with attribution. Roughly 14–18 asset types are needed. Budget: 200–2,000 triangles per parametric instance (instanced), ≤30 k per hero model, whole scene under 1–2 M triangles and ~30 MB so it runs on a laptop; Draco/meshopt compression and LOD swaps to boxes beyond ~500 m.

A useful side effect: the "MW per container / ha per MWp" table this needs is *the* land-area-fit calculation, which is one of the few places a 3D view adds analytical value to an investment decision rather than just presentation (Q1).

## 6. The data-model problem (the real work)

PyPSA's model is electrical, not physical. A campus is, in PyPSA terms, one or more buses with attached components; nothing says where a generator physically sits, how big it is, or which fence it is inside. The 3D view needs three things that do not exist:

1. **A site entity.** Name, boundary polygon (WGS84), centroid, the member buses, and the derived tangent-plane origin. Where the boundary comes from is a product decision (Q2): drawn by the user on the Leaflet map, or seeded from an OSM `landuse` / `power=substation` polygon.
2. **Per-asset placement.** Position (local metres or lat/lon), heading, and any layout parameters (rows, pitch) for every placed component, keyed by `{type, name}`. This must be a sidecar *outside* PyPSA, in the project bundle, versioned with the project, and **carried by scenario copies** — a scenario is a copied project (`CONTEXT.md`, "Scenario"), and whether the copy today carries `layout.json` has to be verified before the same path is relied on. It must also participate in undo, or dragging a container and hitting ⌘Z will desynchronise the scene from the model.
3. **A parameter-to-geometry mapping.** The table in §5, as data (YAML/JSON), not code, so that a new asset type or a Hitachi-specific product line is a data change.

None of these is hard individually. Together they are the part of the work that touches the backend, the bundle format, the scenario copy, the lock semantics and undo, and they are what turns a demo into a product. A spec for this view is mostly a spec for (1)–(3); the rendering is the easy half.

## 7. Offline, packaging and performance

- **Offline.** The packaged Mac app can show a 3D site offline only if the site context bundle (terrain, footprints, and the imagery texture) was fetched while online and cached in the project directory. Terrain and footprints are fine to cache (open licences). **The Esri imagery texture is not**, on the free tier. So an offline site is either textured with imagery the user is not licensed to cache, or shown on a plain/ hillshaded ground. This needs an answer (Q7) and possibly an ArcGIS Online subscription, which Hitachi Energy may already hold (Q8).
- **Bundle weight.** three + R3F + drei add ~0.3 MB gzipped; they must be a lazily-loaded chunk (the current `App.tsx` imports both canvases statically and `vite.config.ts` has no `manualChunks`; the 3D view should be the first `React.lazy` canvas). CesiumJS would add ~30 MB of static assets to the PyInstaller bundle.
- **Backend dependencies.** A site-context service needs `shapely` (footprints), `duckdb` (Overture) or plain `requests` (Overpass), and PIL (Terrarium decode). Avoid `rasterio`/`GDAL`; the spec already fights them.
- **Performance.** A few hundred instanced parametric models plus a few thousand extruded footprints is comfortably 60 fps in WebGL2 on integrated graphics. The risk is not the site; it is someone loading a 500-bus network and expecting every bus's site in one scene. The portal design (one site at a time) is also the performance guard.
- **Tests.** Scene logic (tangent-plane projection, parametric generators, placement store, hit → `{type,name}` resolution) must be pure modules with unit tests; the R3F tree gets `@react-three/test-renderer`; nothing mounts WebGL in jsdom.

## 8. Questions that change the design

These are the decisions only the owner can make. Each one materially changes the spec. **Answered — see §8a.**

1. **What is the 3D view *for* in an investment decision?** (a) Communicating the proposal to a customer; (b) checking physical feasibility — does 200 MWh of BESS fit on this plot next to the GIS hall; (c) an engineering hand-off. (a) needs looks; (b) needs correct footprints per MW and a boundary polygon, and is the only reading where 3D changes a number in the study; (c) is out of reach for a planning tool and is what Bentley sells. Which of the three is the acceptance test?
2. **What is a campus in PyPSA terms?** One bus with everything attached, or a cluster (HV bus, MV bus, H2 bus, heat bus) that the site groups? And where does the boundary come from — user-drawn on the map, or seeded from OSM landuse / `power=substation` polygons?
3. **Continuous zoom or portal?** "Zoom into the 3D model" can mean the map itself tilts into 3D (requires replacing Leaflet with MapLibre and still hand-writing the model layer), or a site polygon on the existing map opens a separate 3D scene with a fly-in animation. The portal is a fraction of the cost. Is the continuous version a requirement or a nice-to-have?
4. **What is the realism bar, and which regions?** Blocky LoD1 buildings on satellite imagery (free, editable, offline-capable) versus Google photoreal (paid per session, online-only, baked scenery you cannot remove). And which countries do the first customers' sites sit in? UK/NL/DE/US get believable context from open data; the Gulf, India or Africa do not.
5. **What does "operate them" mean?** (a) Edit parameters in place (already covered by `PropertiesPanel`); (b) animate solved results — state of charge, loading, flows — over snapshots (covered by `CanvasResultsContext`); (c) anything live, SCADA-like or control-oriented, which is out of scope for a planning tool. If (c) is in the owner's head, it needs to be said now.
6. **Does placement carry meaning?** If an asset's position in the scene is cosmetic, the sidecar can be simple. If it feeds cable lengths, land-use constraints, or a "fits / does not fit" check, it becomes a modelled quantity that must be versioned, copied into scenarios, undone, and locked like everything else. Which is it?
7. **Must the packaged desktop app show the 3D site offline?** If yes, the imagery texture is the licensing problem (§7), and the site context must be pre-fetched per project.
8. **Paid services and existing licences.** Is a Cesium ion Commercial/Team plan or Google Map Tiles billing acceptable? Does Hitachi Energy already hold an ArcGIS Online subscription (which unlocks Esri 3D basemaps and offline imagery export and would change the stack choice)?
9. **Who owns the asset library and its fidelity?** Generic boxes labelled "BESS", or product-faithful Hitachi Energy transformers and GIS? The latter needs someone in the business to supply or approve models and raises the cost per asset type from hours to days.
10. **Multi-user persistence.** The map view's layout is localStorage-only today, a known deferred item. The 3D placement must live server-side in the project bundle from day one. Agreed?
11. **Priority against gridspine.** Is this a customer-date demo, or a product feature? A spike (§10, Phase 0) can be done in 1–2 weeks and answers half of these questions with something on screen.

## 8a. Decisions (owner, 2026-09-28)

The eleven questions above were put to the owner the same day, each with a
recommended answer, and the recommendation was taken in every case. These are
now inputs to the spike and the spec, not open items.

| # | Decision |
|---|---|
| 1 | Purpose: **physical fit + communication.** The boundary polygon and a footprint-per-MW table drive a fits-or-not check on plot area; the same view is the customer visual. Engineering hand-off is out of scope. |
| 2 | Campus: **a cluster of buses** (HV, MV, H2, heat) grouped by a site whose boundary the **user draws** on the Leaflet map; an OSM landuse / substation polygon may be offered as a starting shape. |
| 3 | Transition: **portal.** Leaflet stays; a site polygon opens a self-contained three.js scene with a fly-in. Continuous zoom is not a requirement. |
| 4 | Realism: **open data by default** (Overture / OSM / national LiDAR footprints on the Esri imagery already in use); Google photoreal tiles are a later, online-only, paid add-on. |
| 5 | "Operate": **edit parameters + animate solved results** over snapshots via the existing `PropertiesPanel` and `CanvasResultsContext`. Nothing live or control-like. |
| 6 | Placement: **fit check from footprints; positions cosmetic in v1**, stored server-side so cable lengths and constraints can be added later without a migration. |
| 7 | Offline: **online-first, degraded offline.** Terrain and footprints are cached per project and shown on a plain ground; the Esri imagery texture is not cached (free-tier terms). |
| 8 | Licences: **no paid services in v1.** Open data, Esri free-tier imagery with attribution, MIT/Apache libraries. (Whether Hitachi Energy holds an ArcGIS Online subscription was not confirmed; it would only matter for full-offline imagery.) |
| 9 | Asset library: **parametric geometry from PyPSA parameters, plus 5–8 curated CC0/CC-BY hero models.** No product-faithful equipment, no business sign-off in the loop. |
| 10 | Persistence: **server-side in the project bundle from day one**, with `layout.json`'s guarantees (versioned, carried by scenario copies, shared between users). |
| 11 | Kick-off: **two-week spike, then spec.** |

### Spike scope implied by the decisions

> **Done 2026-09-28** — see `docs/superpowers/notes/2026-09-28-3d-site-view-spike.md` for what landed, how it was verified, and the eight findings the spec should carry.

Phase 0 in §10, made concrete by the answers above:

- A fourth `CanvasView` value and a fourth switcher segment; the 3D canvas is the first `React.lazy` canvas, in its own Vite chunk (three, R3F, drei only — no Cesium, no MapLibre).
- One hard-coded site: a bbox around one bus of a fixture project, a ground plane textured with the Esri World Imagery tiles the map already uses, no terrain, no footprints yet.
- Parametric boxes for every component attached to that bus (BESS from `e_nom`, generators from `p_nom` by carrier, a transformer bay from `s_nom`), laid out on a grid — enough to see that the parameter → geometry table works.
- Click → `setSelectedComponent({type, name})`; the existing `PropertiesPanel` opens; a parameter edit re-generates the geometry.
- Runs in the packaged pywebview build.
- Not in the spike: the site entity, the boundary polygon, the placement sidecar, drag-from-palette, the backend site-context service, results animation, hero models, offline. Those are Phase 1–2 and belong in the spec the spike informs.

## 9. Research detail

### Rendering stacks

- **CesiumJS + Resium.** CesiumJS 1.145 (Sep 2026), Apache-2.0, unchanged after Bentley's 2024 acquisition; no WebGPU renderer (roadmap: "longer-term"). Resium 1.26 tracks each release, React 19 supported. Vite needs `vite-plugin-cesium` or a static copy of `Assets/`, `Workers/`, `ThirdParty/`, `Widgets/`. Runs fully self-hosted per Cesium's Offline Guide (quantized-mesh terrain from your own DEM via `ctb`, any `UrlTemplateImageryProvider`, local glTF/3D Tiles). Esri officially supports CesiumJS with Location Platform keys (imagery, elevation, I3S). Clamping honours terrain and 3D Tiles since 1.114; only the model origin is clamped. `createGooglePhotorealistic3DTileset` requires `onlyUsingWithGoogleGeocoder`. Cesium ion: Community is free only for personal, unfunded-educational or exploratory commercial use; a paid plan is required once the organisation has ≥$50k revenue/funding or ships to production. Sources: cesium.com/blog/2024/09/06/cesium-joins-bentley, cesium.com/platform/cesium-ion/pricing, github.com/CesiumGS/cesium/blob/main/Documentation/OfflineGuide/README.md, developers.arcgis.com/cesiumjs/faq.
- **MapLibre GL JS.** 6.11.2 (Sep 2026), BSD-3; v6 dropped WebGL1 and is ESM-only, and removed `map.transform` (custom three.js layers that reached into it need updating). Layer types verified: no `model` layer; 3D models are `CustomLayerInterface` + three.js (official examples on flat map, terrain and globe). 3D terrain from `raster-dem` (Terrarium / terrain-RGB); `fill-extrusion` for OSM footprints; CPU DEM raycast picking since 6.6. PMTiles via `addProtocol` makes a fully offline basemap feasible. react-map-gl 8.1 supports MapLibre. Sources: maplibre.org/maplibre-style-spec/layers, maplibre.org/news/2026-05-02-maplibre-newsletter-april-2026, github.com/maplibre/maplibre-gl-js/issues/3794.
- **deck.gl.** 9.4.0 (Sep 2026), MIT; every catalog layer supports WebGPU, WebGL2 default; `@deck.gl/maplibre` interleaves with MapLibre 4–6; `ScenegraphLayer` = one glTF per layer, instanced, picked; heavy glTFs (40–80 MB) drop to 10–30 fps; `Tile3DLayer` loads Google tiles (`X-GOOG-API-KEY` header), Cesium ion, I3S; `TerrainLayer` from Terrarium tiles; no gizmos, no clamping. Sources: deck.gl/docs/whats-new, deck.gl/docs/api-reference/mesh-layers/scenegraph-layer.
- **three.js + R3F + drei.** three 0.186.1, R3F 9.8.1 (React 19.0–19.2), drei 10.7.9, all MIT. drei: `useGLTF`, `Clone`, `TransformControls`, `PivotControls`, `Select`; `@react-three/postprocessing` `Outline`. NASA-AMMOS `3d-tiles-renderer` 0.5.3 (Apache-2.0) has an `/r3f` entry with `TilesRenderer`, `GoogleCloudAuthPlugin`, `CesiumIonAuthPlugin`, `TilesAttributionOverlay`. Terrain from a heightmap is a displaced `PlaneGeometry` on a local ENU tangent plane. Sources: drei.docs.pmnd.rs/gizmos/transform-controls, github.com/NASA-AMMOS/3DTilesRendererJS/blob/master/src/r3f/README.md.
- **Mapbox GL JS.** 3.31 (Sep 2026), proprietary since v2 (account-locked); 50k free loads/month then $5/1k; v3 Standard style has 3D buildings and an experimental native `model` layer; no `addProtocol`, offline not permitted by terms; conflicts with the existing Esri imagery. Sources: github.com/mapbox/mapbox-gl-js/blob/main/LICENSE.txt, mapbox.com/pricing.
- **Esri ArcGIS Maps SDK for JS.** `@arcgis/core` 5.1.26, proprietary, free with a Location Platform key ("Powered by Esri" mandatory). Free tier: 2M basemap tiles/month then $0.15/1k; 1k basemap sessions then $4/1k. "The ArcGIS Basemap Styles service does not support 3D basemaps": the OSM 3D Buildings scene layers need an ArcGIS Online subscription. Sources: developers.arcgis.com/javascript/latest/licensing, location.arcgis.com/pricing, developers.arcgis.com/maplibre-gl-js/faq.
- **Babylon.js / game engines.** Babylon 9.28 (Apache-2.0) works with MapLibre custom layers and `3d-tiles-renderer/babylonjs` but has no React reconciler comparable to R3F. Unity WebGL builds are tens of MB in a canvas hostile to React state; Unreal Pixel Streaming needs a cloud GPU per concurrent user and a round-trip per click, and neither runs offline. Both are the wrong tool for a data-editing panel in a Vite app.

### Site data

- OSM buildings: explicit height/levels on <4.7% of ~600M footprints globally (arXiv 2605.25530; FOSS4G 2023); Overpass public instance: 180 s default timeout, 512 MiB, anonymous rate-limiting (wiki.openstreetmap.org/wiki/Overpass_API).
- Overture Maps 2026-09-23.1: buildings schema and DuckDB access (docs.overturemaps.org/guides/buildings, docs.overturemaps.org/getting-data/duckdb, registry.opendata.aws/overture).
- Microsoft Global ML Building Footprints: 1.4B, CDLA-Permissive-2.0, ~174M with height, NA/W-EU concentrated (github.com/microsoft/GlobalMLBuildingFootprints). Google Open Buildings v3 + 2.5D Temporal (Sentinel-2 10 m heights) for Africa/S-SE Asia/LatAm. GlobalBuildingAtlas LoD1: CC BY-NC (essd.copernicus.org/articles/17/6647/2025).
- National LiDAR: EA England 1 m (data.gov.uk), AHN5 (ahn.nl/open-data), German LoD2 (adv-online.de, github.com/OloOcki/awesome-citygml), USGS 3DEP (usgs.gov/faqs/there-api-accessing-national-map-data).
- Google Photorealistic 3D Tiles pricing and policies: developers.google.com/maps/billing-and-pricing/pricing, developers.google.com/maps/documentation/tile/policies. Cesium OSM Buildings: cesium.com/platform/cesium-ion/content/cesium-osm-buildings. Esri OSM 3D buildings: esri.com/arcgis-blog/products/arcgis-living-atlas/mapping/new-osm-3d-scene-layers.
- Terrain: Copernicus GLO-30 (registry.opendata.aws/copernicus-dem), AWS Terrarium (registry.opendata.aws/terrain-tiles), MapTiler terrain-RGB (free tier non-commercial).

### Asset models

Sketchfab Download API (needs end-user OAuth; CC-BY attribution travels with the asset): sketchfab.com/developers/download-api. Poly Haven Industrial & Infrastructure (CC0): polyhaven.com/models/industrial-infrastructure. Kenney City Kit (Industrial) / Factory Kit (CC0, glTF): kenney.nl/assets/city-kit-industrial. trimesh GLB export: trimesh.org. Turbine parametric precedent: github.com/thisistheplace/dash-wtgviewer.

### Industry precedent (calibration of "realistic")

Bentley OpenUtilities Substation / Substation+ on iTwin (WebGL; iTwin models stream as 3D Tiles after the Cesium acquisition) — engineering-grade, clash detection, BoM. Hitachi Energy IdentiQ — interactive 3D and AR/VR walk-throughs built from the delivered engineering model; engine undisclosed. Siemens Gridscale X — a network (map) twin, not a 3D site renderer. Cadence Reality DC (ex-6SigmaDCX) — CFD-driven 3D data halls, 14,000-item vendor library, enterprise quote-only. Schneider EcoStruxure IT Advisor — 3D rack/floor twin on subscription. Autodesk Tandem from $3,540/yr. ArcGIS CityEngine via Professional ($2,200/yr) / Plus ($4,200/yr). PVcase / RatedPower quote-only (~$10–30k+/yr) with terrain-aware ground-mount viewers; HelioScope / Aurora ~$159/mo with simplified 3D. Cesium's energy reference (Gilytics Pathfinder) is schematic, not photoreal. Planning tools render parametric masses on real terrain and imagery; engineering tools render vendor-accurate equipment. This feature is a planning tool.

### Standards

glTF 2.0 + OGC 3D Tiles 1.1 (2.0 in public comment July 2026, backward-compatible) is the de-facto web 3D geospatial interchange and what Bentley, Cesium, Google and Esri all speak — the export format. CityJSON 2.0 (OGC, CityGML 3.0 subset) is how German LoD2 and Dutch 3DBAG arrive — the import format for context buildings. IFC 4.3 (ISO 16739-1:2024) has electrical objects but no substation/plant-site domain; it is what an EPC's model would hand over later. CIM (IEC 61970/61968) CGMES Geographical Location gives `Location` + `PositionPoint` (x, y, optional z) for substations and lines, no geometry. **There is no de-facto standard for energy-asset site layout**; the right move is a simple internal JSON (asset id → PyPSA component ref, WGS84 anchor, heading, layout params) that maps to glTF/3D Tiles for viewing.

## 10. Effort and phasing (estimates, one engineer)

| Phase | Content | Effort | Proves |
|---|---|---|---|
| 0 — spike | Fourth `CanvasView`, lazily-loaded R3F canvas, flat ground textured with Esri imagery for a hard-coded bbox, parametric boxes for the assets at one bus, click → `PropertiesPanel` | 1–2 weeks | The integration; something to show |
| 1 — site + placement | Site entity and boundary on the Leaflet map, placement sidecar in the project bundle (with scenario copy, undo, lock), drag from palette into 3D creating PyPSA components, drei gizmos, backend site-context service (Terrarium terrain, Overture/Overpass footprints, cached per project) | 3–5 weeks | The product shape |
| 2 — assets + results | 14–18 parametric asset types driven by a data table, 5–8 curated hero GLBs, LOD, results overlay animation from `CanvasResultsContext` | 3–4 weeks | "Investment decision" use |
| 3 — optional | Google Photorealistic / Cesium ion context via `3d-tiles-renderer`; GLB/3D Tiles export; national LiDAR heights | 2–4 weeks each | Wow factor, hand-off |

A credible v1 (Phases 0–2) is roughly 2.5–4 engineer-months. These are estimates from the code read and the stack research, not measured; Phase 0 is the cheapest way to tighten them and to answer Q1–Q5 with something on screen.

## 11. Risks, ranked

1. **Region-dependent realism disappoints.** A site in a country without open LiDAR looks like boxes on a satellite photo. Mitigation: set the bar explicitly (Q4); offer Google tiles as a paid, online-only option, not the default.
2. **Placement becomes a modelled quantity by accident.** If positions start feeding cable lengths or fit checks without being versioned, copied and undone, the model and the scene diverge. Mitigation: decide Q6 before Phase 1; put the sidecar in the bundle with `layout.json`'s guarantees from day one.
3. **Licensing of the cached imagery texture.** Offline sites textured with Esri imagery breach the free tier. Mitigation: Q7/Q8; plain ground offline, or an AGOL subscription.
4. **Scope creep into "operations".** "Operate them" read as live control turns a planning view into a SCADA imitation. Mitigation: Q5; define "operate" as edit + animate results.
5. **Untestable canvas.** A 3D view written as one component with WebGL in the render path cannot be tested in jsdom and will rot like the map's localStorage layout. Mitigation: pure scene modules, `@react-three/test-renderer`, no WebGL in tests.
6. **Bundle and packaging weight.** Cesium's ~30 MB static assets or a non-lazy three.js chunk slows every launch, including for users who never open the 3D view. Mitigation: R3F, `React.lazy`, `manualChunks`.
