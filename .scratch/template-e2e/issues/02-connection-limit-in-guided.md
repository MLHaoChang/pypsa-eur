<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — The connection limit cannot be changed in Guided; "Import efficiency" opens an empty Link form

Status: ready-for-agent; audit 2026-11-19: open in #1626
Owner: builder session_01G7qCumsdzuVnJQAD41wFxk
Size: S
Gated: UX
Likely files: pypsa-gui/frontend/src/pages/hubDesign/cards/SiteCard.tsx ("Connection limit" KPI ~l.145); pypsa-gui/frontend/src/pages/hubDesign/SectionView.tsx; pypsa-gui/frontend/src/components/FieldDrawer.tsx; pypsa-gui/backend/services/flow_service.py (assumption rows → drawer target, ~l.855-899)
Journey fit: set up site (section 3) → readiness. The limit feeds the energization check that sections 4 and 9 show.

## What

Design §2.3 makes "confirm limits and ramp" **R** for the data centre, and "connection MW" **R** for the generic site.

- Guided section 3 shows the limit as a read-only header ("grid_import, 40 MW" on eh_datacenter, "35 MW" on eh_concentrator_staged). No control changes it. The Generic Site creation form has "Connection limit (MW)", but the template projects do not.
- The ramp limit and the oscillation band are fine: their Edit opens a value / source / note editor in a drawer, and Expert "Grid connection" shows the same editor.
- The **Import efficiency (assumed)** row's Edit opens a blank *new Link* form (empty Name, empty Capacity, From bus unset) instead of the `grid_import` link.
- In Expert, the limit is the `grid_import` Link's Capacity in the property panel. Grid connection itself only offers the "Point of connection" picker.

## Acceptance

- Guided section 3 has a "Connection limit (MW)" field, per period on staged templates (energization schedule). Expert shows the same value under the same label.
- The Import efficiency Edit opens the existing import link's efficiency.
- A browser test sets the limit in Guided and reads it back in Expert.
- (UX) On staged templates the per-period limit is edited as drawer rows, with the energization check (stage peaks, §2.2) beside them.
  Chat: `PUT /api/projects/{name}/flow/energization` is GUI-only for now (not-via-chat entry `energization_per_period`, disposition unbuilt); this issue holds its route-parity limitation, so whoever closes it builds the tool or moves the declaration.
- (UX) The section header drops the raw name `grid_import` and names the connection in plain words (05).

## Comments

### 2026-11-20: gatekeeper conditions for 02b-1 (#1773), verbatim, with a status per clause

Each condition is quoted verbatim from the gatekeepers' messages (GK B 65 session_01G7qCuk4WYXp3LZ1jtfvioW 2026-11-17T16:01:24Z; GK A 30 session_01G7qCujnY77uA6znnUwXz9M queued 2026-11-17T16:02:25Z; GK B 66 session_01G7qCujsT2Qwb6DXqTzvsM5 2026-11-17T19:59:20Z; GK A 33's NOT OK relay, as relayed in GK B 68's ask). Below each, one status line per clause. Paths are under `pypsa-gui/backend/`; P = `tests/test_energization_per_period.py`, R = `tests/test_energization_route.py`. Line numbers are at the commit that adds this section.

**GK B 65**

> E1. Fingerprint: links_t["eh_energization_mw"] is a custom column the LP reads, so a per-period edit must change _network_input_fingerprint and the investment-case digest. Then cached results and IC can't be reused. Confirm whether it's already in there. If it isn't, add it only when present, keeping existing fingerprints byte-identical, with a pin test (AS-02 rule). GK A has the last word on this one.

  - a per-period edit changes `_network_input_fingerprint` — MET: `services/workflow_tools.py:558-561` (`energization/1` block); P `test_the_fingerprint_moves_on_a_period_edit_both_branches`, `test_the_fingerprint_moves_on_an_upload_equivalent_write`.
  - … and the investment-case digest — MET: `routers/results.py:2264-2273`, `services/finance/investment_case_runner.py:148-149`; P `test_the_investment_case_key_moves_with_the_schedule`.
  - confirm whether it's already in there — MET: it was not (GK A 30 W1 confirmed: only PyPSA-Input columns are hashed); the release note names the gap.
  - add it only when present, existing fingerprints byte-identical, with a pin test — MET: appended only when the column holds a value (`workflow_tools.py:559`); P `test_the_fingerprint_is_unchanged_without_the_column` and `test_d2a_the_fingerprint_of_an_unscheduled_template_is_pre_02b` pin master's hashes as literals (`MASTER_FP_SINGLE`, `MASTER_FP_EH_DATACENTER`).
  - GK A has the last word — GK A 30 W1/W2 and GK A 33 W1 are the binding form; see below.

> E2. Route: run test_route_chat_disposition, test_project_route_acl_policy, test_write_surface_lock_policy and test_golden_coverage at the trial merge. Add the ROUTE_SURFACES row. Viewers and non-owners are refused like updateAsset, behind _check_lock. Write the route-parity and not-via-chat fragments in the .d folders.

  - run the four tests at the trial merge — MET: see D1 (outputs pasted in the PR body).
  - add the ROUTE_SURFACES row — DEVIATION accepted by GK B 66: ROUTE_SURFACES covers chat.py's routes only; this route is not a chat surface (it is declared not-via-chat).
  - viewers refused like updateAsset — MET: `services/_project_write_rules.py` row `PUT /api/projects/{name}/flow/energization  write path:name` (added after GK B 68; `tests/test_viewer_role_gate.py::test_viewer_route_table_is_total` failed without it), so `test_a_viewer_is_refused_every_gated_write_and_nothing_changes` now refuses it with `project_view_only`.
  - non-owners refused — MET: `ProjectAccessDep` (`routers/flow.py:383-386`) answers a non-member 404; `tests/test_project_route_acl_policy.py` passes with the route.
  - behind `_check_lock` — MET: `routers/flow.py:364-373`, called first at `:411`; `tests/test_write_surface_lock_policy.py` passes.
  - not-via-chat fragment in its .d folder — MET: `services/not_via_chat.d/claude-template-e2e-02b-energization.json`.
  - route-parity fragment in its .d folder — DEVIATION accepted by GK B 66 (moved to 02b-2): `smoke.assistant_route_parity --check` does not flag the route at the trial merge (it is classified via not-via-chat), so per D1 the declaration need not move into 02b-1.

> E3. Value checks: refuse bool before the number check (bool is an int). Accept numpy floats, refuse NaN, ±inf, ≤0, str and None. Each refusal is a typed 422 with its own test that kills its mutant. A new error kind goes in its own tool-error-kinds.d fragment.

  - bool before the number check — MET: `services/adequacy/energization.py:235`; P `test_a_value_that_is_not_a_number_is_refused[True/False]`, R `test_d3_a_bool_mw_is_refused_on_the_bool_path`.
  - accept numpy floats — MET: P `test_a_numpy_number_is_accepted`.
  - refuse NaN, ±inf — MET: `energization.py:238`; P `test_a_value_that_is_not_finite_is_refused`.
  - refuse ≤0 — MET: `energization.py:240`; P `test_a_value_not_above_zero_is_refused[0/0.0/-5.0]`.
  - refuse str and None — MET: P `test_a_value_that_is_not_a_number_is_refused["30"/None]`.
  - each refusal a typed 422 with its own test that kills its mutant — MET: R `test_each_refusal_is_a_typed_422_and_writes_nothing` (asserts the exact `error_kind`); the energization.py mutant run killed the bool, isfinite and ≤0 mutants (see D5).
  - new error kinds in a tool-error-kinds.d fragment — DEVIATION accepted by GK B 66: no fragment; the route is not a chat tool, and 02b-2 maps each kind to words with a test per kind (D3).

> E4. Admit compares the frozen by_period to the live one exactly: same periods, same values, no isclose. A staged run frozen before 02b has no by_period, so refuse it with a re-freeze sentence and never pass it (cannot read means violation). Non-staged runs are unaffected, with a test that they stay byte-equal.

  - admit compares exactly, no isclose — MET (strengthened per GK A 33: the digest of the exact schedule is compared, `by_period` only names the links that moved): `harness/run_eh.py:231-255`, `:465`; P `test_freeze_records_the_schedule_and_admit_compares_it_exactly` (54.999999 vs 55 refuses), `test_e4_a_ramp_reversed_inside_a_period_refuses`.
  - a staged run frozen before 02b refuses with a re-freeze sentence — MET: `run_eh.py:225-229`, `:238-243`; P `test_a_record_frozen_before_02b_refuses_on_a_staged_network`.
  - cannot read means violation — MET: `run_eh.py:238-246`; P `test_e4_a_schedule_unreadable_at_freeze_and_admit_cannot_be_compared`, `test_e4_a_schedule_unreadable_only_at_admit_cannot_be_compared`, `test_d4_a_pre_02b_record_refuses_where_the_schedule_is_unreadable`, `test_d4_a_digest_less_record_refuses_on_an_unscheduled_network`.
  - non-staged runs unaffected, tested byte-equal — MET: a non-staged card records no `energization` entry (`harness/run_eh.py` `_energization_entry`), so it is byte-equal to a pre-02b card and admits as one (P `test_d4_a_record_frozen_before_02b_admits_on_an_unscheduled_network` asserts no entry and admission); admission, fingerprint and IC key are byte-equal to master (W1/D2 pins).

> E5. The edit behaves like updateAsset: change-log line, undo, network revision bump and dirty or autosave. A save/reload round-trip test shows the per-period value persists.

  - change-log line, undo, revision bump, dirty — MET: `routers/flow.py:426-447`; R `test_the_route_sets_one_period_with_undo_revision_log_and_reload`.
  - save/reload round trip — MET: same test (save, reload, the value read back).

> E6. One writer: Guided and Expert both go through the route, and the frontend displays by_period from the flow row, never computing it (display-reader rule).

  - one writer — MET for the backend: `energization.set_period_mw` is the only writer, called only by `routers/flow.py:430`.
  - Guided and Expert go through the route; the frontend displays by_period from the flow row — NOT IN THIS PR: the frontend is 02b-2 (#1778). The backend side is met: the flow row carries `energization[].by_period` (`services/flow_service.py:1485-1491`); R `test_a_template_without_a_schedule_keeps_the_single_limit`.

> E7. A non-flat period is refused with its range named, never flattened (as you planned). Pin that test.

  - refused with its range named, never flattened, pinned — MET: `energization.py:253-260`; P `test_a_period_that_varies_is_refused_with_its_range_never_scaled`, `test_a_period_with_hours_without_a_value_is_not_flat`; R `test_a_period_that_varies_is_refused_by_the_route_and_shown_as_a_range`.

> Record E1–E7 and GK A's conditions in issue 02's ## Comments, and put them verbatim in the PR body.

  - issue 02 `## Comments` — MET: this section.
  - verbatim in the PR body — for the PR body (this section is the text to paste).

**GK A 30**

> Q: flat per period, with non-flat periods refused and their range named, is the right semantics. Do not scale.

  - MET: see E7.

> W1 E1 fingerprint: I checked. `_network_input_fingerprint` (workflow_tools.py:536-538) hashes only dynamic columns that PyPSA's defaults mark Input, so `links_t["eh_energization_mw"]` is NOT hashed today. An uploaded schedule already leaves the fingerprint unchanged (an existing gap). Add an AS-02 block in the shape of split-storage/envelope-floors (:539-553), tagged `"energization/1"`. Append it only when the column exists and has at least one non-NaN value, so networks without it stay byte-identical. Cover both the `exact=True` and non-exact branches (`_exact_frame_json` vs `to_json`).
> Tests:
> - the fingerprint changes on a per-period edit and on an upload-equivalent write;
> - it is byte-identical on a network without the column, pinned as a literal or against master's value;
> - a mutant dropping the block is killed.
> Name the fixed upload gap in the release note.

  - AS-02 block tagged `"energization/1"` — MET: `services/workflow_tools.py:554-561`.
  - only when the column exists with a non-NaN value — MET: `workflow_tools.py:559`; P `test_the_fingerprint_is_unchanged_without_the_column` (an all-NaN column hashes as master).
  - both `exact` branches — MET: `encode` is `_exact_frame_json` or `to_json` by `exact`; P `test_the_fingerprint_moves_on_a_period_edit_both_branches`.
  - changes on a per-period edit and an upload-equivalent write — MET: P `test_the_fingerprint_moves_on_a_period_edit_both_branches`, `test_the_fingerprint_moves_on_an_upload_equivalent_write`.
  - byte-identical without the column, pinned as a literal — MET: `MASTER_FP_SINGLE`, `MASTER_FP_EH_DATACENTER` (computed on origin/master).
  - a mutant dropping the block is killed — MET: workflow_tools.py mutant run (see D5).
  - the fixed upload gap in the release note — MET: `doc/release-notes.d/claude-template-e2e-02b-energization.md` ("Fixed: an edited or uploaded energization schedule …").

> W2 IC digest: check whether the investment-case digest (finance_case.owner_assets_digest, :1847, and whatever keys the IC cache) reads LP inputs through the fingerprint or separately. If separately and it misses the column, add it there the same way, with the same tests. Report what you found in the PR body.

  - check — MET: the IC key (`results._ic_assumptions` → `assumptions_digest`) reads its inputs separately (`owner_assets_digest`, `dispatch_digest`), not through the fingerprint, and missed the column.
  - add it there the same way, with the same tests — MET: `routers/results.py:2264-2273` (read under the lock), `investment_case_runner.py:148-149` (part only when a schedule exists); P `test_the_investment_case_key_moves_with_the_schedule`, `test_the_investment_case_key_is_unchanged_without_a_schedule`, `test_g2_the_investment_case_reads_the_schedule_under_its_lock`.
  - report in the PR body — for the PR body (the two lines above).

> W3 Writer:
> - If links_t["eh_energization_mw"] has no column for the link, refuse even when energized_links says otherwise. Never create a column.
> - Check that the write keeps the frame's dtype and index, with no reindex.
> - On a MultiIndex, unknown period means not in level 0. On a single axis, accept only "all" (or None, whichever is defined) and refuse an int period.

  - no column → refuse, never create — MET: `energization.py:243-246`; P `test_a_link_without_a_schedule_is_refused_and_none_is_created`, `test_a_column_for_a_link_the_network_lacks_is_refused`.
  - dtype and index kept, no reindex — MET: `energization.py:281` (`.loc[mask, link] =`); P `test_one_period_is_set_flat_and_no_other_moves` asserts `index.equals` and `dtypes.equals`.
  - MultiIndex: unknown = not in level 0 — MET: `energization.py:215-221`; P `test_a_period_not_in_level_zero_is_refused`, `test_g1_*`.
  - single axis: only "all" or None, int refused — MET: `energization.py:222-225`; P `test_a_single_axis_takes_only_all`, `test_a_single_axis_sets_all`.

> W4 Refusals write nothing: assert the frame equals a pre-call copy (pd.testing.assert_frame_equal) for every refusal case, not just that an error was raised.

  - MET: P's refusal helper asserts `assert_frame_equal(after, before)` for every refusal (P:54-59); R `test_each_refusal_is_a_typed_422_and_writes_nothing` asserts the undo depth and the flow row unchanged.

> W5 mw above the link's p_nom: apply_energization takes min(own, E/p_nom), so the edit is a no-op cap. Accept it, but say so (return a note, or the route's sentence names it). Do not refuse silently or accept silently. Test it.

  - accept it — MET: `energization.py:261-268` accepts and returns `note`.
  - say so (note, and the route's sentence) — MET: note "This is above the link's rating ({p_nom} MW): it doesn't raise the link's capacity." (`energization.py:265`), appended to the route's sentence (`routers/flow.py:443-446`).
  - test it — MET: P `test_above_the_rating_is_accepted_with_its_note`; R `test_the_route_names_the_rating_it_does_not_raise`.

> W6 Mutants on energization.py:
> - drop the bool check; ≤0 → <0; drop the non-flat check;
> - write all periods instead of one;
> - isfinite removed.
> Each one must be killed, or else listed with a reason.

  - each named mutant killed — MET: energization.py run 81/82 killed; the survivor (line ~218, a bool operand of the period type check) is equivalent (G1 argument). See D5.

**GK B 66**

> D1 (02b-1): at a trial merge with master, run `smoke/assistant_route_parity.py --check`, `assistant_control_matrix.py --check` and `assistant_inventory.py --check`, plus the four route tests (route_chat_disposition, project_route_acl_policy, write_surface_lock_policy, golden_coverage). Paste the outputs in the PR body. A `stale (sweep prunes)` line is fine; a FAIL is not. If parity flags the new route as undeclared, the declaration moves into 02b-1 and doesn't wait for 02b-2.

  - run the three smokes and the four tests at a trial merge — MET: the outputs, exit codes and master SHA are in the PR body's D1 block.
  - paste the outputs in the PR body — for the PR body.
  - parity flags the route undeclared → declaration in 02b-1 — not triggered: parity does not flag it.

> D2 (digest, standing rule #1675): a network with no energization schedule must keep a byte-identical IC assumptions_digest. Include the part only when a schedule exists. Test it with a fixture produced by the real writer: unchanged, the study reads fresh. Add a second test: a network that already carries a schedule reads "inputs changed" exactly once, then fresh after a re-run. The release note names that one-time stale read; you said it does, which is good.

  - byte-identical IC digest without a schedule; part only when one exists — MET: `investment_case_runner.py:148-149`; P `test_d2a_an_unscheduled_network_keeps_its_pre_02b_key_and_reads_fresh`.
  - fixture by the real writer, reads fresh — MET: the same test on `build_eh_datacenter()`, the real template builder.
  - a scheduled network reads "inputs changed" once, then fresh — MET: P `test_d2b_a_scheduled_network_reads_inputs_changed_once_then_fresh`.
  - the release note names the one-time stale read — MET: "Projects with a staged schedule read their results as changed once after this update."

> D3: the five typed 422 kinds each get a test asserting the exact `error_kind`. Check `bool` before the number check, as you have. 02b-2 maps every kind to words, with a test per kind; none may show blank or a raw kind.

  - five kinds, exact `error_kind` each — MET: R `test_each_refusal_is_a_typed_422_and_writes_nothing` (invalid_mw, no_schedule, unknown_period, invalid_request), R `test_a_period_that_varies_is_refused_by_the_route_and_shown_as_a_range` (period_not_flat).
  - bool before the number check — MET: R `test_d3_a_bool_mw_is_refused_on_the_bool_path`.
  - 02b-2 maps every kind to words — NOT IN THIS PR: 02b-2 (#1778).

> D4 (E4): a pre-02b frozen run on a scheduled network refuses with the re-freeze sentence. Test it, and test that a pre-02b frozen run on an unscheduled network still runs unchanged.

  - scheduled network refuses with the re-freeze sentence — MET: P `test_a_record_frozen_before_02b_refuses_on_a_staged_network`.
  - unscheduled network still runs unchanged — MET: P `test_d4_a_record_frozen_before_02b_admits_on_an_unscheduled_network`. A pre-02b record has no `energization` key; a record that has the key without a digest is not pre-02b and refuses ("can't be compared"; P `test_d4_a_digest_less_record_refuses_on_an_unscheduled_network`), so the two do not conflict.

> D5: run mutants.py on the changed backend sources and list every survivor in the 02b-1 body with its reason. 02b-2 also needs GK A 30's GO for the writer and fingerprint (E-plan), as GK B 65 noted.

  - mutants.py on the changed backend sources — MET (`--base origin/claude/template-e2e-02-connection-limit`): energization.py 81/82, run_eh.py 25/25, flow.py 14/14 (after the tests below; the first runs were 22/25 and 12/14), workflow_tools.py 5/5, results.py 0/0 (its changed lines are an import, an assignment and a call argument, which mutants.py does not mutate; the IC key tests cover them), flow_service.py 1/1, investment_case_runner.py 2/2.
  - every survivor listed with its reason — MET: energization.py:218, dropping the `isinstance(period, bool)` operand, is equivalent (`str(True)` is "True", no level-0 key; see G1). The five survivors of the first run_eh.py/flow.py runs are killed by tests added after GK B 68: run_eh.py:245 `or now is None` (P `test_e4_a_schedule_unreadable_only_at_admit_cannot_be_compared`), run_eh.py:241 `now is not None and` (P `test_d4_a_pre_02b_record_refuses_where_the_schedule_is_unreadable`), run_eh.py:249 `or {}` (P `test_e4_a_record_without_by_period_still_refuses_a_changed_digest`), flow.py:414 `or not body["link"]` (R `test_each_refusal_is_a_typed_422_and_writes_nothing` with `link: ""`), flow.py:438 `if cleared` (R `test_an_edit_that_clears_no_dispatch_leaves_the_solver_state`). GK B 68's `"digest" not in then` (run_eh.py:245) is killed by P `test_d4_a_digest_less_record_refuses_on_an_unscheduled_network`.
  - 02b-2 needs GK A 30's GO — for 02b-2.

**GK A 33 (NOT OK relay)**

> E4 bind schedule_digest; W1 pin master's hash; G2 lock-entry tests M23/M17; G3 two-Link partly-NaN; G1 survivor argument; M21 unreadable at freeze and admit.

  - E4 bind schedule_digest — MET: `run_eh.py:209-221` freezes `{digest, by_period}`, `:247` compares the digest; P `test_e4_a_ramp_reversed_inside_a_period_refuses` fails under a by_period comparison.
  - (GK A 33 follow-up, relayed by the coordinator) M21 admit-only, for a 02b record and a pre-02b record — MET: P `test_e4_a_schedule_unreadable_only_at_admit_cannot_be_compared` (with digest; refuses "can't be compared", kills dropping `or now is None`, which raised TypeError) and P `test_d4_a_pre_02b_record_refuses_where_the_schedule_is_unreadable` (no entry; refuses with the re-freeze sentence, kills dropping `now is not None and`).
  - (follow-up) the reversed ramp is an exact `a[::-1]` — MET: P `test_e4_a_ramp_reversed_inside_a_period_refuses` reverses the period's own values and asserts the sorted values are equal; an order-blind digest mutant (sorted values plus the index, applied by hand to `schedule_digest`) fails it.
  - (follow-up) a renamed link column with the same values refuses — MET: P `test_e4_a_renamed_link_column_with_the_same_values_refuses`; a digest mutant without the column names (applied by hand) fails it.
  - (follow-up) GK A 30's W1–W6 verbatim in issue 02's ## Comments — MET: this section.
  - W1 pin master's hash — MET: `MASTER_FP_SINGLE`, `MASTER_FP_EH_DATACENTER` literals.
  - G2 lock-entry tests M23/M17 — MET: R `test_g2_the_route_reads_the_network_under_its_lock` (M23); P `test_g2_the_investment_case_reads_the_schedule_under_its_lock` (M17).
  - G3 two-Link partly-NaN — MET: P `test_g3_two_links_one_partly_nan_each_edit_moves_the_fingerprint` (kills `encode(schedule.dropna())`).
  - G1 survivor argument — MET: level 0 is int64 and keys are `str(p)`. `str(2030.0)` is "2030.0" and `str(True)` is "True", which match no key, so dropping the `bool` check (or a float exclusion) is equivalent: the key lookup refuses them anyway (P `test_g1_a_float_or_bool_period_never_writes_2030`). `str(Decimal("2030"))` is "2030", so the type check is what refuses Decimal (killed); `np.int64` from the index is taken (P `test_g1_a_numpy_int_period_from_the_index_is_taken`).
  - M21 unreadable at freeze and admit — MET: P `test_e4_a_schedule_unreadable_at_freeze_and_admit_cannot_be_compared`; admit-only above.

**GK B 68 (not blocking)**

  - A non-dict request body gets FastAPI's untyped 422 (the `body: dict` parameter), not one of the five kinds — for the PR body.
