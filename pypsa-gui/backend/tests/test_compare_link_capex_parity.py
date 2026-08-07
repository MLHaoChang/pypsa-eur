"""
The Capacity tab's CAPEX must equal the Economics tab's for the same network.

`_compute_total_annuitised_capex`'s docstring promises it "mirrors the
per-period aggregation in cost_breakdown". Economics is built from
`n.statistics()`, which charges capital_cost x p_nom_opt for EVERY asset. The
Capacity walk restricted links to extendable ones, so a NON-extendable link
carrying a capital_cost was counted by one view and not the other.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from routers.compare import _compute_total_annuitised_capex, _periodized_lookup
from routers.results import get_cost_breakdown
from services.solver_service import SolverConfig


def _network_with_fixed_costly_link() -> pypsa.Network:
    """Two buses joined by a NON-extendable link that still carries capex."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "A")
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Carrier", "dc")
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("Generator", "g", bus="A", carrier="gas", p_nom=500.0,
          marginal_cost=10.0)
    # The asset under test: fixed size, real capital_cost.
    n.add("Link", "DC", bus0="A", bus1="B", carrier="dc",
          p_nom=200.0, p_nom_extendable=False, efficiency=1.0,
          capital_cost=90_000.0)
    return n


def _capacity_tab_capex(n) -> float:
    pcc = _periodized_lookup(n)
    per_carrier = _compute_total_annuitised_capex(
        n, [], False, {}, pcc)
    return sum(v["total"] for v in per_carrier.values())


def test_a_fixed_link_with_capital_cost_is_counted_by_the_capacity_tab():
    n = _network_with_fixed_costly_link()
    n.optimize(solver_name="highs")

    total = _capacity_tab_capex(n)
    # 200 MW x 90 000 EUR/MW/a = 18 MEUR/a, reported in MEUR.
    assert total == pytest.approx(18.0, rel=1e-6), (
        f"the fixed link's CAPEX is missing from the Capacity tab: {total}"
    )


def test_capacity_and_economics_agree_on_the_same_network(install_network):
    n = _network_with_fixed_costly_link()
    n.optimize(solver_name="highs")
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    payload = get_cost_breakdown()
    assert isinstance(payload, dict), "cost_breakdown did not return a payload"
    economics_capex = float(payload["capex"]) / 1e6  # EUR -> MEUR

    assert _capacity_tab_capex(n) == pytest.approx(economics_capex, rel=1e-6)


def test_an_extendable_link_is_still_counted():
    """Guards against the fix over-reaching and dropping the extendable path."""
    n = _network_with_fixed_costly_link()
    n.links.loc["DC", "p_nom_extendable"] = True
    n.links.loc["DC", "p_nom_max"] = 1_000.0
    n.optimize(solver_name="highs")
    assert _capacity_tab_capex(n) > 0.0
