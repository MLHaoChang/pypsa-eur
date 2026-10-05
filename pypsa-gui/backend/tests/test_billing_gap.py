"""
Billing vs LP gap per item kind, with computed causes (Edge Investment Case P2
WP2.3).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.3.
`gap.billing_vs_lp_gap(n, commercial, site_bill, settlement_lines=...)` compares,
per investment period on the unweighted period-year amounts, what the LP
charged (recomputed from the committed records on the same dispatch) with what
the bill rates, per item kind (energy, demand, tiers, capacity, fixed,
contracts). Every difference the model explains is a cause with a computed
amount. What is left is `unattributed`; above the threshold (default 5 %) it
raises the `billing_gap_unexplained` warn gate.
"""
from __future__ import annotations

import copy
import queue
import threading

import numpy as np
import pandas as pd
import pytest

from models.commercial import CommercialConfig
from services.commercial import billing as B
from services.commercial import contracts as K
from services.commercial import gap as G
from services.commercial import lp_bindings as L
from tests.fixtures.investment_case.edge_15min import build_edge_15min
from tests.test_commercial_objective_reconciliation import CASES, TOU

FIXED = {"id": "standing", "kind": "fixed", "unit": "per_month",
         "periods": [{"name": "all", "rate": 250.0}]}
KVA = {"id": "kva", "kind": "capacity", "unit": "per_kva_year",
       "periods": [{"name": "all", "rate": 20.0}]}
NONCONVEX = {"id": "falling", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
             "periods": [{"name": "all", "rate": 0.0}],
             "tiers": [{"threshold": 0, "rate": 0.08}, {"threshold": 200_000, "rate": 0.03}]}
NET = {"id": "net", "kind": "energy", "unit": "per_kwh", "measured_on": "net",
       "periods": [{"name": "all", "rate": 0.01}]}
LEASE = {"type": "lease", "id": "lease1", "lessor": "Leasing GmbH", "lessee": "site",
         "annual_payment": 52_000.0, "tenor_years": 10, "asset_ids": ["bess"]}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": [copy.deepcopy(i) for i in items]}


def _edge():
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    return n


def _solve(n, commercial, **kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, **kw)
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal"), (status, cond)
    return cfg


def _lines(n, commercial):
    """Settlement lines of a flat network's contracts (WP2.5 builds these from
    the seam; here the generators' output is enough)."""
    cfg = CommercialConfig.model_validate(commercial)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    inp = K.SettlementInputs(period=None, index=pd.DatetimeIndex(n.snapshots), weights=w,
                             generators=n.generators_t.p)
    return [line for c in cfg.contracts for line in K.settle(c, inp)]


def _kinds(gap):
    return [(p, k, v) for p, per in gap["periods"].items() for k, v in per.items()]


# ── the reconciliation fixtures are fully attributed ───────────────────────


@pytest.mark.live_solve
@pytest.mark.parametrize("case", sorted(CASES))
def test_every_lp_fixture_is_fully_attributed(case):
    build, extra = CASES[case]
    n = build()
    multi = isinstance(n.snapshots, pd.MultiIndex)
    commercial = {"poc_link": "import", **copy.deepcopy(extra)}
    _solve(n, commercial, multi_investment_periods=multi)
    bill = B.bill_site(n, commercial)
    lines = _lines(n, commercial) if commercial.get("contracts") else None
    gap = G.billing_vs_lp_gap(n, commercial, bill, settlement_lines=lines)
    assert gap["gates"] == [], (case, gap["gates"])
    assert _kinds(gap), case
    for p, kind, v in _kinds(gap):
        assert v["lp"] is not None and v["billed"] is not None, (case, p, kind, v)
        assert v["unattributed_pct"] is not None, (case, p, kind, v)
        assert v["unattributed_pct"] < 1e-6, (case, p, kind, v)
    if case == "windowed_tiers":
        (per,) = gap["periods"].values()
        tiers = per["tiers"]
        lp = sum(float(r["rate_eur_per_mwh"]) * float(r["q_mwh"])
                 for r in n.meta[L.META_TIERS].values())
        billed = bill.per_period[None].per_item_sampled["wtiers"]
        (cause,) = [c for c in tiers["causes"] if c["cause"] == "tier_allocation"]
        assert cause["amount"] == pytest.approx(billed - lp, rel=1e-9, abs=1e-6)
        assert tiers["lp"] == pytest.approx(lp, rel=1e-9)
    if case == "rep_weeks":
        (per,) = gap["periods"].values()
        causes = {c["cause"]: c for c in per["demand"]["causes"]}
        assert len(causes["months_not_established"]["months"]) == 10
        assert causes["months_not_established"]["amount"] == 0.0   # sampled to sampled
    if isinstance(n.snapshots, pd.MultiIndex):
        # Disclosures belong to their own period (review L5).
        for p, per in gap["periods"].items():
            for c in (per.get("demand") or {}).get("causes", []):
                assert all(m.startswith(f"{p}:") for m in c.get("months", [])), (p, c)
    if case == "ppa_changes_dispatch":
        (per,) = gap["periods"].values()
        c = per["contracts"]
        assert c["lp"] == pytest.approx(c["billed"], rel=1e-9) and c["lp"] > 0


# ── an unexplained difference raises the warn gate ─────────────────────────


@pytest.mark.live_solve
def test_an_adder_mismatch_is_unattributed_and_raises_the_warn_gate(monkeypatch):
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU)}
    real = L._rates
    monkeypatch.setattr(L, "_rates", lambda item, local: real(item, local) * 1.25)
    _solve(n, commercial)
    monkeypatch.setattr(L, "_rates", real)
    bill = B.bill_site(n, commercial)
    assert "config_changed_since_solve" not in bill.flags   # the config did not change
    gap = G.billing_vs_lp_gap(n, commercial, bill)
    energy = gap["periods"][None]["energy"]
    assert energy["unattributed_pct"] == pytest.approx(20.0, rel=1e-6)   # 1 − 1/1.25
    assert energy["causes"] == []
    (gate,) = gap["gates"]
    assert gate["gate"] == "billing_gap_unexplained"
    assert (gate["period"], gate["kind"]) == (None, "energy")
    # The threshold is configurable.
    assert G.billing_vs_lp_gap(n, commercial, bill, threshold_pct=25.0)["gates"] == []


@pytest.mark.live_solve
def test_an_edited_tariff_is_attributed_to_the_config_change_without_a_gate():
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU)}
    _solve(n, commercial)
    edited = copy.deepcopy(commercial)
    edited["import_tariff"]["items"][0]["periods"][2]["rate"] = 0.30
    bill = B.bill_site(n, edited)
    gap = G.billing_vs_lp_gap(n, edited, bill)
    energy = gap["periods"][None]["energy"]
    assert energy["gap"] > 0
    assert energy["causes"] == [{"cause": "config_changed_since_solve",
                                 "amount": pytest.approx(energy["gap"], rel=1e-12)}]
    assert energy["unattributed"] == pytest.approx(0.0, abs=1e-9)
    assert gap["gates"] == []
    assert "config_changed_since_solve" in gap["flags"]


# ── computed causes ────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_fixed_items_and_items_outside_the_lp_are_causes_with_their_billed_amount():
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, FIXED, KVA),
                  "power_factor": 0.95}
    _solve(n, commercial)
    bill = B.bill_site(n, commercial)
    res = bill.per_period[None]
    gap = G.billing_vs_lp_gap(n, commercial, bill)
    per = gap["periods"][None]
    assert per["fixed"]["lp"] == 0.0
    assert per["fixed"]["causes"] == [{"cause": "fixed", "item": "standing",
                                       "amount": pytest.approx(res.per_item_sampled["standing"])}]
    (cap,) = per["capacity"]["causes"]
    assert cap == {"cause": "not_in_lp", "item": "kva", "reason": "per_kva_year_not_in_lp",
                   "amount": pytest.approx(res.per_item_sampled["kva"])}
    assert per["fixed"]["unattributed"] == pytest.approx(0.0, abs=1e-9)
    assert per["capacity"]["unattributed"] == pytest.approx(0.0, abs=1e-9)
    assert gap["gates"] == []


@pytest.mark.live_solve
def test_a_nonconvex_tier_is_the_billed_minus_the_lp_amount():
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, NONCONVEX)}
    _solve(n, commercial)
    bill = B.bill_site(n, commercial)
    gap = G.billing_vs_lp_gap(n, commercial, bill)
    per = gap["periods"][None]
    tiers = per["tiers"]
    (cause,) = tiers["causes"]
    assert cause["cause"] == "nonconvex_tier" and cause["item"] == "falling"
    assert cause["amount"] == pytest.approx(tiers["billed"] - tiers["lp"], rel=1e-9)
    assert cause["amount"] != pytest.approx(0.0)   # the LP priced the first tier
    # The predicted-tier adder is taken out of the energy kind: TOU alone.
    assert per["energy"]["unattributed_pct"] < 1e-6
    assert gap["gates"] == []


@pytest.mark.live_solve
def test_a_settlement_only_contract_is_its_site_view_amount():
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU), "contracts": [LEASE]}
    _solve(n, commercial)
    bill = B.bill_site(n, commercial)
    lines = _lines(n, commercial)
    gap = G.billing_vs_lp_gap(n, commercial, bill, settlement_lines=lines)
    c = gap["periods"][None]["contracts"]
    paid = sum(line.amount for line in lines)
    assert paid > 0
    assert c["lp"] == 0.0 and c["billed"] == pytest.approx(paid)
    assert c["causes"] == [{"cause": "settlement_only", "contract": "lease1",
                            "amount": pytest.approx(paid)}]
    assert gap["gates"] == []
    # Without the settlement the contracts' billed side is unknown, never 0.
    none = G.billing_vs_lp_gap(n, commercial, bill)["periods"][None]["contracts"]
    assert none["billed"] is None and none["gap"] is None
    assert "settlement_not_provided" in none["flags"]


@pytest.mark.live_solve
def test_simultaneous_flows_on_a_net_item_are_the_net_split():
    n = _edge()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    commercial = {"poc_link": "import", "export_link": "export",
                  "import_tariff": _tariff(TOU, NET)}
    _solve(n, commercial)
    # The dispatch as stored, with export beside import in some intervals: the
    # LP charged the net item on import, the meter nets per interval.
    both = n.links_t.p0["import"] > 1.0
    n.links_t.p0.loc[both, "export"] = 0.5
    bill = B.bill_site(n, commercial)
    gap = G.billing_vs_lp_gap(n, commercial, bill)
    energy = gap["periods"][None]["energy"]
    (cause,) = energy["causes"]
    assert cause["cause"] == "net_split_by_direction" and cause["item"] == "net"
    w = n.snapshot_weightings.objective[both]
    assert cause["amount"] == pytest.approx(-float((w * 0.5).sum()) * 10.0, rel=1e-9)
    assert energy["unattributed_pct"] < 1e-6


# ── unknown is None, never 0 ───────────────────────────────────────────────


@pytest.mark.live_solve
def test_an_lp_record_that_is_gone_is_not_established():
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU)}
    _solve(n, commercial)
    n.links_t[L.ENERGY_PRICE_ATTR] = n.links_t[L.ENERGY_PRICE_ATTR].drop(columns=["import"])
    bill = B.bill_site(n, commercial)
    gap = G.billing_vs_lp_gap(n, commercial, bill)
    energy = gap["periods"][None]["energy"]
    assert energy["lp"] is None and energy["gap"] is None
    assert energy["unattributed_pct"] is None
    assert "lp_not_established" in energy["flags"]
    assert gap["gates"] == []


def test_an_unsolved_network_has_no_periods_and_says_why():
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU)}
    bill = B.bill_site(n, commercial)
    gap = G.billing_vs_lp_gap(n, commercial, bill)
    assert gap["periods"] == {} and "not_solved" in gap["flags"]


def test_a_coarser_axis_than_the_settlement_carries_the_resolution_risk():
    n = _edge()
    idx = n.snapshots[::4]
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 1.0
    demand = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
              "periods": [{"name": "all", "rate": 12.0}], "measured_on": "import"}
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, demand)}
    gap = G.billing_vs_lp_gap(n, commercial, B.bill_site(n, commercial))
    (risk,) = gap["resolution_risk"]
    assert risk["item"] == "demand" and risk["code"] == "commercial.demand_resolution"
    assert "understate the bill" in risk["message"]


def test_the_gap_percentages_are_defined_on_known_amounts():
    assert G._unattributed_pct(-10.0, 100.0, 110.0) == pytest.approx(100.0 * 10.0 / 110.0)
    assert G._unattributed_pct(0.0, 0.0, 0.0) == 0.0
    assert G._gap_pct(0.0, 5.0) is None      # an LP of 0 with a bill: undefined
    assert G._gap_pct(0.0, 0.0) == 0.0
    assert G._gap_pct(100.0, 110.0) == pytest.approx(10.0)


# ── review round 1 ─────────────────────────────────────────────────────────


FIT = {"id": "fit", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
       "direction": "revenue", "periods": [{"name": "day", "rate": 0.08, "start_hour": 8,
                                            "end_hour": 18}, {"name": "rest", "rate": 0.03}]}


@pytest.mark.live_solve
def test_export_revenue_on_a_site_clock_is_fully_attributed():
    """L9: export items with TOU windows on Europe/Berlin."""
    n = _edge()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    n.generators.loc["pv", "p_nom"] = 400.0      # a winter week with midday surplus
    n.add("Generator", "grid_sink", bus="grid", carrier="grid", p_nom=500.0, p_min_pu=-1.0,
          p_max_pu=0.0)                           # the grid takes the export
    commercial = {"poc_link": "import", "export_link": "export", "timezone": "Europe/Berlin",
                  "import_tariff": _tariff(TOU, FIT)}
    _solve(n, commercial)
    assert float(n.links_t.p0["export"].sum()) > 0
    gap = G.billing_vs_lp_gap(n, commercial, B.bill_site(n, commercial))
    energy = gap["periods"][None]["energy"]
    assert energy["unattributed_pct"] < 1e-6 and gap["gates"] == []
    assert energy["items"]["fit"]["billed"] < 0
    assert energy["items"]["fit"]["lp"] == pytest.approx(energy["items"]["fit"]["billed"],
                                                         rel=1e-9)


@pytest.mark.live_solve
def test_a_nonconvex_tier_without_its_committed_record_is_not_established():
    """M1: the predicted-tier charge rode `ic_energy_price`."""
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(NONCONVEX)}
    _solve(n, commercial)
    n.links_t[L.ENERGY_PRICE_ATTR] = n.links_t[L.ENERGY_PRICE_ATTR].drop(columns=["import"])
    gap = G.billing_vs_lp_gap(n, commercial, B.bill_site(n, commercial))
    tiers = gap["periods"][None]["tiers"]
    assert tiers["lp"] is None and "lp_not_established" in tiers["flags"]
    assert gap["gates"] == []


@pytest.mark.live_solve
def test_a_config_change_is_scoped_to_the_kinds_whose_record_drifted():
    """M2: an edited TOU rate does not hide a demand-record mismatch, nor
    relabel the fixed item or a settlement-only contract."""
    n = _edge()
    demand = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
              "periods": [{"name": "all", "rate": 12.0}], "measured_on": "import"}
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, demand, FIXED),
                  "contracts": [LEASE]}
    _solve(n, commercial)
    for v in n.meta[L.META_DEMAND].values():
        v["billed_mw"] = float(v.get("billed_mw", v["peak_mw"])) * 0.7   # an LP-record bug
    edited = copy.deepcopy(commercial)
    edited["import_tariff"]["items"][0]["periods"][2]["rate"] = 0.30
    bill = B.bill_site(n, edited)
    assert bill.provenance["drift"]["energy"] and not bill.provenance["drift"]["demand"]
    gap = G.billing_vs_lp_gap(n, edited, bill, settlement_lines=_lines(n, edited))
    per = gap["periods"][None]
    assert per["energy"]["causes"][-1]["cause"] == "config_changed_since_solve"
    assert per["demand"]["unattributed_pct"] > 5.0
    assert (None, "demand") in {(g["period"], g["kind"]) for g in gap["gates"]}
    assert [c["cause"] for c in per["fixed"]["causes"]] == ["fixed"]
    assert [c["cause"] for c in per["contracts"]["causes"]] == ["settlement_only"]


@pytest.mark.live_solve
def test_a_tier_record_that_does_not_match_the_dispatch_is_unattributed():
    """M3: `tier_allocation` is billed − LP only when the LP record is
    consistent (segment volumes = the window's metered energy)."""
    build, extra = CASES["windowed_tiers"]
    n = build()
    commercial = {"poc_link": "import", **copy.deepcopy(extra)}
    _solve(n, commercial)
    for v in n.meta[L.META_TIERS].values():
        v["q_mwh"] = float(v["q_mwh"]) * 0.5
    gap = G.billing_vs_lp_gap(n, commercial, B.bill_site(n, commercial))
    tiers = gap["periods"][None]["tiers"]
    (cause,) = tiers["causes"]
    assert cause["amount"] is None
    assert any(f.startswith("tier_allocation_not_established:wtiers:") for f in tiers["flags"])
    assert (None, "tiers") in {(g["period"], g["kind"]) for g in gap["gates"]}


@pytest.mark.live_solve
def test_a_contract_without_settlement_lines_is_unknown_not_zero():
    """M4."""
    n = _edge()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU), "contracts": [LEASE]}
    _solve(n, commercial)
    gap = G.billing_vs_lp_gap(n, commercial, B.bill_site(n, commercial), settlement_lines=[])
    c = gap["periods"][None]["contracts"]
    assert c["billed"] is None and "settlement_lines_missing:lease1" in c["flags"]


@pytest.mark.live_solve
def test_contracts_compare_without_an_import_tariff():
    """L6: a dispatch PPA's LP row against its settlement, no tariff."""
    n = _edge()
    ppa = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 10.0,
           "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"],
           "changes_dispatch": True}
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0   # the PV runs at 10
    commercial = {"poc_link": "import", "contracts": [ppa]}
    _solve(n, commercial)
    bill = B.bill_site(n, commercial)
    assert bill.per_period == {}
    gap = G.billing_vs_lp_gap(n, commercial, bill, settlement_lines=_lines(n, commercial))
    c = gap["periods"][None]["contracts"]
    assert c["lp"] > 0 and c["lp"] == pytest.approx(c["billed"], rel=1e-9)
    assert set(gap["periods"][None]) == {"contracts"}


@pytest.mark.live_solve
@pytest.mark.parametrize("corrupt", ["volume", "rate", "width"])
def test_an_energy_edit_does_not_hide_a_tier_record_mismatch(corrupt):
    """Round 2 R1 (a TOU edit left the convex tiers' own mismatch hidden) and
    R2 (rate and width guards on the windowed record)."""
    build, extra = CASES["windowed_tiers"]
    n = build()
    commercial = {"poc_link": "import",
                  **copy.deepcopy({**extra, "import_tariff": _tariff(
                      TOU, *extra["import_tariff"]["items"])})}
    _solve(n, commercial)
    recs = list(n.meta[L.META_TIERS].values())
    if corrupt == "volume":
        for v in recs:
            v["q_mwh"] = float(v["q_mwh"]) * 0.5
    elif corrupt == "rate":
        for v in recs:
            v["rate_eur_per_mwh"] = float(v["rate_eur_per_mwh"]) * 0.8
    else:   # every window's volume moved to its cheapest tier: sums kept, widths broken
        by = {}
        for v in recs:
            by.setdefault((v["month"], v["period"]), []).append(v)
        for group in by.values():
            total = sum(float(v["q_mwh"]) for v in group)
            cheapest = min(group, key=lambda v: float(v["rate_eur_per_mwh"]))
            for v in group:
                v["q_mwh"] = total if v is cheapest else 0.0
    edited = copy.deepcopy(commercial)
    edited["import_tariff"]["items"][0]["periods"][2]["rate"] = 0.30
    bill = B.bill_site(n, edited)
    assert bill.provenance["drift"]["energy"] and not bill.provenance["drift"]["tiers"]
    gap = G.billing_vs_lp_gap(n, edited, bill)
    tiers = gap["periods"][None]["tiers"]
    assert all(c["cause"] != "config_changed_since_solve" for c in tiers["causes"])
    assert any(f.startswith("tier_allocation_not_established:wtiers:") for f in tiers["flags"])
    assert (None, "tiers") in {(g["period"], g["kind"]) for g in gap["gates"]}
