"""
P30 (B5, deferred spec 2026-09-28 §5.3) — the `gen_zero_costs` warning.

A generator with capital, overnight and marginal cost all 0 leaves the LP
indifferent between it and any other free unit, so the preflight warns. Two
kinds of generator are not "cost-less" in that sense and are exempt:

  (i)  a generator whose marginal cost is a time series
       (`generators_t.marginal_cost` has its column) — the static 0 is a
       placeholder, the series is the price;
  (ii) a generator with a `generators_t.p_max_pu` profile (a variable
       renewable) — its output is fixed by the weather, not chosen by price.

Everything else still warns, including a fixed dispatchable unit at zero cost
(an indeterminate dispatch). The message is unchanged.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import validation_service as VS
from services.solver_service import SolverConfig

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b", v_nom=20.0, carrier="AC")
    n.add("Load", "d", bus="b", p_set=5.0)
    # A priced unit, so the network is not degenerate and never warns itself.
    n.add("Generator", "priced", bus="b", p_nom=10.0, marginal_cost=50.0)
    return n


def _codes(n) -> list[str]:
    return [i.code for i in VS.validate_for_run(n, SolverConfig())]


def _static_zero(n) -> set[str]:
    g = n.generators
    oc = g["overnight_cost"].fillna(0) if "overnight_cost" in g.columns else 0
    return set(g.index[(g["capital_cost"].fillna(0) == 0)
                       & (g["marginal_cost"].fillna(0) == 0) & (oc == 0)])


def test_the_priced_baseline_does_not_warn():
    assert "gen_zero_costs" not in _codes(_net())


def test_a_marginal_cost_series_is_not_zero_cost():
    n = _net()
    n.add("Generator", "grid", bus="b", p_nom=20.0, marginal_cost=0.0)
    n.generators_t.marginal_cost["grid"] = pd.Series([40.0, 60.0, 80.0, 50.0], n.snapshots)
    assert "gen_zero_costs" not in _codes(n)


def test_a_profiled_renewable_is_not_zero_cost():
    n = _net()
    n.add("Generator", "pv", bus="b", p_nom=4.0, marginal_cost=0.0,
          p_max_pu=pd.Series([0.0, 0.4, 0.8, 0.2], n.snapshots))
    assert "pv" in n.generators_t.p_max_pu.columns
    assert "gen_zero_costs" not in _codes(n)


def test_a_fixed_dispatchable_unit_at_zero_cost_still_warns():
    n = _net()
    n.add("Generator", "free_gas", bus="b", p_nom=10.0, marginal_cost=0.0)
    issues = [i for i in VS.validate_for_run(n, SolverConfig()) if i.code == "gen_zero_costs"]
    assert len(issues) == 1
    # Message unchanged (pinned so the exemption does not reword it).
    assert issues[0].message == (
        "1 generator(s) have capital_cost, overnight_cost, and marginal_cost "
        "all == 0. Result will be indeterminate.")
    assert issues[0].severity == "warning"


def test_an_extendable_unit_at_zero_cost_still_warns():
    n = _net()
    n.add("Generator", "free_new", bus="b", p_nom=0.0, p_nom_extendable=True,
          marginal_cost=0.0, capital_cost=0.0)
    assert "gen_zero_costs" in _codes(n)


def test_a_static_p_max_pu_is_not_a_profile():
    """Only a time-series column exempts; a static p_max_pu < 1 does not."""
    n = _net()
    n.add("Generator", "derated", bus="b", p_nom=10.0, marginal_cost=0.0, p_max_pu=0.5)
    assert "derated" not in n.generators_t.p_max_pu.columns
    assert "gen_zero_costs" in _codes(n)


def test_only_the_exempt_units_are_dropped_from_the_count():
    n = _net()
    n.add("Generator", "pv", bus="b", p_nom=4.0,
          p_max_pu=pd.Series(np.linspace(0, 1, 4), n.snapshots))
    n.add("Generator", "free_a", bus="b", p_nom=1.0)
    n.add("Generator", "free_b", bus="b", p_nom=1.0)
    issues = [i for i in VS.validate_for_run(n, SolverConfig()) if i.code == "gen_zero_costs"]
    assert len(issues) == 1 and issues[0].message.startswith("2 generator(s)")
    assert sorted(VS._zero_cost_generators(n)) == ["free_a", "free_b"]


# The seven the spec-review probe found (scratchpad/qaspec2/probe_b5.py):
# every template generator with all three static costs at 0.
_EXEMPT = {
    "eh_datacenter": {"grid_supply", "rooftop_pv"},
    "eh_h2_hub": {"grid_supply", "wind_farm", "solar_park"},
    "eh_microgrid": {"pv_plant", "wind_turbines"},
}


@pytest.mark.parametrize("tid", sorted(T.BUILDERS))
def test_gen_zero_costs_exemptions_cover_exactly_the_template_generators(tid):
    n = T.BUILDERS[tid]()
    static = _static_zero(n)
    assert static == _EXEMPT[tid]
    still = set(VS._zero_cost_generators(n))
    assert static - still == _EXEMPT[tid]
    assert still == set()
