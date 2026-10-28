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
  - `bindings` validation returns 422 for a non-placeable key, a non-uuid, or one uid on two keys. A well-formed unknown uid is accepted and reads unbound.
  - The rename hook moves the binding after the scene hook, and not when a scene still shows the old key.
- **`tests/test_sites_routes.py`.** It now places an existing component, and saves before its snapshot and bundle checks (the loss window below).

**Behaviour to know.**

- **Legacy keys** (no binding) keep name semantics until the first PUT binds them.
- **The loss window (lost, never aliased).** Autosave runs every 5 minutes. A placement bound since the last save reads unbound after a scenario branch, a snapshot or bundle export, a Save-As (in the source project) or a discarded session. It stays on disk and listed; follow-up `.scratch/site-scene-generation/d06/follow-up-rebind.md` covers SC-02's explicit re-bind.
- **Removals that skip `purge_component_side_data`.** These act on generated or copied components, or on non-placeable classes, so they retire nothing:
  - the vintage expansion and its rollback (`vintage_service`, `routers/vintage.py`);
  - the solve and adequacy copies (`ac_pf_service`, `solver/assumptions`, `solver/lost_production`, `adequacy/*`);
  - `network_global_constraints`.
- **API shape for SC-01b.** GET `/sites` may now carry `bindings` and `unbound_keys`. The UI's `...doc` spread round-trips `bindings`, and PUT ignores `unbound_keys`. No UI change is needed.

**Evidence.**

- `tests/test_site_identity.py` runs 21 tests through the real routes: rename and undo, delete and undo, delete then recreate, failed `sites.json` and scene hooks, replace then save, Save-As (copy isolation), scenario copy, legacy, "a GET never writes", 422s, the loss window, a key naming no component, fingerprint unchanged (normal and exact), the meta writers with a solve and a reload, and clustering.
- The first 11 tests were written first; 10 failed on master.
- Mutation checks: `.scratch/site-scene-generation/d06/evidence/mutation-checks.txt`. Resolving by name, carrying uids through clustering, or keeping the uid on delete each turns the delete-and-recreate and clustering tests red.
- `test_write_surface_lock_policy`, `test_route_chat_disposition`, `test_project_route_acl_policy` and the clustering suites: 32 passed, 1 skipped.

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

https://claude.ai/code/session_01G7qCujC1D8BcuFzWiA63t6
