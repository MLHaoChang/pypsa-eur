<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced task D: opening the cost-reference card no longer throws away a solve

Branch: `outsource/library-reference-preview-read-only` · one PR against `master` · PR body starts with `Outsourced task D`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. The issue: `.scratch/harness/issues/22-library-reference-preview-is-a-write.md`.
3. `pypsa-gui/backend/routers/network_equipment_table.py`, the module docstring and `preview_router` (~l.81-90). This is the pattern you copy. `routers/network_document_values.py` (~l.83-92) did the same thing a second time.
4. `pypsa-gui/backend/main.py`:
   - the prefix sets `_UNDO_PREFIXES` (l.115), `_FOREIGN_LOCK_GATE_PREFIXES` (l.162), `_SOLVER_BLOCKING_PREFIXES` (l.318), `_STUDY_EDIT_PREFIXES` (l.377), `_DISPATCH_INVALIDATE_PREFIXES` (l.391);
   - in `undo_snapshot_middleware` (l.776): the undo snapshot (~l.1084), the dispatch probe (~l.1117), the revision bump (~l.1139) and the dispatch clear (~l.1170);
   - the preview mounts at l.1289-1297.
5. `pypsa-gui/backend/tests/test_equipment_table.py::test_et19_the_form_preview_is_a_read_and_its_card_is_the_one_the_apply_accepts` (l.481) and `tests/test_document_values.py::test_dv12_the_preview_route_is_a_read_and_its_card_is_the_one_the_apply_accepts` (l.395). These are your style models.
6. `pypsa-gui/backend/tests/test_library_reference_apply.py`, section "routes and the assistant tool, on the active network" (l.208-275). Your backend tests go here.

## Goal in plain words

The investment page has a cost-reference card. When the user picks a shipped cost reference for an asset, the frontend calls `POST /api/network/library_reference/preview` to get the old → new edit card. The preview only reads. It plans the change and builds the card on a copy of the network (`harness/edit_card.py::_sandbox_rows`).

But the route sits under `/api/network/`, so the middleware in `main.py` treats every preview as an edit:

- it pushes an undo snapshot (`_UNDO_PREFIXES`);
- it bumps the project's edit counter (`ProjectContext.network_revision`);
- if the network has solver results, it clears them and resets the simulation status to idle (`_DISPATCH_INVALIDATE_PREFIXES`).

So just opening the card throws away a solve and adds an undo step that changes nothing.

Fix: mount the preview outside `/api/network`, at `POST /api/library_reference/preview`, the way the equipment-table preview was moved to `/api/equipment_table/preview` (data-to-model PR-2, #581, commit `3728d8fa3`). The apply, `POST /api/network/library_reference/apply`, is a real write and stays where it is.

## User-facing outcome

- Opening or re-previewing the cost-reference card keeps the solve results, the undo stack and the "edited since solve" state as they were. Applying the card still clears results and adds one undo step, as today.
- **No UI change**: no new buttons, copy, layout or behaviour in the card.
- The assistant's `apply_library_reference` is unchanged (see "The assistant's path" below).

## Files to touch

- `pypsa-gui/backend/routers/network_library_reference.py`:
  - add `preview_router = APIRouter()` next to `router = APIRouter()` (l.21);
  - change the decorator on `preview_library_reference` (l.49) from `@router.post("/library_reference/preview")` to `@preview_router.post("/preview")`. Keep the function name, signature, body and its `user: User = Depends(require_user)` exactly as they are. `routers/network.py` l.199-202 re-exports the name;
  - leave `apply_library_reference_http` (l.77-80) on `router`, unchanged;
  - rewrite the module docstring (l.1-12) so it says what is true now. Model it on the last paragraph of `network_equipment_table.py`'s docstring: the apply is under `/api/network` (undo snapshot, foreign-lock gate, results invalidated); the preview (`preview_router`, mounted at `/api/library_reference`) is a read outside `/api/network` and `/api/io`, so it takes no undo snapshot, leaves the edit counter and the results alone, and needs no `_UNDO_EXCLUDE` entry.
- `pypsa-gui/backend/main.py`: directly after the document-values preview mount (l.1295-1297), add the same three lines in the same shape:

  ```python
  # The cost-reference card's preview of one shipped reference on one asset: a read too.
  from routers.network_library_reference import preview_router as _library_reference_preview  # noqa: E402

  app.include_router(_library_reference_preview, prefix="/api/library_reference", tags=["network"])
  ```

  Change nothing else in `main.py`. Leave `_UNDO_EXCLUDE` (l.118), `_DISPATCH_INVALIDATE_EXCLUDE` and every prefix tuple as they are.
- `pypsa-gui/backend/tests/test_write_surface_lock_policy.py`: add one `ROUTE_POLICY` entry for `("POST", "/api/library_reference/preview")`, directly after the `("POST", "/api/equipment_table/preview")` entry (l.248-252), with category `NOT_PROJECT_DATA` and a reason in the same words, e.g. "a POST only to carry a body (the asset, the reference and the confirmations): it plans the reference on the ACTIVE project's network, builds the edit card on a copy, and writes nothing. The write is `/api/network/library_reference/apply`, behind the foreign-lock gate." Without it, `test_every_write_route_has_a_lock_decision` (l.461) fails. That is expected: the route has left the gated prefixes.
- `pypsa-gui/backend/tests/fixtures/route_inventory_phase0.txt`: delete the row `POST /api/network/library_reference/preview … -> preview_library_reference_api_network_library_reference_preview_post` (l.187). Add `POST    /api/library_reference/preview    -> preview_library_reference_api_library_reference_preview_post` in sorted position, after the last `/api/library/…` row (l.146 today), with the file's column padding. Edit by hand. If you run `tools/openapi_diff.py --phase0-fixture` instead, keep only these two lines of its diff.
- `pypsa-gui/backend/tests/test_library_reference_apply.py`:
  - add `PREVIEW = "/api/library_reference/preview"` under `ROUTE` (l.211);
  - replace the four `f"{ROUTE}/preview"` calls (l.218, 239, 245, 256) with `PREVIEW`. Change nothing else in those tests; `ROUTE` stays for the apply calls;
  - add the new tests below.
- `pypsa-gui/frontend/src/api/libraryReferences.ts`:
  - l.124: `'/network/library_reference/preview'` becomes `'/library_reference/preview'`, still with `QUIET`;
  - l.4-6, the header comment: name the preview as `POST /api/library_reference/preview` (a read) and the apply as `POST /api/network/library_reference/apply`;
  - leave `apply` (l.125-126) unchanged.
- `pypsa-gui/frontend/src/api/libraryReferences.test.ts` (new): the path test below.
- `doc/release-notes.d/outsource-library-reference-preview-read-only.md`: the release-note fragment.

No frontend test or mock names the old path today: `src/pages/results/investment/LibraryReferences.test.tsx` mocks `libraryReferencesApi` as a whole (l.16-18), and there is no MSW handler for it. It must pass unchanged.

## Access checks (must stay the same)

The moved route keeps exactly the checks it has today. None of them depends on the path:

- route level: `user: User = Depends(require_user)` (`deps.require_user`, `deps.py` l.121), which raises 401 without a user. Keep it on the handler;
- app level: `Depends(bind_active_project)` (`deps.bind_active_project`, registered for every route at `main.py` l.664). It binds the request to the session's active project through `services/active_project.py::resolve_for_session`, which calls `_authorized_active_project` (l.54). That re-checks `project_acl.can_access_project` on every request and falls back to the session's own scratch network on denial. The preview reads only that bound network (`reference_apply.plan_active`, `PyPSAService.get_network()`);
- middleware: the auth check (401 for every non-public `/api` path, `main.py` ~l.806-848) and the CSRF check (~l.797-804) cover every `/api` write, whatever the prefix.

Moving out of `/api/network/` drops only the write gates: the solver-in-flight gate (~l.927), the live-study gate (~l.956) and the foreign-lock gate (~l.985). The equipment-table and document-values previews already run without them, and the `ROUTE_POLICY` entry above records why. Do not add a new dependency, a project parameter or a lock check to the preview.

## The assistant's path must keep working

The chat tool `apply_library_reference` never calls the HTTP preview. Its confirmation card is built in-process: `harness/loop.py` l.2635 calls `edit_cards.preview(tool_name, args)`, which runs `harness/edit_card.py::_library_reference_preview` (l.673, registered at l.709). After the user confirms, `services/chat_tools.py::apply_library_reference` (l.1685) calls the router's `apply_library_reference` handler directly. Neither path changes. Check that by:

- `tests/test_library_reference_apply.py::test_the_tool_is_a_network_edit_with_a_card` (l.263) passing unchanged;
- `tests/test_live_network_untouched.py` passing (it lists `apply_library_reference` in `_WRITE_ARGS`, l.660);
- `tests/test_write_surface_lock_policy.py` and `tests/test_chat_tools_endpoint_map.py` passing. `TOOL_ROUTES` in `harness/catalogue.py` l.4668 maps the tool to the apply route only, so it needs no change.

## Tests

### Backend, in `tests/test_library_reference_apply.py`

Add a small helper next to `_net()`, e.g. `_solved_net()`: `_net()` plus seeded dispatch, with `generators_t.p` for `["wind", "pv"]` and `links_t.p0` for `["electrolyser", "poc"]` on `n.snapshots`, so that `services.dispatch_status.dispatch_status(n) == "fresh"`. Copy the pattern from `tests/test_dispatch_status_transient.py::_solved_net` (l.21).

Tests 1 and 2 take `monkeypatch` and set `undo_service.claim_push_slot` to `lambda *a, **k: True` (`from services import undo_service`), as `tests/test_project_switch_isolation.py` l.224 does. Without it the middleware skips a snapshot that falls within 0.5 s of the previous one (`COALESCE_WINDOW_S`). Test 2's `depth + 1` would then depend on timing, and test 1 could miss an unwanted snapshot.

1. `test_the_preview_is_a_read_outside_the_network_prefixes(client, anon_client, install_network, session_ctx, monkeypatch)`, modelled on `test_et19`:
   - `PREVIEW` starts with none of `main._UNDO_PREFIXES`, `main._DISPATCH_INVALIDATE_PREFIXES`, `main._STUDY_EDIT_PREFIXES`;
   - `n = install_network(_solved_net())`; assert `dispatch_status(n) == "fresh"`. Keep a copy of `n.generators_t.p` and `n.links.at["electrolyser", "overnight_cost"]`;
   - read `depth = client.get("/api/network/undo/info").json()["depth"]`, then `revision = session_ctx(client).network_revision`;
   - `client.post(PREVIEW, json=AEC)` is 200 and returns a card for `apply_library_reference`. Also post the `pv` body without confirmations (it returns `needs` and no card): that is a read too;
   - after both calls:
     - the undo depth equals `depth`;
     - `session_ctx(client).network_revision == revision`;
     - `dispatch_status(n) == "fresh"`, and `n.generators_t.p` equals the copy (`pd.testing.assert_frame_equal`);
     - `overnight_cost` is unchanged;
   - `anon_client.post(PREVIEW, json=AEC).status_code == 401`;
   - the old path is gone: `client.post(f"{ROUTE}/preview", json=AEC).status_code in (404, 405)`. Make this the test's last request: it still runs through the `/api/network` middleware.

   It fails on `master`: the new path does not exist there, and through the old path the revision bumps and dispatch is cleared.
2. `test_the_apply_is_still_an_edit_that_clears_results(client, install_network, session_ctx, monkeypatch)`:
   - same setup;
   - preview through `PREVIEW`, then `client.post(f"{ROUTE}/apply", json={**AEC, "digest": card["digest"]})` is 200 with `receipt.outcome == "verified"`;
   - after it, the undo depth is `depth + 1`, `network_revision > revision`, and `dispatch_status(n) == "none"`.

   This pins that only the preview moved. It fails on `master` because `PREVIEW` is not a route there (404 or 405).
3. The three existing route tests (l.216, 236, 254) pass with `PREVIEW` in place of the old path, assertions unchanged.

### Frontend: `pypsa-gui/frontend/src/api/libraryReferences.test.ts` (new)

Model it on `src/api/commercial.test.ts`: mock `./client` as `{ default: { get, post } }`, then `await import('./libraryReferences')`.

4. `preview` posts to `'/library_reference/preview'` with the args and `{ skipErrorToast: true }`, and `apply` still posts to `'/network/library_reference/apply'`. It fails on `master`, whose preview path is `'/network/library_reference/preview'`.

### Commands

From the repo root; install once with `pixi install --frozen -e default -e test`:

```bash
pixi run gui-tests tests/test_library_reference_apply.py tests/test_write_surface_lock_policy.py tests/test_project_route_acl_policy.py tests/test_chat_tools_endpoint_map.py
pixi run gui-tests tests/test_equipment_table.py tests/test_document_values.py tests/test_live_network_untouched.py
pixi run gui-tests   # full backend suite before opening the PR
cd pypsa-gui/frontend && npx vitest run src/api/libraryReferences.test.ts src/pages/results/investment/LibraryReferences.test.tsx && npx tsc -b
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so test paths are relative to that folder.

## Out of scope / don't touch

- `pypsa-gui/backend/harness/catalogue.py` (open PRs edit it), `harness/edit_card.py`, `harness/loop.py` and every other `harness/` file.
- `pypsa-gui/backend/services/chat_tools.py`, `services/library/*` and `routers/network.py`. Its re-export of `preview_library_reference` (l.199-202) stays valid as it is.
- In `main.py`: `_UNDO_EXCLUDE`, `_DISPATCH_INVALIDATE_EXCLUDE`, every prefix tuple and every middleware block. The only change there is the new mount.
- The apply route `POST /api/network/library_reference/apply`, its handler and its tests.
- Campus files: `routers/campus_electrical.py`, `services/campus_*.py`, `frontend/src/**/campusElectrical*` and `CampusElectricalPanel*`, and their tests.
- Anything under `pypsa-gui/frontend/src` except `src/api/libraryReferences.ts` and the new `src/api/libraryReferences.test.ts`. No UI change: no component, copy, CSS or layout edits.
- `pypsa-gui/tool-error-kinds.json` (no new error kinds), `scripts/coord/*`, `docs/agents/*`, `doc/release_notes.md`.

## Repo rules

- Branch `outsource/library-reference-preview-read-only` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body starts with the line `Outsourced task D`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated.
- Add one release-note fragment, `doc/release-notes.d/outsource-library-reference-preview-read-only.md`, containing one bullet in this form:

  ```
  * Fix(pypsa-gui): Previewing a shipped cost reference on an asset no longer clears solve results, adds an undo step or marks the network edited; the preview moved to `POST /api/library_reference/preview`, and the apply stays at `/api/network/library_reference/apply`.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests and docs go in the same PR. Do not skip, loosen or delete any existing test. Existing test files change in these places only:
  - `tests/test_library_reference_apply.py`: the `PREVIEW` constant, the four path swaps, the `_solved_net()` helper and tests 1–2. Make no other change to its existing tests.
  - `tests/test_write_surface_lock_policy.py`: the one `ROUTE_POLICY` entry.
  - `tests/fixtures/route_inventory_phase0.txt`: the one removed row and the one added row.

  Every other existing test file, including `test_equipment_table.py`, `test_document_values.py`, `test_live_network_untouched.py`, `test_chat_tools_endpoint_map.py`, `test_project_route_acl_policy.py` and `LibraryReferences.test.tsx`, stays byte-for-byte unchanged.
- Do not commit credentials or `.env` files. Make no paid model calls.

## Done when

- [ ] `POST /api/library_reference/preview` returns the same body as the old route did. `POST /api/network/library_reference/preview` no longer exists. The apply route is unchanged.
- [ ] The two new backend tests and the frontend path test pass, and all three fail on `master`'s code.
- [ ] Every other test in `test_library_reference_apply.py` passes with only the path swap. `test_write_surface_lock_policy.py`, `test_project_route_acl_policy.py`, `test_chat_tools_endpoint_map.py` and `test_live_network_untouched.py` pass.
- [ ] `LibraryReferences.test.tsx` passes unchanged, and `npx tsc -b` is clean.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the files listed under "Files to touch".
- [ ] The release-note fragment is present.
- [ ] One PR on `outsource/library-reference-preview-read-only`, with a body starting `Outsourced task D`.
