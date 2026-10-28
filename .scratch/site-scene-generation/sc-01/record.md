<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-01 implementation record

Status: SC-01a (backend) in review; SC-01b (UI) not started. Copied from [implementation-record-template.md](../implementation-record-template.md).

## Assignment contract

- **Unit / ticket.** `SC-01`, [issues/02-editable-native-scene.md](../issues/02-editable-native-scene.md), split in two PRs (GK B and UX plan GO, 2026-10-28):
  - **SC-01a**, this PR: backend only.
  - **SC-01b**: the UI. It opens after SC-01a merges, with its own UX placement note.
- **Base / branch.** Master `fe59c529` (= `f0f67080` + #805); branch `claude/site3d-sc01a`.
- **Exclusions in SC-01a.**
  - No UI.
  - No chat tool, and no `tool-error-kinds.json` kind: the scene kinds are route kinds, as D02 froze them.
  - No sources/exports routes (SC-06/SC-04), bindings (SC-02), Blender/photo engine or solver change.
  - No D06: it opens as its own unit before SC-02.
- **Files.**
  - New: `services/site_scene/` (the promoted contract, frames, anchor, store, native and verdicts modules, plus `proposals.py`) and `routers/site_scene.py`.
  - Changed:
    - `main.py`: mount and the `SceneError` handler;
    - `routers/flow.py`: the scene block;
    - `routers/projects.py`: `_BUNDLE_FILES`, `get_sites`/`put_sites`, `_copy_bundle_dirs`, `_project_bundle_bytes`;
    - `services/site_service.py`: the rename/delete hooks;
    - `services/not_via_chat.py`;
    - the regenerated `.scratch/live-voice/route-parity.json`.
  - The prototype modules under `tests/site_scene_proto/` are now aliases of production. `media_policy` and `assistant_context` stay prototypes.

## Plan-review conditions and the test that proves each

GK B (session `01G7qCujXTFZ…`):

| # | Condition | Proof |
|---|---|---|
| 1 | Project ACL on every route; the native edit lock before any read on every write; the per-project mutex across check and write | `test_site_scene_routes.py::test_a_caller_without_project_access_gets_404_on_every_route`, `::test_every_write_route_409s_under_a_foreign_lock_before_any_effect`, `::test_anchor_and_scene_writes_409_under_a_foreign_lock`; `test_project_route_acl_policy`, `test_write_surface_lock_policy`. Production has no viewer/editor role: access is `project_acl` yes/no (404, never 403), and read-only means another user's lock. Raised with GK B. |
| 2 | No scene or anchor field reaches a network input | `::test_anchor_and_scene_writes_leave_the_network_input_fingerprint` (`_network_input_fingerprint(exact=True)` and the `network.nc` bytes unchanged); `::test_generate_preview_then_apply_writes_no_network` |
| 3 | Error kinds classified | `contract.ERROR_STATUS`: one status per kind, and an unclassified kind raises (`test_site_scene_proto_isolation`). They are route kinds and never reach a `tool_error` frame, so `tool-error-kinds.json` is unchanged (D02) and `test_tool_error_kind_manifest` passes. |
| 4 | Proposal map: owner and project keyed, bounded by count, bytes and TTL; apply re-checks the hash and revision under the lock | `::test_a_cleared_map_answers_410`, `::test_apply_refuses_inputs_other_than_the_reviewed_ones`, `::test_another_projects_or_users_proposal_is_refused`, `::test_the_map_is_bounded_by_count`, `::test_the_map_is_bounded_by_age` |
| 5 | Generation pointers are plain ids on every read, copy and import; copies on every path | `test_site_scene_copies.py`: scenario, Save-As, snapshot and restore, export plus import, missing generation, a hostile `../x` (and 3 more) pointer, project delete |
| 6 | Rename/delete order; a failed scene write is visible | `test_site_scene_hooks.py::test_a_failed_scene_write_is_visible_not_silent`. The network edit runs first; the scene write follows in a new generation. On failure the old generation is kept, an error is logged and GET scene lists the key in `unbound_keys`. |
| 7 | `/flow` electrical keys byte-equal to master; the scene block never stale | `::test_flow_keeps_the_electrical_payload_and_shows_an_apply_at_once`; [evidence/flow-master-vs-branch.txt](evidence/flow-master-vs-branch.txt): the same sha256 on master `fe59c529` and on the branch, minus `scene` |
| 8 | The prototype tests run in CI against production | `pytest tests` collects all 126 (`--collect-only`: 20 + 5 + 24 + 19 + 22 + 32 + 4); the modules are aliases of `services.site_scene.*` |
| 9 | 413 on raw body size before parsing, both sides | `::test_an_oversize_body_is_413_before_parsing` |
| 10 | 8 concurrent applies, deterministic; losers name the committed revision | `::test_concurrent_applies_have_one_winner` (a `threading.Barrier`, no sleeps) |
| 11 | Policy tests and scripts at the trial merge | `test_route_chat_disposition`, `test_project_route_acl_policy`, `test_write_surface_lock_policy`, `test_not_via_chat`; `assistant_route_parity.py --check` (514 routes, 0 unclassified); `assistant_inventory.py --check` unchanged (341 tools) |

UX (session `01G7qCuipbbh…`):

| # | Condition | Proof |
|---|---|---|
| 1 | The `/flow` scene strings are the frozen table, with no second copy | `services/site_scene/verdicts.py` is the frozen SC-00 N table, moved, not copied. `test_site_scene_proto_flow_verdicts.py::test_entry_shows_one_label_per_state` pins all eight rule-3 rows, and the route tests read the served entry. |
| 2 | Server strings follow review-ux.md | Every `SceneError` message: a colon, no em dash, no quoted ids, "out of date" never "stale"; `proposal_expired` reads "This proposal expired: generate the site again" |

## Test-first evidence

- **RED.** [evidence/routes-red.txt](evidence/routes-red.txt): 30 of 30 route tests failed before any route existed. The unmounted GET answered the SPA catch-all's 503.
- **GREEN** (run from `pypsa-gui/backend`, Python 3.12):
  - `tests/test_site_scene_routes.py`: 41 passed;
  - `test_site_scene_copies.py`: 11;
  - `test_site_scene_hooks.py`: 3;
  - `test_site_scene_proto_*.py`: 126.

## Decisions taken in SC-01a

- **Proposals.** In memory, never persisted (GK B ruling b). Two actions:
  - `generate_native` replaces the buildings;
  - `edit_buildings` replaces the listed ids and keeps the rest.

  Apply rebuilds the buildings from the stored inputs against the current Site envelope under the scene mutex.
- **`/flow` scene facts.**
  - `saved` is true: a `/flow` exists only for a saved project. SC-01b renders "Save project first" for an unsaved one.
  - `can_edit` is false under another user's lock.
  - `targets` is the count of Loads, Generators, StorageUnits, Stores and Links.
  - `native_runs` is whether the open network has solved generator output.
- **The legacy rename hook** skips scene-enabled Sites' placements, and keeps re-keying or dropping the project-level `asset_footprints` record in sites.json. The scene's own copy is changed in its new generation.
- **A damaged scene** (a missing generation, a non-plain pointer) answers `integrity_error` (500) on scene reads, `GET /sites`, copies and export, through one app-level handler. A copy never falls back to the tree.

## Remaining and risks

- **SC-01b**, the UI:
  - the coordinate field;
  - the rule-3 entry;
  - the `FlowReturn` extension;
  - Generate, then correction, in Site 3D;
  - the sitesStore handover (debounce, unload, localStorage);
  - numeric controls;
  - screenshots per UX conditions 3–7.
- **D06** opens before SC-02. `unbound_keys` makes a failed hook visible, but identity is still name-keyed.
- **Undo.** Undo of a rename or delete does not reach the scene (D06 item 3).
- **Proposals and workers.** Proposals are per process: with more than one worker, a proposal served elsewhere answers 410.
- **Copies.** The scene mutex is per process, so copies and applies in two processes are not ordered. The desktop app runs one worker.
