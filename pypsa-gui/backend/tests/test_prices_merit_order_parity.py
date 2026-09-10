"""
`/results/prices` must apply the SAME merit-order correction as the shared
`corrected_marginal_prices` helper.

`get_prices` grew its own inline copy of the curtailment-subsidy correction.
The copy implements only the first of the helper's two branches — "the LP dual
equals the renewable's effective MC" — and omits the second: a subsidised
renewable pinned AT its ceiling while the dual sits BELOW that effective MC.
So the Prices tab and every consumer of the shared helper
(`/asset_economics`, Compare's `_compute_prices_summary` /
`_compute_economics_summary`) could report different prices for the same
network.

Flagged as a latent drift risk in
`docs/superpowers/findings/2026-08-03-compare-tab-correctness.md`
("Known limitations") and left for a future pass. This is that pass.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.results as R
from services.pypsa_service import PyPSAService

# The subsidised renewable's real marginal cost, and its subsidy. The LP's
# effective MC is therefore REAL_MC - SUBSIDY = -30.0.
REAL_MC = 0.0
SUBSIDY = 30.0
EFFECTIVE_LP_MC = REAL_MC - SUBSIDY

# Dual well BELOW the effective MC — outside the helper's 1 EUR/MWh
# `dual_tol`, so branch one cannot fire and only the at-ceiling branch can.
DUAL_BELOW_EFFECTIVE = -50.0


def _install(n: pypsa.Network) -> None:
    PyPSAService.set_network(n)
    with PyPSAService._registry_lock:
        for k in [k for k in PyPSAService._contexts if k.startswith("scratch:")]:
            PyPSAService._contexts.pop(k, None)


@pytest.fixture
def subsidised_at_ceiling() -> pypsa.Network:
    """
    A subsidised renewable at full output, with the bus dual dragged below
    its effective LP marginal cost.

    Snapshot 0 is the at-ceiling case only the helper's second branch
    corrects. Snapshot 1 is a control: the dual sits exactly at the effective
    MC, which BOTH implementations already correct — so a test that went green
    everywhere would prove nothing about the missing branch.
    """
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=2, freq="h")
    sns.name = "snapshot"
    n.set_snapshots(sns)

    n.add("Bus", "B")
    n.add("Carrier", "solar")
    n.add("Carrier", "gas")
    n.add("Generator", "PV", bus="B", carrier="solar", p_nom=100.0,
          marginal_cost=REAL_MC, curtailment_cost=SUBSIDY, p_max_pu=1.0)
    n.add("Generator", "GAS", bus="B", carrier="gas", p_nom=100.0,
          marginal_cost=80.0)
    n.add("Load", "L", bus="B", p_set=100.0)

    n.generators["p_nom_opt"] = 100.0
    # PV pinned AT its ceiling (p_max_pu 1.0 x p_nom_opt 100) in both snapshots.
    n.generators_t.p = pd.DataFrame(
        {"PV": [100.0, 100.0], "GAS": [0.0, 0.0]}, index=sns)
    n.buses_t.marginal_price = pd.DataFrame(
        {"B": [DUAL_BELOW_EFFECTIVE, EFFECTIVE_LP_MC]}, index=sns)
    # `is_solved` is a read-only property deriving from the objective.
    n._objective = 0.0

    _install(n)
    return n


def test_at_ceiling_dual_below_effective_mc_is_corrected(subsidised_at_ceiling):
    """
    Snapshot 0: PV is at its ceiling and the dual is below its effective MC.

    The shared helper restores the real marginal cost here; the inline copy in
    `get_prices` leaves the raw dual untouched because it only checks the
    dual-equals-effective-MC diagnostic. Fails before the fix with
    data_adjusted[0][0] == -50.0.
    """
    resp = R.get_prices()
    assert resp["data_adjusted"], "no adjusted prices for a solved network"
    assert resp["data_adjusted"][0][0] == pytest.approx(REAL_MC), (
        "a subsidised renewable at its ceiling did not get the merit-order "
        "correction — /results/prices is missing the shared helper's "
        "at-ceiling branch"
    )


def test_control_cell_is_corrected_by_both_implementations(subsidised_at_ceiling):
    """
    Snapshot 1 pins the shared half of the behaviour.

    Without this, the test above could be satisfied by a change that corrects
    everything unconditionally.
    """
    resp = R.get_prices()
    assert resp["data_adjusted"][1][0] == pytest.approx(REAL_MC)


def test_get_prices_adjusted_equals_the_shared_helper(subsidised_at_ceiling):
    """
    The durable guard: whatever the correction does, both surfaces must agree.

    This is the property the findings doc asked for — it stays true however
    either implementation is later edited, which a value-by-value assertion
    against hardcoded numbers would not.
    """
    resp = R.get_prices()
    shared = R.corrected_marginal_prices(subsidised_at_ceiling)

    buses = list(resp["columns"])
    for i in range(len(subsidised_at_ceiling.snapshots)):
        t = subsidised_at_ceiling.snapshots[i]
        for j, bus in enumerate(buses):
            assert resp["data_adjusted"][i][j] == pytest.approx(
                float(shared.at[t, bus])), (
                f"/results/prices and corrected_marginal_prices disagree at "
                f"({t}, {bus}) — the merit-order correction has drifted"
            )
