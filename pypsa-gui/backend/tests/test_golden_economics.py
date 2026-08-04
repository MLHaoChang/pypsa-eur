"""
Every economic surface, against an independent oracle AND against every other
surface.

Anchors catch CONSISTENT wrongness — all surfaces agreeing on a wrong annuity.
Agreement catches coverage gaps and drift. Neither alone suffices: on
2026-07-31 the Economics tab and the LCOH panel agreed exactly at EUR 246.02,
and they would have agreed just as neatly had the annuity been wrong.

Every helper and adapter below was written against the REAL response of the
endpoint it targets (see docs/superpowers/findings/
2026-08-01-economic-surface-disagreements.md for the exploration), not
against the shape the earlier planning brief guessed. Two of the brief's own
assumptions turned out to be wrong once run:

  * `get_asset_results` (Asset Detail) takes several FastAPI `Query(...)`
    parameters. Calling the plain function directly (no HTTP layer, per the
    task's own instructions) leaves any UNPASSED one holding the literal
    `Query(...)` sentinel object rather than its resolved default, and the
    endpoint's own `mode not in VIEW_MODES` / similar guards then 422. Every
    direct call below passes all of them explicitly.
  * The golden fixture's LP builds a real, non-zero `gas` capacity
    (`p_nom_opt` ~118.57 MW — `solar` is capped at p_nom=60, non-extendable,
    and can't clear the ~150 MW electricity load on its own), so `gas`'s
    CAPEX check exercises the annuity arithmetic for real, not just a
    zero-times-anything identity. `electrolyzer` (a Link) remains the other
    real check — kept for class coverage (Generator vs Link), not because
    `gas` is weak.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest

import routers.results as R
from tests.golden import fixture as gf
from tests.golden import oracle

REL = 1e-9  # values derived from one dispatch; float sums over 48 snapshots


@pytest.fixture()
def golden(reset_backend):
    """
    Install the solved golden network AFTER conftest's autouse reset.

    Depending on `reset_backend` explicitly is load-bearing: it is autouse and
    calls PyPSAService.reset_network() before every test, so without the
    ordering this fixture would install into a context that is about to be
    wiped.
    """
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _gas_expected_capex(n) -> float:
    """
    Independent expectation for the `gas` generator's horizon CAPEX.

    The golden fixture's LP builds a real, non-zero `gas` capacity
    (`p_nom_opt` ~118.57 MW — see module docstring), so this exercises the
    CRF/annuity magnitude for real: a broken annuity formula and a correct
    one produce different numbers here, unlike a check against an asset
    sized to zero. See `_electrolyzer_expected_capex` for the Link-class
    equivalent.
    """
    rate = oracle.annualised_capital_cost(
        overnight_cost=float(n.generators.at["gas", "overnight_cost"]),
        rate=gf.GOLDEN_DISCOUNT_RATE,
        lifetime=float(n.generators.at["gas", "lifetime"]),
        snapshots_per_period=gf.SNAPSHOTS_PER_PERIOD,
    )
    return oracle.horizon_capex(
        rate, float(n.generators.at["gas", "p_nom_opt"]), gf.GOLDEN_YEARS
    )


def _electrolyzer_expected_capex(n) -> float:
    """
    Independent expectation for the `electrolyzer` Link's horizon CAPEX.

    Unlike `gas`, the LP builds a strictly positive `p_nom_opt` for this
    asset, so comparing it against the oracle actually exercises the
    overnight-cost → CRF → per-period-fraction → horizon-years pipeline
    end to end.
    """
    rate = oracle.annualised_capital_cost(
        overnight_cost=float(n.links.at["electrolyzer", "overnight_cost"]),
        rate=gf.GOLDEN_DISCOUNT_RATE,
        lifetime=float(n.links.at["electrolyzer", "lifetime"]),
        snapshots_per_period=gf.SNAPSHOTS_PER_PERIOD,
    )
    return oracle.horizon_capex(
        rate, float(n.links.at["electrolyzer", "p_nom_opt"]), gf.GOLDEN_YEARS
    )


def test_asset_economics_reports_the_link_class_at_all(golden):
    # The regression that started this: /results/asset_economics returned
    # generators, storage_units and stores, with no `links` key at all.
    payload = R.get_asset_economics()

    assert "links" in payload
    assert [r["name"] for r in payload["links"]] == ["electrolyzer"]


def test_asset_economics_gas_capex_matches_the_oracle(golden):
    payload = R.get_asset_economics()
    row = next(r for r in payload["generators"] if r["name"] == "gas")

    assert row["fixed_cost_eur"] == pytest.approx(_gas_expected_capex(golden), rel=REL)


def test_asset_economics_electrolyzer_capex_matches_the_oracle(golden):
    """
    The check `test_asset_economics_gas_capex_matches_the_oracle` can't do:
    a nonzero-capacity, overnight_cost-parameterised asset, checked against
    an oracle that never imports `services/solver_service.py`.
    """
    payload = R.get_asset_economics()
    row = next(r for r in payload["links"] if r["name"] == "electrolyzer")

    assert row["fixed_cost_eur"] == pytest.approx(
        _electrolyzer_expected_capex(golden), rel=REL
    )


def test_a_genuinely_zero_cost_asset_reports_zero_not_unresolvable(golden):
    payload = R.get_asset_economics()
    row = next(r for r in payload["storage_units"] if r["name"] == "bess")

    assert row["fixed_cost_eur"] == 0.0


def test_cost_breakdown_agrees_with_asset_economics_on_solar(golden):
    """
    `solar`, not `gas`: `solar` is priced directly via `capital_cost`
    (non-extendable, fixed at p_nom=60), the other pricing path this fixture
    carries alongside `gas`/`electrolyzer`'s overnight_cost path — those are
    already checked for real by `test_asset_economics_gas_capex_matches_the_
    oracle` and `test_asset_economics_electrolyzer_capex_matches_the_oracle`.
    `solar` carries a real, non-zero capital_cost (EUR 82,500,000 horizon
    total) and is the only Generator on its carrier (`carrier="solar"`,
    fixture.py) — so the carrier-coincidence `_find_component_capex` relies
    on still holds.
    """
    ae = R.get_asset_economics()
    solar = next(r for r in ae["generators"] if r["name"] == "solar")
    cb = R.get_cost_breakdown()

    solar_capex = _find_component_capex(cb, "Generator", "solar")
    assert solar_capex == pytest.approx(solar["fixed_cost_eur"], rel=1e-6)
    assert solar_capex != 0.0  # guards against a silently-all-zero payload


def test_line_capex_agrees_with_the_oracle_across_cost_breakdown_and_asset_costs(golden):
    """
    asset_economics never reports Line at all (see
    `coverage.EXCLUSIONS[("asset_economics", "Line")]`), so
    `test_every_surface_agrees_on_every_covered_asset` below — which walks
    outward from asset_economics as its baseline — structurally can never
    exercise a Line comparison, no matter how many surfaces claim to cover
    Line in `coverage.COVERAGE`. L_ab is the fixture's only Line, so its
    (component-class-level) total in cost_breakdown IS its per-asset total;
    check that, and asset_costs's genuinely per-asset figure, against the
    oracle directly instead of leaving Line unchecked by this suite.
    """
    import routers.simulation as sim

    n = golden
    expected = oracle.horizon_capex(
        rate_per_mw=float(n.lines.at["L_ab", "capital_cost"]),
        p_nom_opt=float(n.lines.at["L_ab", "s_nom_opt"]),
        years=gf.GOLDEN_YEARS,
    )

    cb = R.get_cost_breakdown()
    line_row = next(r for r in cb["by_component"] if r["component"] == "Line")
    assert line_row["capex"] == pytest.approx(expected, rel=1e-6)

    ac = sim.asset_costs()
    ac_line = ac["lines"]["L_ab"]
    ac_horizon = (
        float(ac_line["capital_cost"])
        * float(n.lines.at["L_ab", "s_nom_opt"])
        * sum(gf.GOLDEN_YEARS)
    )
    assert ac_horizon == pytest.approx(expected, rel=1e-6)


def test_statistics_h2_capex_matches_asset_economics(golden):
    """
    /results/statistics is a raw `df_to_json(n.statistics())` pass-through,
    indexed by (component_class, carrier) — the same structural limitation
    as cost_breakdown (see `_find_component_capex`'s docstring): no per-
    asset field exists anywhere in the payload. `"Capital Expenditure |
    <period>"` is PyPSA's own PER-PERIOD ANNUAL rate (flat across periods
    here because only p_nom_opt and the years weighting vary by period, not
    the rate itself) — compare it to asset_economics's horizon total
    divided back down to an annual rate.
    """
    stats = R.get_statistics()
    h2_row = next(
        r for r in stats if r.get("level_0") == "Link" and r.get("level_1") == "H2"
    )

    ae = R.get_asset_economics()
    electrolyzer = next(r for r in ae["links"] if r["name"] == "electrolyzer")
    annual_expected = electrolyzer["fixed_cost_eur"] / sum(gf.GOLDEN_YEARS)

    for period in gf.GOLDEN_PERIODS:
        assert h2_row[f"Capital Expenditure | {period}"] == pytest.approx(
            annual_expected, rel=1e-6
        )


def test_economics_by_carrier_h2_capex_agrees_with_asset_economics(golden):
    """
    Was xfail(strict=True) until Task 9: `_safe_capital_cost` in
    `routers/compare.py` used to hand-roll
    `overnight_cost * annuity(rate, lifetime)`, omitting the `1/n.nyears`
    scaling PyPSA's own `capital_cost` accessor applies (365x too high on
    this fixture's unit-weighted 24-snapshot periods; see
    docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md
    Sections 4 and 9). Fixed by making `_safe_capital_cost` delegate to
    `services.solver_service.periodized_capital_costs` — the same resolution
    `asset_economics` / `cost_breakdown` / `asset_costs` / Asset Detail all
    use — instead of carrying a second, independent annuity implementation.
    """
    ae = R.get_asset_economics()
    electrolyzer = next(r for r in ae["links"] if r["name"] == "electrolyzer")

    ebc = R.get_economics_by_carrier()
    h2_capex_eur = ebc["by_carrier"]["h2"]["capex_meur"]["total"] * 1e6

    assert h2_capex_eur == pytest.approx(electrolyzer["fixed_cost_eur"], rel=1e-6)


def test_compare_capacity_agrees_with_asset_economics(golden):
    """
    IMPORTANT-2 (final review, 2026-08-03): `capex_meur_by_carrier` and
    `new_capex_meur_by_carrier` (routers/compare.py::get_results_summary's
    `capacity` field) feed the Compare-Scenarios "Capacity" tab and were
    changed 365x by Task 9 (the same `_safe_capital_cost` delegation fix
    `test_economics_by_carrier_h2_capex_agrees_with_asset_economics` checks
    above) — yet had no golden coverage at all until this test; see
    `coverage.NO_ADAPTER_REASONS["compare_capacity"]` for why this is a spot
    check rather than a generic ADAPTERS entry (carrier-aggregated only, no
    per-asset field).

    Calls `_compute_capacity_summary` directly rather than the real
    `/{name}/results-summary` endpoint — same reason `compare_economics`'s
    spot check does: the endpoint needs a project saved to disk, and this
    function is where the numbers are actually computed.

    Two different fields, two different assets, because they are NOT the
    same quantity:
      * `capex_meur_by_carrier` is a HORIZON total (existing + new,
        scaled by investment-period years) — checked via `solar`
        (non-extendable, p_nom_opt == p_nom, so total IS the whole fleet,
        the same direct-capital_cost pricing path
        `test_cost_breakdown_agrees_with_asset_economics_on_solar` checks).
      * `new_capex_meur_by_carrier` is an ANNUAL rate (NOT scaled by years)
        for the build-year-attributed delta only — checked via `gas`
        (extendable, LP expands it from p_nom=100 to p_nom_opt~118.57, a
        real positive delta that exercises the overnight_cost -> CRF
        pipeline; `electrolyzer` doesn't work for this check because the
        LP SHRINKS it below its initial p_nom here, and `_walk_plain` clamps
        negative deltas to zero, so it never appears in this field at all).
    """
    from routers.compare import _compute_capacity_summary

    n = golden
    summary = _compute_capacity_summary(
        n, periods=list(gf.GOLDEN_PERIODS), is_multi=True, has_solve=True,
    )

    expected_solar_total_eur = oracle.horizon_capex(
        rate_per_mw=float(n.generators.at["solar", "capital_cost"]),
        p_nom_opt=float(n.generators.at["solar", "p_nom_opt"]),
        years=gf.GOLDEN_YEARS,
    )
    solar_capex_meur = summary.capex_meur_by_carrier["solar"].total
    assert solar_capex_meur != 0.0
    assert solar_capex_meur * 1e6 == pytest.approx(expected_solar_total_eur, rel=1e-6)

    annual_rate = oracle.annualised_capital_cost(
        overnight_cost=float(n.generators.at["gas", "overnight_cost"]),
        rate=gf.GOLDEN_DISCOUNT_RATE,
        lifetime=float(n.generators.at["gas", "lifetime"]),
        snapshots_per_period=gf.SNAPSHOTS_PER_PERIOD,
    )
    delta = (
        float(n.generators.at["gas", "p_nom_opt"])
        - float(n.generators.at["gas", "p_nom"])
    )
    assert delta > 0.0  # guards against a silently-zero comparison
    expected_new_gas_eur = annual_rate * delta
    new_gas_meur = summary.new_capex_meur_by_carrier["gas"].total
    assert new_gas_meur * 1e6 == pytest.approx(expected_new_gas_eur, rel=1e-6)


def _find_component_capex(cost_breakdown: dict, component_class: str, name: str) -> float:
    """
    Pull one asset's CAPEX out of /results/cost_breakdown.

    MEASURED SHAPE (2026-08-01): cost_breakdown has NO per-asset field at
    all. `by_component` aggregates to (component_class) totals ONLY — e.g.
    the "Generator" row sums `gas` AND `solar` together — and `by_carrier`
    is one level finer, (component_class, carrier) pairs, but still not
    per-asset. The task-4 brief's original version of this helper assumed a
    `name` key on `by_component` rows; there is no such key anywhere in the
    payload.

    This function matches on CARRIER via `by_carrier`, using `name` as the
    carrier to look up. That is only correct because the golden fixture
    happens to give both `gas` and `solar` a carrier equal to their own
    name, and each is the ONLY Generator on that carrier. It is a
    coincidence of the fixture, not a general capability of cost_breakdown
    — do not reuse this helper for an asset whose carrier differs from its
    name, or where two assets share a carrier: it would silently return a
    SUMMED number instead of raising.
    """
    for row in cost_breakdown.get("by_carrier", []):
        if row.get("component") == component_class and row.get("carrier") == name:
            return float(row.get("capex", 0.0))
    raise AssertionError(
        f"cost_breakdown has no by_carrier entry for {component_class} carrier "
        f"{name!r} — if that is intended, add it to EXCLUSIONS in "
        "tests/golden/coverage.py"
    )


def _find_metric(asset_detail: dict, metric_id: str) -> float:
    """
    Pull one scalar metric's VALUE out of an Asset Detail
    (`GET /asset_results/{class}/{name}`) response.

    MEASURED SHAPE (2026-08-01): `asset_detail["metrics"]` is a CHECKLIST —
    id/label/unit/kind/origin/status(/formula/reason) — with NO `value`
    field anywhere; it exists so the frontend can render every applicable
    metric's availability (ok/blocked/na) before any are actually computed.
    The computed number lives in `asset_detail["scalars"][metric_id]`,
    populated for every metric in the requested set — which defaults to
    every `ok` member of the category when `metrics=""`, so `capex_annual`
    is present without asking for it by id. The task-4 brief's original
    version of this helper read `metrics[i]["value"]`, which raises
    immediately: not because the metric is missing, but because that key
    was never part of the response shape.
    """
    scalars = asset_detail.get("scalars", {})
    if metric_id in scalars:
        return float(scalars[metric_id])
    for m in asset_detail.get("metrics", []):
        if m.get("id") == metric_id:
            raise AssertionError(
                f"Asset Detail lists metric {metric_id!r} (status="
                f"{m.get('status')!r}) but it never reached `scalars` — "
                "check the metric's status/reason instead of assuming it "
                "just needs looking up differently"
            )
    raise AssertionError(f"Asset Detail has no metric {metric_id!r}")


def test_asset_detail_capex_agrees_with_asset_economics(golden):
    import routers.asset_results as AR

    ae = R.get_asset_economics()
    electrolyzer = next(r for r in ae["links"] if r["name"] == "electrolyzer")

    detail = AR.get_asset_results(
        component_class="Link", name="electrolyzer", category="capacity",
        source="lopf", from_=None, to=None, period=None,
        mode="chronological", metrics="",
    )
    capex = _find_metric(detail, "capex_annual")

    # Asset Detail reports an ANNUAL rate; asset_economics reports the horizon
    # total. Compare on the same basis.
    annual = electrolyzer["fixed_cost_eur"] / sum(gf.GOLDEN_YEARS)
    assert capex == pytest.approx(annual, rel=1e-6)


# surface id -> callable(network) -> {(component_class, asset_name): horizon_capex_eur}
Adapter = Callable[[Any], dict[tuple[str, str], float]]


def _from_asset_economics(_n) -> dict[tuple[str, str], float]:
    payload = R.get_asset_economics()
    out: dict[tuple[str, str], float] = {}
    for key, cls in (("generators", "Generator"),
                     ("storage_units", "StorageUnit"),
                     ("stores", "Store"),
                     ("links", "Link")):
        for row in payload.get(key, []):
            out[(cls, row["name"])] = float(row["fixed_cost_eur"])
    return out


def _from_lcoh(_n) -> dict[tuple[str, str], float]:
    """
    Per-Link horizon CAPEX from /results/lcoh.

    `coverage.COVERAGE["lcoh"]` is `{"Link"}`, but the endpoint (routers/
    results.py::get_lcoh) only ever walks Links whose carrier matches an
    electrolyser-like token — narrower than "every Link", though the golden
    fixture's single Link (carrier "H2") happens to qualify.
    `capex_eur_per_year` is PyPSA's annual rate; multiply by the horizon
    years to land on asset_economics's basis.
    """
    payload = R.get_lcoh()
    years = sum(gf.GOLDEN_YEARS)
    return {
        ("Link", row["name"]): float(row["capex_eur_per_year"]) * years
        for row in payload.get("rows", [])
    }


# component_attr (periodized_capital_costs' key) -> (component_class, nominal
# capacity column to read p_nom_opt/s_nom_opt from). Limited to
# coverage.FIXTURE_CLASSES — the golden fixture has no Store, and asset_costs'
# own "stores"/"transformers" buckets would be empty here regardless.
_ASSET_COSTS_CLASSES: dict[str, tuple[str, str]] = {
    "generators": ("Generator", "p_nom_opt"),
    "storage_units": ("StorageUnit", "p_nom_opt"),
    "links": ("Link", "p_nom_opt"),
    "lines": ("Line", "s_nom_opt"),
}


def _from_asset_costs(n) -> dict[tuple[str, str], float]:
    """
    Per-asset horizon CAPEX from /api/simulation/asset_costs
    (services.solver_service.periodized_capital_costs).

    Genuinely per-asset for every class it returns — unlike cost_breakdown /
    statistics / economics_by_carrier, this payload is keyed by asset NAME,
    not by carrier or class alone. `capital_cost` is the annualised
    €/MW(or /MWh)/yr rate (correctly resolved through the same
    with_periodized_cost_defaults wrapper asset_economics uses); multiply by
    the network's own p_nom_opt/s_nom_opt and the horizon years.
    """
    import routers.simulation as sim

    payload = sim.asset_costs()
    years = sum(gf.GOLDEN_YEARS)
    out: dict[tuple[str, str], float] = {}
    for comp_attr, (cls, opt_col) in _ASSET_COSTS_CLASSES.items():
        df = getattr(n, comp_attr, None)
        for name, entry in payload.get(comp_attr, {}).items():
            if df is None or name not in df.index:
                continue
            opt = float(df.at[name, opt_col])
            out[(cls, name)] = float(entry["capital_cost"]) * opt * years
    return out


ADAPTERS: dict[str, Adapter] = {
    "asset_economics": _from_asset_economics,
    "lcoh": _from_lcoh,
    "asset_costs": _from_asset_costs,
}

# Every SURFACE not in ADAPTERS must have an entry here instead — see the
# `test_every_surface_agrees_on_every_covered_asset` assertion that
# `set(ADAPTERS) | set(NO_ADAPTER_REASONS) == set(cov.SURFACES)`. Without that
# assertion, a TENTH surface added to `coverage.SURFACES` in the future would
# silently be skipped by `if sid in ADAPTERS` below and never compared — the
# exact same silence-reads-as-agreement failure mode this whole task exists
# to eliminate, one level up the stack. Keeping the reasons as DATA (not
# comments) is what makes that assertion possible.
NO_ADAPTER_REASONS: dict[str, str] = {
    "cost_breakdown": (
        "Aggregates to (component_class[, carrier]) — no per-asset field at "
        "all (verified 2026-08-01; see `_find_component_capex`'s docstring "
        "and the findings doc). Forcing it into `{(class, name): value}` "
        "would mean inventing a mapping the endpoint doesn't provide, "
        "exactly what the task-4 brief warns against. Spot-checked instead "
        "by `test_cost_breakdown_agrees_with_asset_economics_on_solar` and "
        "`test_line_capex_agrees_with_the_oracle_across_cost_breakdown_and_asset_costs`."
    ),
    "statistics": (
        "Same structural limitation as cost_breakdown: raw "
        "`df_to_json(n.statistics())`, indexed by (component_class, "
        "carrier), no per-asset field. Spot-checked instead by "
        "`test_statistics_h2_capex_matches_asset_economics`."
    ),
    "economics_by_carrier": (
        "Aggregates to carrier only (`{'by_carrier': {carrier: {...}}}`), "
        "no per-asset field — the brief's own example of a surface that "
        "must not be forced into this shape. Until Task 9 (2026-08-01) it "
        "ALSO carried a wrong-number bug (~365x CAPEX overstatement on this "
        "fixture for overnight_cost-parameterised assets, missing factor "
        "1/n.nyears where n.nyears = Sum(weights)/8760) — fixed by making "
        "`compare._safe_capital_cost` delegate to "
        "`services.solver_service.periodized_capital_costs` instead of "
        "hand-rolling the annuity. "
        "`test_economics_by_carrier_h2_capex_agrees_with_asset_economics` "
        "spot-checks this surface directly and now passes for real (the "
        "xfail marker was removed, not just widened)."
    ),
    "asset_results": (
        "Genuinely per-asset, but runs through the exact "
        "`services/asset_results/compute.py::capex_annual` compute path "
        "`test_asset_detail_capex_agrees_with_asset_economics` already "
        "covers directly. That test was a known, xfail'd defect (100% low "
        "for overnight_cost-parameterised assets) until Task 5 fixed it; "
        "the marker is gone and the test now passes for real. Still "
        "excluded from this generic loop rather than added to ADAPTERS — "
        "doing so would duplicate an already-dedicated test without "
        "discovering anything new, and this loop is meant to catch "
        "UNTRACKED disagreements, not restate a tracked, fixed one."
    ),
    "asset_results_xlsx": (
        "Shares the identical compute path as asset_results — "
        "`services/asset_results/export.py` imports `build_response` from "
        "the same `services/asset_results/service.py` — verified by reading "
        "the import, not re-probed at runtime. Same reasoning as "
        "asset_results applies: excluded from this loop, covered by the "
        "same dedicated test."
    ),
    "compare_economics": (
        "Its `by_carrier` field is carrier-aggregated by the exact same "
        "`_compute_economics_summary` function (routers/compare.py) that "
        "economics_by_carrier calls (routers/results.py::"
        "get_economics_by_carrier) — until Task 9 it carried the identical "
        "CAPEX-overstatement bug, now fixed alongside economics_by_carrier's "
        "(same underlying `_safe_capital_cost` delegation fix; MEASURED on "
        "the golden fixture: per_asset_lcoh[electrolyzer].capex_meur.total "
        "* 1e6 == 166249.77136776928 == asset_economics's fixed_cost_eur, "
        "exactly). Its `per_asset_lcoh` field IS genuinely per-asset, but "
        "Link-only — narrower than the Generator/StorageUnit/Link set "
        "`coverage.COVERAGE['compare_economics']` lists, so it can't stand "
        "in as a general per-class adapter either; this coverage-claim "
        "mismatch is the one thing about this surface still deferred, "
        "documented in the findings doc rather than force-fit into "
        "ADAPTERS. (Checked by calling `_compute_economics_summary` "
        "directly, since the real endpoint needs a project saved to disk.)"
    ),
    "compare_capacity": (
        "IMPORTANT-2 (final review, 2026-08-03): `capex_meur_by_carrier` and "
        "`new_capex_meur_by_carrier` (routers/compare.py::"
        "get_results_summary -> _compute_capacity_summary -> "
        "_compute_total_annuitised_capex) are carrier-aggregated only — no "
        "per-asset field, same structural limitation as cost_breakdown / "
        "economics_by_carrier / compare_economics above. These are the "
        "fields Task 9 changed by 365x for overnight_cost-priced assets "
        "(the same `_safe_capital_cost` delegation fix), yet no golden test "
        "asserted them before this entry existed — `coverage.py` had no "
        "`compare_capacity` surface at all, and the only prior check "
        "(tests/qa_results_summary_compare.py) is not pytest-collected "
        "(see pytest.ini). Spot-checked instead by "
        "`test_compare_capacity_agrees_with_asset_economics`, which calls "
        "`_compute_capacity_summary` directly (same reason as "
        "compare_economics: the real endpoint needs a project saved to "
        "disk) and checks both CAPEX fields against the oracle for `solar` "
        "(non-extendable, exercises `capex_meur_by_carrier`'s total-only "
        "basis) and `gas` (extendable, LP expands it from p_nom=100 to "
        "p_nom_opt~118.57, exercising `new_capex_meur_by_carrier`'s "
        "built-increment basis for real — `electrolyzer` can't stand in "
        "here because the LP SHRINKS it below its initial p_nom on this "
        "fixture, and `_walk_plain` clamps negative deltas to zero)."
    ),
    # ── Task 20 additions ───────────────────────────────────────────────
    # The remaining eight Compare-Scenarios tabs (coverage.py SURFACES). None
    # of these report CAPEX / fixed-cost at all — they report installed
    # capacity counts, dispatch energy, branch loading, marginal prices,
    # emissions, curtailment, VOLL shedding and storage cycling respectively
    # — so none of them can be forced into this loop's
    # `{(component_class, name): horizon_capex_eur}` Adapter shape without
    # inventing a mapping the surface doesn't provide (the exact anti-pattern
    # `_from_asset_economics`'s own docstring and cost_breakdown's
    # NO_ADAPTER_REASONS entry both warn against). Each is a genuinely
    # DIFFERENT quantity from the CAPEX baseline this test cross-checks, not
    # merely a differently-shaped view of the same one.
    "compare_overview": (
        "get_compare_state's `installed_capacity_by_carrier` / "
        "`storage_capacity_by_carrier` are INSTALLED p_nom, not annuitised "
        "CAPEX — a different quantity entirely (MW, not EUR), so there is no "
        "sensible {(class, name): capex_eur} mapping to build. No dedicated "
        "cross-surface CAPEX test exists for this field because it never "
        "claims to report CAPEX in the first place."
    ),
    "compare_dispatch": (
        "_compute_dispatch_summary reports dispatch_gwh_by_carrier (MWh) and "
        "opex_meur (variable cost) — no CAPEX/fixed-cost field exists on "
        "DispatchComparison at all. Nothing here is comparable to "
        "asset_economics's fixed_cost_eur baseline."
    ),
    "compare_loading": (
        "_compute_loading_summary reports peak/mean loading (a ratio, "
        "dimensionless) and binding_hours (an hours count) per branch — no "
        "cost field of any kind. Not a CAPEX surface."
    ),
    "compare_prices": (
        "_compute_prices_summary reports EUR/MWh marginal-price statistics — "
        "a price, not an investment cost, and per-bus-carrier rather than "
        "per-asset besides. No CAPEX field exists on PricesComparison."
    ),
    "compare_emissions": (
        "_compute_emissions_summary reports tCO2 (kt) and kg/MWh intensity — "
        "physical quantities with no EUR dimension at all. No CAPEX field "
        "exists on EmissionsComparison."
    ),
    "compare_curtailment": (
        "_compute_curtailment_summary reports GWh curtailed and a percentage "
        "rate — energy quantities, not cost. No CAPEX field exists on "
        "CurtailmentComparison (curtailment's EUR impact, when priced, "
        "surfaces on the Economics tab's curtailment_cost_meur instead)."
    ),
    "compare_lost_load": (
        "_compute_lost_load_summary's `total_cost_meur` is a shedding cost "
        "(unserved energy x VOLL), not an investment/CAPEX cost — a "
        "different EUR quantity than asset_economics's fixed_cost_eur, and "
        "bus-carrier-keyed rather than per-asset. Not comparable to the "
        "CAPEX baseline this loop checks."
    ),
    "compare_storage_cycling": (
        "_compute_storage_cycling_summary reports throughput_mwh and a "
        "dimensionless cycles count — no cost field of any kind exists on "
        "StorageCyclingComparison or StorageUnitCycles."
    ),
}


def test_every_surface_agrees_on_every_covered_asset(golden):
    """
    One loop over the coverage matrix. A surface that reports NOTHING for a
    class it claims to cover fails here — which is the missing-Link bug, caught
    structurally rather than by someone remembering to look.
    """
    from tests.golden import coverage as cov

    # A surface this suite has neither an adapter NOR a documented reason
    # for skipping is a silent gap — the same failure mode this whole task
    # exists to eliminate, just one level up (at "which surfaces did we even
    # look at" instead of "does this asset's number agree").
    accounted_for = set(ADAPTERS) | set(NO_ADAPTER_REASONS)
    assert accounted_for == set(cov.SURFACES), (
        "every surface in coverage.SURFACES must have either an ADAPTERS "
        "entry or a NO_ADAPTER_REASONS entry -- unaccounted: "
        f"{set(cov.SURFACES) - accounted_for}, "
        f"stale (no longer a real surface): {accounted_for - set(cov.SURFACES)}"
    )

    reported = {sid: ADAPTERS[sid](golden) for sid in cov.SURFACES if sid in ADAPTERS}

    baseline = reported["asset_economics"]
    problems: list[str] = []

    for sid, values in reported.items():
        if sid == "asset_economics":
            continue
        for cls in sorted(cov.COVERAGE.get(sid, set()) & cov.FIXTURE_CLASSES):
            names = [n for (c, n) in baseline if c == cls]
            for name in names:
                if (cls, name) not in values:
                    problems.append(
                        f"{sid} claims to cover {cls} but reported nothing for {name!r}"
                    )
                    continue
                a, b = baseline[(cls, name)], values[(cls, name)]
                if a != pytest.approx(b, rel=1e-6):
                    problems.append(
                        f"{sid} {cls} {name}: {b:,.2f} vs asset_economics {a:,.2f} "
                        f"({abs(b - a) / a * 100 if a else float('inf'):.1f}% apart)"
                    )

    assert not problems, "Economic surfaces disagree:\n  " + "\n  ".join(problems)


def test_the_frontend_payload_fixture_is_current(golden):
    """
    The frontend mapping test reads a committed copy of this payload. If the
    copy drifts from what the backend actually returns, that test passes
    against a fiction — so regenerate it here and let CI fail on a dirty tree.
    """
    import json
    import pathlib

    payload = R.get_asset_economics()
    dest = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "pages" / "results" / "__fixtures__"
        / "asset-economics.golden.json"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    # Assert the payload is USABLE, not merely that a file appeared. A test
    # whose only assertion is `exists()` passes just as happily on an empty
    # dict, and the frontend test downstream would then assert against nothing.
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert set(written) >= {"generators", "storage_units", "links"}
    assert written["links"], "no link rows — the frontend mapping test needs one"
    assert written["generators"], "no generator rows"


def test_an_unresolvable_capex_says_why_instead_of_reporting_zero(golden):
    """
    discount_rate = NaN is what turned every annuity into NaN and made
    n.statistics() report EUR 0 CAPEX for assets the LP had costed correctly.
    A zero here is indistinguishable from a free asset; a reason is not.
    """
    import routers.simulation as sim_router
    from services.asset_results import compute as C
    from services.solver_service import SolverConfig

    # DEEP-COPY FIRST. `solve_golden_network()` caches at module level and hands
    # every test THE SAME OBJECT, so mutating `golden` here would leak a NaN
    # discount_rate into every test that runs after this one — including
    # `test_a_resolvable_capex_has_no_reason` immediately below, which would
    # then fail for a reason that has nothing to do with the code under test.
    import copy

    n = copy.deepcopy(golden)
    n.generators.loc["gas", "discount_rate"] = float("nan")
    # The solver_config mutation is safe unmocked: conftest's autouse
    # `reset_backend` restores it after every test.
    sim_router._state["solver_config"] = SolverConfig(discount_rate=float("nan"))

    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    reason = C.gen_capex_unresolved_reason(ctx)

    assert reason is not None
    assert "discount_rate" in reason


def test_a_resolvable_capex_has_no_reason(golden):
    from services.asset_results import compute as C

    ctx = C.build_ctx(golden, "Generator", "gas", source="lopf", sns=golden.snapshots)
    assert C.gen_capex_unresolved_reason(ctx) is None


def test_a_genuine_zero_is_not_reported_as_unresolvable(golden):
    """
    `bess` has capital_cost = 0.0 deliberately, and no overnight_cost. Zero
    is an answer, not a symptom of a bug.

    REVIEW FINDING (2026-08-01): asserting only `bess -> None` passes for an
    accidental reason. `gen_capex_unresolved_reason` returns None the moment
    `overnight_cost` is unset, REGARDLESS of what `capital_cost` holds — the
    check would pass byte-identically if `bess.capital_cost` were 500.0. It
    proves "no overnight_cost -> None", not that a genuine zero specifically
    survives. `solar` (fixture.py: `capital_cost=27_500.0`, no
    `overnight_cost`) is the control: same resolution path, a real non-zero
    value. Both must report None for the claim in this test's name to be
    more than a coincidence of which asset happened to get picked.
    """
    from services.asset_results import compute as C

    assert float(golden.storage_units.at["bess", "capital_cost"]) == 0.0
    assert float(golden.generators.at["solar", "capital_cost"]) != 0.0

    bess_ctx = C.build_ctx(golden, "StorageUnit", "bess", source="lopf", sns=golden.snapshots)
    solar_ctx = C.build_ctx(golden, "Generator", "solar", source="lopf", sns=golden.snapshots)

    assert C.gen_capex_unresolved_reason(bess_ctx) is None
    assert C.gen_capex_unresolved_reason(solar_ctx) is None


def test_an_unresolved_capex_reaches_the_asset_results_endpoint_as_a_reason(golden):
    """
    REVIEW FINDING (2026-08-01): `gen_capex_unresolved_reason` was correct
    but unreachable — declared in compute.py, called only by its own test,
    wired into no `Metric.requires`. From a user's perspective that is dead
    code: the spec requires the surface to STATE the reason, and a helper
    nobody calls does not do that.

    Wiring: `registry.REQ_ANNUITY` is a new precondition key, set by
    `compute._annuity_status` (via `compute.preconditions()`) on every
    `capex_annual` / `fixed_cost_eur` metric's `requires` tuple. This test
    proves the reason actually reaches an API response — GET
    /asset_results/{class}/{name} (routers.asset_results.get_asset_results
    -> services.asset_results.service.build_response) — not just the
    compute-layer function in isolation.

    `capex_annual` lives in category="capacity"; `fixed_cost_eur` in
    category="economics" — the endpoint resolves one category per call, so
    both need their own request.
    """
    import copy

    import routers.asset_results as AR
    import routers.simulation as sim_router
    from services.solver_service import SolverConfig
    from tests.golden import fixture as gf

    n = copy.deepcopy(golden)
    n.generators.loc["gas", "discount_rate"] = float("nan")
    gf.install_golden(n)
    # Override AFTER install_golden (which pins GOLDEN_DISCOUNT_RATE) so the
    # solver-config fallback is unavailable too — same shape as
    # `test_an_unresolvable_capex_says_why_instead_of_reporting_zero`, but
    # driven all the way through the real endpoint instead of the compute
    # function directly. conftest's autouse `reset_backend` restores both
    # the network and the solver config after this test.
    sim_router._state["solver_config"] = SolverConfig(
        discount_rate=float("nan"),
        multi_investment_periods=True,
        investment_periods=list(gf.GOLDEN_PERIODS),
    )

    for category, metric_id in (("capacity", "capex_annual"),
                                 ("economics", "fixed_cost_eur")):
        detail = AR.get_asset_results(
            component_class="Generator", name="gas", category=category,
            source="lopf", from_=None, to=None, period=None,
            mode="chronological", metrics="",
        )
        row = next(m for m in detail["metrics"] if m["id"] == metric_id)

        assert row["status"] == "blocked"
        assert "discount_rate" in row["reason"]
        assert row.get("remedy", {}).get("action") == "open_properties"
        # Blocked means NOT computed — no confident number sits next to the
        # reason for the frontend to render by mistake.
        assert metric_id not in detail.get("scalars", {})


def test_a_blank_lifetime_falls_back_to_the_config_default_like_discount_rate_does(golden):
    """
    IMPORTANT-1 (final review, 2026-08-03): `capex_unresolved_reason` mirrored
    `discount_rate`'s solver-config fallback but never had one for `lifetime`
    — it blocked ANY overnight_cost-priced asset whose `lifetime` was unset
    (PyPSA's own default of +inf), even though the compute path it gates
    (`capex_annual` -> `periodized_capital_costs` ->
    `fill_periodized_cost_defaults`, services/solver_service.py) already
    fills `lifetime` from `cfg.default_lifetime` and returns a real number.
    Every component schema defaults `lifetime` to +inf (`models/schemas.py`),
    so this was reachable from the GUI on any overnight_cost-priced asset
    with a blank Lifetime field — every surface but Asset Detail showed a
    confident euro figure, and Asset Detail's reason was also factually
    wrong ("no period to spread the investment over" when config supplies
    one). `diesel_backup` (fixture.py) carries exactly this shape: real
    overnight_cost, `lifetime` left at PyPSA's default, on its own bus/load
    so it can't perturb any other anchor in this file.
    """
    from services.asset_results import compute as C

    assert golden.generators.at["diesel_backup", "lifetime"] == float("inf")

    ctx = C.build_ctx(golden, "Generator", "diesel_backup", source="lopf",
                       sns=golden.snapshots)
    assert C.gen_capex_unresolved_reason(ctx) is None


def test_a_blank_lifetime_reaches_the_asset_results_endpoint_as_ok(golden):
    """
    Endpoint-level companion to the test above — same relationship
    `test_an_unresolved_capex_reaches_the_asset_results_endpoint_as_a_reason``
    has to `test_an_unresolvable_capex_says_why_instead_of_reporting_zero`:
    proves the fix reaches GET /asset_results/{class}/{name}
    (routers.asset_results.get_asset_results ->
    services.asset_results.service.build_response), not just the
    compute-layer function in isolation. `capex_annual` lives in
    category="capacity"; `fixed_cost_eur` in category="economics" — the
    endpoint resolves one category per call, so both need their own request.
    """
    import routers.asset_results as AR

    # The two metrics are on DIFFERENT time bases and this test used to assert
    # one number for both, which is the conflation that made Asset Detail
    # disagree with the Economics tab:
    #
    #   capex_annual    EUR/a — the annual rate
    #   fixed_cost_eur  EUR   — that rate over the horizon (5 + 10 = 15 years)
    #
    # 28211.676894465407 is not a number this test chose; it is what
    # /results/asset_economics reports for `diesel_backup`, asserted directly
    # in test_asset_detail_horizon_scaling.py.
    annual = 1880.778459631027
    for category, metric_id, expected in (
        ("capacity", "capex_annual", annual),
        ("economics", "fixed_cost_eur", annual * 15.0),
    ):
        detail = AR.get_asset_results(
            component_class="Generator", name="diesel_backup", category=category,
            source="lopf", from_=None, to=None, period=None,
            mode="chronological", metrics="",
        )
        row = next(m for m in detail["metrics"] if m["id"] == metric_id)

        assert row["status"] == "ok"
        # ok means it actually reached `scalars` with a real, non-zero
        # number — not a silent 0.0 masquerading as "computed".
        assert metric_id in detail.get("scalars", {})
        assert detail["scalars"][metric_id] == pytest.approx(expected, rel=1e-6)
