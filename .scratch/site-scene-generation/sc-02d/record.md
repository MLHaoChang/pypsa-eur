<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02d implementation record: detach and group editing

Status: backend and frontend merged (#1040, ac51d61d); follow-ups in [../sc-02/record.md](../sc-02/record.md) (Site 3D follow-ups). Plan verdicts and their conditions: [issues/03-bindings-and-placement.md](../issues/03-bindings-and-placement.md) `## Comments`. Follows [../sc-02/record.md](../sc-02/record.md) follow-up "detach and group editing are not built; placement is one item per preview".

## Frozen contract

Two new actions on the existing `POST .../scene/proposals` (then `apply` / `cancel`); no new route, so the ACL, lock and chat-disposition tables are unchanged:

- `detach` `{keys: ["<Class>:<name>", ...]}`: takes placed items off the scene. Their components stay in the model and their sites.json D06 bindings are kept. Each item leaves its groups (a group left empty is kept) and loses the attachments whose `feature` it is. Preview `{items: [{key, label, kept_in_model, groups: [{id, label, left_empty}], associations: [{id, class, label}]}], findings: []}`. The receipt adds `detached` `{key: uid}`; no other action's receipt changes.
- `undo_detach` `{proposal: "<detach proposal id>"}`: re-derives what that detach removed from its parent generation in this site's own chain (never from the client) and adds it back to the current head: placements under each instance's current name (a rename since is followed), memberships of groups that still exist, attachments. Preview `{items: [{key, label, groups, associations}], findings: [membership_not_restored (warn, with group)]}`. Apply re-derives and refuses, writing nothing, when that differs from the preview.
- Group editing needs no new action: `place_existing` already takes several keys and commits them as one revision; one refused key refuses all.

Route detail codes (under existing error kinds, so no new `error_kind` and no tool-error-kinds fragment): `not_placed`, `already_placed`, `duplicate`, `unsupported_target`, `invalid_placements`, `invalid_allocation` (a detach that would leave a split short), `unbound_target` (422 at prepare, 409 at Apply), `target_changed` (409), `detach_not_found` (404 `not_found`, one text for every miss: "This detach can’t be undone now: the scene changed since").

## Frontend

- `site3d/PlacedEquipment.tsx` (new): the "Placed equipment" list in the Model part, the keyboard path to the same selection the canvas makes with Shift+click; "N selected · Clear"; with two or more picked, "Edit N items" with Move east (m), Move north (m), Rotate by (°) (hint: about their centre) and one Preview; "Detach from scene"; the receipt "Detached N items" with an Undo button, disabled with its reason once the server answers `detach_not_found`.
- `site3d/groupEdit.ts` (new, pure): the group move (clockwise rotation about the mean position, then the offset; roof items keep `on`), and the refusal sentences (UX 1).
- `site3d/AssociationPanel.tsx`: one picked item loads into the usual "Place equipment" fields; the shared Review card renders `detach` ("Detach N items from the scene. They stay in the model.", groups left, "Group X will be empty (kept).", attachments that go) and `undo_detach` ("Undo detach: restore N items", the membership warning by name).
- `site3d/useSiteScene.ts`: `picked` and `lastDetach` in the Model proposal store. `api/siteScene.ts`: the two actions and their preview type.
- `pages/SiteCanvas.tsx`: Shift+click on a placed item of a scene-enabled Site toggles its pick; a picked item gets a ground outline in `PICKED_COLOR` (magenta, apart from hover, findings and the gizmo axes); Esc clears the picks while the view has focus.

## Plan conditions and the test that proves each

Tests in `pypsa-gui/backend/tests/test_site_scene_detach.py`.

| Condition | Proof |
|---|---|
| GK B 1: undo is a delta on the head | `test_undo_is_a_delta_on_the_current_head` (a placement and an attachment made after the detach survive) |
| GK B 2: Apply re-derives; twice is `already_placed`; re-apply reads back | `test_an_attachment_target_deleted_between_preview_and_apply_refuses`, `test_undoing_twice_is_already_placed_and_a_reapply_reads_back`, `test_undo_after_the_item_was_placed_again_is_refused` |
| GK B 3: id format, own chain, one text | `test_undo_input_that_names_no_detach_of_this_site_is_not_found[5 inputs]`, `test_undo_of_a_proposal_that_is_not_a_detach_is_not_found`; `store.detach_record` walks only `scenes/<site>/` of this project |
| GK B 4: new error kinds | none: every refusal is a detail `code` under an existing kind |
| GK B 5: binding without placement, empty group | backend readers: `routers/site_scene._unbound_keys` (placements and footprints only, so a detached key is not unbound), `site_bindings.stamp` (prunes the binding of a key no longer placed on the next sites PUT; undo uses the uid in the detach receipt instead), `site_service` rename hook (re-keys the binding), `store` rename/delete hooks, `associations._check_groups` (empty groups valid). Tests: `test_a_sites_put_after_a_detach_is_accepted_and_undo_still_finds_the_instance`, `test_a_component_deleted_after_its_detach_cannot_be_restored`, `test_an_empty_group_reads_back_and_binds_again`, `test_detach_takes_the_item_off_the_scene_and_keeps_component_and_binding` (not in `unbound_keys`) |
| GK B 6: same cap and validation; other actions unchanged | `_placeable` shared with `place_existing`; `test_detach_over_the_place_cap_is_refused`, `test_other_actions_keep_their_receipt_shape`; the SC-02a suites pass unchanged |
| GK B 7: mutants | [evidence/mutation-checks.txt](evidence/mutation-checks.txt): M1–M3 each killed by its named test, green restored |
| Access | `test_another_org_cannot_apply_a_prepared_detach` (404), `test_a_stale_detach_refuses_and_writes_nothing` (409); prepare never writes or mints: `test_detach_and_undo_prepare_never_write` |
| UX 1 names, never keys | `groupEdit.test.ts` (every refusal starts with the name), `PlacedEquipment.test.tsx` (list, cards and refusals carry no `Class:` key); backend roof refusals now carry the refused `key` |
| UX 2 axis words | `PlacedEquipment.test.tsx` "edits two items as one" (Move east / Move north / Rotate by, hint) |
| UX 3 one versus several | "with one item picked, its placement comes back in the usual fields; group values do not carry over" |
| UX 4 Esc | "Esc clears the picks only while the list has focus, and lets other Esc handlers run" (a window listener still sees both presses) |
| UX 5 detach preview | "previews a detach by name, applies it, and offers Undo" |
| UX 6 undo | the same test (Undo is a button), "Undo opens the undo card; a group deleted since is a warning line by name", "when the server can no longer undo it, Undo stays visible, disabled, with the reason" |
| UX 7 one selection | "Clear keeps the count in sync with the store the canvas reads"; outline colour in screenshots |
| Group editing | `test_several_items_place_in_one_revision`, `test_one_refused_item_refuses_the_whole_group_move`, `test_findings_of_a_group_move_are_per_item` |

## Evidence

- Red first: the detach and undo tests failed on `Proposal: action must be one of ...` before the actions existed; the three group-editing tests passed from the start (they pin existing behaviour).
- Frontend: `groupEdit.test.ts` (7), `PlacedEquipment.test.tsx` (11); `src/site3d` and SiteCanvas suites (54 files) pass; `tsc --noEmit` clean.
- Green: `test_site_scene_detach.py` (34), `test_site_scene_*.py`, `test_site_identity.py`, `test_site_service.py`, `test_sites_routes.py`, the three policy tests and `test_tool_error_kind_manifest.py`: exit 0.

## Screenshots

[evidence/](evidence/), viewport 1600x1000, a seeded demo project: `01-selection` (full page, the magenta outline), `02-group-form`, `03-group-preview` (an overlap finding; a ground move into a building is a finding, not a refusal), `04-detach-preview`, `05-receipt-undo`, `06-undo-preview` (membership warning), `07-undo-disabled` (404 intercepted), `08-disabled-reason` (lock held by another user, intercepted); Expert: `01-selection-expert`, `04-detach-preview-expert`, `05-receipt-undo-expert`; dark: `01-selection-dark`, `04-detach-preview-dark`. Taken before two fixes they found: the undo-gone refusal no longer adds a generic "site no longer exists" line, and a read-only group Preview now gives the read-only reason (both tested).

## Residual risk and unfinished work

- **Canvas Shift+click** is not covered by a test or a screenshot (headless WebGL hit-testing was not reliable); the outline is in `01-selection`.
- **Not changed, seen in screenshots:** "Add model asset in Expert" stays active when read-only; in Expert the colour legend covers the Results pill; the Model panel scrolls at 1000 px height.
- **The Undo receipt lives in the client store**: a page reload drops it; since #1040 the offer is read back from the scene's `last_receipt` (see the sc-02 follow-ups for why only the latest change).
- **Undo after a copy.** A copied project carries only the pointed generation, so a detach made before the copy cannot be undone in the copy (`detach_not_found`); the user places the item again.
- **Another site's detach id** gives `detach_not_found` by construction (the lookup reads only this site's chain); no test spans two enabled sites.
