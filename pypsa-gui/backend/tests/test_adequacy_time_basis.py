"""
The reported time basis must be DERIVED, not asserted.

LOLE and EUE are per-year quantities by convention and every reliability
standard is written that way. The engines sum over whatever horizon the model
spans, weighted, so the two coincide only when Σ(weights) is a year.

`time_basis` used to be the hardcoded string "hours_per_year" at both call
sites, which put an identical label on 80.86 (hours per WEEK, a 168 h horizon
at default weightings) and on 4216.05 (the same system genuinely annualised).
The arithmetic was right — LOLE scales by exactly Σ(weights) — but the label
claimed a basis the number did not have, and the error runs the dangerous
way: a short horizon makes a system look far MORE reliable than it is.

Found by an end-to-end QA run that set the weightings both ways and got the
same label back.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.adequacy.metrics import (
    HOURS_PER_YEAR,
    NYEARS_TOLERANCE,
    horizon_years,
    resolve_time_basis,
)

H = 168


def _week(weight: float) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=H, freq="h"))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=80.0, marginal_cost=10.0)
    return n


def test_a_representative_week_at_unit_weights_is_not_a_year():
    n = _week(1.0)
    ny = horizon_years(n)
    assert ny == pytest.approx(H / HOURS_PER_YEAR)          # ≈ 0.019
    assert resolve_time_basis(ny) == "hours_per_horizon"


def test_the_same_week_annualised_reports_a_year():
    """The preflight warning's own remedy: weight the week up to 8760 h."""
    n = _week(HOURS_PER_YEAR / H)                            # 52.142857…
    ny = horizon_years(n)
    assert ny == pytest.approx(1.0)
    assert resolve_time_basis(ny) == "hours_per_year"


def test_a_full_hourly_year_reports_a_year():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8760, freq="h"))
    assert resolve_time_basis(horizon_years(n)) == "hours_per_year"


def test_the_investment_period_years_multiplier_counts():
    """
    `snapshot_weights` folds in `investment_period_weightings.years`. Omitting
    it is the "~5x too small" bug class period_utils exists to prevent, and it
    would silently mislabel a multi-period horizon as sub-annual.
    """
    n = pypsa.Network()
    sns = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=H, freq="h")])
    n.set_snapshots(sns)
    n.investment_periods = [2030, 2040]
    # each period's week weighted to half a year, x2 periods = one year
    n.snapshot_weightings.loc[:, :] = HOURS_PER_YEAR / H / 2
    assert horizon_years(n) == pytest.approx(1.0)
    assert resolve_time_basis(horizon_years(n)) == "hours_per_year"


@pytest.mark.parametrize("nyears, expected", [
    (1.0, "hours_per_year"),
    (1.0 + NYEARS_TOLERANCE / 2, "hours_per_year"),
    (8784 / HOURS_PER_YEAR, "hours_per_year"),      # a leap year, +0.27%
    (1.0 + NYEARS_TOLERANCE * 2, "hours_per_horizon"),
    (0.5, "hours_per_horizon"),                     # half a year is NOT a year
    (5.0, "hours_per_horizon"),
])
def test_the_tolerance_admits_rounding_but_not_a_different_horizon(nyears, expected):
    assert resolve_time_basis(nyears) == expected


def test_an_unknown_horizon_never_claims_a_year():
    """
    Claiming an annual basis is the failure that lets a number be read against
    a statutory standard, so every degenerate case resolves the safe way.
    """
    for bad in (0.0, -1.0, float("nan")):
        assert resolve_time_basis(bad) == "hours_per_horizon"
    # A bare network is not snapshotless — PyPSA seeds one "now" snapshot at
    # weight 1.0, so the horizon is 1/8760 of a year rather than zero. Either
    # way it must not raise and must not claim an annual basis.
    bare = horizon_years(pypsa.Network())
    assert 0.0 <= bare < NYEARS_TOLERANCE
    assert resolve_time_basis(bare) == "hours_per_horizon"
