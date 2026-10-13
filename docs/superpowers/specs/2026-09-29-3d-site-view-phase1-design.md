# 3D site view — Phase 1 design (site entity, placement, site context)

**Date:** 2026-09-29
**Branch:** `claude/3d-site-visualization-gatc5z`
**Parent:** `docs/superpowers/assessments/2026-09-28-3d-site-view-feasibility.md` (decisions §8a) and the spike result `docs/superpowers/notes/2026-09-28-3d-site-view-spike.md`
**Status:** design — the plan that implements it is a separate document

## 1. Goal

Turn the spike's "one bus and its assets on a photo" into the product shape the owner chose: a **site** the user draws on the map, which groups one or more buses inside a boundary, opens in 3D on real terrain and building footprints, places its assets where the user puts them, accepts new assets dropped from the palette, and says whether the plan fits the plot. Everything the user does in the site view is persisted in the project bundle from day one.

## 2. Locked decisions

Decisions 1–11 in the assessment §8a are inputs and are not re-opened here. The decisions below are the ones this design adds.

| # | Decision | Choice |
|---|---|---|
| D1 | Where site data lives | A **`sites.json` sidecar** in the project directory, added to `_BUNDLE_FILES`, plus a **`sites/` directory** for cached context, added to `_BUNDLE_DIRS`. Not inside `n.meta`. |
| D2 | What a site is | `{id, name, buses[], boundary, origin, placements}`. The boundary is a WGS84 polygon; the origin is its centroid at creation and is **stored**, so later boundary edits do not move every placement. |
| D3 | Placement coordinates | Local **metres east/north of the site origin** plus a heading in degrees, keyed by `"<Class>:<name>"`. Not lat/lon: sub-metre placement precision, and the same frame the scene already uses. |
| D4 | Site membership | The buses whose coordinates fall inside the boundary at creation, editable afterwards (add/remove from a list). A component belongs to a site through its bus; there is no per-component membership. |
| D5 | Boundary drawing | Click-to-add-vertex on the existing Leaflet map, closed by double-click or Enter, mirroring `ClickToPlace`. **No `leaflet-draw` / `geoman` dependency.** |
| D6 | Entering the site | Portal, per §8a Q3: a site polygon on the map has an **Open in 3D** action that sets `canvasView = 'site'` and the active site. The 3D picker lists **sites**, not buses. A project with no site offers "Create a site around bus X", which writes a default rectangular boundary from the packed layout — so the spike's bus-only mode survives as an onboarding path, not a second mode. |
| D7 | Moving assets | Translate in the ground plane and rotate about the vertical, with drei's `PivotControls`. Writes are debounced and persisted exactly like the schematic layout (memory cache → 300 ms debounced PUT → flush on unmount/pagehide → localStorage fallback on failure). |
| D8 | Auto-layout vs placement | An asset **with** a placement renders there. Assets **without** one are auto-packed by the spike's zone algorithm around the primary bus's switchyard. **Arrange** writes the auto positions as placements so they become stable; **Reset placement** on one asset removes its entry. |
| D9 | Fit check (§8a Q1, Q6) | Two numbers on the status line: total asset land take (sum of `areaM2`) and plot area (boundary polygon). Over the plot → red. Additionally, any asset whose footprint corners leave the boundary is outlined red. Positions feed nothing else in v1. |
| D10 | Site context | Backend service, fetched on demand and **cached in `sites/<id>/context.json`**: building footprints with heights, and roads / fences / landuse / power features from **Overpass** (OSM); terrain from **AWS Terrarium tiles** decoded with Pillow. Overture and national LiDAR are Phase 2. **Imagery is never cached** (Esri free-tier terms). |
| D11 | Offline (§8a Q7) | Cached context renders offline on a plain hill-shaded ground; imagery is fetched live when reachable and simply absent when not. Nothing blocks. |
| D12 | Locks and read-only | Site routes live under `/api/projects/{name}/sites` and use the endpoint-level `_check_project_lock` like `/layout` (409 `project_locked`). The frontend **also** disables gizmos, drawing and drops when `readOnly` — the first canvas to do so; the backend 409 is the backstop, not the UX. |
| D13 | Undo | Placements and sites are **not** in the undo stack in v1 (positions are cosmetic, §8a Q6). Undo of a component delete restores the component; its placement was never removed (D14), so the object reappears where it was. |
| D14 | Orphans and renames | A placement whose component no longer exists is ignored at render and **pruned only on Arrange or site save from the UI**, never on read. The existing component rename routes call `site_service.rename_component(cls, old, new)` so a rename keeps its placement. |
| D15 | Sidecar carry gap | `_carry_sidecars_on_move` today copies only chat and uploads, so Save-As, Duplicate and the Clone wizard lose `layout.json`. This increment extends it to **every `_BUNDLE_FILES` sidecar and `_BUNDLE_DIRS`**, which fixes the pre-existing layout gap in passing. Scoped fix, called out in the plan as its own task. |
| D16 | Palette drops into 3D | `useAssetDrag` gains a fourth branch: `.site3d-canvas` → `canvas: 'site'`, position = ground-plane raycast at the drop point, exposed by the scene through a registry the way the schematic exposes `window.rfInstance`. The creation form pre-fills the terminal bus with the site's primary bus (selectable among the site's buses), and on success writes the placement at the drop point (`pendingPlacement`, the analogue of `pendingNodePosition`). |
| D17 | Parameter → geometry table | The constants in `site3d/layout.ts` move to `site3d/assetRules.ts` as one exported data object with a schema, so a product line or regional norm is a data change. No UI to edit it in v1. |

**Rejected: `n.meta`.** It would ride along with undo, save, clone and export for free. It was rejected because `n.optimize()` resets `n.meta` during myopic iterations (which already forced a thread-local side store for vintages), because it puts presentation state into the model file that gridspine and PowerFactory hand-offs read, and because undo of positions is not wanted in v1 anyway.

## 3. Non-goals (this phase)

- Results animation in 3D (state of charge, loading, flows) — Phase 2; `CanvasResultsContext` is the source.
- Curated hero models, LOD, Draco — Phase 2.
- Overture Maps, national LiDAR heights, CityJSON import, Google photoreal tiles — Phase 2–3.
- Cable lengths, land-use constraints or any model quantity derived from position.
- Continuous zoom from the map; replacing Leaflet.
- Editing the asset-rules table from the UI.
- Multi-site scenes; one site is open at a time.

## 4. Data

### 4.1 `sites.json`

```json
{
  "version": 1,
  "sites": [
    {
      "id": "0f3a…",                       
      "name": "Eemshaven campus",
      "buses": ["Campus 110kV", "Campus 33kV"],
      "boundary": [[6.8291, 53.4412], [6.8351, 53.4412], [6.8351, 53.4381], [6.8291, 53.4381]],
      "origin": { "lng": 6.8321, "lat": 53.4396 },
      "placements": {
        "Generator:Gas gensets": { "x": -142.0, "y": 18.5, "heading": 90 },
        "StorageUnit:BESS 1":    { "x": 96.0,  "y": -12.0, "heading": 0 }
      },
      "context_fetched_at": "2026-09-29T10:12:00Z"
    }
  ]
}
```

- `boundary`: ≥ 3 `[lng, lat]` vertices, implicitly closed, no self-intersection check in v1 (the map draw mode prevents it in practice).
- `origin`: stored, not derived (D2).
- `placements`: metres on the tangent plane at `origin`; `heading` degrees clockwise from north. Optional `layout` object reserved for per-asset layout parameters (row count, pitch); unused in v1.
- Validation on PUT: `version == 1`; ids unique and non-empty; every bus name a string; boundary shape; numeric placement fields finite; document ≤ 4 MB (same `_MAX_LAYOUT_BYTES` ceiling). Unknown keys are preserved, not rejected (forward compatibility, same posture as the opaque layout document).

### 4.2 `sites/<id>/context.json`

```json
{
  "version": 1,
  "source": "overpass",
  "fetched_at": "…",
  "bbox": [minLng, minLat, maxLng, maxLat],
  "buildings": [ { "id": 123, "polygon": [[lng, lat], …], "height_m": 8.0, "height_source": "tag|levels|landuse|default", "tags": {…} } ],
  "lines":     [ { "id": 456, "kind": "road|rail|fence|power_line", "points": [[lng, lat], …], "tags": {…} } ],
  "areas":     [ { "id": 789, "kind": "landuse|power_substation|water", "polygon": [[lng, lat], …], "tags": {…} } ],
  "terrain":   { "z": 12, "grid": 64, "bbox": [...], "heights_m": [ …4096 numbers… ], "source": "terrarium" },
  "attribution": ["© OpenStreetMap contributors (ODbL)", "Terrain: Mapzen/AWS Terrain Tiles"]
}
```

Height rule (D10): `height` tag → `building:levels` × 3.3 m → landuse default (industrial 8 m, retail/commercial 6 m, residential 6 m) → 5 m. `height_source` records which.

The bbox is the boundary's bbox padded to the larger of 150 m or 25 % of its extent, so the site has surroundings. Overpass is called once per fetch with one query covering buildings, `highway`, `railway`, `barrier`, `landuse`, `power`, `natural=water`. The public instance is the default; the endpoint is a setting (`PYPSAGUI_OVERPASS_URL`) so an organisation can point at its own.

### 4.3 What is carried where

| Operation | `sites.json` | `sites/` context | Mechanism |
|---|---|---|---|
| Save | untouched (written only by its own route) | untouched | as `layout.json` |
| Scenario create | copied | copied | `_BUNDLE_FILES` / `_BUNDLE_DIRS` loop |
| Export / import bundle | included | included | same loops |
| Snapshot create / restore | included | included | same loops |
| Save-As / Duplicate / Clone | **copied (new, D15)** | **copied (new, D15)** | `_carry_sidecars_on_move` |
| Rename project | moves with the directory | same | existing |
| Undo | not captured (D13) | n/a | — |

## 5. API

All under `/api/projects/{name}`, resolved through `_resolve_project_src`, ACL via `ensure_project_access`, lock via `_check_project_lock` on writes, 404 when the project directory is missing.

| Route | Body / result | Notes |
|---|---|---|
| `GET  /sites` | `SitesDocument` | `{version:1, sites:[]}` when the file is missing, corrupt or not a dict (same degrade rule as `/layout`); `PermissionError` → access-denied error, not an empty document. |
| `PUT  /sites` | `SitesDocument` → `{saved: true, sites: n}` | Validates §4.1; 413 over 4 MB; 422 on shape errors; atomic write. |
| `POST /sites/{site_id}/context` | → `SiteContext` | Fetches Overpass + Terrarium synchronously (FastAPI `def`, threadpool), caches, returns. 60 s overall timeout; 502 with the upstream error text on failure; 404 unknown site. |
| `GET  /sites/{site_id}/context` | → `SiteContext` | Cached copy or 404. |
| `DELETE /sites/{site_id}/context` | → 204 | Clears the cache; used by "Refresh context". |

Deleting a site is a `PUT /sites` without it; the route then removes `sites/<id>/` if present (the only side effect a PUT has).

`services/site_service.py` owns: read/write/validate of the document, `rename_component`, `prune_site_dirs`. `services/site_context.py` owns: bbox padding, the Overpass query text, element → footprint/line/area conversion, the height rule, Terrarium tile selection and decode, and the cache. Network I/O goes through `httpx` (already pinned), everything else is pure and unit-tested with recorded fixtures.

**Dependencies.** None new. `shapely` (present via pypsa) for polygon area, centroid and point-in-polygon on the backend where needed; Pillow (present) for Terrarium PNG decode. `duckdb`, `rasterio`, `requests` stay out. `tests/test_packaging_requirements.py` remains the guard.

## 6. Frontend

### 6.1 Modules

| Module | Role |
|---|---|
| `api/sites.ts` | client for §5 |
| `site3d/sitesStore.ts` | the site document's memory cache, debounced PUT, flush-on-unmount/pagehide, localStorage fallback, `flushPendingSitesToServer(project)` called beside `flushPendingLayoutToServer` at the three save sites. Mirrors `topologyLayoutStore.ts` deliberately; the two are not merged because their documents and consumers differ. |
| `site3d/boundary.ts` | pure: polygon area (shoelace on the tangent plane), centroid, point-in-polygon, `busesInside(buses, boundary)`, default rectangle from a layout's bounds |
| `site3d/assetRules.ts` | the parameter → geometry data table (D17) |
| `site3d/layout.ts` | as spiked, now taking the rules table and a placements map: placed objects keep their origin and heading, the rest are packed |
| `site3d/context.ts` | pure: GeoJSON-ish context → extruded building parts on the tangent plane, line strips, landuse patches; heightmap grid → `PlaneGeometry` displacement and `groundHeightAt(x, y)` |
| `site3d/scene.ts` | as spiked, plus `groundRaycast` helper and the drop registry |
| `pages/SiteCanvas.tsx` | site mode; placements; gizmos; drop target; fit check; out-of-boundary outline; context rendering; attribution for OSM and terrain beside Esri |
| `pages/MapCanvas.tsx` | site polygons (Leaflet `Polygon`), draw mode, site popover with Open in 3D / Edit buses / Delete |
| `hooks/useAssetDrag.ts` | fourth hit-test branch (D16) |
| `layout/CreationForm.tsx` | site-aware bus default; `pendingPlacement` on success |
| `store/uiStore.ts` | `activeSiteId`, `siteDrawMode`, `pendingPlacement` |

### 6.2 Flows

**Create a site.** Map view → "New site" (overlay button, hidden while a slide panel or the palette is open, same rule as the switcher) → click vertices → double-click/Enter closes → a small form: name (default "Site n"), member buses (pre-checked: buses inside) → PUT → polygon appears. Escape cancels. Fewer than three vertices → nothing is created, toast says so.

**Open in 3D.** Polygon click → popover → Open in 3D → `setActiveSite(id)`, `setCanvasView('site')`. The 3D view fits the camera to the boundary (not to the assets), draws the boundary as a ground outline, packs unplaced assets around the primary bus (first member) and renders placed ones where they are. Context is loaded from cache; if absent and the app is online, it is fetched once with a status line ("fetching site context…"); if offline, the ground is a hill-shaded plane from cached terrain or flat grey.

**Move an asset.** Select → gizmo → drag/rotate → `sitesStore.setPlacement(siteId, key, {x, y, heading})` → debounced PUT. Disabled when `readOnly`.

**Drop from the palette.** Palette drag → over the 3D canvas → drop → `resolveDrop` returns `{canvas:'site', ground:{x, y}}` → `CreationForm` opens with the site's buses in the bus field → create → placement written at the drop point → the new object appears there, selected.

**Fit check.** Status line: `land 50.3 ha · plot 32.0 ha` in red when land > plot; red outline on assets outside the boundary. Recomputed from the layout on every change, no persistence.

### 6.3 Read-only

`readOnly` (lock or solve in flight) disables: New site, vertex clicks, gizmos, drops, Arrange, Delete site. The visual state is the same dimmed treatment the header uses. Backend 409 stays as the backstop and remains a quiet toast.

## 7. Rendering (delta from the spike)

- Ground: `PlaneGeometry` subdivided to the terrain grid, displaced by `heights_m`; imagery draped unlit as spiked; shadow catcher on top; when no imagery, a hill-shade computed from the heightmap normals into a canvas texture.
- Buildings: one `ExtrudeGeometry`-equivalent per footprint (triangulated polygon extruded to `height_m`), merged into a single mesh per context for draw-call economy, unlit grey with slight per-building tint by height source. Not pickable.
- Lines: roads as flat ribbons, fences as thin walls, power lines as thin strips at 0.2 m; merged.
- Assets: as spiked; each sits at `groundHeightAt(origin)`.
- Boundary: a thin ribbon on the ground in the accent colour; outside-boundary assets get an emissive red edge.
- Attribution overlay carries all three sources.

## 8. Testing

Pure modules first, as in the spike: `boundary`, `assetRules`, `layout` (placed + packed mix, orphan handling), `context` (extrusion, heightmap, `groundHeightAt`), `sitesStore` (debounce, flush, fallback, rename). Backend: route tests for GET degrade rules, PUT validation and 413, lock 409, context fetch against a mocked `httpx` transport with a recorded Overpass response and a synthetic Terrarium tile, scenario copy and snapshot carrying `sites.json` + `sites/`, `_carry_sidecars_on_move` copying `layout.json` and `sites.json` on Save-As (the D15 regression test). Component tests mount the map's draw mode and the creation form's site-aware bus field in jsdom; nothing mounts WebGL. One headless browser check reads the canvas via `toDataURL`, as the spike note warns, and is a runbook step, not a CI test.

## 9. Acceptance criteria

1. A site drawn on the map appears as a polygon, survives reload, scenario creation, export/import, snapshot restore, Save-As, Duplicate and Clone.
2. Open in 3D shows the site's assets on cached or freshly fetched context; with the network unreachable it still opens on a plain or hill-shaded ground and says why.
3. Dragging an asset persists its position; reload shows it there; a scenario made afterwards has the same positions; the base project does not change when the scenario's positions do.
4. A palette drop onto the 3D ground creates the component on a site bus and places it at the drop point.
5. The status line reports land take versus plot area; an asset dragged outside the boundary is outlined red; nothing in the model changes.
6. Renaming a component keeps its placement; deleting one then undoing restores it in place.
7. With the project locked by another user or a solve in flight, nothing in the site view can be moved, drawn or dropped, and no 409 toast appears.
8. No new Python dependency; `tests/test_packaging_requirements.py` passes; Esri imagery is never written to disk.

## 10. Open items for the plan

- The Overpass public instance rate-limits; the plan should include a 429/504 message that names the setting for a private endpoint.
- `PivotControls` and the shadow catcher have not been exercised on a real GPU; the runbook step in §8 should run on the packaged macOS build once.
- Whether "Create a site around bus X" should also run Arrange immediately (so the first open already has placements) is a UX call for the plan; the design allows either.
