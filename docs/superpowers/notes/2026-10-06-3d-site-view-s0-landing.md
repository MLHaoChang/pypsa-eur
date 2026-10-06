# 3D site view — S0: landing the branch on master

Date: 2026-10-06
Scope: increment S0 of `docs/superpowers/plans/2026-10-06-visual-layers-3-3d-site-view.md` — merge `master` into `claude/3d-site-visualization-gatc5z`, repair, re-verify, open the PR. Run in a cloud container (Linux, no GPU, no macOS, Overpass not reachable), so the two hardware steps of S0 stay open and are listed at the end.

## 1. Merge

`origin/master` at `8c23cea` into the branch at `f2649e3` (forked 2026-09-28 from `6e9fd4b`; 375 commits behind). One merge commit, no rebase.

| Conflict | Resolution |
|---|---|
| `backend/routers/projects.py` `_BUNDLE_FILES` | union: `"sites.json"` (3D sidecar) **and** `"library_refs.json"` (Library pins, master) |
| `backend/routers/projects.py` `_BUNDLE_DIRS` | `("uploads", "reports", "sites")`; the branch's `_SAVE_WRITTEN_FILES` kept (the generalised `_carry_sidecars_on_move` body auto-merged and references it) |
| `pypsa-gui/CONTEXT.md` | both: the branch's **Site** / **Site results** entries, then master's **Assistant language** section |
| `frontend/package-lock.json` | master's lockfile, then `npm install` against the merged `package.json` (adds three, @react-three/fiber, @react-three/drei, @types/three and their 55 transitive packages) |

142 files auto-merged. Master's `_carry_sidecars_on_move` docstring still described two sidecars; the branch's generalisation (every `_BUNDLE_FILES` entry the save does not write itself, every `_BUNDLE_DIRS` entry) is what the merged tree runs, so `library_refs.json` and `reports/` are now carried on Save-As too. `test_save_sidecar_carry_seam.py`, `test_bundle_sidecars.py` and `test_library_bundle_pins.py` pass on the merged tree (§3).

## 2. Frontend

| Check | Result |
|---|---|
| `tsc -b` | clean |
| `vitest run` | 340 files, **3,871 tests, all pass** (branch close: 228 files / 2,490; the rest is master's) |
| `npm run build` | clean; `SiteCanvas` chunk **311.41 kB gzipped**, unchanged from the Phase 2 close; `spa` main chunk 993.76 kB gzipped (master alone: see §2a) |
| `dist/site3d/models/` | the five GLBs, README and licence present |

### 2a. Main-chunk budget

The branch's rule was +5 kB gzipped over its base for the main chunk. After the merge the main chunk carries master's growth as well, so the comparison is against a build of `origin/master` at the same commit with the same `node_modules`: PENDING.

## 3. Backend

Full `pytest` on the merged tree, frontend built: PENDING.

Two failures found and fixed before the full run: `test_sites_routes.py::test_context_route_rejects_traversal_id[..%2F..%2Fetc]` and `[a%2Fb]` answered **503** instead of 404. An id whose decoded form carries a slash matches no API route and falls to `main.serve_spa`, which treats every `/api/` path as a static asset (`static_gate._ASSET_PREFIXES`) and answers 404 — after first answering 503 when `dist/` is absent. The branch's QA ran with the frontend built; CI's backend job (`pixi run gui-tests`) does not build it, so the two cases would have failed there. The test now supplies a stub dist for its duration (`stub_dist` fixture, same contract as `test_serve_spa.local_spa_client`) and passes with `FRONTEND_DIST` pointing at nothing. Not a product change; whether an unknown `/api/` path should 404 regardless of the build is a separate question, not taken here.

## 4. Vocabulary

`CONTEXT.md` **Site** now states that a **campus study** (gridspine's electrical analysis, `routers/campus_electrical.py`) runs on a Site's member Buses and that neither word replaces the other; _Avoid_ reads "campus (unqualified)". Owner decision 2 of the parent assessment, taken as recommended.

## 5. Ledgered, not fixed here

`OPEN-ITEMS.md` items 12–14: the scratch-slot network reset (defects A–C of the branch's full-product E2E, data-loss class, patch and tests kept in `notes/2026-09-30-full-e2e/`), the review credential in `login.html`, and the three-port Link `bus2` drop. Defect D (template → 409) is already fixed on master by `_unique_project_name`.

## 6. Open, needs hardware or network this container lacks

- **Packaged macOS build** (`bash pypsa-gui/build-macos.sh`), then: open a campus project, draw a site, Open in 3D, confirm imagery, context, gizmo, a palette drop, playback; note frame rate and shadow quality on a real GPU. Nobody has seen the view outside SwiftShader.
- **One live Overpass fetch** for a real campus (NL or DE), with the public instance or `PYPSAGUI_OVERPASS_URL`.
- The branch's two headless Playwright drivers (Phase 1: 19 steps, Phase 2: 21 steps) were kept in the original session's scratchpad, not in the repo, and could not be re-run; the per-WP browser smokes under `frontend/src/site3d/*.test.ts*` and the full `vitest` suite are what was re-run here. Re-creating the drivers inside the repo (excluded from CI collection like the backend's `qa_*.py`) is a follow-up worth doing before S2 changes the packer.
