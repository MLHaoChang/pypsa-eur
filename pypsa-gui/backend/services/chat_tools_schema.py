"""
Phase 1 chatbot integration v6 — Anthropic-format tool schemas.

This module exports a single ``TOOLS`` list of dicts in the Anthropic
Messages-API tool-use format:

    {"name": str, "description": str, "input_schema": JSONSchema}

The list IS the registry — `len(TOOLS)` is the only source of truth for the
tool count (v4-NIT-1 / v6-F4: no pre-stated magic number anywhere). Phase 1
QA gate A asserts both that `len(TOOLS)` matches `len(chat_tools.DISPATCHERS)`
and that every `name` resolves to a callable.

v6-F3 — output-description audit:
    Tools whose underlying handler returns a Pydantic model carry the
    actual field names from `models/schemas.py` in their description so the
    Phase 1 schema-match test can grep description vs the live schema.

v6-F2 — list_projects.resident snapshot semantic:
    Documented INSIDE the list_projects description so the LLM knows the
    `resident` flag may flip between this call and a follow-up
    activate_project, and the cold path still succeeds.

v6-F1 — save_project guard:
    Backend-level (already shipped Phase 0). save_project_as carries the
    chat-side pre-check note.

Schema conventions:
  * `input_schema.type = "object"`, properties enumerated, `required` list set.
  * Enum-bounded inputs ALWAYS use JSON Schema enum (no free-form string when
    the underlying API gates on a discrete set) — C16 invariant.
  * Optional fields are NOT in `required`.
"""
from __future__ import annotations

from typing import Any


# Reused enum constants (kept module-level so test_chat_tools_dispatch can
# import them for parametrised testing without re-grepping the file).
COMPONENT_CLASS_ENUM = [
    "Bus", "Carrier", "Line", "Link", "Transformer", "Generator",
    "StorageUnit", "Store", "Load", "ShuntImpedance", "GlobalConstraint",
]
# CRUD-eligible classes (everything in _GENERIC_CRUD_ATTRS + GlobalConstraint
# via its dedicated CRUD).
CRUD_CLASS_ENUM = COMPONENT_CLASS_ENUM[:]
PROFILE_KIND_ENUM = ["loads", "generators", "links"]
TIMESERIES_KIND_ENUM = ["loads", "generators", "links"]
# Friendly + SlidePanel ids accepted by ui_open_panel (frontend normalises).
SAFETY_PANEL_ENUM = [
    "Results", "results",
    "Topology", "topology",
    "MapCanvas", "map",
    "BottomPanel",
    "PropertiesPanel", "properties",
    "TimeSeriesManager", "timeseries", "LoadProfileManager",
    "VintagePeriodBoundsModal", "capacityBounds",
    "OverviewPanel", "overview",
    "GenerationStack",
    "ImportExport", "import_export",
    "SolverSettings", "simparams",
    "Compare", "compare",
    "IssuesPanel", "issues",
    "CommandPalette", "palette",
    "Chat", "chat",
    "Scenarios", "scenarios",
    "Snapshots", "snapshots",
    "Horizon", "horizon",
    "SolveQueue", "solveQueue",
    "ProjectPicker", "project_picker", "OpenProject",
    "NewProject", "new_project", "NewProjectWizard",
]
RESULTS_TAB_ENUM = [
    "overview", "capex", "dispatch", "loadflow", "prices", "economics",
    "emissions", "curtailment", "lostload", "storage", "asset",
]
BOTTOM_TAB_ENUM = [
    "Log", "History", "Buses", "Lines", "Transformers", "Generators",
    "Storage", "Stores", "Loads", "Links", "Carriers",
]
COMPARE_FOCUS_ENUM = [
    "overview", "capacity", "dispatch", "economics", "emissions", "prices",
    "curtailment", "lost_load", "storage_cycling", "all",
]
COMPARE_TAB_ENUM = [
    "overview", "capacity", "dispatch", "loading", "prices", "emissions",
    "economics", "curtailment", "lost_load", "storage_cycling",
]
RESULTS_ENUM = [
    "cost_breakdown", "objective_decomposition", "economics_by_carrier",
    "statistics", "generators", "storage_dispatch", "store_dispatch",
    "store_energy", "storage", "lines", "links", "lcoh", "ac_pf_status",
    "losses", "carrier_kpis", "emissions", "transformers", "unit_commitment",
    "line_duals", "voltages", "line_reactive", "transformer_reactive",
    "prices", "price_drivers", "curtailment", "lost_load", "loads",
    "asset_economics",
]
RESULTS_SOURCE_ENUM = ["lopf", "ac_pf"]
# Task 14 — per-asset results chat tools (get_asset_results /
# ui_open_asset_detail / export_asset_results). Kept as a literal list, same
# convention as COMPONENT_CLASS_ENUM/RESULTS_ENUM above — mirror
# services/asset_results/registry.py's CATEGORY_IDS and service.py's
# VIEW_MODES by hand if either changes.
ASSET_CATEGORY_ENUM = [
    "summary", "capacity", "dispatch", "storage",
    "loadflow", "prices", "economics", "emissions",
]
ASSET_VIEW_MODE_ENUM = ["chronological", "duration", "monthly"]
ASSET_RESOLUTION_ENUM = ["stats", "raw"]


def _t(name: str, description: str, properties: dict[str, Any],
       required: list[str] | None = None) -> dict[str, Any]:
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


TOOLS: list[dict[str, Any]] = [
    # ── Read (22) ──────────────────────────────────────────────────────────

    _t(
        "list_components",
        "List one class of component as JSON rows, one page at a time "
        "(transient-filtered: solver-internal vintage clones and VOLL slack "
        "generators are hidden). Returns "
        "{items, total_count, offset, returned, has_more}. `total_count` is "
        "the size of the WHOLE class, not the page — compare it against "
        "`returned` to see what you are missing, and re-call with "
        "offset=offset+returned while has_more is true. Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "offset": {
                "type": "integer",
                "description": "Row to start at. Defaults to 0.",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Rows to return. Defaults to 200; values above 1000 are "
                    "clamped, and the response then carries limit_clamped_to."
                ),
            },
        },
        ["component_class"],
    ),
    _t(
        "get_component",
        "Single-row direct df.loc[name].to_dict() lookup — cheap; does NOT "
        "re-fetch a multi-MB dataframe. Returns one row dict with NaN/Inf "
        "coerced to None. Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
        },
        ["component_class", "name"],
    ),
    _empty(
        "diagnose_network",
        "Electrical connectivity diagnosis: how many islands the network "
        "splits into, which buses have no branch attached, and — the usual "
        "cause of an infeasible solve — which islands hold load but nothing "
        "able to serve it. Returns {bus_count, island_count, islands, "
        "isolated_buses, islands_without_generation, verdict}, where verdict "
        "is connected | fragmented | infeasible_topology | empty. Call this "
        "FIRST when a solve is infeasible or a result looks impossible. Does "
        "not check dangling bus references — run_preflight covers those. "
        "Safety: read.",
    ),
    _empty(
        "get_meta",
        "Network meta: {name, bus_count, line_count, snapshot_count, ...}. Safety: read.",
    ),
    _empty(
        "list_snapshots",
        "Snapshot index + weightings + uploaded-TS extent + can_sample_weeks "
        "flag. Handles flat and MultiIndex snapshots. Safety: read.",
    ),
    _empty(
        "list_carriers",
        "All carriers as rows (color, co2_emissions, nice_name). Safety: read.",
    ),
    _empty(
        "list_global_constraints",
        "All global constraints (CO2 caps, fuel limits, expansion limits). "
        "Transient-filtered. Safety: read.",
    ),
    _t(
        "list_timeseries_profiles",
        "Read-only listing of uploaded + computed time-series profiles for "
        "one component class. Safety: read.",
        {"profile_kind": {"type": "string", "enum": PROFILE_KIND_ENUM}},
        ["profile_kind"],
    ),
    _empty(
        "list_transformer_types",
        "Catalogue of common voltage-step presets used by create_transformer "
        "/ update_transformer. Safety: read.",
    ),
    _t(
        "download_timeseries_template",
        "CSV template for uploading time-series profiles. The CSV has the "
        "snapshot index as rows and asset names as columns — fill in values "
        "and upload via upload_load_profile / upload_generator_profile / "
        "upload_link_profile. Safety: read.",
        {"kind": {"type": "string", "enum": TIMESERIES_KIND_ENUM}},
        ["kind"],
    ),
    _empty(
        "download_snapshot_weightings_csv",
        "CSV dump of n.snapshot_weightings (one row per snapshot, columns "
        "objective/generators/stores). Round-trips through "
        "upload_snapshot_weightings_csv. Safety: read.",
    ),
    _empty(
        "list_investment_periods",
        "{periods: [..], weightings: {period: {years, objective}}} from "
        "n.investment_periods + n.investment_period_weightings. Safety: read.",
    ),
    _t(
        "list_vintage_bounds",
        "Per-asset, per-period extendable bounds (n.meta['vintage_bounds']). "
        "Returns ALL saved bounds (no filtering). Safety: read.",
        {},
    ),
    _empty(
        "get_vintage_results",
        "Per-vintage p_nom_opt aggregates from the most recent solve. Safety: read.",
    ),
    _t(
        "get_timeseries",
        "One time-series for a (component, name, attribute) triple. Period "
        "qualifier only used in multi-period networks. Safety: read.",
        {
            "component": {"type": "string"},
            "name": {"type": "string"},
            "attribute": {"type": "string"},
            "period": {"type": "integer"},
        },
        ["component", "name", "attribute"],
    ),
    _t(
        "list_all_timeseries",
        "Enumerate every (component, attribute, column) time-series entry "
        "with metadata, one page at a time. Returns "
        "{items, total_count, offset, returned, has_more} — re-call with "
        "offset=offset+returned while has_more is true. Safety: read.",
        {
            "offset": {
                "type": "integer",
                "description": "Row to start at. Defaults to 0.",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Rows to return. Defaults to 200; values above 1000 are "
                    "clamped, and the response then carries limit_clamped_to."
                ),
            },
        },
        [],
    ),
    _empty(
        "get_solver_config",
        "SolverConfig dict (solver_name, mode, options, multi_investment_periods, "
        "voll, discount_rate, …). Safety: read.",
    ),
    _empty(
        "get_solver_capabilities",
        "{multi_period_supported, sclopf_supported, ...} — chat answering "
        "'why can't I enable SCLOPF' needs this. Safety: read.",
    ),
    _empty(
        "get_asset_costs",
        "Periodised CAPEX defaults — $/kW per asset type. Safety: read.",
    ),
    _empty(
        "get_simulation_status",
        "{status, objective, solve_time, condition} from _state_snapshot. Safety: read.",
    ),
    _empty(
        "get_simulation_lock_status",
        "{lock_held, worker_alive} — non-blocking probe of the active solve. "
        "Safety: read.",
    ),
    _empty(
        "get_simulation_log_history",
        "Returns {lines: [str], running: bool} — the most recent solver-log "
        "lines (history of the BufferedLogQueue) plus whether a solve is "
        "currently running. Safety: read.",
    ),
    _t(
        "get_results",
        "Results dispatcher. result_kind selects which /api/results/* "
        "endpoint to read; ac_pf_status routes to /api/results/ac_pf/status "
        "via a lookup dict (v4-MAJOR-4). source ('lopf' | 'ac_pf') is "
        "forwarded where the underlying handler accepts it. "
        "Returns (dispatch kinds): {index:[iso], columns:[name], data:[[float]]}; "
        "(cost_breakdown): {total, capex, opex, by_component, by_carrier, by_period}. "
        "Safety: read.",
        {
            "result_kind": {"type": "string", "enum": RESULTS_ENUM},
            "source": {"type": "string", "enum": RESULTS_SOURCE_ENUM},
        },
        ["result_kind"],
    ),
    _t(
        "get_aggregate_load",
        "Time-aligned sum of load p_set across loads (by explicit names CSV "
        "or by section). Returns {index, values, total_loads, "
        "loads_with_profile, peak, mean}. Safety: read.",
        {
            "section": {"type": "string"},
            "names": {"type": "string"},
        },
    ),

    # ── Component CRUD (4) ─────────────────────────────────────────────────

    _t(
        "create_component",
        "Create one component via the generic helper. Routes to the dedicated "
        "create_<class> handler so voltage validation (Transformer), "
        "haversine auto-fill (Line), carrier auto-create, and partial-PUT "
        "footgun mitigation (GlobalConstraint) all run. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "attrs": {"type": "object"},
        },
        ["component_class", "name", "attrs"],
    ),
    _t(
        "update_component",
        "Update one component. EXPLICIT routing (v6 F1/F2/F3): Bus with "
        "new_name set → POST /buses/{name}/rename (preserves dependent "
        "bus0/bus1 references); Bus without new_name → update_bus "
        "(coord-change line-length recompute); Transformer → "
        "update_transformer (voltage validation); GlobalConstraint → "
        "update_global_constraint (dedicated partial-PUT mitigation); other "
        "7 classes → generic _update_component. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "attrs": {"type": "object"},
            "new_name": {"type": "string"},
        },
        ["component_class", "name"],
    ),
    _t(
        "delete_component",
        "Delete one component (non-cascade). For Bus cascade-delete use "
        "cascade_delete_bus. _user_ts cleanup + vintage_bounds cleanup run "
        "via the generic helper. Safety: destructive.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
        },
        ["component_class", "name"],
    ),
    _t(
        "cascade_delete_bus",
        "Delete a Bus AND every line/link/transformer/generator/load/storage_unit"
        "/store attached to it. Confirmation card surfaces the cascade list. "
        "Safety: destructive.",
        {"name": {"type": "string"}},
        ["name"],
    ),

    # ── Bulk (1) ───────────────────────────────────────────────────────────

    _t(
        "bulk_update_components",
        "Atomic bulk attribute set on N components of one class (PATCH "
        "/api/network/_bulk). Single lock acquisition, single audit entry, "
        "single undo snapshot. Backend coerces values against df[col].dtype; "
        "all-or-nothing on unknown names. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "names": {"type": "array", "items": {"type": "string"}},
            "updates": {"type": "object"},
        },
        ["component_class", "names", "updates"],
    ),

    _t(
        "batch_create_components",
        "Create MANY components of one class in a single call. Prefer this "
        "over repeated create_component: a turn allows only 25 tool calls, "
        "so building a network one component at a time is a task that gets "
        "cut off rather than one that finishes slowly. Each entry is an "
        "object with 'name' plus that class's attributes. The WHOLE batch is "
        "refused if any entry is invalid, duplicates another entry's name, "
        "or collides with an existing component — nothing is created in that "
        "case, and the error names the offending entry and its index. Max "
        "200 per call. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "components": {
                "type": "array",
                "items": {"type": "object"},
                "description": "One object per component: {name, ...attributes}.",
            },
        },
        ["component_class", "components"],
    ),

    _t(
        "batch_delete_components",
        "Delete MANY components of one class in a single call. The WHOLE "
        "batch is refused if any name is absent or is solver scaffolding — "
        "nothing is deleted in that case. Max 200 per call. "
        "Safety: destructive.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "names": {"type": "array", "items": {"type": "string"}},
        },
        ["component_class", "names"],
    ),

    # ── Carriers (1) ───────────────────────────────────────────────────────

    _t(
        "create_carrier",
        "Create one carrier (color, co2_emissions, nice_name). Safety: write.",
        {
            "name": {"type": "string"},
            "color": {"type": "string"},
            "co2_emissions": {"type": "number"},
            "nice_name": {"type": "string"},
        },
        ["name"],
    ),

    # ── Meta (1) ───────────────────────────────────────────────────────────

    _t(
        "update_meta",
        "Update n.name (display title). Closes the n.name vs loaded_project "
        "divergence after a chat-driven Save-As. Safety: write.",
        {"name": {"type": "string"}},
        ["name"],
    ),

    # ── Topology (2) ───────────────────────────────────────────────────────

    _t(
        "cluster_network",
        "Apply clustering to reduce the bus count. `mode` (required) is one of "
        "nodal|zone|region|custom; `algorithm` is kmeans|hac|greedy_modularity|"
        "stubs; `n_clusters` is the target bus count (required by kmeans/hac). "
        "PyPSAService.set_network swap is destructive — topology is reshaped. "
        "Safety: destructive.",
        {
            "mode": {"type": "string", "enum": ["nodal", "zone", "region", "custom"]},
            "algorithm": {"type": "string",
                          "enum": ["kmeans", "hac", "greedy_modularity", "stubs"]},
            "n_clusters": {"type": "integer"},
        },
        ["mode"],
    ),
    _empty(
        "recalculate_line_lengths",
        "Rewrite n.lines.length from haversine distance between bus0 / bus1 "
        "coordinates. Buses without coords are skipped. Safety: write.",
    ),

    # ── Snapshots (4) ──────────────────────────────────────────────────────

    _t(
        "set_snapshots",
        "Set the flat snapshot DatetimeIndex (start, end, freq). Triggers "
        "_user_ts reapply + _normalise_dynamic_indexes. PITFALL: "
        "set_snapshots(MultiIndex) silently resets snapshot_weightings to 1.0 "
        "— confirmation card warns. Safety: write.",
        {
            "start": {"type": "string"},
            "end": {"type": "string"},
            "freq": {"type": "string"},
        },
        ["start", "end"],
    ),
    _t(
        "set_snapshot_weightings",
        "Patch per-snapshot weightings (objective / generators / stores). "
        "Multi-period: accepts both 'period|iso' and bare 'iso' keys "
        "(last-write-wins across periods on bare-iso). Safety: write.",
        {"updates": {"type": "object"}},
        ["updates"],
    ),
    _t(
        "upload_snapshot_weightings_csv",
        "Upload CSV (base64). Two-pass validate-then-apply — bad rows reject "
        "the whole upload BEFORE any write. Safety: write.",
        {
            "csv_content_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["csv_content_b64"],
    ),
    _t(
        "sample_representative_weeks",
        "Build representative-week snapshots by sampling ISO weeks per "
        "calendar month (n_weeks per month, 1-5). Requires a full-year hourly "
        "uploaded profile. snapshot_weightings are set to the days-in-month "
        "scaling that reconstructs the full year (REPLACES existing weights; "
        "audit-log warning emitted if the user had custom weights). "
        "Safety: write.",
        {
            "n_weeks": {"type": "integer"},
        },
        ["n_weeks"],
    ),

    # ── Investment periods (3) ─────────────────────────────────────────────

    _t(
        "set_multi_period_snapshots",
        "Promote flat→multi snapshots OR rebuild multi→multi for a new period "
        "list. Sets mi.name='snapshot' explicitly. PITFALL: weights reset to "
        "1.0 — warn in confirmation card. Safety: write.",
        {
            "periods": {"type": "array", "items": {"type": "integer"}},
            "operational_from": {"type": "string"},
            "operational_to": {"type": "string"},
            "freq": {"type": "string"},
        },
        ["periods", "operational_from", "operational_to"],
    ),
    _t(
        "set_investment_periods",
        "Set n.investment_periods. Handles all 3 transitions (flat→multi, "
        "multi→multi, multi→flat). PITFALL: multi→flat demotion trips the "
        "pandas M-dtype reindex bug — pre-trim _t tables. Safety: write.",
        {"periods": {"type": "array", "items": {"type": "integer"}}},
        ["periods"],
    ),
    _t(
        "set_investment_period_weightings",
        "PATCH per-period weightings (years, objective). Years scaling is "
        "what makes per-period OPEX correctly weighted across horizons. "
        "Safety: write.",
        {"updates": {"type": "object"}},
        ["updates"],
    ),

    # ── Vintage bounds (3) ─────────────────────────────────────────────────

    _t(
        "set_vintage_bounds",
        "Per-asset, per-period extendable bounds (p_nom_min / p_nom_max). "
        "Rejects unknown periods with 400 listing the valid set. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "period_bounds": {"type": "object"},
        },
        ["component_class", "name", "period_bounds"],
    ),
    _t(
        "delete_vintage_bounds",
        "Drop the vintage_bounds entry for one (component_class, name). "
        "Safety: destructive.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
        },
        ["component_class", "name"],
    ),
    _empty(
        "cleanup_orphan_vintages",
        "Idempotent sweep for orphan vintage entries (regex match AND "
        "(registry OR period match) — both gates per the CLAUDE.md pitfall). "
        "Safety: write.",
    ),

    # ── Time-series (5) ────────────────────────────────────────────────────

    _t(
        "upload_timeseries",
        "PER-ASSET time-series upload (POST /api/network/timeseries/upload). "
        "csv_content is a CSV whose first column is the timestamp index and whose "
        "data column is named EXACTLY `name` (the asset). _user_ts follows "
        "rename/delete via generic CRUD helpers. "
        "CRITICAL: do NOT inline full-year hourly CSVs (~8760 rows) — that "
        "exceeds the turn output budget and freezes the UI. For synthetic "
        "year profiles use generate_exemplary_timeseries instead. Safety: write.",
        {
            "component": {"type": "string"},
            "name": {"type": "string"},
            "attribute": {"type": "string"},
            "csv_content": {"type": "string"},
        },
        ["component", "name", "attribute", "csv_content"],
    ),
    _t(
        "generate_exemplary_timeseries",
        "SERVER-SIDE synthetic profile aligned to n.snapshots (no giant CSV in "
        "the tool args). Use this for exemplary full-year load / PV / flat "
        "series. Profiles: load_daily (diurnal+weekend demand → loads p_set), "
        "pv_solar (daylight CF → generators p_max_pu, peak≤1), constant. "
        "`peak` is MW for load p_set or per-unit for p_max_pu. Returns "
        "{rows, profile, value_min/max/mean, snapshot_count, …}. Safety: write.",
        {
            "component": {
                "type": "string",
                "enum": ["loads", "generators", "links", "storage_units", "stores"],
            },
            "name": {"type": "string"},
            "attribute": {"type": "string"},
            "profile": {
                "type": "string",
                "enum": ["load_daily", "pv_solar", "constant"],
            },
            "peak": {"type": "number"},
        },
        ["component", "name", "attribute"],
    ),
    _t(
        "delete_timeseries",
        "Remove one _user_ts entry. Safety: destructive.",
        {
            "component": {"type": "string"},
            "name": {"type": "string"},
            "attribute": {"type": "string"},
        },
        ["component", "name", "attribute"],
    ),
    _t(
        "upload_load_profile",
        "MULTI-COLUMN CSV upload of load p_set profiles. Columns ARE load "
        "names, index is timestamps. Returns {matched, unmatched, rows, "
        "snapshot_count}. For per-load upload use upload_timeseries with "
        "component='loads', attribute='p_set'. CRITICAL: do NOT base64-encode "
        "full-year hourly CSVs in the tool args — use "
        "generate_exemplary_timeseries for synthetic profiles. Safety: write.",
        {
            "csv_content_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["csv_content_b64"],
    ),
    _t(
        "upload_generator_profile",
        "MULTI-COLUMN CSV upload of generator profiles (default attribute "
        "p_max_pu). Columns ARE generator names. Returns {matched, unmatched, "
        "rows, snapshot_count}. CRITICAL: do NOT base64-encode full-year "
        "hourly CSVs — use generate_exemplary_timeseries (pv_solar) for "
        "synthetic PV. Safety: write.",
        {
            "csv_content_b64": {"type": "string"},
            "filename": {"type": "string"},
            "attribute": {"type": "string"},
        },
        ["csv_content_b64"],
    ),
    _t(
        "upload_link_profile",
        "MULTI-COLUMN CSV upload of link profiles (default attribute "
        "p_max_pu). Columns ARE link names. Safety: write.",
        {
            "csv_content_b64": {"type": "string"},
            "filename": {"type": "string"},
            "attribute": {"type": "string"},
        },
        ["csv_content_b64"],
    ),

    # ── Solver config (1) ──────────────────────────────────────────────────

    _t(
        "update_solver_config",
        "Partial update of SolverConfig. Backend uses model_dump(exclude_unset"
        "=True) + dict merge — partial PUT does NOT reset omitted fields to "
        "schema defaults (CLAUDE.md known pitfall). Safety: write.",
        {"partial": {"type": "object"}},
        ["partial"],
    ),

    # ── Validation (3) ─────────────────────────────────────────────────────

    _empty(
        "validate_network",
        "Run preflight validation. Returns list[Issue {severity, code, "
        "component_class, name, message}]. Lock-free, no side effects. "
        "Safety: read.",
    ),
    _empty(
        "check_solver_availability",
        "{highs, gurobi, scip, glpk} — which solvers are installed. Safety: read.",
    ),
    _empty(
        "dispatch_status",
        "{state: fresh|stale|none, mismatched_classes: [str]} — direct call to "
        "services.dispatch_status.dispatch_status_detail(n) (NO HTTP endpoint "
        "exists). mismatched_classes lists the component classes whose dispatch "
        "is stale. Surveys ALL dispatch-bearing component classes per CLAUDE.md. "
        "Safety: read.",
    ),

    # ── Simulation execution (4) ───────────────────────────────────────────

    _t(
        "run_simulation",
        "Start LOPF in a worker thread. Long-running (minutes to hours). "
        "[PHASE] / [VALIDATION] / TRACEBACK lines stream via the chat tool-"
        "progress SSE bridge. Safety: execution.",
        {},
    ),
    _empty(
        "run_ac_pf_stage",
        "Run Stage 2 AC PF using the most recent LOPF dispatch as the seed. "
        "Long-running. dispatch_status must be 'fresh' before calling. "
        "Safety: execution.",
    ),
    _empty(
        "abort_simulation",
        "Signal stop_event on the active solver thread. HiGHS/Gurobi "
        "mid-iteration native code is UNINTERRUPTIBLE — may take seconds. "
        "Safety: destructive.",
    ),
    _empty(
        "force_reset_simulation",
        "Force-clear solver state (emergency only). v4-NIT-2: classified as "
        "single destructive tier (NOT execution_long_running). Card UX: red "
        "border + 1s delay + disclaimer 'may not free the PyPSA lock if "
        "solver is in native code'. Safety: destructive.",
    ),

    # ── Solve queue (4) ────────────────────────────────────────────────────

    _t(
        "solve_queue_enqueue",
        "Enqueue a project for background solving. Dispatcher auto-runs on "
        "enqueue. Project must have a saved network.nc. Idempotent per project: "
        "if the project already has a queued or running job the response is 200 "
        "with THAT job and `already_queued: true`, and no second job is created "
        "— this is not an error, so do not retry. A new job returns "
        "`already_queued: false`. Safety: execution.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _empty(
        "solve_queue_list",
        "{jobs: [...], current: job_id|None} — FIFO queue snapshot. "
        "Safety: read.",
    ),
    _t(
        "solve_queue_abort",
        "Abort a running OR cancel a queued job. `job_id` is the job's UUID, "
        "exactly as returned by solve_queue_list / solve_queue_enqueue — not an "
        "index and not a project name. Safety: destructive.",
        {"job_id": {"type": "string"}},
        ["job_id"],
    ),
    _empty(
        "solve_queue_clear_finished",
        "Drop FINISHED job listing entries from the in-memory queue. "
        "Idempotent. No disk/solver impact. Safety: read.",
    ),

    # ── Project management (21) ────────────────────────────────────────────

    _empty(
        "list_projects",
        "All projects on disk as ProjectInfo entries: name, id, created_at, "
        "has_solver_config, bus_count, snapshot_count, objective, "
        "has_orphan_tmp, missing, parent_project, scenario_description, "
        "scenario_type (per "
        "schemas.py:467-491). `id` is the DB-registry UUID when multi-user "
        "auth is enabled and null in single-user mode. Each entry is "
        "augmented with `resident: bool` "
        "indicating whether the project currently has a ProjectContext in "
        "PyPSAService._contexts. v6-F2 NOTE: `resident` is a SNAPSHOT AT "
        "READ TIME. A concurrent eviction can flip resident=true → false "
        "between this call and a subsequent activate_project — both paths "
        "still succeed (cold path kicks in automatically). Do NOT retry on "
        "resident drift. Safety: read.",
    ),
    _t(
        "load_project",
        "Cold-load a project (GET /api/projects/{name}). Returns "
        "ImportSummary {buses, generators, lines, links, storage_units, "
        "stores, loads, transformers, snapshots} per schemas.py:455-464 "
        "(v6-F3). Swaps active context — in-memory unsaved edits are lost. "
        "Memory rule: chat MUST NOT load while the user has the same project "
        "open in the browser (autosave footgun). Safety: write.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "activate_project",
        "Open / switch the active project (POST /api/projects/{project_id}/"
        "activate). Prefer this when nothing is loaded or the user asks to "
        "open a saved project — list_projects first if the name is unclear. "
        "Emits project_rebound so the browser UI mirrors the new binding. "
        "Returns {activated: str, evicted: list[str]} per projects.py:1330. "
        "v6-F2: if `resident` flipped between list_projects and this call, "
        "the backend takes the cold path automatically "
        "(projects.py:1319-1326). Refuses with 409 error_kind='solver_in_"
        "flight' when a foreground solve is active. Safety: write.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "save_project",
        "Save active network as projects/<name>/. DESTRUCTIVE — overwrites "
        "existing data. The v6-F1 backend guard at projects.py:976-992 "
        "INSIDE ctx.mutation_lock refuses cross-project overwrite unless "
        "force or rebind. Safety: destructive.",
        {
            "name": {"type": "string"},
            "force": {"type": "boolean"},
            "expect": {"type": "string"},
        },
        ["name"],
    ),
    _t(
        "save_project_as",
        "Save-As (POST /api/projects/{name}?rebind=true). M1 chat-side "
        "PRE-CHECK: if `name` already exists AND active loaded_project != "
        "name, this tool returns HTTPException 409 error_kind='project_exists' "
        "BEFORE issuing the POST so the agent never accidentally overwrites a "
        "sibling project. Backend complement is the v6-F1 guard at "
        "projects.py:976-992 (defence in depth). Safety: destructive.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "save_project_a_copy",
        "Save a branched copy WITHOUT rebinding active context (rebind=false). "
        "Active session continues on the original project's chat.jsonl. "
        "Safety: destructive.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "rename_project",
        "Rename the project directory + update loaded_project. chat.jsonl "
        "moves with the directory (filesystem rename). Safety: destructive.",
        {
            "name": {"type": "string"},
            "new_name": {"type": "string"},
        },
        ["name", "new_name"],
    ),
    _t(
        "delete_project",
        "Delete project directory. v4-MINOR-1: cascade=true deletes the "
        "project AND all its descendant scenarios. On 409 with descendants "
        "(projects.py:1598-1606) the confirmation card surfaces the "
        "descendant list — do NOT auto-cascade. Doubly confirmed when name "
        "== active loaded_project. Safety: destructive.",
        {
            "name": {"type": "string"},
            "cascade": {"type": "boolean"},
        },
        ["name"],
    ),
    _t(
        "create_scenario",
        "Branch the active project under projects/<base>/<new_name>/. "
        "Requires active ctx.loaded_project == base. chat.jsonl is COPIED to "
        "the scenario dir (F12 Phase 4 polish). Safety: destructive.",
        {
            "base": {"type": "string"},
            "new_name": {"type": "string"},
            "description": {"type": "string"},
        },
        ["base", "new_name"],
    ),
    _t(
        "list_scenarios",
        "Derived tool (no /scenarios endpoint exists — B2 fix). Returns "
        "list_projects filtered to entries where parent_project == name. Each "
        "entry is a ProjectInfo {name, id, created_at, has_solver_config, "
        "bus_count, snapshot_count, objective, has_orphan_tmp, missing, "
        "parent_project, scenario_description, scenario_type} per "
        "schemas.py:467-491. "
        "Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "get_project_results_bundle",
        "Cached results bundle for a non-active project — read without "
        "activating. Apply result-ref truncation downstream. Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "get_project_layout",
        "Per-project saved canvas layout (pane positions, open tabs, zoom). "
        "Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "update_project_layout",
        "Write per-project canvas layout. Non-destructive UI state. "
        "Safety: write.",
        {
            "name": {"type": "string"},
            "layout": {"type": "object"},
        },
        ["name", "layout"],
    ),
    _t(
        "download_project_bundle",
        "Zip export of the project directory. Saves a downloadable .pypsaproj.zip "
        "in the chat file strip and returns its metadata {file_id, filename, size}. "
        "Requires a loaded project. Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "get_project_statistics",
        "Offline statistics summary (no activate). Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "get_project_network_meta",
        "Non-active project network meta — peek without losing the active. "
        "Safety: read.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "list_project_network_component",
        "Read one component table from a NON-ACTIVE resident project. Lets "
        "chat answer 'how many buses does project X have?' without losing the "
        "active. Safety: read.",
        {
            "project_id": {"type": "string"},
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
        },
        ["project_id", "component_class"],
    ),
    _t(
        "import_project_bundle",
        "Import a previously-exported project bundle (.zip). Creates a new "
        "project directory. Safety: destructive.",
        {
            "bundle_bytes_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["bundle_bytes_b64"],
    ),
    _t(
        "create_project_from_template",
        "Scaffold a new project from a built-in template. Safety: destructive.",
        {
            "template_id": {"type": "string"},
            "new_name": {"type": "string"},
        },
        ["template_id", "new_name"],
    ),
    _t(
        "get_project_compare_state",
        "A-vs-B compare payload (drives the Compare panel). Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "get_project_results_summary",
        "Numeric summary for Compare rail consumers. Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),

    # ── Project checkpoint snapshots (4) — distinct from time snapshots ────

    _t(
        "create_project_snapshot",
        "Disk-backup checkpoint of the project bundle. NOT n.snapshots — "
        "those are time-index snapshots, this is a versioning snapshot. "
        "Naming distinct via 'project_snapshot' prefix. Returns SnapshotInfo. "
        "Safety: write.",
        {
            "name": {"type": "string"},
            "label": {"type": "string"},
            "message": {"type": "string"},
        },
        ["name", "label"],
    ),
    _t(
        "list_project_snapshots",
        "list[SnapshotInfo] for a project. Safety: read.",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _t(
        "restore_project_snapshot",
        "Restore project from a saved snapshot. Overwrites project files + "
        "reloads in-memory. Pre-restore auto-snapshot mitigates user error. "
        "Safety: destructive.",
        {
            "name": {"type": "string"},
            "snapshot_id": {"type": "string"},
        },
        ["name", "snapshot_id"],
    ),
    _t(
        "delete_project_snapshot",
        "Remove a saved snapshot from disk. Safety: destructive.",
        {
            "name": {"type": "string"},
            "snapshot_id": {"type": "string"},
        },
        ["name", "snapshot_id"],
    ),

    # ── Import / Export (8) ────────────────────────────────────────────────

    _t(
        "import_network_nc",
        "Import a PyPSA netCDF (.nc) file. Returns ImportSummary {buses, "
        "generators, lines, links, storage_units, stores, loads, transformers, "
        "snapshots} per schemas.py:455-464. reset_network() runs first. "
        "Safety: destructive.",
        {
            "bytes_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["bytes_b64"],
    ),
    _t(
        "import_csv_bundle",
        "Import a CSV bundle (zip with one CSV per component class). Returns "
        "ImportSummary {buses, generators, lines, links, storage_units, "
        "stores, loads, transformers, snapshots} per schemas.py:455-464. "
        "Safety: destructive.",
        {
            "bytes_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["bytes_b64"],
    ),
    _t(
        "import_excel",
        "Import a network from an .xlsx workbook. Returns ImportSummary "
        "{buses, generators, lines, links, storage_units, stores, loads, "
        "transformers, snapshots} per schemas.py:455-464. Safety: destructive.",
        {
            "bytes_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["bytes_b64"],
    ),
    _t(
        "import_matpower",
        "Import a MATPOWER case file (.m). Returns ImportSummary {buses, "
        "generators, lines, links, storage_units, stores, loads, transformers, "
        "snapshots} per schemas.py:455-464. Safety: destructive.",
        {
            "bytes_b64": {"type": "string"},
            "filename": {"type": "string"},
        },
        ["bytes_b64"],
    ),
    _empty(
        "export_network_nc",
        "Export the active network as PyPSA netCDF. Saves a downloadable file in "
        "the chat file strip and returns its metadata {file_id, filename, size}. "
        "Requires a loaded project. Safety: read.",
    ),
    _empty(
        "export_csv_bundle",
        "Export the active network as a CSV bundle (zip). Saves a downloadable file "
        "and returns its metadata {file_id, filename, size}. Requires a loaded "
        "project. Safety: read.",
    ),
    _empty(
        "export_excel",
        "Export the active network to an .xlsx workbook. Saves a downloadable file "
        "and returns its metadata {file_id, filename, size}. Requires a loaded "
        "project. Safety: read.",
    ),
    _empty(
        "export_matpower",
        "Export the active network in MATPOWER case format. Saves a downloadable "
        "file and returns its metadata {file_id, filename, size}. Requires a "
        "loaded project. Safety: read.",
    ),

    # ── Audit / Undo (4) ───────────────────────────────────────────────────

    _t(
        "audit_log",
        "Recent audit-log entries (changelog deque). Each entry: {id, action, "
        "component_type, name, description, ts}. Chat-driven mutations carry "
        "action prefix 'agent:<verb>:<session6>'. Safety: read.",
        {"limit": {"type": "integer"}},
    ),
    _empty(
        "clear_audit_log",
        "Empty the changelog deque. Safety: destructive.",
    ),
    _empty(
        "undo_last",
        "Roll back the most recent mutation (pops the per-project undo stack). "
        "Returns {undone: bool, remaining: int} (remaining = undo-stack depth "
        "after the pop). Safety: destructive.",
    ),
    _empty(
        "undo_status",
        "{depth: int, memory_bytes: int, max_bytes: int, max_steps: int} — "
        "non-blocking undo probe (depth = current stack size; memory_bytes / "
        "max_bytes = byte budget; max_steps = step cap). Safety: read.",
    ),

    # ── UI control (3) ─────────────────────────────────────────────────────

    _t(
        "ui_select_component",
        "Emit a typed SSE ui_event frame the ChatPanel forwards to "
        "uiStore.setSelectedComponent. NO backend mutation. Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
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
        "saved projects without naming one. Safety: read.",
        {
            "panel_id": {"type": "string", "enum": SAFETY_PANEL_ENUM},
            "results_tab": {"type": "string", "enum": RESULTS_TAB_ENUM},
            "bottom_tab": {"type": "string", "enum": BOTTOM_TAB_ENUM},
            "compare_rail": {"type": "boolean"},
            "compare_a": {"type": "string"},
            "compare_b": {"type": "string"},
            "compare_tab": {"type": "string", "enum": COMPARE_TAB_ENUM},
        },
        ["panel_id"],
    ),
    _t(
        "compare_scenarios",
        "Side-by-side comparison of two saved projects/scenarios. Returns "
        "headline KPIs for A and B plus delta_b_minus_a (B − A), and the "
        "optional focus_section payload from each project's results-summary. "
        "Does NOT activate either project. Set open_compare_rail=true to also "
        "open the Results compare rail on the matching tab. Safety: read.",
        {
            "project_a": {"type": "string"},
            "project_b": {"type": "string"},
            "focus": {"type": "string", "enum": COMPARE_FOCUS_ENUM},
            "open_compare_rail": {"type": "boolean"},
        },
        ["project_a", "project_b"],
    ),
    _t(
        "ui_set_snapshot",
        "Drive SnapshotPicker selection from chat. Multi-period: use "
        "period|iso form. Safety: read.",
        {
            "snapshot_iso": {"type": "string"},
            "period": {"type": "integer"},
        },
        ["snapshot_iso"],
    ),

    # ── Conversation (2) ───────────────────────────────────────────────────

    _t(
        "list_chat_history",
        "Tail-read of ctx.chat_state.persist_path (chat.jsonl). Lock-free; "
        "skips trailing partial lines on JSONDecodeError; merges across "
        "rotated chat.jsonl.1. Safety: read.",
        {"limit": {"type": "integer"}},
    ),
    _empty(
        "clear_chat_history",
        "Empty chat.jsonl + chat.jsonl.1 for the active project. Acquires "
        "ctx.chat_state.lock (M9). Safety: destructive.",
    ),

    # ── Chatbot uploads — consume (5) ──────────────────────────────────────

    _empty(
        "list_uploads",
        "List the active project's uploaded + agent-exported files. Each "
        "entry: {file_id, filename, mime, kind, size_kb, uploaded_at}. "
        "kind='user_upload' for files the user dragged in; "
        "'agent_export' for files this agent created via the export_* "
        "tools. Safety: read.",
    ),
    _t(
        "read_upload_meta",
        "Full meta.json for one upload: schema_version, file_id, filename, "
        "mime, size, sha256, kind, uploaded_at, blob_ready, version, plus "
        "page_count + truncated_to_100_pages for PDFs. Safety: read.",
        {"file_id": {"type": "string"}},
        ["file_id"],
    ),
    _t(
        "read_excel_sheet",
        "Parse one sheet of an uploaded Excel (.xlsx/.xls) or CSV file. "
        "Returns {columns, rows, total_rows, total_cols, sheet_name, "
        "available_sheets, truncated}. `sheet_name` in the response is the "
        "REAL name pandas resolved (e.g. 'Sheet1', 'load_profile') — never "
        "a placeholder — so you can pass it verbatim to a follow-up "
        "`apply_demand_from_excel` call. `available_sheets` lists every "
        "sheet in the workbook so you don't need to guess. Omit "
        "`sheet_name` to read the first sheet. Rows are capped at "
        "`max_rows` (default 200). For CSV the sheet_name arg is ignored. "
        "Safety: read.",
        {
            "file_id": {"type": "string"},
            "sheet_name": {"type": "string"},
            "max_rows": {"type": "integer"},
        },
        ["file_id"],
    ),
    _t(
        "apply_demand_from_excel",
        "Write a demand profile from an uploaded Excel/CSV into the named "
        "Load's p_set time-series. Values are mapped to snapshots POSITIONALLY "
        "(row 0 → first snapshot, row 1 → second, etc.), so the spreadsheet "
        "order matters. Both FLAT and MULTI-PERIOD networks are supported. "
        "Row-count rules:\n"
        "  • Flat network: rows == len(n.snapshots) exactly\n"
        "  • Multi-period network: rows == total snapshots OR rows == "
        "timesteps-per-period (a 1-year operational profile auto-tiles "
        "across all N investment periods — the most common multi-period "
        "input format). Mixed or non-divisible counts are rejected.\n"
        "Response carries `auto_tiled: bool` + `tile_factor: int` so you "
        "can mention the tiling in your reply to the user. Errors: "
        "load_not_found / time_column_parse_error / value_column_parse_error "
        "/ snapshot_count_mismatch / snapshot_range_mismatch (flat only). "
        "`sheet_name` defaults to the first sheet — omit it unless the "
        "workbook has multiple sheets. Writes participate in the turn-"
        "level undo. Safety: destructive.",
        {
            "file_id": {"type": "string"},
            "sheet_name": {"type": "string"},
            "time_col": {"type": "string"},
            "value_col": {"type": "string"},
            "load_name": {"type": "string"},
            "replace": {"type": "boolean"},
        },
        ["file_id", "time_col", "value_col", "load_name"],
    ),
    _t(
        "delete_upload",
        "Remove one upload from the active project. Idempotent: returns "
        "{deleted:true, file_id} on success and {deleted:false, file_id, "
        "reason:'not_found'} when the file_id is already gone. Same "
        "shape for both user uploads and agent exports. Safety: destructive.",
        {"file_id": {"type": "string"}},
        ["file_id"],
    ),

    # ── Chatbot uploads — vision (1) ─────────────────────────────────────────

    _t(
        "reconstruct_network_from_image",
        "Read an uploaded image of a network topology and use the multimodal "
        "vision model to identify buses + lines + their coordinates, then "
        "materialise them via `create_component`. The image must be a "
        "PNG/JPEG/WebP upload in the active project. Coordinate transform: "
        "`gx = (px - origin_x) * scale_x`, `gy = (origin_y - py) * scale_y` "
        "(Y-flip — image origin is top-left, canvas origin is bottom-left). "
        "All identity-transform defaults are sane for schematic diagrams; "
        "override `origin_x`, `origin_y`, `scale_x`, `scale_y` when anchoring "
        "to existing canvas geometry. Sub-call has a 30 s timeout — returns "
        "`image_analysis_timeout` on stall, `vision_invalid_json` if the "
        "model emits prose instead of JSON, `vision_call_failed` for SDK "
        "errors. Skips buses whose names already exist; skips lines that "
        "reference unknown buses. Returns a summary "
        "`{ok, buses_created, lines_created, buses_reported, lines_reported, "
        "buses_skipped, lines_skipped}`. Safety: destructive.",
        {
            "file_id": {"type": "string"},
            "origin_x": {"type": "number"},
            "origin_y": {"type": "number"},
            "scale_x": {"type": "number"},
            "scale_y": {"type": "number"},
        },
        ["file_id"],
    ),

    # ── Chatbot uploads — produce / agent exports (4) ──────────────────────

    _t(
        "export_to_excel",
        "Write a multi-sheet xlsx workbook from `sheets`: a dict where "
        "each key is a sheet name and each value is a list of rows (each "
        "row a list of cell values; the first row is treated as headers). "
        "Filename is sanitised; 25 MB cap on the serialised workbook. The "
        "file lands in the project's uploads/ as kind='agent_export' so "
        "the chat panel shows a download chip. Safety: write.",
        {
            "sheets": {"type": "object"},
            "filename": {"type": "string"},
        },
        ["sheets", "filename"],
    ),
    _t(
        "export_to_csv",
        "Write a single rectangular CSV. `columns` is the header list and "
        "`rows` is a list of value-lists (each must match columns length). "
        "RFC 4180 CRLF line endings. 25 MB cap on serialised output. "
        "Filename sanitised. Safety: write.",
        {
            "columns": {"type": "array"},
            "rows": {"type": "array"},
            "filename": {"type": "string"},
        },
        ["columns", "rows", "filename"],
    ),
    _t(
        "export_preview_png",
        "Write a PNG image generated by the agent (e.g. a topology preview "
        "diagram). `png_bytes_b64` must be base64-encoded PNG bytes — the "
        "first bytes are validated against PNG magic (89 50 4e 47) before "
        "writing. 25 MB cap; filename sanitised. Safety: write.",
        {
            "filename": {"type": "string"},
            "png_bytes_b64": {"type": "string"},
        },
        ["filename", "png_bytes_b64"],
    ),
    _empty(
        "clear_uploads",
        "Delete every upload (user-uploaded AND agent-exported) for the "
        "active project. Locked decision row 7 — uploads are INDEPENDENT "
        "of chat history: `clear_chat_history` does NOT touch uploads, and "
        "this tool does NOT touch chat.jsonl. Returns "
        "`{cleared: int, files: list[str]}`. Safety: destructive.",
    ),
    _t(
        "export_chat_summary",
        "Render the active project's chat history as a downloadable "
        "summary file in the uploads/ dir. `format` is 'md' or 'txt'; "
        "`since_turn` (optional) drops turns before that index. Default "
        "filename is chat_summary_<ts>.<ext> but can be overridden. The "
        "exported file appears in the chat panel's file strip with a "
        "download button. Safety: write.",
        {
            "format": {"type": "string", "enum": ["md", "txt"]},
            "since_turn": {"type": "integer"},
            "filename": {"type": "string"},
        },
        [],
    ),

    # ── Asset results (3) — Task 14 ─────────────────────────────────────────
    _t(
        "get_asset_results",
        "Per-asset results for ONE component (e.g. Generator 'Gas 1'). "
        "Returns {asset, category, categories[{id,status,reason}], scalars, "
        "unavailable[{id,label,status,reason}], n_snapshots} plus, by default "
        "(resolution='stats'), series_stats[metric] = {min,max,mean,sum,p50,"
        "p95,peak_at,zero_count,sparkline} — a <=48-point downsample, NOT the "
        "full series. Use this default for questions about totals, peaks, "
        "timing and shape. resolution='raw' returns real arrays truncated to "
        "max_rows (default 2000) with truncated + n_total set; an hourly year "
        "is 8760 rows, so prefer export_asset_results when the user wants the "
        "complete data rather than an answer. Metrics that do not apply come "
        "back under `unavailable` with the reason, never as an error. "
        "Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "category": {"type": "string", "enum": ASSET_CATEGORY_ENUM},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string", "enum": RESULTS_SOURCE_ENUM},
            "from_iso": {"type": "string"},
            "to_iso": {"type": "string"},
            "period": {"type": "string"},
            "resolution": {"type": "string", "enum": ASSET_RESOLUTION_ENUM},
            "max_rows": {"type": "integer"},
        },
        ["component_class", "name"],
    ),
    _t(
        "ui_open_asset_detail",
        "Open the Results > Asset Detail tab on one asset, optionally with a "
        "category, a metric selection, a view mode and the chart toggle "
        "pre-set. Emits a ui_event (kind=open_asset_detail); NO backend "
        "mutation. Omitted arguments leave the panel's current state alone. "
        "Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "category": {"type": "string", "enum": ASSET_CATEGORY_ENUM},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ASSET_VIEW_MODE_ENUM},
            "chart": {"type": "boolean"},
        },
        ["component_class", "name"],
    ),
    _t(
        "export_asset_results",
        "Write one asset's results to an xlsx workbook in the project's "
        "uploads/ (kind='agent_export'), so the chat panel shows a download "
        "chip. scope='view' exports the named category and metrics; "
        "scope='full' exports every applicable category with every available "
        "metric. The workbook always opens with an About sheet carrying the "
        "project, solve time, objective, result source, horizon, period and "
        "view mode. Returns {filename, bytes, kind}. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "scope": {"type": "string", "enum": ["view", "full"]},
            "category": {"type": "string", "enum": ASSET_CATEGORY_ENUM},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "filename": {"type": "string"},
            "source": {"type": "string", "enum": RESULTS_SOURCE_ENUM},
            "mode": {"type": "string", "enum": ASSET_VIEW_MODE_ENUM},
        },
        ["component_class", "name"],
    ),
]


# v4-NIT-1 / v6-F4: derive the count from the registry length at module
# import. NEVER pre-state the count anywhere in code or docs.
TOOL_COUNT: int = len(TOOLS)


# ─────────────────────────────────────────────────────────────────────────
# Endpoint-coverage side table — drives test_chat_tools_endpoint_map.py.
#
# Each entry maps a tool name to one of:
#   * list[tuple[METHOD, PATH_TEMPLATE]] — concrete HTTP route(s) the
#     dispatcher will call. PATH_TEMPLATE matches FastAPI's OpenAPI form
#     (e.g. "/api/network/buses/{name}").
#   * ["_service_call_"] — no HTTP route; dispatcher calls a service helper
#     directly (dispatch_status, get_component direct df.loc lookup).
#   * ["_derived_"] — composed from other tools (list_scenarios = list_projects
#     + filter).
#   * ["_ui_event_"] — emits a typed SSE frame the ChatPanel forwards to
#     uiStore; no backend mutation.
#   * ["_chat_jsonl_"] — operates on the per-project chat.jsonl directly
#     (list_chat_history / clear_chat_history).
#
# Phase 1 QA gate A asserts: every TOOLS[i]["name"] appears here, and every
# HTTP entry matches a row in route_inventory_phase0.txt.
# ─────────────────────────────────────────────────────────────────────────

_SERVICE_CALL = ["_service_call_"]
_DERIVED = ["_derived_"]
_UI_EVENT = ["_ui_event_"]
_CHAT_JSONL = ["_chat_jsonl_"]

# Component-class endpoint sets — reused by list_components / create_component
# / update_component / delete_component multi-route tools.
_COMP_LIST_ROUTES = [
    ("GET", f"/api/network/{plural}")
    for plural in (
        "buses", "carriers", "lines", "links", "transformers", "generators",
        "storage_units", "stores", "loads", "shunt_impedances",
        "global_constraints",
    )
]
_COMP_CREATE_ROUTES = [
    ("POST", f"/api/network/{plural}")
    for plural in (
        "buses", "carriers", "lines", "links", "transformers", "generators",
        "storage_units", "stores", "loads", "shunt_impedances",
        "global_constraints",
    )
]
_COMP_UPDATE_ROUTES = [
    ("PUT", "/api/network/buses/{name}"),  # update_bus (non-rename)
    ("POST", "/api/network/buses/{name}/rename"),  # F1: Bus rename
    ("PUT", "/api/network/carriers/{name}"),
    ("PUT", "/api/network/lines/{name}"),
    ("PUT", "/api/network/links/{name}"),
    ("PUT", "/api/network/transformers/{name}"),  # F2: voltage validation
    ("PUT", "/api/network/generators/{name}"),
    ("PUT", "/api/network/storage_units/{name}"),
    ("PUT", "/api/network/stores/{name}"),
    ("PUT", "/api/network/loads/{name}"),
    ("PUT", "/api/network/shunt_impedances/{name}"),
    ("PUT", "/api/network/global_constraints/{name}"),  # F3: dedicated CRUD
]
_COMP_DELETE_ROUTES = [
    ("DELETE", f"/api/network/{plural}/{{name}}")
    for plural in (
        "buses", "carriers", "lines", "links", "transformers", "generators",
        "storage_units", "stores", "loads", "shunt_impedances",
        "global_constraints",
    )
]


TOOL_ROUTES: dict[str, list] = {
    # read (22)
    "list_components": _COMP_LIST_ROUTES,
    "get_component": _SERVICE_CALL,
    # #15: derived entirely from the in-memory graph — there is no HTTP
    # endpoint to mirror, same as dispatch_status.
    "diagnose_network": _SERVICE_CALL,
    "get_meta": [("GET", "/api/network/meta")],
    "list_snapshots": [("GET", "/api/network/snapshots")],
    "list_carriers": [("GET", "/api/network/carriers")],
    "list_global_constraints": [("GET", "/api/network/global_constraints")],
    "list_timeseries_profiles": [
        ("GET", "/api/network/loads/profiles"),
        ("GET", "/api/network/generators/profiles"),
        ("GET", "/api/network/links/profiles"),
    ],
    "list_transformer_types": [("GET", "/api/network/transformers/types")],
    "download_timeseries_template": [
        ("GET", "/api/network/loads/template"),
        ("GET", "/api/network/generators/template"),
        ("GET", "/api/network/links/template"),
    ],
    "download_snapshot_weightings_csv": [("GET", "/api/network/snapshots/weightings.csv")],
    "list_investment_periods": [("GET", "/api/network/investment_periods")],
    "list_vintage_bounds": [("GET", "/api/network/vintage_bounds")],
    "get_vintage_results": [("GET", "/api/network/vintage_results")],
    "get_timeseries": [("GET", "/api/network/timeseries/{component}/{attribute}")],
    "list_all_timeseries": [("GET", "/api/network/timeseries")],
    "get_solver_config": [("GET", "/api/simulation/solver_config")],
    "get_solver_capabilities": [("GET", "/api/simulation/capabilities")],
    "get_asset_costs": [("GET", "/api/simulation/asset_costs")],
    "get_simulation_status": [("GET", "/api/simulation/status")],
    "get_simulation_lock_status": [("GET", "/api/simulation/lock_status")],
    "get_simulation_log_history": [("GET", "/api/simulation/log_history")],
    "get_results": [
        # 27 of 28 enums map 1:1 to /api/results/{kind}; ac_pf_status is the
        # one outlier (v4-MAJOR-4 lookup-dict gap).
        ("GET", f"/api/results/{k}") for k in RESULTS_ENUM if k != "ac_pf_status"
    ] + [("GET", "/api/results/ac_pf/status")],
    "get_aggregate_load": [("GET", "/api/network/loads/aggregate")],
    # write_generic_crud (4)
    "create_component": _COMP_CREATE_ROUTES,
    "update_component": _COMP_UPDATE_ROUTES,
    "delete_component": _COMP_DELETE_ROUTES,
    "cascade_delete_bus": [("DELETE", "/api/network/buses/{name}/cascade")],
    # write_bulk (1)
    "bulk_update_components": [("PATCH", "/api/network/_bulk")],
    # #17: loops the per-class handlers so their dedicated logic and the
    # _user_ts / vintage cleanup keep running; no single endpoint mirrors it.
    "batch_create_components": _SERVICE_CALL,
    "batch_delete_components": _SERVICE_CALL,
    # write_carriers (1)
    "create_carrier": [("POST", "/api/network/carriers")],
    # write_meta (1)
    "update_meta": [("PUT", "/api/network/meta")],
    # write_topology (2)
    "cluster_network": [("POST", "/api/network/cluster")],
    "recalculate_line_lengths": [("POST", "/api/network/lines/recalculate_lengths")],
    # write_snapshots (4)
    "set_snapshots": [("POST", "/api/network/snapshots")],
    "set_snapshot_weightings": [("PATCH", "/api/network/snapshots/weightings")],
    "upload_snapshot_weightings_csv": [("POST", "/api/network/snapshots/weightings.csv")],
    "sample_representative_weeks": [("POST", "/api/network/snapshots/sample_weeks")],
    # write_periods (3)
    "set_multi_period_snapshots": [("POST", "/api/network/snapshots/multi_period")],
    "set_investment_periods": [("POST", "/api/network/investment_periods")],
    "set_investment_period_weightings": [("PATCH", "/api/network/investment_period_weightings")],
    # write_vintage (3)
    "set_vintage_bounds": [("PUT", "/api/network/vintage_bounds/{component_class}/{name}")],
    "delete_vintage_bounds": [("DELETE", "/api/network/vintage_bounds/{component_class}/{name}")],
    "cleanup_orphan_vintages": [("POST", "/api/network/vintage_bounds/_cleanup_orphans")],
    # write_timeseries (6)
    "upload_timeseries": [("PUT", "/api/network/timeseries/{component}/{attribute}")],
    "generate_exemplary_timeseries": _SERVICE_CALL,
    "delete_timeseries": [("DELETE", "/api/network/timeseries")],
    "upload_load_profile": [("POST", "/api/network/loads/upload_profile")],
    "upload_generator_profile": [("POST", "/api/network/generators/upload_profile")],
    "upload_link_profile": [("POST", "/api/network/links/upload_profile")],
    # write_solver (1)
    "update_solver_config": [("PUT", "/api/simulation/solver_config")],
    # validation (3)
    "validate_network": [("POST", "/api/simulation/preflight")],
    "check_solver_availability": [("GET", "/api/simulation/check_solvers")],
    "dispatch_status": _SERVICE_CALL,  # B3: NO HTTP endpoint exists
    # execution_long_running (2)
    "run_simulation": [("POST", "/api/simulation/run")],
    "run_ac_pf_stage": [("POST", "/api/simulation/run_ac_pf")],
    # execution (2)
    "abort_simulation": [("POST", "/api/simulation/abort")],
    "force_reset_simulation": [("POST", "/api/simulation/force_reset")],
    # solve_queue (4)
    "solve_queue_enqueue": [("POST", "/api/simulation/queue")],
    "solve_queue_list": [("GET", "/api/simulation/queue")],
    "solve_queue_abort": [("POST", "/api/simulation/queue/{job_id}/abort")],
    "solve_queue_clear_finished": [("POST", "/api/simulation/queue/clear_finished")],
    # project_mgmt (21)
    "list_projects": [("GET", "/api/projects/")],
    "load_project": [("GET", "/api/projects/{name}")],
    "activate_project": [("POST", "/api/projects/{project_id}/activate")],
    "save_project": [("POST", "/api/projects/{name}")],
    "save_project_as": [("POST", "/api/projects/{name}")],   # rebind=true via query
    "save_project_a_copy": [("POST", "/api/projects/{name}")],  # rebind=false
    "rename_project": [("POST", "/api/projects/{name}/rename")],
    "delete_project": [("DELETE", "/api/projects/{name}")],
    "create_scenario": [("POST", "/api/projects/{base}/scenarios")],
    "list_scenarios": _DERIVED,  # B2: filters list_projects
    "get_project_results_bundle": [("GET", "/api/projects/{name}/results_bundle")],
    "get_project_layout": [("GET", "/api/projects/{name}/layout")],
    "update_project_layout": [("PUT", "/api/projects/{name}/layout")],
    "download_project_bundle": [("GET", "/api/projects/{name}/bundle")],
    "get_project_statistics": [("GET", "/api/projects/{name}/statistics")],
    "get_project_network_meta": [("GET", "/api/projects/{project_id}/network/meta")],
    "list_project_network_component": [("GET", "/api/projects/{project_id}/network/{component_class}")],
    "import_project_bundle": [("POST", "/api/projects/import_bundle")],
    "create_project_from_template": [("POST", "/api/projects/from_template/{template_id}")],
    "get_project_compare_state": [("GET", "/api/projects/{name}/compare-state")],
    "get_project_results_summary": [("GET", "/api/projects/{name}/results-summary")],
    "compare_scenarios": _DERIVED,  # dual results-summary + optional ui_event
    # project_snapshots (4)
    "create_project_snapshot": [("POST", "/api/projects/{name}/snapshots")],
    "list_project_snapshots": [("GET", "/api/projects/{name}/snapshots")],
    "restore_project_snapshot": [("POST", "/api/projects/{name}/snapshots/{snapshot_id}/restore")],
    "delete_project_snapshot": [("DELETE", "/api/projects/{name}/snapshots/{snapshot_id}")],
    # import_export (8)
    "import_network_nc": [("POST", "/api/io/import/netcdf")],
    "import_csv_bundle": [("POST", "/api/io/import/csv")],
    "import_excel": [("POST", "/api/io/import/excel")],
    "import_matpower": [("POST", "/api/io/import/matpower")],
    "export_network_nc": [("GET", "/api/io/export/netcdf")],
    "export_csv_bundle": [("GET", "/api/io/export/csv")],
    "export_excel": [("GET", "/api/io/export/excel")],
    "export_matpower": [("GET", "/api/io/export/matpower")],
    # audit_undo (4)
    "audit_log": [("GET", "/api/changelog/")],
    "clear_audit_log": [("DELETE", "/api/changelog/")],
    "undo_last": [("POST", "/api/network/undo")],
    "undo_status": [("GET", "/api/network/undo/info")],
    # ui_control (3)
    "ui_select_component": _UI_EVENT,
    "ui_open_panel": _UI_EVENT,
    "ui_set_snapshot": _UI_EVENT,
    # conversation (2)
    "list_chat_history": _CHAT_JSONL,
    "clear_chat_history": _CHAT_JSONL,
    # uploads — consume (5)
    "list_uploads": [("GET", "/api/projects/{name}/uploads")],
    "read_upload_meta": [("GET", "/api/projects/{name}/uploads/{file_id}/meta")],
    "read_excel_sheet": _SERVICE_CALL,
    "apply_demand_from_excel": _SERVICE_CALL,
    "delete_upload": [("DELETE", "/api/projects/{name}/uploads/{file_id}")],
    # uploads — vision (Phase C dependency stub)
    "reconstruct_network_from_image": _SERVICE_CALL,
    # uploads — produce / agent exports (4)
    "export_to_excel": _SERVICE_CALL,
    "export_to_csv": _SERVICE_CALL,
    "export_preview_png": _SERVICE_CALL,
    "export_chat_summary": _SERVICE_CALL,
    "clear_uploads": _SERVICE_CALL,
    # asset_results (3) — Task 14. Real HTTP routes DO exist
    # (routers/asset_results.py, mounted at /api/results/asset in main.py)
    # but the dispatchers below call services.asset_results.{service,export}
    # directly, not through those routes — same "_service_call_" pattern as
    # read_excel_sheet/export_to_excel above. They're also absent from
    # route_inventory_phase0.txt (that fixture predates this feature), so
    # mapping them to the real paths would fail
    # test_every_http_route_resolves_in_inventory until the fixture is
    # regenerated — out of scope for this task.
    "get_asset_results": _SERVICE_CALL,
    "ui_open_asset_detail": _UI_EVENT,
    "export_asset_results": _SERVICE_CALL,
}


# Sentinels that mean "no HTTP route to verify against openapi.json".
NON_HTTP_SENTINELS = frozenset(
    ["_service_call_", "_derived_", "_ui_event_", "_chat_jsonl_"]
)
