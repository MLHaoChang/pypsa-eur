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

Plan verdict: GK B GO on option 1 (strict: never re-attach by name), with conditions. Accepted only through GK B's OK on the D06 PR's merged head.

**Accepted contract.**

- **Identity.** A component instance has a uuid4 in `n.meta["component_ids"]["uids"][Class][name]` (`services/component_identity.py`, the sole writer; callers hold `PyPSAService.get_lock()`). It travels with the network through netCDF, undo, saves, Save-As and scenario copies. A solve and a reload keep it.
  - **Mint.** Only on a write: the PUT, on first binding. The mint marks the network dirty.
  - **Rename.** The uid moves (`network_crud._update_component`, `network_buses.apply_rename_bus`).
  - **Removal.** `network_crud.purge_component_side_data` drops the uid, so a same-name recreation gets a fresh one.
  - **Clustering.** `forget_all` drops every uid, because a cluster can reuse an old name.
  - **No re-attachment.** A uid is never re-attached by name. No `retired` list exists: a dropped uid is simply gone.
- **Bindings.** `sites.json` has a top-level `bindings: {"<Class>:<name>": uuid}`. A request is rejected with 422 for a non-placeable key, a uid that is not a uuid, or one uid on two keys. A well-formed unknown uid is accepted and reads unbound (the undo case).
  - **Rename hook.** It moves the binding after the scene hook, and not when a scene still shows the old key. That key keeps the binding and reads unbound, and the PUT gives each uid to the key of the instance's current name only.
- **GET never writes** (`/sites` and the scene GET). It mints nothing, marks nothing dirty and writes no `sites.json`.
  - A bound entry follows its instance: shown under its current name (re-keyed in the response only), or, when its uid is gone, hidden on a legacy Site and listed in `unbound_keys`.
  - An enabled Site's entries and the footprints its scene owns are the scene's (D03). A stale one, or one whose instance now has another name, is listed and never hidden.
  - A key with no binding (pre-D06, or newly placed) keeps name semantics until the first PUT binds it.
- **PUT `/sites`**, under the network lock:
  - It binds each unbound key, to the live component's uid or to a uuid no component holds. A key naming no component is therefore never taken by a later one.
  - It keeps the bindings the client echoes, and carries hidden stale entries forward so an undo restores them.
  - Another project's document reads and writes as stored.

**Lifecycle trace.**

| Path | Hook | Effect |
|---|---|---|
| Rename (Properties, chat) | `_update_component` moves the uid | Bound under the new name |
| Bus rename | `apply_rename_bus` moves the uid | Bound under the new name |
| Delete, including the bus cascade | `purge_component_side_data` drops the uid | Stale |
| Failed `sites.json` or scene hook | None needed | The uid changed with the network |
| Undo | `network_undo.apply_undo` re-imports the netCDF with its meta | Bound again |
| Redo | No redo endpoint exists in the backend or the frontend | Nothing to trace |
| Import or replace, then save over the project | The foreign network has no uids | Stale |
| Clustering | `forget_all` | Stale |
| Save-As | Saves the in-memory network | The copy keeps the uids; a PUT in the copy leaves the source byte-identical |
| Scenario copy, snapshot, bundle | `_copy_bundle_dirs` copies the project as saved | The copy keeps the saved uids |

**Meta rewriters.** `git grep -nE "\.meta\s*=[^=]"` finds four. Clustering is handled above. `asset_schema/provenance._root` and `vintage_service._ensure_meta_bucket` replace `n.meta` only when it is not a dict, so they keep `component_ids` (tested with a solve and a reload). `component_identity._root` follows the same rule.

**Fingerprint.** `workflow_tools._network_input_fingerprint`, normal and exact, is unchanged by a mint, a rename or a drop (tested). Placing an asset never makes a solve stale.

**Removals that skip `purge_component_side_data`.** The vintage expansion (`vintage_service`, `routers/vintage.py`), the solve and adequacy copies (`ac_pf_service`, `solver/assumptions`, `solver/lost_production`, `adequacy/*`) and `network_global_constraints`. They act on generated or copied components, or on non-placeable classes.

**Alternatives rejected.**

- A sidecar ledger with an undo snapshot: it fails open on a hook error.
- A uid column: remove plus add on every update, and it leaks into tables and exports.
- A scope amendment to bind by `Class:name`: forbidden by the register.
- Lineage-prefixed uids with re-adoption: withdrawn. A lineage-less network adopts any uid, and an undo past a recreate adopts a later instance's uid.
- Writing each mint into `network.nc`, or saving on first mint: rejected by GK B. The first adds a second writer; the second saves as a side effect.

**Evidence.**

- `pypsa-gui/backend/tests/test_site_identity.py` runs 21 tests. They cover every row above, plus: a GET never writes; legacy name semantics until the first PUT; 422 validation; the loss window (hidden and listed, kept on disk); a key naming no component; the scene delete and rename hook failures; fingerprint; the meta writers with a solve and a reload; clustering.
- Mutation checks are in [../d06/evidence/mutation-checks.txt](../d06/evidence/mutation-checks.txt). Resolving by name, carrying uids through clustering, or keeping the uid on delete each turns the delete-and-recreate and clustering tests red.
- The first 11 tests were written first; 10 failed on master. Command: `cd pypsa-gui/backend && python -m pytest -q tests/test_site_identity.py`.

**Residual risk.**

- **The loss window (lost, never aliased).** A placement bound since the last save reads unbound after a scenario branch, snapshot, bundle, Save-As (in the source) or discard. It stays on disk and is listed. Follow-up: [../d06/follow-up-rebind.md](../d06/follow-up-rebind.md).
- **Legacy keys** keep name semantics until their first PUT, as today.
- **Another project's document** reads as stored.

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
