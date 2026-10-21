"""
An extendable asset's bounds (IPP renewables + BESS ticket 03; gatekeeper, 2026-10-20): finite
`p_nom_min < p_nom_max`, or `p_nom_min == p_nom_max > 0` — an explicit forced size, built exactly at
that size and still carrying its capex. Equal bounds of 0 can never build and stay refused, as do
reversed and non-finite bounds.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from services.validation_service import _check_extendable_bounds


def _codes(lo, hi):
    df = pd.DataFrame({"p_nom_extendable": [True], "p_nom_min": [lo], "p_nom_max": [hi],
                       "capital_cost": [0.0], "overnight_cost": [1.0e6]}, index=["g"])
    return [i.code for i in _check_extendable_bounds(df, "Generator", "p_nom", True)]


def test_a_forced_size_passes():
    assert _codes(60.0, 60.0) == []


def test_an_ordinary_range_still_passes():
    assert _codes(0.0, 60.0) == []


@pytest.mark.parametrize("lo, hi", [(60.0, 50.0), (0.0, 0.0), (0.0, math.inf),
                                    (math.nan, 60.0), (60.0, math.nan)])
def test_reversed_zero_and_non_finite_bounds_are_refused(lo, hi):
    assert _codes(lo, hi) == ["generator_p_nom_bounds"]


def test_a_forced_size_still_needs_its_capital_cost():
    df = pd.DataFrame({"p_nom_extendable": [True], "p_nom_min": [60.0], "p_nom_max": [60.0],
                       "capital_cost": [0.0], "overnight_cost": [0.0]}, index=["g"])
    assert [i.code for i in _check_extendable_bounds(df, "Generator", "p_nom", True)] == \
        ["generator_no_capital_cost"]
