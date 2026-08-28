"""
COPT engine (Phase 2 Tasks 1–2): convolution core, hourly screening
adequacy, and the leave-one-out outage-attribution criticality.

Design: spec §§3.1, 3.3, 5.3; plan docs/superpowers/plans/
2026-08-28-fmea-phase2-copt.md. The test standard here is EXACT
hand-computed arithmetic on two-unit systems, not statistical assertions.

The canonical fixture: units 60 MW (q=0.1) and 40 MW (q=0.2), load 70 MW.
Capacity states: 0 (0.02), 40 (0.08), 60 (0.18), 100 (0.72).
  LOLP = P[cap < 70] = 0.28
  EUE/h = 0.02·70 + 0.08·30 + 0.18·10 = 5.6 MWh
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C

Q1, CAP1 = 0.1, 60.0
Q2, CAP2 = 0.2, 40.0
LOAD = 70.0
EXACT_LOLP = 0.28
EXACT_EUE_H = 5.6


def _units():
    return [C.CoptUnit(name="u1", capacity_mw=CAP1, q=Q1),
            C.CoptUnit(name="u2", capacity_mw=CAP2, q=Q2)]


# ── the convolution ───────────────────────────────────────────────────────

def test_two_unit_distribution_is_exact():
    dist = C.build_copt(_units(), delta_mw=1.0)
    # {state_mw: probability}; probabilities over the four states.
    assert dist.probability_of(0.0) == pytest.approx(Q1 * Q2)
    assert dist.probability_of(40.0) == pytest.approx(Q1 * (1 - Q2))
    assert dist.probability_of(60.0) == pytest.approx((1 - Q1) * Q2)
    assert dist.probability_of(100.0) == pytest.approx((1 - Q1) * (1 - Q2))
    assert dist.total_probability == pytest.approx(1.0)


def test_survival_function():
    dist = C.build_copt(_units(), delta_mw=1.0)
    # P[available ≥ 70] = P[100] = 0.72; P[available ≥ 41] = P[60]+P[100].
    assert dist.survival(LOAD) == pytest.approx(1 - EXACT_LOLP)
    assert dist.survival(41.0) == pytest.approx((1 - Q1) * Q2 + (1 - Q1) * (1 - Q2))
    assert dist.survival(0.0) == pytest.approx(1.0)


def test_rounding_apportions_and_preserves_the_mean():
    """A 2.5 MW unit on a 1 MW grid must split between the 2 and 3 MW
    states so the expected available capacity is exactly (1−q)·2.5."""
    dist = C.build_copt([C.CoptUnit(name="frac", capacity_mw=2.5, q=0.1)],
                        delta_mw=1.0)
    assert dist.mean() == pytest.approx(0.9 * 2.5)
    assert dist.probability_of(2.0) == pytest.approx(0.45)
    assert dist.probability_of(3.0) == pytest.approx(0.45)


def test_empty_fleet_has_zero_capacity():
    dist = C.build_copt([], delta_mw=1.0)
    assert dist.survival(0.1) == 0.0
    assert dist.probability_of(0.0) == pytest.approx(1.0)


# ── hourly adequacy ───────────────────────────────────────────────────────

def _residual(values, weight=3.0):
    idx = pd.date_range("2030-01-01", periods=len(values), freq="h")
    return pd.Series(values, index=idx), pd.Series(weight, index=idx)


def test_hourly_adequacy_matches_the_exact_case():
    dist = C.build_copt(_units(), delta_mw=1.0)
    residual, w = _residual([LOAD, LOAD])
    out = C.hourly_adequacy(dist, residual, weights=w)
    assert out["lole_hours"] == pytest.approx(EXACT_LOLP * 3.0 * 2)
    assert out["eue_mwh"] == pytest.approx(EXACT_EUE_H * 3.0 * 2)
    assert out["lolp_max"] == pytest.approx(EXACT_LOLP)
    assert out["by_period"]["ALL"]["lole_hours"] == pytest.approx(out["lole_hours"])


def test_hourly_adequacy_zero_and_negative_residual_hours_are_safe():
    dist = C.build_copt(_units(), delta_mw=1.0)
    residual, w = _residual([0.0, -5.0, LOAD])
    out = C.hourly_adequacy(dist, residual, weights=w)
    # Only the third hour contributes.
    assert out["lole_hours"] == pytest.approx(EXACT_LOLP * 3.0)
    assert out["eue_mwh"] == pytest.approx(EXACT_EUE_H * 3.0)


def test_hourly_adequacy_splits_by_period():
    dist = C.build_copt(_units(), delta_mw=1.0)
    base = pd.date_range("2030-01-01", periods=2, freq="h")
    idx = pd.MultiIndex.from_product([[2030, 2035], base],
                                     names=["period", "timestep"])
    residual = pd.Series([LOAD, LOAD, 0.0, LOAD], index=idx)
    w = pd.Series(3.0, index=idx)
    out = C.hourly_adequacy(dist, residual, weights=w)
    assert out["by_period"][2030]["lole_hours"] == pytest.approx(EXACT_LOLP * 6.0)
    assert out["by_period"][2035]["lole_hours"] == pytest.approx(EXACT_LOLP * 3.0)


# ── fleet membership + residual load from a network ───────────────────────

def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas"); n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "b_h2", carrier="H2")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Load", "l_h2", bus="b_h2", p_set=30.0)
    # Two-state COPT units: explicit occurrence data.
    n.add("Generator", "thermal1", bus="b", carrier="gas", p_nom=CAP1,
          outage_rate_value=Q1, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "thermal2", bus="b", carrier="gas", p_nom=CAP2,
          outage_rate_value=Q2, outage_rate_basis="EFORd", mttr_hours=40.0)
    # Must-take: wind has NO occurrence data (and deliberately no library
    # default) → netted from load at its hourly availability.
    n.add("Generator", "windfarm", bus="b", carrier="wind", p_nom=50.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"windfarm": [0.6, 0.0]}, index=n.snapshots)
    # An H2 generator must be excluded entirely (electrical scope).
    n.add("Generator", "electrolyser_h2", bus="b_h2", carrier="gas", p_nom=10.0,
          outage_rate_value=0.1, outage_rate_basis="FOR", mttr_hours=10.0)
    return n


def test_fleet_and_residual_apply_the_membership_rule():
    units, residual, w = C.fleet_and_residual(_network())
    names = sorted(u.name for u in units)
    assert names == ["thermal1", "thermal2"], names
    # Residual = 100 − wind availability (0.6·50=30, then 0): [70, 100].
    assert list(residual.values) == pytest.approx([70.0, 100.0])
    assert list(w.values) == pytest.approx([3.0, 3.0])


def test_fleet_uses_carrier_defaults_where_asset_data_is_absent():
    n = _network()
    n.add("Generator", "coalplant", bus="b", carrier="coal", p_nom=20.0)
    units, _, _ = C.fleet_and_residual(n)
    from services.adequacy.occurrence import CARRIER_DEFAULTS
    coal = next(u for u in units if u.name == "coalplant")
    assert coal.q == pytest.approx(CARRIER_DEFAULTS["coal"].rate)
