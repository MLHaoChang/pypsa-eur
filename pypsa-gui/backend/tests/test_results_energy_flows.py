"""
Energy flows (site commercial UX A1): Source → Via → Destination MWh.

The conservation test is the contract: generation = destinations (loads served,
export, losses) + Δ storage, on the three Energy Hub templates the owner reads,
on the multi-period golden network, and on a hand-built network where the
proportional-pool rule can be worked by hand.
"""
from __future__ import annotations

import csv
import io
import logging
import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.results.energy_flows import (
    CSV_HEADER, GRID_IMPORT, REL_TOL, csv_text, energy_flows,
)
from tests.golden import fixture as gf

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TEMPLATES = ["eh_datacenter", "eh_h2_hub", "eh_microgrid"]


def _solve(n, multi=False):
    logging.disable(logging.CRITICAL)
    try:
        if multi:
            n.snapshots.name = "snapshot"
        status, cond = n.optimize(solver_name="highs", multi_investment_periods=multi)
    finally:
        logging.disable(logging.NOTSET)
    assert (status, cond) == ("ok", "optimal")
    return n


def _by(rows, *keys):
    out: dict = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        out[k] = out.get(k, 0.0) + r["mwh"]
    return out


def _assert_conserves(p):
    c = p["conservation"]
    assert p["status"] == "ok", (p.get("reason"), p.get("flags"), c)
    assert abs(c["residual_mwh"]) <= c["tolerance_mwh"], c
    # the stated tolerance is tight: a millionth of the energy moved, plus a kWh
    assert c["tolerance_mwh"] <= REL_TOL * max(c["generation_mwh"], c["destinations_mwh"], 1.0) + 1e-3 + 1e-12
    # the rows are the destinations the check summed
    assert sum(r["mwh"] for r in p["rows"]) == pytest.approx(c["destinations_mwh"], rel=1e-9)
    assert not c["checks"] and not p["flags"]


@pytest.mark.parametrize("tid", TEMPLATES)
def test_template_flows_conserve_energy(tid):
    n = T.BUILDERS[tid]()
    probe = n.copy()
    from services.adequacy.energization import apply_energization
    apply_energization(probe)
    _solve(probe)
    p = energy_flows(probe)
    _assert_conserves(p)
    assert p["rows"], "a solved site moves energy"
    # every row says where it came from, what it passed and where it went
    assert all(r["source"] and r["via"] and r["destination"] for r in p["rows"])
    # served energy of each load is what the dispatch recorded
    served = _by(p["rows"], "destination")
    w = probe.snapshot_weightings["generators"]
    for ld in probe.loads.index:
        assert served[(ld,)] == pytest.approx(float((probe.loads_t.p[ld] * w).sum()), rel=1e-6)


def test_h2_hub_energy_reaches_the_offtake_through_the_store():
    n = T.BUILDERS["eh_h2_hub"]()
    _solve(n)
    p = energy_flows(n)
    via_store = _by([r for r in p["rows"] if r["destination"] == "h2_offtake"], "via")
    assert via_store.get(("h2_storage",), 0.0) > 0.0
    assert via_store.get(("Direct",), 0.0) > 0.0


def test_microgrid_battery_keeps_solar_and_wind_apart():
    n = T.BUILDERS["eh_microgrid"]()
    _solve(n)
    p = energy_flows(n)
    batt = _by([r for r in p["rows"] if r["via"] in ("island_battery", "bess_new")], "source")
    assert batt.get(("solar",), 0) > 0 and batt.get(("wind",), 0) > 0


def _battery_net():
    """Solar (cheap, limited) and grid charge a battery that serves a load later."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Bus", "site")
    n.add("Bus", "grid")
    n.add("Carrier", ["solar", "grid"])
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=10,
          p_max_pu=[1.0, 1.0, 0.0, 0.0], marginal_cost=0.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=100, marginal_cost=[1, 1, 100, 100])
    n.add("Link", "tie", bus0="grid", bus1="site", p_nom=100, efficiency=1.0)
    n.add("StorageUnit", "bat", bus="site", p_nom=10, max_hours=10, efficiency_store=1.0,
          efficiency_dispatch=1.0, cyclic_state_of_charge=True)
    # snapshots 2 and 3 need power the battery must have stored in 0 and 1
    n.add("Load", "demand", bus="site", p_set=[0.0, 0.0, 10.0, 10.0])
    return n


def test_the_battery_discharge_carries_the_mix_it_was_charged_with():
    n = _solve(_battery_net())
    p = energy_flows(n)
    _assert_conserves(p)
    got = _by(p["rows"], "source", "via", "destination")
    # 20 MWh delivered in snapshots 2-3 is all battery; the battery is charged by
    # pv (10 MW × 2 h) first and the rest by the grid — and the loss-free battery
    # hands the mix back in exactly that proportion.
    solar = got.get(("solar", "bat", "demand"), 0.0)
    grid = got.get((GRID_IMPORT, "bat", "demand"), 0.0)
    assert solar + grid == pytest.approx(20.0, abs=1e-6)
    ch = n.storage_units_t.p_store
    pv = n.generators_t.p["pv"]
    assert solar == pytest.approx(min(float(pv.iloc[:2].sum()), float(ch.sum().iloc[0] if hasattr(ch.sum(), "iloc") else ch.sum())), abs=1e-6)
    # nothing is "Direct" to the demand in the hours the battery serves it
    assert got.get(("solar", "Direct", "demand"), 0.0) == pytest.approx(0.0, abs=1e-9)


def test_losses_are_booked_with_the_origin_of_the_energy_lost():
    n = _battery_net()
    n.storage_units.loc["bat", ["efficiency_store", "efficiency_dispatch"]] = 0.9
    n = _solve(n)
    p = energy_flows(n)
    _assert_conserves(p)
    lost = _by(p["rows"], "destination")[("Losses",)]
    assert lost > 0
    # round trip: what the battery lost is in the Losses rows, via the battery
    assert sum(r["mwh"] for r in p["rows"] if r["destination"] == "Losses" and r["via"] == "bat") == \
        pytest.approx(lost, rel=1e-9)


def test_window_narrows_rows_and_series_but_not_the_storage_replay():
    n = _solve(_battery_net())
    full = energy_flows(n)
    late = energy_flows(n, window=(2, 3))
    _assert_conserves(late)
    assert {k: late["window"][k] for k in ("from", "to", "snapshots")} == {"from": 2, "to": 3, "snapshots": 4}
    # the battery fills in the first two snapshots: nothing is delivered yet, and
    # the window still conserves because the stored energy is Δ storage
    early = energy_flows(n, window=(0, 1))
    _assert_conserves(early)
    assert early["conservation"]["storage_delta_mwh"] == pytest.approx(20.0, abs=1e-6)
    assert not [r for r in early["rows"] if r["destination"] == "demand"]
    assert sum(r["mwh"] for r in late["rows"]) == pytest.approx(sum(r["mwh"] for r in full["rows"]))
    # the battery was charged before the window and still delivers its mix in it
    assert _by(late["rows"], "via").get(("bat",), 0.0) > 0.0
    assert len(late["series"]["index"]) == 2


def test_csv_is_the_rows_of_the_chart():
    n = _solve(_battery_net())
    p = energy_flows(n)
    read = list(csv.reader(io.StringIO(csv_text(p))))
    assert tuple(read[0]) == CSV_HEADER
    assert len(read) - 1 == len(p["rows"])
    for line, row in zip(read[1:], p["rows"]):
        assert line[:3] == [row["source"], row["via"], row["destination"]]
        assert float(line[3]) == pytest.approx(row["mwh"], abs=1e-6)


def test_missing_dispatch_is_unavailable_not_zero():
    n = _solve(_battery_net())
    n.generators_t.p = n.generators_t.p.drop(columns=["pv"])
    p = energy_flows(n)
    assert p["status"] == "not_established" and p["rows"] == [] and p["reason"]


def test_a_bus_that_does_not_balance_is_not_drawn():
    n = _solve(_battery_net())
    n.loads_t.p = n.loads_t.p * 1.5          # demand the generation never covered
    p = energy_flows(n)
    assert p["status"] == "not_established" and p["rows"] == []
    assert "bus_balance" in p["conservation"]["checks"]


def test_unserved_demand_is_removed_from_what_the_load_was_served():
    n = _battery_net()
    n.add("Load", "extra", bus="site", p_set=[0.0, 0.0, 500.0, 0.0])      # can't be met
    capture = None
    n.add("Generator", "__voll_extra", bus="site", carrier="load_shedding", p_nom=1000, marginal_cost=1e4)
    _solve(n)
    shed = n.generators_t.p[["__voll_extra"]].copy()
    shed.columns = ["extra"]
    capture = {"lost_load_t": shed}
    n.remove("Generator", "__voll_extra")
    p = energy_flows(n, lost_load=capture)
    _assert_conserves(p)
    served_extra = _by(p["rows"], "destination").get(("extra",), 0.0)
    assert served_extra == pytest.approx(500.0 - float(shed["extra"].sum()), abs=1e-6)
    assert p["unserved_mwh"] == pytest.approx(float(shed["extra"].sum()), abs=1e-6)
    # without the capture the balance cannot be proved
    assert energy_flows(n)["status"] == "not_established"


def test_golden_multi_period_network_conserves_in_every_period():
    n = gf.solve_golden_network()
    periods = energy_flows(n)["periods"]
    assert periods == list(gf.GOLDEN_PERIODS)
    for per in periods:
        _assert_conserves(energy_flows(n, per))


# ---- the route ---------------------------------------------------------------------

def test_route_204_before_a_solve(client):
    assert client.get("/api/results/energy_flows").status_code == 204
    assert client.get("/api/results/energy_flows.csv").status_code == 204


def test_route_and_csv_agree(client, reset_backend):
    gf.install_golden(gf.solve_golden_network())
    r = client.get("/api/results/energy_flows", params={"period": gf.GOLDEN_PERIODS[1]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["period"] == gf.GOLDEN_PERIODS[1]
    c = client.get("/api/results/energy_flows.csv", params={"period": gf.GOLDEN_PERIODS[1]})
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(c.text)))
    assert len(rows) - 1 == len(body["rows"])
    assert sum(float(x[3]) for x in rows[1:]) == pytest.approx(sum(x["mwh"] for x in body["rows"]), abs=1e-4)


def test_route_window_is_positional_and_inclusive(client, reset_backend):
    gf.install_golden(gf.solve_golden_network())
    r = client.get("/api/results/energy_flows", params={"period": gf.GOLDEN_PERIODS[0], "from": 0, "to": 5}).json()
    assert r["window"]["from"] == 0 and r["window"]["to"] == 5
    assert r["window"]["snapshots"] == gf.SNAPSHOTS_PER_PERIOD


def test_a_non_cyclic_storage_starts_from_its_stock_which_is_named_not_dropped():
    n = _battery_net()
    n.storage_units.loc["bat", "cyclic_state_of_charge"] = False
    n.storage_units.loc["bat", "state_of_charge_initial"] = 6.0
    n = _solve(n)
    p = energy_flows(n)
    _assert_conserves(p)
    stock = [r for r in p["rows"] if r["source"] == "Stored before the period"]
    assert stock and sum(r["mwh"] for r in stock) == pytest.approx(6.0, abs=1e-6)
    assert all(r["via"] == "bat" for r in stock)
    assert p["conservation"]["stock_mwh"] == pytest.approx(6.0, abs=1e-6)


def _two_period_battery(cyclic):
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product([[2030, 2040], pd.RangeIndex(4)], names=["period", "timestep"])
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Bus", "site")
    n.add("Carrier", ["solar", "grid"])
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=10, p_max_pu=[1.0, 1.0, 0.0, 0.0] * 2)
    n.add("Generator", "grid_supply", bus="site", carrier="grid", p_nom=100, marginal_cost=50)
    n.add("StorageUnit", "bat", bus="site", p_nom=10, max_hours=10, efficiency_store=0.9,
          efficiency_dispatch=0.9, cyclic_state_of_charge=cyclic,
          cyclic_state_of_charge_per_period=cyclic)
    n.add("Load", "demand", bus="site", p_set=[0.0, 0.0, 6.0, 6.0] * 2)
    return n


@pytest.mark.parametrize("cyclic", [True, False])
def test_each_period_of_a_multi_period_storage_conserves_on_its_own(cyclic):
    n = _solve(_two_period_battery(cyclic), multi=True)
    for per in (2030, 2040):
        p = energy_flows(n, per)
        _assert_conserves(p)
        assert p["period"] == per
        assert any(r["via"] == "bat" for r in p["rows"])


def _commodity_net(commodity_unit="t/h"):
    """Electricity feeds a process unit whose product is a commodity in t/h."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Bus", "el")
    n.add("Bus", "nh3", unit=commodity_unit)
    n.add("Carrier", ["grid"])
    n.add("Generator", "grid_supply", bus="el", carrier="grid", p_nom=100, marginal_cost=10)
    n.add("Link", "synthesis", bus0="el", bus1="nh3", p_nom=50, efficiency=0.2)
    n.add("Load", "offtake", bus="nh3", p_set=2.0)
    return n


def test_a_link_into_a_commodity_in_tonnes_is_not_drawn_as_energy_with_losses():
    p = energy_flows(_solve(_commodity_net()))
    assert p["status"] == "not_established" and p["rows"] == []
    assert p["reason"].startswith("mixed units on Link synthesis")


def test_a_commodity_only_bus_is_named_not_summed_as_mwh():
    n = _commodity_net()
    n.buses.loc["el", "unit"] = "t/h"
    p = energy_flows(_solve(n))
    assert p["status"] == "not_established" and "not an energy unit" in p["reason"]


def test_energy_units_in_any_spelling_still_draw():
    n = _commodity_net(commodity_unit="MW_th")
    n.buses.loc["el", "unit"] = "MW"
    p = energy_flows(_solve(n))
    _assert_conserves(p)
    assert any(r["destination"] == "Losses" for r in p["rows"])


def test_a_three_port_link_splits_its_input_mix_over_both_outputs_and_books_the_loss():
    """A CHP: gas in, electricity and heat out; the heat and power both carry "gas"."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    n.snapshot_weightings.loc[:, :] = 1.0
    for b in ("gas", "el", "heat"):
        n.add("Bus", b)
    n.add("Carrier", ["gas", "solar"])
    n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=100, marginal_cost=30)
    n.add("Generator", "pv", bus="el", carrier="solar", p_nom=3, p_max_pu=[1, 0, 1, 0], marginal_cost=0)
    n.add("Generator", "backup", bus="el", carrier="grid", p_nom=100, marginal_cost=500)
    n.add("Link", "chp", bus0="gas", bus1="el", bus2="heat", p_nom=50, efficiency=0.4, efficiency2=0.45)
    n.add("Load", "power", bus="el", p_set=4.0)
    n.add("Load", "warmth", bus="heat", p_set=3.0)
    p = energy_flows(_solve(n))
    _assert_conserves(p)
    got = _by(p["rows"], "source", "destination")
    # 4 h × 3 MW heat is all gas; the loss is 15 % of the gas burned
    assert got[("gas", "warmth")] == pytest.approx(12.0, abs=1e-6)
    burned = float(n.links_t.p0["chp"].sum())
    assert got[("gas", "Losses")] == pytest.approx(0.15 * burned, abs=1e-6)
    # electricity is gas, the sun and (dear) backup; the sun is not turned into heat
    assert got.get(("solar", "warmth"), 0.0) == pytest.approx(0.0, abs=1e-9)
    assert got[("solar", "power")] == pytest.approx(float(n.generators_t.p["pv"].sum()), abs=1e-6)


def test_surplus_sent_to_the_grid_is_export_with_its_origin():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Bus", "site")
    n.add("Bus", "grid")
    n.add("Carrier", ["solar", "grid"])
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=10, p_max_pu=[1, 1, 0, 0], marginal_cost=0)
    # a grid that also buys: its generator may run negative; the tie is reversible
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=100, p_min_pu=-1, marginal_cost=[20, 20, 40, 40])
    n.add("Link", "tie", bus0="grid", bus1="site", p_nom=6, p_min_pu=-1, efficiency=0.98)
    n.add("Load", "demand", bus="site", p_set=[1, 1, 3, 3])
    p = energy_flows(_solve(n))
    _assert_conserves(p)
    got = _by(p["rows"], "source", "destination")
    exported = float((-n.generators_t.p["grid_supply"].clip(upper=0)).sum())
    assert exported > 0
    assert got[("solar", "Export")] == pytest.approx(exported, abs=1e-6)
    assert got[(GRID_IMPORT, "demand")] == pytest.approx(
        float(n.loads_t.p["demand"].sum()) - got.get(("solar", "demand"), 0.0), abs=1e-6)


def test_a_process_shortfall_is_lost_production_not_supply():
    """Ticket 04: the product's shortfall generator is never a source; the load is
    served what was really produced, and the rest is reported beside the rows."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Bus", "el")
    n.add("Bus", "prod")
    n.add("Carrier", ["grid", "product_shortfall"])
    n.add("Generator", "grid_supply", bus="el", carrier="grid", p_nom=100, marginal_cost=10)
    n.add("Link", "unit", bus0="el", bus1="prod", p_nom=3, efficiency=1.0)      # makes at most 3 MW
    n.add("Generator", "__lost_prod", bus="prod", carrier="product_shortfall", p_nom=100,
          marginal_cost=1e4, process_role="shortfall")
    n.add("Load", "delivery", bus="prod", p_set=5.0)
    n = _solve(n)
    assert float(n.generators_t.p["__lost_prod"].sum()) > 0            # the fixture really runs it
    p = energy_flows(n)
    _assert_conserves(p)
    assert not [r for r in p["rows"] if r["source"] == "product_shortfall"]
    served = _by(p["rows"], "destination")[("delivery",)]
    unserved = p["unserved_product_mwh"]["delivery"]
    assert unserved == pytest.approx(float(n.generators_t.p["__lost_prod"].sum()), abs=1e-6)
    assert served + unserved == pytest.approx(float(n.loads_t.p["delivery"].sum()), abs=1e-6)


def test_a_process_buy_in_stays_a_source_under_its_own_name():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Bus", "prod")
    n.add("Carrier", ["product_purchase"])
    n.add("Generator", "__buy_prod", bus="prod", carrier="product_purchase", p_nom=100,
          marginal_cost=50, process_role="buy_in")
    n.add("Load", "delivery", bus="prod", p_set=2.0)
    p = energy_flows(_solve(n))
    _assert_conserves(p)
    assert _by(p["rows"], "source", "destination")[("product_purchase", "delivery")] == pytest.approx(6.0)
    assert p["unserved_product_mwh"] == {}
