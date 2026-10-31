"""
The project flow's section registry (project-workflow design §2.1, §3.1).

Pure data and lookups, no I/O: the ten flow sections in order, which section a
validator issue code belongs to, and the fallback on the issue's component
class. ``services/flow_service.py`` is the collector that uses it.

``CODE_SECTION`` holds a row for every code ``validation_service.validate_for_run``
can emit, including the codes of the modules it delegates to
(``timeseries_qa``, ``topology_analyzer``, ``commercial/preflight``,
``study/pre_u2``); ``tests/test_flow_sections_complete.py`` collects them from
the source and fails on a code with no row, so a new check never falls into
"other" unnoticed. A row may say ``BY_COMPONENT``: the code is emitted for
several component classes (``generator_p_nom_bounds``, ``nonfinite_cost``) and
files by the issue's class.
"""
from __future__ import annotations

from typing import Final

# (id, label), in flow order. The ids are the API contract (B2, the assistant).
SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("project", "Project & site"),
    ("carriers", "Carriers & processes"),
    ("connection", "Connection & grid"),
    ("loads", "Loads & demand"),
    ("assets", "Assets & sizing"),
    ("profiles", "Time series & profiles"),
    ("commercial", "Commercial & parties"),
    ("scenarios", "Scenarios & optimisation"),
    ("readiness", "Readiness → run"),
    ("results", "Results → reports"),
)
SECTION_IDS: Final[tuple[str, ...]] = tuple(s for s, _ in SECTIONS)
SECTION_LABELS: Final[dict[str, str]] = dict(SECTIONS)
# Sections 1–8 carry a state; readiness summarises them, results reports freshness.
INPUT_SECTIONS: Final[tuple[str, ...]] = SECTION_IDS[:8]

# The panel a section's item opens when the issue names no better one: the
# frontend's `SlidePanel` ids (`store/uiStore.ts`). None = the component's own
# Properties (the target's `component` + `entity`), or no panel yet (B2).
SECTION_PANEL: Final[dict[str, str | None]] = {
    "project": "horizon",
    "carriers": None,
    "connection": None,
    "loads": None,
    "assets": None,
    "profiles": "timeseries",
    "commercial": "economics",
    "scenarios": "simparams",
    "readiness": "issues",
    "results": "results",
}

BY_COMPONENT: Final = "by_component"

# Every code validate_for_run can emit → its section (or BY_COMPONENT).
CODE_SECTION: Final[dict[str, str]] = {
    # 1 Project & site: snapshots and weightings.
    "snapshots_empty": "project",
    "snapshot_weighting_nan": "project",
    "snapshot_weights_nyears_off": "project",
    "multi_period_no_periods": "project",
    # 2 Carriers & processes.
    "carrier_co2_nan": "carriers",
    "carrier_zero_co2": "carriers",
    # 3 Connection & grid: buses, branches, topology.
    "buses_empty": "connection",
    "bus_v_nom_invalid": "connection",
    "bus_ref_missing": BY_COMPONENT,
    "bus_ref_unknown": BY_COMPONENT,
    "pf_no_slack": "connection",
    "line_loss_high": "connection",
    "line_loss_vnom_too_small": "connection",
    "transformer_loss_high": "connection",
    "transformer_type_unregistered": "connection",
    "import_energy_link_bidirectional": "connection",
    "network_islanded": "connection",
    "island_no_supply": "connection",
    "island_under_capacity": "connection",
    # 4 Loads & demand, with their flexibility (demand response).
    "load_p_set_invalid": "loads",
    "load_p_set_nan": "loads",
    "dsr_double_count_risk": "loads",
    "dsr_enabled_without_buses": "loads",
    "dsr_unknown_bus": "loads",
    "dsr_zero_volume": "loads",
    # 5 Assets & sizing: units, their sizing, UC and outage data.
    "asset_retired_before_periods": "assets",
    "asset_unbuildable_period": "assets",
    "availability_may_include_outages": "assets",
    "curtailment_cost_on_thermal": "assets",
    "curtailment_cost_negative_prices": "assets",
    "gen_committable_extendable": "assets",
    "gen_control_invalid": "assets",
    "gen_uc_param_invalid": "assets",
    "gen_zero_costs": "assets",
    "multi_period_no_extendable": "assets",
    "outage_params_implausible": "assets",
    "outages_folded_into_availability": "assets",
    "outages_folded_into_availability_ignored": "assets",
    "p_max_pu_above_one": "assets",
    "profile_and_outage_modelled": "assets",
    "storage_initial_ignored": "assets",
    "inventory_cadence_needs_year": "assets",
    "storage_max_hours_invalid": "assets",
    "storage_soc_oob": "assets",
    "split_storage_sizes": "assets",
    "uc_start_cost_unweighted": "assets",
    # Per-class value checks (`{class}_…` in validation_service, and the
    # non-finite input categories): filed by the issue's component class.
    "link_efficiency_invalid": BY_COMPONENT,
    "nonfinite_bound": BY_COMPONENT,
    "nonfinite_cost": BY_COMPONENT,
    "nonfinite_efficiency": BY_COMPONENT,
    "nonfinite_storage_constant": BY_COMPONENT,
    "nonfinite_input": BY_COMPONENT,
    # A series that covers part of the horizon is a time-series problem.
    "nonfinite_bound_partial_coverage": "profiles",
    "nonfinite_cost_partial_coverage": "profiles",
    "nonfinite_efficiency_partial_coverage": "profiles",
    "nonfinite_storage_constant_partial_coverage": "profiles",
    "nonfinite_input_partial_coverage": "profiles",
    # 6 Time series & profiles (timeseries_qa).
    "timeseries_all_zero": "profiles",
    "timeseries_frozen": "profiles",
    "timeseries_negative": "profiles",
    "timeseries_scale_outlier": "profiles",
    "timeseries_spike": "profiles",
    # 7 Commercial & parties.
    "discount_rate_no_effect": "commercial",
    "tariff_export_exceeds_import": "commercial",
    "tariff_export_exceeds_import_via_storage": "commercial",
    "study_network_tariff_in_links": "commercial",
    # 8 Scenarios & optimisation: solver mode, strategy, VOLL, CO2, the
    # reliability goal.
    "solver_unavailable": "scenarios",
    "unknown_mode": "scenarios",
    "co2_price_no_fossil": "scenarios",
    "voll_no_loads": "scenarios",
    # Grid-to-process ticket 08 (design §5 table): the lost-production value lives
    # in 8 beside VoLL; the criticality tier on the demand, in 4.
    "lost_production_value_below_buy_in": "scenarios",
    "safety_tier_off_critical_bus": "loads",
    "ens_cap_generous": "scenarios",
    "ens_cap_unsupported_strategy": "scenarios",
    "ens_cap_without_voll": "scenarios",
    "ens_zone_multiple_without_cap": "scenarios",
    "import_energy_cap_unsupported_strategy": "scenarios",
    "reserve_margin_carrier_default_derating": "scenarios",
    "reserve_margin_myopic_report_is_partial": "scenarios",
    "reserve_margin_unpriceable_assets": "scenarios",
    "reserve_margin_unreachable": "scenarios",
    "reserve_margin_unsupported_strategy": "scenarios",
    "rolling_horizon_exceeds_snapshots": "scenarios",
    "rolling_horizon_invalid": "scenarios",
    "rolling_overlap_exceeds_horizon": "scenarios",
    "rolling_overlap_invalid": "scenarios",
    "rolling_with_ac_pf": "scenarios",
    "rolling_with_multi_period": "scenarios",
    "rolling_with_sclopf": "scenarios",
    "fixed_sizes_no_capacity_run": "scenarios",
    "fixed_sizes_source_changed": "scenarios",
    "fixed_sizes_with_myopic": "scenarios",
    "fixed_sizes_with_rolling": "scenarios",
    "fixed_sizes_pf_mode": "scenarios",
    "fixed_sizes_nameplate_empty": "scenarios",
    "fixed_sizes_zero_nameplate": "scenarios",
    "fixed_sizes_unknown": "scenarios",
    "myopic_capacity_locked_after_first_period": "scenarios",
    "myopic_flat_snapshots": "scenarios",
    "myopic_no_periods": "scenarios",
    "myopic_requires_multi_period": "scenarios",
    "myopic_sclopf_cost": "scenarios",
    "myopic_with_ac_pf": "scenarios",
    "sclopf_large_contingency_set": "scenarios",
    "sclopf_no_branches": "scenarios",
    "sclopf_radial_subnet": "scenarios",
    "sclopf_scope_current_with_lf": "scenarios",
    "sclopf_scope_invalid": "scenarios",
    "sclopf_with_transmission_losses": "scenarios",
    "stage2_multi_period": "scenarios",
    "stage2_multi_slack": "scenarios",
    "stage2_no_explicit_slack": "scenarios",
    "stage2_zero_impedance": "scenarios",
}

# Code families matched by prefix after the exact rows (design §3.1): the
# commercial preflight's `commercial.<check>` codes, and the families the
# design names, so a new member of a family is filed without a new row.
CODE_PREFIX_SECTION: Final[tuple[tuple[str, str], ...]] = (
    ("commercial.", "commercial"),
    ("carrier_", "carriers"),
    ("discount_rate_", "commercial"),
    ("snapshot", "project"),
    ("sclopf_", "scenarios"),
    ("rolling_", "scenarios"),
    ("myopic_", "scenarios"),
    ("voll_", "scenarios"),
    ("co2_", "scenarios"),
    ("timeseries_", "profiles"),
)

# Component class → section (design §3.1). Links split: the import/export
# Links the archetype selector picks are the connection, the rest are assets.
COMPONENT_SECTION: Final[dict[str, str]] = {
    "Bus": "connection",
    "Line": "connection",
    "Transformer": "connection",
    "Load": "loads",
    "Generator": "assets",
    "StorageUnit": "assets",
    "Store": "assets",
    "Link": "assets",
    "Carrier": "carriers",
    "GlobalConstraint": "scenarios",
}

# The per-class prefixes `validation_service` builds codes from
# (`f"{comp.lower()}_…"`), for the completeness test and the lookup.
COMPONENT_CODE_PREFIXES: Final[tuple[str, ...]] = (
    "generator_", "storageunit_", "store_", "link_", "line_", "transformer_",
)


def section_for_code(code: str) -> str | None:
    """The section (or ``BY_COMPONENT``) a code files to, or None if unknown."""
    if code in CODE_SECTION:
        return CODE_SECTION[code]
    if code.startswith(COMPONENT_CODE_PREFIXES):
        return BY_COMPONENT
    for prefix, section in CODE_PREFIX_SECTION:
        if code.startswith(prefix):
            return section
    return None


def file_issue(code: str, component_class: str, name: str,
               connection_links: frozenset[str] = frozenset()) -> str:
    """The section an issue belongs to.

    ``connection_links`` are the import/export Links
    (`archetypes.select_import_links_with_rule`); a Link issue on one of them
    is the connection's. A code with no row and no class files to
    ``readiness``, so it still shows (and still blocks, if an error) rather
    than vanishing.
    """
    section = section_for_code(code)
    if section is not None and section != BY_COMPONENT:
        return section
    return _class_section(component_class, name, connection_links) or "readiness"


def _class_section(component_class: str, name: str,
                   connection_links: frozenset[str]) -> str | None:
    """The class rule ``file_issue`` and ``section_for`` share: import/export
    Links are the connection, other classes by ``COMPONENT_SECTION``."""
    if component_class == "Link" and name in connection_links:
        return "connection"
    return COMPONENT_SECTION.get(component_class)


def section_for(component_class: str, entity: str,
                connection_links: frozenset[str] = frozenset()) -> dict:
    """``{id, label, panel}``: the section a component lives in, by the class
    rule the flow files issues with. An unknown class is the project's
    (Project & site): an edit to it is a project setting, where an issue with
    no class files to ``readiness`` so it still shows."""
    section = _class_section(component_class, entity, connection_links) or "project"
    return {"id": section, "label": SECTION_LABELS[section], "panel": SECTION_PANEL[section]}
