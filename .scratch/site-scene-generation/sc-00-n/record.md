<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-00 N implementation record

Status: native prototype and proposed contract closures; nothing user-visible ships. Copied from [implementation-record-template.md](../implementation-record-template.md).

## Assignment contract

- **Unit / ticket / spec.** `SC-00-N`, [issues/01-prototype-and-contracts.md](../issues/01-prototype-and-contracts.md). The spec is this package as merged on master `60bf3a4bb41f2acbe486f43adefdbe8ea6b4b343`.
- **Base / branch / head.** Base master `60bf3a4bb41f2acbe486f43adefdbe8ea6b4b343`; branch `claude/site3d-sc00n`. The current full head is the PR's head, reported in the PR and to GK B and the coordinator.
- **Prerequisites.** None (`depends_on: []`).
- **Supported behaviour.** The solve queue refuses unknown job kinds (production). Everything else is a prototype under `pypsa-gui/backend/tests/site_scene_proto/`, frozen contracts, and evidence.
- **Exclusions.** No mounted route, chat tool, `tool-error-kinds.json` kind, assistant-inventory change, UI, Blender, photo engine, worker, paid call, base dependency or solver change.
- **Allowlist (frozen against `60bf3a4b`; no open-PR overlap, rechecked after #771/#777/#785 merged).**
  - `pypsa-gui/backend/services/solve_queue.py` and `services/solve_job_store.py` (D04; GK B's condition 2 puts the boot path in scope).
  - New: `pypsa-gui/backend/tests/site_scene_proto/*`, `tests/test_site_scene_proto_*.py`, `tests/test_solve_queue_unknown_kind.py`, `pypsa-gui/frontend/src/site3d/site3dUiContract.test.ts`, `doc/release-notes.d/claude-site3d-sc00n.md`, and this `sc-00-n/` directory.
  - Status lines in `decision-register.md`, `execution-manifest.yaml` (SC-00-N status) and `HANDOFF.md`.
  - Not touched: `issues/01` and `optional-site-workflow.md` (UX asked the coordinator to route the label correction into both at once), `flow_service.py`, routers, harness.
- **Plan review.**
  - UX GO (session `session_01G7qCujyq1JKH7zZvwTq9uP`) with four conditions:
    1. the return label is "Site 3D";
    2. the save-first state points at the header Save;
    3. labels and reasons are pinned as exact strings, colons only, "›" only on the home link;
    4. the coordinate field's Guided home opens by request-then-scroll.
  - GK B GO (session `session_01G7qCujuZX7pfefytYkdaXq`) with seven conditions:
    1. base `60bf3a4b`;
    2. persisted unknown-kind rows through `reconcile_on_boot`, `enqueue_unique` covered;
    3. `unknown_job_kind` is not a tool error kind and the inventory is unchanged;
    4. the queue/store/shutdown regressions are named;
    5. B and P are named pending;
    6. D06 stays open and the import test covers routers, harness, services and the catalogue;
    7. the release fragment describes the queue guard.
- **Frozen contracts.** [decisions.md](decisions.md): D01–D05, D08, D11, D12, plus the D06 trace.
- **Generated outputs.** None. `smoke/assistant_inventory.py --check` passes unchanged (341 tools, 21 result tabs, 23 panels).

## SC-00 N UI contract: frozen

The rule list in issues/01 and optional-site-workflow.md is frozen as written, with one correction UX required. Rule 4's return label is **"Site 3D"**, rendering "Back to Site 3D", per rule 1 and SITE3D-NAME-1. Master's "Back to 3D site" (issues/01 line 84, optional-site-workflow.md line 46) is corrected in both files by the coordinator-routed follow-up UX requested, not here; this record is the frozen value until then. "Create 3D site" and "Open 3D site" name the thing being made and stay as written.

Source anchors are pinned by `pypsa-gui/frontend/src/site3d/site3dUiContract.test.ts`:

| Rule | Pinned source anchor |
|---|---|
| 1 | MapModeSwitcher `site` → "Site 3D" |
| 4 | Homes: `SECTION_OPEN.project = {panel: 'horizon'}`, `loads = {table: 'Loads'}`, `assets = {panel: 'capacityBounds'}`, `carriers = {table: 'Carriers'}`, `connection = {panel: 'campusElectrical'}`, `results = {panel: 'results'}`. `sectionForComponent`: Load → loads; Generator/StorageUnit/Store → assets; Transformer → connection. |
| 4 | Navigation: `openFlowTarget`, `openFlowTargetInPlace`, `showResultsTab`, `withReturn` |
| 4 | The seven current `FlowReturn` fields; "Back to {label}" |
| 2 | `SiteEmptyState`'s own "Generate 3D site" and "New site", which SC-01 folds into the one action |

Exact labels, reasons and homes are frozen in `pypsa-gui/backend/tests/site_scene_proto/flow_verdicts.py` and pinned by `test_site_scene_proto_flow_verdicts.py`.

| State (rule 3) | Label | Enabled | Reason › home |
|---|---|---|---|
| No confirmed anchor | Create 3D site | no | "Add site coordinates to create a 3D site" › Guided `project` card, `site_anchor` drawer, request-then-scroll; Expert `horizon` panel |
| Unsaved project | Save project first | yes | Target `{header_action: "save"}`: the existing header Save, never a panel, so no second save button |
| Anchor, no Site | Create 3D site | yes | — |
| Site with unfinished parts | Resume site setup | yes | — |
| Accepted scene | Open 3D site | yes | — |
| Read-only, no Site | Create 3D site | no | "Read-only project: ask an editor to create a 3D site" |

Reasons use a colon and never an em dash. The one "›" link goes to a rule-4 home. "Buildings only: configure model elements to map them" links to `loads` (Guided) / the `Loads` table (Expert).

**`FlowReturn` extension, frozen for SC-01 to add** (typed, UX GO):

```ts
mapMode: CanvasView | null                     // 'site' when the jump left Site 3D
siteId: string | null                          // sites.json Site id
part: 'map' | 'generate' | 'model' | 'results' | null   // the four parts in optional-site-workflow.md
partSelection: { kind: 'building' | 'target'; id: string } | null  // stable id (building id or target key), never a display name
```

The return label is `"Site 3D"`. In Guided, a jump always uses `openFlowTargetInPlace` and never switches to Expert. A deep link to a block uses request-then-scroll.

## Test-first and result evidence

**RED.**
- D04: [evidence/d04-red.txt](evidence/d04-red.txt) shows 5 of the 7 new tests failing for the missing guard. The unknown kind ran through the solve path, and boot resumed it as `(0, 1)`.
- Prototype tests: [evidence/proto-red.txt](evidence/proto-red.txt). All 7 files fail to collect with the prototype modules absent. That is a module-level RED; the behavioural RED is D04's.

**GREEN.** 133 passed: 126 prototype and 7 queue ([evidence/proto-green.txt](evidence/proto-green.txt)).

**Commands** (run from `pypsa-gui/backend`, Python 3.12.3, pytest via `python -m pytest`):
- `tests/test_site_scene_proto_*.py tests/test_solve_queue_unknown_kind.py`
- The 24 existing queue, job-store and shutdown files, each in its own run, all pass ([evidence/d04-queue-regressions.txt](evidence/d04-queue-regressions.txt)): `test_solve_queue*.py` (18 files), `test_solve_jobs_table.py`, `test_shutdown*.py` (4 files, including `test_shutdown_queue_kind.py`) and `test_chat_tools_schema_solve_queue.py`.

**Frontend.** `npx vitest run src/site3d/site3dUiContract.test.ts`: 5 passed. `npx tsc -b`: exit 0.

**Lint.** `ruff check` on the new and changed files: the 4 UP017 findings are pre-existing on master in untouched lines of the two queue files, with the same count before and after. The new code is clean.

| V | Native assertion(s) exercised in N | Test node(s) | Status |
|---|---|---|---|
| V05 | 10 × 20 × 8 m kept under 8 headings, centre and area exact | geometry `test_reference_building_*`, `test_heading_zero_*` | passed (native) |
| V06 | m/mm/ft agree; missing/unknown unit refused; ENU ↔ glTF Y-up; existing projection | geometry `test_literal_units_agree`, `test_a_missing_or_unknown_unit_*`, `test_gltf_*`, `test_local_frame_*` | passed (native); B glTF round trip not_run (B) |
| V07 | Equal source ids in two namespaces stay two buildings; duplicates refused | geometry `test_equal_source_ids_*` | passed (native); context promotion is SC-01 |
| V08 | tagged/levels/fallback/greenfield stored | geometry `test_height_evidence_*`; generation `test_buildings_only_*` (reload) | passed |
| V49 | Both sides of each native limit (buildings, vertices, triangles, bytes, segment pairs, media quotas) | geometry `test_*_limit_both_sides`; closure_privacy `test_media_quotas_both_sides` | partial: native passed; B/P budgets not_run |
| V53 | Empty network: generation needs no Bus; the real /flow is unchanged; Site `buses: []` | geometry `test_generation_needs_no_bus_*`; flow_verdicts `test_anchor_and_scene_files_*` | passed (prototype; SC-01 route) |
| V54 | One revision wins among 8 racers; four stale legacy-PUT shapes refused; client flag ignored | generation `test_concurrent_applies_*`, `test_legacy_put_*`, `test_a_client_scene_enabled_*` | passed (store); sitesStore debounce/unload/localStorage is SC-01 |
| V55 | Real bundle export and Save-As carry no marked bytes, hash, EXIF, filename or path; reauthorization on read; copy marking | closure_privacy | passed (policy); upload runtime is SC-06 |
| V56 | Server verdicts for all 11 actions; optional engines never enabled; electrical /flow unchanged | flow_verdicts | passed (contract); UI agreement and late-loss resubmit are SC-01 |
| V57 | null + reason vs resolved 0 | isolation `test_the_numeric_null_contract`; proxy height | passed (contract); process series is SC-03 |
| V58 | Native verdicts unaffected with engines absent or "installed" | flow_verdicts `test_optional_engines_*` | passed (native); B/P not_run |
| V59 | Crash before and after commit; tampered generation; copy one generation under a racing apply | generation, closure_privacy | passed |
| V60 | Orphan/superseded/failed/cache excluded; missing artifact fails and leaves the destination; real recursive copy gap recorded | closure_privacy; evidence/gap-probe.txt | passed (closure); SC-01 wires it into copies |
| V64 | Stale base or expected revision refused; same id with other inputs refused; scene receipt changes only the scene | generation | passed (store); the card/approval path is SC-07 |
| V65 | Disconnect after commit is read back; no duplicate write | generation `test_reapplying_*`, `test_a_crash_after_*` | passed (store); asset-plus-placement partial is SC-02 |
| V67 | No provider tool or job; scripted provider only | assistant | passed |
| V73 | Disabled create with the coordinate home; NaN/inf/range/swapped refused; confirmed 0/0 valid; unconfirmed admits nothing | anchor; flow_verdicts | passed |
| V74 | Anchor opens mapping; create needs a boundary; generate needs a Site; unsaved/read-only refuse | flow_verdicts | passed (contract) |
| V79 | Buildings-only apply and reload resume the same Site; assign explains absent targets | generation `test_buildings_only_*`; flow_verdicts `test_buildings_only_*` | passed (store/contract); UI is SC-01 |
| V91 | Scripted guided setup reads native prerequisites, offers the next task, no tool, job or write | assistant `test_scripted_guided_*` | passed (prototype); the composed block is SC-07 |

## B and P items: named pending results (not done, not stubbed)

| Ticket item | Milestone | Result |
|---|---|---|
| Supported pinned Blender version and GLB/manifest round trip (D09) | B | **pending**: no Blender installed or run |
| Blender export/worker budgets | B | **pending** |
| Two known camera/control/scale fixtures and a small genuine reconstruction (D10) | P | **pending**: no photo engine installed or run |
| Photo texture/mesh/job budgets | P | **pending** |
| Worker unavailable is a named missing result, not a stub | B, P | **pending** for the real worker. In N, the three optional actions return `worker_unavailable` whether or not an engine is present (`test_optional_engines_never_enable_*`). |
| V06/V49/V55/V56/V58 B/P assertions; V29–V48 and V90 B/P rows | B, P | **not_run** |

## Completion and release boundary

- **Implemented.**
  - The D04 queue guard (production).
  - The prototype contracts for D01–D05, D08, D11 and D12, and the D06 trace.
  - The UI-contract pin.
  - Measured native budgets.
- **Remaining.** Every runtime route and UI (SC-01, SC-07), D06 (before SC-02), D07 (SC-03), B and P.
- **Review references.** GK B and UX plan GOs above. PR verdicts are comments on the exact head.
- **Release fragment.** `doc/release-notes.d/claude-site3d-sc00n.md` covers the queue guard only. No capability flag changes; every scene action stays unavailable.
- **No claim** of engineering compliance, device performance, real-provider quality or calibration.

## Successor handoff

- **Next unit.** SC-01: mount the D01/D02 routes; build `scene_store` into a service with the real project lock and ACL; route enabled-Site copies through the closure; build the rule-3 entry and the `FlowReturn` extension. First failing test: `GET /api/projects/{name}/site-anchor` returns `{version: 1, revision: 0, anchor: null}` on a saved project, through the real router.
- **Before SC-02.** Close D06. The delete hook leaves placements behind, and best-effort hooks can leave footprints (evidence/gap-probe.txt).
- **Skipped, and the risk it leaves.**
  - The return-label correction landed on master in 810c0abb: issues/01 and optional-site-workflow.md both read "Back to Site 3D".
  - D12 device numbers are software-GL only.
  - D05 quotas are policy values not yet enforced by any upload path.
  - The prototype project lock is a process-local stand-in for the native project lock.
