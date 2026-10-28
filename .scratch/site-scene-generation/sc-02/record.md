<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02 implementation record

Status: SC-02a (backend) in review; SC-02b (UI) after it. Copied from [implementation-record-template.md](../implementation-record-template.md). Plan and plan verdicts: [plan.md](plan.md).

## Assignment contract

- **Unit / ticket.** `SC-02`, [issues/03-bindings-and-placement.md](../issues/03-bindings-and-placement.md), split in two PRs (coordinator, 2026-10-28):
  - **SC-02a**, this PR: backend only.
  - **SC-02b**: the UI (association panel, placement findings, Guided/Expert screenshots), under UX's plan conditions.
- **Base / branch.** Master `cbcca7a0`; branch `claude/site3d-sc02a`.
- **Gate.** D06 closed by GK B's OK @7f3038782796d131c0b061535ae47567aad8efb6 on #817 (merged 2026-10-28); this PR flips the register.
- **Exclusions.**
  - No UI (SC-02b).
  - No chat tool, catalogue entry or tool-error kind: SC-07 is held on an open owner question (folding into study-harness 07/08); V93–V98 stay with SC-07-bindings. Accepted by the coordinator.
  - V80–V86 (coordinate-first mapping) out. Accepted by the coordinator.
  - No new route: the SC-01 proposal routes carry three new actions, so route parity, `not_via_chat.py` and the ACL/lock tables are unchanged.
- **Files.**
  - New: `services/site_scene/associations.py`; `tests/test_site_scene_associations.py`, `tests/test_site_scene_placement.py`.
  - Changed: `services/site_scene/proposals.py` (actions, network-then-scene Apply), `services/site_scene/store.py` (`APPLY_KEYS`, read, rename re-keys members/features), `routers/site_scene.py` (read-back on GET), `services/site_service.py` (scene mutex in the two legacy hooks).

## Frozen contract

Proposal actions on `POST .../scene/proposals` (then `apply`/`cancel`):

- `bind_targets` `{associations: [{feature, target: {class, name} | {class: "Load", carrier}, weight?} | {id}], groups: [{id, label, members}]}`: replaces the Site's associations and groups. `{id}` keeps an existing association unchanged.
- `rebind` `{association | placement, target: {class, name}, confirmed: true}`: re-attaches one association (`rebound_from` keeps the old uid; shares must still total 100 %) or one placed key (re-keyed to the chosen component, with its footprint, group memberships and associations).
- `place_existing` `{placements: {"<Class>:<name>": {x, y, heading, on?: "ground" | "roof:<building id>"}}}`: scene placements only, plus the D06 sites.json binding of each key. A key whose binding names another instance (deleted or replaced) is refused `unbound_target`; it is never re-bound by name.

Generation fields: `associations: [{id, feature, class, uid, name_at_bind, role, weight?, rebound_from?, carrier?}]` (an unavailable carrier row persists with `uid: null` and its `carrier`), `groups: [{id, label, members}]`.

Scene GET adds `associations` (each with live `name`, `status` bound / unbound / unavailable / not_checked, `carrier` from the component's own column, else its bus, `unit`) and `targets` (one per bound instance: `features`, `enclosures`, `capacity` once, `allocation` `{basis: none | explicit, shares: [{feature, weight, allocated}]}`).

Route detail codes (under the existing `invalid_input` 422 and `revision_conflict` 409 kinds): `unsupported_target`, `unknown_target`, `ambiguous_target` (with `candidates`), `orphan_member`, `cycle` (with `path`), `invalid_allocation`, `duplicate`, `confirmation_required`, `not_roof_eligible`, `off_roof`, `unbound_target`, `project_not_open`, `target_changed` (409). Messages name kinds in words, never class paths. Findings (non-blocking, on the preview): `double_counted_demand` (warn: an electricity Load and a process unit with an electricity feed on one feature), `overlap` (warn: a ground placement's footprint corners inside a building).

**Changed after plan review (flagged to GK B):** `feature` names the bound building, group or placement key; `weight` sits on each row; placements carry `heading`; `{id}` keeps a row; `not_checked` (no network open) and `unavailable` (persisted) statuses; `rebind` also takes `{placement}`; `unbound_target`; roof and overlap use the #712 footprint corners.

## Plan conditions and the test that proves each

| Condition | Proof |
|---|---|
| GK B 1: legacy hooks under the scene mutex, never the reverse; every caller | `test_every_legacy_sites_write_holds_the_scene_mutex_and_never_the_reverse[crud_rename, crud_delete, bus_rename]`; bind Apply: `test_bind_apply_takes_the_network_lock_before_the_scene_mutex`; real threads: `test_a_threaded_placement_apply_and_a_crud_edit_keep_both_sites_writes[rename, delete]` (no deadlock, both writes kept). `map_layout_service` writes `map_layout.json`, which no scene write touches: unchanged. |
| GK B 2: GET and prepare never mint or write | `test_a_scene_get_and_a_prepare_never_mint_or_write` (`uid_for`/`_root` patched to fail; bytes and mtimes of every project file unchanged) |
| GK B 3: Apply re-validates; ambiguity never picked | `test_a_target_deleted_between_prepare_and_apply_refuses_and_writes_nothing`, `test_a_target_whose_carrier_changed_between_prepare_and_apply_refuses`, `test_two_loads_on_one_carrier_are_ambiguous_and_never_picked` |
| GK B 4: allocation tolerance `abs(sum - 1) <= 1e-9`, finite and > 0 | `test_allocation_weights_are_finite_positive_and_sum_to_one` (0.1+0.2+0.7 accepted; 0.9, NaN, 0, negative refused), `test_a_partial_split_is_refused` |
| GK B 5: confirmed rebind; refused if the uid changed; declined keeps the old uid | `test_rebind_needs_the_users_confirmation`, `test_a_rebind_prepared_before_a_recreation_refuses_at_apply`, `test_a_declined_rebind_leaves_it_unbound_with_its_old_uid` |
| GK B 6: placement retry never re-creates | `test_a_failed_placement_after_a_create_is_retried_without_a_second_create` (asserts the same uid in sites.json `bindings` before and after the retry, one Generator) |
| GK B 7: `double_counted_demand` is a warn finding, same feature only | `test_a_process_link_and_a_fixed_load_on_its_feed_bus_is_a_finding`, `test_a_process_link_binds_without_adding_a_load`, `test_double_count_fires_for_an_electricity_load_on_another_ac_bus_and_not_for_heat` |
| GK B 8: policy tests and route parity | `test_project_route_acl_policy`, `test_write_surface_lock_policy`, `test_route_chat_disposition` pass; Apply re-checks access: `test_another_org_cannot_apply_a_prepared_bind` (404), `test_a_lock_taken_between_prepare_and_apply_refuses_and_writes_nothing` (409 `project_locked`), `test_a_stale_revision_refuses_and_writes_nothing` (409), nothing written; `smoke/assistant_route_parity.py --check` ok (unclassified 0) |
| GK B 9: mutations | [evidence/mutation-checks.txt](evidence/mutation-checks.txt): M1–M10 (M5 is condition 9's drop of the Apply re-validation), each red on its named test, all green restored |
| GK B 10: digest test on the trial merge | `test_site_scene_digest.py` passes on this head; re-run on the trial merge before reporting green |

## Validation cases

| Case | Status in SC-02a | Tests |
|---|---|---|
| V11 | backend pass | `test_a_building_binds_one_load_per_carrier_in_native_units` (cooling unavailable, read back as `unavailable`) |
| V12 | backend pass | `test_a_process_link_binds_without_adding_a_load`, `test_double_count_fires_for_an_electricity_load_on_another_ac_bus_and_not_for_heat` |
| V13 | backend pass | `test_a_complex_spans_buildings_a_process_unit_and_inventory`, `test_a_cycle_and_an_orphan_member_are_refused`, `test_a_placed_member_follows_its_components_rename` |
| V14 | backend pass | rename, delete, same-name recreate, confirmed/declined rebind, replaced network, rebind that breaks a split, stale-bound placement refused then re-keyed by a placement rebind |
| V15 | backend pass | `test_several_enclosures_of_one_battery_count_its_capacity_once`, `test_an_aggregate_load_in_two_buildings_is_not_split` |
| V16 | backend pass | `test_an_explicit_split_shows_allocated_values_that_sum_to_the_native_total` and the weight cases |
| V17 | backend half | roof eligibility, off-roof, building overlap finding; the scene view in SC-02b |
| V18 | SC-02b | catalogue exact versus approximate dimensions are drawn client-side |
| V19 | backend half | overlap finding names both objects; full findings view in SC-02b |
| V20 | backend pass | `test_placing_existing_equipment_changes_the_scene_only`, create-then-failed-placement retry |
| V21 | regression | `test_a_visual_move_leaves_line_lengths_whatever_the_length_setting[False, True]`; derived lengths stay on the existing routes (`test_site_route_lengths.py`) |
| V47, V61–V72, V93–V98 | not here | SC-02b / SC-07 |

## Evidence

- Red first: the 36 new tests failed for missing behaviour (`Proposal: action must be generate_native or edit_buildings`, missing `code`, and the legacy hooks writing outside the mutex: `[False, True]`). The rename re-key test was red without its store change.
- Green: the two new files, plus `test_site_identity.py`, `test_site_scene_*.py`, `test_site_service.py`, `test_sites_routes.py`, `test_site_route_lengths.py`, `test_flow_site_card_parity.py`, `test_no_implicit_site.py` and the three policy tests: exit 0 (2 pre-existing skips).
- Domain fixture: a synthetic multi-energy network in the test (AC/heat/H2/NH3 buses, two AC Loads, a heat and an H2 Load, an `eh_process` Link, an ordinary Link, an NH3 Store, a BESS), no real-site data.

## Residual risk and unfinished work

- **A never-bound target replaced between prepare and Apply.** If a component that holds no uid yet is deleted and recreated with the same name, bus and carrier between the preview and Apply, Apply binds the new one: there is no identity to compare (uids are minted only at Apply, GK B condition 2). The user reviewed that name and bus; a bound target is fully checked.
- **Place-existing writes two files.** The D06 binding goes to sites.json before the generation commit, so a placement never exists without one; a failed commit leaves a binding to the same instance, which the retry reuses.
- **Test thread versus request network.** `PyPSAService.get_network()` called from the test thread is not the network the requests use (a uid minted through a request was not visible there). These tests read the model only over HTTP and check minting by patching `component_identity`. `test_site_identity.py::test_a_get_never_writes` compares `get_network().meta` from the test thread, which may be the same blind spot; reported to GK B, not changed here.
- **SC-02b next**: the association panel, Attach picker, placement findings view, Add model asset flow and the two SC-01b follow-ups UX asked for, with Guided/Expert screenshots.
