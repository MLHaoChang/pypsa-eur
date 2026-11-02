<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02d implementation record: detach and group editing

Status: backend built (branch `claude/site3d-sc02d`); frontend waits for SC-02c (site3d/) to merge. Plan verdicts and their conditions: [issues/03-bindings-and-placement.md](../issues/03-bindings-and-placement.md) `## Comments`. Follows [../sc-02/record.md](../sc-02/record.md) follow-up "detach and group editing are not built; placement is one item per preview".

## Frozen contract

Two new actions on the existing `POST .../scene/proposals` (then `apply` / `cancel`); no new route, so the ACL, lock and chat-disposition tables are unchanged:

- `detach` `{keys: ["<Class>:<name>", ...]}`: takes placed items off the scene. Their components stay in the model and their sites.json D06 bindings are kept. Each item leaves its groups (a group left empty is kept) and loses the attachments whose `feature` it is. Preview `{items: [{key, label, kept_in_model, groups: [{id, label, left_empty}], associations: [{id, class, label}]}], findings: []}`. The receipt adds `detached` `{key: uid}`; no other action's receipt changes.
- `undo_detach` `{proposal: "<detach proposal id>"}`: re-derives what that detach removed from its parent generation in this site's own chain (never from the client) and adds it back to the current head: placements under each instance's current name (a rename since is followed), memberships of groups that still exist, attachments. Preview `{items: [{key, label, groups, associations}], findings: [membership_not_restored (warn, with group)]}`. Apply re-derives and refuses, writing nothing, when that differs from the preview.
- Group editing needs no new action: `place_existing` already takes several keys and commits them as one revision; one refused key refuses all.

Route detail codes (under existing error kinds, so no new `error_kind` and no tool-error-kinds fragment): `not_placed`, `already_placed`, `duplicate`, `unsupported_target`, `invalid_placements`, `invalid_allocation` (a detach that would leave a split short), `unbound_target` (422 at prepare, 409 at Apply), `target_changed` (409), `detach_not_found` (404 `not_found`, one text for every miss: "This detach can’t be undone now: the scene changed since").

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
| Group editing | `test_several_items_place_in_one_revision`, `test_one_refused_item_refuses_the_whole_group_move`, `test_findings_of_a_group_move_are_per_item` |

## Evidence

- Red first: the detach and undo tests failed on `Proposal: action must be one of ...` before the actions existed; the three group-editing tests passed from the start (they pin existing behaviour).
- Green: `test_site_scene_detach.py` (34), `test_site_scene_*.py`, `test_site_identity.py`, `test_site_service.py`, `test_sites_routes.py`, the three policy tests and `test_tool_error_kind_manifest.py`: exit 0.

## Residual risk and unfinished work

- **Frontend not built yet** (waits for SC-02c).
- **Undo after a copy.** A copied project carries only the pointed generation, so a detach made before the copy cannot be undone in the copy (`detach_not_found`); the user places the item again.
- **Another site's detach id** gives `detach_not_found` by construction (the lookup reads only this site's chain); no test spans two enabled sites.
