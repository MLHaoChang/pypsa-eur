# 3D site view — Phase 1 implementation plan (v2)

> **For agentic workers:** implement task-by-task, test first. Every task states its red tests before its green step; a red test that passes before any code is written is a plan defect and must be reported, not "fixed" by weakening the test. Tests marked **(pin)** are characterisation tests that are expected to pass immediately; they exist to lock a behaviour the feature depends on. Each work package ends with a review gate and an integration commit; the gate is an agent that did not write the code.

**Goal:** Ship the Phase 1 design (`docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md`): a site the user draws on the map, persisted in the project bundle, opened in 3D on cached site context, with placeable assets, palette drops, and a fit check.

**Architecture:** Five work packages in dependency order. Backend pure services (`site_service.py`, `site_context.py`) under `pypsa-gui/backend/services/`, routes under `/api/projects/{name}/sites` in `routers/projects.py` beside `/layout`, with the same helpers. Frontend pure modules under `pypsa-gui/frontend/src/site3d/` carry every decision the canvas makes; `SiteCanvas.tsx` and `MapCanvas.tsx` compose them. **No module in the main bundle imports `three`** (`SiteCanvas.tsx` and the modules only it imports may). No WebGL in any unit test.

**Tech stack:** FastAPI + pytest (`/root/.venv-pypsa-gui/bin/python -m pytest` from `pypsa-gui/backend`, or `pixi run gui-tests`); React 19 + TypeScript 5.8 strict + vitest 4 + jsdom (`npx vitest run` from `pypsa-gui/frontend`); three 0.186, @react-three/fiber 9.8, @react-three/drei 10.7 (installed by the spike); shapely, Pillow, httpx already present in the backend environment (shapely and Pillow become explicit pins in Task 5.0).

**Base:** `7f999e1` on `claude/3d-site-visualization-gatc5z` (spike + spec + plan v1).

**v2 (2026-09-29):** revised after two independent reviews (backend/persistence; frontend/testability). The findings and where each landed are in the last section.

---

## Process

| Stage | What | Gate |
|---|---|---|
| WP1 | Sidecar, routes, bundle carry, frontend store | review agent → fix → commit |
| WP2 | Boundary drawing, site creation, portal, fit check | review agent → fix → commit |
| WP3 | Multi-bus layout, placements, gizmos, arrange, read-only gating | review agent → fix → commit |
| WP4 | Palette drops into 3D | review agent → fix → commit |
| WP5 | Site context service + rendering | review agent → fix → commit |
| QA | Headless end-to-end run + full suites + build; QA note | QA note in `docs/superpowers/notes/` |

**Per work package, in order:**

1. Red: write the listed tests; run them; every non-pin test fails for the stated reason.
2. Green: the smallest implementation that passes them.
3. Refactor with the suite green.
4. `tsc -b`, the touched vitest files, the touched pytest files, then the **full** frontend and backend suites.
5. Review gate: an agent that did not write the code reads the diff against the spec's decisions and this plan's tasks and reports defects with file:line. Every finding is fixed or answered in the commit message.
6. Integration commit and push on the feature branch, one per work package.

**Never:** skip or weaken a test to get green; mount WebGL in vitest; write imagery to disk; add a Python dependency that is not already installed; import `three` from a main-bundle module.

### Spec decision coverage

| Decision | Task(s) |
|---|---|
| D1 sidecar in bundle | 1.1, 1.2, 1.4 |
| D2 site shape, stored origin, safe id | 1.1, 2.2 |
| D3 placement frame (metres from the **site origin**) | 1.1, 3.1 |
| D4 membership by bus | 2.1, 2.3, 3.1 |
| D5 click-to-vertex drawing | 2.3 |
| D6 portal, picker lists sites, default site | 2.4, 2.5 |
| D7 gizmo moves, layout-style persistence | 1.5, 3.3 |
| D8 placed vs packed, Arrange, Reset | 3.1, 3.4 |
| D9 fit check | 2.6 |
| D10 Overpass + Terrarium context, cached | 5.0–5.4 |
| D11 offline degrade | 5.6 |
| D12 lock + readOnly gating | 1.3, 3.5 |
| D13 undo excluded (and the rename caveat) | 1.2 (pin), 1.6 |
| D14 orphans, rename hook | 1.6, 3.1 |
| D15 sidecar carry on Save-As/Save-a-Copy/Clone | 1.4 |
| D16 drops into 3D | 4.1–4.4 |
| D17 asset rules table | 3.2 |

---

## WP1 — `sites.json` sidecar, routes, bundle carry, frontend store

### Task 1.1 — `site_service.py`: the document, its validation, and its I/O

**Files:** `backend/services/site_service.py` (new), `backend/tests/test_site_service.py` (new).

**Constants:** `SITES_FILE = "sites.json"`, `SITES_DIR = "sites"`, `MAX_SITES_BYTES = 4 * 1024 * 1024`, `SITE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")` (same posture as `_SNAPSHOT_ID_RE` in `routers/snapshots.py:97` and `_FILE_ID_RE` in `services/upload_service.py:183`: validated before any path join).

**Red:**

- `test_empty_document_when_missing` — `read_sites(dir)` with no file → `{"version": 1, "sites": []}`.
- `test_corrupt_file_degrades_to_empty` — invalid JSON, or a top-level list → the empty document.
- `test_permission_error_is_raised_not_swallowed` — `PermissionError` on read propagates.
- `test_validate_accepts_spec_example`.
- `test_validate_rejects` (parametrised): wrong `version`; duplicate ids; empty id; **id failing `SITE_ID_RE` (`..`, `a/b`, 65 chars, unicode)**; non-string bus; boundary with two vertices; vertex outside ±180/±90; non-finite placement `x`; placement key without a colon; unknown class prefix in a placement key (allowed set: Bus, Generator, StorageUnit, Store, Load, Transformer, Line, Link).
- `test_validate_preserves_unknown_keys` — extra top-level and per-site keys survive validate → write → read.
- `test_write_is_atomic_and_compact` — no temp file remains; over `MAX_SITES_BYTES` raises `SitesTooLarge`.
- `test_rename_component_moves_placement_key` — `rename_component(doc, "Generator", "old", "new")` renames `Generator:old` in every site; other classes untouched; missing key no-op; **`Bus` is a valid class** (the switchyard is placeable).
- `test_site_dir_is_contained` — `site_dir(project_dir, site_id)` raises for any id failing `SITE_ID_RE` and asserts `resolved.is_relative_to(project_dir)`.
- `test_prune_site_dirs_removes_only_orphans` — with `sites/a/`, `sites/b/` and only `a` in the document, `b` is removed (through `_force_rmtree`, `projects.py:207`, injected as a callable so the test can observe it), `a` kept; a name in `sites/` failing `SITE_ID_RE` is left alone and logged.

### Task 1.2 — Routes `GET/PUT /api/projects/{name}/sites`

**Files:** `backend/routers/projects.py`, `backend/tests/test_sites_routes.py` (new).

**Red:**

- `test_get_sites_missing_project_404`.
- `test_get_sites_empty_when_no_file` — 200 with the empty document.
- `test_put_then_get_round_trip`.
- `test_put_invalid_422_names_the_field` — 422 body names the offending site id / field.
- `test_put_over_limit_413`.
- `test_put_removes_orphan_site_dirs` — pre-create `sites/zzz/`; PUT without `zzz`; gone.
- `test_get_permission_denied_is_access_error` — monkeypatch `site_service.read_sites` to raise `PermissionError`; response is the `_access_denied` shape.
- `test_other_org_cannot_read_or_write` — `other_org_client` gets 404 on both (`_resolve_project_src` → `resolve_project`, `projects.py:245-270`).
- **(pin)** `test_sites_writes_never_snapshot_undo` — `"/api/projects/" not in main._UNDO_PREFIXES` (`main.py:100`), and `GET /api/network/undo/info` `depth` is unchanged across a PUT (`tests/test_chat_edits_are_captured.py:42` shows the shape).

**Green:** two handlers beside `get_layout` / `put_layout` (`projects.py:3244-3335`), reusing `_resolve_project_src`, `_atomic_write_text`, `_access_denied`.

### Task 1.3 — Lock and solver gating on the write route

**Red (direct-handler style, as `tests/test_project_locks.py:455 test_put_layout_409s_under_a_foreign_lock` does — an HTTP foreign lock is impossible because `second_identity` is another org, `conftest.py:346,464`):**

- `test_put_sites_409s_under_a_foreign_lock` — `put_sites(name, doc, db=db, user=other_user_same_org)` with a live lock held by `user_a` raises 409 `project_locked`; `get_sites` still returns.
- **(pin)** `test_put_sites_409_while_solving` — `/api/projects/` is already in `_SOLVER_BLOCKING_PREFIXES` (`main.py:292`) and the middleware runs before the handler (`main.py:801-822`); simulate the solve the way `tests/test_activate.py:160-185` parks an alive thread in `session_ctx(client).solver_state["thread"]`; assert `code == "solver_in_flight"`. Applies equally to the context routes of Task 5.3.

**Green:** `_check_project_lock` in `put_sites`, mirroring `put_layout` (`projects.py:3318-3325`).

### Task 1.4 — Bundle membership and the sidecar carry fix (D15)

**Files:** `backend/routers/projects.py` (`_BUNDLE_FILES` `:89-99`, `_BUNDLE_DIRS` `:109`, `_carry_sidecars_on_move` `:1536-1624`), `backend/tests/test_bundle_sidecars.py` (extend), `backend/tests/test_save_sidecar_carry_seam.py` (extend).

**Facts the tests rely on:** `_carry_sidecars_on_move` runs only from `_save_context` (`:2085`) — Save-As (`AppHeader.tsx:385`, rebind=true), Save-a-Copy (`Sidebar.tsx:770-775`, rebind=false) and Clone (`NewProjectWizard.tsx:485-486`: load(src) then save(dest, rebind=true), so `loaded ≠ name` and carry runs). Scenario create does **not** use it (`_create_scenario_db`, `:2724-2728` copies the tuples directly); every bundle loop is generic over the tuples (`projects.py:1034,1049,2724,2728,3360,3367`; `snapshots.py:447,457,653,668`; `_copy_bundle_dirs:201`), nothing hard-codes `uploads`.

**Red:**

- `test_sites_in_bundle_tuples`.
- `test_scenario_copies_sites_and_context_dir` — both present in the scenario dir; editing the scenario's document leaves the base's unchanged.
- `test_export_import_round_trips_sites`.
- `test_snapshot_restore_brings_sites_back` — snapshot, PUT a different document, restore, GET shows the original. **Documented caveat (same as `layout.json` today):** restoring a snapshot that predates sites leaves the live `sites.json` in place; not special-cased.
- `test_carry_on_move_copies_and_replaces_every_bundle_sidecar` (seam test; same `_Ctx` harness; **not** the `spy` fixture, which stubs `_copy_bundle_dirs`; needs `tmp_projects_dir` because `_safe_project_dir(loaded)` resolves against `PROJECTS_DIR` when `db is None`) — with `layout.json`, `sites.json`, `adequacy_worksheet.json` and `sites/a/` in the source dir and a **stale** `sites.json` already in `dest`, both `rebind=False` and `rebind=True` copy every sidecar and **replace** the stale one (Save-As with `force=true` over an existing project must not keep the overwritten project's sidecars). `chat.jsonl` behaviour unchanged (existing tests stay green).
- `test_carry_on_move_is_best_effort` — monkeypatch `shutil.copy2` to raise for `sites.json`; the call logs and returns; the other sidecars are still copied.

**Green:** extend the tuples; in `_carry_sidecars_on_move`, after the uploads copy, loop `_BUNDLE_FILES` minus the five files `_save_context` itself writes (`network.nc` `:1890`, `solver_config.json` `:1929`, `results_state.pkl` `:1947`, `user_ts.json` `:1978`, `metadata.json` `:2049`) and `_BUNDLE_DIRS`, copy-and-replace when present in the source. Fix the stale docstring claim that `create_scenario` uses this helper.

### Task 1.5 — Frontend `api/sites.ts` and `site3d/sitesStore.ts`

**Files:** `frontend/src/api/sites.ts`, `frontend/src/site3d/types.ts`, `frontend/src/site3d/sitesStore.ts` (**a zustand store** like `uiStore`, because four consumers read it — `MapCanvas` polygons, `SiteCanvas`, `CreationForm`, `SiteDraftPanel` — and `useSyncExternalStore` semantics come for free; the debounce / flush / keepalive stay module functions exactly as `topologyLayoutStore.ts` does), `frontend/src/site3d/sitesStore.test.ts`.

**Red (vitest, jsdom, fake timers, mocked `sitesApi`):**

- `loads once per project and caches`.
- `setPlacement writes the store synchronously and PUTs after 300 ms`.
- `three edits inside the window produce one PUT with the last document`.
- `flushPendingSitesToServer PUTs immediately and reports server` — `{status: 'nothing' | 'server' | 'local'}`; **when called with the new name after a Save-As (`Sidebar.tsx:786-787` flushes to `result.saved`) it chases the previous project's pending document the way `flushPendingLayoutToServer` chases `__local__`**.
- `PUT failure writes localStorage pypsa-gui:sites:<project ?? '__local__'>`; **`ensureLoaded` falls back to localStorage only when the GET throws** (never when the server returns the empty document — a deleted-and-recreated project name must not resurrect a stale copy); **a successful PUT clears the local key**.
- `upsertSite / removeSite / setSiteBuses / setBoundary keep ids unique and the document valid`.
- `renamePlacement(cls, old, new)`.
- `writes are refused when evaluateMutation says read-only` — with `uiStore.readOnly = true`, `setPlacement` / `upsertSite` are no-ops and **zero PUTs** happen even after the debounce window (a debounce that fires after a lock never writes).
- `persistSitesOnUnload uses fetch keepalive` like `persistLayoutOnUnload`.

**Green:** as above; the three `flushPendingLayoutToServer` call sites (`Sidebar.tsx`, `AppHeader.tsx`, `utils/projectActions.ts:554`) also call `flushPendingSitesToServer`.

### Task 1.6 — Rename hook (D14) — in the CRUD seam, not a route

**Facts:** the only rename route is `POST /buses/{name}/rename` (`routers/network.py:264` → `services/network_buses.py:102 apply_rename_bus`). Every other class renames through `PUT /api/network/{collection}/{name}` with `name` in the body → `services/network_crud.py::_update_component` (`:330-380`, `new_name = merged.pop("name", name)` → `_rename_component_safely`). The chat tools reach the same two functions (`services/chat_tools.py:700-725`). The storage dir is `PyPSAService.get_active_context().storage_dir` (`services/project_registry.py:159-168`), `None` for an unsaved scratch network.

**Red:**

- `test_rename_via_put_renames_placement` — parametrised over generators, storage_units, stores, loads, transformers, lines, links: with a placement `<Class>:old`, `PUT /api/network/<collection>/old {"name": "new", …full row…}`; `GET /sites` shows `<Class>:new`.
- `test_rename_bus_route_renames_placement` — via `POST /buses/old/rename`.
- `test_rename_hook_noops_without_storage_dir` — scratch network: no error, no file.
- `test_rename_hook_failure_never_fails_the_rename` — monkeypatch the sidecar write to raise; the rename still succeeds.

**Green:** one call in `_update_component` after `_rename_component_safely`, one in `apply_rename_bus`, both best-effort.

**Documented consequence (D13):** undo of a rename restores the component under the old name while the placement stays under the new key; the object then packs at its default position until moved. Accepted for v1 and recorded in the spec's D13 row by this task.

### WP1 gate

Review: every §4.1 rule plus `SITE_ID_RE` has a red test; the carry loop replaces; the store falls back on error only; no `/layout` behaviour changed; the rename hook is in both seams. Commit `feat(gui): site sidecar — routes, bundle carry, frontend store`.

---

## WP2 — Boundary drawing, site creation, portal, fit check

### Task 2.1 — `site3d/boundary.ts` (pure, no `three`)

**Red:** `polygonAreaM2` (100 m × 200 m at 53°N → 20 000 ± 1 %); `centroid`; `pointInPolygon` (inside, outside, vertex counts as inside); `busesInside(buses, boundary)` ignores unplaced buses; `defaultBoundaryFor(bounds, origin)` returns a 4-vertex clockwise rectangle 20 % larger than `bounds` expressed in metres from `origin` — **the test pins that the rectangle's centroid is the centre of `bounds`, not the origin**; `isValidBoundary` rejects < 3 vertices and repeated consecutive vertices; `dedupeTrailing(vertices)` removes trailing duplicates (what a double-click close produces).

### Task 2.2 — Site model (pure)

**Red:** `newSite({name, boundary, buses})` sets `origin` = centroid and an id matching `SITE_ID_RE` (22-char base64url uuid); `siteForBus(doc, busName)`; `primaryBus(site)` = first member; `siteBounds(site)` = the boundary in metres from `site.origin`; `busOffsets(site, buses)` = `toLocal(site.origin, busLngLat)` per member (the translation between the site frame and each bus, consumed by Task 3.1).

### Task 2.3 — Draw mode on the map

**Files:** `MapCanvas.tsx`, `store/uiStore.ts` (`siteDrawMode`, `siteDraft`), `site3d/useSiteDraw.ts` (extracted hook, testable without Leaflet), `components/SiteDraftPanel.tsx`, tests `useSiteDraw.test.ts`, `SiteDraftPanel.test.tsx`.

**Red:**

- `useSiteDraw`: click adds a vertex; Escape clears; **a double-click (two clicks then `dblclick` at the same point) closes with the trailing duplicate removed**; Enter with < 3 vertices reports `too_few`; Enter with ≥ 3 yields `{boundary, busesInside}`; **clicks are ignored while `placing` (bus placement) is active, and `readOnly` blocks entering draw mode**.
- `mapClickOwner(state) → 'place' | 'draw' | null` (pure selector on the store): never both; `'place'` wins when placement is active.
- `SiteDraftPanel`: default name `Site n`; buses inside pre-checked; Create is `disabled` with `title={readOnlyMessage(reason)}` when read-only (the `AppHeader.tsx:827` vocabulary); submit calls `sitesStore.upsertSite`.
- New site button hidden while a slide panel or the palette is open (store-state test, same rule as `MapModeSwitcher`).

**Green:** `<ClickToPlace>` mounts only when `mapClickOwner === 'place'`; the draw handler is registered only when `'draw'`; **`doubleClickZoom` disabled while drawing**; site `<Polygon bubblingMouseEvents={false}>` so a polygon click never reaches the map click handlers; dashed draft polyline; popover with Open in 3D / Edit buses / Delete (the last two via `confirmToast`).

### Task 2.4 — Portal and the site picker

**Files:** `uiStore.ts` (`activeSiteId`, **persisted per project under `network-diagram:active-site:<project>`**), `SiteCanvas.tsx`, `site3d/scene.ts` (`chooseSite`).

**Red:** `chooseSite(doc, activeSiteId, selectedComponent, buses)` prefers `activeSiteId`, then the site containing the selected component's bus, then the first site; `null` with no sites. Persisted `activeSiteId` is restored per project on reload. The picker `<select aria-label="Site">` lists site names.

**Green:** Open in 3D sets `activeSiteId` and `canvasView`. **One `siteExtent = union(siteBounds(site), layout.bounds)` feeds the camera fit, the shadow frustum, `OrbitControls maxDistance` and the imagery tile range** (today all four read `layout.halfSizeM` / `layout.bounds`, `SiteCanvas.tsx:96-111, 158-164, 231-246, 271-279`); `<Canvas key={site.id}>`.

### Task 2.5 — "Create a site around bus X"

**Files:** `components/SiteEmptyState.tsx` (extracted; DOM only), test.

**Red:** with no sites and ≥ 1 placed bus, the offer renders; clicking creates a site from `defaultBoundaryFor` with that bus as the only member and sets it active; hidden when `readOnly`.

### Task 2.6 — Fit check

**Files:** `site3d/fit.ts` (pure), `site3d/layout.ts` (**`SiteObject` gains `heading: number`, default 0** — heading is applied nowhere today; this task adds the field and the corner rotation, WP3 adds the source of non-zero values), `SiteCanvas.tsx`.

**Red:** `fitReport(objects, site)` → `{landM2, plotM2, over, outside: string[]}`; `outside` uses the four footprint corners rotated by `heading` about the object origin; tests: fits; over; straddling; a rotated object whose unrotated box would be inside but whose rotated corner is outside.

**Green:** status line `land 50.3 ha · plot 32.0 ha` red when `over`; red emissive edge on `outside`; the boundary ribbon on the ground.

### WP2 gate

Review: click ownership is exclusive; double-click close is valid; the four extents share `siteExtent`; `activeSiteId` restores; heading is applied in `fitReport`. Commit `feat(gui): sites on the map — draw, open in 3D, fit check`.

---

## WP3 — Multi-bus layout, placements, gizmos, arrange, read-only

### Task 3.1 — Layout for a site with several buses and placements (pure)

**Files:** `site3d/layout.ts`, `layout.test.ts`.

**Red:**

- `SiteInput.buses: Array<{name, v_nom, offset: [x, y]}>` replaces the single `bus`; **one switchyard per bus at its offset**; a component attached to any member bus is included; assets pack around **their own** bus's yard; the packed origins are in the **site frame** (bus offset added).
- A placement keeps `origin` and `heading`; a placement for `Bus:<name>` moves that yard (its packed assets follow the yard's offset only when unplaced).
- Orphan placement keys are ignored and returned in `layout.orphans`.
- Determinism with placements; the no-overlap test holds for unplaced objects of one bus; overlaps between placed and packed objects are allowed (documented).

### Task 3.2 — `site3d/assetRules.ts` (D17)

**Red:** the rules object validates against `validateRules`; `layout.ts` takes rules as a parameter with the default; changing `MWH_PER_BESS_CONTAINER` in a test-local copy changes only the container count.

### Task 3.3 — Gizmos

**Files:** `SiteCanvas.tsx`, `site3d/placementMath.ts` (**pure; may import `three` — it is imported only by `SiteCanvas`**), test.

**Facts:** drei `PivotControls` (`node_modules/@react-three/drei/web/pivotControls/index.d.ts:29-31`): `onDragEnd?: () => void`, `onDrag?: (l, deltaL, w, deltaW)`; `activeAxes=[true,false,true]` gives X/Z arrows, the XZ slider and the Y rotator, **plus scaling spheres unless `disableScaling`**; with `autoTransform` (default) it mutates the object's matrix and re-assigns a passed `matrix` prop every frame; `l` is local to the pivot's parent group; handles disable the default camera controls, which requires `makeDefault` on `OrbitControls` (the spike has it).

**Red:** `placementFromMatrix(l: Matrix4) → {x, y, heading}` (north = −Z, heading clockwise from north) and `matrixFor(placement)` round-trip; a heading of 90° yields the expected east-facing rotation.

**Green:** `<PivotControls activeAxes={[true,false,true]} disableScaling matrix={useMemo(() => matrixFor(placement), [placement])} onDrag={l => lastL.current.copy(l)} onDragEnd={() => setPlacement(site.id, key, placementFromMatrix(lastL.current))} enabled={!readOnly}>` wrapping the selected object's group; **the group no longer sets `position` itself — the matrix carries it** (otherwise the offset is applied twice, `SiteCanvas.tsx:44`); the pivot sits at scene root so `l` is world.

### Task 3.4 — Arrange and Reset

**Red:** `arrangeAll(layout, site)` writes every packed object's current origin (site frame) as a placement and prunes orphans; `resetPlacement(site, key)` removes one; store tests including the read-only refusal.

### Task 3.5 — Read-only gating

**Red:** with `readOnly` in `uiStore`, the extracted `SiteOverlay.tsx` renders Arrange/Reset `disabled` with `title={readOnlyMessage(reason)}`; `useSiteDraw` ignores clicks; `sitesStore` writes are refused (1.5). **Assert zero PUTs, not "no toast"** (`project_locked` is already quiet, `api/client.ts:83`).

### WP3 gate

Review: frames (bus offset vs site origin) are consistent between layout, fit and gizmos; the pivot does not double-apply position; nothing writes when read-only. Commit `feat(gui): place assets on the site — multi-bus layout, gizmos, arrange, read-only`.

---

## WP4 — Palette drops into 3D

### Task 4.1 — Drop registry and ground raycast

**Files:** `site3d/dropRegistry.ts` (**no imports at all**: a `{screenToGround?: (clientX, clientY) => {x, y} | null; groundToScreen?: (x, y) => {x, y} | null}` slot the canvas fills on mount and clears on unmount), `site3d/raycast.ts` (imports `three`; **imported only by `SiteCanvas`**), tests; **a red test that greps `site3d/{dropRegistry,sitesStore,boundary,fit,layout,scene,useSiteDraw,types}.ts` for `from 'three'` and fails on any hit** — this is the bundle-boundary guard the QA size check confirms.

**Red:** registry set/clear; `screenToGround` returns `null` for a ray missing the ground plane (`y = 0`; **after WP5 the ground is displaced — a drop on a slope lands short by `height·tan(pitch)`; documented, not solved in v1**).

### Task 4.2 — `useAssetDrag` fourth branch

**Facts:** `resolveDrop` receives `clientX/Y` (`useAssetDrag.ts:55`); `DropResult.canvas` is `'schematic' | 'map' | null` with `position` only (`:16-30`); the hook forwards only `dropPosition` / `dropBusName` (`:118-123`); R3F spreads props onto its wrapper `<div>` so `<Canvas className="site3d-canvas">` is what `closest()` finds.

**Red (extend `useAssetDrag.test.tsx`):** `DropResult` gains `canvas: 'site'` and `ground`; a drop over `.site3d-canvas` resolves `{canvas: 'site', busName: null, ground}` via the registry; no registry entry → cancel; the three existing branches unchanged.

### Task 4.3 — `CreationRequest.dropSite` and the site-aware bus field

**Red:** `CreationRequest` gains `dropSite?: {siteId, ground: {x, y}}`; `useAssetDrag` forwards it; in `CreationForm`, when `dropSite` is set the bus field is **`BusAutocomplete` prefilled with `primaryBus(site)` and its option list restricted to the site's buses** (least invasive: prefill + restrict, no new component); two-terminal items prefill `bus0`, leave `bus1` free.

### Task 4.4 — Placement on success (no pending state)

**Red:** on create success with `dropSite`, `CreationForm` calls `sitesStore.setPlacement(siteId, \`${COMPONENT_TYPE[item.id]}:${name}\`, {x, y, heading: 0})` directly (an orphan-for-a-moment is harmless under D14); once the query refetches, the object renders at the drop point (layout test with the placement present). **`pendingPlacement` does not exist.**

### WP4 gate

Review: no regression in the schematic/map branches; the site drop cannot target a bus outside the site; no `three` import leaked into the main bundle. Commit `feat(gui): drop palette assets onto the 3D site`.

---

## WP5 — Site context service and rendering

### Task 5.0 — Pins

**Red:** `tests/test_packaging_requirements.py` (`:203-230`, every unguarded import's distribution must be pinned in `gui-requirements.txt`) fails once `site_context.py` imports `shapely` and `PIL`. **Green:** pin `shapely==2.1.2` and `pillow==12.3.0` (the versions in the desktop env, per the file's header rule) with a comment naming this feature. `httpx` is already pinned.

### Task 5.1 — `services/site_context.py` pure core

**Files:** `backend/services/site_context.py`, `backend/tests/test_site_context.py`, fixtures `backend/tests/fixtures/overpass_eemshaven.json` (recorded, trimmed, ~50 elements) and a synthetic Terrarium PNG built in the test.

**Red:** `padded_bbox`; `overpass_query(bbox)` contains the seven selectors and the bbox in Overpass order; `elements_to_context(fixture)` (building count, each of the four `height_source` values, lines/areas typed as §4.2, a way with < 3 nodes dropped, relations ignored); `terrain_grid` decodes `(R·256 + G + B/256) − 32768` (flat tile → flat grid; ramp → monotone); `context_document` carries `attribution` and `version`.

### Task 5.2 — Fetching

**Red (mocked `httpx.MockTransport`):** one POST to the Overpass endpoint; `PYPSAGUI_OVERPASS_URL` overrides; **`User-Agent: pypsa-gui/<version>` on every request** (OSM/Overpass usage policy); 429/504 → `SiteContextUnavailable` naming the status and the setting; **overall budget 25 s** (the axios client times out at 30 s, `api/client.ts:18`); a missing terrain tile falls back to the neighbour mean; the client is constructed like `services/llm_openai_compat.py:249` (default `trust_env`, so proxies and the CA bundle behave as the packaged app's LLM calls do).

### Task 5.3 — Routes and cache

**Red:** `POST /sites/{site_id}/context` writes `sites/<id>/context.json` (through `site_dir`, so **a traversal id is 404 before any I/O — `test_context_route_rejects_traversal_id`**) and returns it; `GET` returns the cache with zero upstream calls; `DELETE` clears; 404 unknown site; 502 with the upstream message; 409 under a foreign lock (direct-handler style); **(pin)** 409 while solving.

### Task 5.4 — Frontend `site3d/context.ts` (pure; imports `three` for `ShapeUtils.triangulateShape` and `Matrix4` — verified to import cleanly under jsdom — **imported only by `SiteCanvas`**)

**Red:** `footprintToParts` → prism with the expected triangle count for a rectangle; `linesToRibbons`; `heightmapToDisplacement(grid, extent)` → `(grid+1)²` vertices; `groundHeightAt(x, y)` bilinear with clamping.

### Task 5.5 — Rendering

**Green:** merged building mesh, ribbons, displaced ground, assets at `groundHeightAt`, OSM + terrain attribution. Verified in QA via the debug hook's `snapshot()`.

### Task 5.6 — Offline degrade

**Red:** `groundMode(context, imageryOk)` → `'imagery' | 'hillshade' | 'flat'`; **`hillshade(grid) → Uint8ClampedArray` is pure and tested** (a sloped cell differs from a flat one); the `CanvasTexture` wrapper is untested (jsdom has no 2D canvas).

### WP5 gate

Review: height rule order; no imagery on disk; the fetch budget; ODbL attribution; pins present. Commit `feat(gui): site context — OSM footprints and terrain, cached per site`.

---

## Debug hook (permanent, gated) — built in WP2, used by QA

`<Site3dDebugHook/>` inside the Canvas, rendered only when `import.meta.env.DEV || location.search.includes('site3dDebug')`, exposes `window.__site3d = { project(key) → {x, y} | null, layout, placements, snapshot: () => gl.domElement.toDataURL() }` from the same `useThree` camera/size the drop registry uses (`groundToScreen` is the inverse of `screenToGround`). The spike removed its ad-hoc hook; this one is the supported way to drive the view from a browser test.

## QA — end to end

1. Backend: full `pytest`, including `test_packaging_requirements.py`.
2. Frontend: `tsc -b`, full `vitest` (including the `three`-import guard), `npm run build`; the `SiteCanvas` chunk stays separate and the main `spa` chunk's gzipped size does not grow by more than 5 kB against the WP1 baseline.
3. Headless run (Playwright, dev servers, **auth mode with two users in one org** for the lock step, Chromium on SwiftShader, canvas via `snapshot()`): import the fixture campus → draw a boundary around the 33 kV bus (double-click close) → name it → Open in 3D → context fetch status → move the BESS with the gizmo → reload → still there → create a scenario → move it in the scenario → base unchanged → drop a battery onto the ground → it exists on the 33 kV bus at the drop point → drag the PV field outside the boundary → red outline and red status line → lock the project as user B → as user A: gizmos disabled, zero PUTs (network log), no toast. Also: a solve in flight (start a real LOPF) → same read-only behaviour.
4. Packaging: the macOS build is not reachable here; recorded as the one open runbook step with `build-macos.sh`'s commands and the spike note's real-GPU caveat.
5. `docs/superpowers/notes/<date>-3d-site-view-phase1-qa.md`: pass/fail per step with capture names, open items.

---

## Review findings folded into v2

| # | Finding (reviewer) | Where it landed |
|---|---|---|
| B1 | No per-class rename routes; renames go through `PUT` with `name` in the body; Bus is a placement key too | Task 1.6 rewritten; D13 caveat documented |
| B2 | Site id is a path segment with no charset rule → traversal on context write | `SITE_ID_RE`, `site_dir` containment, `_force_rmtree` (1.1, 5.3) |
| B3 | Foreign lock untestable over HTTP; solver test green before code | Direct-handler lock test; solver test marked (pin) (1.3, 5.3) |
| B4 | Undo test named a POST route and could never be red | (pin) on `_UNDO_PREFIXES` + `/undo/info` (1.2) |
| B5 | "Lock from a second session" impossible in local mode | QA runs the lock step in auth mode with two users; plus a solve-in-flight check |
| B6 | `test_packaging_requirements` fails on unpinned shapely/Pillow | Task 5.0 |
| B7 | Copy-when-absent keeps stale sidecars on forced Save-As; scenario doesn't use the helper; seam-test fixture traps | Copy-and-replace; facts recorded; fixture guidance (1.4) |
| B8 | Bundle loops are generic; snapshot-restore caveat | Documented in 1.4 |
| B9 | 60 s budget > 30 s axios timeout; no User-Agent; client construction | 25 s budget, UA, client like the LLM client (5.2) |
| B10 | Stale docstring; minor confirmations | 1.4 |
| F1 | Layout takes one bus; two frames (bus vs site origin) never reconciled | Task 3.1 rewritten; `busOffsets` in 2.2; 2.1 centroid pin; 2.5 |
| F2 | `pendingPlacement` unnecessary; cited pattern has no timeout | Task 4.4 rewritten |
| F3 | `DropResult` / `CreationRequest` cannot carry the data; class placement; registry precedent | 4.2, 4.3 |
| F4 | `PivotControls` gives no matrix in `onDragEnd`; scaling spheres; matrix prop mutation; double position | 3.3 rewritten |
| F5 | Double-click close yields a duplicate vertex; polygon clicks bubble into both click modes | 2.1 `dedupeTrailing`, 2.3 `mapClickOwner`, `bubblingMouseEvents`, `doubleClickZoom` |
| F6 | Heading does not exist in WP2 | `SiteObject.heading` added in 2.6 |
| F7 | Fit-to-boundary implied; shadow/maxDistance/mosaic still follow the layout; Canvas key | `siteExtent` and `key={site.id}` (2.4) |
| F8 | `three` import could leak into the main bundle; slope drop error | `dropRegistry` import-free, `raycast.ts`, grep guard test, QA size assertion (4.1); slope caveat |
| F9 | Store shape and localStorage fallback rule; Save-As flush target | zustand store; fallback on error only; clear on success; chase previous key (1.5) |
| F10 | `hillshadeTexture` cannot run in jsdom | pure `hillshade` array (5.6) |
| F11 | Read-only vocabulary; test zero PUTs not toasts; guard writes in the store | 1.5, 3.5 |
| F12 | No debug hook exists; QA needs projection | Permanent gated `Site3dDebugHook` |
| F13 | `activeSiteId` not persisted; default rectangle centroid | 2.4, 2.1 |
