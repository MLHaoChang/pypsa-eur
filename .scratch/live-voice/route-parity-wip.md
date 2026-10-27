<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Live-voice 08 slice A: work in progress (delete both WIP files before the PR)

GK B gave PLAN OK with conditions V1–V8 (see the hand-off note sent to the coordinator).

State:
- `pypsa-gui/frontend/scripts/api-bindings.mjs`: a TypeScript-checker scanner (needs `npm ci`), about 20 s.
  It finds 472 bindings and 3 unresolved sites (client.ts `'/api'` base, projectMismatch `'/api/'` prefix test,
  DocxPreview `fetch(url)` prop); these need `binding_overrides` (routes `[]` for the first two; DocxPreview
  fetches the upload blob, `GET /api/projects/{name}/uploads/{file_id}/blob`).
- `pypsa-gui/backend/smoke/assistant_route_parity.py`: imports the app (isolated app-data dir) and flattens
  routes through FastAPI 0.162 `effective_route_contexts()`, cross-checked against the OpenAPI schema. It
  matches like Starlette (dispatch order, first full match), raises V2 ambiguity only for earlier routes that
  shadow a GUI parameter, applies the V4 tier rule and the V3 `service_matches` allowlist (28 entries,
  reviewed), and flags stale declarations.
- Current counts: 443 GUI routes, 261 tool, 182 unclassified (listed in `route-parity-wip-unclassified.txt`).

Left for slice A:
1. Implement `acknowledged_shadows` (the declarations key exists, the code does not), or use per-binding
   `binding_overrides` with `url`+`method`, for about 16 ambiguity failures (projects/{} vs from_template,
   import-folder, unclaimed; chat/{sid}/… vs chat/answers/…; studies/{} vs studies/library; reports/{} vs
   capabilities/evidence_hash). These are pre-existing route-order shadows.
2. Two `no route matches`: network.ts `GET /network/{}` and `DELETE /network/{}/{}` (generic per-component
   helpers). Override them to the concrete component routes.
3. Classify the 182 routes: `limitations` (reason enum in `LIMITATION_REASONS`: admin, auth, chat settings,
   durable conversations, dictation, local settings, geocode, layouts) or `needs_tool` with a family. Fill
   `non_mutating_routes` for the preview POSTs (`*/preview`, `billing/preview`) once a tool covers them.
4. Write `.scratch/live-voice/route-parity.json`. In `assistant_control_matrix.py`, replace the blocker
   `ui-control-tool-parity-unaudited` with one blocker per needs-tool row read from that file, plus
   application-coverage.md.
5. Tests `tests/test_assistant_route_parity.py`, one per V1/V2/V3/V4/V5 form. Scanner tests run node on
   fixture dirs, skipped without node_modules unless `REQUIRE_TS_SCANNER=1`, which the workflow sets.
6. Add `.github/workflows/assistant-route-parity.yaml` (pixi + setup-node + npm ci) with the V7 path filter,
   then a release note, and confirm `capability-inventory.json` is unchanged (V8).
