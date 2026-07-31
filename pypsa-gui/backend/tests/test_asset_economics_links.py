"""
`/api/results/asset_economics` must report Links, not only Generator /
StorageUnit / Store.

**Reported by the user, 2026-07-31:** "check the 4_nodes_system why the
economics for electrolyzers are all 0 or missing". Measured against their
saved project, the endpoint returned

    TOP-LEVEL KEYS: ['currency', 'is_multi_period', 'periods',
                     'generators', 'storage_units', 'stores']

with no `links` key at all. An electrolyser is a Link, so it could never
appear in the Economics table however its costs were configured — and the
same hole hides heat pumps and every other P2X converter. `Economics.tsx`
mirrors the omission, mapping only those three collections.

**Sign convention (user decision).** A Link BUYS energy at bus0 and SELLS at
bus1, so `revenue_eur` is the NET of the two: value delivered at bus1 minus
electricity bought at bus0. That makes `net_profit_eur` mean the same thing it
does for a generator — revenue less CAPEX and VOM — and matches how the LCOH
panel already treats the electricity input as a cost. The gross terms stay
available as their own fields so the netting is auditable rather than implied.

The network here is hand-built rather than solved: every quantity below is
chosen so the expected euro figures can be written down in the assertions
instead of being read back from whatever the solver happened to produce.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest


def _solved_link_network() -> pypsa.Network:
    """
    Two buses, one Link, two snapshots, no solver.

    Chosen so every term is exact:

        p0 (elec in)     = [100, 200] MW      price at elec = 50 €/MWh
        efficiency       = 0.5
        p1 (h2 out)      = [-50, -100] MW     price at h2   = 200 €/MWh
        marginal_cost    = 10 €/MWh of INPUT
        capital_cost     = 1000 €/MW/yr, p_nom_opt = 200 MW

        gross revenue  = (50 + 100) × 200          = 30_000
        electricity    = (100 + 200) × 50          = 15_000
        net revenue    = 30_000 − 15_000           = 15_000
        vom            = (100 + 200) × 10          =  3_000
        fixed          = 1000 × 200                = 200_000
        net profit     = 15_000 − 3_000 − 200_000  = −188_000
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "elec")
    n.add("Bus", "h2", carrier="H2")
    n.add("Carrier", "H2")
    n.add(
        "Link", "Electrolyzer 1",
        bus0="elec", bus1="h2", carrier="H2",
        efficiency=0.5, p_nom=200.0, marginal_cost=10.0, capital_cost=1000.0,
    )
    # A generator keeps the dispatch-freshness gate honest: it checks every
    # component class, so a network with links_t populated and generators_t
    # empty is not "fresh".
    n.add("Bus", "spare")
    n.add("Generator", "G", bus="elec", p_nom=500.0, marginal_cost=50.0)

    sns = n.snapshots
    n.links["p_nom_opt"] = 200.0
    n.links_t["p0"] = pd.DataFrame({"Electrolyzer 1": [100.0, 200.0]}, index=sns)
    # PyPSA's sign convention: p1 is NEGATIVE when the link delivers to bus1.
    n.links_t["p1"] = pd.DataFrame({"Electrolyzer 1": [-50.0, -100.0]}, index=sns)
    n.generators["p_nom_opt"] = 500.0
    n.generators_t["p"] = pd.DataFrame({"G": [100.0, 200.0]}, index=sns)
    n.buses_t["marginal_price"] = pd.DataFrame(
        {"elec": [50.0, 50.0], "h2": [200.0, 200.0], "spare": [0.0, 0.0]},
        index=sns,
    )
    # `is_solved` is a read-only property deriving from the objective, so mark
    # the network solved the way PyPSA itself does rather than by assignment.
    n._objective = 0.0
    return n


@pytest.fixture()
def economics(monkeypatch):
    """Run the endpoint against the hand-built network."""
    import routers.results as R
    from services.pypsa_service import PyPSAService

    n = _solved_link_network()
    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        yield R.get_asset_economics()
    finally:
        ctx.network = previous


def test_links_are_reported_at_all(economics):
    """The regression itself: there was no `links` key in the response."""
    assert "links" in economics, (
        "asset_economics still reports only generators/storage_units/stores — "
        "an electrolyser is a Link and cannot appear in the Economics table"
    )
    names = [row["name"] for row in economics["links"]]
    assert names == ["Electrolyzer 1"]


def test_revenue_nets_the_electricity_bought_against_the_output_sold(economics):
    """
    30_000 delivered at h2 − 15_000 bought at elec = 15_000 net.

    The number that would appear if the netting were dropped is 30_000, and if
    the sign of p1 were mishandled it would be −30_000 — both far outside this
    band, so the assertion discriminates all three.
    """
    row = economics["links"][0]

    assert row["revenue_eur"] == pytest.approx(15_000.0)
    assert row["gross_revenue_eur"] == pytest.approx(30_000.0)
    assert row["input_cost_eur"] == pytest.approx(15_000.0)


def test_costs_and_net_profit_follow_the_generator_convention(economics):
    row = economics["links"][0]

    assert row["vom_cost_eur"] == pytest.approx(3_000.0)
    assert row["fixed_cost_eur"] == pytest.approx(200_000.0)
    # revenue − fixed − vom, exactly as the generator block computes it.
    assert row["net_profit_eur"] == pytest.approx(-188_000.0)


def test_energy_is_the_output_not_the_input(economics):
    """
    A Link's useful product is what leaves bus1. Reporting `p0` here would
    overstate energy by 1/efficiency (300 instead of 150) and halve the
    implied unit cost.
    """
    row = economics["links"][0]

    assert row["energy_mwh"] == pytest.approx(150.0)
    assert row["p_nom_opt_mw"] == pytest.approx(200.0)


def test_unit_cost_is_all_in_per_mwh_of_output(economics):
    """
    (fixed + vom + energy bought) / output = (200_000 + 3_000 + 15_000) / 150.

    The bought energy belongs in the numerator: it is what the converter had
    to purchase to make the output. Excluding it is not a rounding difference —
    on the user's real electrolyser it reported €43.74/MWh against the LCOH
    panel's €246.02 for the same asset, and this endpoint must agree with
    `/results/lcoh` term for term.
    """
    row = economics["links"][0]

    assert row["lcoe_eur_per_mwh"] == pytest.approx(218_000.0 / 150.0)
    # The excluding-input value, asserted as NOT the answer so a revert to it
    # fails here rather than silently reintroducing the 5.6x disagreement.
    assert row["lcoe_eur_per_mwh"] != pytest.approx(203_000.0 / 150.0)


def test_a_multi_output_link_counts_every_port_it_delivers_to():
    """
    A CHP delivers electricity at bus1 AND heat at bus2. Counting only bus1
    drops half its product and inflates the unit cost by the same factor.

    Each port is valued at ITS OWN bus price:
        bus1  60 MWh @ 100 = 6_000
        bus2  40 MWh @  20 =   800
        gross                7_000... no: 6_800
        input 100 MWh @ 30 = 3_000  ->  net 3_800
        energy = 60 + 40   =   100
    """
    import routers.results as R
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=1, freq="h"))
    for b in ("gas", "elec", "heat", "spare"):
        n.add("Bus", b)
    n.add("Link", "CHP", bus0="gas", bus1="elec", bus2="heat",
          efficiency=0.6, efficiency2=0.4, p_nom=100.0, capital_cost=0.0)
    n.add("Generator", "G", bus="gas", p_nom=500.0)

    sns = n.snapshots
    n.links["p_nom_opt"] = 100.0
    n.links_t["p0"] = pd.DataFrame({"CHP": [100.0]}, index=sns)
    n.links_t["p1"] = pd.DataFrame({"CHP": [-60.0]}, index=sns)
    n.links_t["p2"] = pd.DataFrame({"CHP": [-40.0]}, index=sns)
    n.generators["p_nom_opt"] = 500.0
    n.generators_t["p"] = pd.DataFrame({"G": [100.0]}, index=sns)
    n.buses_t["marginal_price"] = pd.DataFrame(
        {"gas": [30.0], "elec": [100.0], "heat": [20.0], "spare": [0.0]}, index=sns,
    )
    n._objective = 0.0

    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        payload = R.get_asset_economics()
    finally:
        ctx.network = previous

    row = next(r for r in payload["links"] if r["name"] == "CHP")
    # 60 electricity + 40 heat. Bus1-only would report 60.
    assert row["energy_mwh"] == pytest.approx(100.0)
    # 6_000 + 800. Bus1-only would report 6_000.
    assert row["gross_revenue_eur"] == pytest.approx(6_800.0)
    assert row["input_cost_eur"] == pytest.approx(3_000.0)
    assert row["revenue_eur"] == pytest.approx(3_800.0)


def test_a_link_with_no_dispatch_is_skipped_rather_than_dividing_by_zero():
    """
    An idle Link has zero output, so any per-MWh figure is undefined. It must
    not surface as inf/NaN — `JSONResponse.render` refuses non-finite floats
    and turns the whole endpoint into a 500 (the documented `/results/*`
    failure mode in CLAUDE.md).
    """
    import routers.results as R
    from services.pypsa_service import PyPSAService

    n = _solved_link_network()
    n.links_t["p0"] = pd.DataFrame({"Electrolyzer 1": [0.0, 0.0]}, index=n.snapshots)
    n.links_t["p1"] = pd.DataFrame({"Electrolyzer 1": [0.0, 0.0]}, index=n.snapshots)

    ctx = PyPSAService._ensure_active()
    previous = ctx.network
    ctx.network = n
    try:
        payload = R.get_asset_economics()
    finally:
        ctx.network = previous

    for row in payload.get("links", []):
        for key, value in row.items():
            if isinstance(value, float):
                assert value == value, f"{key} is NaN"
                assert value not in (float("inf"), float("-inf")), f"{key} is infinite"
