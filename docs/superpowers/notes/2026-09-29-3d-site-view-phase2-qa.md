# 3D site view — Phase 2 end-to-end QA

Date: 2026-09-29
Scope: the QA stage of `docs/superpowers/plans/2026-09-29-3d-site-view-phase2.md` (§ "QA — end to end"), run on branch `claude/3d-site-visualization-gatc5z` after WP0–WP6 and their review-gate fix-ups (last code commit `7b6832d`).

## 1. Backend

Full `pytest` on a still tree at `7b6832d` (later commits touch docs only). Phase 2 changes no backend file (`git diff 4e9ecc0^..HEAD -- pypsa-gui/backend` is empty).

| Result | Count |
|---|---|
| passed | 6163 |
| skipped | 27 |
| failed | 2 |

(Counted from pytest's progress lines; this configuration prints no totals line.)

- `tests/test_packaging_requirements.py::test_the_spec_names_every_gridspine_module_the_backend_guard_imports` — the pre-existing failure the plan allows (`pypsa-gui.spec` lacks `gridspine.drivers.year_study`), as at the Phase 1 close.
- `tests/test_chat_sse.py::test_invalid_decision_returns_400_and_preserves_token` — the retry after the invalid decision answered **409** instead of 200. It **passes** alone, with its module (18/18) and with every `tests/test_chat*.py` module together (1169 passed), so another module earlier in the full run leaves state behind (a 409 is the lock / in-flight guard). It did not fail at the Phase 1 close. Nothing in Phase 2 touches the backend or the chat service, so it is recorded as a follow-up (§6) rather than fixed here; its root cause is not established.

## 2. Frontend

| Check | Result |
|---|---|
| `tsc -b` | clean |
| `vitest run` (full) | 228 files, 2490 tests, all pass — includes the `three`-import bundle guard (now listing `heroLoader.tsx` and `resultsLayer.tsx`), the no-CDN / no-decoder source guard and the timeline pin |
| `npm run build` | clean |
| `SiteCanvas` chunk separate | yes — 311.41 kB gzipped (Phase 1 close: 280.79 kB); three.js and its GLTF loader appear only there |
| Main `spa` chunk vs the Phase 1 close | 825.74 → 826.51 kB gzipped, **+0.77 kB** (budget 5 kB) |
| `dist/site3d/models/` | the five GLBs (~187 kB raw), `README.md` (source, version, the gltf-transform command) and `LICENSE-Kenney.txt` (CC0) |

## 3. Headless run

**Setup.** As Phase 1: backend in auth mode with a fresh SQLite database and two seeded admin users in one org, the Overpass stub, real Terrarium tiles and Esri imagery, Vite dev server, Chromium on SwiftShader through Playwright, canvas read through the gated debug hook. Run on an otherwise idle machine. The Phase 1 E2E driver ran first on the fresh stack (it builds `campus` and its site): **19/19** — Phase 2 changed none of its outcomes.

**Fixture.** `campus-qa`: the spike's Eemshaven campus over 8760 hourly snapshots (its 24-hour profiles tiled over 2030), plus an extendable battery (`BESS 2`: 10 MW installed, optimum constrained to 30–60 MW), a varying wind profile and a day/night grid import price, imported with the `campus` site document. The script is in the appendix.

| # | Step | Result | Evidence |
|---|---|---|---|
| 0 | Fixture imported, saved, site document attached | pass | 200 / 200 / 200 |
| 1 | Every component renders as its type (PV, wind, gensets, two BESS, data hall, electrolyser, transformers, feeder, switchyard) | pass | 13 objects |
| 1b | Hero models visible | pass | 9 instanced hero meshes |
| 2 | Solve started **from the header** (Run LOPF) | pass | optimal |
| 2b | "Sized: as built / optimised" appears once the dispatch is fresh | pass | |
| 3 | As built → optimised | pass | BESS 2: 20 MWh / 10 MW in 5 containers → 120 MWh / 60 MW in 30 containers (optimised) |
| 3b | Back to as built | pass | |
| 4 | Eye on → results show | pass | 11 objects with a state |
| 5 | 48 snapshots: a gauge, the spin, a glow and the loading each change | pass | distinct eased states over 48 steps — BESS 1: 21, BESS 2: 24, wind: 29, PV: 23, TR1: 21 |
| 5b | Only the results driver re-renders while playing | pass | SiteCanvas and every object mesh flat |
| 5c | The readout shows values | pass | "Onshore wind · 7.4 MW · 49 % of 15 MW", "TR1 110/33 · 42.6 MW → Campus 33kV · 36 % of 120 MVA rating", … |
| 6 | An edit makes the results vanish (component refetch or one poll) | pass | first empty frame 1.5 s after the PUT |
| 6b | Re-solve: **no frame shows the previous solve's values** (spec E12 rule 3) | pass | every frame sampled at 100 ms is empty until the whole map equals the new solve's (which differs from the old one); 0 frames of the old solve, 0 mixed |
| 7 | Reload a solved project (the simulation store is idle) → results show | pass | |
| 7b | An edit after the reload → results vanish within a poll (rule 1) | pass | 0.3 s |
| 8 | Model files blocked → the parametric scene, no hero, no page error | pass | |
| 8b | No CDN, decoder or `.wasm` request | pass | |
| 9 | Reduced motion: rotors still, gauges at their target at once | pass | angle constant; BESS 2 fill equals its SoC share on the first frame |
| — | Page errors | none | three sessions |

**21/21 steps pass.** The per-WP browser smokes (WP3 heroes and fallback, WP4 sizing flip on a re-solve, WP5 results map and isolation, WP6 rendering with material-level glow checks and reduced motion) also pass on the final tree.

**What QA itself found (all in the harness, none in the product).** The first run failed step 5: in the first fixture the wind had no time series and the grid import a flat price, so the batteries never cycled and the wind never changed speed. That left nothing for the gauges or the spin to show, and the check (any three of five objects) was too lax to say so. The fixture gained a wind profile and a day/night price, and the check now demands a gauge, the spin, a glow and the loading individually. The second run failed 6b falsely. The edited asset (the H₂ offtake) is off-site, and the electrolyser sat at its limit in both solves, so an unchanged value read as "stale". The check now compares whole maps, requires the new map to differ from the old one, and edits an on-site asset (the wind rating).

**Packaging (QA §4).** With `dist/` built, the backend's static route serves `/site3d/models/*.glb` (200, `model/gltf-binary`) and the licence (200). WebP textures are the models' only required extension; per the plan this is recorded as satisfied by the desktop app's minimum OS (macOS 14, whose WebKit decodes WebP) — not tested here. The macOS build remains a runbook step.

## 4. Review gates

Each work package had a review agent that had not written the code. It read the diff against the spec and the plan, ran the suites and a private-copy build, and sabotaged the tests. It also probed the running app. Findings were fixed (or answered in the commit message) before the next package.

| WP | Commit(s) | Gate outcome |
|---|---|---|
| WP0 groundwork | `4e9ecc0`, `bf93a2f` | tautological palette test → render-based submit test |
| WP1 asset library | `17b08bc`, `106a342` | blocker: part rotation applied twice; regex, footprint and rooftop fixes |
| WP2 merged meshes | `7b7c0cb`, `c4677df` | geometry rebuilt every build; flaky perf test |
| WP3 hero models | `4023d57`, `dbcdacc` | blocker: hero PV tables faced north; type tint lost on the textures; canopy tables on the ground; floating roof strip; 1.87 M triangles (tile cap: 289 k); tests that compared the code with itself |
| WP4 sizing | `7420803`, `7f75477`, `b398ee7` | no blocker; the post-solve list refetch would have looked like an edit → the settle signal |
| WP5 results data | `2393ac8`, `09fd745` | no blocker; major: reopening after a re-solve showed the previous solve's invalidated chunks → fixed and pinned by a browser repro; failed-refetch loop; one null series blanked all; load peak unlike the solver's |
| WP6 rendering | `1c2315e`, `7b6832d` | no blocker; majors: decorations not rebuilt on new anchors; readout covering the fit status |

## 5. Decisions made during implementation (recorded in the spec)

- E8: tank uses a per-axis box fit (a uniform scale cannot make the Kenney tank a 3 × 20 m bullet); at most 240 hero instances per object; PV tables cast no shadow; hero materials take the type's colour with the texture as shading.
- §6.1: a load's share is of its profile peak × the solver's own per-carrier, per-period factor (static loads unscaled).
- §6.3: "fresh" is per solve; the view is current only while the lists it read for that solve are unchanged; the first solve is trusted at once only when the cached status is recent.

## 6. Follow-ups (spec §8)

- The schematic looks transformer flows up in the Lines map; result chunks are not invalidated after an edit in the canvases; no bulk Link p1 endpoint.
- `SnapshotPicker`: no `aria-live` timestamp; playback ignores reduced motion.
- `pypsa-gui.spec` lacks `gridspine.drivers.year_study` (pre-existing test failure).
- A three-port Link loses `bus2` on creation (`network_crud._drop_unknown_extras`).
- The shared period-effective capacity ignores vintage `lifetime`; the vintage breakdown is not invalidated by a finished solve for the schematic.
- A component whose name contains "/" cannot be updated (405).
- `test_chat_sse.py::test_invalid_decision_returns_400_and_preserves_token` fails only in the full backend run (order-dependent 409; §1).
- Full-project E2E (§7): defects A–C (a network reset while a project is open lands in the scratch slot: undo, raw imports, snapshot restore, stale copies after template create and bundle import), with a proposed fix and regression tests. Defect D (a second project from a template → 409). The export dialog ignores Escape. `login.html` prefills a review credential. `CreationForm` labels are unassociated. Unbuilt templates look available.

## 7. Full-project browser E2E

Date: 2026-09-30. A UI-driven run across the whole product, beyond the 3D view, on the same stack (auth mode, fresh SQLite database, seeded users, Overpass stub, Vite, Chromium on SwiftShader). Each step is driven through the page as a user would: clicks, typing, file inputs and keyboard shortcuts. It fails on a thrown error, a page error, a console error, an HTTP status ≥ 400 outside a short allowlist (the chat-settings 403, the local-settings 404 and the first 404 for a site context that has not been fetched yet), or an error-boundary fallback. The API is read only to check what the UI claims. The bundled templates were built first with `backend/project_templates/_build.py`: `3bus`, `ieee14` and `ieee39` built. `belgium` needs `resources/test-elec/networks/base_s_5_elec_.nc`, which this checkout lacks.

**43/47 steps pass. The four failures are product defects (below), not the harness.** On the same fresh stack afterwards, the Phase 1 E2E ran PHASE1_RESULT and the Phase 2 QA driver ran PHASE2_RESULT.

| Area | Steps | Result |
|---|---|---|
| Auth | an unauthenticated `/app` shows the sign-in form; a wrong password is refused (401 "Invalid credentials"); sign in lands on the projects home | pass |
| Build by hand | blank project; two buses from the canvas toolbar; voltage edited in the properties panel; generator, line and load added through "Add to this bus" | pass |
| Undo | undo (toolbar) removes the last addition | **fail: defect A** |
| Save and solve | Ctrl+S; Run LOPF from the header → Optimal (objective 800); all 12 result tabs render (Capacity Expansion … Asset Detail) | pass |
| Import | a `.nc` imported from the projects home while another project is open | **fail: defect B** |
| Import | the same file after sign-out and sign-in (a fresh session): 4 buses, 24 snapshots; opens in the workspace | pass |
| Data | every component table lists its rows (Buses 4, Lines 1, Transformers 2, Generators 4, Storage 1, Stores 1, Loads 3, Links 1) | pass |
| Map and 3D | satellite and hybrid views place every bus; a site boundary drawn on the map → Open in 3D (9 objects, 6 heroes, context with 5 buildings, 4 lines, 3 areas and terrain, no missing tile) | pass |
| Panels | Project info, Workspace, Time series, Solver settings, Model horizon, Capacity bounds, Issues, Solve queue, Planning → dynamics, Snapshots, Scenarios: each opens without an error and closes | pass |
| Results | solve plus the solve queue; all result tabs for the campus; the results overlay on the schematic stepped with the timeline (1 of 24 → 2 of 24); the 3D site shows the results (8 objects with a state) | pass |
| Project life cycle | a snapshot saved; a scenario branched; the export bundle downloads (`.pypsaproj.zip`, 26.7 kB); a project cloned from the projects home (4 buses) | pass |
| Shell | command palette (Ctrl+K → Solver settings); light theme and compact density toggled and restored; a reload restores the project, its network and its results (fresh); the admin users page; sign-out gates the app again | pass |
| Templates | a project from the IEEE 14-bus template (14 buses, 20 lines) solves (objective 53 297) | pass |
| Templates | a second project from the same template | **fail: defect D** |
| Import | a `.nc` imported into the open project (Project data → Import) | **fail: defect C** |

### Defects found

**A. Undo reports success but changes nothing in auth mode.** After adding a generator, a line and a load, the toolbar shows "Undo last action (Ctrl+Z) · 7 steps available". Undo answers `200 {"undone":true,"remaining":6}`, yet the network still holds all three components. This reproduces through the API alone: activate a project, add a load, `POST /api/network/undo`, and the load is still there.

**B. A `.nc` imported from the projects home while a project is open is lost.** The wizard's "From file" tab imports the file (`POST /io/import/netcdf` → `200 {"buses":4}`) and then saves under the new name. The next request still sees the open project's network, so the new project is saved holding the **old** network (2 buses, 1 snapshot instead of 4 and 24). The file's contents are silently discarded. In a fresh session (no open project) the same flow works. Reproduced through the API alone.

**C. A `.nc` imported into the open project leaves its network unchanged.** From the workspace's Project data dialog, Import → a `.nc` answers 200 with the file's summary, but the project keeps its 14 buses and the header still says "14 buses".

**Root cause of A, B and C (one cause).** `PyPSAService.reset_network()` publishes the new, unbound network context into the session's `scratch:<sid>` slot (`services/pypsa_service.py`, `reset_network`). The next request resolves its context through the session's database pointer `sessions.active_project_id` (`services/active_project.py`, `resolve_for_session`). While that pointer is set, the request re-resolves the **project's** registry slot and never sees the scratch slot. `project_registry.bind_context` and `set_binding` do not move a context between slots; only save and rename call `PyPSAService.rekey_context`. The routes that reset the network while a project is open are therefore affected:

- raw imports: `.nc`, CSV, Excel and MATPOWER (`routers/io.py`);
- undo (`services/network_undo.py`, `apply_undo`);
- snapshot restore (`routers/snapshots.py`);
- template create and bundle import (`routers/projects.py`). These re-bind, but leave a stale bound copy in scratch, and when that copy is evicted it is written back over the project's saved edits. An agent confirmed this in a repro.

The backend tests miss it because the `install_network` fixture (`tests/conftest.py`) clears `active_project_id`, so every test runs with the pointer unset.

**Proposed fix (not applied).** The fix is 5 files, about +86/−14. It needs a decision because it changes backend routes, which Phase 2's rules exclude:

- after a successful raw import, clear the session's pointer, as `POST /api/network/reset` already does;
- after re-binding in undo, snapshot restore, template create and bundle import, call `PyPSAService.rekey_context(PyPSAService.get_active_context())` so the context lives under the project's key;
- the chat tools' import wrappers pass the acting session through.

Five regression tests exercise each route with a project active. They fail before the fix and pass after it, and 1830 related backend tests pass with it. Two further points are worth considering with the fix:

- undo and snapshot restore should refuse while that project is in the solve queue;
- `reset_network` carries `mutation_lock`, `undo` and `chat_state` forward into the new context.

**D. A second project from the same template fails with 409.** The templates tab calls `projectsApi.createFromTemplate(templateId)` with no name. The backend then uses the template's default name ("IEEE 14-Bus"), which already exists: `409 {"detail":"Project 'IEEE 14-Bus' already exists"}`, and a toast "Template import failed: Request failed with status code 409". The route already accepts `name`. The "From file" tab avoids the clash with `_uniqueProjectName`, and the templates tab could do the same. This is a small frontend fix.

### Other findings

- **The export dialog stays open after the download, and Escape does not close it.** Only Cancel does. Minor.
- **`frontend/public/login.html` prefills and prints a review account** (`admin@example.com` / `admin-pass-123`), and auth mode serves that page (`/login.html` passes the static gate). This predates Phase 2. If the page ships beyond review builds, the credential should go.
- **`CreationForm` labels are siblings of their inputs, not associated with them**, so they have no accessible name. The properties panel's add form wraps its inputs in their labels and is fine.
- **The template cards show as available even when a template's `network.nc` is not built.** The `.nc` files are gitignored and built by `build-macos.sh`, so a dev checkout offers templates that fail until `_build.py` runs.
- **The Scenarios panel lists every project in the org as a scenario forest.** This is by design, but it reads oddly when the projects are unrelated.

## Appendix — the QA fixture

```python
"""Phase 2 QA fixture: the spike's Eemshaven campus over 8760 hourly
snapshots (its 24-hour profiles tiled over 2030), plus an extendable
battery ("BESS 2": 10 MW installed, optimum forced into 30-60 MW) so the
"Sized: as built / optimised" switch has something to show, a varying
wind profile (so rotors change speed) and a day/night grid import price
(so the batteries cycle and their gauges move).

Usage: python make_campus_year.py campus.nc campus-year-qa.nc
"""
import math
import sys
import warnings

import pandas as pd
import pypsa

warnings.filterwarnings("ignore")
src, dst = sys.argv[1], sys.argv[2]
n = pypsa.Network(src)
day = len(n.snapshots)
year = pd.date_range("2030-01-01", periods=8760, freq="h")
tiled = {}
for comp in ("generators_t", "loads_t", "storage_units_t", "stores_t", "links_t"):
    for attr, df in getattr(n, comp).items():
        if hasattr(df, "columns") and len(df.columns):
            reps = -(-8760 // day)
            tiled[(comp, attr)] = pd.concat([df.reset_index(drop=True)] * reps, ignore_index=True).iloc[:8760].set_axis(year)
n.set_snapshots(year)
for (comp, attr), df in tiled.items():
    getattr(n, comp)[attr] = df
hours = pd.Series(range(8760), index=year)
wind = 0.15 + 0.7 * ((hours * 0.37).apply(math.sin) ** 2) * (0.6 + 0.4 * (hours / 24 * 0.9).apply(math.cos) ** 2)
n.generators_t.p_max_pu["Onshore wind"] = wind.clip(0, 1)
n.generators_t.marginal_cost["Grid import"] = year.hour.map(lambda h: 35.0 if h < 6 or h >= 22 else 140.0).astype(float).to_numpy()
n.add("StorageUnit", "BESS 2", bus="Campus 33kV", carrier="battery", p_nom=10, p_nom_extendable=True,
      p_nom_min=30, p_nom_max=60, max_hours=2, capital_cost=20_000, efficiency_store=0.95, efficiency_dispatch=0.95)
n.export_to_netcdf(dst)
print(f"{dst}: {len(n.snapshots)} snapshots, storage units {list(n.storage_units.index)}")
```

Run as `python make_campus_year.py campus.nc campus-year-qa.nc` against the spike's campus network.
