# 3D site view — Phase 1 end-to-end QA

Date: 2026-09-29
Scope: the QA stage of `docs/superpowers/plans/2026-09-29-3d-site-view-phase1.md` (§ "QA — end to end"), run on branch `claude/3d-site-visualization-gatc5z` after WP1–WP5 and their review-gate fix-ups.

## 1. Backend

Full `pytest` on a still tree, at the commit that carries the last code change (`3d5d44e`; later commits touch this note only).

| Result | Count |
|---|---|
| passed | 6184 |
| skipped | 27 |
| failed | 1 |

The one failure is `tests/test_packaging_requirements.py::test_the_spec_names_every_gridspine_module_the_backend_guard_imports`: `pypsa-gui.spec` does not name `gridspine.drivers.year_study`. It fails the same way on the base branch, this branch touches neither the spec nor gridspine, and it is queued as a separate task (§6). The pin test in the same file, which this feature's `shapely` and `pillow` pins had to satisfy, passes.

## 2. Frontend

| Check | Result |
|---|---|
| `tsc -b` | clean |
| `vitest run` (full) | 200 files, 2156 tests, all pass — includes the `three`-import bundle guard |
| `npm run build` | clean |
| `SiteCanvas` chunk separate | yes — 280.79 kB gzipped (spike: 263 kB; WP5 adds the context geometry) |
| Main `spa` chunk growth vs WP1 (`6bc338a`) | 821.21 → 825.74 kB gzipped, **+4.53 kB** (budget 5 kB) |

The main chunk first measured +5.30 kB. What it gained is map-view and form code that has to live there (site polygons and drawing, the popover, the sites store, the creation form's site-bus checks); none of the 3D code is in it. The new-site panel, needed only once a boundary is drawn, is now lazy-loaded (its own 1.27 kB chunk), which brought the growth under budget.

## 3. Headless run

**Setup.** Backend in **auth mode** (not local mode) with a fresh SQLite database and one org holding two admin users, A and B; Vite dev server; Chromium on SwiftShader through Playwright; the canvas read through the gated debug hook's `snapshot()`, never `page.screenshot` (spike note, lesson 8). Overpass (`overpass-api.de`) is unreachable from this container (connection reset), so the backend's `PYPSAGUI_OVERPASS_URL` pointed at a local stub that answers every query with the synthetic fixture `backend/tests/fixtures/overpass_eemshaven.json` (5 buildings, 4 lines, 3 areas). The **terrain tiles were real** (AWS Terrarium over S3), and so was the Esri imagery. The fixture network is the spike's Eemshaven campus (110 kV + 33 kV buses; wind, transformers, gensets, PV, BESS, data halls, an electrolyser link to H₂ tanks). For the solve step, the same network tiled over 8760 hourly snapshots.

Driver: a single Playwright script (kept outside the repo, in the session scratchpad); captures `e2e-*.png` from the canvas.

**Result: 19 of 19 steps pass; no page errors, no unexpected console errors.** (The chat-settings probes answer 403 to a non-super-admin in auth mode; that is unrelated to this feature and filtered.)

| # | Step (plan §QA-3) | Result | Evidence |
|---|---|---|---|
| 1 | Import the fixture campus, save it as `campus` (user A) | pass | import 200, save 200 |
| 2 | Draw a boundary around the 33 kV bus (double-click close), name it, create | pass | site on the server with both campus buses, 4 vertices; `e2e-2-polygon.png` |
| 3 | Open in 3D → site context fetched and drawn | pass | status "5 buildings · 4 lines · terrain", ground mode imagery; `e2e-3-site3d.png` |
| 3b | Context cached on the server | pass | GET `/sites/{id}/context` 200 |
| 3c | Refresh context | pass | exactly one POST; the scene keeps its context throughout |
| 4 | Move the BESS with the gizmo | pass | placement x = 228.0 m on the server; `e2e-4-moved.png` |
| 4b | Reload → still there | pass | the reloaded view reads the same placement |
| 5 | Create a scenario | pass | 201 |
| 5b | The scenario carries the site document | pass | same site id in `campus-s1` |
| 5c | Move the BESS in the scenario → base unchanged | pass | scenario x = 94.6 m, base still 228.0 m |
| 6a | Drop a battery from the palette onto the ground → form | pass | bus prefilled with the site's primary bus (110 kV), per D16 |
| 6b | Choose the 33 kV bus, create | pass | storage unit on `Campus 33kV`, placement written at the drop point; `e2e-6-dropped.png` |
| 7 | Drag the PV field outside the boundary | pass | red outline, status "land 68.1 ha · plot 11.3 ha · does not fit", "1 outside"; `e2e-7-outside.png` |
| 8 | User B locks the project | pass | 200 |
| 8b | As user A: read-only | pass | "Read-only" banner naming B, Arrange disabled with the read-only reason, a gizmo drag writes nothing (**zero PUTs**), document unchanged, no new toast; `e2e-8-locked.png` |
| 9 | The campus over 8760 h, same site | pass | 8760 snapshots |
| 9b | Start a real LOPF from the header (Run LOPF) | pass | job queued |
| 9c | While it solves: read-only | pass | "Read-only — this project is solving in the queue", a gizmo drag writes nothing (zero PUTs); `e2e-9-solving.png` |
| 9d | Solve finishes → writable again | pass | job completed (optimal), Arrange enabled again |

Two earlier runs failed on the harness, not the app, and are recorded because they shaped the procedure. One ran while the backend suite saturated the CPU; the software WebGL renderer then took 27 s to draw the view and navigations timed out. So the headless run needs an otherwise idle machine. The other queued the solve through the API; the header stops polling an idle queue, so it never saw the job. A user starts a solve from the header, which is what the run now does.

## 4. Packaging (open runbook step)

The macOS build cannot be produced here. It is the one QA step left open, to be run on a Mac:

```
bash pypsa-gui/build-macos.sh
```

Then, in the packaged app: open the campus project, draw a site, Open in 3D, confirm the ground imagery, the context buildings and terrain, the gizmo, and a palette drop. The script syncs the build venv to `gui-requirements.txt` on every build, so the two new pins (`shapely==2.1.2`, `pillow==12.3.0`) are picked up; `test_packaging_requirements.py`'s pin test passes. Carried from the spike note: the render path is plain WebGL2, which WKWebView has, but shadow quality and frame rate on a real GPU have not been seen by anyone yet.

## 5. Defects found by QA and fixed

| Found in | Defect | Fix |
|---|---|---|
| Dry run, "Open in 3D" and the first gizmo drag | React warned "Attempted to synchronously unmount a root while React was already rendering", and once the page threw an uncaught `removeChild … not a child of this node`. Cause: drei's `<Html>` hover label renders through its own React root and unmounts it synchronously in a layout-effect cleanup, mid-render of the app when the 3D tree tears down. Present since the spike. | The label is now ordinary DOM over the canvas, moved each frame to its object's projected top (`LabelTracker` in `SiteCanvas.tsx`). No nested roots remain; the warning and the error are gone in the repro. |
| Bundle-size check | Main chunk +5.30 kB against a 5 kB budget. | New-site panel lazy-loaded (§2). |

Found by the WP5 review gate and fixed before QA (commit `3ccd44c`): the fetch budget did not bound the total (tile timeouts are now clamped to what is left); no ceiling on the bbox, the Overpass body or the document (25 km², 32 MB streamed, 20 000 features); a 409 started the five-minute retry backoff (now only upstream failures do); plus height units, the Mercator clamp, 512 px terrain tiles, a Refresh context control, and the OSM credit no longer depending on the cached document.

## 6. Open items

- **Packaging** (§4).
- **Overpass for real.** Every OSM answer in this run came from the stub. The live endpoint, its rate limiting, and a real campus's building count have not been exercised from here. A private endpoint is one setting (`PYPSAGUI_OVERPASS_URL`); terrain has the same (`PYPSAGUI_TERRAIN_URL`).
- **Drops on a slope** land short by height × tan(pitch): the drop ray still meets the flat plane y = 0 (accepted for Phase 1 in the plan, Task 4.1).
- **Buildings do not occlude clicks.** The context meshes have no pointer handlers (so the renderer never raycasts thousands of prisms on every pointer move); a click on a building face that visually covers an asset selects the asset behind it.
- **Pre-existing, unrelated:** `tests/test_packaging_requirements.py::test_the_spec_names_every_gridspine_module_the_backend_guard_imports` (the `.spec` lacks `gridspine.drivers.year_study`) fails on the base branch too; queued as a separate task.
