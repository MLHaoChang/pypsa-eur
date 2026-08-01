"""
The golden network must contain every shape that has actually produced a wrong
number, and must solve. If this file fails, every other golden test is
meaningless — so it asserts the fixture's composition, not just that it runs.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tests.golden import fixture as gf


@pytest.fixture(scope="module")
def solved():
    return gf.solve_golden_network()


def test_it_solves(solved):
    assert solved.is_solved


def test_it_is_multi_period_with_different_year_weightings(solved):
    # The 22% CAPEX gap appears only in multi-period, and only bites when the
    # periods carry DIFFERENT weights — equal weights hide an averaging bug.
    assert isinstance(solved.snapshots, pd.MultiIndex)
    assert list(solved.investment_periods) == list(gf.GOLDEN_PERIODS)
    years = solved.investment_period_weightings["years"].tolist()
    assert years == list(gf.GOLDEN_YEARS)
    assert len(set(years)) == 2, "equal year weights would hide the bug this exists to catch"


def test_the_overnight_shape_is_present_and_has_no_raw_capital_cost(solved):
    # The exact shape that made Asset Detail read 22-100% low: cost supplied
    # via overnight_cost, with capital_cost left at its 0.0 default.
    assert solved.generators.at["gas", "overnight_cost"] == 900_000.0
    assert solved.generators.at["gas", "capital_cost"] == 0.0
    assert solved.generators.at["gas", "p_nom_extendable"]


def test_the_direct_capital_cost_shape_is_present(solved):
    # The shape that already works. Proves a fix does not regress it.
    assert solved.lines.at["L_ab", "capital_cost"] == 1_000_000.0
    assert pd.isna(solved.lines.at["L_ab", "overnight_cost"])


def test_the_link_class_is_present(solved):
    # The class that was missing from /results/asset_economics entirely.
    assert "electrolyzer" in solved.links.index
    assert solved.links.at["electrolyzer", "efficiency"] == 0.7


def test_a_genuinely_zero_cost_asset_is_present(solved):
    # Proves a real zero still reports zero and is not flagged as unresolvable.
    assert solved.storage_units.at["bess", "capital_cost"] == 0.0
    assert pd.isna(solved.storage_units.at["bess", "overnight_cost"])


def test_both_extendable_and_non_extendable_are_present(solved):
    ext = solved.generators["p_nom_extendable"]
    assert ext.any() and not ext.all()


def test_install_survives_the_autouse_reset(solved):
    # conftest's reset_backend is autouse and calls reset_network() BEFORE every
    # test, so the fixture must re-install rather than rely on session state.
    from services.pypsa_service import PyPSAService
    import routers.simulation as sim_router

    gf.install_golden(solved)

    assert PyPSAService.get_network() is solved
    assert sim_router._state["solver_config"].discount_rate == gf.GOLDEN_DISCOUNT_RATE
