# Visual layers, plan 2 of 3 — the map view with real topology and real lengths

**Date:** 2026-10-06
**Parent:** `docs/superpowers/assessments/2026-10-06-campus-visual-layers-assessment.md` (§3, §7 and the owner decisions of 2026-10-06)
**Siblings:** plan 1 (abstract canvas), plan 3 (3D site view)
**Status:** plan, written before the code
**Target:** `master`, one PR per increment

## The goal

The map view (`pypsa-gui/frontend/src/pages/MapCanvas.tsx`) places buses on real coordinates and lets the user drag them, writing `bus.x`/`bus.y` back to the model and recomputing line lengths. What it cannot do is hold the *real* network: a cable that follows the road, a feeder that runs round the hall, a line whose length is what was actually laid. Routed geometry is a browser-local decoration, length is a straight chord or a typed number, and nothing can be imported. The owner's requirement is that the map represents the real topology, that it is draggable, that lengths follow it, and that it can be adopted from external sources. This plan makes geometry a first-class, persisted, validated part of the project, and it is the foundation plan 3 needs for "derived from the map" to mean something.

## What exists today (surveyed 2026-10-06)

- Coordinates: `bus.x` = longitude, `bus.y` = latitude; (0,0) and non-finite count as unplaced (`utils/geo.ts`, `backend/services/network_geometry.py:26-57`). `UnplacedBusesPanel` and a click-to-place mode handle the rest.
- Drag → `PUT /api/network/buses/{name}` with new x/y → `services/network_buses.update_bus` (`:35-75`) → `_recompute_lengths_for_bus` rewrites every touching **line's** length by haversine and returns impedance-rescale previews that `RescaleDialogHost` offers to apply. `POST /api/network/lines/recalculate_lengths` does the same for all lines. Links never get a length.
- Routing: `EditableLine` (`MapCanvas.tsx:382-570`) draws bus-to-bus chords through user waypoints stored **only** in `localStorage` (`pypsa-gui:map:line-waypoints:<project>`, `:107-143`), "PURELY VISUAL — never touch line.length" (`:134-136`). Asset bubbles are pixel offsets, also localStorage. Server persistence is "a deferred follow-up" (`:108-112`).
- Import: NetCDF, CSV, Excel, MATPOWER (`backend/routers/io.py:206-290`). No GeoJSON, KML, shapefile, OSM or geocoding anywhere.
- Templates: the hub templates (`backend/project_templates/eh_templates.py:106-108`, `:191-193`, `:271-273`) place buses at lon 2–3 / lat 0–0.6, so every template campus sits in the Gulf of Guinea instead of being unplaced.
- Tiles: Esri World Imagery, satellite and hybrid, no key; no OSM base.
- No create or connect tools, no legend, no minimap, no transformer overlay. Tests cover icons, geo helpers, placement queue and drops; nothing covers `EditableLine`, bus dragging or the map overlay.
- On the 3D branch (plan 3 S0): a backend Overpass client with caching (`services/site_context.py`), a WGS84 polygon drawn on this map (`useSiteDraw.ts`), and a `sites.json` sidecar following the `layout.json` pattern — all reusable here.

## Owner decisions this plan relies on (2026-10-06)

- Placement carries meaning: positions and routes are modelled quantities, not decoration.
- The three layers describe one campus; the map is where real coordinates and real routes live.

## Increments

### M1 — Map geometry becomes a project sidecar

- **`map_layout.json`** in the project directory, added to `_BUNDLE_FILES` (`routers/projects.py:89`) so it is carried by scenarios, snapshots, export, Save-As, Duplicate and Clone like `layout.json` and (after plan 3 S0) `sites.json`. Content: `{version: 1, routes: {"<kind>:<name>": {points: [[lng, lat], ...], source: 'user' | 'import' | 'osm'}}, bubbles: {...}}`. Routes hold **all** vertices including the ends, so a route survives a bus drag with its interior intact and only its end vertices snap.
- **Routes** `GET/PUT /api/projects/{name}/map_layout`, same validation posture as `/layout` (4 MB cap, 422 on shape, 409 on lock, degrade-to-empty on a corrupt file), and a `rename_component` hook so a renamed line keeps its route (the 3D branch's `site_service.rename_component` is the pattern).
- **Frontend store** `pages/mapLayoutStore.ts` mirroring `topologyLayoutStore.ts` (memory cache → 300 ms debounced PUT → flush on unmount/pagehide → localStorage fallback), with a **one-time migration** of the existing localStorage waypoints and bubble offsets into the document on first load of a project that has none on the server.
- **Tests.** Route round trip, bundle carry (scenario, snapshot, export/import, Save-As), rename, migration, lock 409.

### M2 — Lengths follow geometry, by consent

- **Length provenance per branch**, stored in `map_layout.json` beside the route (not in PyPSA, which has no column for it): `length_source: 'typed' | 'chord' | 'route'`. Today every line is implicitly `chord` after a drag and `typed` otherwise; the migration marks them so.
- **Route length** = the geodesic length of the route polyline (haversine per segment, pure, tested). A new service `services/network_geometry.route_length_km(points)` sits beside `_line_haversine_km`.
- **Project setting "Derive lengths from geometry"** (off by default, in Project info, stored in `metadata.json`). When on: a route edit, a bus drag, or an import rewrites `length` of the affected Lines **and Links** from the route when one exists, else from the chord; the existing impedance-rescale preview is offered exactly as it is today for a bus drag. When off: nothing is rewritten, and the map shows a **discrepancy badge** on any branch whose stored `length` differs from its geometry by more than 10 % or 100 m, with a one-click "Use geometry" per branch. The owner's "validated and compliant" is this badge plus the topology check in M5.
- **Links.** `_recompute_lengths_for_bus` gains a Link arm (Links carry `length` in PyPSA; only `n.lines` is touched today). Links are drawn with their length in the tooltip like lines.
- **Tests.** Route length against known geodesics; derive-on/derive-off behaviour on drag, on route edit and on import; discrepancy threshold; rescale preview still offered; the campus electrical draft (`gridspine/producers/campus.py`, which uses `length` and falls back to 1 km at zero) picks up a derived length end to end.

### M3 — Adopt real topology from outside

- **GeoJSON / KML import of routes.** `POST /api/projects/{name}/map_layout/import` accepts a FeatureCollection of LineStrings; each feature is matched to a branch by a `name` property, else by its endpoints snapping to two placed buses within a tolerance (default 50 m, a setting), else listed as unmatched. The response is a match report the UI shows before anything is written; the user confirms. Routes land in `map_layout.json` with `source: 'import'`; with M2's setting on, lengths follow.
- **GeoJSON import of site polygons** (plan 3's `Site.boundary`) through the same endpoint, Polygon features → sites, so a surveyed plot boundary can be adopted instead of drawn.
- **OSM suggestions.** Reusing the 3D branch's Overpass client: for a bounding box around the placed buses, fetch `power=line|minor_line|cable` ways and `power=substation` areas and show them as a dimmed **suggestion layer**; clicking a suggested way beside a chord offers "Use as route for <line>". The public Overpass instance rate-limits; the endpoint setting the branch introduced (`PYPSAGUI_OVERPASS_URL`) is reused and the UI names it on a 429/504. ODbL attribution is shown whenever the layer is on.
- **Address placement.** The click-to-place mode gains an address box backed by a configurable geocoder endpoint (`PYPSAGUI_GEOCODER_URL`, default none → box hidden). The public Nominatim usage policy forbids heavy automated use, so the default is *off* and the setting names what to point at. Not a blocker for anything else.
- **Tests.** Matching by name, by endpoints, unmatched report; polygon → site; the suggestion layer against the recorded Overpass fixture the 3D branch ships (`backend/tests/fixtures/overpass_eemshaven.json`); attribution string present.

### M4 — Build on the map

- **Add bus at a click** (toolbar, mirrors the schematic's "Add bus" but writes x/y at the click instead of 0,0).
- **Connect mode shared with the schematic**: the same `NewConnectionDialog` from two bus clicks on the map; the new line's length defaults to the chord (M2) rather than `'1'`.
- **Palette drop with a coordinate.** `hooks/useAssetDrag.ts` gives the map branch a `position` (reverting spec D26): a drop on a bus still prefills the terminal; a drop on empty map opens the creation form with the nearest placed bus prefilled **and**, when the dropped item is a bus, places it there. This is also the hit-test shape plan 3 S0 adds for the 3D canvas (`.site3d-canvas` → ground point), so the hook gets both branches in one change.
- **Templates start unplaced, honestly.** The hub templates' placeholder coordinates become (0,0) so the buses appear in `UnplacedBusesPanel` with a one-line prompt "Place your campus: pick the grid connection on the map", and the panel offers "Place all buses around this point" which lays them out 200 m apart around the first click. One template test asserts no bus of any template is placed at a real coordinate.

### M5 — Topology check, legend, minimap, coverage

- **Topology check** (a section in the existing Issues panel, computed client-side from the model and `map_layout.json`): unplaced buses; two buses within 5 m of each other; a transformer whose buses are more than 500 m apart; a branch whose stored length disagrees with its geometry (M2 badge); a route that leaves every site boundary it starts in (plan 3). Each row deep-links to the component. Thresholds are constants with tests.
- **Legend** (voltage tiers, carriers, asset types from the shared module of plan 1 A2) and a **minimap** (Leaflet overview) with the same fold rule as the schematic's (hidden under 640 px).
- **Transformer overlay** on the map lands with plan 1 A3's provider change; this increment only draws it.
- **Tests** for `EditableLine` (insert, move, remove, right-click clear, no length write when M2 is off), for a bus drag writing x/y and offering a rescale, and for the topology check rules.

## Acceptance

1. A routed line survives reload, scenario creation, export/import and Save-As, on another machine, for another user.
2. With "Derive lengths from geometry" on, bending a route changes the line's length and offers the rescale preview; with it off, nothing is written and the discrepancy badge appears.
3. A GeoJSON of surveyed cable routes imports with a match report; a surveyed plot polygon becomes a site.
4. A campus template opens with every bus listed as unplaced and places itself around one click.
5. A bus can be added and two buses connected on the map; the new line's length is the chord.
6. The topology check lists the five rule classes above with deep links.
7. The campus electrical draft reads derived lengths.
8. `tsc -b`, `vitest run`, backend suite green; the main chunk grows at most 5 kB gzipped per increment; `test_packaging_requirements.py` still passes (no new Python dependency; `shapely` and `pillow` arrive with plan 3 S0).

## Out of scope, ledgered

- Replacing Leaflet with MapLibre for 3D terrain or continuous zoom (owner decision 3 of 2026-09-28: portal).
- Automatic route finding along roads (a routing engine). Suggestions from OSM ways are offered; nothing is computed.
- Cadastral or land-registry parcel import beyond GeoJSON polygons.
- Impedance rewrite without consent; the rescale stays a preview the user accepts.

## Order and first step

M1 (one week) → M2 (one week) → M4 (one week) → M3 (one to two weeks) → M5 (three to five days). M1 should land right after plan 3 S0 so its bundle-carry change is made once, in the same `_BUNDLE_FILES` tuple that S0 touches.

First step: the bundle-carry test for `map_layout.json` across scenario, snapshot, export/import and Save-As, red; then the sidecar.
