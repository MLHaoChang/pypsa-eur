# 3D site view — Phase 2 design (asset library, hero models, results in 3D)

**Date:** 2026-09-29
**Branch:** `claude/3d-site-visualization-gatc5z`
**Parent:** `docs/superpowers/assessments/2026-09-28-3d-site-view-feasibility.md` (§8a decisions 5 and 9; §10 row "2 — assets + results") and the Phase 1 design `docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md`, whose QA note (`docs/superpowers/notes/2026-09-29-3d-site-view-phase1-qa.md`) closes Phase 1.
**Status:** design, revised after the two plan reviews (v2 of the plan lists the findings) — the plan that implements it is a separate document.

## 1. Goal

Phase 1 gave the product its shape: a site the user draws, opens in 3D, arranges and grows. Phase 2 makes it answer the **investment question** the assessment set for it: *what does this plan look like on this plot, and how does it run?*

1. **Every asset looks like what it is.** A table-driven library of asset types covers the palette and the carriers the app uses. Today an electrolyser added from the palette draws as a line bay, a pumped-hydro unit as battery containers, a heat load as a data hall.
2. **A few assets look real.** Five curated public-domain models (wind turbine, container, bullet tank, PV table, hall) replace the parametric boxes for those units, with the parametric geometry kept as the loading and fallback form.
3. **Solved results move.** With a fresh solve, the timeline that already floats over every canvas drives the 3D scene: storage fills and empties, turbines turn with their output, halls glow with their load, feeders and transformers colour with their loading and show which way power flows.

## 2. Decisions

Assessment §8a decisions 5 ("operate" = edit parameters + animate solved results; nothing live) and 9 (parametric geometry plus 5–8 curated CC0/CC-BY hero models; no product-faithful equipment) are inputs, not re-opened.

The decisions below are this design's. The owner has not been asked about them; each is the recommended answer to a question that would otherwise block the design, stated so it can be overturned in review. **"Owner check"** marks the ones most worth a second look.

| # | Question | Decision (recommended) | Why |
|---|---|---|---|
| E1 | Where do asset types live? | **One data table, `site3d/assetLibrary.ts`**: each entry = match rule + sizing rule + geometry *template* + land-take rule + zone + colour + label + summary. `layout.ts` becomes the packer and template interpreter; it no longer names asset types. Replaces Phase 1's D17 `assetRules.ts` (its 13 numbers move into the entries that use them). | Phase 1 put 13 numbers in a table and left the classification, most geometry, zones, colours and labels hard-coded; adding a type meant editing five places. |
| E2 | How is a component matched to a type? | Each type lists one or more **match rules** `{cls, carrier?, farCarrier?, hasBus2?, port?}`; the first matching type in table order wins; every class has exactly one **fallback** rule (no carrier). `farCarrier` tests the carrier of the port that is *not* the electrical one, which separates an electrolyser (AC → H2) from a fuel cell (H2 → AC). The **owner** (the member bus the object is drawn from) is chosen **after** matching: a rule that names a `port` (where the equipment stands) matches only when that bus is a member, and the owner is that bus; otherwise the component falls through to the next rule, and the owner of a portless rule is the first member among bus0, bus1, bus2. So an electrolyser seen only from its H₂ side is a feeder bay to it (a Phase 1 pin), and a CHP whose only member is its heat bus is a feeder bay too. Carrier patterns share the creation form's bus-carrier families (`utils/busCarriers.ts`) and have no `g`/`y` flag. The component *name* is never used. | Names are user text; carriers are the model's own taxonomy. Phase 1 chose the owner before knowing the type (bus0 first), which drew a fuel cell as an electrolyser at the H2 manifold. |
| E3 | Which types? | **17 asset types plus 4 infrastructure types** (§4.2), chosen from the palette, the carrier catalog and the results views: every palette item gets a type that looks like it. | The assessment's 14–18 asset types; the palette has 18 items and several map wrongly today. |
| E4 | Geometry vocabulary | Parts gain a **shape**: box (today) or **cylinder** (tanks, towers, stacks), plus an **axis** for cylinders (`up`, `east`, `north`). `size` keeps Phase 1's order (east, north, height): a vertical tank is `[d, d, h]`, a horizontal bullet along north is `[d, length, d]`. Units a hero can replace are flagged `heroable`. | Tanks and towers as boxes read wrong; keeping the size order means the packer's pitch and ground-height rules do not change. |
| E5 | Draw performance | **One merged geometry per object** (all its box/cylinder parts, vertex-coloured, indexed), not one mesh per part, **except** each turbine's rotor, which is its own mesh with its pivot at the hub (so it can spin). Selection, hover and the outside-the-boundary tint stay per object. The part cap (120 units drawn per object) now also applies to wind. | A campus is ~40 objects × up to 120 parts = several thousand draw calls today. Merging keeps selection semantics (one object = one mesh) without instancing bookkeeping. |
| E6 | Sizes: installed or optimised? | **Installed (`p_nom`, `e_nom`, `s_nom`) by default; a "Sized: as built / optimised" switch** in the site overlay when a solve is fresh, which sizes extendable assets from `*_nom_opt`. The effective mode is optimised **only while** dispatch is fresh; after an edit the scene falls back to installed sizes even if the switch was left on. **Owner check.** | "Investment decision" use wants to see what the optimiser built; the default must not change geometry behind the user's back after every solve. |
| E7 | Hero models: which and how many | **Five hero models, all CC0, from one pack** (Kenney *City Kit Industrial 2.0*): wind turbine, shipping container, PV table group, bullet tank, industrial hall. Bundled with the app as static files (no CDN), **no Draco/meshopt** (so no decoder to ship or fetch), ~59 kB gzipped in total (measured after processing). The transformer, switchgear and dry coolers stay parametric. Details §5. **Owner check** (low-poly style). | §8a Q9 asked for 5–8 CC0/CC-BY models; the only CC-BY transformer candidates cannot be downloaded without a Sketchfab account, and nothing acceptable exists for switchgear. One pack = one consistent style and no attribution burden. |
| E8 | Level of detail | **No distance LOD.** A hero is drawn **instanced**, one instance per unit, with the model's own node transforms baked in, a per-hero base rotation onto the site axes, and a per-template **fit mode**: uniform scale with the hub on the parametric hub (turbine; blades scaled about the hub to the library's rotor diameter), per-axis box fit (container, tank — *amended at the WP3 gate: a uniform scale cannot make the Kenney tank a 3 × 20 m bullet*), uniform scale plus tiling (PV tables, halls). The turbine's blades are a second instanced mesh so each hub can spin. Hero materials are cloned per object and **coloured with the type's tint, the texture kept as shading** (a red Kenney container reads as a violet battery), so tint and selection never leak between objects. The packer's cap bounds the units and **at most 240 instances per object** bound the tiles (past it each tile stretches along its row); PV tables cast no shadow. The parametric form is drawn while the model loads and whenever it fails (a failed model is tried again when a site opens). | Measured sizes make LOD unnecessary: the worst cases are 120 containers × 402 triangles (~50k) and 240 PV tiles × 976 triangles (~234k, no shadow pass) in one call each. *(WP3 gate: untiled-capped, a large PV field drew ~900 tiles, 1.87M triangles with shadows.)* The assessment's LOD line assumed heavier models. |
| E9 | Which results animate | Per asset, at the selected snapshot: **storage state of charge** (fill level), **output as a share of capacity** (generators, electrolysers, heat pumps, CHP), **load** (halls, offtakes), **loading and flow direction** (lines, transformers, DC links). Bus prices, voltages, curtailment and unit commitment are **not** drawn in Phase 2 (they stay in the Results tabs). | The four quantities a customer reads off a moving site; everything else is a table. |
| E10 | How each result is shown | Fill level = a translucent level plane inside tanks and a bar on container rows; output = emissive intensity plus **wind rotors turning** at a speed ∝ output; loading = the existing red/amber/green `loadingColor` bands **plus** the percentage in the label (never colour only); direction = chevrons on feeders and transformers pointing downstream. | Reuses the canvases' conventions (`loadingColor`, SoC bands) so the 3D view reads like the other two. |
| E11 | Timeline | **Reuse `SnapshotPicker` and `resultsSnapshotIdx`** (already shown over the 3D view). No second timeline. Values ease between snapshots over ~300 ms; **`prefers-reduced-motion` turns easing and rotor spin off**. | One time control for the whole app. |
| E12 | When results show | Only when the Eye toggle is on (`resultsOverlayEnabled`) **and** the 3D view's **own** status poll says `dispatch === 'fresh'` **and** each chunk was fetched after the latest transition to fresh (§6.3). Otherwise the scene is exactly Phase 1's. | Nothing in the app polls the status reliably (the status bar polls only after an in-session solve), and the canvases keep showing cached chunks after an edit and briefly after a re-solve; the 3D view must not copy either. |
| E13 | Transformer flows | Fetched from `/results/transformers` and keyed `Transformer:<name>`. | The existing canvases never fetch them and the schematic looks transformers up in the Lines map (a bug to not copy; fixing the schematic is out of scope, §3). |

**Rejected: instancing everything (E5).** Instanced meshes per part type would draw fastest, but selection, hover, per-object tint and the gizmo all work per object; with instancing each needs per-instance colour bookkeeping and a pick map. Merging per object gets the draw count from thousands to tens with none of that.

**Rejected: a 3D-only timeline.** A second scrubber would disagree with the schematic's.

## 3. Non-goals (this phase)

- Bus prices, voltages, curtailment, unit-commitment state, reactive power in 3D.
- Fixing the existing canvases' results gaps (transformer lookup in the schematic, cached chunks after an edit, no Link p1); recorded in §8 as follow-ups.
- Editing the asset table from the UI; per-project asset tables.
- Product-faithful equipment, manufacturer models, BIM/IFC.
- Photoreal context (Google 3D Tiles), Overture/LiDAR heights, glTF export — Phase 3.
- Drawing a multi-port Link (CHP) in two sites; it is drawn once, from the owner chosen by E2.

## 4. Asset library

### 4.1 Entry shape

```ts
interface Match {
  cls: PyPSAClass                    // 'Generator' | 'StorageUnit' | 'Store' | 'Load' | 'Link' | 'Line' | 'Transformer' | 'Bus'
  carrier?: RegExp                   // the component's carrier (Bus: the bus's); absent = the class fallback
  farCarrier?: RegExp                // Links: the carrier of the non-electrical port (H2 for an electrolyser's bus1)
  hasBus2?: boolean                  // three-port Links (CHP); an empty-string bus2 counts as absent
  port?: 'bus0' | 'bus1' | 'bus2'    // the port the object is drawn from when that bus is a member
}
interface AssetType {
  id: string                         // Phase 1 ids kept where the type is unchanged (switchyard, transformer, feeder, bess, pv, wind, electrolyser, h2store, load)
  label: string | ((carrier: string) => string)   // legend text; the Generator fallback names the carrier
  color: string                      // body colour; parts may override
  zone: 'yard' | 'west' | 'east' | 'northeast' | 'south' | 'roof'
  match: Match[]                     // one or more; a type may span classes (BESS: StorageUnit and Store)
  size: SizeRule                     // which parameter, unit rating, minimum placeholder
  geometry: Template                 // one of the templates in §4.3, with its numbers
  land: 'footprint' | { haPerMW: number } | 'none'   // rooftop PV takes no land
  summary: (n: SizeInfo) => string   // "40 MWh in 10 containers"
  hero?: HeroRef                     // §5; absent = parametric only
  flags?: { bay?: boolean; infrastructure?: boolean }   // bays widen the yard; infrastructure is not counted as an asset
}
```

Validation (`validateLibrary`) replaces `validateRules`: every number finite and > 0, every id unique, every class has exactly one fallback rule (a rule with no `carrier`) and it belongs to the last type matching that class, no regex carries a `g`/`y` flag, templates known, hero refs known. The table is frozen; `buildSiteLayout(input, library = DEFAULT_LIBRARY)` takes it as an argument (no module-level mutable state; Phase 1 held the rules in a module global).

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

`H2` as a Link carrier is ambiguous (the palette uses it for both electrolysers and fuel cells). `farCarrier` resolves it: an H2 Link whose bus1 is an H2 bus is an electrolyser (drawn from bus0, the electrical side); one whose bus0 is an H2 bus is a fuel cell (drawn from bus1, the electrical side). That needs every bus's carrier, which the layout input gains (`busCarrier(name)`, built from all network buses; an unknown carrier counts as AC). A type whose named port is not a member does not match; the component is drawn once, by a later rule (usually as a feeder bay), from its first member port. A CHP stands at its electrical port (bus1), with the electrical plant.

**Rooftop PV** is placed by a site-wide pass after placements are applied: it takes the final origin of the largest data hall in the site (any member bus), follows it when the hall moves, fills the free southern part of the roof (clear of the rooftop plant; beyond that each drawn table stands for several), and is exempt from the no-overlap rule (it is on the roof). It has no placement of its own: Arrange skips it and it has no gizmo. Without a hall — or when not one table fits the roof — it stands on a 4 m canopy (in the south zone, or just south of the hall).

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

After processing, each file embeds its own 2.6 kB WebP copy of the pack's colour map (`EXT_texture_webp`, required); the five files total 192 kB raw, 59 kB gzipped (measured). WebP is decoded by Chromium and by WKWebView on macOS 14, the packaged app's minimum (`pypsa-gui.spec`).

The models do not share the site's axes or scale: the container's root carries a 0.27 scale and runs along glTF Z; the tank's root is mirrored (`scale [-1, 1, 1]`) and runs along X; the turbine's hub is at 1.676 units, not at its 2.31 total height, and its rotor axis is X. The manifest records, per hero, the baked node matrices, a base rotation onto (east, north, up), the hub pivot and rotor axis, and the fit mode (E8).

### 5.2 Processing and loading

- Files are processed once, offline, with `@gltf-transform/cli` and committed under `frontend/public/site3d/models/`: `optimize --compress false --texture-compress webp --join-named false --flatten false` (**not** meshopt: it rewrites node transforms and made the blade node wobble; **not** Draco: it would need a decoder shipped or fetched).
- A `models/README.md` records source URL, licence, version and the exact command, so a file can be regenerated.
- Loading: `useGLTF(url, false, false)` — drei's default fetches the Draco decoder from a Google CDN, which the offline desktop app must never do. Loaded under `<Suspense>` per object with the parametric form as the fallback; a small scene-graph error boundary (the app's DOM `ErrorBoundary` cannot render inside the Canvas) keeps the parametric form on failure.
- Vite copies `public/` into `dist/`, which the desktop build already bundles; nothing in the JS chunks grows.

### 5.3 Rejected

- **CC-BY transformer models** (e.g. "Oil-immersed transformer (TMG)", iwan306, CC BY 4.0): downloadable only with a Sketchfab account, 73k faces before simplification, and they would carry an attribution and "modified" notice in the app. Revisit in Phase 3 if the parametric transformer reads poorly.
- **Poly Pizza**: its catalogue could not be reached from the build environment (Cloudflare challenge), so no licence could be verified.
- **Poly Haven air-conditioning unit** as a dry cooler: a split-AC unit, not a dry cooler, and 135 kB for one prop; the parametric fan box stays.

## 6. Results in 3D

### 6.1 Data

A new hook, `useSiteResults(project, objects, components)`, takes the site's objects **and** the component lists SiteCanvas already fetched (capacities, buses, `max_hours` live there, not on `SiteObject`). It reuses the chunked-series machinery of `CanvasResultsContext` (`useChunkedSeries`, exported with an additive variant that also reports when a chunk was fetched; same query keys, so the 3D view shares the cache with the other canvases) and builds **one map per snapshot**: `Map<"Class:name", AssetState>` for the site's objects only. It runs in a leaf component inside the Canvas and writes into a ref, so a snapshot step re-renders that leaf only.

| Class | Series (endpoint) | State |
|---|---|---|
| Generator | `p` (`/results/generators`) | output MW, share of capacity |
| StorageUnit | `p` (`/results/storage_dispatch`), SoC (`/results/storage`) | charge/discharge MW, SoC share |
| Store | `p` (`/results/store_dispatch`), `e` (`/results/store_energy`) | MW, fill share |
| Load | `p` (`/results/loads`) | MW, share of the site peak |
| Link | `p0` (`/results/links`) | MW, share of `p_nom(_opt)`, direction |
| Line | `p0` (`/results/lines`) | MW, loading %, direction |
| Transformer | `p0` (`/results/transformers`) | MW, loading %, direction |

Capacity denominators come from one helper: `*_nom_opt` if finite and > 0, else `*_nom` if > 0, else no share (the label shows MW only). In multi-period runs the capacity is the **period-effective** one (the context's vintage rule, moved into a shared pure module). A load's share is of its **peak**: a profile load's profile peak (load-profile metadata) × the largest factor the solver applies to it over the network's investment periods (per carrier bucket, then the legacy map; multi-period runs only — backend `load_scale_factors`), a static load's |p_set| unscaled; clamped to 0–1, and the label always shows MW. *(Amended at the WP5 gate: the first draft scaled every load by the largest scaler of any carrier.)* Link values are **MW in** at bus0 (there is no bulk p1 series). Loading uses `s_nom_opt`/`s_nom`, as the other canvases do (they ignore `s_max_pu`; so does this, and the label says "of rating"). Every share's label names its capacity ("SoC 64 % of 160 MWh"), because the drawn size (E6) and the denominator can differ.

### 6.2 Pure mapping

`site3d/resultStyle.ts` (no three, main-bundle-safe) turns an `AssetState` into a `Visual`: `{fill?: 0..1, emissive?: 0..1, spin?: rpm, flow?: {dir: 1|-1, pct}, color?: string, label: string}`. Everything the renderer does with results is decided here and unit-tested: SoC bands, loading bands, rotor speed from output (0 at cut-in, rated at 100 %), the text of the hover label ("BESS 1 · discharging 18.2 MW · SoC 64 %").

### 6.3 Freshness

Three rules, because no existing observer is reliable (the status bar polls `/simulation/status` only after an in-session solve; component edits do not invalidate the status; result chunks are not invalidated by edits):

1. The hook **owns a status poll** (same query key as the status bar, every 3 s while the Eye is on), independent of the simulation store's state. Its data never triggers the status bar's toast.
2. It **drops the map** as soon as any component list the site reads refetches after the map was built (an edit), without waiting for the poll. Placement writes go to the sites sidecar and do not count.
3. It **rejects a chunk fetched before the latest transition to fresh** (a re-solve), so the previous solve's values are never shown as current.

*Amended at the WP5 gate.* "Fresh" is per solve (objective and solve time), so fresh A → fresh B with no poll in between counts. On each fresh solve the view refetches the status and the component lists it reads and records their data; it is **current** only while the lists still hold that data (an edit — a list that refetches with new data — ends it at once; an identical refetch does not). The first solve seen as the view opens is trusted at once only when the cached status answer is recent (another view is polling it), so a warm cache opens filled; otherwise it waits for that confirmation. A chunk, or the vintage breakdown, fetched before the latest solve or invalidated by the finished job is never shown; a stale one is refetched once per solve. A series that answers nothing drops only its class; the last map is held only for the same solve and source.

### 6.4 Rendering

A `ResultsLayer` inside the Canvas reads the map for the current snapshot and drives each object's anchors in `useFrame`, easing towards the target over ~300 ms; SiteCanvas and the object meshes do not re-render per snapshot (selector subscriptions, memoised meshes; the driver leaf is the only thing that renders per snapshot, nothing renders per frame). With reduced motion, targets apply instantly and rotors hold still (their speed shows in the label).

- **Fill** is an exterior **gauge** (a bar beside the tank array or container row, its own material), not a plane inside an opaque body.
- **Glow** uses the emissive channel with a fixed precedence: selected > hovered > outside the boundary > results (hover is transient and its label says "outside the boundary").
- **Spin**: each turbine's rotor mesh (parametric) or blade instance (hero) rotates about its own hub.
- **Flow**: `SiteObject` gains `bus` (its owner) and `far` (the other end); the flow anchor is a segment from the owner's yard side to the far side; chevrons point downstream: `sign(p0) × (bus is bus0 ? 1 : −1)`, and the label names the destination bus.
- **Not colour-only, and not hover-only**: while results are on, the overlay lists the site's objects with their current value (a compact readout), the legend prints the band thresholds, and the timeline's slider gets an accessible name and value text.

## 7. Performance and bundle

- Objects: one merged mesh per object (E5); a campus of ~40 objects draws in ~40–60 calls plus context.
- Heroes: instanced per type (no distance LOD, E8); blades a second instanced mesh.
- Bundle: nothing new in the main chunk except `resultStyle.ts` and the library table (both small; the main-chunk budget of +5 kB over the Phase 1 close applies again). three.js loaders stay in the `SiteCanvas` chunk. Model files are static assets, not in any JS chunk.

## 8. Follow-ups noticed (not in this phase)

- The schematic looks transformer flows up in the Lines map (`TopologyCanvas.tsx:931–933`).
- Result chunks are not invalidated after a network edit; the canvases can show stale values until a chunk boundary.
- No bulk `Link p1` endpoint (the context's comment names one that does not exist).
- `SnapshotPicker`: no `aria-live` timestamp, and playback ignores reduced motion (the slider's `aria-label` / `aria-valuetext` were added in WP6).
- `packaging`: `pypsa-gui.spec` lacks `gridspine.drivers.year_study` (pre-existing test failure).
- **Creating a three-port Link drops `bus2`/`efficiency2`** unless a Link in the network already has a `bus2` column: `network_crud._drop_unknown_extras` keeps only catalog input attributes, and PyPSA's catalog does not list the multi-port fields. The palette's CHP item therefore creates a plain gas → electricity link with no heat output (found by the Phase 2 WP1 smoke; the 3D view correctly draws what the model holds). Fix: allow `bus\d+` / `efficiency\d+` in the filter.
- The period-effective capacity (the context's vintage rule, shared with the 3D view in `site3d/capacity.ts`) ignores a vintage's `lifetime`, so a retired vintage still counts (WP5 gate).
- The vintage breakdown is not invalidated by a finished solve (`useJobTerminalInvalidation` covers results, status, meta and the bundle); the 3D view refetches it itself, the schematic's asset-group capacities do not.
- A component whose name contains "/" cannot be updated: `PUT /api/network/transformers/TR2%20110%2F33` answers 405 (the encoded slash splits the route), and the app's `updateTransformer` encodes names the same way (found at the WP6 gate).
- `tests/test_chat_sse.py::test_invalid_decision_returns_400_and_preserves_token` fails only in the full backend run (409 on the retry; passes alone and with the chat modules): order-dependent state from another module (Phase 2 QA).
