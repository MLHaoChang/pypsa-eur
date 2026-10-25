<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PR #712 — optional asset footprint evidence

Implementation source: c117493f494a74d7d19d9a053286cb7f347730e9, master base aba252d2384f55116bbd15516a5b025951ba265e.

- Frontend final JSON: 884 passing tests, 89 files, zero failures. Covers Site layout/footprint/fit/placement/store, Properties panel, API, asset-write and UI-copy regressions. Counts overlap development runs; do not sum them.
- Backend final JUnit XML: 357 passing, one existing empty-parameter skip in test_write_surface_lock_policy, zero failures/errors. Covers footprint/service/authenticated sites HTTP/bundle/delete/cascade regressions and the three mandatory chat-tool semantic suites.
- TypeScript `tsc -b --pretty false`: exit 0 (empty tsc-final.log).
- Six screenshots and browser.json: real shared-editor browser checks with a synthetic intercepted sites API, Guided and Expert. Save/reload/reset/read-only succeed; no page errors. These are component/browser evidence, not authenticated full-application E2E or rendered-3D/device performance evidence.
- Advisory Spec and Standards review of this head found no remaining bounded blocker after lifecycle/rename/pending-reset, corrupt-sidecar and rooftop corrections. Official UX/GK approval is not claimed.

## Reproduction

Use configured test Python and frontend node_modules; no new dependency.

Backend, from pypsa-gui/backend:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest tests/test_asset_footprints.py tests/test_site_service.py tests/test_sites_routes.py tests/test_bus_cascade_side_data.py tests/test_write_surface_lock_policy.py tests/test_chat_tools_handler_dependencies.py tests/test_chat_tools_argshape.py --disable-warnings --junitxml=backend-final.xml
```

Frontend, from pypsa-gui/frontend:

```sh
node node_modules/vitest/vitest.mjs run src/layout/PropertiesPanel src/utils/assetWrite src/components/AssetFootprintEditor.test.tsx src/site3d src/components/SiteOverlay.test.tsx src/uiCopyGuard.test.ts src/api --maxWorkers=2 --reporter=json --outputFile=frontend-final.json
node node_modules/typescript/bin/tsc -b --pretty false
```

No paid model/media/weather calls, optional engine execution, production activation, reviewer contact or merge. Exact-head remote CI is separate evidence; consult the PR. No broad engineering/domain readiness claim.
