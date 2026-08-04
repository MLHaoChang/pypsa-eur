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
    # ── Task 20: the remaining eight Compare-Scenarios tabs. compare_capacity
    # and compare_economics (above) were already covered; these are the rest
    # of the ten tabs routers/compare.py serves (GET .../compare-state for
    # compare_overview, GET .../results-summary for the other seven — see
    # test_golden_coverage.py::ROUTE_SURFACES). None of these seven report
    # CAPEX/fixed-cost at all, so none can carry an ADAPTERS entry in
    # test_golden_economics.py — see NO_ADAPTER_REASONS there for why each is
    # a documented gap rather than a silent one.
    "compare_overview",
    "compare_dispatch",
    "compare_loading",
    "compare_prices",
    "compare_emissions",
    "compare_curtailment",
    "compare_lost_load",
    "compare_storage_cycling",
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
    # ── Task 20 additions ───────────────────────────────────────────────
    # routers/compare.py::get_compare_state(). `installed_capacity_by_carrier`
    # / `optimised_capacity_by_carrier` come from `_carrier_sum(temp_n.
    # generators, ...)`; `storage_capacity_by_carrier` from `_carrier_sum(
    # temp_n.storage_units, "p_nom")`. `line_count` / `link_count` are bare
    # `len()` calls with no carrier attribution -- Line and Link are counted,
    # never reported per-carrier, so they don't count as "covered" here (see
    # EXCLUSIONS).
    "compare_overview":        {"Generator", "StorageUnit"},
    # routers/compare.py::_compute_dispatch_summary(). Walks
    # `n.generators_t.p` (energy-mix GWh + OPEX) and `n.storage_units_t.p`
    # (discharge-only dispatch + fleet cycling). No reference to `n.lines` /
    # `n.links` anywhere in the function body.
    "compare_dispatch":        {"Generator", "StorageUnit"},
    # routers/compare.py::_compute_loading_summary(). `_walk_branches` is
    # called for n.lines, n.transformers (not a FIXTURE_CLASS) and n.links --
    # a branch-loading metric (|p0| / nominal rating), so Generator and
    # StorageUnit never appear.
    "compare_loading":         {"Line", "Link"},
    # routers/compare.py::_compute_prices_summary(). Purely bus-level:
    # marginal prices come from `n.buses_t.marginal_price` (via
    # `corrected_marginal_prices`) and are grouped by `n.buses.carrier`. None
    # of Generator/Line/Link/StorageUnit is ever referenced -- see
    # EXCLUSIONS for all four.
    "compare_prices":          set(),
    # routers/compare.py::_compute_emissions_summary(). Walks
    # `n.generators_t.p` x `carrier.co2_emissions` only -- emissions are a
    # generator-dispatch quantity in this model.
    "compare_emissions":       {"Generator"},
    # routers/compare.py::_compute_curtailment_summary(). Walks
    # `n.generators_t.p` / `n.generators_t.p_max_pu` only (renewables with a
    # profile); no other component class contributes.
    "compare_curtailment":     {"Generator"},
    # routers/compare.py::_compute_lost_load_summary(). Reads the VOLL slack
    # DataFrame (bus x snapshot) out of `results_state.pkl` and groups by
    # `n.buses.carrier` -- no component-class walk at all, same structural
    # shape as compare_prices.
    "compare_lost_load":       set(),
    # routers/compare.py::_compute_storage_cycling_summary(). Walks
    # `n.storage_units` / `n.storage_units_t.p` only.
    "compare_storage_cycling": {"StorageUnit"},
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
    # ── Task 20 additions ───────────────────────────────────────────────
    ("compare_overview", "Line"): (
        "get_compare_state (routers/compare.py) reports `line_count` as a "
        "bare `len(temp_n.lines)` with no carrier attribution at all — "
        "unlike generators/storage_units, lines never pass through "
        "`_carrier_sum`. Per-carrier line detail is reported by the "
        "Capacity Expansion tab's underlying cost_breakdown/statistics "
        "surfaces instead."
    ),
    ("compare_overview", "Link"): (
        "Same structural gap as Line above: `link_count` is a bare "
        "`len(temp_n.links)`, never passed through `_carrier_sum`. No "
        "per-carrier link figure exists on the Overview tab."
    ),
    ("compare_dispatch", "Line"): (
        "_compute_dispatch_summary (routers/compare.py) walks "
        "`n.generators_t.p` and `n.storage_units_t.p` only — a line carries "
        "no dispatch of its own (it transports energy between buses, it "
        "doesn't produce or consume it), so it has no place in an "
        "energy-mix-by-carrier view. Line flow/loading is reported by the "
        "Loading tab (compare_loading) instead."
    ),
    ("compare_dispatch", "Link"): (
        "Same reasoning as Line: _compute_dispatch_summary never reads "
        "`n.links` or `n.links_t` — a link's throughput is a branch flow, "
        "not a generator/storage energy-mix entry. Reported by the Loading "
        "tab (compare_loading) instead."
    ),
    ("compare_loading", "Generator"): (
        "_compute_loading_summary (routers/compare.py) only calls "
        "`_walk_branches` for n.lines / n.transformers / n.links — loading "
        "(|p0| / nominal rating) is a branch-only concept; a generator has "
        "no `s_nom`/`p_nom`-relative flow to report here. Generator "
        "dispatch is reported by the Dispatch tab instead."
    ),
    ("compare_loading", "StorageUnit"): (
        "Same reasoning as Generator: storage units are never passed to "
        "`_walk_branches` — they have no branch loading concept. Storage "
        "dispatch/cycling is reported by the Dispatch and Storage Cycling "
        "tabs instead."
    ),
    ("compare_prices", "Generator"): (
        "_compute_prices_summary (routers/compare.py) reads "
        "`n.buses_t.marginal_price` exclusively and groups by "
        "`n.buses.carrier` — marginal price is a BUS-level LP dual, not an "
        "asset attribute, so no generator name or carrier walk happens "
        "anywhere in the function body."
    ),
    ("compare_prices", "Line"): (
        "Same reasoning as Generator: this surface never reads `n.lines` — "
        "it is bus-price-only. A line's own congestion is reported by the "
        "Loading tab (binding_hours), which is the closest analogue for a "
        "branch."
    ),
    ("compare_prices", "Link"): (
        "Same reasoning as Generator: this surface never reads `n.links` — "
        "bus-price-only, grouped by `n.buses.carrier` alone."
    ),
    ("compare_prices", "StorageUnit"): (
        "Same reasoning as Generator: this surface never reads "
        "`n.storage_units` — bus-price-only, grouped by `n.buses.carrier` "
        "alone."
    ),
    ("compare_emissions", "Line"): (
        "_compute_emissions_summary (routers/compare.py) walks "
        "`n.generators_t.p` x `carrier.co2_emissions` exclusively — a line "
        "moves electricity, it doesn't burn a fuel with a CO2 intensity, so "
        "it never enters the per-carrier emissions accumulator."
    ),
    ("compare_emissions", "Link"): (
        "Same reasoning as Line: `_compute_emissions_summary` never reads "
        "`n.links` — only generator dispatch × carrier CO2 intensity is "
        "accumulated. A sector-coupling link (e.g. an electrolyser) has no "
        "direct CO2 factor of its own in this model; its INPUT generator's "
        "emissions are what's counted."
    ),
    ("compare_emissions", "StorageUnit"): (
        "Same reasoning as Line: storage discharge is not a primary-fuel "
        "combustion event and carries no `co2_emissions` factor, so "
        "`_compute_emissions_summary` never reads `n.storage_units`."
    ),
    ("compare_curtailment", "Line"): (
        "_compute_curtailment_summary (routers/compare.py) only walks "
        "generators with a time-varying `p_max_pu` profile (renewables) — "
        "curtailment is specifically 'available renewable output minus "
        "actual dispatch', a concept that doesn't apply to a line."
    ),
    ("compare_curtailment", "Link"): (
        "Same reasoning as Line: `_compute_curtailment_summary` never reads "
        "`n.links` — curtailment is a generator-availability concept."
    ),
    ("compare_curtailment", "StorageUnit"): (
        "Same reasoning as Line: a storage unit's dispatch is bounded by "
        "state of charge, not a `p_max_pu` renewable-availability profile, "
        "so it's structurally outside this function's per-generator loop."
    ),
    ("compare_lost_load", "Generator"): (
        "_compute_lost_load_summary (routers/compare.py) reads the VOLL "
        "slack DataFrame (bus x snapshot) from `results_state.pkl` and "
        "groups by `n.buses.carrier` — the slack generators PyPSA adds "
        "internally for VOLL are stripped right after capture (see the "
        "function's own docstring), so no generator name ever survives "
        "into this payload."
    ),
    ("compare_lost_load", "Line"): (
        "Same reasoning as Generator: this surface is bus-shedding-only, "
        "grouped by `n.buses.carrier` — it never reads `n.lines`."
    ),
    ("compare_lost_load", "Link"): (
        "Same reasoning as Generator: this surface is bus-shedding-only, "
        "grouped by `n.buses.carrier` — it never reads `n.links`."
    ),
    ("compare_lost_load", "StorageUnit"): (
        "Same reasoning as Generator: this surface is bus-shedding-only, "
        "grouped by `n.buses.carrier` — it never reads `n.storage_units`."
    ),
    ("compare_storage_cycling", "Generator"): (
        "_compute_storage_cycling_summary (routers/compare.py) walks "
        "`n.storage_units` / `n.storage_units_t.p` exclusively — "
        "equivalent-full-cycles is a storage-specific metric "
        "(throughput / 2x energy capacity) with no generator analogue."
    ),
    ("compare_storage_cycling", "Line"): (
        "Same reasoning as Generator: this surface never reads `n.lines` — "
        "cycling is a storage-only concept."
    ),
    ("compare_storage_cycling", "Link"): (
        "Same reasoning as Generator: this surface never reads `n.links` — "
        "cycling is a storage-only concept."
    ),
}
