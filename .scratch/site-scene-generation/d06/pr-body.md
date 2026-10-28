Closes decision D06 of the Site 3D package (native target lifecycle identity), which unblocks SC-02. Plan verdict: GK B GO on option 1 (strict: a uid is never re-attached by name), with conditions 1–8. Closure record: `.scratch/site-scene-generation/sc-00-n/decisions.md` (D06); plan: `.scratch/site-scene-generation/d06/plan.md`.

## Changes proposed in this Pull Request

A 3D placement or footprint key (`<Class>:<name>`) now binds to one component instance, not to a name.

- **`services/component_identity.py`** (new, the sole writer). It holds a uuid4 per component in `n.meta["component_ids"]`, so the uid travels with the network through netCDF, undo, saves, Save-As and scenario copies.
  - **Mint:** only on a write (PUT), under the network lock.
  - **Rename:** moves the uid (`_update_component`, `apply_rename_bus`).
  - **Removal:** `purge_component_side_data` drops it.
  - **Clustering:** forgets every uid, because a cluster can reuse an old name.
- **`services/site_bindings.py`** (new). `sites.json` gets a top-level `bindings` map.
  - **GET `/sites` is read-only.** A bound entry follows its instance. An entry whose instance is gone is hidden on a legacy Site and listed in `unbound_keys`; it stays on disk, so an undo brings it back.
  - **Enabled Sites.** Their entries are never hidden here (D03), only listed. The scene GET's `unbound_keys` applies the same uid rule.
  - **PUT `/sites`.** It binds each unbound key, to the live component, or to a uuid no component holds. It carries hidden stale entries forward and gives each uid to one key only.
- **`site_service`.**
  - `bindings` validation returns 422 for a non-placeable key, a non-uuid, or one uid on two keys (compared in canonical form, so a case or hyphen variant is the same uid). A well-formed unknown uid is accepted and reads unbound.
  - The rename hook moves the binding after the scene hook, and not when a scene still shows the old key.
- **`tests/test_sites_routes.py`.** It now places an existing component, and saves before its snapshot and bundle checks (the loss window below).

**Behaviour to know.**

- **Legacy keys** (no binding) keep name semantics until the first PUT binds them.
- **The loss window (lost, never aliased).** Autosave runs every 5 minutes. A placement bound since the last save reads unbound after a scenario branch, a snapshot or bundle export, a Save-As (in the source project) or a discarded session. It stays on disk and listed; follow-up `.scratch/site-scene-generation/d06/follow-up-rebind.md` covers SC-02's explicit re-bind.
- **Every removal drops the uid.** `component_identity` wraps `pypsa.Network.remove` once on import, so the paths that skip `purge_component_side_data` (the vintage cleanup and rollback, the solve and adequacy copies, `network_global_constraints`) cannot leave a name→uid entry for a same-name re-add to inherit (GK B NOT OK item 3). `_update_component`, which edits by remove and re-add under the same name, puts the uid back (`component_identity.keep`), so an edit keeps its binding. The only other remove-and-re-add path, the GlobalConstraint update, is not a placeable class.
- **One lock order: network, then scene.** PUT `/sites` takes `PyPSAService.get_lock()` before the scene mutex when the project is the open one, the same order as the delete and rename hooks. The binding rename writes `sites.json` under the scene mutex (GK B NOT OK items 1 and 2).
- **API shape for SC-01b.** GET `/sites` may now carry `bindings` and `unbound_keys`. The UI's `...doc` spread round-trips `bindings`, and PUT ignores `unbound_keys`. No UI change is needed.

**Evidence.**

- `tests/test_site_identity.py` runs 24 tests through the real routes: rename and undo, delete and undo, delete then recreate, failed `sites.json` and scene hooks, replace then save, Save-As (copy isolation), scenario copy, legacy, "a GET never writes", 422s, the loss window, a key naming no component, fingerprint unchanged (normal and exact), the meta writers with a solve and a reload, clustering, the vintage orphan cleanup followed by a same-name re-add, an edit keeping its binding, and the lock order of PUT, rename and delete (asserted on order rather than raced, so a regression fails instead of hanging the suite).
- The first 11 tests were written first; 10 failed on master.
- Mutation checks: `.scratch/site-scene-generation/d06/evidence/mutation-checks.txt`. Resolving by name, carrying uids through clustering, or keeping the uid on delete each turns the delete-and-recreate and clustering tests red. For the GK B fixes: the old PUT lock order, an unlocked binding rename, no remove hook, a raw-string duplicate check, and an edit that does not put the uid back each turn their test red.
- `test_write_surface_lock_policy`, `test_route_chat_disposition`, `test_project_route_acl_policy` and the clustering suites: 32 passed, 1 skipped.
- On the master merge `fb7cec71`: `tests/test_site_*.py` and `tests/test_sites_routes.py` (389 tests), plus undo, bundle sidecars, workflow tools and `site_scene_proto` (61 tests), all passed.

**Skipped / risk.**

- There is no redo endpoint, so redo has no test.
- Snapshot restore and bundle import are covered by the existing route tests only.
- Another project's document reads as stored: the 3D view binds the open project only.
- A failed rename-binding write is logged. That binding reads unbound, never aliased.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated: written by Claude Code.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G7qCum27T2W3q9a7wP6PkY
