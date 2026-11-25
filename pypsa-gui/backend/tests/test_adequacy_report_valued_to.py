"""Issue 28: a multi-period study records the year its cost runs to, at solve time."""

import pandas as pd
import pypsa

from services.adequacy.report import _valued_to_year


def _net(periods, years):
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([periods, [0, 1]]))
    n.investment_periods = periods
    n.investment_period_weightings["years"] = years
    return n


def test_the_cost_runs_from_the_first_period_over_the_weighting_years():
    assert _valued_to_year(_net([2027, 2030, 2035], [3, 5, 10])) == 2045


def test_a_single_period_network_says_nothing():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    assert _valued_to_year(n) is None
