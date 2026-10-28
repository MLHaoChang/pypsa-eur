<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-00 N decision closures

Status: **proposed** for D01–D05, D08, D11, D12. Each becomes accepted only through the GK B and UX OK on this PR's merged head; no closure here is self-accepted. D06 stays **open** and blocks SC-02. D07 (SC-03), D09 (B) and D10 (P) are not N decisions.

Source base: master `60bf3a4bb41f2acbe486f43adefdbe8ea6b4b343`. Spec revision: the `.scratch/site-scene-generation/` package as merged on that master (#711, plus #785's ticket Status line). Plan GO: UX (session `01G7qCujyq1J…`, with four conditions, folded in below) and GK B (session `01G7qCujuZX7…`, seven conditions, folded in). Prototype: `pypsa-gui/backend/tests/site_scene_proto/` (imported by no production module; `test_site_scene_proto_isolation.py`). Commands are run from `pypsa-gui/backend` with the repo's Python 3.12 environment; evidence files sit in [evidence/](evidence/).

## D01: native confirmed site anchor

- **Observed.** No project-level coordinate exists. `/flow`'s `site_location_missing` (`services/flow_service.py`, `_presence`) is derived from Bus x/y. Each legacy Site has its own `origin` in sites.json (`services/site_service.py`, `validate_sites`). Coordinates are entered today only as Bus x/y (`CreationForm`, `UnplacedBusesPanel`).
- **Alternatives.** (a) A top-level `anchor` key in sites.json: rejected. `PUT /api/projects/{name}/sites` (`routers/projects.py`, `put_sites`) carries no revision, and `sitesStore` writes back `{...doc}` from its cache, so a stale tab silently reverts an anchor kept there. (b) Bus x/y or `n.meta`: forbidden (no dummy Bus; the 3D design rejected `n.meta`). (c) **Chosen:** a revisioned sidecar.
- **Frozen contract.** `<project>/site_anchor.json` = `{version: 1, revision: int, anchor: {lat, lng, source: "manual"|"geocode", confirmed: bool} | null}`. `GET /api/projects/{name}/site-anchor` (viewer) returns that document (revision 0 and anchor null when the file is absent). `PUT …/site-anchor` (editor, lock-checked) takes `{anchor, expected_revision}` and writes atomically (`services.atomic_io.atomic_write_text`) only when `expected_revision` equals the stored one. Otherwise it returns 409 `revision_conflict` with `{current_revision}`.
  - Validation, all 422 `invalid_input` naming the field: finite numbers, lat −90…90, lng −180…180, a known source, and a boolean `confirmed`. A latitude out of range whose longitude would be a valid latitude adds `hint: "swapped"`. It is refused, never swapped or clamped. Explicit confirmed (0, 0) is valid. `confirmed: false` is stored as a draft and admits nothing.
  - One anchor per project. Each Site's `origin` stays fixed from Site creation; moving the anchor never moves a Site. `/flow` reports `anchor_moved` for a Site whose origin is over 1000 m from the anchor.
  - SC-01 adds the file to `_BUNDLE_FILES`, because an anchor is not private.
- **Evidence.** `tests/test_site_scene_proto_anchor.py` (20 cases: NaN/±inf/bool/string, each range edge both sides, swapped, ambiguous valid pair, confirmed 0/0, bus/None source, CAS stale and missing revision, sidecar-only write). The disabled-entry state is `test_site_scene_proto_flow_verdicts.py::test_the_disabled_create_links_to_the_coordinate_field_in_both_modes`.
- **Downstream.** SC-01 mounts the two routes, adds the coordinate field to the Project & site card (Guided) and the `horizon` panel (Expert), and adds `_BUNDLE_FILES`. ACL is the existing project dependency, not proven by a mounted route in N.

## D02: public API, flow and tool contract

- **Observed.** On master, `app.openapi()` has `GET/PUT /api/projects/{name}/sites` and `…/sites/{site_id}/context|thumbnail` and no scene routes.
- **Frozen contract.** `site_scene_proto/contract.py`:
  - **Route table** (`ROUTES`: method, path, least role, lock check, chat disposition). Site anchor GET/PUT; `GET …/sites/{site_id}/scene`; `POST …/scene/adopt`; `POST …/scene/proposals`; `GET …/proposals/{id}`; `POST …/proposals/{id}/apply`; `POST …/proposals/{id}/cancel`. Every chat disposition is `none` until SC-07.
  - **Deferred routes** (`DEFERRED_ROUTES`): sources (SC-06, after D05 runtime proof) and exports/artifacts (SC-04).
  - **Error kinds.** Each kind has exactly one status (`ERROR_STATUS`); the wire shape is `{detail: {error_kind, message, …}}`, as the project routes already raise. `not_found` 404, `forbidden` 403, `project_locked` 409, `invalid_input`/`invalid_geometry` 422, `revision_conflict` 409, `scene_managed_site` 409, `source_unavailable` 410, `worker_unavailable` 503, `resource_limit` 413, `proposal_expired` 410, `proposal_cancelled` 409, `integrity_error` 500. An unclassified kind raises. These are scene-route kinds; `pypsa-gui/tool-error-kinds.json` is unchanged.
  - **Numeric null contract** (V57). `quantity()` returns `{value: null, available: false, reason, unit}` or `{value, available: true, reason: null, unit}`; a resolved 0 stays 0.
  - **Scene `/flow` verdicts** (`flow_verdicts.py`). An additive `scene: {entry, actions}` with eleven actions, each giving availability, `blocker {code, reason, link: "›", home}`, required inputs and what is computed. Electrical keys keep their values.
  - **Existing equipment geometry.** #712's sites.json `asset_footprints` record is it: native proxies (`native_scene.equipment_proxies`) reuse `width_m`/`depth_m`/`height_m`. A missing `height_m` is `quantity(None, "m", "height_not_set")`, never a guess. There is no second footprint store or route.
- **Evidence.** `test_site_scene_proto_isolation.py`: no frozen route is mounted or collides in `app.openapi()`; every kind has one status; the null contract. `test_site_scene_proto_flow_verdicts.py`: the real `GET /flow` is byte-equal before and after writing an anchor and adopting a Site on an empty network, and has no `scene` key yet.

## D03: generation, CAS and copy lifecycle

- **Observed.** sites.json is written whole by `site_service.write_sites` (atomic, not durable: `services/atomic_io.py` deliberately has no fsync). The rename/delete hooks are `rename_component_on_disk`/`remove_asset_footprint_on_disk`, called from `services/network_crud.py`. Copies are recursive: `_copy_bundle_dirs` for Save-As, scenario and snapshot, and `_project_bundle_bytes` for export (`routers/projects.py`, `_BUNDLE_DIRS = ("uploads", "reports", "studies", "sites")`).
- **Frozen contract** (`scene_store.py`).
  - **Layout.** `sites/<id>/scene/current.json` is the only commit point: `{version, revision, generation, sha256, last_receipt}`. Immutable `generations/<gen>/generation.json` holds the complete Site envelope, buildings, owned `asset_footprints`, parent and receipt.
  - **Commit.** Under the project lock: check the expected revision; write the generation and fsync the file and its directory; write the pointer to a temp file, fsync, `os.replace`, fsync the directory. Platform note: Windows cannot fsync a directory, but `os.replace` is still atomic within a volume.
  - **Adoption.** Explicit and digest-checked. It snapshots the exact legacy Site plus the footprints of its placed equipment, and does not rewrite sites.json. A GET never adopts.
  - **Apply.** Takes `{id, base_revision, input_hash, changes ⊆ {buildings, placements, asset_footprints}}` and returns the receipt. Re-applying an id returns the committed receipt. The same id with different inputs is refused. A stale expected or base revision is 409 `revision_conflict`.
  - **Integrity.** A pointer/hash mismatch is `integrity_error`.
  - **Legacy projection.** `project_sites` builds GET /sites from the generation for enabled Sites, in the same shape `validate_sites` accepts.
  - **Stale-save refusal.** `guard_legacy_put` refuses a whole-document PUT that changes or drops an enabled Site or a footprint that Site owns (`scene_managed_site`). The enabled set is the backend's pointer set; a client `scene_enabled` flag is ignored.
  - **Rename/delete re-keying** happens inside a new generation; sites.json is never edited behind the pointer.
  - **Copy closure.** The pointer plus the one pointed generation, read and copied under the lock. Orphan, superseded, failed and cache files are excluded. A missing generation fails with `integrity_error` and leaves the destination untouched.
- **Evidence.** `test_site_scene_proto_generation.py` (22 cases):
  - crash injected before commit: the old state is kept and the orphan is excluded from the closure;
  - crash after commit: the receipt is read back and nothing is re-applied;
  - 8 concurrent applies on one revision: exactly 1 wins and 7 get `revision_conflict`;
  - four stale legacy-PUT shapes are refused;
  - rename/delete re-keying; sites.json bytes unchanged.

  `test_site_scene_proto_closure_privacy.py`: closure-only copy, a missing artifact, and an apply racing a copy that cannot mix generations.
- **Recorded gap** ([evidence/gap-probe.txt](evidence/gap-probe.txt)). Today's `_copy_bundle_dirs` copies an orphan generation (`g000009-orphan`) that the closure excludes. SC-01 must route enabled Sites in snapshot, scenario, Save-As and export through the closure builder. In N, no Site is ever enabled in production, so nothing is exposed today.

## D04: native proposal execution and limits

- **Observed (gap, fixed here).** `SolveQueue._run_job` sent every kind other than gridspine/campus/profile_gen to `_run_solve_job`. `enqueue`/`enqueue_unique` accepted any string. `solve_job_store.reconcile_on_boot` re-admitted any queued row. RED: [evidence/d04-red.txt](evidence/d04-red.txt) shows `scene_native` dispatched to the solve path and resumed at boot as `(0, 1)`.
- **Fix (the PR's one production change).** `services/solve_queue.py` adds `UNKNOWN_KIND_CONDITION = "unknown_job_kind"` and `check_kind`:
  - `enqueue`, `enqueue_unique` and `restore` raise `ValueError`;
  - `_run_job` solves only `KIND_SOLVE` and finishes any other kind as `failed` without solving.

  `services/solve_job_store.py`: `reconcile_on_boot` marks an unknown-kind queued row `failed` with condition `unknown_job_kind` in the persisted store, and does not count it as resumed. A row with no kind still defaults to `solve`. `unknown_job_kind` is a job error/condition string, not a tool error kind.
- **Execution choice.** Native generation runs bounded in the request, with no queue adapter and no new job kind. Measured on this container ([evidence/d12-native-budgets.json](evidence/d12-native-budgets.json)), the worst admitted request generates in ≤ 290 ms (max of 5 runs). Apply of 2000 buildings takes ≤ 160 ms.
- **Frozen limits** (both sides tested in `test_site_scene_proto_geometry.py`):

  | Limit | Value | Basis |
  |---|---|---|
  | Buildings per request | 2000 | |
  | Footprint vertices | 256 | |
  | Triangles | 200 000 | Prism = 4n − 4 |
  | Preview bytes | 4 MiB | = `MAX_SITES_BYTES` |
  | Segment pairs | 250 000 | Bounds the O(n²) crossing check: 0.9 µs/pair measured. An unbounded 196 × 256-gon request took 5.6 s before this limit. |

  Each limit is refused as 413 `resource_limit` with `{limit, max, actual}`. Cancellation is moot for an in-request preview, because nothing persists before Apply.
- **Evidence.** `tests/test_solve_queue_unknown_kind.py` (7 tests, including a real store row through `reconcile_on_boot`). Regressions: the 24 existing queue/store/shutdown test files plus the new one, each run separately, all pass ([evidence/d04-queue-regressions.txt](evidence/d04-queue-regressions.txt)).

## D05: private-media ownership and retention (policy; upload stays disabled)

- **Observed.** `Organization.id` and `Project.id` are UUIDs (`db/models.py`). Flat projects (`app_paths.default_flat_projects_root`) have no registry row. Every project directory under `uploads/ reports/ studies/ sites/` and the history directories is recursively copied or exported.
- **Frozen policy** (`media_policy.py`).
  - **Store.** `<app data>/private_media/<org uuid>/<project uuid>/<media uuid>`, refused if it falls under any projects root. A flat project has no owner identity, so it is refused (`forbidden`, `no_owner_identity`) with no shared fallback.
  - **Scene state** holds only `{source_id, kind, state, reason}`.
  - **Reads.** Every read reauthorizes the handle. A foreign project or revoked access gives `not_found`; deleted or expired gives `source_unavailable` with its reason.
  - **Retention and quotas.** Retention is 90 days. Quotas: 25 MiB per file, 50 MP, 200 files and 1 GiB per project. The quotas are policy values for SC-06 to enforce and measure at upload; N measures no upload path because none exists.
  - **Copies.** Snapshot and restore keep the handle. Scenario, Save-As, bundle export and import mark the original `unavailable: original_not_copied`.
- **Evidence.** `test_site_scene_proto_closure_privacy.py` runs the real `_project_bundle_bytes` and `_copy_bundle_dirs` over a project whose original holds marked bytes, an EXIF marker, an original filename and its private path. None appears in the zip, the unpacked zip, the copy or the generation.
- **Downstream.** SC-06 enables upload only with these limits enforced and a runtime proof; the quotas need measuring there.

## D06: native target lifecycle identity

Status: proposed closure on branch `claude/site3d-d06` ([plan](../d06/plan.md)). Accepted only through GK B's OK on that PR's merged head. SC-02 stays blocked until then.

### Trace on master (unchanged record)

Traced on master ([evidence/gap-probe.txt](evidence/gap-probe.txt)):

1. **Delete.** `network_crud.purge_component_side_data` calls `site_service.remove_asset_footprint_on_disk`. It drops `asset_footprints[<Class>:<name>]` but not `placements[<Class>:<name>]` in any Site, so a recreated same-name component inherits the old position and heading.
2. **Best-effort hooks.** The `_on_disk` hooks swallow `OSError`/`ValueError`. After a failed delete hook, the footprint survives and a same-name recreation inherits it.
3. **Undo.** `services/undo_service.py` restores network state with no sites.json hook, so keys can point at the wrong or a missing component after an undo of a rename or delete.
4. **Other paths.** Network import/replace leaves sites.json keys bound to any same-named component. Scenario and Save-As copy names verbatim. No identity generation exists anywhere.

The prototype's generation-scoped re-keying (`scene_store.rename_component`/`remove_component`) avoids editing sites.json behind the pointer, but it is still name-keyed. SC-02 must add a lifecycle identity (or record a reviewed scope amendment) before any binding relies on it. Not closed in N, by design; closed by the D06 PR below.

### Closure (proposed)

**Accepted contract.**

- **Identity.** A component instance has a random uid in `n.meta["component_ids"]["uids"][Class][name]` (`services/component_identity.py`, the sole writer). It travels with the network through netCDF, undo, saves, Save-As, scenario copies and imports, and it changes under the same lock as the network edit.
  - **Mint.** Lazily, on the first binding (`uid_for`). The mint marks the network dirty.
  - **Rename.** The uid moves (`network_crud._update_component`, `network_buses.apply_rename_bus`).
  - **Removal.** `network_crud.purge_component_side_data`, the path every removal takes, drops the uid. A same-name recreation gets a fresh one.
  - **No re-attachment.** A uid is never re-attached by name.
- **Bindings.** `sites.json` has a top-level `bindings: {"<Class>:<name>": uid}`. Validation: placeable-class keys, uid strings of at most 64 characters (422 otherwise). A binding never moves on disk; a rename shows up in the projection. Correctness never depends on a sidecar hook.
- **First sight.** Each key gets a binding the first time the open network sees it, on GET or PUT. The binding is the live component's uid, or a uid no component holds when the name has none. A pre-D06 key is therefore bound by name exactly once, at migration, and a key naming no component is never taken by a later one.
- **Resolution** (`services/site_bindings.py`, open project only).
  - The uid is held by a live component: bound. If the instance has a new name (an undo of a rename, or a failed sidecar rename), the entry is re-keyed in the projection.
  - Otherwise the entry is stale. GET `/sites` hides it on a legacy Site and lists it in `unbound_keys`. It stays on disk, and a PUT carries it forward, so an undo of the delete brings it back.
  - An enabled Site's entries and the footprints its scene owns are the scene's (D03). They are never hidden or re-keyed here. One that is stale, or whose instance now has another name, is listed. Scene GET `unbound_keys` applies the same rule.
- **PUT `/sites`.** A key the client sends without a binding (a new placement) is bound at first sight, and the bindings the client echoes are kept. Another project's document reads and writes as stored: no binding decision is made without its network.

**Lifecycle trace.**

| Path | Hook | Effect |
|---|---|---|
| Rename (Properties, chat) | `_update_component` moves the uid | Bound under the new name |
| Bus rename | `apply_rename_bus` moves the uid | Bound under the new name |
| Delete, including the bus cascade | `purge_component_side_data` drops the uid | Stale |
| Failed `sites.json` or scene hook | None needed | The uid changed with the network: stale or re-keyed |
| Undo | `network_undo.apply_undo` re-imports the netCDF with its meta | Bound again |
| Redo | No redo endpoint exists in the backend or the frontend | Nothing to trace |
| Import or replace, then save over the project | The foreign network does not hold the uids | Stale |
| Save-As | Saves the in-memory network | The copy keeps the uids |
| Scenario copy, snapshot, bundle | `_copy_bundle_dirs` copies the project as saved | The copy keeps the saved uids |

**Copy mapping.** A copy's component holds the same uid in a separate project. Save-As and scenario copy are tested. Snapshot and bundle are covered in `test_sites_routes.py`.

**Alternatives rejected.**

- A sidecar-only generation ledger with a sites snapshot in each undo entry: it fails open on a hook error and changes the undo stack.
- A uid column on the component tables: every update is a remove plus add, and the column leaks into tables and exports.
- A scope amendment to bind by `Class:name`: the register forbids it.
- Lineage-prefixed uids with re-adoption of a uid minted after the last save: withdrawn. A lineage-less network adopts any uid, and an undo past a recreate adopts a later instance's uid. Both alias.

**Evidence.** `pypsa-gui/backend/tests/test_site_identity.py` runs 16 tests through the real routes:

| # | Test |
|---|---|
| 1 | A PUT binds |
| 2 | Delete, then same-name recreate |
| 3 | A failed `write_sites` delete hook |
| 4 | Rename, and undo of the rename |
| 5 | Undo of a delete |
| 6 | A PUT carries hidden entries forward |
| 7 | Replace the network, then save over the project |
| 8 | Save-As: the copy only, and the source after reload |
| 9 | Scenario copy |
| 10 | Legacy adoption once, then delete and recreate |
| 11 | Malformed binding returns 422 |
| 12 | A binding never saved is lost, not re-attached by name |
| 13 | A saved delete and recreate survives a reload |
| 14 | A key naming no component is never taken by a later one |
| 15 | A failed scene delete hook flags the recreation |
| 16 | A scene key left by a failed scene rename keeps its binding through a PUT |

Tests 1–11 were written first. On master, 10 of 11 failed; scenario copy already held. Command:

```
cd pypsa-gui/backend && python -m pytest -q tests/test_site_identity.py
```

`test_sites_routes.py` now places an existing component, and saves before a snapshot or bundle.

**Residual risk.**

- **Unsaved mint (lost, never aliased).** A uid minted since the last save lives only in the in-memory network until that save; autosave runs every 5 minutes. A scenario branch, a snapshot, a bundle export, the Save-As source or a discarded session in that window shows those placements as unbound. They stay on disk and listed, for an SC-02 re-bind offer. See the GK B question on durability.
- **Unretired removals.** The removals that skip `purge_component_side_data` (the vintage expansion, and the solve and study copies) act on generated or copied components, not placeable live ones.
- **Legacy migration** binds a pre-D06 key by name once, which is today's behaviour.
- **Failed write.** If the GET write-back of a first-sight binding fails, it is logged and retried on the next read.

## D08: native source, fixture and licence policy

- All N fixtures are synthetic and repo-owned (CC-BY-4.0 like this package): rectangles, n-gons, a 10 × 20 × 8 m reference building, marked byte strings. No OSM or third-party geometry was fetched or committed. N ingests no external source.
- **Existing sources, recorded for SC-01.** `services/site_context.py` fetches OpenStreetMap features. Derived footprints are ODbL: attribution "© OpenStreetMap contributors" travels with any export, and a derived database is share-alike. Satellite imagery tiles are display-only and never stored in a scene.
- **Height evidence classes** (V08) are stored and survive reload:

  | Class | Source of the height |
  |---|---|
  | `tagged` | Source height |
  | `levels` | levels × 3.0 m |
  | `fallback` | 6.0 m |
  | `greenfield` | Proposal |

  The source namespace is part of the building id (V07).

## D11: assistant bounded context and offers

- **Observed.** `harness/compose._format_ui_context` whitelists its keys, so a `site_scene` key in `ui_context` reaches no provider today. The catalogue has no scene tool.
- **Frozen.** `assistant_context.block`:
  - **Fields.** A closed field set (project, site, revision, anchor, part, entry, buildings, equipment, targets). Each is a scalar of at most 80 characters.
  - **Action lines.** One line per action with its verdict code.
  - **Cap.** The block is capped at 1536 bytes (about 400 tokens). Cutting drops whole action lines and says how many.
  - **Excluded.** Never a mesh, footprint, coordinate list, media id or path.
  - **Delivery.** Per-turn user content (harness contract 5).
- **Proposed SC-07 tool tiers.** `site_scene_read` (read), `site_scene_prepare` (read, preview only), `site_scene_apply` (write, card-confirmed for one exact proposal hash). A scene approval never authorizes a model write, solve, worker or media transfer.
- **Evidence** (`test_site_scene_proto_assistant.py`, the real `run_turn` with `FakeProvider`, no paid call):
  - a mesh marker in `ui_context` never reaches the provider, and the tool offer is unchanged;
  - a scripted guided-setup journey reads the real anchor and verdicts, offers the next in-place task, and makes no tool round, job or write;
  - a model calling the nonexistent `site_scene_apply` gets an error result, no confirmation card and no effect.
- **Inventory.** `smoke/assistant_inventory.py --check` passes unchanged (341 tools).

## D12: target-device native performance gates

- **Backend** ([evidence/d12-native-budgets.json](evidence/d12-native-budgets.json)): see the D04 numbers. 2000 rectangles generate in 45 ms (median) and apply in 56 ms. The generation file is 611 KB.
- **Renderer probe** ([evidence/d12-render-probe.json](evidence/d12-render-probe.json), [render_probe.mjs](evidence/render_probe.mjs)): three 0.186.1 (the repo's), Chromium 153 headless with SwiftShader, 1280 × 720. 2000 buildings take a 3.1 ms median frame as one mesh each (1572 draw calls after culling) and 0.1 ms instanced.
- **Not a packaged device.** These are software-GL numbers in a cloud container. They size the limits but do not certify a device. Real-device confirmation (packaged desktop app, pinned browser, frame, latency and memory) stays **pending** for native release, as the register says.
