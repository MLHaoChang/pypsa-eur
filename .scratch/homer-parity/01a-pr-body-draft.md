Closes homer-parity 01a (`.scratch/homer-parity/issues/01-offgrid-renewable-fraction-and-operating-reserve.md`, owner decisions HOMER-GAPS-1 and RENFRAC-ALL-1). 01b (operating reserve) is not in this PR.

## Changes proposed in this Pull Request

### Summary

HOMER's renewable fraction, electrical side: `f_ren = 1 − E_nonren / E_used`. It is an optional LP row and a KPI shown on every solve. Per owner decision RENFRAC-ALL-1, the denominator is **all electricity used on site**: served Load plus what conversion Links draw from electricity.

```diff
 build_lp_chain
   … _wrap_with_reserve_margin
+  _wrap_with_min_renewable_fraction      # services/solver/renewable_fraction.py
   _wrap_with_island …

 per investment period P (row min_renewable_fraction[_P]; none on a period with neither load nor converter):
+  Σ w·nonren + (1−f)·Σ w·slack − (1−f)·Σ w·draw ≤ (1−f)·Σ w·load        # w = snapshot_weightings.generators
```

- **E_used:** the scaled `p_set` of active Loads on electrical buses, plus what conversion Links draw from electricity (R14), minus every slack's supply (VOLL, DSR, dc_curtailment). Shed energy is not used. With a slack present, the LP may meet the target by shedding at the slack's price (R5).
- **Conversion (R14, RENFRAC-ALL-1):** a Link that draws electricity (bus0, or a second input with efficiencyN < 0) into outputs that are all non-electrical is *use* when no output has a path back to electricity. Examples are an electrolyser into an H2 offtake, a heat pump, an e-boiler, DAC and process Links. Storage loops (a battery charger, an electrolyser whose H2 feeds a fuel cell) and export (elec→elec, into a supply bus or a grid/export bus) are not use. When a Link has both a path back and a sink, the conversion is **ambiguous**: the KPI is None with a plain-words reason naming the Link (R15), and the row warns.
- **E_nonren:**
  - non-renewable Generators on electrical buses, counted gross (an auxiliary `z ≥ p, z ≥ 0` where `p_min_pu < 0`). A Generator that withdraws (sign < 0, or p_max_pu ≤ 0 throughout) is a sink, counted neither as supply nor as use (R16, GK B D1 / GK A C3). A non-renewable sign < 0 Generator that can also inject (p_min_pu < 0) supplies `−p` at p < 0; that injection counts gross, through an auxiliary `y ≥ −p, y ≥ 0` in the row (GK B N3, GK A ruling R1).
  - Links out of a *supply bus* into an electrical bus, counted as η(t)·p0. A supply bus has a non-renewable Generator, no Load and no Line, for example a fuel bus or a grid bus. Its own Generator is then not counted, so nothing is counted twice.
  - Storage, storage loops and elec→elec converters are not sources.
  - Any non-renewable path the fraction cannot attribute fails closed (R13): the KPI is None with a reason, and the row warns. Examples are a fuel Store, a chain, a multi-port Link, and a reversible Link with a non-renewable far side (including a time-varying one or a cycle).
- **Disclosed loophole (owner: accept and disclose):** renewable electricity dumped into a converter that nothing uses still counts as use, and grid or diesel electricity drawn into a converter counts as used. The KPI payload carries `note` (`CONVERSION_NOTE`), which names both cases.
- **Renewable test:** reuses `carrier_kpis._is_renewable_carrier`.
- **Bounded (GK B N2):** all non-renewable output counts as used on site (conservative), so the fraction is always in [0, 1]. When non-renewable output exceeds what was used (for example diesel exported through a withdrawing Generator), the KPI and that period read None with a reason, never a negative number. The LP row keeps its gross numerator.
- **Missing dispatch is never inferred (GK B A1/A2/A5, GK A B1–B3):** the KPI is None when the dispatch is missing a counted Load or a non-renewable Generator, when a source Link has neither `p0` nor `p1`, or when anything the KPI reads is NaN (Generators, slacks, Loads, source `p0`/`p1`, conversion `p0`). A source Link without `p0` reads its delivery from `−p1` (positive part only, A3). With the fallbacks gone, GK A's B1–B3 (the p_set and generator-sum fallbacks) no longer apply; B1's unsolved, Generator-less network is tested to read None.

```text
SolverConfigSchema.min_renewable_fraction  float | None, ge=0, le=1, no NaN/inf   (None/0 = off)
GET /api/results/renewable_fraction        renewable_fraction_result(n, lost-load capture, target)
  -> {fraction|None+reason, renewable_mwh, nonrenewable_mwh, served_mwh, per_period[], target, note}
get_results(kind="renewable_fraction")     chat reads the same payload
_diagnose_infeasibility                    "Minimum renewable fraction: the target of 90% (row …) needs X MWh …; at most Y MWh"
goal_free_config                           drops the key while off (OFF_WHEN_FALSY): fingerprints and IC digest unchanged
```

`renewable_fraction(n)` is the one implementation, as R2 requires.

**G13 (03a, #1423, merged at `9d629b56`).** `services/study/supply_architectures.renewable_fraction(n)` now returns `services.solver.renewable_fraction.renewable_fraction(n)["fraction"]`, and 03a's own served and non-renewable helpers are deleted. Its row's `unavailable.renewable_fraction` is `no_served_load` for no dispatch or nothing served, and `uncounted_non_renewable` otherwise (an unattributed source, an ambiguous conversion, or non-renewable output above use). **R14f, heads-up for 03a's readers:** since RENFRAC-ALL-1, the architecture column is the share of all electricity used on site. On a sector-coupled architecture, what converters draw now counts as used. 03a's tests are kept: G5 (weightings, periods, import-link delivery) and F1–F1″ / F3 / F6′. Per GK B A4, its fixtures now write a solved network's full dispatch (`loads_t.p`, and `links_t.p0` for every Link); the p1-only import test drops `p0` so that it still reads `−p1`. These expected values changed:
  - `test_renewable_fraction_stays_in_unit_interval` (was `…_clips_to_unit_interval`): a genset above the site's use reads None, not 0.0 (GK B N2).
  - `test_renewable_fraction_is_none_when_load_dispatch_is_missing` (was `…_falls_back_to_p_set…`): None, not 0.75 (GK B A1).
  - `test_an_uncounted_non_renewable_generator_makes_the_fraction_none` (F1′a): with both Links out of the grid bus dispatched, 01a counts each as a source Link, so the fraction is 0.5; without the second Link's p0 it is still None / `uncounted_non_renewable`.

  None of 03a's fixtures has a conversion Link, so no denominator changed. `test_study_one_engine` and `test_family_manifest` pass at the merge.

**UI.** Solver settings › Network tab gets a new **Off-grid constraints** block, after Reliability; 01b's inputs join it. Section 8, Scenarios & optimisation. The block is reachable in Expert and, in place, in Guided through `openFlowTargetInPlace({section:'scenarios'})`.

```diff
 <SolverSettings> tab=network
   <ReliabilityAssumptions/>
+  <OffGridConstraints draft patch/>    # pages/solver/OffGridConstraints.tsx
   <StressConditionsSection/>
```

### Evidence

Screenshots are in `.scratch/homer-parity/shots-01a/`:
- idle, value set and out-of-range, in light and dark: `{light,dark}-{idle,set,out-of-range}.png`;
- the block in its page: `light-page-context.png`;
- Guided, in place: `guided-light-value-100.png`;
- an infeasible solve's log naming the row: `dark-infeasible-log.png`.

Infeasible solve on the `eh_microgrid` template at a 100 % target with VOLL 0:

```text
[RENEWABLE] Minimum renewable fraction 100.0% (row min_renewable_fraction): non-renewable supply ≤ 0.0 MWh of 55,347.1 MWh electrical load.
[INFEASIBLE]   • Minimum renewable fraction: the target of 100% (row min_renewable_fraction) needs 55,347.1 MWh of renewable electricity served; the renewable generators can produce at most 46,539.8 MWh. …
```

**Plan conditions → tests.** All tests are in `backend/tests/test_homer_renewable_fraction.py` unless named otherwise.

| | Condition | Test |
|---|---|---|
| Acc. | 0–1 input, validated | `test_the_input_rejects_values_outside_0_to_1[-0.1,1.5,nan,inf,x]`, `test_the_input_accepts_a_fraction_or_none` |
| Acc. | KPI = hand-computed on the 3-asset fixture (PV, diesel, battery: 0.625) | `test_the_kpi_equals_the_hand_computed_fraction` |
| Acc. | row binds | `test_the_row_forces_more_renewables_and_holds` (free optimum 0.33 → ≥ 0.8) |
| Acc. | infeasible names the row | `test_an_unreachable_target_is_infeasible_and_names_the_row` |
| R1 | off is byte-identical | `test_off_keeps_master_fingerprint_input[None,0]` (pins master's sha), `test_a_target_changes_the_fingerprint_input`, `test_off_solve_is_identical_to_a_solve_without_the_field`, `test_off_solve_settings_carry_no_new_key`, `test_off_leaves_the_lp_untouched`; `test_reliability_goal.py::test_goal_free_config_drops_only_the_inert_fields` restated |
| R2 | one implementation | `renewable_fraction(n)`; 03a's function calls it after G13 |
| R3 | grid import, either shape; storage is not a source | `test_grid_import_generator_is_non_renewable`, `test_grid_import_link_from_an_electrical_grid_bus_is_counted_once`, `test_a_link_fed_through_storage_is_not_a_source`, `test_the_kpi_counts_a_link_genset_from_a_fuel_bus_and_not_the_battery`, `test_a_genset_behind_a_line_counts_at_the_generator` |
| R4 | scaled load, time-varying η | `test_the_row_uses_the_scaled_load` (×1.5 in 2035 → 360 MWh), `test_a_time_varying_efficiency_reaches_the_row` |
| R5 | slack sheds instead | `test_with_a_voll_slack_the_target_is_met_by_shedding` (and test d runs with no slack) |
| R6 | served 0 → None; NaN/inf refused | `test_nothing_served_is_none_with_a_reason`, `test_a_capture_from_another_solve_is_refused`, schema tests above |
| R7 | every settings path validates | `test_the_solver_config_put_refuses_out_of_range` (422, value unchanged), `test_the_chat_settings_tool_refuses_out_of_range`, `test_the_chat_edit_card_preview_refuses_out_of_range`. The chat tool takes a generic partial, so no inventory change is needed. `assistant_inventory.py --check` and `assistant_control_matrix.py --check` are ok. |
| R8 | new route | golden coverage, route ACL, write lock, chat disposition (`get_results` kind), `test_chat_tools_endpoint_map.py` (enum 32, inventory fixture line), `test_results_range.py` (aggregate); `assistant_route_parity.py --check` ok |
| R9 | diagnosis names the row and the reach; no row on an empty period | `test_the_infeasibility_hint_names_need_and_reach`, `test_no_infeasibility_hint_when_off`, `test_a_period_without_load_gets_no_row`, `test_a_period_with_no_load_but_a_converter_gets_a_row`, `test_a_period_with_neither_load_nor_converter_gets_no_row` |
| R10 | one row per period, on its own weightings | `test_one_row_per_investment_period`, `test_each_period_row_uses_only_its_own_weightings` (weights 1 and 2 → rhs 120 and 240), `test_horizon_totals_weight_each_period_by_its_years`, `test_the_draw_term_carries_the_generator_weighting` |
| R11 | vitest | `pages/solver/OffGridConstraints.test.tsx`: 80 → 0.8, empty/0 → null, −5/150/text refused with no patch; tsc clean |
| R12 | mutants | see below |
| R13 | fail closed on an uncounted non-renewable path: KPI None with a reason, LP row warns | `test_a_diesel_store_feeding_a_genset_fails_closed`, `test_an_import_whose_upstream_bus_feeds_another_link_fails_closed`, `test_a_multi_port_output_into_the_site_fails_closed`, `test_the_row_warns_about_a_fuel_store_genset`, `test_a_two_link_chain_into_the_site_fails_closed`, `test_gas_through_smr_and_a_fuel_cell_fails_closed`, `test_a_grey_h2_generator_behind_a_reversible_electrolyser_fails_closed`, `test_gas_through_an_smr_behind_a_reversible_electrolyser_fails_closed`, `test_a_grey_h2_store_behind_a_reversible_electrolyser_fails_closed`, `test_a_grey_h2_generator_behind_a_time_varying_reversible_link_fails_closed` (GK A C1), `test_a_link_cycle_behind_a_grey_source_terminates_and_fails_closed` (GK A C2); storage is still not a source: `test_a_battery_store_with_electrical_inflow_reads_as_storage`, `test_a_battery_fed_only_by_electricity_is_not_refused`, `test_a_reversible_h2_loop_without_a_non_renewable_source_is_not_refused` |
| R14 | RENFRAC-ALL-1: what converters draw is used on site; storage loops and export are not use; the loophole is disclosed | `test_conversion_links_are_classified_as_use`, `test_the_kpi_counts_what_converters_draw_as_used`, `test_the_row_puts_the_conversion_draw_on_the_left`, `test_an_inactive_converter_is_not_counted`, `test_a_second_electrical_input_counts_its_draw`, `test_a_second_electrical_input_given_in_time_reaches_the_row` / `_kpi`, `test_a_battery_charger_is_not_use`, `test_an_h2_storage_loop_is_not_use`, `test_a_reversible_converter_is_storage_or_ambiguous_never_use`, `test_an_export_link_is_not_use`, `test_a_renewable_electrolyser_lets_more_diesel_serve_the_load`, `test_dumping_renewables_into_an_unused_converter_buys_allowance`, `test_the_conversion_note_names_both_loopholes`, `test_each_site_template_classifies_its_conversion_links[*]`, `test_the_concentrator_process_links_are_use` |
| R15 | an ambiguous conversion fails closed with a plain-words reason (net draw: follow-up 06) | `test_an_h2_bus_with_an_offtake_and_a_fuel_cell_fails_closed`, `test_a_dispatched_negative_offtake_with_a_fuel_cell_is_ambiguous_not_unattributed` |
| R16 | a withdrawing Generator is a sink, not a supply (GK B D1, GK A C3) | `test_a_negative_generator_offtake_with_a_fuel_cell_fails_closed[*]`, `test_a_negative_generator_offtake_given_in_time_is_a_sink`, `test_a_negative_generator_offtake_without_a_fuel_cell_is_use[*]`, `test_a_slack_or_positive_generator_on_the_h2_bus_is_not_a_sink[*]`, `test_a_two_way_generator_on_the_h2_bus_is_not_a_sink`, `test_a_withdrawing_lost_production_slack_on_the_h2_bus_is_not_a_sink`, `test_a_withdrawing_generator_on_the_site_bus_is_not_non_renewable_supply[*]`, `test_a_withdrawing_generator_on_the_site_bus_is_not_in_the_row[*]`, `test_a_full_target_still_lets_renewables_export` |
| N1 | a two-way grid Generator counts only its import in the KPI | `test_a_two_way_grid_generator_counts_only_its_import_in_the_kpi` (80/240; kills clip→abs) |
| N2 | 0 ≤ fraction ≤ 1; non-renewable output above the site's use reads None | `test_diesel_exported_beyond_the_site_use_reads_none_not_negative`, `test_the_fraction_stays_within_0_and_1_up_to_the_bound[*]`; 03a `test_renewable_fraction_stays_in_unit_interval` |
| N3 / GK A R1 | a non-renewable sign < 0 Generator that can inject counts its injection gross, in the KPI and the row | `test_a_sign_negative_generator_that_can_inject_is_counted_gross` (170 of 240), `test_a_sign_negative_two_way_generator_that_only_withdraws_reads_a_value`, `test_a_sign_negative_injecting_generator_missing_from_the_dispatch_is_none`, `test_the_row_counts_a_sign_negative_injection_gross`, `test_a_binding_row_holds_against_a_cheap_sign_negative_injection[static,time-series]` (w = 2; with the term dropped, all three fail), `test_a_sign_negative_generator_that_cannot_inject_is_not_unattributed`; GK A G4: the bus receives sign·p, so the injection is weighed by |sign| (KPI (−p)⁺·|sign|, row coefficient |sign|·w): `test_a_sign_minus_two_injection_counts_twice_its_p` (sign −2, p −5 → 10 MW), `test_the_row_weighs_a_sign_minus_two_injection_by_two`; GK A G2: the binding test asserts the fraction sits on 0.8 |
| G13 | 03a calls 01a | `test_homer_supply_architectures.py` (all) |
| A1 | a Load missing from the dispatch → None | `test_no_load_dispatch_is_unavailable_even_with_generator_dispatch`, `test_a_load_missing_from_the_dispatch_reads_as_not_solved`, `test_a_load_added_after_a_solve_reads_as_not_solved` (real solve) |
| A2 | a source Link with neither p0 nor p1 → None, never inferred | `test_an_import_link_without_p0_or_p1_reads_unavailable`, `test_a_second_consumer_on_the_supply_bus_without_p0_reads_unavailable` |
| A3 | −p1 read, positive part only | `test_an_import_link_without_p0_reads_its_delivery_from_p1`, `test_a_positive_p1_delivers_nothing` |
| A5 | a non-renewable Generator missing from the dispatch → None | `test_a_generator_added_after_the_solve_reads_as_unavailable` (was `…_reads_as_zero`), `test_a_renewable_generator_added_after_the_solve_is_not_read`, `test_no_generator_dispatch_is_unavailable` |
| NaN | a NaN in a read dispatch → None (03a F3) | `test_a_nan_in_the_dispatch_reads_none[gen,load]`, `test_a_nan_in_an_import_link_dispatch_reads_none`, `test_a_nan_in_an_import_link_p1_reads_none`, `test_a_nan_in_a_slack_generator_reads_none`, `test_a_nan_in_a_conversion_link_reads_none`, `test_a_nan_in_an_unread_column_is_ignored` |
| B1 | an unsolved network with no Generators reads None | `test_an_unsolved_network_without_generators_reads_none` |
| U1–U4 | strings and block | vitest "never shows the engine key, and says 0 means no minimum"; out-of-range refusal opens with the label |
| U6 | edit-card label parity | `test_the_chat_edit_card_label_matches_the_panel` (reads the TSX constant) |

**Review rounds.**

| Round | Head | Verdicts | Fixed in |
|---|---|---|---|
| code review | before round 1 | 7 findings (comment on this PR) | `bad11cdf`, `1bc603d1` |
| 1 | `1bc603d1` | UX OK; GK B NOT OK F1–F5; GK A NOT OK B1–B3 (N2, N3 non-blocking) | `26fd1915`, together with RENFRAC-ALL-1 (R14) |
| 2 | `26fd1915` | UX OK; GK B NOT OK F2′, R15, R16, G13; GK A NOT OK A1–A4 (M6b non-blocking) | `a0dd0211` (F2′, R15, R16, follow-up 06), `38111bab` (A1–A4) |
| 3 | `38111bab` | GK B NOT OK D1, M9, M10, M12; GK A NOT OK C1–C3 | `d706ea73` (D1 = C3, M9/M10/M12), `98c90adf` (C1, C2) |
| 4 | `98c90adf` | GK A OK (LP row and KPI); GK B NOT OK N1–N3 (D1, M9, M10, M12 verified) | `576bc832` (N1–N3, GK A's "source(s)" wording), then master merge `be5a8786` and G13 `37caafe7` |
| 5 | `37caafe7` | UX OK; GK B NOT OK A1–A5 (N1–N3 and the G13 wiring verified; the dispatch fallbacks rejected); GK A NOT OK R1 (count N3 gross), B1–B3, NaN killers | `687cad99` (GK B A1–A5), `e8bd3c51` (GK A R1, NaN killers, B1) |
| 6 | `e8bd3c51` | GK B NOT OK: routes fake solver (A4), NaN in an injecting Generator, slack missing from the dispatch (A1–A3, A5, F1′a, N3 KPI verified) | `b02b3740` |
| 6 (GK A) | `e8bd3c51` | GK A NOT OK G1–G4 (crossed the hand-off; G1, G3 = GK B's round 6) | `b02b3740` (G1, G3), `35f33940` (G2, G4) |

**Mutants (R12).** `scripts/coord/mutants.py services/solver/renewable_fraction.py tests/test_homer_renewable_fraction.py` at the final head:

_Rerun in progress at this head (about 2 h); this table replaces the stale 63eb4ac3 run (227/252 killed) when it completes._

### Merge Danger

**Door:** two-way. The feature is off by default and adds a field and a route; nothing is migrated.

**Blast radius:** small. With the field unset, `build_lp_chain` returns the same callable, `goal_free_config` emits the same dict (pinned sha) and the solve settings carry no new key. The chat `get_results` enum grows by one kind (ChatPanel tool-list vitests pass). G13 changes 03a's architecture column: a genset above the site's use now reads None instead of 0.0, and on sector-coupled architectures the denominator includes what converters draw.

### Skipped and known gaps
- Heat and hydrogen loads are not in the fraction (electrical side only, documented); what converters draw from electricity is (R14).
- A bus fed by both a renewable and a non-renewable Generator counts whole as non-renewable (conservative, documented).
- The Issues panel's "No feasible solution" card stays generic. The row is named in the solve log only. Follow-up 05 (UX).
- A fuel-Store genset makes the KPI None (fail closed, R13). Counting its η·p0 as non-renewable is a follow-up.
- Two chained grid supply buses (grid A → grid B → site) fail closed (KPI None); they are not counted once. Conservative; a follow-up can count the chain.
- The flagship H2 hub KPI reads None: its electrolyser is an ambiguous conversion (GK B ruling 1, R15). Net draw for ambiguous clusters is follow-up 06.
- (GK B) A Store with no carrier, on a bus with no carrier, that feeds a genset reads as storage.
- (GK B) An H2 Store directly on an AC bus fails closed.
- (GK B) A `SolverConfig` built directly with −0.1 (bypassing the schema) changes the fingerprint.
- An "H2" tank beside a reversible electrolyser fails closed. This is conservative; GK A accepted it as a named gap and needs no exception (no eh template hits it).
- A two-way Generator (p_min_pu < 0 < p_max_pu) with sign > 0 is not a sink.
- `renewable_generators`, which only sets the infeasibility hint's reach, includes a withdrawing renewable Generator.
- (GK B N2) All non-renewable output counts as used on site, a conservative attribution. Diesel that is exported, or that charges storage the site never uses back, lowers the fraction; above the site's use, the KPI reads None.
- In 03a's rows, `uncounted_non_renewable` also covers an ambiguous conversion, a NaN dispatch and non-renewable output above use; the code's name is kept. (03a used to report a NaN dispatch as `no_served_load`.)
- A conversion Link missing from `links_t.p0` draws 0. That lowers the denominator, which is conservative; it is not refused.
- `reserve_margin` has the same load/import validation gap; it is not changed here.
- No results UI reads `/results/renewable_fraction` yet. The KPI is served to the API and the chat; UX asked to see the `note` wording when a UI first reads it.

## Checklist

**Required:**
- [x] Changes are tested locally and behave as expected.
- [x] Code and workflow changes are documented.
- [x] A release note is added as a new file under `doc/release-notes.d/` (see its `README.md`); `doc/release_notes.md` is not edited directly.
- [x] The description is human-written and any AI-generated content is marked. (AI-generated by Claude Code.)

Session: session_01G7qCumdqnF5AZh3LUFg5bi

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G7qCukA7PBck4tEDPJ2ifm
