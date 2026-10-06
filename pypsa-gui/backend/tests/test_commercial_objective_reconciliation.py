"""
Commercial cost rows reconcile with the LP objective, before AND after a
project save → load (Edge Investment Case P1 WP1.7, the P1 gate).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.7
Spec §5.1 (as implemented): every commercial transform is transient; what the
rows need is committed on success to `links_t["ic_energy_price"]` and
`n.meta[...]`, which ride the project's `network.nc`. So `cost_breakdown`
recomputes the rows after a reload with no solver state at all, and
`_HANDLER_PARAMS` is unchanged (no new route argument; plan deviation from the
`commercial_terms=` keyword, recorded in the plan).

Nineteen cases: energy only; + capacity fee; + demand charge; + ratchet; + convex
tiers; + group cap; a representative-weeks axis; two investment periods (TOU +
demand + fee); a two-period group whose demand is metered on the group; annual FOM on the extendable assets; capex and FOM on a fee-bearing PoC Link (WP2.0);
rising demand tiers, a designated-month and a cyclic ratchet (WP2.1c-i); convex
windowed energy tiers (WP2.1c-ii); tariff capacity, contracted and measured-peak
(WP2.1c-iii); a changes_dispatch PPA (WP2.2d); a net cost item on a three-member
group's net import (P3 WP3.3b, V6).
After the reload the gap must still be computed (never None), and the flags,
the not-established months and the group record must be the same.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.investment_case.edge_15min import build_edge_15min
from tests.test_group_net_import import v6_commercial, v6_network

TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
    {"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
    {"name": "peak", "rate": 0.40, "start_hour": 17, "end_hour": 21},
    {"name": "day", "rate": 0.20}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 12.0}], "measured_on": "import"}
RATCHET = {**DEMAND, "ratchet": {"lookback_months": 11, "share": 0.8}}
TIERS = {"id": "tiered", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
         "periods": [{"name": "all", "rate": 0.0}],
         "tiers": [{"threshold": 0, "rate": 0.02}, {"threshold": 500_000, "rate": 0.06}]}
FEE = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01",
       "capacity_fee": {"id": "fee", "kind": "capacity", "unit": "per_kw_year",
                        "periods": [{"name": "all", "rate": 60.0}]}}
HISTORY = {f"2029-{m:02d}": 30_000.0 for m in range(2, 13)}
# WP2.1c-i: rising demand tiers, designated-month and cyclic ratchets.
DEMAND_TIERS = {**DEMAND, "periods": [{"name": "all", "rate": 0.0}],
                "tiers": [{"threshold": 0, "rate": 5.0}, {"threshold": 30_000, "rate": 20.0}]}
# WP2.1c-ii: convex windowed energy tiers (per-period rates on shared thresholds).
WINDOWED_TIERS = {"id": "wtiers", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
                  "tiers": [{"threshold": 0, "rate": 0.0}, {"threshold": 300_000, "rate": 0.0}],
                  "periods": [{"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21,
                               "tier_rates": [0.30, 0.40]},
                              {"name": "off", "rate": 0.0, "tier_rates": [0.10, 0.15]}]}
# WP2.1c-iii: tariff capacity items — contracted (on an extendable PoC) and
# measured on the annual import peak (the DE Leistungspreis).
TARIFF_CAP = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
              "periods": [{"name": "all", "rate": 400.0}]}
LEISTUNG = {**TARIFF_CAP, "id": "lp", "measured_on": "peak_import"}
# WP2.2d: a pay-as-produced PPA the site buys, in the LP as a PV marginal cost.
PPA_DISPATCH = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 15.0,
                "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"],
                "changes_dispatch": True}
RATCHET_MONTHS = {**DEMAND, "ratchet": {"months": [1], "share": 0.95}}
RATCHET_CYCLIC = {**DEMAND, "ratchet": {"lookback_months": 1, "share": 0.9,
                                        "cyclic_year": True}}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": list(items)}


def _edge():
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    return n


def _two_members():
    n = _edge()
    n.add("Bus", "poc_b", carrier="AC")
    n.add("Bus", "site_b", carrier="AC")
    n.add("Link", "import_b", bus0="grid", bus1="poc_b", p_nom=80.0, carrier="AC")
    n.add("Link", "poc_site_b", bus0="poc_b", bus1="site_b", p_nom=200.0, carrier="AC")
    n.add("Load", "site_b_load", bus="site_b", p_set=n.loads_t.p_set["site_load"] * 0.8)
    n.add("Generator", "backup_b", bus="site_b", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    return n


def _extendable_poc():
    n = _edge()
    n.links.loc["import", ["p_nom_extendable", "p_nom_max", "capital_cost"]] = [True, 120.0, 0.0]
    return n


def _jan_feb():
    """The 15-min edge across a month boundary (Jan 28 – Feb 3): a ratchet
    reads one modelled month from the other."""
    from tests.fixtures.investment_case import edge_15min as F

    old = F.START
    F.START = "2030-01-28 00:00"
    try:
        n = F.build_edge_15min()
    finally:
        F.START = old
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    return n


def _two_periods(build):
    def make():
        n = build()
        n.set_investment_periods([2030, 2040])
        return n
    return make


def _with_fom():
    """WP2.0 (P1 binding condition 5): annual FOM on the extendable assets; the
    plain-solve objective pays it, so the rows must too."""
    n = _edge()
    n.storage_units.loc["bess", ["p_nom_extendable", "p_nom_max", "capital_cost", "fom_cost"]] = \
        [True, 60.0, 20_000.0, 3_000.0]
    n.generators.loc["pv", ["p_nom_extendable", "p_nom_max", "capital_cost", "fom_cost"]] = \
        [True, 120.0, 30_000.0, 800.0]
    return n


def _poc_capex():
    """WP2.0 review: capital cost and FOM on the PoC Link that also carries a
    capacity fee — the fee (× Σw/8760) and the scaled capex/FOM are separate
    terms, never double-scaled."""
    n = _edge()
    n.links.loc["import", ["capital_cost", "fom_cost"]] = [15_000.0, 2_500.0]
    return n


def _rep_weeks():
    n = _edge()
    parts = [pd.date_range(f"2030-{m:02d}-07", periods=96 * 7, freq="15min") for m in (1, 7)]
    idx = parts[0].append(parts[1])
    load = np.tile(n.loads_t.p_set["site_load"].to_numpy(), 2)
    pv = np.tile(n.generators_t.p_max_pu["pv"].to_numpy(), 2)
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 8760.0 / len(idx)
    n.loads_t.p_set = pd.DataFrame({"site_load": load}, index=idx)
    n.generators_t.p_max_pu = pd.DataFrame({"pv": pv}, index=idx)
    return n


CASES = {
    "energy": (_edge, {"import_tariff": _tariff(TOU)}),
    "fee": (_edge, {"import_tariff": _tariff(TOU), "connection": FEE}),
    "demand": (_edge, {"import_tariff": _tariff(TOU, DEMAND)}),
    "ratchet": (_edge, {"import_tariff": _tariff(TOU, RATCHET), "meter_history_peaks_kw": HISTORY}),
    "tiers": (_edge, {"import_tariff": _tariff(TIERS)}),
    "group": (_two_members, {"import_tariff": _tariff(TOU), "group_contract": "hub",
                             "group_members": ["import", "import_b"], "group_cap_mw": 60.0}),
    "rep_weeks": (_rep_weeks, {"import_tariff": _tariff(TOU, DEMAND)}),
    "multi_period": (_two_periods(_edge), {"import_tariff": _tariff(TOU, DEMAND),
                                           "connection": FEE}),
    "fom": (_with_fom, {"import_tariff": _tariff(TOU)}),
    "poc_capex_fee": (_poc_capex, {"import_tariff": _tariff(TOU), "connection": FEE}),
    "demand_tiers": (_edge, {"import_tariff": _tariff(TOU, DEMAND_TIERS)}),
    "windowed_tiers": (_edge, {"import_tariff": _tariff(WINDOWED_TIERS)}),
    "tariff_capacity": (_extendable_poc, {"import_tariff": _tariff(TOU, TARIFF_CAP)}),
    "tariff_capacity_peak": (_edge, {"import_tariff": _tariff(TOU, LEISTUNG)}),
    "ppa_changes_dispatch": (_edge, {"import_tariff": _tariff(TOU), "contracts": [PPA_DISPATCH]}),
    "ratchet_designated": (_jan_feb, {"import_tariff": _tariff(TOU, RATCHET_MONTHS)}),
    "ratchet_cyclic": (_jan_feb, {"import_tariff": _tariff(TOU, RATCHET_CYCLIC),
                                  "meter_history_peaks_kw": {"2030-12": 60_000.0}}),
    # P3 WP3.3b (V6): a net cost item priced once on a 3-member group's net import.
    "group_net_import": (v6_network, {k: v for k, v in v6_commercial().items()
                                      if k != "poc_link"}),
    "group_multi_period": (_two_periods(_two_members),
                           {"import_tariff": _tariff(TOU, DEMAND), "group_contract": "hub",
                            "group_members": ["import", "import_b"], "group_cap_mw": 60.0}),
}


def _gap_and_rows(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb), cb


@pytest.mark.live_solve
@pytest.mark.parametrize("case", sorted(CASES))
def test_rows_reconcile_before_and_after_a_save_and_load(case, client, install_network,
                                                        session_ctx):
    from services.solver_service import SolverConfig, run_simulation
    from services.pypsa_service import PyPSAService

    build, extra = CASES[case]
    name = f"recon_{case}"
    net = build()
    multi = isinstance(net.snapshots, pd.MultiIndex)
    install_network(net, name=name)
    assert client.post(f"/api/projects/{name}",
                       params={"force": True, "rebind": True}).status_code == 200
    ctx = session_ctx(client)
    # A two-period network is solved as one: PyPSA's multi-period objective
    # (capex per active period) is what the rows must reconcile with
    # (WP2.0 review #4 — without the flag the solve was single-period).
    cfg = SolverConfig(commercial={"poc_link": "import", **extra},
                       multi_investment_periods=multi)
    ctx.solver_state["solver_config"] = cfg
    n = ctx.network
    status, condition = run_simulation(cfg, n, ctx.mutation_lock, threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, condition)
    dec, cb = _gap_and_rows(n, cfg)
    assert abs(dec["gap_pct"]) < 1e-6, (case, dec)
    before = cb["commercial"]
    unestablished = [f for f in before["flags"] if f.endswith("not_established")]
    if case == "rep_weeks":
        # Two sampled weeks stand for a year: the ten unsampled months' demand
        # charges are disclosed as not established (spec §5.2), never inferred.
        assert unestablished == ["demand_months_not_established"], before
        assert len(before["demand_months_not_established"]) == 10
    else:
        assert unestablished == [], before

    assert client.post(f"/api/projects/{name}", params={"expect": name}).status_code == 200
    assert client.get(f"/api/projects/{name}").status_code == 200
    reloaded = session_ctx(client)
    cfg2 = reloaded.solver_state["solver_config"]
    assert cfg2.commercial == cfg.commercial
    dec2, cb2 = _gap_and_rows(reloaded.network, cfg2)
    after = cb2["commercial"]
    for key in ("energy_import", "energy_export", "demand_charge", "energy_tiers",
                "network_capacity", "tariff_capacity", "ppa_settlement", "energy_net_group"):
        if before.get(key) is None:
            continue
        assert after.get(key) == pytest.approx(before[key], rel=1e-9), (case, key)
    assert after["flags"] == before["flags"], case
    assert after.get("demand_months_not_established") == \
        before.get("demand_months_not_established"), case
    assert after.get("group") == before.get("group"), case
    if case == "group_net_import":
        assert before["energy_net_group"] and after["energy_net_group"]
    elif extra.get("group_members"):
        assert before["group"]["members"] == ["import", "import_b"]
        assert sum(before["group"]["energy_share"].values()) == pytest.approx(1.0)
    assert cb2["total"] == pytest.approx(cb["total"], rel=1e-9)
    assert dec2["gap_pct"] is not None, (case, dec2)
    assert abs(dec2["gap_pct"]) < 1e-6, (case, dec2)
