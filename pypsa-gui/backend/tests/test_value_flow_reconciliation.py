"""
The ledger on a solved site reconciles to `cost_breakdown` to the cent (Edge
Investment Case P3 WP3.1, fixtures V1 and V1b).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.1 and
§ Conservation and reconciliation. V1: the P1 edge site under `single_owner`
with a TOU / demand / fixed / export tariff, an export price, a firm connection
fee, a dispatch PPA and a lease, and capital costs on PV and BESS. V1b adds a
costed Line and Transformer and an island bus the meter reaches from neither
side: every costed asset is in the ledger (never dropped) and it still closes.
"""
from __future__ import annotations

import copy
import queue
import threading

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.investment_case.edge_15min import build_edge_15min

REF = {"id": "px", "version": 1, "hash": "abcdefabcdefabcd", "source": "test"}
TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
    {"name": "night", "rate": 0.06, "start_hour": 0, "end_hour": 6},
    {"name": "day", "rate": 0.18}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 9.0}]}
FIXED = {"id": "standing", "kind": "fixed", "unit": "per_month",
         "periods": [{"name": "all", "rate": 150.0}]}
FEED_IN = {"id": "feed_in", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
           "direction": "revenue", "periods": [{"name": "all", "rate": 0.01}]}
LEVY = {"id": "levy", "kind": "tax_levy", "unit": "per_kwh",
        "periods": [{"name": "all", "rate": 0.02}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 20.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"],
       "changes_dispatch": True}
LEASE = {"type": "lease", "id": "lease1", "lessor": "Leasing GmbH", "lessee": "site",
         "annual_payment": 120_000.0, "tenor_years": 10, "asset_ids": ["bess"]}
FEE = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2029-01-01",
       "capacity_fee": {"id": "cap_fee", "kind": "capacity", "unit": "per_kw_year",
                        "periods": [{"name": "all", "rate": 45.0}]}}
VF = {"template": "single_owner",
      "participants": [{"id": "site", "name": "Site", "role": "site_owner"}],
      "externals": ["retailer", "dso", "tso", "market", "tax_authority", "capex_supplier",
                    "om_contractor", "Solar BV", "Leasing GmbH"]}


def _network(*, multi=False, extras=False):
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    n.generators.loc["grid_supply", "p_min_pu"] = -1.0        # the grid absorbs export
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
          marginal_cost=0.01)
    n.generators.loc["pv", "p_nom_extendable"] = True
    n.generators.loc["pv", "p_nom_max"] = 60.0
    n.generators.loc["pv", "capital_cost"] = 40_000.0
    if "fom_cost" in n.generators.columns:
        n.generators.loc["pv", "fom_cost"] = 5_000.0
    n.storage_units.loc["bess", "capital_cost"] = 25_000.0
    n.links.loc["import", "p_nom_extendable"] = True
    n.links.loc["import", "p_nom_max"] = 80.0
    n.links.loc["import", "capital_cost"] = 1_000.0
    price = 30.0 + 20.0 * np.sin(np.arange(len(n.snapshots)) / 96 * 2 * np.pi)
    if extras:
        n.add("Bus", "site2", carrier="AC", v_nom=20.0)
        n.add("Line", "feeder", bus0="site", bus1="site2", x=0.1, r=0.01, s_nom=50.0,
              capital_cost=3_000.0)
        n.add("Bus", "lv", carrier="AC", v_nom=0.4)
        n.add("Transformer", "tx", bus0="site2", bus1="lv", x=0.05, s_nom=20.0,
              capital_cost=2_000.0)
        n.add("Load", "lv_load", bus="lv", p_set=1.0)
        n.add("Bus", "island", carrier="AC")
        n.add("Generator", "island_gen", bus="island", p_nom=5.0, marginal_cost=80.0,
              capital_cost=10_000.0)
        n.add("Load", "island_load", bus="island", p_set=2.0)
    if multi:
        n.set_investment_periods([2030, 2040])
        n.investment_period_weightings["years"] = 10.0
        n.investment_period_weightings["objective"] = 10.0
        price = np.concatenate([price, price])
    n.links_t["ic_export_price"] = pd.DataFrame({"export": price}, index=n.snapshots)
    return n


def _commercial(vf=VF, **extra):
    out = {"poc_link": "import", "export_link": "export", "export_price_ref": REF,
           "import_tariff": {"id": "t", "name": "t", "jurisdiction": "US",
                             "valid_from": "2029-01-01",
                             "items": [TOU, DEMAND, FIXED, FEED_IN, LEVY]},
           "contracts": [PPA, LEASE], "connection": FEE, "value_flows": vf}
    out.update(extra)
    return out


def _solve(n, commercial, *, multi=False, state=None, **kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    import routers.simulation as sim_router
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n)
    cfg = SolverConfig(commercial=commercial, multi_investment_periods=multi, **kw)
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(),
                                  state_update=lambda **k: None if state is None
                                  else state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    return n, cfg


def _ledger(n, cfg, lost_load=None):
    import routers.results as R
    from services.results.value_flows import value_flow_ledger

    got = value_flow_ledger(n, cfg, result_df=R._result_df, lost_load=lost_load)
    assert got is not None
    return got


def _checks(res, p):
    return {c["name"]: c for c in res.periods[p].checks}


@pytest.mark.live_solve
@pytest.mark.parametrize("multi", [False, True], ids=["flat", "multi"])
def test_v1_single_owner_reconciles_to_cost_breakdown_to_the_cent(reset_backend, multi):
    n, cfg = _solve(_network(multi=multi), _commercial(), multi=multi)
    inputs, vf, ledger, res = _ledger(n, cfg)
    for p in inputs.periods:
        checks = _checks(res, p)
        assert res.periods[p].ok is True, checks
        rec = checks["reconciliation"]["detail"]
        assert abs(rec["difference"]) < 0.005, rec
    p = inputs.periods[0]
    lines = ledger.periods[p]
    sources = {ln.source for ln in lines}
    assert {"bill", "connection", "export_price", "contract", "asset"} <= sources
    grid = [ln for ln in lines if ln.asset == "grid_supply"]
    assert grid and all(ln.payee == "market" for ln in grid)
    assert any("commodity_from_grid_side_generator" in ln.flags for ln in grid)
    pv = {ln.value_stream for ln in lines if ln.asset == "pv"}
    assert "capex" in pv
    fee = [ln for ln in lines if ln.source == "connection"]
    assert fee and fee[0].payee == "dso" and fee[0].amount > 0
    exp = [ln for ln in lines if ln.source == "export_price"]
    assert exp and exp[0].payer == "market"
    ppa = [ln for ln in lines if ln.contract_id == "ppa1"]
    assert ppa and ppa[0].payee == "Solar BV"


@pytest.mark.live_solve
@pytest.mark.parametrize("multi", [False, True], ids=["flat", "multi"])
def test_v1b_every_costed_asset_is_in_the_ledger_and_it_still_closes(reset_backend, multi):
    n, cfg = _solve(_network(extras=True, multi=multi), _commercial(), multi=multi)
    inputs, vf, ledger, res = _ledger(n, cfg)
    for p in inputs.periods:
        assert res.periods[p].ok is True, _checks(res, p)
    by_asset = {(a.component, a.name): a for a in inputs.assets}
    assert by_asset[("Line", "feeder")].side == "site"
    assert by_asset[("Transformer", "tx")].side == "site"
    assert by_asset[("Generator", "island_gen")].side == "unclassified"
    lines = ledger.periods[inputs.periods[0]]
    assert any(ln.asset == "feeder" and ln.value_stream == "capex" for ln in lines)
    assert any(ln.asset == "tx" for ln in lines)
    island = [ln for ln in lines if ln.asset == "island_gen"]
    assert island and all("asset_side_unclassified" in ln.flags for ln in island)


@pytest.mark.live_solve
def test_an_internal_retailer_and_dso_still_close(reset_backend):
    """V4's shape on a real solve: payees that are participants drop out of
    both sides of check 4."""
    vf = copy.deepcopy(VF)
    vf["participants"] += [{"id": "dso", "name": "DSO", "role": "dso"},
                           {"id": "retailer", "name": "R", "role": "retailer"}]
    vf["externals"] = [e for e in vf["externals"] if e not in ("dso", "retailer")]
    n, cfg = _solve(_network(), _commercial(vf))
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert res.periods["_"].ok is True, _checks(res, "_")


@pytest.mark.live_solve
def test_export_revenue_to_the_asset_owner_splits_both_export_sources(reset_backend):
    vf = {**copy.deepcopy(VF), "export_revenue_to": "asset_owner",
          "participants": [{"id": "site", "name": "Site", "role": "offtaker"},
                           {"id": "developer", "name": "Dev", "role": "developer"}],
          "asset_owners": [{"asset_id": "pv", "component": "Generator",
                            "owner": "developer"}]}
    n, cfg = _solve(_network(), _commercial(vf))
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert res.periods["_"].ok is True, _checks(res, "_")
    lines = ledger.periods["_"]
    to_dev = [ln for ln in lines if ln.source in ("export_price", "bill")
              and ln.payee == "developer"]
    assert {ln.source for ln in to_dev} == {"export_price", "bill"}
    # Hand formula: per interval, the export revenue × pv's share of site generation.
    w = n.snapshot_weightings.objective.to_numpy(float)
    exp_mw = n.links_t.p0["export"].to_numpy(float)
    price = n.links_t["ic_export_price"]["export"].to_numpy(float)
    pv = np.clip(n.generators_t.p["pv"].to_numpy(float), 0, None)
    hand = float((w * exp_mw * price * np.where(pv > 0, 1.0, 0.0)).sum())
    got = sum(ln.amount for ln in lines if ln.source == "export_price"
              and ln.payee == "developer")
    assert got == pytest.approx(hand, abs=0.005)


@pytest.mark.live_solve
def test_a_corrupted_value_flows_means_no_ledger_but_a_named_error(reset_backend):
    from services.commercial.participants import ValueFlowsInvalid
    from services.results.value_flows import value_flow_ledger
    import routers.results as R

    n, cfg = _solve(_network(), _commercial({"participants": "x"}))
    with pytest.raises(ValueFlowsInvalid):
        value_flow_ledger(n, cfg, result_df=R._result_df)


# ── WP3.1 review round 1 ───────────────────────────────────────────────────


@pytest.mark.live_solve
def test_dsr_and_voll_are_disclosed_and_it_still_closes(reset_backend):
    n = _network()
    n.links.loc["import", "p_nom_extendable"] = False
    n.links.loc["import", "p_nom"] = 1.0                   # scarce enough to shed
    n.generators.loc["pv", "p_nom_extendable"] = False
    n.generators.loc["pv", "p_nom"] = 5.0
    commercial = _commercial()
    commercial.pop("connection")      # a firm agreement's import cap would lift the Link's
    state: dict = {}
    n, cfg = _solve(n, commercial, state=state, dsr_price_eur_per_mwh=40.0,
                    dsr_share_of_load=0.1, dsr_buses=["site"], voll=1000.0)
    inputs, vf, ledger, res = _ledger(n, cfg, lost_load=state.get("last_lost_load"))
    assert res.periods["_"].ok is True, _checks(res, "_")
    d = ledger.disclosures["_"]
    assert d["dsr_slack"] and d["dsr_slack"] > 0
    ll = state["last_lost_load"]
    assert d["voll"] == pytest.approx(ll["lost_load_cost_eur"], rel=1e-9) and d["voll"] > 0
    assert {"dsr_slack_not_a_cash_flow", "voll_not_a_cash_flow"} <= set(ledger.flags)


@pytest.mark.live_solve
def test_an_unsettled_contract_or_a_dead_retail_contract_makes_the_result_none(reset_backend):
    """#1: a PPA on a missing asset and a retail contract on another tariff
    never vanish — the ledger says it cannot be established."""
    ghost = {**PPA, "id": "ppa2", "asset_ids": ["ghost"], "changes_dispatch": False}
    retail = {"type": "retail", "id": "r1", "retailer": "retailer", "customer": "site",
              "tariff_id": "not_this_one", "tenor_years": 5}
    n, cfg = _solve(_network(), _commercial(contracts=[PPA, LEASE, ghost, retail]))
    inputs, vf, ledger, res = _ledger(n, cfg)
    assert res.ok is None
    assert any(ln.contract_id == "ppa2" and ln.amount is None for ln in ledger.periods["_"])
    assert any(f.startswith("input_not_established:contract_not_settled:ppa2") for f in res.flags)
    assert any("contract_not_settled:r1" in f for f in res.flags)


@pytest.mark.live_solve
def test_a_tariff_edited_after_the_solve_makes_the_result_none(reset_backend):
    """#2 probe A: the bill is re-rated on a tariff the solve never saw."""
    n, cfg = _solve(_network(), _commercial())
    edited = copy.deepcopy(cfg.commercial)
    edited["import_tariff"]["items"][0]["periods"][1]["rate"] = 0.45
    cfg.commercial = edited
    inputs, vf, ledger, res = _ledger(n, cfg)
    assert res.ok is None
    assert "input_not_established:config_changed_since_solve" in res.flags


@pytest.mark.live_solve
def test_a_partial_tariff_import_makes_the_result_none(reset_backend):
    """#2 probe B: charges the import could not map are missing money."""
    commercial = _commercial()
    commercial["import_tariff"]["unsupported_fields"] = ["demandratchetpercentage"]
    n, cfg = _solve(_network(), commercial)
    inputs, vf, ledger, res = _ledger(n, cfg)
    assert res.ok is None
    assert any("tariff_incomplete" in f for f in res.flags)


@pytest.mark.live_solve
def test_the_export_split_by_hand_with_two_site_generators(reset_backend):
    """#11: pv (developer) and pv2 (site) with different profiles share every
    interval's export revenue — the export price AND the feed-in item — pro
    rata to their output in that interval."""
    n = _network()
    prof = 0.5 + 0.5 * np.cos(np.arange(len(n.snapshots)) / 96 * 2 * np.pi)
    n.add("Generator", "pv2", bus="site", carrier="solar", p_nom=15.0,
          p_max_pu=pd.Series(np.clip(prof, 0, 1), index=n.snapshots), marginal_cost=0.0)
    vf = {**copy.deepcopy(VF), "export_revenue_to": "asset_owner",
          "participants": [{"id": "site", "name": "Site", "role": "offtaker"},
                           {"id": "developer", "name": "Dev", "role": "developer"}],
          "asset_owners": [{"asset_id": "pv", "component": "Generator",
                            "owner": "developer"}]}
    n, cfg = _solve(n, _commercial(vf))
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert res.periods["_"].ok is True, _checks(res, "_")
    w = n.snapshot_weightings.objective.to_numpy(float)
    exp_mw = n.links_t.p0["export"].to_numpy(float)
    price = n.links_t["ic_export_price"]["export"].to_numpy(float)
    g1 = np.clip(n.generators_t.p["pv"].to_numpy(float), 0, None)
    g2 = np.clip(n.generators_t.p["pv2"].to_numpy(float), 0, None)
    tot = g1 + g2
    share = np.where(tot > 0, g1 / np.where(tot > 0, tot, 1.0), 0.0)
    assert 0.05 < share[tot > 0].mean() < 0.95             # the shares really vary
    hand_price = float((w * exp_mw * price * share).sum())
    hand_feed_in = float((w * exp_mw * 1000.0 * 0.01 * share).sum())
    lines = ledger.periods["_"]
    got_price = sum(ln.amount for ln in lines if ln.source == "export_price"
                    and ln.payee == "developer")
    got_feed_in = sum(ln.amount for ln in lines if ln.tariff_item == "feed_in"
                      and ln.payee == "developer")
    assert got_price == pytest.approx(hand_price, abs=0.005)
    assert got_feed_in == pytest.approx(hand_feed_in, abs=0.005)


@pytest.mark.live_solve
def test_two_generators_of_one_owner_share_the_export_and_close(reset_backend):
    """R1 live: pv and pv2 both owned by the developer."""
    n = _network()
    prof = 0.5 + 0.5 * np.cos(np.arange(len(n.snapshots)) / 96 * 2 * np.pi)
    n.add("Generator", "pv2", bus="site", carrier="solar", p_nom=15.0,
          p_max_pu=pd.Series(np.clip(prof, 0, 1), index=n.snapshots), marginal_cost=0.0)
    vf = {**copy.deepcopy(VF), "export_revenue_to": "asset_owner",
          "participants": [{"id": "site", "name": "Site", "role": "offtaker"},
                           {"id": "developer", "name": "Dev", "role": "developer"}],
          "asset_owners": [{"asset_id": g, "component": "Generator", "owner": "developer"}
                           for g in ("pv", "pv2")]}
    n, cfg = _solve(n, _commercial(vf))
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert res.periods["_"].ok is True, _checks(res, "_")


@pytest.mark.live_solve
def test_a_representative_week_keeps_its_ledger(reset_backend):
    """R2 live: one week standing for a year (the solver's own advice) leaves
    most months out of the dispatch; that is a disclosure, not unknown money."""
    n = _network()
    n.snapshot_weightings.loc[:, :] = 8760.0 / len(n.snapshots)
    n, cfg = _solve(n, _commercial())
    inputs, vf, ledger, res = _ledger(n, cfg)
    assert res.periods["_"].ok is True, _checks(res, "_")
    assert "demand_months_not_established" in ledger.flags


def _add_topology(n, topology):
    """The gate's generation topologies (assessor rounds 1–3): returns a
    function of the solved network giving the site's NON-pv electric output."""
    if topology in ("store_battery", "battery_two_hop", "h2_two_hop", "heat_store_orc"):
        hours = np.asarray(n.snapshots.hour)
        px = n.links_t["ic_export_price"]["export"].to_numpy(float).copy()
        px[(hours >= 11) & (hours < 13)] = 400.0            # discharges while PV exports
        n.links_t["ic_export_price"]["export"] = px
    if topology == "store_battery":
        # PyPSA's Store battery: charger and discharger Links around a Store.
        # Its discharge is stored site electricity, never generation (round 3).
        n.add("Bus", "batt", carrier="battery")
        n.add("Store", "batt_store", bus="batt", e_nom=200.0, e_cyclic=True)
        n.add("Link", "charger", bus0="site", bus1="batt", p_nom=40.0, efficiency=0.95)
        n.add("Link", "discharger", bus0="batt", bus1="site", p_nom=40.0, efficiency=0.95)
        return lambda n: np.zeros(len(n.snapshots))
    if topology == "battery_two_hop":
        # Round 4: charger → Store → BMS → inverter — still stored electricity.
        n.add("Bus", "batt", carrier="battery")
        n.add("Bus", "batt_dc", carrier="battery_dc")
        n.add("Store", "batt_store", bus="batt", e_nom=200.0, e_cyclic=True)
        n.add("Link", "charger", bus0="site", bus1="batt", p_nom=40.0, efficiency=0.95)
        n.add("Link", "bms", bus0="batt", bus1="batt_dc", p_nom=40.0, efficiency=0.99)
        n.add("Link", "discharger", bus0="batt_dc", bus1="site", p_nom=40.0, efficiency=0.96)
        return lambda n: np.zeros(len(n.snapshots))
    if topology == "h2_two_hop":
        # Round 4: electrolyser → H2 Store → pipe → fuel cell — stored electricity.
        n.add("Bus", "h2", carrier="H2")
        n.add("Bus", "h2b", carrier="H2")
        n.add("Store", "h2_store", bus="h2", e_nom=400.0, e_cyclic=True)
        n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=20.0, efficiency=0.7)
        n.add("Link", "pipe", bus0="h2", bus1="h2b", p_nom=40.0, efficiency=1.0)
        n.add("Link", "discharger", bus0="h2b", bus1="site", p_nom=20.0, efficiency=0.5)
        return lambda n: np.zeros(len(n.snapshots))
    n.add("Bus", "gas", carrier="gas")
    n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=100.0, marginal_cost=2.0)
    if topology in ("chp", "gas_load"):
        n.add("Link", "chp", bus0="gas", bus1="site", carrier="CHP", p_nom=40.0, efficiency=0.4)
        if topology == "gas_load":
            n.add("Load", "boiler", bus="gas", p_set=3.0)        # is_fuel_supply is then False
        return lambda n: np.clip(-n.links_t.p1["chp"].to_numpy(float), 0, None)
    if topology == "heat_store_orc":
        # Round 4: a gas boiler charges a heat Store; an ORC turns the heat into
        # power — generation (its origin is fuel), however the Store buffers it.
        n.add("Bus", "heat", carrier="heat")
        n.add("Store", "heat_store", bus="heat", e_nom=200.0, e_cyclic=True)
        n.add("Link", "boiler", bus0="gas", bus1="heat", p_nom=60.0, efficiency=0.9)
        n.add("Link", "orc", bus0="heat", bus1="site", p_nom=20.0, efficiency=0.2)
        return lambda n: np.clip(-n.links_t.p1["orc"].to_numpy(float), 0, None)
    n.add("Bus", "heat", carrier="heat")
    n.add("Load", "heat_load", bus="heat", p_set=4.0)
    n.add("Generator", "heat_dump", bus="heat", carrier="heat", p_nom=100.0, p_max_pu=0.0,
          p_min_pu=-1.0)
    if topology == "multi_output":
        n.add("Link", "chp", bus0="gas", bus1="heat", bus2="site", carrier="CHP", p_nom=40.0,
              efficiency=0.4, efficiency2=0.35)
        return lambda n: np.clip(-n.links_t.p2["chp"].to_numpy(float), 0, None)
    # solar_thermal: heat made from sun and a heat pump — no electric output.
    n.add("Generator", "solar_thermal", bus="heat", carrier="solar thermal", p_nom=6.0,
          marginal_cost=0.0)
    n.add("Link", "heat_pump", bus0="site", bus1="heat", carrier="heat pump", p_nom=5.0,
          efficiency=3.0)
    return lambda n: np.zeros(len(n.snapshots))


_BTM = {"type": "ppa", "id": "btm", "kind": "as_consumed_btm", "price": 20.0,
        "tenor_years": 10, "seller": "developer", "buyer": "site", "asset_ids": ["pv"]}
_VF_DEV = {**copy.deepcopy(VF), "export_revenue_to": "asset_owner",
           "participants": [{"id": "site", "name": "Site", "role": "offtaker"},
                            {"id": "developer", "name": "Dev", "role": "developer"}],
           "asset_owners": [{"asset_id": "pv", "component": "Generator", "owner": "developer"}]}


@pytest.mark.live_solve
@pytest.mark.parametrize("topology", ["chp", "gas_load", "multi_output", "solar_thermal",
                                      "store_battery", "battery_two_hop", "h2_two_hop",
                                      "heat_store_orc"])
def test_the_export_split_counts_electric_generation_only(reset_backend, topology):
    """IC P3 gate condition 2 (rounds 1–2): the export split and the
    `as_consumed_btm` PPA's share count ELECTRIC generation behind the meter —
    Generators on electric buses and what converting Links deliver to electric
    buses from ANY port. A gas supply (with or without a gas load beside it), a
    solar-thermal collector or a heat dump never dilute the split; a CHP's power
    counts on whichever port it leaves by (keyed by the Link), its heat never."""
    from services.commercial import lp_bindings as _lp
    from models.commercial import CommercialConfig

    n = _network()
    other = _add_topology(n, topology)
    n, cfg = _solve(n, _commercial(copy.deepcopy(_VF_DEV), contracts=[LEASE, _BTM]))
    parsed = CommercialConfig.model_validate(cfg.commercial)
    assert _lp.site_generators(n, parsed) == ["pv"]
    stored = ("store_battery", "battery_two_hop", "h2_two_hop")
    assert _lp.site_generating_links(n, parsed) == (
        [] if topology in ("solar_thermal", *stored)
        else ["orc"] if topology == "heat_store_orc" else ["chp"])
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert res.periods["_"].ok is True, _checks(res, "_")

    w = n.snapshot_weightings.objective.to_numpy(float)
    exp_mw = n.links_t.p0["export"].to_numpy(float)
    price = n.links_t["ic_export_price"]["export"].to_numpy(float)
    pv = np.clip(n.generators_t.p["pv"].to_numpy(float), 0, None)
    el = other(n)
    tot = pv + el
    share = np.where(tot > 0, pv / np.where(tot > 0, tot, 1.0), 0.0)
    if topology in stored:
        dis = np.clip(-n.links_t.p1["discharger"].to_numpy(float), 0, None)
        assert ((exp_mw > 1e-6) & (pv > 1e-6) & (dis > 1e-6)).sum() > 10  # the case arises
    elif topology != "solar_thermal":
        assert ((exp_mw > 1e-6) & (pv > 1e-6) & (el > 1e-6)).sum() > 10   # the case arises
    hand = float((w * exp_mw * price * share).sum())
    lines = ledger.periods["_"]
    got = sum(ln.amount for ln in lines if ln.source == "export_price" and ln.payee == "developer")
    assert got == pytest.approx(hand, abs=0.005)
    site_part = sum(ln.amount for ln in lines if ln.source == "export_price" and ln.payee == "site")
    assert site_part == pytest.approx(float((w * exp_mw * price).sum()) - hand, abs=0.005)
    # The PPA: pv's consumed MWh = pv − its share of the export, × 20 (same share).
    hand_ppa = 20.0 * float((w * (pv - np.minimum(pv, np.clip(exp_mw, 0, None) * share))).sum())
    ppa = [ln for ln in lines if ln.contract_id == "btm"]
    assert ppa and sum(ln.amount for ln in ppa) == pytest.approx(hand_ppa, abs=0.005)


@pytest.mark.live_solve
@pytest.mark.parametrize("fuel", ["h2_supply", "reformer", "reversible_sofc", "cofiring", "rsoc"])
def test_a_converter_fed_by_fuel_and_storage_is_not_established(reset_backend, fuel):
    """Rounds 3–5: a converter whose input's energy comes both from fuel and
    from site electricity — an H2 bus with a bought-H2 supply or a reformer
    upstream AND an electrolyser; a REVERSIBLE fuel cell (it electrolyses
    too, `p_min_pu < 0`, whichever side is its bus0); an engine co-firing H2
    taken on an INPUT port (`efficiency2 < 0`) from electrolytic H2 — is mixed:
    which part of its output is generation is not known, so whenever it
    delivers the split (and the PPA share) say not established, never a
    guess."""
    from services.commercial import lp_bindings as _lp
    from models.commercial import CommercialConfig

    n = _network()
    hours = np.asarray(n.snapshots.hour)
    px = n.links_t["ic_export_price"]["export"].to_numpy(float).copy()
    px[(hours >= 11) & (hours < 13)] = 400.0
    n.links_t["ic_export_price"]["export"] = px
    n.add("Bus", "h2", carrier="H2")
    conv, port = "fuel_cell", 1
    if fuel == "h2_supply":
        n.add("Generator", "h2_supply", bus="h2", carrier="H2", p_nom=20.0, marginal_cost=1.0)
    elif fuel == "reformer":
        n.add("Bus", "gas", carrier="gas")
        n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=60.0, marginal_cost=1.0)
        n.add("Link", "reformer", bus0="gas", bus1="h2", p_nom=40.0, efficiency=0.7)
        n.add("Store", "h2_store", bus="h2", e_nom=200.0, e_cyclic=True)
    elif fuel in ("reversible_sofc", "rsoc"):
        n.add("Generator", "h2_supply", bus="h2", carrier="H2", p_nom=5.0, marginal_cost=60.0)
        n.add("Store", "h2_store", bus="h2", e_nom=200.0, e_cyclic=True)
    else:   # cofiring: gas engine taking H2 on bus2; the H2 is electrolytic
        n.add("Bus", "gas", carrier="gas")
        n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=60.0, marginal_cost=1.0)
        n.add("Store", "h2_store", bus="h2", e_nom=400.0, e_cyclic=True)
        n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=10.0, efficiency=0.7)
        n.add("Link", "engine", bus0="gas", bus1="site", bus2="h2", p_nom=40.0, efficiency=0.4,
              efficiency2=-0.5)
        conv = "engine"
    if fuel in ("h2_supply", "reformer"):
        n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=10.0, efficiency=0.7)
        n.add("Link", "fuel_cell", bus0="h2", bus1="site", p_nom=20.0, efficiency=0.5)
    elif fuel == "reversible_sofc":
        n.add("Link", "fuel_cell", bus0="h2", bus1="site", p_nom=20.0, efficiency=0.6,
              p_min_pu=-1.0)
    elif fuel == "rsoc":
        n.add("Link", "fuel_cell", bus0="site", bus1="h2", p_nom=20.0, efficiency=1.0,
              p_min_pu=-1.0)
        port = 0
    n, cfg = _solve(n, _commercial(copy.deepcopy(_VF_DEV), contracts=[LEASE, _BTM]))
    parsed = CommercialConfig.model_validate(cfg.commercial)
    assert _lp.site_generating_links(n, parsed) == [conv]
    gen = _lp.site_link_generation(n, parsed, lambda k: getattr(n.links_t, f"p{k}", None))
    delivered = -getattr(n.links_t, f"p{port}")[conv].to_numpy(float)
    assert (delivered > 1e-6).any()                            # it delivers
    assert np.isnan(gen[conv].to_numpy(float)[delivered > 1e-6]).all()
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert any(f.startswith("export_split_not_established:export_price:") for f in inputs.input_flags)
    assert res.periods["_"].ok is not True
    ppa = [ln for ln in ledger.periods["_"] if ln.contract_id == "btm"]
    assert ppa and all(ln.amount is None for ln in ppa)


@pytest.mark.live_solve
def test_unknown_generation_makes_the_export_split_not_established(reset_backend):
    """IC P3 gate condition 2 (round 2): a NaN in the site's generation in an
    export interval is not a share for the site — the split is not established
    (a blocking flag), as the PPA's share already says."""
    n, cfg = _solve(_network(), _commercial(copy.deepcopy(_VF_DEV)))
    exp_mw = n.links_t.p0["export"].to_numpy(float)
    pv = n.generators_t.p["pv"].to_numpy(float)
    at = np.flatnonzero((exp_mw > 1e-6) & (pv > 1e-6))[:5]
    assert len(at)
    n.generators_t.p.iloc[at, n.generators_t.p.columns.get_loc("pv")] = np.nan
    inputs, _vf, ledger, res = _ledger(n, cfg)
    assert any(f.startswith("export_split_not_established:export_price:") for f in inputs.input_flags)
    assert res.periods["_"].ok is not True
    dev = [ln for ln in ledger.periods["_"] if ln.source == "export_price"]
    assert dev and all(ln.amount is None for ln in dev)
