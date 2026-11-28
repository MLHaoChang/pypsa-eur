"""
UI control: ui_select_component and ui_open_panel: schemas and routes.

Moved out of `harness/catalogue.py` (RETRO-FRAG-1); spliced back at its old
place, so it declares no ORDER.
Dispatchers: `services/chat_dispatchers/ui_control.py`.

A schema fragment imports only `harness.catalogue_base`, so the enums below
are copies of catalogue's; `tests/test_tool_fragments.py` pins each.
"""

from __future__ import annotations

from harness.catalogue_base import _UI_EVENT, _t

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

TOOLS = [
    _t(
        "ui_select_component",
        "Emit a typed SSE ui_event frame the ChatPanel forwards to "
        "uiStore.setSelectedComponent, closing an open panel except Issues so Properties is visible. "
        "With open_editor=true, opens the component's Edit form in Properties for the user to change and save; "
        "nothing is saved by this tool. NO backend mutation. Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "open_editor": {"type": "boolean"},
        },
        ["component_class", "name"],
    ),
    _t(
        "ui_open_panel",
        "Navigate the GUI via SSE ui_event (kind=navigate). Opens a main "
        "panel (Results / SolverSettings / TimeSeriesManager / Scenarios / "
        "Issues / Chat / project_picker / new_project / …), optionally a "
        "Results sub-tab (capex, dispatch, economics, …), a bottom asset "
        "table tab (Buses, Generators, …), and/or the A|B compare rail with "
        "scenario picks. Use project_picker when the user wants to browse "
        "saved projects without naming one. hubDesign opens the step-by-step "
        "Energy Hub design panel (Guided mode's main view). workspace, settings, "
        "gridspine, reports and campusElectrical (the grid connection) open their application panels; campusStudy opens the grid study's settings and run, campusResults its results; economics opens the rates, "
        "price basis and every asset's investment costs; "
        "adequacy and fmea select their Results tabs. Use map with canvas_view hybrid "
        "(satellite with labels) or site (the 3D site view). "
        "With hubDesign, guided_step opens one Guided card (start, site, goal, results or improve). "
        "With campusElectrical, campusStudy, campusResults or gridspine, panel_section scrolls to one "
        "section of that panel (for example island, grid_codes, readback); a section the project does not "
        "have yet leaves the panel at its top. "
        "With results_tab loadflow, pass loadflow_source, window_from/window_to and "
        "highlight_branch from get_loadflow_summary's ui_reference to open the same source and "
        "window and highlight one line or transformer. Only when one of these is given, an "
        "unavailable view or an unknown branch refuses with no navigation; the result's highlight "
        "says whether the branch is ranked and shown. With panel_id scenarios, highlight_projects "
        "(1–4 project ids, e.g. a sweep's new cases) highlights them in the scenario tree. Safety: read.",
        {
            "panel_id": {"type": "string", "enum": SAFETY_PANEL_ENUM},
            "canvas_view": {
                "type": "string",
                "enum": ["blank", "satellite", "hybrid", "site"],
            },
            "guided_step": {"type": "string", "enum": GUIDED_STEP_ENUM},
            "panel_section": {"type": "string", "enum": PANEL_SECTION_ENUM},
            "results_tab": {"type": "string", "enum": RESULTS_TAB_ENUM},
            "bottom_tab": {"type": "string", "enum": BOTTOM_TAB_ENUM},
            "compare_rail": {"type": "boolean"},
            "compare_a": {"type": "string"},
            "compare_b": {"type": "string"},
            "compare_tab": {"type": "string", "enum": COMPARE_TAB_ENUM},
            "loadflow_source": {"type": "string", "enum": ["lopf", "ac_pf"]},
            "window_from": {"type": "integer", "minimum": 0},
            "window_to": {"type": "integer", "minimum": 0},
            "highlight_branch": {
                "type": "object",
                "properties": {
                    "component": {"type": "string", "enum": ["Line", "Transformer"]},
                    "name": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "required": ["component", "name"],
                "additionalProperties": False,
            },
            "highlight_projects": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {"type": "string"},
            },
        },
        ["panel_id"],
    ),
]

ROUTES = {
    "ui_select_component": _UI_EVENT,
    "ui_open_panel": _UI_EVENT,
}
