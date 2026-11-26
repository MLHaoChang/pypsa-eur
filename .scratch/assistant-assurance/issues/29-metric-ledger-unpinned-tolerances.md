<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Metric-ledger tolerances their oracle tests do not use

Status: ready-for-agent
Type: task
Reviewer: GK B

Split out of [issue 10](10-metric-calculation-and-validity.md) R5 on 2026-11-26 (GK B (73) ruling on R5 condition 5, option C).

R5 runs `test_a_reviewed_row_names_the_tolerance_its_oracle_test_uses` (`pypsa-gui/backend/tests/assurance_acceptance/test_metric_ledger.py`) over every row whose `domain_review` is not `pending`. The 35 verified rows below name a tolerance constant that their oracle test's module never mentions, so none of them can take a `gk-b:` sha. They stay `pending` in the domain review, with a one-line note naming the constant. `investment-case/1 project_irr` held GK B (72)'s R2 OK (`e0be8aa7`); R5 returned it to `pending` on this ground.

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
