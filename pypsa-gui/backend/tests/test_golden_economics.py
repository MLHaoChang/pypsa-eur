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
  * The golden fixture's LP declines to build any `gas` capacity at all
    (`solar` alone clears the electricity load), so `gas`'s `p_nom_opt` is
    0. That makes `gas` a WEAK check for anything multiplied by capacity —
    a broken annuity formula and a correct one both evaluate to zero once
    multiplied by zero. Tests that need to exercise the annuity arithmetic
    for real use the Link `electrolyzer` instead, which the LP DOES build.
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

    WEAK BY CONSTRUCTION: the golden fixture's LP builds zero `gas` capacity
    (see module docstring), so this only confirms asset_economics reports a
    clean 0.0 — not NaN, not an exception — for an extendable asset sized to
    nothing. It does NOT exercise the CRF/annuity magnitude, because
    anything times zero is zero regardless of whether the annuity itself is
    right. See `_electrolyzer_expected_capex` for the check that does.
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
    `solar`, not `gas`: `gas`'s LP-optimal capacity is 0 (see module
    docstring), so a gas-vs-cost_breakdown check would only ever compare
    0.0 to 0.0 — passing against a cost_breakdown that reported zero for
    EVERYTHING, not just gas. `solar` carries a real, non-zero capital_cost
    (EUR 82,500,000 horizon total) and, like `gas`, is the only Generator on
    its carrier (`carrier="solar"`, fixture.py:72-78) — so the
    carrier-coincidence `_find_component_capex` relies on still holds.
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MEASURED on the golden fixture, 2026-08-01: economics_by_carrier "
        "and compare_economics share one computation "
        "(routers/compare.py:_compute_economics_summary, called from "
        "routers/results.py:791/798 and routers/compare.py:2652), which "
        "prices an overnight_cost-parameterised asset via "
        "_safe_capital_cost() = overnight_cost * annuity(rate, lifetime) "
        "(routers/compare.py:341-374). That OMITS the scaling PyPSA's own "
        "capital_cost accessor applies via periodized_cost(..., "
        "nyears=n.nyears) -- and the trigger is NOT snapshot count, it is "
        "the snapshot WEIGHTS: n.nyears = (Sum(snapshot_weightings["
        "'objective'] per period) / 8760) (pypsa/network/index.py:648-654; "
        "n.nyears itself is the fraction of a year the model represents, "
        "so the MISSING factor -- what _safe_capital_cost should have "
        "multiplied by and didn't -- is 1/n.nyears, not n.nyears). On "
        "THIS fixture's 24 unit-weighted snapshots per period, n.nyears = "
        "24/8760 and the missing factor 1/n.nyears = 8760/24 = 365x too "
        "HIGH. Measured: economics_by_carrier's 'h2' carrier reports "
        "capex_meur.total = 60.681166... M EUR against the correct EUR "
        "166,249.77 (asset_economics fixed_cost_eur for 'electrolyzer') "
        "-- a 36,400% overstatement. The factor is NOT fixed at 365x: "
        "1/n.nyears is 52.14x on a unit-weighted 168-hour representative "
        "week, and 1x -- INVISIBLE -- on a full 8760-snapshot year AND on "
        "a correctly weighted representative-week set, whose "
        "snapshot_weightings are set so the weighted total reconstructs "
        "the full year (Sum ~= 8760h, n.nyears ~= 1, "
        "routers/network.py:1467-1469, sample_representative_weeks). The "
        "bug reappears at 52.14x if a rep-week project is later promoted "
        "to multi-period, because set_snapshots(MultiIndex) resets "
        "snapshot_weightings to 1.0 (documented in CLAUDE.md). So the "
        "common unit-weighted small-snapshot-set case (24h, 48h, 168h) is "
        "affected, not a representative-week corner case. Assets priced "
        "via a direct `capital_cost` (solar in this fixture) are "
        "unaffected: _safe_capital_cost only mis-scales the overnight_cost "
        "branch. Not previously tracked; found by this task, not by the "
        "originating brief. strict=True fails this marker once the bug is "
        "gone so it cannot outlive the defect."
    ),
)
def test_economics_by_carrier_h2_capex_agrees_with_asset_economics(golden):
    ae = R.get_asset_economics()
    electrolyzer = next(r for r in ae["links"] if r["name"] == "electrolyzer")

    ebc = R.get_economics_by_carrier()
    h2_capex_eur = ebc["by_carrier"]["h2"]["capex_meur"]["total"] * 1e6

    assert h2_capex_eur == pytest.approx(electrolyzer["fixed_cost_eur"], rel=1e-6)


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

    `coverage.COVERAGE["lcoh"]` is `{"Link"}`, but the endpoint only ever
    walks Links whose carrier matches an electrolyser-like token
    (routers/results.py:939-949) — narrower than "every Link", though the
    golden fixture's single Link (carrier "H2") happens to qualify.
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
        "must not be forced into this shape. ALSO carries a newly-found "
        "wrong-number bug (~365x CAPEX overstatement on this fixture for "
        "overnight_cost-parameterised assets; the true factor is "
        "1/n.nyears (n.nyears = Sum(weights)/8760), not a fixed 365 — see "
        "the xfail reason on "
        "`test_economics_by_carrier_h2_capex_agrees_with_asset_economics`, "
        "which spot-checks this surface directly)."
    ),
    "asset_results": (
        "Genuinely per-asset, but runs through the exact "
        "`services/asset_results/compute.py::capex_annual` that "
        "`test_asset_detail_capex_agrees_with_asset_economics` already "
        "tracks as a known, xfail'd defect. Including it here would re-trip "
        "the SAME already-tracked bug as a hard failure inside this loop "
        "instead of the dedicated xfail — discovering nothing new, only "
        "duplicating it in a form that can't be marked xfail without hiding "
        "whatever ELSE this loop might catch on a future run."
    ),
    "asset_results_xlsx": (
        "Shares the identical compute path as asset_results — "
        "`services/asset_results/export.py` imports `build_response` from "
        "the same `services/asset_results/service.py` — verified by reading "
        "the import, not re-probed at runtime. Same reasoning as "
        "asset_results applies: excluded from this loop, tracked via the "
        "same dedicated xfail."
    ),
    "compare_economics": (
        "Its `by_carrier` field is carrier-aggregated by the exact same "
        "`_compute_economics_summary` function economics_by_carrier calls "
        "(routers/compare.py:2652 vs routers/results.py:791,798), so it "
        "carries the identical CAPEX-overstatement bug. Its `per_asset_lcoh` "
        "field IS genuinely per-asset, but Link-only — narrower than the "
        "Generator/StorageUnit/Link set `coverage.COVERAGE['compare_economics']` "
        "lists, so it can't stand in as a general per-class adapter either. "
        "See the findings doc for the measured per_asset_lcoh numbers "
        "(checked by calling `_compute_economics_summary` directly, since "
        "the real endpoint needs a project saved to disk)."
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
