"""Generator capacity + dispatch metrics, checked against direct frame reads."""
import pytest

from services.asset_results import compute as C
from tests.conftest import build_network


@pytest.fixture
def ctx():
    n = build_network(solve=True)
    return C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)


def test_p_matches_a_direct_read_of_the_dispatch_frame(ctx):
    got = C.gen_p(ctx)
    want = ctx.n.generators_t.p["gas"]
    assert list(got.values) == pytest.approx(list(want.values))


def test_available_is_optimised_capacity_times_availability(ctx):
    avail = C.gen_available(ctx)
    p_nom_opt = float(ctx.n.generators.at["gas", "p_nom_opt"])
    assert list(avail.values) == pytest.approx([p_nom_opt] * len(ctx.sns))


def test_curtailment_is_available_minus_dispatch_and_never_negative(ctx):
    curt = C.gen_curtailment(ctx)
    avail = C.gen_available(ctx)
    p = C.gen_p(ctx)
    assert list(curt.values) == pytest.approx(list((avail - p).clip(lower=0).values))
    assert (curt >= 0).all()


def test_capacity_factor_is_dispatch_over_optimised_capacity(ctx):
    cf = C.gen_capacity_factor(ctx)
    p_nom_opt = float(ctx.n.generators.at["gas", "p_nom_opt"])
    assert list(cf.values) == pytest.approx(list((C.gen_p(ctx) / p_nom_opt).values))


def test_capacity_factor_is_none_when_optimised_capacity_is_zero():
    n = build_network(solve=True)
    n.generators.loc["gas", "p_nom_opt"] = 0.0
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_capacity_factor(ctx) is None


def test_energy_applies_the_snapshot_weighting():
    n = build_network(solve=True, gens_weight=3.0)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    raw = float(n.generators_t.p["gas"].sum())
    assert C.gen_energy(ctx) == pytest.approx(raw * 3.0)


def test_full_load_hours_is_energy_over_optimised_capacity(ctx):
    flh = C.gen_full_load_hours(ctx)
    p_nom_opt = float(ctx.n.generators.at["gas", "p_nom_opt"])
    assert flh == pytest.approx(C.gen_energy(ctx) / p_nom_opt)


def test_peak_is_the_maximum_dispatch(ctx):
    assert C.gen_peak(ctx) == pytest.approx(float(ctx.n.generators_t.p["gas"].max()))


def test_zero_hours_counts_weighted_snapshots_at_zero_output():
    n = build_network(solve=True)
    ctx = C.build_ctx(n, "Generator", "solar", source="lopf", sns=n.snapshots)
    p = n.generators_t.p["solar"]
    expected = float((p.abs() < 1e-9).sum())
    assert C.gen_zero_hours(ctx) == pytest.approx(expected)


def test_ramp_metrics_read_consecutive_differences(ctx):
    diffs = ctx.n.generators_t.p["gas"].diff().dropna()
    assert C.gen_max_ramp_up(ctx) == pytest.approx(float(diffs.max()))
    assert C.gen_max_ramp_down(ctx) == pytest.approx(float(diffs.min()))


def test_capacity_scalars_read_the_static_columns(ctx):
    assert C.gen_p_nom(ctx) == pytest.approx(200.0)
    assert C.gen_p_nom_opt(ctx) == pytest.approx(
        float(ctx.n.generators.at["gas", "p_nom_opt"]))
    assert C.gen_p_nom_delta(ctx) == pytest.approx(
        C.gen_p_nom_opt(ctx) - C.gen_p_nom(ctx))


def test_capex_annual_is_capital_cost_times_optimised_capacity(ctx):
    assert C.gen_capex_annual(ctx) == pytest.approx(
        100_000.0 * float(ctx.n.generators.at["gas", "p_nom_opt"]))


def test_vintage_breakdown_is_none_on_a_flat_network(ctx):
    assert C.gen_p_nom_by_vintage(ctx) is None


def test_vintage_breakdown_sums_the_at_year_rows():
    n = build_network(solve=True)
    n.add("Generator", "gas@2030", bus="B1", carrier="gas", p_nom=0.0)
    n.generators.loc["gas@2030", "p_nom_opt"] = 80.0
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_p_nom_by_vintage(ctx) == {"2030": pytest.approx(80.0)}


def test_series_for_returns_none_for_an_absent_attribute(ctx):
    assert C.series_for(ctx, "no_such_attr") is None
