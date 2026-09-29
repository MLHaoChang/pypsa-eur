# 3D site view — Phase 2 design (asset library, hero models, results in 3D)

**Date:** 2026-09-29
**Branch:** `claude/3d-site-visualization-gatc5z`
**Parent:** `docs/superpowers/assessments/2026-09-28-3d-site-view-feasibility.md` (§8a decisions 5 and 9; §10 row "2 — assets + results") and the Phase 1 design `docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md`, whose QA note (`docs/superpowers/notes/2026-09-29-3d-site-view-phase1-qa.md`) closes Phase 1.
**Status:** design — the plan that implements it is a separate document.

## 1. Goal

Phase 1 gave the product its shape: a site the user draws, opens in 3D, arranges and grows. Phase 2 makes it answer the **investment question** the assessment set for it: *what does this plan look like on this plot, and how does it run?*

1. **Every asset looks like what it is.** A table-driven library of asset types covers the palette and the carriers the app uses. Today an electrolyser added from the palette draws as a line bay, a pumped-hydro unit as battery containers, a heat load as a data hall.
2. **A few assets look real.** A handful of curated, freely licensed models (turbine, container, transformer, tank, PV table, chiller) replace boxes up close, with the parametric geometry kept as the far and fallback form.
3. **Solved results move.** With a fresh solve, the timeline that already floats over every canvas drives the 3D scene: storage fills and empties, turbines turn with their output, halls glow with their load, feeders and transformers colour with their loading and show which way power flows.

## 2. Decisions

Assessment §8a decisions 5 ("operate" = edit parameters + animate solved results; nothing live) and 9 (parametric geometry plus 5–8 curated CC0/CC-BY hero models; no product-faithful equipment) are inputs, not re-opened.

The decisions below are this design's. The owner has not been asked about them; each is the recommended answer to a question that would otherwise block the design, stated so it can be overturned in review. **"Owner check"** marks the ones most worth a second look.

| # | Question | Decision (recommended) | Why |
|---|---|---|---|
| E1 | Where do asset types live? | **One data table, `site3d/assetLibrary.ts`**: each entry = match rule + sizing rule + geometry *template* + land-take rule + zone + colour + label + summary. `layout.ts` becomes the packer and template interpreter; it no longer names asset types. Replaces Phase 1's D17 `assetRules.ts` (its 13 numbers move into the entries that use them). | Phase 1 put 13 numbers in a table and left the classification, most geometry, zones, colours and labels hard-coded; adding a type meant editing five places. |
| E2 | How is a component matched to a type? | **Class + carrier + port side**, first match wins, in table order; the last entry per class is its fallback. Link rules can test `bus2` (CHP). The component *name* is never used. | Names are user text; carriers are the model's own taxonomy (the app's carrier catalog). |
| E3 | Which types? | **17 asset types plus 4 infrastructure types** (§4.2), chosen from the palette, the carrier catalog and the results views: every palette item gets a type that looks like it. | The assessment's 14–18 asset types; the palette has 18 items and several map wrongly today. |
| E4 | Geometry vocabulary | Parts gain a **shape**: box (today), **cylinder** (tanks, towers, stacks, silos), and **hero** (a model reference with a box fallback). Templates stay parametric. | Tanks and towers as boxes read wrong; a cylinder is one more three.js primitive. |
| E5 | Draw performance | **One merged geometry per object** (all its box/cylinder parts, vertex-coloured), not one mesh per part. Selection, hover and the outside-the-boundary tint stay per object. The per-object part cap (120) stays for the packer's summary, not for the renderer. | A campus is ~40 objects × up to 120 parts = several thousand draw calls today. Merging keeps selection semantics (one object = one mesh) without instancing bookkeeping. |
| E6 | Sizes: installed or optimised? | **Installed (`p_nom`, `e_nom`, `s_nom`) by default; a "Sized: as built / optimised" switch** in the site overlay when a solve is fresh, which sizes extendable assets from `*_nom_opt`. **Owner check.** | "Investment decision" use wants to see what the optimiser built; the default must not change geometry behind the user's back after every solve. |
| E7 | Hero models: which and how many | **Five hero models, all CC0, from one pack** (Kenney *City Kit Industrial 2.0*): wind turbine, shipping container, PV table group, bullet tank, industrial hall. Bundled with the app as static files (no CDN), **no Draco/meshopt** (so no decoder to ship or fetch), ~83 kB gzipped in total. The transformer, switchgear and dry coolers stay parametric. Details §5. **Owner check** (low-poly style). | §8a Q9 asked for 5–8 CC0/CC-BY models; the only CC-BY transformer candidates cannot be downloaded without a Sketchfab account, and nothing acceptable exists for switchgear. One pack = one consistent style and no attribution burden. |
| E8 | Level of detail | **No distance LOD.** A hero is drawn **instanced**, one instance per unit (container, turbine, table, tank), one draw call per hero type per object; the packer's existing cap (≤ 120 units drawn per object, the rest summarised) bounds the instance count. The parametric form is drawn while the model loads and **whenever it fails to load**. | Measured sizes make LOD unnecessary: the worst case (120 containers × 402 triangles) is ~50k triangles in one call. The assessment's LOD line assumed heavier models. |
| E9 | Which results animate | Per asset, at the selected snapshot: **storage state of charge** (fill level), **output as a share of capacity** (generators, electrolysers, heat pumps, CHP), **load** (halls, offtakes), **loading and flow direction** (lines, transformers, DC links). Bus prices, voltages, curtailment and unit commitment are **not** drawn in Phase 2 (they stay in the Results tabs). | The four quantities a customer reads off a moving site; everything else is a table. |
| E10 | How each result is shown | Fill level = a translucent level plane inside tanks and a bar on container rows; output = emissive intensity plus **wind rotors turning** at a speed ∝ output; loading = the existing red/amber/green `loadingColor` bands **plus** the percentage in the label (never colour only); direction = chevrons on feeders and transformers pointing downstream. | Reuses the canvases' conventions (`loadingColor`, SoC bands) so the 3D view reads like the other two. |
| E11 | Timeline | **Reuse `SnapshotPicker` and `resultsSnapshotIdx`** (already shown over the 3D view). No second timeline. Values ease between snapshots over ~300 ms; **`prefers-reduced-motion` turns easing and rotor spin off**. | One time control for the whole app. |
| E12 | When results show | Only when the Eye toggle is on (`resultsOverlayEnabled`) **and** `/simulation/status` says `dispatch === 'fresh'` — the 3D view does not trust cached result chunks after an edit (§6.3). Otherwise the scene is exactly Phase 1's. | The canvas overlays keep showing cached chunks after an edit today; the 3D view must not copy that. |
| E13 | Transformer flows | Fetched from `/results/transformers` and keyed `Transformer:<name>`. | The existing canvases never fetch them and the schematic looks transformers up in the Lines map (a bug to not copy; fixing the schematic is out of scope, §3). |

**Rejected: instancing everything (E5).** Instanced meshes per part type would draw fastest, but selection, hover, per-object tint and the gizmo all work per object; with instancing each needs per-instance colour bookkeeping and a pick map. Merging per object gets the draw count from thousands to tens with none of that.

**Rejected: a 3D-only timeline.** A second scrubber would disagree with the schematic's.

## 3. Non-goals (this phase)

- Bus prices, voltages, curtailment, unit-commitment state, reactive power in 3D.
- Fixing the existing canvases' results gaps (transformer lookup in the schematic, cached chunks after an edit, no Link p1); recorded in §8 as follow-ups.
- Editing the asset table from the UI; per-project asset tables.
- Product-faithful equipment, manufacturer models, BIM/IFC.
- Photoreal context (Google 3D Tiles), Overture/LiDAR heights, glTF export — Phase 3.
- Moving multi-port Links (CHP) as one object across two sites; a CHP belongs to the site of its `bus0`.

## 4. Asset library

### 4.1 Entry shape

```ts
interface AssetType {
  id: string                         // 'bess', 'electrolyser', …
  label: string                      // legend text
  color: string                      // body colour; parts may override
  zone: 'yard' | 'west' | 'east' | 'northeast' | 'south' | 'roof'
  match: {
    cls: PyPSAClass                  // 'Generator' | 'StorageUnit' | 'Store' | 'Load' | 'Link' | 'Line' | 'Transformer' | 'Bus'
    carrier?: RegExp                 // tested against the carrier; absent = any
    side?: 'bus0' | 'bus1'           // the member bus must be this port (two-port Links)
    hasBus2?: boolean                // CHP-style three-port Links
  }
  size: SizeRule                     // which parameter, unit rating, minimum placeholder
  geometry: Template                 // one of the templates in §4.3, with its numbers
  land: 'footprint' | { haPerMW: number } | 'none'   // rooftop PV takes no land
  summary: (n: SizeInfo) => string   // "40 MWh in 10 containers"
  hero?: HeroRef                     // §5; absent = parametric only
}
```

Validation (`validateLibrary`) replaces `validateRules`: every number finite and > 0, every id unique, every class has a fallback entry (an entry with no `carrier`), templates known, hero refs known. The table is frozen; `buildSiteLayout(input, library = DEFAULT_LIBRARY)` takes it as an argument (no module-level mutable state; Phase 1 held the rules in a module global).

### 4.2 The types

| Type | Matches (class · carrier · side) | Sized by | Template | Land |
|---|---|---|---|---|
| Switchyard | Bus (AC/DC) | `v_nom`, bay count | yard | footprint |
| Non-electrical bus | Bus (H2, heat, gas) | — | manifold (pipe rack) | footprint |
| Transformer | Transformer | `s_nom` | transformer | footprint |
| Feeder / cable bay | Line; Link DC | `s_nom` / `p_nom` | bay | footprint |
| BESS | StorageUnit · battery; Store · battery | MWh | unitGrid (20 ft) + PCS skids | footprint |
| Flywheel | StorageUnit · flywheel | MW | unitGrid (small cylinders) | footprint |
| Pumped hydro | StorageUnit · hydro/PHS | MWh | reservoir (basin) + powerhouse | footprint |
| Compressed air | StorageUnit · air/CAES | MW | plant hall + cylinder receivers | footprint |
| H₂ storage | Store · H2; StorageUnit · H2 | MWh | tankArray (bullets, cylinders) | footprint |
| Thermal store | Store · heat | MWh | tankArray (vertical cylinders) | footprint |
| PV (ground) | Generator · solar | MWp | pvField | ha/MWp |
| PV (rooftop) | Generator · solar-rooftop | MWp | pvRoof (on the site's largest hall, else a canopy) | none |
| Wind | Generator · onwind/offwind/wind | MW | turbineArray | spacing envelope |
| Gas turbine | Generator · CCGT/OCGT | MW | turbine hall + HRSG + stack | footprint |
| Engine genset | Generator · gas/diesel/biomass/oil (fallback) | MW | unitGrid (40 ft) + stacks | footprint |
| Electrolyser | Link · electrolysis/H2 · side bus0 | MW | unitGrid (40 ft skids) + BoP building | footprint |
| Fuel cell | Link · fuel cell/H2 · side bus1 | MW | unitGrid (20 ft) | footprint |
| Heat pump / e-boiler | Link · heat-pump*/resistive* | MW | unitGrid (skids) + dry coolers | footprint |
| CHP | Link · gas/biogas · hasBus2 | MW | engine hall + stack + heat exchanger | footprint |
| Data hall | Load · AC; Link · datacenter | MW | hall + rooftop plant | footprint |
| Offtake | Load · H2/heat/gas | MW | skid | footprint |

The first four rows are infrastructure; the other 17 are asset types. Remaining carriers (nuclear, coal, lignite, ror, geothermal, wave, tidal) fall to the class fallback of their class — the engine genset for Generators, which is labelled with the carrier so it does not pretend to be a genset ("coal — generic plant block").

`H2` as a Link carrier is ambiguous (the palette uses it for both electrolysers and fuel cells). The **side** rule resolves it: a Link whose `bus0` is the electrical member and whose `bus1` carrier is H2 is an electrolyser; the reverse is a fuel cell. That needs the far bus's carrier, which the layout input gains (`busCarrier(name)`).

### 4.3 Templates

`unitGrid` (unit box/cylinder, per-row count, gap, extra part per row), `tankArray` (cylinders, axis horizontal or vertical), `turbineArray` (tower cylinder, nacelle, three blades, rotor hub node for animation), `pvField` / `pvRoof` (tilted tables), `hall` (box + rooftop strip), `yard`, `bay`, `transformer`, `reservoir`, `manifold`, `composite` (a fixed list of parts sized from the driver). Every template returns parts in the object frame plus a footprint; the packer is unchanged.

Every template also declares its **animation anchors** (§6.4): a fill volume (tanks, containers), rotor nodes (wind), an emissive group (halls, skids) and a flow path (bays, transformers).

### 4.4 Invariants carried over

Phase 1's layout tests stay green unchanged in meaning (they may move files): exact part counts for BESS, the "each box = N" cap, PV land ≈ 2.5 ha/MWp, wind 3 × 5 MW = 15 parts, no footprint overlap within a bus, determinism, placements and orphans, the rules-drive-geometry test (now against a library entry). New: every palette item's default component maps to the type named in §4.2 (a table-driven test over the palette), every class has a fallback, and `layout.ts` contains no carrier regex.

## 5. Hero models

### 5.1 The set

Source: Kenney, *City Kit (Industrial) 2.0*, https://kenney.nl/assets/city-kit-industrial. Licence (verbatim, `License.txt`): *"Creative Commons Zero, CC0 … You can use this content for personal, educational, and commercial purposes. Support by crediting 'Kenney' or 'www.kenney.nl' (this is not a requirement)."* The app credits it anyway, in the attribution line, as "Models: Kenney (CC0)".

| Hero | File | Used by types | Triangles | Size (gz) | Notes |
|---|---|---|---|---|---|
| Wind turbine | `windmill.glb` | Wind | 456 | 12 kB | 2.31 units tall → scaled to the hub height; the `blades` node has its pivot at the hub (rotor animation, §6.4). |
| Container | `shipping-container-a.glb` | BESS, engine genset, electrolyser, fuel cell, heat pump skids | 402 | 8.6 kB | ≈ 20 ft proportions; 40 ft = scaled on the long axis; tinted per type. |
| PV table | `solar-panel-landscape-group.glb` | PV (ground, rooftop) | 976 | 17 kB | Tilted tables; one instance per table. |
| Bullet tank | `detail-tank.glb` | H₂ storage | 310 | 8 kB | Horizontal on saddles; scaled to the tank length. |
| Hall | `building-s.glb` | Data hall, electrolyser BoP building | ~1–2k | 10–28 kB | Long low shed; scaled to the hall footprint. |

Every file shares one 512² colour-map texture (12 kB). Measured by download and `gltf-transform inspect`; total ≈ 83 kB gzipped.

### 5.2 Processing and loading

- Files are processed once, offline, with `@gltf-transform/cli` and committed under `frontend/public/site3d/models/`: `optimize --compress false --texture-compress webp --join-named false --flatten false` (**not** meshopt: it rewrites node transforms and made the blade node wobble; **not** Draco: it would need a decoder shipped or fetched). The shared texture is kept as PNG if WebP cannot be confirmed in the packaged webview.
- A `models/README.md` records source URL, licence, version and the exact command, so a file can be regenerated.
- Loading: `useGLTF(url, false, false)` — drei's default fetches the Draco decoder from a Google CDN, which the offline desktop app must never do. Loaded under `<Suspense>` per object with the parametric form as the fallback; an `ErrorBoundary` keeps the parametric form on failure.
- Vite copies `public/` into `dist/`, which the desktop build already bundles; nothing in the JS chunks grows.

### 5.3 Rejected

- **CC-BY transformer models** (e.g. "Oil-immersed transformer (TMG)", iwan306, CC BY 4.0): downloadable only with a Sketchfab account, 73k faces before simplification, and they would carry an attribution and "modified" notice in the app. Revisit in Phase 3 if the parametric transformer reads poorly.
- **Poly Pizza**: its catalogue could not be reached from the build environment (Cloudflare challenge), so no licence could be verified.
- **Poly Haven air-conditioning unit** as a dry cooler: a split-AC unit, not a dry cooler, and 135 kB for one prop; the parametric fan box stays.

## 6. Results in 3D

### 6.1 Data

A new hook, `useSiteResults(project, site objects)`, reuses the chunked-series machinery of `CanvasResultsContext` (`useChunkedSeries`, exported for the purpose, same query keys, so the 3D view shares the cache with the other canvases) and builds **one map per snapshot**: `Map<"Class:name", AssetState>` for the site's objects only.

| Class | Series (endpoint) | State |
|---|---|---|
| Generator | `p` (`/results/generators`) | output MW, share of capacity |
| StorageUnit | `p` (`/results/storage_dispatch`), SoC (`/results/storage`) | charge/discharge MW, SoC share |
| Store | `p` (`/results/store_dispatch`), `e` (`/results/store_energy`) | MW, fill share |
| Load | `p` (`/results/loads`) | MW, share of the site peak |
| Link | `p0` (`/results/links`) | MW, share of `p_nom(_opt)`, direction |
| Line | `p0` (`/results/lines`) | MW, loading %, direction |
| Transformer | `p0` (`/results/transformers`) | MW, loading %, direction |

Capacity denominators: `*_nom_opt` when present, else `*_nom` (the same rule as the context's SoC). Loading uses `s_nom_opt`/`s_nom`, as the other canvases do (they ignore `s_max_pu`; so does this, and the label says "of rating").

### 6.2 Pure mapping

`site3d/resultStyle.ts` (no three, main-bundle-safe) turns an `AssetState` into a `Visual`: `{fill?: 0..1, emissive?: 0..1, spin?: rpm, flow?: {dir: 1|-1, pct}, color?: string, label: string}`. Everything the renderer does with results is decided here and unit-tested: SoC bands, loading bands, rotor speed from output (0 at cut-in, rated at 100 %), the text of the hover label ("BESS 1 · discharging 18.2 MW · SoC 64 %").

### 6.3 Freshness

The hook is enabled only when `resultsOverlayEnabled` and `status.dispatch === 'fresh'` (`/simulation/status`, already polled by `StatusBar`). A fresh → not-fresh transition drops the map immediately, before any cached chunk can be drawn.

### 6.4 Rendering

A `ResultsLayer` inside the Canvas reads the map for the current snapshot and drives each object's animation anchors in `useFrame`, easing towards the target over ~300 ms; the scene graph does not re-render per tick (anchors are refs). With reduced motion, targets apply instantly and rotors hold still (their speed shows in the label). The legend gains the loading and SoC bands when results are on.

## 7. Performance and bundle

- Objects: one merged mesh per object (E5); a campus of ~40 objects draws in ~40–60 calls plus context.
- Heroes: instanced per type; LOD distance per type.
- Bundle: nothing new in the main chunk except `resultStyle.ts` and the library table (both small; the main-chunk budget of +5 kB over the Phase 1 close applies again). three.js loaders stay in the `SiteCanvas` chunk. Model files are static assets, not in any JS chunk.

## 8. Follow-ups noticed (not in this phase)

- The schematic looks transformer flows up in the Lines map (`TopologyCanvas.tsx:931–933`).
- Result chunks are not invalidated after a network edit; the canvases can show stale values until a chunk boundary.
- No bulk `Link p1` endpoint (the context's comment names one that does not exist).
- `SnapshotPicker`: no `aria-label` on the slider, no `aria-live` timestamp, playback ignores reduced motion.
- `packaging`: `pypsa-gui.spec` lacks `gridspine.drivers.year_study` (pre-existing test failure).
