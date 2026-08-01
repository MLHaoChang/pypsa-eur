"""
Prices, economics and emissions — including reconciliation with the
existing /results/asset_economics endpoint.
"""
import pandas as pd
import pytest

from services.asset_results import compute as C
from tests.conftest import build_network


@pytest.fixture
def ctx():
    n = build_network(solve=True)
    return C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)


def test_bus_price_reads_the_generators_own_bus(ctx):
    got = C.gen_bus_price(ctx)
    want = ctx.n.buses_t.marginal_price["B1"]
    assert list(got.values) == pytest.approx(list(want.values))


def test_revenue_is_dispatch_times_price_times_weighting(ctx):
    p = ctx.n.generators_t.p["gas"]
    lam = ctx.n.buses_t.marginal_price["B1"]
    assert C.gen_revenue(ctx) == pytest.approx(float((p * lam).sum()))


def test_vom_is_absolute_dispatch_times_marginal_cost(ctx):
    p = ctx.n.generators_t.p["gas"]
    assert C.gen_vom(ctx) == pytest.approx(float(p.abs().sum()) * 50.0)


def test_fixed_cost_is_capital_cost_times_optimised_capacity(ctx):
    assert C.gen_fixed_cost(ctx) == pytest.approx(
        100_000.0 * float(ctx.n.generators.at["gas", "p_nom_opt"]))


def test_net_profit_is_revenue_minus_fixed_and_variable(ctx):
    assert C.gen_net_profit(ctx) == pytest.approx(
        C.gen_revenue(ctx) - (C.gen_fixed_cost(ctx) + C.gen_vom(ctx)))


def test_lcoe_is_total_cost_over_energy(ctx):
    assert C.gen_lcoe(ctx) == pytest.approx(
        (C.gen_fixed_cost(ctx) + C.gen_vom(ctx)) / C.gen_energy(ctx))


def test_lcoe_is_none_when_the_asset_produced_nothing():
    n = build_network(solve=True)
    n.generators_t.p["gas"] = 0.0
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_lcoe(ctx) is None


def test_capture_price_is_the_dispatch_weighted_mean_price(ctx):
    p = ctx.n.generators_t.p["gas"]
    lam = ctx.n.buses_t.marginal_price["B1"]
    assert C.gen_capture_price(ctx) == pytest.approx(
        float((p * lam).sum()) / float(p.sum()))


def test_capture_rate_compares_against_the_time_weighted_mean(ctx):
    lam = ctx.n.buses_t.marginal_price["B1"]
    assert C.gen_capture_rate(ctx) == pytest.approx(
        C.gen_capture_price(ctx) / float(lam.mean()))


def test_binding_hours_counts_weighted_snapshots_with_a_nonzero_dual():
    """
    Changed from a bare `.sum()` of the boolean mask (snapshot COUNT) to a
    weighted sum via `ctx.weights`, matching gen_zero_hours / full_load_hours
    / mean_capacity_factor — all four carry unit="h" and sit side by side as
    KPI cards, so they must measure hours the same way.

    The shared `ctx` fixture (weight=1.0, and mu_upper/mu_lower columns
    empty on the un-extended base network — PyPSA never assigns duals for a
    non-extendable, non-committable generator's simple bound by default)
    cannot discriminate weighted from unweighted, so this builds its own
    network: `snapshot_weightings.generators = 2.0` plus `assign_all_duals`
    and a varying load so gas (p_nom=100) actually binds on 2 of 4
    snapshots. 2 binding snapshots -> 2.0 unweighted vs 4.0 weighted.
    """
    n = build_network(solve=False, gens_weight=2.0)
    n.generators.loc["solar", "p_nom_extendable"] = True
    n.generators.loc["solar", "capital_cost"] = 1000.0
    n.generators.loc["gas", "p_nom"] = 100.0
    n.loads_t.p_set = pd.DataFrame(
        {"L1": [100.0, 150.0, 90.0, 40.0]}, index=n.snapshots)
    n.optimize(solver_name="highs", assign_all_duals=True)

    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    up = ctx.n.generators_t.mu_upper.get("gas")
    lo = ctx.n.generators_t.mu_lower.get("gas")
    binding = (up.abs() > 1e-9) | (lo.abs() > 1e-9)
    assert binding.any(), "fixture must actually produce a nonzero dual"

    unweighted = float(binding.sum())
    weighted = float((binding.astype(float) * ctx.weights).sum())
    assert weighted != pytest.approx(unweighted), (
        "fixture does not discriminate weighted from unweighted hours")
    assert C.gen_binding_hours(ctx) == pytest.approx(weighted)


def test_co2_rate_divides_by_efficiency():
    n = build_network(solve=False)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.generators.loc["gas", "efficiency"] = 0.5
    n.optimize(solver_name="highs")
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    rate = C.gen_co2_rate(ctx)
    want = n.generators_t.p["gas"] / 0.5 * 0.2
    assert list(rate.values) == pytest.approx(list(want.values))
    assert C.gen_co2_total(ctx) == pytest.approx(float(want.sum()))
    assert C.gen_co2_intensity(ctx) == pytest.approx(
        float(want.sum()) / C.gen_energy(ctx))


def test_economics_reconcile_with_the_asset_economics_endpoint(client, install_network):
    """Two implementations of one number must agree. See CLAUDE.md."""
    n = build_network(solve=True)
    install_network(n)
    rows = client.get("/api/results/asset_economics").json()["generators"]
    row = next(r for r in rows if r["name"] == "gas")
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_revenue(ctx) == pytest.approx(row["revenue_eur"], rel=1e-6)
    assert C.gen_vom(ctx) == pytest.approx(row["vom_cost_eur"], rel=1e-6)
    assert C.gen_fixed_cost(ctx) == pytest.approx(row["fixed_cost_eur"], rel=1e-6)
    assert C.gen_net_profit(ctx) == pytest.approx(row["net_profit_eur"], rel=1e-6)


def test_reconciles_when_a_subsidised_renewable_sets_the_price(
        client, install_network):
    """
    curtailment_cost drags the bus dual negative when a subsidised renewable
    is the price-setting unit — an LP artefact, not a real price. Both
    implementations must strip it via corrected_marginal_prices.

    A bare `n.optimize()` does NOT reproduce the distortion: curtailment_cost
    is extra functionality `solver_service` injects into the objective, not
    something PyPSA applies on its own. Measured: with the subsidy set but a
    plain solve, raw and corrected duals are bit-identical, so a fixture built
    that way cannot discriminate. The distorted dual is therefore written in
    directly — which is exactly the state a real GUI solve leaves behind.
    """
    n = build_network(solve=False)
    n.generators.loc["solar", "p_nom"] = 200.0     # so solar, not gas,
    n.generators.loc["solar", "p_max_pu"] = 1.0    # is the marginal unit
    n.generators.loc["solar", "curtailment_cost"] = 30.0
    n.optimize(solver_name="highs")
    n.buses_t.marginal_price["B1"] = -30.0
    install_network(n)

    from routers.results import corrected_marginal_prices
    # Guard: prove the fixture actually exercises the correction. Without this
    # the test can pass while asserting nothing — which is exactly what the
    # first version of it did.
    assert not corrected_marginal_prices(n)["B1"].equals(
        n.buses_t.marginal_price["B1"]
    ), "fixture does not trigger the merit-order correction"

    rows = client.get("/api/results/asset_economics").json()["generators"]
    row = next(r for r in rows if r["name"] == "solar")
    ctx = C.build_ctx(n, "Generator", "solar", source="lopf", sns=n.snapshots)
    assert C.gen_revenue(ctx) == pytest.approx(row["revenue_eur"], rel=1e-6)
    # …and that the agreement is not both sides reading the raw dual.
    raw_revenue = float((n.generators_t.p["solar"] * -30.0).sum())
    assert C.gen_revenue(ctx) != pytest.approx(raw_revenue, rel=1e-6)


def test_reconciles_with_a_time_varying_marginal_cost(client, install_network):
    """
    /results/asset_economics reads marginal_cost via
    get_switchable_as_dense. A static-only read here would understate VOM for
    any generator with a fuel-price profile.
    """
    import pandas as pd
    n = build_network(solve=False)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"gas": [40.0, 60.0, 80.0, 100.0]}, index=n.snapshots)
    n.optimize(solver_name="highs")
    install_network(n)
    rows = client.get("/api/results/asset_economics").json()["generators"]
    row = next(r for r in rows if r["name"] == "gas")
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_vom(ctx) == pytest.approx(row["vom_cost_eur"], rel=1e-6)
