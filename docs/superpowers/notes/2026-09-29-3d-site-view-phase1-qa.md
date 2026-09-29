# 3D site view — Phase 1 end-to-end QA

Date: 2026-09-29
Scope: the QA stage of `docs/superpowers/plans/2026-09-29-3d-site-view-phase1.md` (§ "QA — end to end"), run on branch `claude/3d-site-visualization-gatc5z` after WP1–WP5 and their review-gate fix-ups.

## 1. Backend

Full `pytest` on a still tree (commit under test: see §6).

_Pending: the full run on the committed tree is in progress._

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

_Pending: the clean run follows the backend suite (a first run under that suite's CPU load timed out in the browser)._

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
