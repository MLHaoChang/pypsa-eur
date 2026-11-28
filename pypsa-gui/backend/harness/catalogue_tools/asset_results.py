"""
Asset results: get_asset_results, ui_open_asset_detail and export_asset_results: schemas and routes.

Moved out of `harness/catalogue.py` (RETRO-FRAG-1); spliced back at its old
place, so it declares no ORDER.
Dispatchers: `services/chat_dispatchers/asset_results.py`.

A schema fragment imports only `harness.catalogue_base`, so the enums below
are copies of catalogue's; `tests/test_tool_fragments.py` pins each.
"""

from __future__ import annotations

from harness.catalogue_base import _SERVICE_CALL, _UI_EVENT, _t

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
RESULTS_SOURCE_ENUM = ["lopf", "ac_pf"]
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

TOOLS = [
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

ROUTES = {
    "get_asset_results": _SERVICE_CALL,
    "ui_open_asset_detail": _UI_EVENT,
    "export_asset_results": _SERVICE_CALL,
}

# `_SERVICE_CALL` writes that do NOT touch the live network (moved from
# catalogue's `_SERVICE_CALL_NON_NETWORK_WRITES`): the export file under uploads/.
NON_NETWORK_WRITES = frozenset({"export_asset_results"})
