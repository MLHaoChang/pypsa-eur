<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02 plan: bindings, groups, allocation and building-aware placement

Status: plan sent to GK B (native lifecycle/mutation seams) and UX (placement, Guided/Expert); no code yet.
Base: master `17354ee1a` (D06 #794-follow-up merged, SC-01a #807 and SC-01b #821 merged).
Ticket: [../issues/03-bindings-and-placement.md](../issues/03-bindings-and-placement.md).

## Gates

- **D06**: built and merged (component_identity / site_bindings). The register still says "proposed closure"; the first SC-02 PR flips it to closed, citing the merged D06 head and GK B's OK.
- **SC-07-foundation** (manifest dependency) has not started. SC-02 therefore ships no chat tool, no catalogue entry and no tool-error kind. Its cross-cutting AI part is limited to what the ticket allows without SC-07: the scene GET carries the association read-back (exact targets, units, unbound/unavailable reasons) that SC-07-bindings will read, and every action has a truthful manual path. V93–V98 stay with SC-07-bindings.
- **Optional coordinate-first part (V80–V86)** is not in this package: it maps 2D/SLD edges and PoCs, and is a separate slice.

## Public contracts (frozen before tests)

No new route. Everything goes through the existing SC-01 proposal lifecycle, `POST /projects/{name}/sites/{site_id}/scene/proposals` → `GET` → `apply`/`cancel`, so ACL, the network-then-scene lock order, expected revision, input hash and receipts are reused unchanged. Route parity and `not_via_chat.py` are untouched (#800 owns that file now).

New proposal actions (`proposals.ACTIONS`):

| Action | Inputs | Writes (one new generation) |
|---|---|---|
| `bind_targets` | `associations: [{feature_id, target: {class, name}, role, carrier?, allocation?}]`, `groups: [{id, label, members: [feature_id \| group_id \| target]}]` | `gen["associations"]`, `gen["groups"]` |
| `place_existing` | `placements: {"<Class>:<name>": {x, y, rot, on: "ground" \| "roof:<building_id>"}}` | `gen["site"]["placements"]` (scene only) |
| `rebind` | `key`, `target: {class, name}`, `confirmed: true` | one association/placement re-bound to a fresh uid |

`APPLY_KEYS` gains `associations` and `groups`. Each association stores the target's uid from `component_identity.uid_for` (minted under the network lock at Apply, never at prepare), never matched by name later.

Validation at prepare and again at Apply (under the lock, against the live network):

- **Target classes**: `Load` (one per carrier; carrier from the native `carrier` column), `Link` with `eh_process` (process unit; input stays the Link, no Load created), `Store` (inventory), plus existing placeable classes. Anything else: `unsupported_target`.
- **Units**: from the native schema routes (`asset_schema`); a carrier or quantity the model lacks (e.g. no cooling Load) is returned `unavailable` with a reason, never synthesized.
- **Groups**: acyclic (DFS; a cycle → `invalid_input` naming the cycle), no orphan member (a member must be a feature, a group or a bound target), several buildings plus outdoor equipment allowed.
- **Multiple enclosures**: several features may bind one target; capacity is the target's, counted once (the read-back reports `enclosures: n`, one capacity).
- **Allocation**: optional `{basis: "explicit", weights: {feature_id: w}}`; weights > 0, sum to 1 within 1e-9, else `invalid_allocation`. No allocation on an aggregate Load spanning buildings means "not split", shown as such. Allocated values are labelled allocated and sum to the native total (V16: 10 MW × 0.4/0.6 → 4/6, total 10).
- **No duplicate demand**: binding a process Link never creates or proposes a Load; a proposal that would pair a process Link with a fixed electricity Load for the same feature is flagged `double_counted_demand` (finding, not silent).

Read-back (scene GET, open project only, via `site_bindings`): each association resolves through its uid: `bound` (name may differ after a rename), `unbound` (deleted, recreated, imported, clustered; listed in `unbound_keys`), never inherited by a same-name component.

Re-bind (D06 follow-up-rebind.md): only `rebind` with `confirmed: true` and the user's chosen target; never automatic, never by name. Declining leaves the entry unbound.

## Lifecycle and locking

- Lock order network → scene `project_lock` everywhere (as D06 froze); Apply mints uids under the network lock first.
- `n.remove` drops the uid via `component_identity.keep`/`drop` (already wired by D06); SC-02 adds no removal path.
- The pre-D06 risk "sites.json delete/rename hooks write without project_lock" (`site_service` rename/delete hooks): fixed in this PR by taking `project_lock` inside those hooks, with a test that asserts the order. Small and in `services/site_service.py`, which no open PR touches.
- Add model asset: the UI opens the existing native add editor (same approval and edit card); creation and placement are two receipts. If placement fails after creation, the asset stays, the scene is unchanged, and the next step offers placement only; the create is never retried (V20/V65).

## Placement (UI)

- Native scene buildings (SC-01 generation) become obstacles in `placementCheck` and `layout`, and their roofs become placement targets for roof-eligible kinds (PV): roof eligibility = building has a footprint polygon and a height; a placement on a roof checks containment in the roof polygon.
- Source-verified catalogue equipment keeps exact dimensions (no non-uniform stretch; terminals and service envelope drawn from the same record). Generic fitted equipment is labelled "approximate".
- Findings name the actual objects; an unknown rule shows "no rule", never a pass (V19).
- Geometry-derived lengths stay behind `derive_lengths_from_geometry` and existing `network_lengths` routes; user/import/OSM routes preserved (V21; regression tests only, no change expected).
- Guided: the Site 3D section's next task is "Associate buildings with loads and processes", then "Place equipment"; Expert shows uid-free target refs, carriers, units, allocation and enclosure counts in the existing properties panel. No new card, palette or top-level workflow.

## File allowlist (actual master; overlap-checked against open PRs)

Backend (new): `services/site_scene/associations.py`; tests `tests/test_site_scene_associations.py`, `tests/test_site_scene_placement.py`.
Backend (changed): `services/site_scene/proposals.py`, `services/site_scene/store.py` (`APPLY_KEYS`, closure carry), `services/site_bindings.py` (association resolution), `routers/site_scene.py` (read-back), `services/site_service.py` (project_lock in hooks).
Frontend (changed): `site3d/placementCheck.ts`, `site3d/layout.ts`, `site3d/SiteScenePanel.tsx`, `site3d/useSiteScene.ts`, `site3d/types.ts`, and their tests; new `site3d/associations.ts` (+test), `site3d/AssociationPanel.tsx` (+test).
Docs: `.scratch/site-scene-generation/{decision-register.md, issues/03-bindings-and-placement.md, sc-02/record.md, execution-manifest.yaml}`; `doc/release-notes.d/<branch>.md`.
Not touched: `harness/catalogue.py`, `routers/projects.py` (#795), `services/not_via_chat.py` (#800), `models/process_unit.py` (#827/#832, read only), `tool-error-kinds.json`.

## Tests first (public seams, real project via TestClient; no mocked authorization/persistence)

1. V11: a building binds electricity, heat and H2 Loads in native units; a cooling association with no cooling Load reads `unavailable`.
2. V12: process Link binds; no Load added (network fingerprint unchanged); a fixed electricity Load plus the Link on one feature is flagged.
3. V13: complex over two buildings, a process unit and an inventory Store applies; a cycle and an orphan refuse.
4. V14: rename keeps the binding; delete unbinds; same-name recreate stays unbound until a confirmed `rebind`; declined rebind stays unbound; a foreign (import) target cannot attach by label.
5. V15/V16: two enclosures, one capacity; aggregate Load without allocation shows unsplit; 0.4/0.6 of 10 MW → 4/6/10; weights summing to 0.9 refuse.
6. V20: place_existing leaves `network.nc` and the input fingerprint unchanged; a placement failure after a native create keeps exactly one asset and does not re-create.
7. V21: length opt-in off, a visual move leaves line lengths; on, a supported route updates and user/import/OSM routes stay.
8. Lifecycle: lock order network → scene on bind Apply and on the site_service hooks; access revoked between prepare and Apply refuses; a stale revision refuses.
9. Mutation regression (recorded in `sc-02/evidence/mutation-checks.txt`): resolve by name, skip the uid at Apply, drop the cycle check, drop the allocation sum check; each must turn a named test red.
10. Frontend (vitest + browser evidence): native building as obstacle, PV on an eligible roof, BESS beside a building, transformer in the yard (V17); exact versus approximate dimensions (V18); findings name objects, unknown rule is not a pass (V19); keyboard and reduced-motion paths (V47).

Domain fixtures: the existing synthetic eh_site template (process Links, Inventory Stores, multi-carrier Loads) for bindings; no new real-site data.

## Package size

This is the size of SC-01, which shipped as a backend PR (a) and a UI PR (b). The coordinator brief asks for one PR; the split question went to the coordinator.

## Not in this package

SC-07 tools and conversational binding (V93–V98), V80–V86 coordinate-first mapping, the SC-01b follow-ups (footer Overpass URL, pill overlap, empty dark pill, Satellite "New site"), result projection (SC-03).

## Plan verdicts

**UX (session `01G7qCukgawg573GcenNwGsX`), 2026-10-28: GO with conditions** (covers the UI PR if SC-02 splits):

1. Names, not refs: Guided never shows `Class:name`; Expert writes "cooling_1 · Load" (kind via the panel's word map); carrier and unit from the existing label maps; weights as % with their total, and a total other than 100 % is a visible finding.
2. Each "Not attached: …" / "Unavailable: …" that has a fix carries one ">" link to it, opened mode-aware (`openFlowTargetInPlace` in Guided, `openFlowTarget` in Expert) through `withReturn`.
3. "Attach to…" is a user picker, never preselected by name; with no candidates it is disabled with a visible reason. Confirm stays disabled with a visible reason until there is something to confirm.
4. Add model asset in Guided opens the asset's Guided home, not Expert; "Back to Site 3D" returns; then "Place it" is the one next step. Never jump on its own after a placement or check: offer a ">" link.
5. Findings name the user's objects and selecting one highlights both; "No rule checked: <which rule>"; "Approximate size" on the item itself.
6. Wording: ">" for links only, "·" between data, a colon between clauses, "out of date"; Guided task "Attach buildings to loads and processes"; one verb (Attach) throughout.
7. Guided and Expert screenshots: unbound, attached, unavailable, picker open, weights not 100 %, add asset → place it, placement pass, overlap, no rule checked, approximate size, confirm and refusal.
8. Fold in two SC-01b follow-ups in `SiteScenePanel`: (a) the footer that shows the Overpass URL / "ConnectError" / `PYPSAGUI_OVERPASS_URL` becomes "Site context unavailable: the map data service could not be reached" + "> Retry", details behind Details or in the log; (b) the empty dark pill under "Library dimensions". The "Back to …" overlap and Satellite "New site" stay out.

GK B: pending. Coordinator split question: pending.
