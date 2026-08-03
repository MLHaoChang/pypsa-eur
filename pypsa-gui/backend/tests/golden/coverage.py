"""
Which economic surface reports which component class.

Doubles as the honest answer to "what does this app actually report?" — a
question that currently cannot be answered without reading ten endpoints.

Every COVERAGE entry below was checked against the actual endpoint code in
routers/results.py, routers/simulation.py, routers/compare.py and
routers/asset_results.py (2026-08-01, re-verified 2026-08-03) — not assumed.
Two entries in the original draft of this table turned out to be wrong; see
the EXCLUSIONS entries for economics_by_carrier x Line and compare_economics
x Line for the evidence.

Citations below name FUNCTIONS, not line numbers. This is a shared worktree
(CLAUDE.md: "more than one agent session works in this ONE worktree") —
line numbers rot the moment a concurrent session touches the same file, and
a stale line number silently points at the wrong code instead of failing
loudly. A function name survives a reflow; `grep -n 'def <name>'` finds the
current line in one step when you need it.
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
    "compare_capacity",
)

FIXTURE_CLASSES = frozenset({"Generator", "Line", "Link", "StorageUnit"})

COVERAGE: dict[str, set[str]] = {
    # routers/results.py::get_asset_economics(). Docstring says
    # "Generator / StorageUnit / Store"; a dedicated Link block was added
    # 2026-07-31 ("Link block (converters: electrolysers, heat pumps,
    # P2X)"). No Line handling anywhere in the function (grep for
    # "lines_t"/"n.lines" in the function body: no hits).
    "asset_economics":      {"Generator", "StorageUnit", "Link"},
    # routers/results.py::get_cost_breakdown(). Pivots PyPSA's raw
    # n.statistics() output (class-agnostic) plus a per-class NOM_PAIRS walk
    # that explicitly includes ("lines", "Line", "s_nom").
    "cost_breakdown":       {"Generator", "StorageUnit", "Link", "Line"},
    # routers/results.py::get_economics_by_carrier() delegates directly to
    # routers/compare.py::_compute_economics_summary(). See compare_economics
    # below — that function never touches Line.
    "economics_by_carrier": {"Generator", "StorageUnit", "Link"},
    # routers/results.py::get_statistics(). Raw pass-through of
    # n.statistics() (df_to_json(stats), no per-class filtering) — reports
    # every component class PyPSA's own statistics engine reports.
    "statistics":           {"Generator", "StorageUnit", "Link", "Line"},
    # routers/results.py::get_lcoh(). Iterates n.links filtered to
    # electrolyser-like carriers only.
    "lcoh":                 {"Link"},
    # routers/simulation.py::asset_costs() -> services/solver_service.py::
    # periodized_capital_costs(), which walks a fixed component tuple
    # including ("lines", "Line").
    "asset_costs":          {"Generator", "StorageUnit", "Link", "Line"},
    # routers/asset_results.py::get_asset_results(). Generic per
    # component_class, gated on ALL_CLASSES (services/asset_results/
    # registry.py), which includes Bus/Generator/Load/Line/Transformer/
    # Link/StorageUnit/Store.
    "asset_results":        {"Generator", "StorageUnit", "Link", "Line"},
    # routers/asset_results.py::export_asset_results_xlsx(). Same
    # ALL_CLASSES gate and same compute path as asset_results, just
    # rendered to a workbook instead of JSON.
    "asset_results_xlsx":   {"Generator", "StorageUnit", "Link", "Line"},
    # routers/compare.py::get_results_summary()'s `economics` field is built
    # by _compute_economics_summary(). Its CAPEX walk
    # (_walk_capex_vintage / _walk_capex_plain closures) and its
    # dispatch/OPEX/revenue walk (_walk_dispatch_side closure) are each
    # called once per class — Generator, StorageUnit, Store, Link — and
    # never for Line. Confirmed by grep: no "n.lines" / '"Line"' reference
    # anywhere in the function body.
    "compare_economics":    {"Generator", "StorageUnit", "Link"},
    # routers/compare.py::get_results_summary()'s `capacity` field is built
    # by _compute_capacity_summary(), which walks Generator, StorageUnit,
    # Store and Link (via _walk_vintages / _walk_plain, called once per
    # class) into `capacity_mw_by_carrier` / `new_capacity_mw_by_carrier` /
    # `new_capex_meur_by_carrier`. Line is never walked by that function —
    # same omission as compare_economics, see EXCLUSIONS. The COMPANION
    # `capex_meur_by_carrier` field (total annuitised CAPEX, from
    # _compute_total_annuitised_capex()) is narrower still: it walks only
    # Generator/StorageUnit/Store — Link is DELIBERATELY excluded there too
    # (see that function's own docstring: "Lines / links / transformers are
    # deliberately omitted"). COVERAGE is per-surface, not per-field, so
    # Link is listed here because `new_capex_meur_by_carrier` reports it
    # even though `capex_meur_by_carrier` does not — see
    # test_golden_economics.py::test_compare_capacity_agrees_with_asset_economics
    # for the field-level detail.
    "compare_capacity":     {"Generator", "StorageUnit", "Link"},
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
        "economics_by_carrier (routers/results.py::get_economics_by_carrier) "
        "delegates straight to routers/compare.py::_compute_economics_summary. "
        "That function's per-class walks (the _walk_capex_vintage / "
        "_walk_capex_plain closures for CAPEX, _walk_dispatch_side for "
        "dispatch/OPEX/revenue) are called once each for Generator, "
        "StorageUnit, Store and Link — Line is never passed in, so a line's "
        "cost never reaches a carrier bucket. Line CAPEX is reported by "
        "cost_breakdown and asset_costs instead."
    ),
    # Same correction as above: the original brief listed compare_economics
    # as covering Line, but it shares the exact same underlying function.
    ("compare_economics", "Line"): (
        "compare_economics's `economics` field "
        "(routers/compare.py::get_results_summary) is computed by the same "
        "_compute_economics_summary function that economics_by_carrier "
        "calls, whose CAPEX and dispatch/OPEX/revenue walks only cover "
        "Generator, StorageUnit, Store and Link — Line is never accumulated "
        "into a carrier bucket. Line-level detail is visible via the "
        "Compare Capacity tab and cost_breakdown instead."
    ),
    # compare_capacity's `capacity` field (routers/compare.py::
    # get_results_summary, built by _compute_capacity_summary) walks
    # Generator, StorageUnit, Store and Link via _walk_vintages / _walk_plain
    # — Line is never one of the classes passed to either closure, so a
    # line's capacity/CAPEX never reaches a carrier bucket here either. Same
    # omission as compare_economics / economics_by_carrier (all three
    # ultimately share the "no Line" boundary of this comparison view's
    # per-carrier walks). Line capacity/CAPEX is reported by cost_breakdown,
    # statistics and asset_costs instead.
    ("compare_capacity", "Line"): (
        "_compute_capacity_summary (routers/compare.py) walks Generator, "
        "StorageUnit, Store and Link via _walk_vintages / _walk_plain — Line "
        "is never one of the classes passed to either closure, so a line's "
        "capacity/CAPEX never reaches a carrier bucket in this surface. "
        "Line capacity/CAPEX is reported by cost_breakdown, statistics and "
        "asset_costs instead."
    ),
}
