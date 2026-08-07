"""
Myopic freezes capacity after the first period — the user must be told.

`_freeze_period_capacities` pins every extendable asset ACTIVE in a period to
`p_nom = p_nom_opt, extendable = False` once that period solves. An asset left
at the default `build_year = 0` is active in every period, so the FIRST
iteration freezes it and no later period can add to it. The run still reports
`optimal`; growing demand is simply absorbed by unserved energy.

Measured on a 3-period system with +44% demand growth: gas froze at 977 MW and
unserved energy ran 47 -> 1 756 -> 5 183 MWh. With per-period vintage bounds the
same asset built 977 / +195 / +234 MW and unserved stayed 47 -> 56 -> 68.

Freezing is legitimate when the user means "decide the fleet once, then operate
it", so this is a warning, not a block.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from services.solver_service import SolverConfig
from services.validation_service import validate_for_run

CODE = "myopic_capacity_locked_after_first_period"
PERIODS = [2030, 2035, 2040]


def _network(periods=PERIODS, extendable=True) -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [periods, pd.date_range("2030-01-01", periods=3, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = periods
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=100.0)
    n.add("Generator", "GAS", bus="B", carrier="gas",
          p_nom_extendable=extendable, p_nom=500.0,
          capital_cost=120.0, marginal_cost=10.0, p_nom_max=10_000.0)
    return n


def _cfg(periods=PERIODS) -> SolverConfig:
    return SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                        investment_periods=periods)


def _codes(n, cfg) -> list[str]:
    return [i.code for i in validate_for_run(n, cfg)]


def test_warns_when_an_extendable_asset_cannot_expand_after_period_one():
    n = _network()
    issues = validate_for_run(n, _cfg())
    hit = [i for i in issues if i.code == CODE]
    assert hit, f"expected {CODE}, got {[i.code for i in issues]}"
    assert hit[0].severity == "warning", "freezing is a choice, not an error"
    assert "GAS" in hit[0].message
    assert "vintage bounds" in hit[0].message.lower()


def test_no_warning_when_the_asset_has_per_period_vintage_bounds():
    """The supported way to expand in more than one period."""
    n = _network()
    n.meta["vintage_bounds"] = {
        "Generator": {"GAS": {str(p): {"p_nom_min": 0.0, "p_nom_max": 5000.0}
                              for p in PERIODS}}
    }
    assert CODE not in _codes(n, _cfg())


def test_no_warning_when_nothing_is_extendable():
    """A fixed fleet has no expansion to lose."""
    assert CODE not in _codes(_network(extendable=False), _cfg())


def test_no_warning_on_a_single_period_horizon():
    """With one period there is no later period to starve."""
    one = [2030]
    assert CODE not in _codes(_network(periods=one), _cfg(one))


def test_no_warning_for_a_full_horizon_solve():
    """Perfect foresight never freezes — the check must not fire outside myopic."""
    n = _network()
    cfg = SolverConfig(solve_strategy="full", multi_investment_periods=True,
                       investment_periods=PERIODS)
    assert CODE not in _codes(n, cfg)


def test_an_asset_dated_into_a_later_period_is_not_reported_as_locked():
    """
    `build_year > first_period` means the asset is decided by ITS OWN iteration
    (that is what `_defer_future_vintage_builds` arranges), so it was never
    locked out and must not be listed.
    """
    n = _network()
    n.add("Generator", "GAS_LATE", bus="B", carrier="gas", p_nom_extendable=True,
          build_year=2035, capital_cost=120.0, marginal_cost=10.0,
          p_nom_max=10_000.0)
    hit = [i for i in validate_for_run(n, _cfg()) if i.code == CODE]
    assert hit, "the build_year=0 asset should still be reported"
    assert "GAS_LATE" not in hit[0].message
    assert "Generator:GAS" in hit[0].message


def test_the_warning_counts_covered_assets_separately():
    """A partially-configured network should say so rather than imply all-or-nothing."""
    n = _network()
    n.add("Generator", "PV", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=300.0, marginal_cost=0.1, p_nom_max=10_000.0)
    n.meta["vintage_bounds"] = {
        "Generator": {"PV": {str(p): {"p_nom_max": 5000.0} for p in PERIODS}}
    }
    hit = [i for i in validate_for_run(n, _cfg()) if i.code == CODE]
    assert hit
    assert "PV" not in hit[0].message.split(":")[0]
    assert "1 asset(s) DO have per-period bounds" in hit[0].message
