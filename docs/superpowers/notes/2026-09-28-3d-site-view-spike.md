# 3D site view — spike result

Date: 2026-09-28
Scope: Phase 0 of `docs/superpowers/assessments/2026-09-28-3d-site-view-feasibility.md` (§8a, "Spike scope implied by the decisions"). This note records what was built, what was verified and how, and what the spike taught that the spec should carry. It is not a design.

## What landed

A fourth canvas view, **Site 3D**, beside Blank / Satellite / Hybrid.

| Piece | Where | What it is |
|---|---|---|
| View switch | `frontend/src/store/uiStore.ts`, `components/MapModeSwitcher.tsx`, `App.tsx` | `CanvasView` gains `'site'`; the switcher a fourth segment; `App.tsx` renders `SiteCanvas` through `React.lazy` + `Suspense`, so three.js is a separate chunk (263 kB gzipped) that a user who never opens the view never downloads. The main bundle is unchanged. |
| Tangent plane + tiles | `frontend/src/site3d/geo.ts` | Local ENU frame at the bus (metres east/north), Web Mercator tile arithmetic, tile range and mosaic extent for a square around the bus, zoom choice. Pure; 11 tests. |
| Parametric layout | `frontend/src/site3d/layout.ts` | Every component attached to one bus → boxes sized from its PyPSA parameters (BESS containers from MWh, PV rows from MWp, turbines from MW, genset enclosures, electrolyser skids, H₂ bullets, data hall from MW, transformer bay from MVA, feeder bays, the switchyard itself), shelf-packed into zones around the switchyard. Emits the exact `{type, name}` the properties panel switches on, plus a per-asset land take. Pure; 11 tests including a no-overlap check. |
| Ground imagery | `frontend/src/site3d/imagery.ts` | Stitches the Esri World Imagery tiles the map view already uses into one canvas for the tile range (≤ 64 tiles, one retry per tile). Attribution string carried with it. Pure tile-URL part tested. |
| Scene decisions | `frontend/src/site3d/scene.ts` | Frame mapping (north = −Z), which bus is the site, and a fit-to-bounds camera for the real viewport aspect. 9 tests. |
| The canvas | `frontend/src/pages/SiteCanvas.tsx` | react-three-fiber scene: unlit imagery ground + transparent shadow catcher, one `<group>` per object with its boxes, hover label (drei `Html`), click → `setSelectedComponent`, selected object emissive-highlighted, pointer-miss clears selection, `OrbitControls`, bus picker, legend, land-take line, tile status, Esri attribution. No scene-side state beyond hover and camera. |

Frontend suite: 1984 → 2015 tests (31 new), all green. `tsc -b` clean. `npm run build` clean.

## What was verified, and how

The app was driven headless (Playwright against the Vite dev server and the FastAPI backend in local mode, Chromium on SwiftShader) with a fixture network: an Eemshaven-area campus with a 110 kV bus (wind, transformers, grid tie) and a 33 kV bus (two data-hall loads, gas gensets, PV, BESS, an electrolyser link to an H₂ bus with H₂ tanks).

- **Renders.** The 33 kV site shows the real Eemshaven imagery draped on the ground with the switchyard, transformer bays, genset enclosures, BESS containers, data halls and the PV rows placed around it; the objects cast shadows on the photo. (The wind turbines with long shadows visible in that capture are the real ones in the satellite photo, not models — the fixture's wind generator sits on the 110 kV bus.)
- **Every object selects the right component.** Each of the ten objects was projected to screen and clicked; the properties panel opened on the matching class and name every time (`GENERATOR | Gas gensets`, `LOAD | Data hall B`, `TRANSFORMER | TR1 110/33`, `LINK / HVDC | Electrolyser`, …). Clicking empty ground clears the selection.
- **A parameter edit regenerates the geometry with no new plumbing.** With the gensets selected, Edit Generator → `p_nom` 30 → 60 → Save in the existing properties panel changed the 3D hover label from "30 MW in 12 enclosures" to "60 MW in 24 enclosures" and the object's part count from 14 to 29. The scene re-derived itself from the invalidated query; nothing in the 3D view was told about the edit.
- **Empty state.** With no placed bus the view explains itself instead of rendering Null Island.

Not verified here: the packaged pywebview build (macOS only; the render path is plain WebGL2, which WKWebView has), and real-GPU shadow quality.

## What the spike taught (carry into the spec)

1. **Selection reuse is as cheap as the assessment claimed.** The whole "click a box → edit it → scene updates" loop is `setSelectedComponent` plus React Query invalidation. The 3D view owns nothing.
2. **Parametric geometry works as a data table.** The rules of thumb in `layout.ts` (MWh per container, ha per MWp, MW per enclosure…) are already isolated constants; the spec should move them to a data file so a product line or a regional norm is a data change.
3. **Framing must fit the viewport, not the site.** A fixed south-west vantage put half the site off the left edge whenever the canvas column was narrow (assistant dock + tab panel open leave ~440 px, portrait). The fit-to-bounds camera from the south, computed with the real aspect, is what made the objects clickable at all.
4. **Materials must not be patched from mapless to mapped.** Assigning `map` to a material compiled without one rendered the ground black on SwiftShader (the program cache key is only re-read on `needsUpdate`). The imagery material is keyed so it is a new material. Anyone adding textured meshes should follow that.
5. **Satellite imagery must be unlit.** Lighting a photo again darkens it and double-shadows it. Unlit ground + a transparent `ShadowMaterial` catcher gives correct shadows on the photo.
6. **Real-world scale bites early.** Four 5 MW turbines at 4 rotor diameters apart are a 1.7 km site; the tile range then jumps to z15 and 25 tiles, and the switchyard becomes a speck. The spec's site entity needs a boundary so the view frames the plot, not every asset the bus happens to own.
7. **Tile fetching is the slow part, not rendering.** 36 tiles at z16 through a proxy took 10–40 s; the backend site-context service in Phase 1 (fetch once, cache in the project) is the right owner of this, as the assessment said.
8. **Headless screenshots of WebGL lie on SwiftShader.** `page.screenshot` blanked the large textured plane while `canvas.toDataURL()` after a manual render was correct. Any future browser-driven check of this view should read the canvas, not the page.

## Not in the spike (Phase 1–2, per the assessment)

Site entity and boundary polygon, placement sidecar in the project bundle, drag-from-palette into 3D, moving objects, terrain and building footprints, results animation, hero models, offline caching, read-only gating of any editing.
