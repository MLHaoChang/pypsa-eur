"""
The Capacity tab's CAPEX must equal the Economics tab's for a network with a Store.

Closes the coverage gap recorded under "Known limitations" in
`docs/superpowers/findings/2026-08-03-compare-tab-correctness.md`:

    No fixture or real project available to this examination exercises a
    Store. `_compute_total_annuitised_capex` and `_compute_economics_summary`
    both walk `Store` [...] so the Store branch of either function's code
    path is read, not measured.

Same parity property as `test_compare_link_capex_parity.py`, on the one
component class neither the golden fixture nor `3_nodes_system` contains.
Stores are billed on `e_nom` (MWh of energy capacity), not `p_nom` — a walk
that reached for the power field would silently contribute zero here, which
is exactly the failure this pins.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from routers.compare import _compute_total_annuitised_capex, _periodized_lookup
from routers.results import get_cost_breakdown
from services.solver_service import SolverConfig

# 400 MWh x 25 000 EUR/MWh/a = 10 MEUR/a.
STORE_E_NOM = 400.0
STORE_CAPITAL_COST = 25_000.0
EXPECTED_STORE_CAPEX_MEUR = STORE_E_NOM * STORE_CAPITAL_COST / 1e6


def _network_with_costly_store() -> pypsa.Network:
    """One bus, one generator, one Load, and a Store that carries capex."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "A")
    n.add("Carrier", "gas")
    n.add("Carrier", "h2")
    n.add("Load", "L", bus="A", p_set=50.0)
    n.add("Generator", "g", bus="A", carrier="gas", p_nom=500.0,
          marginal_cost=10.0)
    # The asset under test: fixed size, real capital_cost, billed on e_nom.
    n.add("Store", "S", bus="A", carrier="h2", e_nom=STORE_E_NOM,
          e_nom_extendable=False, capital_cost=STORE_CAPITAL_COST)
    return n


def _capacity_tab_capex(n) -> float:
    pcc = _periodized_lookup(n)
    per_carrier = _compute_total_annuitised_capex(n, [], False, {}, pcc)
    return sum(v["total"] for v in per_carrier.values())


def test_a_store_with_capital_cost_is_counted_by_the_capacity_tab():
    """The Store branch charges e_nom x capital_cost, not zero."""
    n = _network_with_costly_store()
    n.optimize(solver_name="highs")

    total = _capacity_tab_capex(n)
    assert total == pytest.approx(EXPECTED_STORE_CAPEX_MEUR, rel=1e-6), (
        f"the Store's CAPEX is missing from or mis-scaled in the Capacity "
        f"tab: got {total}, expected {EXPECTED_STORE_CAPEX_MEUR}"
    )


def test_capacity_and_economics_agree_on_a_network_with_a_store(install_network):
    """
    The parity property itself — the one S1 was about, on the Store class.

    Economics is built from `n.statistics()`, which charges
    capital_cost x e_nom_opt for every Store. If the Capacity walk ever
    diverges (wrong nominal field, or Stores dropped) this catches it.
    """
    n = _network_with_costly_store()
    n.optimize(solver_name="highs")
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    payload = get_cost_breakdown()
    assert isinstance(payload, dict), "cost_breakdown did not return a payload"
    economics_capex = float(payload["capex"]) / 1e6  # EUR -> MEUR

    assert _capacity_tab_capex(n) == pytest.approx(economics_capex, rel=1e-6), (
        "Capacity and Economics disagree on a network containing a Store"
    )


def test_an_extendable_store_is_also_counted():
    """
    The optimiser sizes the Store, so CAPEX must follow `e_nom_opt`.

    Pins the other half: a walk that read the user-typed `e_nom` instead of
    the optimised value would pass the fixed-size test above and be wrong
    here.
    """
    n = _network_with_costly_store()
    n.stores.loc["S", "e_nom_extendable"] = True
    n.stores.loc["S", "e_nom_max"] = 1_000.0
    n.stores.loc["S", "capital_cost"] = 1.0
    # Storage only pays for itself if prices MOVE. With a flat marginal cost
    # and a flat load there is nothing to arbitrage and the optimiser builds
    # zero — which made this test vacuous until the `built > 0` guard below
    # caught it. Alternate cheap/expensive hours so shifting 1 MWh saves 99.
    n.generators_t.marginal_cost = pd.DataFrame(
        {"g": [1.0, 100.0, 1.0, 100.0]}, index=n.snapshots)
    n.optimize(solver_name="highs")

    built = float(n.stores.at["S", "e_nom_opt"])
    # Without this the test is vacuous: if the optimiser builds nothing, the
    # assertion below reduces to 0 == 0 and passes even when the Store walk
    # is reading the wrong nominal field. Found by mutation — swapping
    # `e_nom` for `p_nom` in the walk killed the other two tests in this
    # file but left this one green.
    assert built > 0.0, "the optimiser built no Store — test would be vacuous"
    expected = built * 1.0 / 1e6
    assert _capacity_tab_capex(n) == pytest.approx(expected, rel=1e-6, abs=1e-9)
