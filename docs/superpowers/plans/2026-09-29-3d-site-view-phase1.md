# 3D site view — Phase 1 implementation plan

> **For agentic workers:** implement task-by-task, test first. Every task states its red tests before its green step; a task whose red step passes before any code is written is a plan defect and must be reported, not "fixed" by weakening the test. Each work package ends with a review gate and an integration commit; the gate is a separate agent that has not seen the implementation being written.

**Goal:** Ship the Phase 1 design (`docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md`): a site the user draws on the map, persisted in the project bundle, opened in 3D on cached site context, with placeable assets, palette drops, and a fit check.

**Architecture:** Five work packages (WP1–WP5) in dependency order. Backend pure services (`site_service.py`, `site_context.py`) under `pypsa-gui/backend/services/`, routes under `/api/projects/{name}/sites` in `routers/projects.py` (same file as `/layout`, same helpers). Frontend pure modules under `pypsa-gui/frontend/src/site3d/` carry every decision the canvas makes; `SiteCanvas.tsx` and `MapCanvas.tsx` compose them. No WebGL in any unit test.

**Tech stack:** FastAPI + pytest (`/root/.venv-pypsa-gui/bin/python -m pytest` from `pypsa-gui/backend`, or `pixi run gui-tests`); React 19 + TypeScript 5.8 strict + vitest 4 + jsdom (`npx vitest run` from `pypsa-gui/frontend`); three 0.186, @react-three/fiber 9.8, @react-three/drei 10.7 (already installed by the spike); shapely, Pillow, httpx already present in the backend environment.

**Base:** `ec42f19` on `claude/3d-site-visualization-gatc5z` (spike + spec).

---

## Plan set and process

| Stage | What | Gate |
|---|---|---|
| P0 | This plan, reviewed by two independent agents (backend/persistence; frontend/testability) before any code | findings folded into v2 of this file |
| WP1 | Sidecar, routes, bundle carry, frontend store | review agent A → fix → commit |
| WP2 | Boundary drawing, site creation, portal, fit check | review agent B → fix → commit |
| WP3 | Placements, gizmos, arrange, read-only gating | review agent → fix → commit |
| WP4 | Palette drops into 3D | review agent → fix → commit |
| WP5 | Site context service + rendering | review agent → fix → commit |
| QA | Headless end-to-end run + full suites + build; QA note | QA note in `docs/superpowers/notes/` |

**Per work package, in order:**

1. Red: write the listed tests; run them; every one fails for the stated reason.
2. Green: the smallest implementation that passes them.
3. Refactor with the suite green.
4. `tsc -b`, the touched vitest files, the touched pytest files, then the **full** frontend and backend suites.
5. Review gate: an agent that did not write the code reads the diff against the spec's decisions and this plan's tasks and reports defects with file:line. Every finding is fixed or answered in writing in the commit message.
6. Integration commit and push on the feature branch. One commit per work package (plus fix-ups from the gate squashed in before pushing is fine; the branch is ours).

**Never:** skip or weaken a test to get green; mount WebGL in vitest; write imagery to disk; add a Python dependency.

### Spec decision coverage

| Decision | Task(s) |
|---|---|
| D1 sidecar in bundle | 1.1, 1.2, 1.4 |
| D2 site shape, stored origin | 1.1, 2.2 |
| D3 placement frame | 1.1, 3.1 |
| D4 membership by bus | 2.1, 2.3 |
| D5 click-to-vertex drawing | 2.3 |
| D6 portal, picker lists sites, default site | 2.4, 2.5 |
| D7 gizmo moves, layout-style persistence | 1.5, 3.3 |
| D8 placed vs packed, Arrange, Reset | 3.1, 3.4 |
| D9 fit check | 2.6 |
| D10 Overpass + Terrarium context, cached | 5.1–5.4 |
| D11 offline degrade | 5.6 |
| D12 lock + readOnly gating | 1.3, 3.5 |
| D13 undo excluded | 1.2 (no undo prefix), test in 1.3 |
| D14 orphans, rename hook | 1.6, 3.1 |
| D15 sidecar carry on Save-As/Duplicate/Clone | 1.4 |
| D16 drops into 3D | 4.1–4.4 |
| D17 asset rules table | 3.2 |

---

## WP1 — `sites.json` sidecar, routes, bundle carry, frontend store

### Task 1.1 — `site_service.py`: the document, its validation, and its I/O

**Files:** `backend/services/site_service.py` (new), `backend/tests/test_site_service.py` (new).

**Red (tests written first):**

- `test_empty_document_when_missing` — `read_sites(dir)` on a directory with no file returns `{"version": 1, "sites": []}`.
- `test_corrupt_file_degrades_to_empty` — invalid JSON or a top-level list returns the empty document (the `/layout` rule).
- `test_permission_error_is_raised_not_swallowed` — a `PermissionError` on read propagates (so the route can turn it into access-denied, not a silent empty document).
- `test_validate_accepts_spec_example` — the §4.1 example document validates.
- `test_validate_rejects` (parametrised): wrong `version`; duplicate ids; empty id; non-string bus; boundary with two vertices; a vertex outside ±180/±90; a non-finite placement `x`; a placement key without a colon.
- `test_validate_preserves_unknown_keys` — an extra top-level key and an extra per-site key survive `validate → write → read`.
- `test_write_is_atomic_and_compact` — after `write_sites`, no temp file remains and the file parses; a document over `MAX_SITES_BYTES` (4 MB) raises `SitesTooLarge`.
- `test_rename_component_moves_placement_key` — `rename_component(doc, "Generator", "old", "new")` renames `Generator:old` → `Generator:new` in every site and leaves other classes alone; a missing key is a no-op.
- `test_prune_site_dirs_removes_only_orphans` — with `sites/a/`, `sites/b/` on disk and only `a` in the document, `prune_site_dirs(dir, doc)` deletes `b` and keeps `a`.

**Green:** dataclass-free, dict-in/dict-out functions; `validate_sites(doc) -> None | raises SitesInvalid(msg)`; constants `MAX_SITES_BYTES = 4 * 1024 * 1024`, `SITES_FILE = "sites.json"`, `SITES_DIR = "sites"`.

### Task 1.2 — Routes `GET/PUT /api/projects/{name}/sites`

**Files:** `backend/routers/projects.py`, `backend/tests/test_sites_routes.py` (new).

**Red:**

- `test_get_sites_missing_project_404`.
- `test_get_sites_empty_when_no_file` — 200 with the empty document.
- `test_put_then_get_round_trip` — PUT the spec example, GET returns it byte-equal in content.
- `test_put_invalid_422_with_reason` — the 422 body names the offending field.
- `test_put_over_limit_413`.
- `test_put_removes_orphan_site_dirs` — pre-create `sites/zzz/`; PUT a document without `zzz`; the directory is gone.
- `test_get_permission_denied_is_access_error` — monkeypatch read to raise `PermissionError`; the response is the `_access_denied` shape, not `{}`.
- `test_other_org_cannot_read_or_write` — using `other_org_client`: 404 on both.
- `test_sites_put_is_not_an_undo_step` — after a PUT, `GET /api/network/undo` (or the undo-info endpoint the header polls) reports the same depth as before.

**Green:** two handlers beside `get_layout`/`put_layout`, reusing `_resolve_project_src`, `_atomic_write_text`, `_access_denied`. The route module registers nothing new (same router).

### Task 1.3 — Lock and solver gating on the write route

**Files:** `backend/tests/test_sites_routes.py`.

**Red:**

- `test_put_sites_409_when_foreign_lock` — the same fixture pattern `test_layout_lock` uses for `/layout` (find it: `grep -n "project_locked" backend/tests/*layout*`); a foreign live lock makes PUT return 409 `project_locked`; GET still 200.
- `test_put_sites_409_while_solving` — with the solver-in-flight flag set the way the existing `_SOLVER_BLOCKING_PREFIXES` tests set it, PUT is 409.

**Green:** `_check_project_lock` call in `put_sites`, mirroring `put_layout`; no middleware change.

### Task 1.4 — Bundle membership and the sidecar carry fix (D15)

**Files:** `backend/routers/projects.py` (`_BUNDLE_FILES`, `_BUNDLE_DIRS`, `_carry_sidecars_on_move`), `backend/tests/test_bundle_sidecars.py` (extend), `backend/tests/test_save_sidecar_carry_seam.py` (extend).

**Red:**

- `test_sites_in_bundle_tuples` — `"sites.json" in _BUNDLE_FILES` and `"sites" in _BUNDLE_DIRS`.
- `test_scenario_copies_sites_and_context_dir` — create a project with `sites.json` and `sites/a/context.json`; `POST /{base}/scenarios`; both exist in the scenario dir; editing the scenario's document leaves the base's unchanged.
- `test_export_import_round_trips_sites` — the zip contains both; import restores both.
- `test_snapshot_restore_brings_sites_back` — snapshot, PUT a different document, restore, GET shows the original.
- `test_carry_on_move_copies_every_bundle_sidecar` (seam test, same `_Ctx` harness as the existing file) — with `layout.json`, `sites.json`, `adequacy_worksheet.json` and `sites/a/` in the source dir, a `rebind=False` save-as copies all of them to `dest`; `rebind=True` also copies (never moves) them. `chat.jsonl` behaviour is unchanged (existing tests stay green).
- `test_carry_on_move_is_best_effort` — a copy failure on `sites/` is logged and does not raise (same posture as uploads).

**Green:** extend the two tuples; in `_carry_sidecars_on_move`, after the uploads copy, loop `_BUNDLE_FILES` minus `network.nc`/`metadata.json`/`solver_config.json`/`user_ts.json`/`results_state.pkl` (those are written by the save itself) and `_BUNDLE_DIRS`, copying when present in the source and absent in `dest`. Document in the docstring why the five save-written files are excluded.

### Task 1.5 — Frontend `api/sites.ts` and `site3d/sitesStore.ts`

**Files:** `frontend/src/api/sites.ts`, `frontend/src/site3d/sitesStore.ts`, `frontend/src/site3d/sitesStore.test.ts`, `frontend/src/site3d/types.ts` (the `SitesDocument`, `Site`, `Placement` types).

**Red (vitest, jsdom, `vi.useFakeTimers`, mocked `sitesApi`):**

- `loads once per project and caches` — two `ensureLoaded(project)` calls → one GET.
- `setPlacement writes the memory cache synchronously and PUTs after 300 ms` — assert the cache before the timer, the PUT after.
- `three edits inside the window produce one PUT with the last document`.
- `flushPendingSitesToServer PUTs immediately and reports server` — same result shape as the layout flush (`status: 'nothing' | 'server' | 'local'`).
- `PUT failure falls back to localStorage under pypsa-gui:sites:<project>` and `ensureLoaded` prefers a newer localStorage copy over the server copy only when the server returned the empty document (never overwrite a real server document with a stale local one).
- `upsertSite / removeSite / setSiteBuses / setBoundary keep ids unique and the document valid` — mirror the backend validation for the fields the UI can produce.
- `renamePlacement(cls, old, new)` — the frontend twin of D14 for the optimistic path.
- `subscribe notifies on every cache write` — the canvas re-renders from the store, not from React Query (the document is not a query; it is the same posture as the layout cache).

**Green:** a small zustand-free module (Map cache + listeners), the debounced PUT, `persistSitesOnUnload` with `fetch keepalive` like `persistLayoutOnUnload`, and the three call sites of `flushPendingLayoutToServer` (`Sidebar.tsx`, `AppHeader.tsx`, `utils/projectActions.ts`) each also call `flushPendingSitesToServer`.

### Task 1.6 — Rename hook

**Files:** `backend/services/network_buses.py` is not it (buses are not placement keys); the component rename routes are. `grep -n "/rename" backend/routers/network.py backend/routers/*.py` and hook each class that can be a placement key (Generator, StorageUnit, Store, Load, Transformer, Line, Link).

**Red:** `test_rename_generator_renames_its_placement` — with a placement `Generator:old`, `POST /api/network/generators/old/rename {new}`; `GET /sites` shows `Generator:new`. One parametrised test over the classes that have a rename route; classes without one are listed in the test's docstring as not renameable today.

**Green:** the rename service calls `site_service.rename_component` on the active context's storage dir, best-effort (a sidecar failure never fails a rename).

### WP1 gate

Review agent A checks: every §4.1 validation rule has a red test; the carry helper's exclusion list is exactly the save-written files; the store never overwrites a real server document with localStorage; no route changed behaviour for `/layout`. Then: `tsc -b`, full vitest, full pytest, commit `feat(gui): site sidecar — routes, bundle carry, frontend store`.

---

## WP2 — Boundary drawing, site creation, portal, fit check

### Task 2.1 — `site3d/boundary.ts` (pure)

**Red:** `polygonAreaM2` (shoelace on the tangent plane; a 100 m × 200 m rectangle at 53°N → 20 000 ± 1 %); `centroid`; `pointInPolygon` (inside, outside, on a vertex counts as inside); `busesInside(buses, boundary)` ignores unplaced buses; `defaultBoundaryFor(bounds, originLngLat)` returns a rectangle 20 % larger than the packed bounds, clockwise, 4 vertices; `isValidBoundary` rejects < 3 vertices and repeated consecutive vertices.

### Task 2.2 — Site creation model (pure)

**Red:** `newSite({name, boundary, buses}) → Site` sets `origin` to the centroid and a fresh uuid; `siteForBus(doc, busName)` finds the site containing a bus; `primaryBus(site)` is the first member; `siteBounds(site)` returns the boundary in local metres for the camera fit.

### Task 2.3 — Draw mode on the map

**Files:** `MapCanvas.tsx`, `store/uiStore.ts` (`siteDrawMode: 'idle' | 'drawing'`, `siteDraft: [lng,lat][]`), `components/SiteDraftPanel.tsx` (new: the name + buses form), tests `MapCanvas.siteDraw.test.tsx` (mounting only the draw-mode reducer/hook, not Leaflet — extract `useSiteDraw` so it is testable), `SiteDraftPanel.test.tsx`.

**Red:**

- `useSiteDraw`: click adds a vertex; Escape clears; Enter with < 3 vertices does nothing and reports `too_few`; Enter with ≥ 3 yields `{boundary, busesInside}`.
- `SiteDraftPanel`: default name `Site n` where n = existing count + 1; buses inside pre-checked; Create disabled when `readOnly`; submit calls `sitesStore.upsertSite` and closes.
- The New site button is hidden when a slide panel or the palette is open (same rule as `MapModeSwitcher`; test by store state).

**Green:** a `Polygon` per site with a click popover (Open in 3D / Edit buses / Delete, the last two confirm through `confirmToast`); a dashed draft polyline while drawing; `map.on('click')` only while drawing so bus placement mode is unaffected.

### Task 2.4 — Portal and the site picker

**Files:** `uiStore.ts` (`activeSiteId`), `SiteCanvas.tsx`, `site3d/scene.ts` (`chooseSite(doc, activeSiteId, selectedComponent, buses)` replacing `chooseSiteBus`), tests in `scene.test.ts`.

**Red:** `chooseSite` prefers `activeSiteId`, then the site containing the selected component's bus, then the first site; returns `null` with no sites. The picker `<select aria-label="Site">` lists site names; changing it sets `activeSiteId`.

**Green:** Open in 3D sets both `activeSiteId` and `canvasView`.

### Task 2.5 — "Create a site around bus X"

**Red:** with no sites and ≥ 1 placed bus, the site view shows the offer; clicking it creates a site from `defaultBoundaryFor` with that bus as the only member and opens it (store assertion; the button is DOM, testable in jsdom without WebGL by rendering the empty-state component extracted as `SiteEmptyState.tsx`).

### Task 2.6 — Fit check

**Files:** `site3d/fit.ts` (pure), `SiteCanvas.tsx`.

**Red:** `fitReport(layout, site)` → `{landM2, plotM2, over: boolean, outside: string[]}` where `outside` lists objects with any footprint corner (after heading rotation) outside the boundary. Tests: a site whose objects fit; one over; one object straddling the edge.

**Green:** status line `land 50.3 ha · plot 32.0 ha` red when `over`; red emissive edge on `outside` objects; the boundary ribbon on the ground.

### WP2 gate

Review agent B checks: draw mode cannot conflict with `ClickToPlace`; `readOnly` gates Create; `chooseSite` precedence; the fit report handles heading rotation. Then suites, commit `feat(gui): sites on the map — draw, open in 3D, fit check`.

---

## WP3 — Placements, gizmos, arrange, read-only gating

### Task 3.1 — Layout with placements (pure)

**Files:** `site3d/layout.ts`, `layout.test.ts`.

**Red:** `buildSiteLayout(input, placements)`: a placed object keeps `origin` and gains `heading`; packed objects avoid nothing (documented); orphan placement keys are ignored and returned in `layout.orphans`; a placement for the switchyard itself is honoured (the Bus can be placed too); determinism holds with placements.

### Task 3.2 — `site3d/assetRules.ts` (D17)

**Red:** the rules object validates against its own schema (`validateRules`); `layout.ts` consumes it through a parameter with the default rules; changing `MWH_PER_BESS_CONTAINER` in a test-local copy changes the container count and nothing else.

### Task 3.3 — Gizmos

**Files:** `SiteCanvas.tsx`, `site3d/scene.ts` (`placementFromGizmo(matrix) → {x, y, heading}` pure, tested), `sitesStore.setPlacement`.

**Red:** `placementFromGizmo` decomposes a translation + Y-rotation matrix into local metres and a clockwise-from-north heading; round trips through `gizmoMatrixFor(placement)`.

**Green:** drei `PivotControls` on the selected object (`activeAxes=[true,false,true]`, rotation about Y only), `onDragEnd` → `setPlacement`; disabled when `readOnly`; the group re-reads its placement from the store.

### Task 3.4 — Arrange and Reset

**Red:** `arrangeAll(layout, site)` writes every packed object's current origin as a placement and prunes orphans (D14); `resetPlacement(site, key)` removes one. Store tests.

**Green:** two buttons in the site overlay, both hidden when `readOnly`.

### Task 3.5 — Read-only gating

**Red:** with `readOnly: true` in `uiStore`, `SiteCanvas`'s overlay renders no Arrange/Reset, the draft panel's Create is disabled, and `useSiteDraw` ignores clicks. Component tests on the extracted overlay (`SiteOverlay.tsx`) in jsdom.

### WP3 gate

Review: `placementFromGizmo` sign conventions (north = −Z, heading clockwise); no write when `readOnly`; orphans never pruned on read. Commit `feat(gui): place assets on the site — gizmos, arrange, read-only`.

---

## WP4 — Palette drops into 3D

### Task 4.1 — Drop registry and ground raycast (pure + scene)

**Files:** `site3d/dropRegistry.ts` (a module-level `{ screenToGround?: (clientX, clientY) => {x, y} | null }` the canvas registers on mount and clears on unmount), `scene.ts`.

**Red:** registry set/clear; `screenToGround` returns `null` when the ray misses the ground plane (pointing at the sky).

### Task 4.2 — `useAssetDrag` fourth branch

**Red (extend `useAssetDrag.test.tsx`):** a drop over an element with class `site3d-canvas` resolves `{canvas: 'site', ground: {x, y}}` via the registry; with no registry entry it cancels; the three existing branches are unchanged (existing tests stay green).

### Task 4.3 — `CreationForm` site-aware bus default

**Red:** when `creationItem.canvas === 'site'` and the active site has buses `[A, B]`, the bus field is a select pre-filled with `A`; for two-terminal items `bus0` defaults to `A` and `bus1` stays free; on success `setPendingPlacement({key, x, y})`.

### Task 4.4 — `pendingPlacement` consumption

**Red:** store test: when the created component appears in the query data, `SiteCanvas` (through a pure `consumePendingPlacement(doc, pending, components)`) writes the placement and clears the pending entry; if the component never appears within the existing pending-node timeout pattern, the pending entry is dropped with a console warning, not left forever.

### WP4 gate

Review: no regression in the schematic/map drop branches; the site drop cannot create a component on a bus outside the site. Commit `feat(gui): drop palette assets onto the 3D site`.

---

## WP5 — Site context service and rendering

### Task 5.1 — `services/site_context.py` pure core

**Files:** `backend/services/site_context.py`, `backend/tests/test_site_context.py`, fixtures `backend/tests/fixtures/overpass_eemshaven.json` (a recorded, trimmed Overpass response, ~50 elements) and a 256×256 synthetic Terrarium PNG generated in the test from a known height field.

**Red:**

- `padded_bbox(boundary)` — max(150 m, 25 %) padding.
- `overpass_query(bbox)` — the query text contains the seven feature selectors and the bbox in Overpass order (south, west, north, east).
- `elements_to_context(fixture)` — buildings count, a `height_source` for each of the four rules, lines and areas typed as in §4.2; a way with < 3 nodes is dropped; relations are ignored in v1 (documented).
- `terrain_grid(tiles, bbox, grid=64)` — decodes Terrarium `(R*256 + G + B/256) − 32768`, samples a 64×64 grid; a flat synthetic tile yields a flat grid; a ramp tile yields a monotone grid.
- `context_document(...)` — carries `attribution` for both sources and `version: 1`.

### Task 5.2 — Fetching with `httpx`, timeouts, endpoint setting

**Red (mocked `httpx.MockTransport`):** one POST to the Overpass endpoint; `PYPSAGUI_OVERPASS_URL` overrides it; 429/504 upstream → `SiteContextUnavailable` with the status and the setting's name in the message; a 60 s overall budget; the terrain fetch tolerates a missing tile (grid cell falls back to the neighbour mean).

### Task 5.3 — Routes and cache

**Red (`test_sites_routes.py`):** `POST /sites/{id}/context` writes `sites/<id>/context.json` and returns it; `GET` returns the cached copy without calling upstream (transport asserts zero calls); `DELETE` clears it; 404 for an unknown site id; 502 with the upstream message on failure; the POST is refused with 409 under a foreign lock (it writes to the project dir).

### Task 5.4 — Frontend `site3d/context.ts` (pure)

**Red:** `footprintToParts(polygon, height, origin)` → triangulated prism (use three's `ShapeGeometry`-free earcut through `THREE.ShapeUtils.triangulateShape`, which is pure math) with the expected triangle count for a rectangle; `linesToRibbons`; `heightmapToDisplacement(grid, extent)` produces `(grid+1)²` vertices; `groundHeightAt(x, y)` bilinear-samples the grid and clamps outside it.

### Task 5.5 — Rendering

**Green (no unit tests beyond the pure modules):** merged building mesh, ribbons, displaced ground, assets at `groundHeightAt`, OSM + terrain attribution added to the overlay. Verified in the QA stage headless run via `toDataURL`.

### Task 5.6 — Offline degrade

**Red:** `groundMode(context, imageryOk)` → `'imagery' | 'hillshade' | 'flat'`; `hillshadeTexture(grid)` is a canvas texture whose pixel at a slope faces differs from a flat cell's (test with a 2D canvas in jsdom via the existing `getContext` stub pattern in `exportPng.test.ts`, or assert the computed shade array instead of the canvas).

### WP5 gate

Review: the height rule order; no imagery on disk; the context fetch cannot block the request thread beyond 60 s; ODbL attribution present. Commit `feat(gui): site context — OSM footprints and terrain, cached per site`.

---

## QA — end to end

1. Backend: full `pytest`; `tests/test_packaging_requirements.py` passes.
2. Frontend: `tsc -b`, full `vitest`, `npm run build`; the `SiteCanvas` chunk stays separate.
3. Headless run (Playwright against dev servers in local mode, Chromium on SwiftShader, canvas read via `toDataURL` as the spike note prescribes) through: import the fixture campus → draw a boundary around the 33 kV bus → name it → Open in 3D → context fetch status → move the BESS with the gizmo → reload → it is still there → create a scenario → move it in the scenario → base unchanged → drop a battery from the palette onto the ground → it exists on the 33 kV bus at the drop point → drag the PV field outside the boundary → red outline and the status line goes red → lock the project from a second session → no gizmo, no 409 toast.
4. Packaging: the macOS build is not reachable here; record it as the one open runbook step, with the exact commands from `build-macos.sh` and the spike note's warning about real-GPU shadows.
5. Write `docs/superpowers/notes/<date>-3d-site-view-phase1-qa.md`: what passed, what did not, with the capture filenames, and the open items.

## Plan self-review (to be replaced by the reviewers' findings)

- Every spec decision maps to at least one task (table above).
- Every task has a red step whose failure mode is stated.
- No task mounts WebGL in a unit test; the three canvas-adjacent pieces that need DOM tests are extracted (`useSiteDraw`, `SiteDraftPanel`, `SiteOverlay`, `SiteEmptyState`).
- The only cross-cutting backend change outside the new routes is the carry helper (D15) and the rename hook (D14); both have seam tests.
