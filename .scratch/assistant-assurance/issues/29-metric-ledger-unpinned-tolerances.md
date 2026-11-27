<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Metric-ledger tolerances their oracle tests do not use

Status: ready-for-agent
Type: task
Reviewer: GK B

Split out of [issue 10](10-metric-calculation-and-validity.md) R5 on 2026-11-26 (GK B (73) ruling on R5 condition 5, option C).

R5 runs `test_a_reviewed_row_names_the_tolerance_its_oracle_test_uses` (`pypsa-gui/backend/tests/assurance_acceptance/test_metric_ledger.py`) over every row whose `domain_review` is not `pending`. The 35 verified rows in the first part of the table below name a tolerance constant that their oracle test's module never mentions, so none of them can take a `gk-b:` sha. They stay `pending` in the domain review, with a one-line note naming the constant. `investment-case/1 project_irr` held GK B (72)'s R2 OK (`e0be8aa7`); R5 returned it to `pending` on this ground.

Three more rows are unpinned in fact, though the check passes them. GK B (73) found this in the R5 table review: `energy-balance/1` `generation_energy`, `served_energy` and `unserved_energy` name `harness.energy_balance_calculations:SERVED_TOL`. Their oracle, `test_energy_balance_assurance.py::test_a_shortfall_comes_off_served_as_it_comes_off_generation`, compares at plain `approx` with a literal `abs=1e-6`. `SERVED_TOL` is imported there and used only by other tests. These rows stay `pending` too and are listed below.

**Limit of the check.** `unpinned_reviews` checks that the constant's name appears somewhere in the oracle test's file. It cannot prove that the oracle compares at that constant. Part of this issue is to tighten the check, for example by resolving the oracle function's source with `ast` and finding the constant inside it. When that is done, rows that are already reviewed must still pass it.

## What to do

For each row, take one of two fixes, the one the table names:

- **Compare at the named constant:** the oracle test imports the constant and asserts at it (`pytest.approx(..., rel=CONST)` or the module's own comparison).
- **The ledger string becomes what the test really uses:** for threshold constants the metric is a count that the test compares exactly, so the tolerance becomes `exact`. The constant stays part of the formula or validity text.

Then send the rows to GK B for domain review. A row that passes takes the sha of that PR's OK.

## Acceptance

- Every row below is pinned. Widened to all verified rows, `unpinned_reviews` returns `[]`.
- Each row's oracle still asserts its hand number; no oracle test is weakened.

## Rows

| profile | metric | tolerance | oracle test | fix |
|---|---|---|---|---|
| ac-branch-loading/1 | ac_hours_above_rating | `harness.branch_calculations:OVER_LIMIT_TOLERANCE` | `test_p2_request_slots.py::test_a_non_converged_interval_keeps_its_warning_slot_whatever_is_asked` | ledger string becomes what the test really uses (`exact`: the constant is the metric's threshold, the count compares exactly) |
| branch-loading/1 | hours_above_rating | `harness.branch_calculations:OVER_LIMIT_TOLERANCE` | `test_branch_loading_assurance.py::test_an_observed_overload_counts_weighted_hours_like_the_tab` | ledger string becomes what the test really uses (`exact`: the constant is the metric's threshold, the count compares exactly) |
| bus-price/1 | hours_at_voll | `harness.price_calculations:VOLL_TOLERANCE` | `test_result_profiles_assurance.py::test_hours_at_voll_use_the_solves_voll_and_need_one` | ledger string becomes what the test really uses (`exact`: the constant is the metric's threshold, the count compares exactly) |
| carrier-energy/1 | carrier_storage_charged_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_carrier_assurance.py::test_battery_carrier_reads_split_storage_power_and_an_empty_generator_set` | compare at the named constant |
| carrier-energy/1 | carrier_storage_discharged_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_carrier_assurance.py::test_battery_carrier_reads_split_storage_power_and_an_empty_generator_set` | compare at the named constant |
| copt-screening/1 | screening_eue | `harness.copt_calculations:REL_TOLERANCE` | `test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it` | compare at the named constant |
| copt-screening/1 | screening_lole | `harness.copt_calculations:REL_TOLERANCE` | `test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it` | compare at the named constant |
| copt-screening/1 | screening_lolp_max | `harness.copt_calculations:REL_TOLERANCE` | `test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it` | compare at the named constant |
| curtailment/1 | available_energy | `harness.curtailment_calculations:RESIDUAL_TOLERANCE` | `test_result_profiles_assurance.py::test_curtailment_of_a_variable_generator` | compare at the named constant |
| curtailment/1 | curtailed_energy | `harness.curtailment_calculations:RESIDUAL_TOLERANCE` | `test_result_profiles_assurance.py::test_curtailment_of_a_variable_generator` | compare at the named constant |
| curtailment/1 | curtailment_share | `harness.curtailment_calculations:RESIDUAL_TOLERANCE` | `test_result_profiles_assurance.py::test_curtailment_of_a_variable_generator` | compare at the named constant |
| investment-case/1 | equity_irr | `harness.investment_calculations:IRR_TOLERANCE` | `test_investment_assurance.py::test_hand_cash_with_a_nonzero_first_year` | compare at the named constant |
| investment-case/1 | equity_npv | `harness.investment_calculations:REL_TOLERANCE` | `test_investment_assurance.py::test_hand_cash_with_a_nonzero_first_year` | compare at the named constant |
| investment-case/1 | project_irr | `harness.investment_calculations:IRR_TOLERANCE` | `test_investment_assurance.py::test_project_irr_of_one_sign_change_is_the_hand_root` | compare at the named constant |
| investment-case/1 | project_npv | `harness.investment_calculations:REL_TOLERANCE` | `test_investment_assurance.py::test_hand_cash_with_a_nonzero_first_year` | compare at the named constant |
| investment-lcoe/1 | lcoe_nominal | `harness.investment_calculations:LCOE_TOLERANCE` | `test_investment_assurance.py::test_the_lcoe_from_the_retained_series` | compare at the named constant |
| investment-lcoe/1 | lcoe_real | `harness.investment_calculations:LCOE_TOLERANCE` | `test_investment_assurance.py::test_the_lcoe_from_the_retained_series` | compare at the named constant |
| lost-load/1 | shed_hours | `harness.lost_load_calculations:SHED_THRESHOLD_MW` | `test_lost_load_assurance.py::test_lost_load_matches_the_hand_values_from_a_real_solve` | ledger string becomes what the test really uses (`exact`: the constant is the metric's threshold, the count compares exactly) |
| network-loading/1 | branches_above_rating | `harness.branch_calculations:OVER_LIMIT_TOLERANCE` | `test_branch_loading_assurance.py::test_network_counts_branches_above_rating_as_an_integer` | ledger string becomes what the test really uses (`exact`: the constant is the metric's threshold, the count compares exactly) |
| site-import/1 | hours_at_import_limit | `harness.site_import_calculations:AT_LIMIT` | `test_site_import_assurance.py::test_export_is_what_the_meter_delivers_into_the_grid_bus` | ledger string becomes what the test really uses (`exact`: the constant is the metric's threshold, the count compares exactly) |
| storage-cycles/1 | energy_capacity | `harness.storage_cycles_calculations:REL_TOLERANCE` | `test_capacity_cycles_assurance.py::test_a_non_cyclic_unit_matches_the_storage_profile` | compare at the named constant |
| storage-cycles/1 | equivalent_cycles | `harness.storage_cycles_calculations:REL_TOLERANCE` | `test_capacity_cycles_assurance.py::test_a_non_cyclic_unit_matches_the_storage_profile` | compare at the named constant |
| storage-cycles/1 | window_discharged_energy | `harness.storage_cycles_calculations:REL_TOLERANCE` | `test_capacity_cycles_assurance.py::test_a_non_cyclic_unit_matches_the_storage_profile` | compare at the named constant |
| storage-unit-cost/1 | charged_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle` | compare at the named constant |
| storage-unit-cost/1 | discharged_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle` | compare at the named constant |
| storage-unit-cost/1 | final_state_of_charge | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle` | compare at the named constant |
| storage-unit-cost/1 | net_storage_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle` | compare at the named constant |
| storage-unit-cost/1 | peak_charging_power | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle` | compare at the named constant |
| storage-unit-cost/1 | peak_discharging_power | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle` | compare at the named constant |
| storage-unit-dispatch/1 | charged_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations` | compare at the named constant |
| storage-unit-dispatch/1 | discharged_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations` | compare at the named constant |
| storage-unit-dispatch/1 | final_state_of_charge | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations` | compare at the named constant |
| storage-unit-dispatch/1 | net_storage_energy | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations` | compare at the named constant |
| storage-unit-dispatch/1 | peak_charging_power | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations` | compare at the named constant |
| storage-unit-dispatch/1 | peak_discharging_power | `harness.storage_calculations:REL_TOLERANCE` | `test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations` | compare at the named constant |
| energy-balance/1 | generation_energy | `harness.energy_balance_calculations:SERVED_TOL` | `test_energy_balance_assurance.py::test_a_shortfall_comes_off_served_as_it_comes_off_generation` | compare at the named constant (now `abs=1e-6`) |
| energy-balance/1 | served_energy | `harness.energy_balance_calculations:SERVED_TOL` | `test_energy_balance_assurance.py::test_a_shortfall_comes_off_served_as_it_comes_off_generation` | compare at the named constant (now `abs=1e-6`) |
| energy-balance/1 | unserved_energy | `harness.energy_balance_calculations:SERVED_TOL` | `test_energy_balance_assurance.py::test_a_shortfall_comes_off_served_as_it_comes_off_generation` | compare at the named constant (now `abs=1e-6`) |

Outside the table above, previously reviewed: widening the check to all verified rows flags these four. They keep their `gk-b:` sha and GK B re-confirms them in this PR's domain review.

| profile | metric | was | fix |
|---|---|---|---|
| branch-losses/1 | loss_pct_of_demand | `harness.derived:NEAR_ZERO` | ledger string becomes `ulp:4504` (the oracle's rel 1e-12); NEAR_ZERO stays in the validity text |
| renewable-share/1 | renewable_share | `harness.derived:NEAR_ZERO` | same |
| system-cost-breakdown/1 | system_capex | `harness.system_cost_calculations:RESIDUAL_TOLERANCE` | oracle also asserts at the constant |
| system-cost-breakdown/1 | objective_residual | `harness.system_cost_calculations:RESIDUAL_TOLERANCE` | oracle also asserts at the constant |
