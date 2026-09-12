"""
`/asset_economics` must price on the SAME merit-order-corrected duals as
`corrected_marginal_prices`.

`get_asset_economics` carries a third copy of the curtailment-subsidy
correction (after `corrected_marginal_prices` and the one removed from
`get_prices` in 02b5e806). Unlike the `get_prices` copy it implements BOTH
branches, so it is a maintenance duplicate rather than a live drift — but
three copies of a rule this subtle is how the `get_prices` drift happened in
the first place.

This test pins the OBSERVABLE consequence — the revenue a subsidised
renewable is credited with — so the collapse to the shared helper is
provably behaviour-preserving, and so a future edit to one copy alone is
caught.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.results as R
from services.pypsa_service import PyPSAService

REAL_MC = 0.0
SUBSIDY = 30.0
# Below REAL_MC - SUBSIDY = -30, outside the 1 EUR/MWh dual_tol, so ONLY the
# at-ceiling branch can correct it.
DUAL_BELOW_EFFECTIVE = -50.0
PV_OUTPUT = 100.0
N_SNAPSHOTS = 2


def _network() -> pypsa.Network:
    """A subsidised renewable at full output against a depressed dual."""
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h")
    sns.name = "snapshot"
    n.set_snapshots(sns)

    n.add("Bus", "B", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "solar")
    n.add("Generator", "PV", bus="B", carrier="solar", p_nom=PV_OUTPUT,
          marginal_cost=REAL_MC, curtailment_cost=SUBSIDY, p_max_pu=1.0)
    n.add("Load", "L", bus="B", p_set=PV_OUTPUT)

    n.generators["p_nom_opt"] = PV_OUTPUT
    n.generators_t.p = pd.DataFrame(
        {"PV": [PV_OUTPUT] * N_SNAPSHOTS}, index=sns)
    n.buses_t.marginal_price = pd.DataFrame(
        {"B": [DUAL_BELOW_EFFECTIVE] * N_SNAPSHOTS}, index=sns)
    n._objective = 0.0
    return n


@pytest.fixture
def payload():
    n = _network()
    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        yield R.get_asset_economics(), n
    finally:
        ctx.network = previous


def test_subsidised_renewable_is_credited_at_its_corrected_price(payload):
    """
    Revenue must reflect the CORRECTED price (0), not the raw dual (-50).

    Uncorrected this reads -10 000 EUR: the generator appears to pay to
    produce, which is the LP-accounting artefact the correction exists to
    remove.
    """
    data, _ = payload
    row = next(r for r in data["generators"] if r["name"] == "PV")
    assert row["revenue_eur"] == pytest.approx(
        REAL_MC * PV_OUTPUT * N_SNAPSHOTS), (
        f"PV priced on the raw dual, not the merit-order-corrected one: "
        f"{row['revenue_eur']}"
    )


def test_asset_economics_prices_match_the_shared_helper(payload):
    """
    The durable guard: whatever the correction does, both surfaces agree.

    Asserts against the helper rather than a constant, so it stays true
    however the rule is later changed — the property that would have caught
    the `get_prices` drift years earlier.
    """
    data, n = payload
    shared = R.corrected_marginal_prices(n)
    row = next(r for r in data["generators"] if r["name"] == "PV")

    expected = sum(
        float(n.generators_t.p.at[t, "PV"]) * float(shared.at[t, "B"])
        for t in n.snapshots
    )
    assert row["revenue_eur"] == pytest.approx(expected), (
        "/asset_economics and corrected_marginal_prices disagree on the "
        "price used — the merit-order correction has drifted"
    )
