"""Plain-language titles for the preflight issue codes.

The server owns this copy: ``Issue.to_dict`` and the project flow send the title
beside the code, so the Issues list and the Run's blocker read the same words
and no client keeps a table of its own. This module is the one place a new
check gets its title. ``tests/test_issue_titles.py`` fails when a code in
``flow_sections.CODE_SECTION`` (the complete list of codes the flow files) or a
literal code in ``validation_service`` has no title here.

A code with no title is not an error: ``issue_title`` reads it as a sentence for
the Issues list, and the flow leaves the item's ``title`` null so the Run's
blocker falls back to ``<section>: <message>``.
"""
from __future__ import annotations

ISSUE_TITLES: dict[str, str] = {
    # Model set-up
    "snapshots_empty": "No time steps defined",
    "buses_empty": "Network has no buses",
    "snapshot_weighting_nan": "Time-step weights are missing",
    "snapshot_weights_nyears_off": "Time-step weights do not add up to a year",
    "solver_unavailable": "Solver not available",
    "unknown_mode": "Unknown solver mode",

    # Buses and connections
    "bus_v_nom_invalid": "Bus voltage must be positive",
    "bus_ref_missing": "Connection to a bus is missing",
    "bus_ref_unknown": "Connected bus does not exist",

    # Loads
    "load_p_set_nan": "Load profile has gaps",
    "load_p_set_invalid": "Load value is not a number",
    "voll_no_loads": "Value of lost load set, but there are no loads",

    # Generators
    "gen_zero_costs": "Generators without any cost",
    "gen_control_invalid": "Generator control type not valid for power flow",
    "gen_committable_extendable": "Unit commitment and capacity expansion on the same generator",
    "gen_uc_param_invalid": "Unit-commitment parameter not valid",
    "uc_start_cost_unweighted": "Start-up cost not weighted by the snapshots",
    "safety_tier_off_critical_bus": "Safety-tier process unit not on a critical bus",
    "lost_production_value_below_buy_in": "Lost-production value at or below the buy-in price",
    "p_max_pu_above_one": "Availability profile above 100 %",
    "curtailment_cost_on_thermal": "Curtailment cost on a non-renewable generator",
    "curtailment_cost_negative_prices": "Curtailment cost can create negative prices",

    # Storage
    "storage_max_hours_invalid": "Storage duration must be positive",
    "storage_soc_oob": "Initial state of charge outside the storage range",
    "storage_initial_ignored": "Initial state of charge is ignored (cyclic storage)",

    # Links, lines and transformers
    "link_efficiency_invalid": "Link efficiency is not a number",
    "line_loss_vnom_too_small": "Line losses with a voltage too low to model",
    "line_loss_high": "Line losses are very high",
    "transformer_loss_high": "Transformer losses are very high",
    "transformer_type_unregistered": "Transformer type not in the type library",

    # Carriers and emissions
    "carrier_zero_co2": "Fossil carrier has zero CO₂ emissions",
    "carrier_co2_nan": "Carrier CO₂ emissions missing under a CO₂ limit",
    "co2_price_no_fossil": "CO₂ price set, but no carrier emits CO₂",

    # Investment and horizon
    "discount_rate_no_effect": "Discount rate has no effect (no overnight costs)",
    "multi_period_no_extendable": "Investment periods set, but nothing can be built",
    "multi_period_no_periods": "Multi-period planning on, but no periods defined",
    "asset_unbuildable_period": "Asset is built after the last investment period",
    "asset_retired_before_periods": "Asset retires before the first investment period",

    # Myopic foresight
    "myopic_requires_multi_period": "Myopic foresight needs multi-period planning",
    "myopic_no_periods": "Myopic foresight needs investment periods",
    "myopic_flat_snapshots": "Myopic foresight needs period-indexed time steps",
    "myopic_capacity_locked_after_first_period": "Myopic foresight will freeze some capacity",
    "myopic_sclopf_cost": "N-1 security with myopic foresight will be large",
    "myopic_with_ac_pf": "AC power flow is not available with myopic foresight",

    # Rolling horizon
    "rolling_horizon_invalid": "Rolling-horizon window must be positive",
    "rolling_overlap_invalid": "Rolling-horizon overlap cannot be negative",
    "rolling_overlap_exceeds_horizon": "Rolling-horizon overlap is longer than the window",
    "rolling_horizon_exceeds_snapshots": "Rolling-horizon window is longer than the model",
    "rolling_with_sclopf": "N-1 security is not available with a rolling horizon",
    "rolling_with_ac_pf": "AC power flow is not available with a rolling horizon",
    "rolling_with_multi_period": "Rolling horizon cannot be combined with investment periods",

    # Security-constrained dispatch (SCLOPF)
    "sclopf_with_transmission_losses": "N-1 security cannot be combined with line losses",
    "sclopf_no_branches": "N-1 security has no branches to check yet",
    "sclopf_large_contingency_set": "Many contingencies: the solve will be slow",
    "sclopf_radial_subnet": "Radial network part: contingencies would island it",
    "sclopf_scope_invalid": "Security-constraint scope not recognised",
    "sclopf_scope_current_with_lf": "Security constraints ignore future periods",

    # AC power flow
    "pf_no_slack": "Power flow needs a slack bus",
    "stage2_multi_period": "AC power flow does not support investment periods",
    "stage2_zero_impedance": "Line with zero impedance",
    "stage2_no_explicit_slack": "No slack chosen for AC power flow",
    "stage2_multi_slack": "Several slack generators",

    # Demand response
    "dsr_enabled_without_buses": "Demand response priced, but no bus takes part",
    "dsr_zero_volume": "Demand response has zero volume",
    "dsr_unknown_bus": "Demand-response bus does not exist",
    "dsr_double_count_risk": "Demand response may be counted twice",

    # Reliability and adequacy
    "ens_zone_multiple_without_cap": "Zone energy-not-served limit without a system target",
    "ens_cap_without_voll": "Energy-not-served target without a value of lost load",
    "ens_cap_generous": "Energy-not-served target is very loose",
    "ens_cap_unsupported_strategy": "Energy-not-served target not supported by this solve strategy",
    "import_energy_cap_unsupported_strategy": "Import energy cap not supported by this solve strategy",
    "import_energy_link_bidirectional": "Metered import link can also export",
    "reserve_margin_unsupported_strategy": "Reserve margin not supported by this solve strategy",
    "reserve_margin_myopic_report_is_partial": "Reserve margin report covers only part of a myopic run",
    "reserve_margin_unpriceable_assets": "Reserve margin cannot price some assets",
    "reserve_margin_unreachable": "Reserve margin cannot be reached",
    "reserve_margin_carrier_default_derating": "Reserve margin uses default derating for some assets",

    # Tariff sanity (decision study)
    "tariff_export_exceeds_import": "Export pays more than import in some hours",
    "tariff_export_exceeds_import_via_storage": "Export pays more than import after storage losses",

    # Commercial preflight
    "commercial.grid_connection_double_count": "Grid connection may be paid twice",

    # Outages and availability
    "outage_params_implausible": "Outage data looks implausible",
    "profile_and_outage_modelled": "Outages may be counted twice (profile and sampled outages)",
    "availability_may_include_outages": "Availability may already include outages",
    "outages_folded_into_availability": "Outages are folded into availability",
    "outages_folded_into_availability_ignored": "Outages-in-availability flag is ignored",

    # Island control presets (data-centre grid readiness, ticket 04)
    "preset_leaves_no_grid_former": "No unit forms the island (control preset leaves no grid former)",
    "preset_not_applied_to_unit": "Control preset not applied to a unit",
    # Disconnected parts of the network
    "network_islanded": "Network is split into disconnected parts",
    "island_no_supply": "Disconnected part of the network has no supply",
    "island_under_capacity": "Disconnected part of the network cannot meet its demand",

    # Blank or non-numeric inputs (a series can cover only some time steps)
    "nonfinite_bound": "A capacity limit is blank or not a number",
    "nonfinite_bound_partial_coverage": "A capacity limit is missing for some time steps",
    "nonfinite_cost": "A cost is blank or not a number",
    "nonfinite_cost_partial_coverage": "A cost is missing for some time steps",
    "nonfinite_efficiency": "An efficiency or loss value is blank or not a number",
    "nonfinite_efficiency_partial_coverage": "An efficiency or loss value is missing for some time steps",
    "nonfinite_input": "An input is blank or not a number",
    "nonfinite_input_partial_coverage": "An input is missing for some time steps",
    "nonfinite_storage_constant": "A storage or constraint constant is blank or not a number",
    "nonfinite_storage_constant_partial_coverage": "A storage or constraint constant is missing for some time steps",

    # Time-series quality
    "timeseries_all_zero": "Time series is zero in every time step",
    "timeseries_frozen": "Time series never changes",
    "timeseries_negative": "Time series has negative values",
    "timeseries_scale_outlier": "Demand is much larger or smaller than the other loads",
    "timeseries_spike": "Time series has an extreme spike",

    # Decision study
    "study_network_tariff_in_links": "Study network from an earlier version prices its tariff on the meter links",
    # Fixed-size runs ("sizing off")
    "fixed_sizes_unknown": "Sizing source not recognised",
    "fixed_sizes_pf_mode": "Sizing off needs the optimisation mode, not power flow",
    "fixed_sizes_with_myopic": "Sizing off cannot run with the myopic strategy",
    "fixed_sizes_with_rolling": "Sizing off cannot run with the rolling strategy",
    "fixed_sizes_no_capacity_run": "Sizing off needs a capacity run of this network first",
    "fixed_sizes_source_changed": "Network sizes no longer match the capacity run",
    "fixed_sizes_nameplate_empty": "Sizing off on the nameplate: every extendable asset's nameplate is 0",
    "fixed_sizes_zero_nameplate": "Asset with a nameplate of 0 is not built when sizing is off",
}


def issue_title(code: str) -> str:
    """The plain title for an issue code; an unknown code reads as a sentence."""
    known = ISSUE_TITLES.get(code)
    if known:
        return known
    words = code.replace("_", " ").strip()
    return words[0].upper() + words[1:] if words else "Issue"
