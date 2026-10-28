"""Check/write the LC-06 family execution manifest.

Assigns every assistant tool to one of the 14 capability-matrix families and
lists each named engine/control variant of the LC-D01–24 domain suites
(`.scratch/live-copilot/domain-coverage.md`) with its disposition:

- ``covered``: public-seam tests (chat tool, checked stream or authenticated
  API) that check an independent hand oracle, named as pytest node ids;
- ``pending``: the gap, naming any engine-only seed oracle;
- ``unavailable``: a claim the application does not make, and why.

A tool that no family names is reported as unmapped (it blocks claims for its
slice), not a failure, so other packages can add tools. ``full_coverage``
stays ``not_established``: covered variants are evidence for that variant only.
"""
from __future__ import annotations

import argparse
import ast
import functools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / '.scratch/live-copilot/family-manifest.json'
B = 'pypsa-gui/backend/tests/'
G = 'tests/gridspine/'

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


def _covered(vid: str, oracle: str, *tests: str) -> dict:
    return {'id': vid, 'suite': vid.split('.')[0], 'status': 'covered', 'oracle': oracle, 'tests': list(tests)}


def _pending(vid: str, reason: str) -> dict:
    return {'id': vid, 'suite': vid.split('.')[0], 'status': 'pending', 'reason': reason}


def _unavailable(vid: str, reason: str) -> dict:
    return {'id': vid, 'suite': vid.split('.')[0], 'status': 'unavailable', 'reason': reason}


# Rebaselined on master a6d73f57; see the slice log in
# .scratch/live-copilot/issues/06-family-adapters.md.
VARIANTS: list[dict] = [
    _pending('LC-D01.projects', 'seam tests check identity/ACL; no independent file/ID oracle'),
    _pending('LC-D02.topology', 'seam tests check shape; no reference-count/KCL oracle'),
    _covered('LC-D03.dynamic_weights', 'objective 10*(5*2+7*3)=310 through objective weights',
             B + 'test_assistant_capabilities.py::test_native_dynamic_roundtrip_solves_and_persists_evidence'),
    _pending('LC-D03.chronology_edges', 'DST/leap/duplicate/nonmonotonic times not mapped'),
    _covered('LC-D04.bound_build', 'hand 3 MW extendable build to its bound; fixed is not optimised',
             B + 'test_capacity_cycles_assurance.py::test_an_extendable_generator_built_to_its_bound',
             B + 'test_capacity_cycles_assurance.py::test_a_fixed_generator_says_it_was_not_optimised'),
    _covered('LC-D04.units', 'MVA for branches, MWh for Store',
             B + 'test_capacity_cycles_assurance.py::test_a_line_states_mva_and_an_unbounded_build_never_reads_as_at_its_bound',
             B + 'test_capacity_cycles_assurance.py::test_a_store_counts_only_energy_leaving_it'),
    _pending('LC-D04.unique_lp', '10 MW demand, 6 MW cheap cap, 4 MW backup: hand objective and cap perturbation'),
    _pending('LC-D04.vintage_results', 'no test calls get_vintage_results'),
    _pending('LC-D05.perfect_foresight', 'engine seed only: test_myopic_horizon_cost.py'),
    _pending('LC-D05.myopic', 'engine seed only: test_myopic_horizon_cost.py'),
    _pending('LC-D05.limited_foresight', 'engine seed only: test_myopic_horizon_cost.py'),
    _pending('LC-D05.two_period_discount', 'hand-discounted build/retirement fixture'),
    _covered('LC-D06.full_lp', 'hand objectives 310 and 200 = 5*2*20',
             B + 'test_assistant_capabilities.py::test_native_dynamic_roundtrip_solves_and_persists_evidence'),
    _covered('LC-D06.infeasible', 'hand-built infeasible island is triaged and never re-run',
             B + 'test_lc19_run_lifecycle_journeys.py::test_oj33_an_infeasible_solve_is_triaged_and_never_rerun'),
    _pending('LC-D06.rolling', 'config refusals only; no hand value through the seam'),
    _pending('LC-D06.myopic_solve', 'engine seed only (see LC-D05)'),
    _pending('LC-D06.sclopf', 'engine seed only: test_fixed_sizes.py'),
    _pending('LC-D06.ac_stage', 'lifecycle/refusal only, no numbers'),
    _pending('LC-D06.solver_outcomes', 'unbounded, time limit, MIP gap, unavailable solver'),
    _covered('LC-D07.active_proxy', 'hand line loadings; LOPF never claims compliance',
             B + 'test_loadflow_summary.py::test_lopf_is_an_active_power_proxy_and_never_claims_compliance',
             B + 'test_branch_loading_assurance.py::test_line_loading_matches_the_hand_values_and_the_load_flow_tab'),
    _covered('LC-D07.ac_apparent', 'hypot(95, 60) from hand-set flows',
             B + 'test_loadflow_summary.py::test_ac_pf_uses_apparent_power_where_reactive_flows_exist'),
    _covered('LC-D07.convergence_mask', 'unconverged snapshots excluded and reported',
             B + 'test_loadflow_summary.py::test_ac_pf_excludes_unconverged_snapshots_and_reports_them'),
    _covered('LC-D07.weighting', 'weighted hours/mean and zero s_max_pu limit skipped',
             B + 'test_loadflow_summary.py::test_weighted_snapshots_drive_hours_and_mean',
             B + 'test_loadflow_summary.py::test_zero_effective_limit_from_s_max_pu_is_skipped'),
    _covered('LC-D07.missing_q', 'earlier PF Q never mixed into LOPF flow',
             B + 'test_branch_loading_assurance.py::test_q_left_by_an_earlier_power_flow_is_never_mixed_into_lopf_flow'),
    _pending('LC-D07.static_pf_oracle', 'P=60/Q=80 -> 100 MVA and an independently solved static PF'),
    _unavailable('LC-D07.n1_voltage', 'no N-1 or voltage compliance claim is made; summaries say so'),
    _covered('LC-D08.dispatch', 'hand lost load and curtailment from a real solve',
             B + 'test_lost_load_assurance.py::test_lost_load_matches_the_hand_values_from_a_real_solve',
             B + 'test_result_profiles_assurance.py::test_curtailment_of_a_variable_generator'),
    _covered('LC-D09.storage_unit', 'chronological trajectory with efficiencies and standing loss',
             B + 'test_storage_assurance.py::test_storage_key_results_use_raw_split_power_and_storage_durations',
             B + 'test_storage_assurance.py::test_storage_standing_loss_and_fractional_efficiencies_use_chronological_duration',
             B + 'test_storage_assurance.py::test_storage_simultaneous_operation_cannot_be_reconstructed_from_net_power'),
    _covered('LC-D09.cost_edit', 'independent 51 EUR direct cost; edit invalidates',
             B + 'test_storage_cost_assurance.py::test_direct_storage_cost_uses_independent_51_eur_oracle',
             B + 'test_storage_cost_assurance.py::test_current_cost_evidence_invalidates_after_cost_edit'),
    _covered('LC-D09.store', 'reversed sign refused; cyclic store checked round its loop',
             B + 'test_capacity_cycles_assurance.py::test_a_store_with_a_reversed_sign_is_refused',
             B + 'test_capacity_cycles_assurance.py::test_a_cyclic_store_is_checked_around_its_loop'),
    _pending('LC-D09.inflow_spill', 'refusal only; no native inflow/spill oracle'),
    _covered('LC-D10.prices_emissions', 'time-weighted price, binding-cap dual, dispatch/efficiency emissions',
             B + 'test_result_profiles_assurance.py::test_bus_price_is_time_weighted_and_observed',
             B + 'test_result_profiles_assurance.py::test_a_binding_cap_reports_the_dual_as_a_positive_price',
             B + 'test_result_profiles_assurance.py::test_generator_emissions_use_dispatch_over_efficiency'),
    _covered('LC-D11.copt', 'two units 0.72/0.18/0.08/0.02, mass sums to one',
             B + 'test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it'),
    _pending('LC-D11.fmea', 'seam checks shape only; engine seeds test_energy_hub_fmea_top.py'),
    _pending('LC-D11.health', 'validation only'),
    _pending('LC-D12.standard_mc', 'engine seed only: thermal-only MC vs exact convolution (test_adequacy_mc.py)'),
    _pending('LC-D12.zonal_mc', 'engine seed only: test_energy_hub_zonal_*.py'),
    _pending('LC-D12.common_mode', 'engine seed only: test_energy_hub_common_mode.py'),
    _covered('LC-D13.copt_screening', 'hand EUE/LOLE on the two-unit COPT',
             B + 'test_copt_assurance.py::test_the_classical_fleet_by_hand_and_as_the_tab_has_it'),
    _pending('LC-D13.margin_loop', 'HTTP test stubs the engine'),
    _pending('LC-D13.reserve_margin_coupling', 'seam checks shape only'),
    _pending('LC-D13.elcc_frontier_campaign', 'seam checks contract keys only'),
    _pending('LC-D13.eh_redundancy', 'engine seed only'),
    _pending('LC-D13.eh_levers', 'engine seed only'),
    _pending('LC-D13.eh_dtc', 'engine seed only'),
    _pending('LC-D13.eh_dtc_planning', 'engine seed only'),
    _pending('LC-D13.energization', 'engine seed only: min(schedule, capacity)'),
    _pending('LC-D13.multi_energy', 'no H2/heat attribution test found'),
    _pending('LC-D14.static_campus', 'chat tests stub the service; seed tests/gridspine/test_campus_ranking.py'),
    *[_pending(f'LC-D15.{m}', 'engine seed only (test_island_schema.py); chat tests stub the service')
      for m in ('gfl_pq', 'gfl_qu', 'gfl_pf', 'gfl_fw', 'gfm_droop', 'gfm_vsm', 'governors')],
    *[_pending(f'LC-D16.{e}', 'engine seed only (test_frequency*.py); campus_run_island_study tests stub it')
      for e in ('E1', 'E2', 'E3', 'E4', 'E5', 'E6')],
    _unavailable('LC-D16.emt_pll_voltage', 'single-bus aggregate frequency screening only; no EMT/RMS/PLL/voltage dynamics'),
    _pending('LC-D17.recommend_apply_restudy', 'apply digest checked by equality; no chain through the seam'),
    _covered('LC-D18.investment', 'hand cash flow with a nonzero first year',
             B + 'test_investment_assurance.py::test_hand_cash_with_a_nonzero_first_year'),
    _covered('LC-D19.single_case', 'objective 200 = 5*2*20 through the chat tool and queue',
             B + 'test_experiment_case_advancement.py'),
    _pending('LC-D19.two_case_compare', 'two independently solved cases and signed deltas'),
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


def _with_gridspine(tools_by_family: dict) -> dict[str, set[str]]:
    families = {f: set(tools) for f, tools in tools_by_family.items()}
    families['gridspine-campus'] |= {t for t in inventory_tools()
                                     if t.startswith(('campus_', 'gridspine_')) and t not in _CAMPUS_ECONOMICS}
    families['gridspine-campus'].add('get_study_evidence')
    families['economics-investment'] |= _CAMPUS_ECONOMICS
    return families


@functools.cache
def _test_names(path: str) -> frozenset[str]:
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    return frozenset(n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _node_problem(node: str) -> str | None:
    path, _, name = node.partition('::')
    if not (ROOT / path).is_file():
        return f'{node}: no such test file'
    if name and name not in _test_names(path):
        return f'{node}: no test function {name}'
    return None


def manifest_problems(*, tools_by_family: dict | None = None, variants: list[dict] | None = None) -> list[str]:
    families = _with_gridspine(TOOLS_BY_FAMILY if tools_by_family is None else tools_by_family)
    variants = VARIANTS if variants is None else variants
    known, problems, seen = set(inventory_tools()), [], {}
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
    for v in variants:
        if v['suite'] not in suites or not v['id'].startswith(v['suite'] + '.'):
            problems.append(f'{v["id"]}: suite {v["suite"]} is not an annex suite')
        if v['status'] == 'covered':
            if not v.get('tests') or not v.get('oracle'):
                problems.append(f'{v["id"]}: covered needs tests and an oracle')
            problems += [p for p in map(_node_problem, v.get('tests', [])) if p]
        elif v['status'] in ('pending', 'unavailable'):
            if not v.get('reason'):
                problems.append(f'{v["id"]}: {v["status"]} needs a reason')
        else:
            problems.append(f'{v["id"]}: unknown status {v["status"]!r}')
    return problems


def build_manifest() -> dict:
    families = _with_gridspine(TOOLS_BY_FAMILY)
    mapped = set().union(*families.values())
    counts = {s: sum(v['status'] == s for v in VARIANTS) for s in ('covered', 'pending', 'unavailable')}
    return {
        'format_version': 1,
        'source': 'pypsa-gui/backend/smoke/family_manifest.py',
        'families': FAMILIES,
        'suites_by_family': {f: [f'LC-D{i:02d}' for i in s] for f, s in SUITES_BY_FAMILY.items()},
        'tools_by_family': {f: sorted(families[f]) for f in FAMILIES},
        'unmapped_tools': sorted(set(inventory_tools()) - mapped),
        'variants': VARIANTS,
        'counts': {'tools': len(inventory_tools()), 'variants': len(VARIANTS), **counts},
        'full_coverage': 'not_established',
    }


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
    c = built['counts']
    print('ok', f'families={len(FAMILIES)}', 'suites=24', f'tools={c["tools"]}',
          f'unmapped={len(built["unmapped_tools"])}', f'variants={c["variants"]}',
          f'covered={c["covered"]}', f'pending={c["pending"]}', f'unavailable={c["unavailable"]}',
          'full_coverage=not_established')
    if built['unmapped_tools']:
        print('unmapped:', ' '.join(built['unmapped_tools']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
