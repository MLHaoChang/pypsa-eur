"""Check/write the LC-06 family execution manifest.

Assigns every assistant tool one PRIMARY capability-matrix family (of 14): the
family that owns its native seam. A tool other families also use (get_results,
run_simulation, find_capabilities) stays in its primary family only; each
variant lists the tools it exercises in ``tools``, which is where shared use
shows. There is no ``shared`` family, and the guard refuses a tool in two
families.

Each named engine/control variant of the LC-D01–24 domain suites
(`.scratch/live-copilot/domain-coverage.md`) has a disposition:

- ``covered``: public-seam tests (chat tool, checked stream or authenticated
  API) that assert an independent hand oracle. ``tests`` are pytest node ids
  that must collect; ``oracle`` names the hand value or source they assert;
- ``pending``: the gap, naming any engine-only seed oracle;
- ``unavailable``: a claim the application does not make, with ``pointer`` to
  the issue or the code that refuses it.

Denominator: every inventory tool, mapped or not. A tool no family names is
printed as unmapped and counts as not covered; it never fails the check (other
packages add tools) and is never dropped from the total. A tool is covered when
a covered variant lists it. ``full_coverage`` stays ``not_established``.
"""
from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / '.scratch/live-copilot/family-manifest.json'
B = 'pypsa-gui/backend/tests/'
G = 'tests/gridspine/'
D15_TOOLS = 'campus_set_island_config campus_island_config campus_run_island_study'
#: E1-E6 read through GET .../island/report: the chat tool trims the events away (ticket 27).
D16_TOOLS = 'campus_set_island_config campus_draft_campus campus_run_study'
D17_TOOLS = (D16_TOOLS + ' campus_run_island_study campus_recommend_gfm_settings campus_island_gfm_settings '
             'campus_gfm_settings_card campus_apply_gfm_settings')

# The capability-matrix families, in `assistant_control_matrix.FAMILY_JOURNEYS`
# order, keyed by their journey slug.
FAMILIES: dict[str, str] = {
    'projects': 'Projects/scenarios/templates',
    'network-topology': 'Network/topology/asset parameters',
    'timeseries-horizon': 'Time series/horizon/multi-period',
    'solver-simulation': 'Solver/simulation',
    'all-results': 'All results',
    'reliability-adequacy': 'Reliability/health/adequacy',
    'gridspine-campus': 'GridSpine/campus electrical',
    'economics-investment': 'Economics/investment/Library',
    'experiments': 'Experiments/sensitivity',
    'import-export': 'Import/export/delivery',
    'reports': 'Reports/templates/edit round trip',
    'audit-undo': 'Audit/undo/checkpoints',
    'harness-workflows': 'Harness/workflows/providers',
    'ui-monitoring': 'UI/preferences/monitoring',
}

# Domain-coverage annex, "Map the actual 14 families to suites".
SUITES_BY_FAMILY: dict[str, tuple[int, ...]] = {
    'projects': (1, 19, 22),
    'network-topology': (2, 9, 15),
    'timeseries-horizon': (3, 5, 9),
    'solver-simulation': (4, 5, 6, 7, 14, 16, 17),
    'all-results': tuple(range(4, 19)),
    'reliability-adequacy': (11, 12, 13),
    'gridspine-campus': (14, 15, 16, 17),
    'economics-investment': (4, 5, 9, 10, 18),
    'experiments': (19,),
    'import-export': (20,),
    'reports': (21,),
    'audit-undo': (22,),
    'harness-workflows': (23,),
    'ui-monitoring': (24,),
}

_CAMPUS_ECONOMICS = {'campus_get_investment', 'campus_get_library', 'campus_set_library', 'campus_reset_library'}

TOOLS_BY_FAMILY: dict[str, frozenset[str]] = {f: frozenset(names.split()) for f, names in {
    'projects': """
        activate_project create_project_from_template create_scenario delete_project list_projects
        load_project rename_project save_project save_project_a_copy save_project_as list_scenarios
        compare_scenarios update_project_scenario_metadata update_project_settings resolve_project_target
        get_project_sites set_project_sites get_project_layout update_project_layout get_project_flow
        get_project_flow_scope update_project_flow_scope get_project_network_meta get_meta update_meta
        project_readiness get_site_template_form fetch_site_context geocode_place get_osm_suggestions
        list_project_network_component set_site_connection""",
    'network-topology': """
        batch_create_components batch_delete_components bulk_update_components cascade_delete_bus
        create_carrier create_component delete_component get_component get_component_schema list_carriers
        list_components list_global_constraints list_transformer_types update_component validate_network
        diagnose_network cluster_network derive_lengths_from_geometry recalculate_line_lengths
        rescale_line_impedances reset_network reconstruct_network_from_image get_asset_reference
        resolve_reference apply_project_changes preview_project_changes apply_equipment_mapping
        preview_equipment_mapping apply_document_values preview_document_values""",
    'timeseries-horizon': """
        apply_default_series preview_default_series apply_demand_from_excel apply_generated_profile
        apply_generated_profiles compare_profiles delete_timeseries generate_availability_profile
        generate_exemplary_timeseries generate_load_profile generate_renewable_profile
        generate_series_profile get_aggregate_load get_current_series get_profile_parameters
        get_profile_readiness get_timeseries get_weather_choice list_all_timeseries list_investment_periods
        list_load_profile_archetypes list_pending_profiles list_profile_presets list_snapshots
        list_timeseries_profiles pending_profile_from_current preview_renewable_profile
        refine_generated_profile sample_representative_weeks set_investment_period_weightings
        set_investment_periods set_multi_period_snapshots set_snapshot_weightings set_snapshots
        set_weather_choice upload_generator_profile upload_link_profile upload_load_profile
        upload_load_profiles_file upload_series_file upload_snapshot_weightings_csv upload_timeseries
        list_vintage_bounds set_vintage_bounds delete_vintage_bounds cleanup_orphan_vintages""",
    'solver-simulation': """
        abort_simulation check_solver_availability dispatch_status force_reset_simulation
        get_simulation_lock_status get_simulation_log_history get_simulation_status get_solver_capabilities
        get_solver_config run_ac_pf_stage run_simulation solve_queue_abort solve_queue_cancel_queued
        solve_queue_clear_finished solve_queue_dismiss solve_queue_enqueue solve_queue_job_log
        solve_queue_list solve_queue_requeue update_solver_config wait_for_job""",
    'all-results': """
        compare_project_runs discuss_project_run get_asset_results get_curtailment_by_cause get_energy_flows
        get_load_shape get_loadflow_summary get_project_results_bundle get_project_results_summary
        get_project_statistics get_project_compare_state get_results get_run_evidence get_vintage_results
        list_project_runs list_result_assets list_result_views read_result""",
    'reliability-adequacy': """
        abort_adequacy_study get_adequacy_results get_asset_health record_asset_health get_fmea_worksheet
        save_fmea_worksheet run_fmea_sweep get_reliability_goal get_stress_scenarios put_stress_scenarios
        list_stress_profile_packs run_coupling_loop run_frontier_study run_margin_loop run_mc_study
        campaign_status start_campaign end_campaign run_eh_study review_eh_study get_eh_readiness
        get_eh_template suggest_eh_setup""",
    'economics-investment': """
        abort_decision_study abort_investment_case apply_commercial_document_values
        preview_commercial_document_values apply_library_reference preview_library_reference attach_tariff
        define_participants delete_decision_study explain_cashflow explain_investment explain_verdict
        get_asset_costs get_decision_study get_finance_inputs get_investment_case get_library_item
        get_library_reference get_library_series import_library_series import_urdb_tariff
        list_decision_studies list_library_items list_library_references list_library_series preview_billing
        preview_decision_study_intake put_library_item run_decision_study run_investment_case
        set_finance_inputs solve_ppa_price start_decision_study update_decision_study set_study_parameter
        get_study_findings get_poc_state get_value_flow_designer""",
    'experiments': """
        advance_experiment cancel_experiment compare_experiments compare_sensitivities get_experiment
        list_experiments prepare_experiment reconcile_experiment refresh_experiment run_sensitivity_sweep
        start_experiment""",
    'import-export': """
        build_delivery download_project_bundle export_asset_results export_csv_bundle export_excel
        export_experiment_comparison export_matpower export_network_nc export_preview_png export_run_evidence
        export_sensitivity_comparison export_to_csv export_to_excel get_file_delivery import_csv_bundle
        import_excel import_map_geometry import_matpower import_meter_data import_network_nc
        import_project_bundle import_uploaded_network inspect_import read_excel_sheet read_upload_meta
        list_uploads delete_upload clear_uploads download_snapshot_weightings_csv
        download_timeseries_template create_chart""",
    'reports': """
        abort_report_generation build_study_report create_evidence_report delete_report diff_report_versions
        export_eh_report_docx export_report_docx export_report_pdf generate_report generate_study_report
        get_report get_report_capabilities get_report_evidence_hash get_report_status get_report_table
        get_report_template get_report_version import_edited_report list_report_roundtrips
        list_report_templates list_reports propose_report_mapping regenerate_report_section
        set_report_mapping set_report_template""",
    'audit-undo': """
        audit_log clear_audit_log create_project_snapshot delete_project_snapshot list_project_snapshots
        restore_project_snapshot undo_last undo_status""",
    'harness-workflows': """
        advance_workflow ask_user cancel_task clear_chat_history complete_goal end_workflow
        export_chat_summary find_capabilities get_feature_guide get_task list_chat_history list_tasks
        pause_goal resolve_task_step resume_task set_goal start_task start_workflow use_skill use_toolset
        set_active_profile""",
    'ui-monitoring': 'ui_open_asset_detail ui_open_panel ui_select_component ui_set_snapshot',
    'gridspine-campus': '',  # every campus_*/gridspine_* tool bar _CAMPUS_ECONOMICS, plus below
}.items()}


CHECKED_STREAM = 'checked stream (/api/chat/answers): ledger from run evidence, no catalogue tool'


def _covered(vid: str, oracle: str, tools: str, *tests: str) -> dict:
    """``tools`` empty means the checked stream, which answers from run
    evidence without calling a catalogue tool; its tools stay uncovered."""
    return {'id': vid, 'suite': vid.split('.')[0], 'status': 'covered', 'oracle': oracle,
            'tools': tools.split(), 'seam': 'chat tools' if tools.split() else CHECKED_STREAM,
            'tests': list(tests)}


def _pending(vid: str, reason: str) -> dict:
    return {'id': vid, 'suite': vid.split('.')[0], 'status': 'pending', 'reason': reason}


def _unavailable(vid: str, reason: str, pointer: str) -> dict:
    return {'id': vid, 'suite': vid.split('.')[0], 'status': 'unavailable', 'reason': reason, 'pointer': pointer}


# Rebaselined on master a6d73f57; see the slice log in
# .scratch/live-copilot/issues/06-family-adapters.md.
VARIANTS: list[dict] = [
    _pending('LC-D01.projects', 'seam tests check identity/ACL; no independent file/ID oracle'),
    _pending('LC-D02.topology', 'seam tests check shape; no reference-count/KCL oracle'),
    _covered('LC-D03.dynamic_weights', 'objective = 10 EUR/MWh x (5 MW x 2 h + 7 MW x 3 h) = 310, after an import round trip',
             'import_uploaded_network save_project_as run_simulation save_project get_study_evidence',
             B + 'test_assistant_capabilities.py::test_native_dynamic_roundtrip_solves_and_persists_evidence'),
    _pending('LC-D03.chronology_edges', 'DST/leap/duplicate/nonmonotonic times not mapped'),
    _covered('LC-D04.bound_build', 'extendable G2 built to p_nom_max: optimal_capacity 3 = capacity_change 3; fixed G: 20 MW, change 0',
             '',
             B + 'test_capacity_cycles_assurance.py::test_an_extendable_generator_built_to_its_bound',
             B + 'test_capacity_cycles_assurance.py::test_a_fixed_generator_says_it_was_not_optimised'),
    _covered('LC-D04.units', 'answer states "Line s_nom in MVA: apparent power."; Store cycles: discharged 4 MWh of 20 MWh = 0.2 cycles',
             '',
             B + 'test_capacity_cycles_assurance.py::test_a_line_states_mva_and_an_unbounded_build_never_reads_as_at_its_bound',
             B + 'test_capacity_cycles_assurance.py::test_a_store_counts_only_energy_leaving_it'),
    _covered('LC-D04.unique_lp', 'one-year horizon: Cheap 6 MW at 60,000 + 8760 x 10, Backup 4 MW at 20,000 + 8760 x 50, '
             'objective 2,717,600; cap 6 -> 7 lowers it by 458,000 - 147,600 = 310,400; capex/opex/GWh per carrier',
             'run_simulation get_results explain_investment',
             B + 'test_lc06_d04_capacity_lp.py::test_the_unique_optimum_matches_the_hand_solution',
             B + 'test_lc06_d04_capacity_lp.py::test_raising_the_cap_lowers_the_objective_by_the_cost_difference'),
    _covered('LC-D04.vintage_results', 'two periods with per-period bounds on G: G@2030 = 6, G@2040 = 4 MW; '
             'a single period answers no vintages',
             'set_vintage_bounds run_simulation get_vintage_results',
             B + 'test_lc06_d05_foresight.py::test_two_periods_discount_and_build_vintages_by_hand',
             B + 'test_lc06_d04_capacity_lp.py::test_a_single_period_network_reports_no_vintages'),
    _covered('LC-D05.perfect_foresight', 'horizon cost 33,000 = 3 x 100 MW x 100 EUR/MW + 3 x 100 MWh x 10; LP total on the horizon',
             'update_solver_config run_simulation get_simulation_status get_results',
             B + 'test_lc06_d05_foresight.py::test_every_foresight_reports_the_hand_horizon_cost[perfect]'),
    _covered('LC-D05.myopic', 'status 33,000 (not the per-period LP sum 13,000, reported as myopic_lp_sum)',
             'update_solver_config run_simulation get_simulation_status get_results',
             B + 'test_lc06_d05_foresight.py::test_every_foresight_reports_the_hand_horizon_cost[myopic]'),
    _covered('LC-D05.limited_foresight', 'lf_aggregate_future: status 33,000, the lookahead never double-counted',
             'update_solver_config run_simulation get_simulation_status get_results',
             B + 'test_lc06_d05_foresight.py::test_every_foresight_reports_the_hand_horizon_cost[limited]'),
    _covered('LC-D05.two_period_discount', 'r = 0.05, years 10: weight 10 x 1.05^-(p - 2030); Old (2020 + 15) retires before 2040; '
             'objective = hand PV of capex + opex; a half-year horizon charges capex x 0.5',
             'update_solver_config set_vintage_bounds run_simulation get_simulation_status',
             B + 'test_lc06_d05_foresight.py::test_two_periods_discount_and_build_vintages_by_hand',
             B + 'test_lc06_d05_foresight.py::test_a_half_year_horizon_charges_half_the_annual_capex'),
    _covered('LC-D06.full_lp', 'objectives 310 (LC-D03 fixture) and 200 = 5 MW x 2 h x 20 EUR/MWh through the experiment queue',
             'run_simulation refresh_experiment wait_for_job export_run_evidence get_file_delivery',
             B + 'test_assistant_capabilities.py::test_native_dynamic_roundtrip_solves_and_persists_evidence',
             B + 'test_experiment_case_advancement.py::test_real_case_finishes_only_with_terminal_and_intact_evidence'),
    _covered('LC-D06.infeasible', 'hand-built infeasible island: triaged by validate/diagnose, never re-run',
             'run_simulation validate_network diagnose_network get_simulation_log_history start_task get_task',
             B + 'test_lc19_run_lifecycle_journeys.py::test_oj33_an_infeasible_solve_is_triaged_and_never_rerun'),
    _covered('LC-D06.rolling', 'windows of 2 with overlap 1 over loads 10/20/30/40 at 2 EUR/MWh: status, run record and queue reader 200 '
             '(not the last window); decomposition labels its LP figure last_window',
             'update_solver_config run_simulation get_simulation_status list_project_runs get_run_evidence get_results',
             B + 'test_lc06_d05_foresight.py::test_a_rolling_solve_reports_the_cost_of_every_window',
             B + 'test_lc06_d05_foresight.py::test_a_rolling_run_without_statistics_reports_no_cost'),
    _covered('LC-D06.myopic_solve', 'see LC-D05.myopic and LC-D05.limited_foresight',
             'update_solver_config run_simulation get_simulation_status',
             B + 'test_lc06_d05_foresight.py::test_every_foresight_reports_the_hand_horizon_cost[myopic]',
             B + 'test_lc06_d05_foresight.py::test_every_foresight_reports_the_hand_horizon_cost[limited]'),
    _pending('LC-D06.sclopf', 'engine seed only: test_fixed_sizes.py'),
    _pending('LC-D06.ac_stage', 'lifecycle/refusal only, no numbers'),
    _covered('LC-D06.solver_outcomes', 'unbounded and unavailable solver refused by the real validator before the solver; '
             'a limit stop with no incumbent (real HiGHS; stubbed return at the full, SCLOPF and myopic call sites) = failed, '
             'no objective; with an incumbent = completed, record condition time_limit; MIP gap 0.25 bounds the UC objective (hand 640)',
             'update_solver_config run_simulation get_simulation_status get_simulation_log_history list_project_runs get_run_evidence',
             B + 'test_lc06_d05_foresight.py::test_an_unbounded_lp_is_refused_before_the_solver',
             B + 'test_lc06_d05_foresight.py::test_an_unavailable_solver_is_refused_by_the_real_dispatcher',
             B + 'test_lc06_d05_foresight.py::test_the_hard_uc_solves_without_a_limit',
             B + 'test_lc06_d05_foresight.py::test_a_real_time_limit_without_an_incumbent_is_a_failed_run',
             B + 'test_lc06_d05_foresight.py::test_a_limit_stop_without_an_incumbent_fails_at_every_call_site',
             B + 'test_lc06_d05_foresight.py::test_a_limit_stop_with_an_incumbent_completes_but_never_reads_as_optimal',
             B + 'test_lc06_d05_foresight.py::test_a_mip_gap_reaches_the_solver_and_bounds_the_objective'),
    _covered('LC-D07.active_proxy', 'basis active_power_proxy only; line values equal the test LINE dict and the tab peak_branch_utilisation',
             'get_loadflow_summary',
             B + 'test_loadflow_summary.py::test_lopf_is_an_active_power_proxy_and_never_claims_compliance',
             B + 'test_branch_loading_assurance.py::test_line_loading_matches_the_hand_values_and_the_load_flow_tab'),
    _covered('LC-D07.ac_apparent', 'apparent loading hypot(95, 60) from hand-set p and q flows',
             'get_loadflow_summary',
             B + 'test_loadflow_summary.py::test_ac_pf_uses_apparent_power_where_reactive_flows_exist'),
    _covered('LC-D07.convergence_mask', '1 unconverged snapshot excluded; L_hot util 30 %',
             'get_loadflow_summary',
             B + 'test_loadflow_summary.py::test_ac_pf_excludes_unconverged_snapshots_and_reports_them'),
    _covered('LC-D07.weighting', 'h_at_limit 3.0, mean util (95x3+10+20+100x3)/8, weighted hours 8; s_max_pu=0 -> zero_effective_limit',
             'get_loadflow_summary',
             B + 'test_loadflow_summary.py::test_weighted_snapshots_drive_hours_and_mean',
             B + 'test_loadflow_summary.py::test_zero_effective_limit_from_s_max_pu_is_skipped'),
    _covered('LC-D07.missing_q', 'LOPF line values equal the test LINE dict despite Q left by an earlier PF',
             'get_loadflow_summary',
             B + 'test_branch_loading_assurance.py::test_q_left_by_an_earlier_power_flow_is_never_mixed_into_lopf_flow'),
    _covered('LC-D07.static_pf_oracle', 'meshed 3-bus AC PF equals an independent scipy solve of the hand-built Ybus (V 1e-9 pu, flows 1e-6 MW); KCL 60+j80 -> 100 MVA at the load bus; exact 100.00 MVA on the LC-T07 fixture',
             'run_simulation run_ac_pf_stage update_solver_config get_simulation_status get_results get_loadflow_summary',
             B + 'test_lc06_d07_static_pf.py::test_the_independent_solve_closes_kcl_and_meets_the_100_mva_load',
             B + 'test_lc06_d07_static_pf.py::test_bus_voltages_equal_the_independent_solve',
             B + 'test_lc06_d07_static_pf.py::test_line_flows_at_bus0_equal_the_independent_solve',
             B + 'test_lc06_d07_static_pf.py::test_both_terminals_close_kcl_at_the_100_mva_load_bus',
             B + 'test_lc06_d07_static_pf.py::test_losses_and_the_slack_equal_the_independent_solve',
             B + 'test_lc06_d07_static_pf.py::test_the_summary_ranks_on_the_independent_apparent_power',
             B + 'test_lc06_d07_static_pf.py::test_p60_q80_at_both_terminals_is_exactly_100_mva'),
    _unavailable('LC-D07.n1_voltage', 'no N-1 or voltage compliance claim is made',
                 'pypsa-gui/backend/services/loadflow_summary.py LIMITS_CLAIM'),
    _covered('LC-D08.dispatch', 'lost load and curtailment equal the values hand-computed in each test',
             '',
             B + 'test_lost_load_assurance.py::test_lost_load_matches_the_hand_values_from_a_real_solve',
             B + 'test_result_profiles_assurance.py::test_curtailment_of_a_variable_generator'),
    _covered('LC-D09.storage_unit', 'chronological E_t with efficiencies and standing loss, values hand-computed in each test',
             '',
             B + 'test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations',
             B + 'test_storage_assurance.py::test_storage_standing_loss_and_fractional_efficiencies_use_chronological_duration',
             B + 'test_storage_assurance.py::test_storage_simultaneous_operation_cannot_be_reconstructed_from_net_power'),
    _covered('LC-D09.cost_edit', 'direct storage cost 51 EUR computed independently; a cost edit invalidates current evidence',
             '',
             B + 'test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle',
             B + 'test_storage_cost_assurance.py::test_current_cost_evidence_invalidates_after_cost_edit'),
    _covered('LC-D09.store', 'reversed sign -> unsupported_storage_boundary; cyclic Store: 4 MWh of 10 MWh = 0.4 cycles, loop closes at 0',
             '',
             B + 'test_capacity_cycles_assurance.py::test_a_store_with_a_reversed_sign_is_refused',
             B + 'test_capacity_cycles_assurance.py::test_a_cyclic_store_is_checked_around_its_loop'),
    _covered('LC-D09.spill', 'a held-full 20 MWh reservoir, 10 MW inflow, 4 MW load, 3 h weights: spill 6 MW each '
             'snapshot, spilled_mwh 54; inflow 9 gives 5 and 45',
             'run_simulation get_simulation_status get_asset_results',
             B + 'test_lc06_d09_inflow_spill.py::test_a_full_reservoir_spills_its_inflow_less_its_dispatch[inflow10]',
             B + 'test_lc06_d09_inflow_spill.py::test_a_full_reservoir_spills_its_inflow_less_its_dispatch[inflow9]'),
    _unavailable('LC-D09.inflow', 'no tool reports a StorageUnit inflow; spill is its only native water output (ticket 31)',
                 'pypsa-gui/backend/services/asset_results/registry.py spilled_mwh'),
    _covered('LC-D10.prices_emissions', 'time-weighted bus price, positive dual of a binding cap, emissions = dispatch / efficiency x factor',
             '',
             B + 'test_result_profiles_assurance.py::test_bus_price_is_time_weighted_and_observed',
             B + 'test_result_profiles_assurance.py::test_a_binding_cap_reports_the_dual_as_a_positive_price',
             B + 'test_result_profiles_assurance.py::test_generator_emissions_use_dispatch_over_efficiency'),
    _covered('LC-D11.copt', 'two-unit capacity distribution 0.72/0.18/0.08/0.02 (sums to 1), equal to GET /results/copt',
             '',
             B + 'test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it'),
    _covered('LC-D11.fmea', 'class A leave-one-out on the two-unit COPT fleet: dEUE 28.8 / 31.8 MWh, 17.52 / 43.8 per yr, '
             'q 0.2 -> 0.1 gives 15.9; class B: a 10 MW tie out sheds 5 MW x 2 x 3 h = 30 MWh (4 MW: 24); '
             'dEUE and occurrence only',
             'update_solver_config get_adequacy_results run_fmea_sweep',
             B + 'test_lc06_d11_fmea_health.py::test_class_a_prices_each_units_outages_by_hand[thermal1]',
             B + 'test_lc06_d11_fmea_health.py::test_class_a_prices_each_units_outages_by_hand[thermal2]',
             B + 'test_lc06_d11_fmea_health.py::test_class_a_moves_with_one_units_rate',
             B + 'test_lc06_d11_fmea_health.py::test_class_b_one_link_outage_sheds_its_load',
             B + 'test_lc06_d11_fmea_health.py::test_class_b_moves_with_the_load_behind_the_tie'),
    _pending('LC-D11.fmea_criticality', 'criticality and severity are per horizon, labelled per year (ticket 30)'),
    _covered('LC-D11.health', 'ledger read back from storage and the route; provenance 1 sourced, 1 drifted (0.25 vs 0.2), '
             '1 orphaned; four invalid fields refused with the field named, the file byte-identical',
             'record_asset_health get_asset_health',
             B + 'test_lc06_d11_fmea_health.py::test_health_reads_back_from_storage_and_reconciles_with_the_network',
             B + 'test_lc06_d11_fmea_health.py::test_an_invalid_record_names_its_field_and_stores_nothing[outage_rate_value-1.0]',
             B + 'test_lc06_d11_fmea_health.py::test_an_invalid_record_names_its_field_and_stores_nothing[mttr_hours-0.0]',
             B + 'test_lc06_d11_fmea_health.py::test_an_invalid_record_names_its_field_and_stores_nothing[measured_at-01/01/2026]',
             B + 'test_lc06_d11_fmea_health.py::test_an_invalid_record_names_its_field_and_stores_nothing[confidence-certain]'),
    _covered('LC-D12.standard_mc', 'exact two-unit COPT LOLE 1927.2 h / EUE 54,312 MWh inside the seed-1 95 % CI; LOLE half-width <= 10 % of the oracle; stationary start',
             'run_mc_study get_adequacy_results',
             B + 'test_lc06_d12_standard_mc.py::test_the_oracle_is_the_hand_copt',
             B + 'test_lc06_d12_standard_mc.py::test_lole_contains_the_exact_copt_within_a_tight_ci',
             B + 'test_lc06_d12_standard_mc.py::test_eue_contains_the_exact_copt'),
    _covered('LC-D12.zonal_mc', 'deterministic zonal EUE 256,230 MWh = sum_h max(L - own - min(cap x eff, max(surplus, 0) x eff), 0), CI width 0; Link-, surplus- and zero-surplus-bound hours each present; mutations Z1 (cap halved) and Z2 (grid load +10 MW on the surplus hours) move EUE by the hand deltas. Chat seam: totals. Engine layer (simulate_zonal_blocks on the freeze_fixed_plan inputs): the per-hour import equals the hand min(offered, max(surplus, 0) x ratio), and the pass-through offer hook leaves the result bitwise unchanged',
             'run_eh_study get_adequacy_results',
             B + 'test_lc06_d12_zonal_mc.py::test_the_fixture_has_every_binding_case',
             B + 'test_lc06_d12_zonal_mc.py::test_the_zonal_import_is_the_lesser_bound_every_hour',
             B + 'test_lc06_d12_zonal_mc.py::test_each_mutation_moves_eue_by_the_hand_delta',
             B + 'test_lc06_d12_zonal_mc.py::test_the_engine_imports_the_lesser_bound_per_hour_and_the_hook_changes_nothing'),
    _covered('LC-D12.common_mode', 'same seed. Chat seam (totals): EUE/LOLE bitwise equal where the Link cannot help (own RNG stream, EUE > 0); EUE strictly higher where it can. Engine layer (simulate_zonal_blocks on the freeze_fixed_plan inputs): EUE_cm >= EUE_base per draw and strictly greater in total, hub unit availability bitwise equal across the two runs, per-hour import = min(offered, max(surplus, 0) x ratio)',
             'run_eh_study get_adequacy_results',
             B + 'test_lc06_d12_zonal_mc.py::test_a_common_mode_outage_draws_on_its_own_stream',
             B + 'test_lc06_d12_zonal_mc.py::test_a_common_mode_outage_raises_eue_where_the_link_matters',
             B + 'test_lc06_d12_zonal_mc.py::test_the_engine_common_mode_raises_eue_draw_by_draw_and_leaves_the_hub_draws'),
    _covered('LC-D13.copt_screening', 'EUE/LOLE from the hand two-unit COPT distribution',
             '',
             B + 'test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it'),
    _pending('LC-D13.margin_loop', 'HTTP test stubs the engine'),
    _pending('LC-D13.reserve_margin_coupling', 'seam checks shape only'),
    _pending('LC-D13.elcc_frontier_campaign', 'seam checks contract keys only'),
    _pending('LC-D13.eh_redundancy', 'engine seed only'),
    _pending('LC-D13.eh_levers', 'engine seed only'),
    _covered('LC-D13.eh_dtc', 'P16 shared bus, 2 h at weight 1: critical unserved 140 MWh under the bus aggregate (bus VoLL) and 40 MWh under per_load (per-Load VoLL priority, priority_exact); local 40 MW gives 80 against 0',
             'run_eh_study get_adequacy_results',
             B + 'test_lc06_d13_energization_dtc.py::test_bus_and_per_load_voll_each_set_the_critical_unserved'),
    _pending('LC-D13.eh_dtc_planning', 'engine seed only'),
    _covered('LC-D13.energization', 'ENS = sum_h w_h max(L - G - min(E_h, cap), 0) at each cap step 80 -> 30 -> 80 MW (97,000 / 158,000 / 97,000 MWh, non-uniform weights); the solve sees the edited p_nom; dropping the schedule moves ENS by -82,000 MWh',
             'run_eh_study get_adequacy_results',
             B + 'test_lc06_d13_energization_dtc.py::test_the_fixture_binds_each_way_at_the_low_cap',
             B + 'test_lc06_d13_energization_dtc.py::test_the_schedule_composes_as_min_with_each_capacity_edit',
             B + 'test_lc06_d13_energization_dtc.py::test_dropping_the_schedule_moves_ens_by_the_hand_delta'),
    _pending('LC-D13.multi_energy', 'no H2/heat attribution test found'),
    _covered('LC-D14.static_campus', 'six untied hours through the queued Run: k 1 picks import/trafo h0, consumption h2, '
             'export/renewable h3; k 2 adds h5 import/trafo, h0 consumption, h2 renewable; k read back from the job; '
             '50 MW in h5 moves the import pick',
             'campus_draft_campus campus_run_study',
             B + 'test_lc06_d14_static_campus.py::test_k1_picks_each_criterions_extreme_hour_and_unions_the_reasons',
             B + 'test_lc06_d14_static_campus.py::test_k2_adds_each_criterions_runner_up',
             B + 'test_lc06_d14_static_campus.py::test_more_load_in_the_last_hour_moves_the_import_pick'),
    *[_covered(f'LC-D15.{m}', 'exact {value, source} read-back; refusals name the field and the unit\'s control; '
               'a gfl BESS enters no GFM row: the genset carries 10 MW x 1000 x 4 h = 40,000 with only headroom binding',
               D15_TOOLS,
               B + 'test_lc06_d15_control_models.py::test_each_model_and_governor_reads_back_exactly',
               B + 'test_lc06_d15_control_models.py::test_a_grid_following_bess_does_not_enter_the_gfm_rows',
               B + f'test_lc06_d15_control_models.py::{refusal}')
      for m, refusal in (('gfl_pq', 'test_a_missing_field_is_refused_naming_it_and_the_model'),
                         ('gfl_qu', 'test_a_missing_field_is_refused_naming_it_and_the_model'),
                         ('gfl_pf', 'test_gfl_pf_needs_one_power_factor_choice_and_says_so_with_its_model'),
                         ('gfl_fw', 'test_a_field_of_another_model_is_refused_naming_it_and_the_units_own_model'))],
    _covered('LC-D15.gfm_droop', 'QSS row sum K x 0.5 Hz >= 10 MW with K = p_nom/(R f0), R = droop_pct/100: p_nom 40 MW, 4000 '
             '(droop read as pu: 400,000); refusals name the field and gfm_droop; exact read-back',
             D15_TOOLS,
             B + 'test_lc06_d15_control_models.py::test_gfm_droop_gain_reads_droop_in_percent',
             B + 'test_lc06_d15_control_models.py::test_a_value_out_of_range_names_the_model_and_the_field',
             B + 'test_lc06_d15_control_models.py::test_each_model_and_governor_reads_back_exactly'),
    _covered('LC-D15.gfm_vsm', 'RoCoF row 2 H_v p_nom/pf >= f0 dP/RoCoF_max: p_nom 47.5 MW, 4750 at 50 Hz and 5700 at 60 Hz '
             '(f0 from the campus file; H on P: 5000/6000); the grid_forming_support preset expands to its assumed '
             'fields and gives 5937.5; refusals name the field and gfm_vsm',
             D15_TOOLS,
             B + 'test_lc06_d15_control_models.py::test_vsm_inertia_is_on_the_apparent_power_base',
             B + 'test_lc06_d15_control_models.py::test_a_preset_reads_back_as_written_and_expands_to_its_tagged_fields',
             B + 'test_lc06_d15_control_models.py::test_a_field_of_another_model_is_refused_naming_it_and_the_units_own_model'),
    _covered('LC-D15.governors', 'a 20 MW 4 % droop genset adds K = 10 MW/Hz to the QSS row (BESS 20 MW, 2000) where an '
             'isochronous one adds none (40 MW, 4000); refusals name the governor; exact read-back of both',
             D15_TOOLS,
             B + 'test_lc06_d15_control_models.py::test_a_droop_genset_adds_its_gain_to_the_qss_row',
             B + 'test_lc06_d15_control_models.py::test_gfm_droop_gain_reads_droop_in_percent',
             B + 'test_lc06_d15_control_models.py::test_a_droop_governor_without_droop_pct_names_the_governor',
             B + 'test_lc06_d15_control_models.py::test_a_field_of_a_converter_on_an_isochronous_genset_names_the_governor'),
    _covered('LC-D16.E1', 'islanding: RoCoF0 = -dP f0/(2 H_v S_n) and settled df = -dP/sum K on the row\'s own I4 import '
             '(40 MW plus losses), D = 0; E2 oracles below hold on it',
             D16_TOOLS,
             B + 'test_lc06_d16_frequency_events.py::test_e1_islanding_rocof_holds_on_the_rows_own_import'),
    _covered('LC-D16.E2', '40 MW step on a 100 MW gfm_vsm BESS (H_v 5 s, pf 0.95, 4 %): RoCoF0 -1.900 Hz/s, df_qss -0.800 Hz; '
             '60 Hz from the campus file -2.280 / -0.960; H_v halved -3.800; droop 8 % -1.600; an isochronous spinning '
             'genset adds its H S to RoCoF0 (-1.1095) and returns df to 0; RK4 dt halving cuts the error >= 1.8x',
             D16_TOOLS,
             B + 'test_lc06_d16_frequency_events.py::test_e2_rocof_and_settled_frequency_are_the_hand_values',
             B + 'test_lc06_d16_frequency_events.py::test_halving_h_doubles_the_rocof_and_leaves_the_settled_frequency',
             B + 'test_lc06_d16_frequency_events.py::test_doubling_the_droop_doubles_the_settled_frequency',
             B + 'test_lc06_d16_frequency_events.py::test_at_60_hz_both_figures_scale_with_f0',
             B + 'test_lc06_d16_frequency_events.py::test_an_isochronous_genset_returns_the_frequency_to_nominal',
             B + 'test_lc06_d16_frequency_events.py::test_halving_dt_cuts_the_error_against_the_closed_form'),
    _covered('LC-D16.E3', 'tripping the only grid former collapses the island at t = 0, named "no grid-forming headroom"',
             D16_TOOLS,
             B + 'test_lc06_d16_frequency_events.py::test_e3_tripping_the_only_grid_former_collapses_the_island'),
    _covered('LC-D16.E4', 'ups_bridged: a cold 60 MW droop genset alone picks up 20 + 20 MW, each block from f0: windowed '
             'RoCoF on the two-state matrix exponential, df_qss -0.6667 Hz (droop 8 % doubles it), block 2 at ready + '
             'the settling instant; UPS autonomy e 3600/L = 900 s, 0.05 MWh short; N+1 drops the largest, named',
             D16_TOOLS,
             B + 'test_lc06_d16_frequency_events.py::test_e4_each_block_steps_the_genset_alone_from_nominal[4.0]',
             B + 'test_lc06_d16_frequency_events.py::test_e4_each_block_steps_the_genset_alone_from_nominal[8.0]',
             B + 'test_lc06_d16_frequency_events.py::test_e4_at_n_plus_1_drops_the_largest_genset_and_waits_for_the_rest',
             B + 'test_lc06_d16_frequency_events.py::test_e4_the_ups_bridges_the_start_only_with_the_energy[10.0-True]',
             B + 'test_lc06_d16_frequency_events.py::test_e4_the_ups_bridges_the_start_only_with_the_energy[0.05-False]'),
    _covered('LC-D16.E5', 'PV loss: a 20 MW gfl_pq PV lost on the gfm_vsm BESS, RoCoF0 -0.950 Hz/s and df_qss -0.400 Hz, D = 0; '
             '10 MW halves both; UPS walk-in on E1: energy L T/2/3600 = 0.055556 MWh, T halved halves it, a 0.1 Hz '
             'window drops the UPS',
             D16_TOOLS,
             B + 'test_lc06_d16_frequency_events.py::test_e5_losing_the_pv_is_a_step_of_its_output[20.0]',
             B + 'test_lc06_d16_frequency_events.py::test_e5_losing_the_pv_is_a_step_of_its_output[10.0]',
             B + 'test_lc06_d16_frequency_events.py::test_the_ups_walk_in_energy_is_half_load_times_walk_in[10.0]',
             B + 'test_lc06_d16_frequency_events.py::test_the_ups_walk_in_energy_is_half_load_times_walk_in[5.0]',
             B + 'test_lc06_d16_frequency_events.py::test_a_narrow_window_drops_the_ups_off_line'),
    _covered('LC-D16.E6', 'planned: a 30 MW BESS at once plus a 60 MW genset ramping 6 MW/s from p_min cover the import at '
             '(import - 30)/6 s, ramp halved doubles it; a 5 MW genset leaves shortfall import - (30 + 4.5)',
             D16_TOOLS,
             B + 'test_lc06_d16_frequency_events.py::test_e6_the_ramp_time_is_the_genset_covering_what_the_bess_cannot[0.1]',
             B + 'test_lc06_d16_frequency_events.py::test_e6_the_ramp_time_is_the_genset_covering_what_the_bess_cannot[0.05]',
             B + 'test_lc06_d16_frequency_events.py::test_e6_short_of_room_is_infeasible_with_the_shortfall'),
    _unavailable('LC-D16.emt_pll_voltage', 'single-bus aggregate frequency screening only; no EMT/RMS/PLL/voltage dynamics',
                 'gridspine/static/frequency.py LABEL'),
    _covered('LC-D17.recommend_apply_restudy', 'gfm_vsm BESS (H_v 1 s, 4 %) beside a 5 % droop genset, band 0.25 Hz: '
             'the card sets droop and H_v within the search bounds; both files read back the card exactly, tagged '
             'assumed; the re-study puts E2 RoCoF0 and df_qss on the applied values; without it the old figures stand; '
             'a network edit (refused by the recommendation\'s own staleness, not the digest: ticket 28) or a wrong '
             'digest after the card is edit_preview_stale with nothing written; the HTTP apply with no digest is '
             'still 409 (ticket 29)',
             D17_TOOLS,
             B + 'test_lc06_d17_recommend_apply.py::test_the_applied_card_is_read_back_and_the_restudy_runs_on_it',
             B + 'test_lc06_d17_recommend_apply.py::test_without_the_restudy_the_old_figures_stand_and_no_study_is_made',
             B + 'test_lc06_d17_recommend_apply.py::test_a_network_edit_after_the_card_refuses_the_apply',
             B + 'test_lc06_d17_recommend_apply.py::test_a_digest_that_is_not_the_cards_refuses_the_apply',
             B + 'test_lc06_d17_recommend_apply.py::test_the_http_apply_without_a_digest_still_refuses_a_stale_network'),
    _covered('LC-D18.investment', 'equity NPV 4.1322314 at cost of equity 10 %, WACC 7 %; financial close undiscounted',
             '',
             B + 'test_investment_assurance.py::test_hand_cash_with_a_nonzero_first_year'),
    _covered('LC-D19.single_case', 'objective 200 = 5 MW x 2 h x 20 EUR/MWh, same in the exported evidence',
             'prepare_experiment advance_experiment refresh_experiment wait_for_job export_run_evidence',
             B + 'test_experiment_case_advancement.py::test_real_case_finishes_only_with_terminal_and_intact_evidence'),
    _covered('LC-D19.two_case_compare', 'two solved cases at 3 h weighting: 20 and 30 EUR/MWh x 5 MW x 6 h = 600 and 900 EUR, '
             'each its own run evidence; compare +300 EUR (+50 %), reversed -300 EUR; the export downloads the same report',
             'start_experiment prepare_experiment advance_experiment refresh_experiment wait_for_job export_run_evidence '
             'compare_experiments export_experiment_comparison',
             B + 'test_lc06_d19_two_case_compare.py::test_two_weighted_cases_compare_their_own_solved_objectives',
             B + 'test_lc06_d19_two_case_compare.py::test_the_signed_delta_flips_with_the_order'),
    _pending('LC-D20.delivery', 'seam only; delivered bytes not independently parsed'),
    _pending('LC-D21.reports', 'seam only; document facts not independently inspected'),
    _pending('LC-D22.undo_checkpoints', 'seam only; no before/after fingerprint oracle'),
    _pending('LC-D23.provider_parity', 'dispatch parity only (test_assistant_provider_parity.py)'),
    _pending('LC-D24.ui', 'frontend e2e; not re-audited in LC-06'),
]


@functools.cache
def inventory_tools() -> tuple[str, ...]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from assistant_inventory import build_inventory
    return tuple(sorted(t['name'] for t in build_inventory()['tools']))


def _families(tools_by_family: dict, inventory: tuple[str, ...]) -> dict[str, set[str]]:
    families = {f: set(tools) for f, tools in tools_by_family.items()}
    families['gridspine-campus'] |= {t for t in inventory
                                     if t.startswith(('campus_', 'gridspine_')) and t not in _CAMPUS_ECONOMICS}
    families['gridspine-campus'].add('get_study_evidence')
    families['economics-investment'] |= _CAMPUS_ECONOMICS
    return families


def uncollected_nodes(nodes: list[str]) -> list[str]:
    """Node ids pytest cannot collect. Backend ids collect from the backend
    directory (its conftest), the rest from the repository root."""
    missing = []
    for cwd, prefix in ((ROOT / 'pypsa-gui/backend', 'pypsa-gui/backend/'), (ROOT, '')):
        group = [n for n in nodes if n.startswith(prefix) and (prefix or not n.startswith('pypsa-gui/backend/'))]
        if not group:
            continue
        local = [n[len(prefix):] for n in group]
        run = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-o', 'addopts=', '-W', 'ignore',
                              '-p', 'no:cacheprovider', *local], cwd=cwd, capture_output=True, text=True, timeout=300)
        out = run.stdout + run.stderr
        not_found = {n for n, loc in zip(group, local) if f'not found: {cwd / loc}' in out}
        missing += sorted(not_found) if not_found or run.returncode == 0 else group
    return missing


def _pointer_problem(pointer: str) -> str | None:
    path, _, symbol = pointer.partition(' ')
    if not (ROOT / path).is_file():
        return f'pointer {pointer}: no such file'
    if symbol and symbol not in (ROOT / path).read_text(encoding='utf-8'):
        return f'pointer {pointer}: {symbol} not in the file'
    return None


def manifest_problems(*, tools_by_family: dict | None = None, variants: list[dict] | None = None,
                      inventory: tuple[str, ...] | None = None, collect: bool = True) -> list[str]:
    inventory = inventory_tools() if inventory is None else inventory
    families = _families(TOOLS_BY_FAMILY if tools_by_family is None else tools_by_family, inventory)
    variants = VARIANTS if variants is None else variants
    known, problems, seen = set(inventory), [], {}
    if set(families) != set(FAMILIES) or set(SUITES_BY_FAMILY) != set(FAMILIES):
        problems.append('families disagree with FAMILIES')
    for family, tools in families.items():
        problems += [f'{family}: {t} is not an inventory tool' for t in sorted(tools - known)]
        for t in sorted(tools & known):
            if t in seen:
                problems.append(f'{t} is in both {seen[t]} and {family}')
            seen[t] = family
    suites = {f'LC-D{i:02d}' for i in range(1, 25)}
    ids = [v['id'] for v in variants]
    problems += [f'duplicate variant {i}' for i in sorted({i for i in ids if ids.count(i) > 1})]
    problems += [f'{s} has no variant' for s in sorted(suites - {v['suite'] for v in variants})]
    nodes = []
    for v in variants:
        if v['suite'] not in suites or not v['id'].startswith(v['suite'] + '.'):
            problems.append(f'{v["id"]}: suite {v["suite"]} is not an annex suite')
        if v['status'] == 'covered':
            if not v.get('tests') or not v.get('oracle'):
                problems.append(f'{v["id"]}: covered needs tests and an oracle')
            if not v.get('tools') and v.get('seam') != CHECKED_STREAM:
                problems.append(f'{v["id"]}: covered needs its tools or the checked-stream seam')
            if any('::' not in t for t in v.get('tests', [])):
                problems.append(f'{v["id"]}: covered tests must be node ids (file::test)')
            problems += [f'{v["id"]}: {t} is not an inventory tool' for t in v.get('tools', []) if t not in known]
            nodes += v.get('tests', [])
        elif v['status'] in ('pending', 'unavailable'):
            if not v.get('reason'):
                problems.append(f'{v["id"]}: {v["status"]} needs a reason')
            if v['status'] == 'unavailable':
                problem = _pointer_problem(v.get('pointer') or '') if v.get('pointer') else 'no pointer'
                if problem:
                    problems.append(f'{v["id"]}: {problem}')
        else:
            problems.append(f'{v["id"]}: unknown status {v["status"]!r}')
    if collect and nodes:
        problems += [f'{n}: does not collect' for n in uncollected_nodes(sorted(set(nodes)))]
    return problems


def build_manifest(inventory: tuple[str, ...] | None = None) -> dict:
    inventory = inventory_tools() if inventory is None else inventory
    families = _families(TOOLS_BY_FAMILY, inventory)
    mapped = set().union(*families.values())
    covered_tools = {t for v in VARIANTS if v['status'] == 'covered' for t in v['tools']}
    counts = {s: sum(v['status'] == s for v in VARIANTS) for s in ('covered', 'pending', 'unavailable')}
    return {
        'format_version': 1,
        'source': 'pypsa-gui/backend/smoke/family_manifest.py',
        'families': FAMILIES,
        'suites_by_family': {f: [f'LC-D{i:02d}' for i in s] for f, s in SUITES_BY_FAMILY.items()},
        'tools_by_family': {f: sorted(families[f] & set(inventory)) for f in FAMILIES},
        'unmapped_tools': sorted(set(inventory) - mapped),
        'covered_tools': sorted(covered_tools & set(inventory)),
        'variants': VARIANTS,
        'counts': {'tools': len(inventory), 'covered_tools': len(covered_tools & set(inventory)),
                   'unmapped_tools': len(set(inventory) - mapped), 'variants': len(VARIANTS),
                   **{f'{k}_variants': n for k, n in counts.items()}},
        'full_coverage': 'not_established',
    }


def report(built: dict) -> str:
    c = built['counts']
    lines = [' '.join([
        'ok', f'families={len(FAMILIES)}', 'suites=24',
        f'covered {c["covered_tools"]} / {c["tools"]} ({c["unmapped_tools"]} unmapped)',
        f'variants={c["variants"]}', f'covered={c["covered_variants"]}', f'pending={c["pending_variants"]}',
        f'unavailable={c["unavailable_variants"]}', 'full_coverage=not_established'])]
    if built['unmapped_tools']:
        lines.append('unmapped: ' + ' '.join(built['unmapped_tools']))
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Build in memory; fail on any guard.')
    mode.add_argument('--write', action='store_true', help='Also write the manifest (not committed) for reading.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    problems = manifest_problems()
    if problems:
        print('\n'.join(problems))
        return 1
    built = build_manifest()
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(built, indent=2) + '\n', encoding='utf-8')
    print(report(built))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
