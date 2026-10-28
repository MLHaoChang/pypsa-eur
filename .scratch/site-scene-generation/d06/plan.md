# D06 plan: native target lifecycle identity

Status: proposed to GK B (plan checkpoint), not accepted.
Base: master `02e413808`.

## Design

A component's lifecycle identity is a random uid held in the network itself, so it moves atomically with every network edit, undo, save, Save-As, scenario copy and import, without a sidecar hook.

- **Identity (new `services/component_identity.py`, sole writer).** `n.meta["component_ids"] = {"uids": {Class: {name: uid}}, "retired": [uid, ...]}`.
  - `uid_for(n, cls, name)` mints a uid lazily, on the first binding of that component.
  - `rename(n, cls, old, new)` moves it.
  - `retire(n, cls, name)` drops it and appends it to `retired`.
  - `name_of(n, cls, uid)` resolves a uid to a name.
  - No eager stamping of every component, so packs, templates and imports are untouched.
- **Bindings (sites.json, additive).** A top-level `bindings: {"<Class>:<name>": uid}` records which component instance each placement or footprint key was authored against. The frontend store spreads `...doc`, so the key round-trips with no UI change. Validation: string keys of the `<Class>:<name>` shape, uid strings, a size cap.
- **Resolution (one function `resolve(doc, n)`).** It runs on GET `/sites` and on the scene GET, for the open project only. For each placement or footprint key with binding `u`:
  - `name_of(u)` is a live name: bound. If that name differs from the key, the entry is re-keyed in the projection (undo or redo of a rename).
  - `u` is unknown or retired: stale. The entry is hidden from the projection and listed in `unbound_keys`. It is never shown on, or inherited by, a same-name component. It stays on disk, so an undo of the delete brings it back.
  - No binding (a pre-D06 entry): legacy adoption, once. If a component of that name exists and holds no uid, a uid is minted and the binding is recorded. This keeps today's name behaviour for existing projects exactly once, at upgrade.
- **Stamping.** PUT `/sites` records a binding for each new key against the open network, minting a uid if needed. On-disk entries that are stale and absent from the body are carried forward, so an undo can still restore them. A key the client re-places for a recreated component takes a fresh binding.
- **Not the open project.** GET returns the stored document unchanged (today's behaviour). No binding decision is made without the network. SC-02 binds only against the open network.

### How each D06 gap closes

| Gap | Today | After |
|---|---|---|
| 1. Delete keeps the placement | A recreated `pv` inherits the position | `purge_component_side_data` retires the uid under the same lock as `n.remove`. The new `pv` has no or a fresh uid, so the old entry is stale. |
| 2. A failed hook keeps the footprint | Inherited | The sidecar hooks become cleanup only. Correctness comes from the uid, which changed atomically with the network. |
| 3. Undo has no sites hook | Wrong or missing key | Undo restores `n.meta`, so the uid resolves to the restored name. Undo of a rename re-keys and undo of a delete re-binds, with no hook. |
| 4. Import/replace, scenario copy, Save-As | Name-bound | A replaced network carries foreign meta or none, so every post-D06 binding is stale. A copy carries meta and sites together: the copy's component holds the same uid in a separate project. That is the copy mapping. |

## Alternatives considered

- **A sidecar-only generation ledger plus a sites snapshot in each undo entry.** It needs reliable hooks on every remove, replace and undo path, and an undo-stack change. It also still fails open on a hook error. Rejected.
- **A uid column on the component tables.** Every update path does a remove and add, the column leaks into tables and exports, and netCDF behaviour varies. Rejected.
- **Scope amendment (SC-02 binds by name and accepts aliasing).** The register says Class:name alone is insufficient. Rejected.

## Residual risk (recorded, not hidden)

- **Unsaved identity.** A uid minted when an existing component is first bound lives in the unsaved network. The mint marks the network dirty, so autosave or a save persists it. If the session is discarded instead, the placement shows as unbound on reopen: lost, never aliased. It stays on disk for an SC-02 re-bind offer.
- **Legacy adoption** is by name, once. A legacy project whose network was replaced before its first open after upgrade adopts by name, which is today's behaviour.
- **Removal paths that skip `purge_component_side_data`.** Examples are the vintage expansion and the solve copies. They do not retire uids. All of them remove generated or solve-copy components, and none is a placeable user component on the live network. I will list them in the closure.

## Hooks changed

- `network_crud.purge_component_side_data`: `identity.retire`.
- `network_crud._update_component` rename branch and `apply_rename_bus` (`routers/network.py`): `identity.rename`.
- `routers/projects.py` `get_sites`/`put_sites`: resolve and stamp.
- `routers/site_scene._unbound_keys`: uid-based.
- `site_service`: `bindings` validation. The rename and remove hooks re-key or drop the binding as cleanup.

`undo_service`, the import paths, Save-As and scenario copy need no change. Tests prove that.

## Tests first (public seams: HTTP via TestClient on a real project)

1. **Delete then same-name recreate.** The old placement and footprint are absent from GET `/sites` and listed in `unbound_keys`.
2. **Failed delete hook.** `write_sites` raises and the footprint stays on disk. The recreated `pv` still does not inherit it.
3. **Rename.** The binding follows. Undo restores the old key and redo the new one (via GET).
4. **Undo of delete.** The placement comes back bound.
5. **Import/replace** of a different network with a same-named component: stale.
6. **Save-As** and **scenario copy**: the copy's components are bound, and a delete plus recreate in the copy leaves the source unaffected.
7. **Legacy document** (no `bindings`): adopted once. A delete and recreate after adoption leaves the entry stale.
8. **PUT** stamps bindings and carries hidden stale entries forward. A validation failure on a bad `bindings` returns 422.
9. `gap_probe.py` rerun: both D06 lines flip.

## Coordination

- `services/site_scene/store.py` and the frontend belong to the SC-01 builder. I won't edit them: resolution wraps `project_sites` at the router. `bindings` is additive JSON that the frontend round-trips.
- No UI.
