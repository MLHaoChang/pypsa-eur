"""
The chat tool catalogue's shared builders and enums (RETRO-FRAG-1).

Split out of `harness/catalogue.py` so a per-tool fragment under
`harness/catalogue_tools/` builds its entries without importing the catalogue
itself (which imports every fragment). `harness/catalogue.py` re-exports every
name here, so `catalogue.COMPONENT_CLASS_ENUM` and the rest keep working.
"""

from __future__ import annotations

from typing import Any

from services.asset_schema.attrs_doc import ATTRS_DOC  # noqa: F401  (for the catalogue and fragments)


# Reused enum constants (kept module-level so test_chat_tools_dispatch can
# import them for parametrised testing without re-grepping the file).
COMPONENT_CLASS_ENUM = [
    "Bus",
    "Carrier",
    "Line",
    "Link",
    "Transformer",
    "Generator",
    "StorageUnit",
    "Store",
    "Load",
    "ShuntImpedance",
    "GlobalConstraint",
]
# CRUD-eligible classes (everything in _GENERIC_CRUD_ATTRS + GlobalConstraint
# via its dedicated CRUD).
CRUD_CLASS_ENUM = COMPONENT_CLASS_ENUM[:]
PROFILE_KIND_ENUM = ["loads", "generators", "links"]
# The Guided cards. A literal, because the inventory reads this file without the backend
# on its path; test_guided_steps_match_the_workflow pins it to the hub-design workflow.
GUIDED_STEP_ENUM = ["start", "site", "goal", "results", "improve"]
# The sections of the grid connection, grid study, its results and GridSpine panels chat can scroll to.
# A literal for the same reason; test_panel_section_enum_matches_the_pairs pins it to chat_tools._PANEL_SECTIONS.
PANEL_SECTION_ENUM = [
    "campus_file",
    "grid_connection",
    "connection_limits",
    "grid_codes",
    "library",
    "study_settings",
    "island",
    "grid_code_results",
    "electrical_design",
    "dispatch_source",
    "configuration",
    "ranked_snapshots",
    "assumptions_ledger",
    "readback",
    "connection_capacity",
    "connection_assessment",
]
TIMESERIES_KIND_ENUM = ["loads", "generators", "links"]
# Friendly + SlidePanel ids accepted by ui_open_panel (frontend normalises).
SAFETY_PANEL_ENUM = [
    "Results",
    "results",
    "Topology",
    "topology",
    "MapCanvas",
    "map",
    "BottomPanel",
    "PropertiesPanel",
    "properties",
    "TimeSeriesManager",
    "timeseries",
    "LoadProfileManager",
    "VintagePeriodBoundsModal",
    "capacityBounds",
    "OverviewPanel",
    "overview",
    "GenerationStack",
    "ImportExport",
    "import_export",
    "SolverSettings",
    "simparams",
    "Compare",
    "compare",
    "IssuesPanel",
    "issues",
    "CommandPalette",
    "palette",
    "Chat",
    "chat",
    "Scenarios",
    "scenarios",
    "Snapshots",
    "snapshots",
    "Horizon",
    "horizon",
    "SolveQueue",
    "solveQueue",
    "ProjectPicker",
    "project_picker",
    "OpenProject",
    "NewProject",
    "new_project",
    "NewProjectWizard",
    # Guided-mode hub-design panel (frontend normalises both to 'hubDesign').
    "HubDesign",
    "hubDesign",
    "Workspace",
    "workspace",
    "Settings",
    "settings",
    "GridSpine",
    "gridspine",
    "Reports",
    "reports",
    "CampusElectrical",
    "campusElectrical",
    # The grid study's settings (section 8) and results (section 10).
    "CampusStudy",
    "campusStudy",
    "CampusResults",
    "campusResults",
    # The guided investment study panel (frontend normalises both to 'decision').
    "DecisionStudy",
    "decision",
    # Economics in Expert: rates, price basis and every asset's costs.
    "Economics",
    "economics",
    # The renewable profile generator (Profile Studio) slide panel.
    "ProfileStudio",
    "profiles",
    # The workspace-level Commercial library page (tariffs, contracts, series).
    "CommercialLibrary",
    "library",
]
RESULTS_TAB_ENUM = [
    "summary",  # M3: the answer-first tab Results opens on after a solve
    "overview",
    "capex",
    "dispatch",
    "loadflow",
    "prices",
    "economics",
    "emissions",
    "curtailment",
    "lostload",
    "storage",
    "asset",
    "investment",  # IC P3 WP3.4 (the Investment tab, WP3.5)
    "adequacy",
    "fmea",
    "flows",  # site commercial UX A1 (Energy flow)
    "loadshape",  # site commercial UX A2 (Load shape)
    "grid_code",  # W10 R2 (Grid code & connection)
    "electrical_design",  # W10 R2 (Electrical design)
    "island",  # W10 R3 (Control: Island & ride-through)
    "grid_forming",  # W10 R3 (Control: Grid-forming)
    "grid_risk",  # G2P-13 (Reliability: Grid risk to production)
    "products",  # guided-results 09 (Capacity expansion: Products)
]
BOTTOM_TAB_ENUM = [
    "Log",
    "History",
    "Buses",
    "Lines",
    "Transformers",
    "Generators",
    "Storage",
    "Stores",
    "Loads",
    "Links",
    "Carriers",
    "Shunts",
]
COMPARE_FOCUS_ENUM = [
    "overview",
    "capacity",
    "dispatch",
    "loading",
    "economics",
    "emissions",
    "prices",
    "curtailment",
    "lost_load",
    "storage_cycling",
    "all",
]
SENSITIVITY_METRICS = [
    "total_cost_meur",
    "opex_meur",
    "capex_meur_total",
    "capacity_mw_total",
    "dispatch_gwh_total",
    "emissions_kt",
    "curtailment_gwh",
    "lost_load_mwh",
    "peak_line_loading_pct",
]
COMPARE_TAB_ENUM = [
    "overview",
    "capacity",
    "dispatch",
    "loading",
    "prices",
    "emissions",
    "economics",
    "curtailment",
    "lost_load",
    "storage_cycling",
]
RESULTS_ENUM = [
    "cost_breakdown",
    "objective_decomposition",
    "economics_by_carrier",
    "statistics",
    "generators",
    "storage_dispatch",
    "store_dispatch",
    "store_energy",
    "storage",
    "lines",
    "links",
    "lcoh",
    "ac_pf_status",
    "losses",
    "carrier_kpis",
    "emissions",
    "transformers",
    "unit_commitment",
    "line_duals",
    "voltages",
    "line_reactive",
    "transformer_reactive",
    "prices",
    "price_drivers",
    "curtailment",
    "lost_load",
    "loads",
    "asset_economics",
    "billing",
    "cfe_score",
    "value_flows",
    "renewable_fraction",
    "operating_reserve",
]
RESULTS_SOURCE_ENUM = ["lopf", "ac_pf"]
# Task 14 — per-asset results chat tools (get_asset_results /
# ui_open_asset_detail / export_asset_results). Kept as a literal list, same
# convention as COMPONENT_CLASS_ENUM/RESULTS_ENUM above — mirror
# services/asset_results/registry.py's CATEGORY_IDS and service.py's
# VIEW_MODES by hand if either changes.
ASSET_CATEGORY_ENUM = [
    "summary",
    "capacity",
    "dispatch",
    "storage",
    "loadflow",
    "prices",
    "economics",
    "emissions",
]
ASSET_VIEW_MODE_ENUM = ["chronological", "duration", "monthly"]
ASSET_RESOLUTION_ENUM = ["stats", "raw"]

# Adequacy / solution-FMEA surface (services/adequacy/*, routed under
# /api/results). Twelve no-argument GETs behind ONE dispatcher tool, same shape
# as RESULTS_ENUM/get_results. Mirror routers/results.py by hand if a kind is
# added there.
ADEQUACY_KIND_ENUM = [
    "copt",
    "fmea_modes",
    "fmea_sweep",
    "frontier",
    "mc",
    "mc_elcc_candidates",
    "coupling_loop",
    "margin_loop",
    "adequacy",
    "reserve_margin",
    "eh_study",
    "eh_reference_design",
    "eh_redundancy",
    "eh_levers",
    "eh_dtc",
    "eh_dtc_planning",
]
# The six kinds that run in a worker thread, i.e. the ones that can be
# aborted. Read-only surfaces (copt / fmea_modes / adequacy / reserve_margin /
# mc_elcc_candidates / eh_reference_design) have no thread to stop and are
# deliberately absent.
ADEQUACY_STUDY_ENUM = [
    "fmea_sweep",
    "frontier",
    "mc",
    "coupling_loop",
    "margin_loop",
    "eh_study",
]
# Where a reliability loop leaves the network when it finishes: at the base
# case it started from, or at the final iterate that met the target.
ADEQUACY_RESTORE_ENUM = ["base", "final"]
# Engine limits the run_eh_study schema states — imported, never restated.
from models.energy_hub import MAX_EH_BUDGET_SOLVES as _MAX_EH_BUDGET_SOLVES  # noqa: E402,F401
from services.adequacy.mc import MAX_DRAWS as _MC_MAX_DRAWS  # noqa: E402,F401

# The island report's table ids: imported, never restated.
from services.campus_island_report import TABLE_IDS as _ISLAND_TABLE_IDS  # noqa: E402,F401

# Energy Hub archetype packs — mirrors models.energy_hub.EnergyHubArchetype.
EH_ARCHETYPE_ENUM = ["strong_grid", "weak_flexible", "off_grid"]
# EH pipeline stages — mirrors models.energy_hub.EH_PIPELINE_STAGES (order
# included). An explicit list must keep apply_pack + ens_solve.
EH_STAGE_ENUM = [
    "apply_pack",
    "ens_solve",
    "frontier",
    "front",
    "mc_certify",
    "fmea_top",
    "redundancy",
    "levers",
    "dtc_stress",
    "dtc_planning",
    "assemble",
]

# Classes whose nominal capacity the optimiser can size — mirrors
# services/asset_results/compute._NOM_COL, the map explain_investment reads
# through `nom_col_for`. Bus and Load are absent: neither is an investment.
INVESTMENT_CLASS_ENUM = [
    "Generator",
    "StorageUnit",
    "Store",
    "Link",
    "Line",
    "Transformer",
]

# Outage-rate provenance. Mirrors services/adequacy/asset_health.py — the
# validator there is the authority; these enums exist so the model is told the
# vocabulary instead of guessing at it and getting a 422.
ASSET_HEALTH_COMPONENT_ENUM = [
    "generators",
    "storage_units",
    "stores",
    "links",
    "lines",
]
ASSET_HEALTH_METHOD_ENUM = [
    "inspection",
    "sensor",
    "lab_test",
    "vendor_datasheet",
    "operating_history",
    "fleet_statistic",
    "expert_judgement",
]
ASSET_HEALTH_CONFIDENCE_ENUM = ["low", "medium", "high"]


def _t(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Factory: one tool entry in Anthropic format."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


def _empty(name: str, description: str) -> dict[str, Any]:
    return _t(name, description, {}, [])


# LC-19: the re-run rule the solve tools and the load flow share (harness/run_lifecycle.py).
_RERUN_NOTE = (
    "When the current project's results already match its inputs, the call is refused with "
    "results_current before any card: tell the user and ask; only on their yes call it again "
    "with rerun_current: true. "
)


# TOOL_ROUTES sentinels for tools with no HTTP route (see catalogue.NON_HTTP_SENTINELS).
_SERVICE_CALL = ["_service_call_"]
_DERIVED = ["_derived_"]
_UI_EVENT = ["_ui_event_"]
_CHAT_JSONL = ["_chat_jsonl_"]
