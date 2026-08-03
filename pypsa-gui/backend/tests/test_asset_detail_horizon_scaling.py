"""
Asset Detail costs are on the SAME time basis as the Economics tab.

`capex_annual` is a rate (EUR/a); revenue, VOM and energy are weighted sums
over the selected window. Asset Detail used to subtract the rate straight from
the sum, so on the user's real three-period project PV_B3 reported a net profit
of 98,324,594 against the Economics tab's 27,956,088 — high by exactly two
years of CAPEX — and a one-day window reported a 34.9 MEUR loss (one day of
revenue minus a full year of CAPEX).

The nine-surface audit in
`docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md` did
not catch this: §7 compared Asset Detail's `capex_annual` against
`asset_economics fixed_cost_eur / 15` — per-year against per-year — so the two
surfaces' identically-named `fixed_cost_eur` fields were never put side by
side. These tests are that missing comparison.
"""
from __future__ import annotations

import pytest

import routers.asset_results as AR
import routers.results as R
from services.asset_results import compute as C
from tests.conftest import build_network
from tests.golden import fixture as gf

# The golden network spans 2030 (×5 years) and 2035 (×10) — 15 modelled years.
HORIZON_YEARS = float(sum(gf.GOLDEN_YEARS))


@pytest.fixture()
def golden(reset_backend):
    """Solved golden network, installed after conftest's autouse reset."""
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _detail_scalars(cls: str, name: str, category: str = "economics", **kw):
    detail = AR.get_asset_results(
        component_class=cls, name=name, category=category, source="lopf",
        from_=kw.pop("from_", None), to=kw.pop("to", None),
        period=kw.pop("period", None), mode="chronological", metrics="",
    )
    return detail.get("scalars", {})


_BUCKETS = (("generators", "Generator"), ("links", "Link"),
            ("storage_units", "StorageUnit"))


def _pairs(econ, field):
    """(class, name, economics-tab value) for every asset reporting `field`."""
    for bucket, cls in _BUCKETS:
        for row in econ.get(bucket, []) or []:
            if row.get("name") is not None and row.get(field) is not None:
                yield cls, row["name"], row[field]


# ── the comparison the audit never made ─────────────────────────────────────

def test_fixed_cost_agrees_with_the_economics_tab_on_every_asset(golden):
    econ = R.get_asset_economics()
    checked = 0
    for cls, name, tab in _pairs(econ, "fixed_cost_eur"):
        panel = _detail_scalars(cls, name).get("fixed_cost_eur")
        if panel is None:
            continue
        assert panel == pytest.approx(tab, rel=1e-9), (
            f"{cls}/{name}: Asset Detail {panel} != Economics tab {tab}")
        checked += 1
    assert checked >= 4, f"only {checked} assets compared — fixture shrank?"


def test_net_profit_agrees_with_the_economics_tab_on_every_asset(golden):
    econ = R.get_asset_economics()
    checked = 0
    for cls, name, tab in _pairs(econ, "net_profit_eur"):
        panel = _detail_scalars(cls, name).get("net_profit_eur")
        if panel is None:
            continue
        assert panel == pytest.approx(tab, rel=1e-9), (
            f"{cls}/{name}: Asset Detail {panel} != Economics tab {tab}")
        checked += 1
    assert checked >= 3, f"only {checked} assets compared — fixture shrank?"


def test_the_electrolyzer_reports_the_oracle_verified_horizon_capex(golden):
    """
    A named, independently-derived number rather than a cross-surface tie:
    `oracle.horizon_capex` puts the electrolyzer at EUR 166,249.77 over the
    15-year horizon (see the findings doc's headline table). Two surfaces
    agreeing on a wrong number would pass the tests above; this one wouldn't.
    """
    assert _detail_scalars("Link", "electrolyzer").get("fixed_cost_eur") == \
        pytest.approx(166249.77136776928, rel=1e-9)


# ── additivity: the property the user asked to be checked ───────────────────

def test_per_period_fixed_costs_sum_to_the_whole_horizon(golden):
    whole = _detail_scalars("Generator", "solar")["fixed_cost_eur"]
    parts = [_detail_scalars("Generator", "solar", period=str(p))["fixed_cost_eur"]
             for p in gf.GOLDEN_PERIODS]
    assert sum(parts) == pytest.approx(whole, rel=1e-9)
    # And each period carries its OWN weighting, not an even split: 2030 is
    # worth 5 years and 2035 is worth 10, so the second is twice the first.
    assert parts[1] == pytest.approx(2.0 * parts[0], rel=1e-9)


def test_per_period_net_profits_sum_to_the_whole_horizon(golden):
    whole = _detail_scalars("Generator", "solar")["net_profit_eur"]
    parts = [_detail_scalars("Generator", "solar", period=str(p))["net_profit_eur"]
             for p in gf.GOLDEN_PERIODS]
    assert sum(parts) == pytest.approx(whole, rel=1e-9)


# ── the window factor itself ────────────────────────────────────────────────

def test_horizon_years_is_the_full_span_for_the_full_horizon(golden):
    ctx = C.build_ctx(golden, "Generator", "solar", source="lopf",
                      sns=golden.snapshots)
    assert C.horizon_years(ctx) == pytest.approx(HORIZON_YEARS)


def test_horizon_years_splits_by_each_periods_own_year_count(golden):
    for period, years in zip(gf.GOLDEN_PERIODS, gf.GOLDEN_YEARS):
        sns = golden.snapshots[golden.snapshots.get_level_values(0) == period]
        ctx = C.build_ctx(golden, "Generator", "solar", source="lopf", sns=sns)
        assert C.horizon_years(ctx) == pytest.approx(float(years))


def test_a_sub_window_is_pro_rated_rather_than_charged_a_whole_year(golden):
    """Half of one period's snapshots costs half of that period's CAPEX."""
    period = gf.GOLDEN_PERIODS[0]
    sns = golden.snapshots[golden.snapshots.get_level_values(0) == period]
    half = sns[: len(sns) // 2]
    ctx = C.build_ctx(golden, "Generator", "solar", source="lopf", sns=half)
    assert C.horizon_years(ctx) == pytest.approx(gf.GOLDEN_YEARS[0] / 2.0)


def test_a_flat_network_is_one_year_matching_the_economics_tab(golden):
    """
    `asset_economics` uses `total_years_factor = 1.0` on a flat network however
    many snapshots it has. A bare `Σweights / 8760` would read 24/8760 here and
    reintroduce the very disagreement this change removes.
    """
    n = build_network(solve=True)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.horizon_years(ctx) == pytest.approx(1.0)
    assert C.gen_fixed_cost(ctx) == pytest.approx(C.capex_annual(ctx))


# ── the rate must stay a rate ───────────────────────────────────────────────

def test_capex_annual_stays_annual(golden):
    """
    The sibling metric is labelled EUR/a and must NOT pick up the horizon
    factor — otherwise this fix trades one conflation for its mirror image.
    """
    annual = _detail_scalars("Generator", "solar", category="capacity")["capex_annual"]
    horizon = _detail_scalars("Generator", "solar")["fixed_cost_eur"]
    assert horizon == pytest.approx(annual * HORIZON_YEARS, rel=1e-9)
