"""
Which economic surface reports which component class.

Doubles as the honest answer to "what does this app actually report?" — a
question that currently cannot be answered without reading nine endpoints.

Every COVERAGE entry below was checked against the actual endpoint code in
routers/results.py, routers/simulation.py, routers/compare.py and
routers/asset_results.py (2026-08-01) — not assumed. Two entries in the
original draft of this table turned out to be wrong; see the EXCLUSIONS
entries for economics_by_carrier x Line and compare_economics x Line for
the file:line evidence.
"""
from __future__ import annotations

SURFACES = (
    "asset_economics",
    "cost_breakdown",
    "economics_by_carrier",
    "statistics",
    "lcoh",
    "asset_costs",
    "asset_results",
    "asset_results_xlsx",
    "compare_economics",
)

FIXTURE_CLASSES = frozenset({"Generator", "Line", "Link", "StorageUnit"})

COVERAGE: dict[str, set[str]] = {
    # routers/results.py:3056 get_asset_economics(). Docstring says
    # "Generator / StorageUnit / Store"; a dedicated Link block was added
    # 2026-07-31 (routers/results.py:3736-3920, "Link block (converters:
    # electrolysers, heat pumps, P2X)"). No Line handling anywhere in the
    # function (grep for "lines_t"/"n.lines" in the function body: no hits).
    "asset_economics":      {"Generator", "StorageUnit", "Link"},
    # routers/results.py:159 get_cost_breakdown(). Pivots PyPSA's raw
    # n.statistics() output (class-agnostic) plus a per-class NOM_PAIRS walk
    # that explicitly includes ("lines", "Line", "s_nom") at line 204.
    "cost_breakdown":       {"Generator", "StorageUnit", "Link", "Line"},
    # routers/results.py:771 get_economics_by_carrier() delegates directly to
    # routers/compare.py:_compute_economics_summary (results.py:791, 798).
    # See compare_economics below — that function never touches Line.
    "economics_by_carrier": {"Generator", "StorageUnit", "Link"},
    # routers/results.py:809 get_statistics(). Raw pass-through of
    # n.statistics() (df_to_json(stats), no per-class filtering) — reports
    # every component class PyPSA's own statistics engine reports.
    "statistics":           {"Generator", "StorageUnit", "Link", "Line"},
    # routers/results.py:896 get_lcoh(). Iterates n.links filtered to
    # electrolyser-like carriers only (routers/results.py:939-949).
    "lcoh":                 {"Link"},
    # routers/simulation.py:337 asset_costs() -> services/solver_service.py:
    # periodized_capital_costs(), which walks a fixed component tuple
    # including ("lines", "Line") at solver_service.py:3592.
    "asset_costs":          {"Generator", "StorageUnit", "Link", "Line"},
    # routers/asset_results.py:75 get_asset_results(). Generic per
    # component_class, gated on ALL_CLASSES (services/asset_results/
    # registry.py:35-38), which includes Bus/Generator/Load/Line/
    # Transformer/Link/StorageUnit/Store.
    "asset_results":        {"Generator", "StorageUnit", "Link", "Line"},
    # routers/asset_results.py:23 export_asset_results_xlsx(). Same
    # ALL_CLASSES gate and same compute path as asset_results, just
    # rendered to a workbook instead of JSON.
    "asset_results_xlsx":   {"Generator", "StorageUnit", "Link", "Line"},
    # routers/compare.py:2601 get_results_summary()'s `economics` field is
    # built by _compute_economics_summary (routers/compare.py:1469, called
    # at line 2652). Its CAPEX walk (lines 1775-1782: _walk_capex_vintage /
    # _walk_capex_plain) and its dispatch/OPEX/revenue walk (lines
    # 1792-1794: _walk_dispatch_side) are each called once per class —
    # Generator, StorageUnit, Store, Link — and never for Line. Confirmed by
    # grep: no "n.lines" / '"Line"' reference anywhere in the function body
    # (lines 1469-2600).
    "compare_economics":    {"Generator", "StorageUnit", "Link"},
}

EXCLUSIONS: dict[tuple[str, str], str] = {
    ("asset_economics", "Line"): (
        "Lines carry no dispatchable energy of their own, so per-asset "
        "revenue and unit cost are undefined for them. Line CAPEX is "
        "reported by cost_breakdown and the Capacity Expansion tab instead."
    ),
    ("lcoh", "Generator"): (
        "LCOH is a per-electrolyser metric: it levelises the cost of hydrogen "
        "OUTPUT. A generator produces no hydrogen, so the ratio has no "
        "denominator."
    ),
    ("lcoh", "Line"): (
        "LCOH levelises hydrogen output. A line transports electricity and "
        "produces no hydrogen, so the metric does not apply."
    ),
    ("lcoh", "StorageUnit"): (
        "LCOH levelises hydrogen output. A battery stores electricity and "
        "produces no hydrogen; hydrogen STORES are a separate case and are "
        "not in the golden fixture."
    ),
    # Correction to the original brief: the brief's draft COVERAGE table
    # listed economics_by_carrier as covering Line. Verified against the
    # actual code and it does not.
    ("economics_by_carrier", "Line"): (
        "economics_by_carrier (routers/results.py:771) delegates straight to "
        "_compute_economics_summary (routers/compare.py:1469). That function's "
        "per-class walks (routers/compare.py:1775-1782 for CAPEX, 1792-1794 "
        "for dispatch/OPEX/revenue) are called once each for Generator, "
        "StorageUnit, Store and Link — Line is never passed in, so a line's "
        "cost never reaches a carrier bucket. Line CAPEX is reported by "
        "cost_breakdown and asset_costs instead."
    ),
    # Same correction as above: the original brief listed compare_economics
    # as covering Line, but it shares the exact same underlying function.
    ("compare_economics", "Line"): (
        "compare_economics's `economics` field (routers/compare.py:2652) is "
        "computed by the same _compute_economics_summary function that "
        "economics_by_carrier calls, whose CAPEX and dispatch/OPEX/revenue "
        "walks only cover Generator, StorageUnit, Store and Link (see "
        "routers/compare.py:1775-1794) — Line is never accumulated into a "
        "carrier bucket. Line-level detail is visible via the Compare "
        "Capacity tab and cost_breakdown instead."
    ),
}
