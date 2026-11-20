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
- (UX) The section header drops the raw name `grid_import` and names the connection in plain words (05).

## Comments

### 2026-11-20: gatekeeper conditions for 02b-1 (#1773), each mapped to its test

The sources are GK B 65 (E1–E7, sent 2026-11-17 16:01Z), GK A 30 (W1–W6, 16:20Z), GK B 66 (D1–D5, 19:59Z), GK A 31's hand-off (G1–G3, 2026-11-19 03:45Z), and the NOT OKs at d6b4a452 from GK A 32 and GK B 68. The wording is restated from those messages and the GK comments on #1773; the originals are in the gatekeepers' transcripts. Tests are in `pypsa-gui/backend/tests/test_energization_per_period.py` (P) and `test_energization_route.py` (R).

**GK B 65**
- E1. An edited or uploaded schedule enters the input fingerprint and the IC key. Met by `workflow_tools.py` (the `energization/1` block) and `results.py` `_ic_assumptions`. Tests: P `test_the_fingerprint_moves_on_*`, `test_the_investment_case_key_moves_with_the_schedule`.
- E2. The route takes the edit lock, and has a ROUTE_SURFACES row plus route-parity and not-via-chat fragments. Met: `_check_lock` and the not-via-chat fragment. Deviations GK B 66 accepted: no ROUTE_SURFACES row (the table covers chat.py only), and the parity fragment goes in 02b-2.
- E3. Typed 422 kinds, with an error-kind fragment. Met: the 5 kinds, tested in R `test_each_refusal_is_a_typed_422_and_writes_nothing`. Deviation accepted: no error-kinds fragment, so 02b-2 maps each kind to words.
- E4. A run's card is bound to the schedule; admission refuses after a change. Met: `harness/run_eh.py` `_energization` freezes `schedule_digest`, and `_energization_change` compares it. Tests: P `test_freeze_records_the_schedule_and_admit_compares_it_exactly`, `test_a_record_frozen_before_02b_refuses_on_a_staged_network`.
- E5. The route behaves as an asset edit does: undo, revision, unsaved mark, change log, save and reload. R `test_the_route_sets_one_period_with_undo_revision_log_and_reload`.
- E6. The connection row reads `energization.by_period` back, and is absent without a schedule. R `test_a_template_without_a_schedule_keeps_the_single_limit`, plus the E5 test.
- E7. A non-flat period is refused with its range and never scaled. P `test_a_period_that_varies_is_refused_with_its_range_never_scaled`, R `test_a_period_that_varies_is_refused_by_the_route_and_shown_as_a_range`.
- Also asked: record E1–E7 and GK A's conditions here and verbatim in the PR body. This section does the former.

**GK A 30**
- W1. The schedule is in the fingerprint, and a network without one hashes byte-identically to master. P `test_the_fingerprint_is_unchanged_without_the_column` and `test_d2a_the_fingerprint_of_an_unscheduled_template_is_pre_02b` pin master's hashes as literals (`MASTER_FP_*`).
- W2. The schedule is in the investment case's key, read under the lock. P `test_the_investment_case_key_*` and `test_g2_the_investment_case_reads_the_schedule_under_its_lock`.
- W3. No column is created. P `test_a_link_without_a_schedule_is_refused_and_none_is_created`.
- W4. A refusal leaves the frame untouched. P `test_one_period_is_set_flat_and_no_other_moves` and the refusal tests.
- W5. The per-period value does not cap or raise `p_nom`. P `test_above_the_rating_is_accepted_with_its_note`.
- W6. The non-flat check, plus mutants on the module. The non-flat refusal tests and the mutant run (below).

**GK A 31 (hand-off)**
- G1. A period's type: `np.integer` from the index is accepted; Decimal, `2030.0` and `True` are refused, typed, and the frame is unchanged. P `test_g1_*`. Argument: level 0 is int64 and keys are `str(p)` ("2030"). `str(np.int64(2030))` is "2030", the value `pd.unique(level)` yields, so it must be taken. `str(2030.0)` is "2030.0" and `str(True)` is "True", which match no key. So the `bool` check and the float exclusion are equivalent mutants: the key lookup refuses them anyway. `str(Decimal("2030"))` is "2030", which does match, so the type check is what refuses Decimal (killed).
- G2. The route and the IC digest read the network under the lock. R `test_g2_the_route_reads_the_network_under_its_lock` swaps the network at lock entry and asserts the read is inside the lock (kills M23). P `test_g2_the_investment_case_reads_the_schedule_under_its_lock` makes an edit at lock entry and asserts the digest is read inside the lock (kills M17, before or after the `with`).
- G3. A partly-NaN schedule is hashed whole. P `test_g3_a_partly_nan_column_*` and `test_g3_two_links_one_partly_nan_each_edit_moves_the_fingerprint` (two links, one partly NaN; kills `encode(schedule.dropna())`).

**GK B 66**
- D1. Checks at a clean trial merge with master: route-parity, control-matrix and inventory `--check`, the route-policy tests (including `test_project_route_acl_policy` and `test_golden_coverage`), and both new test files.
- D2a. An unscheduled template keeps its pre-02b IC key and fingerprint. P `test_d2a_*`, with the fingerprint pinned literally.
- D2b. A scheduled template reads `inputs_changed` once, then fresh. P `test_d2b_a_scheduled_network_reads_inputs_changed_once_then_fresh`.
- D3. A bool `mw` is refused on the bool path. R `test_d3_a_bool_mw_is_refused_on_the_bool_path`.
- D4. A record frozen before 02b admits on an unscheduled network. P `test_d4_a_record_frozen_before_02b_admits_on_an_unscheduled_network`.
- D5. The PR body says what each number means (energized limit vs `p_nom` rating), and names every mutant survivor with its reason.

**GK A 32 NOT OK @d6b4a452**
- E4. Bind `schedule_digest`, not `all_by_period`. Met: `run_eh.py` `_energization` returns `{digest, by_period}`, and admission compares the digest. `by_period` is used only to name the links that moved. P `test_e4_a_ramp_reversed_inside_a_period_refuses` fails under the old by_period comparison.
- W1. Pin master's hash literally. Met: `MASTER_FP_SINGLE` and `MASTER_FP_EH_DATACENTER`, computed on origin/master. Moving `"energization/1"` above its `if` is killed.
- G2. Lock-entry tests for M23 and M17. Met; see G2 above.
- G3. A multi-link dropna case. Met; see G3 above.
- M21. A schedule unreadable at both freeze and admit refuses "can't be compared". P `test_e4_a_schedule_unreadable_at_freeze_and_admit_cannot_be_compared`.
- G1 argument in the PR body. Given under G1 above.

**GK B 68 NOT OK @d6b4a452**
- Conditions verbatim in the body and here: this section.
- D1: the body lists `test_project_route_acl_policy` and `test_golden_coverage`.
- D5: the second survivor is named; see the PR body's mutants line.
- Nit: "2027 varies 15–15 MW". This was a period flat apart from hours with no value. It now reads "2027 has hours without a value (15 MW in the others): set it on the network." P `test_a_period_with_hours_without_a_value_is_not_flat`. `by_period` still reads `{min, max}` for such a period.
- Nit: the dead `isinstance(body, dict)` check is removed. FastAPI's `body: dict` already answers 422 to a body that is not an object.
