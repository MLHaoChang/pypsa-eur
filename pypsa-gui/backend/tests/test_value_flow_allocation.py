"""
Energy-hub allocation: each member's share of the group bill (Edge Investment
Case P3 WP3.3a; oracle V5).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.3a. The
hub (= site_party) pays the group's bill, fees and receives its export revenue;
each shared source is split into internal lines member → hub (a revenue share
hub → member): linear import items metered per member, the rest by the key.
The last member in sorted-id order takes the remainder. V5 (three members,
15-min, 7 days, hand arithmetic in `fixtures/investment_case/hub/`) pins every
share to the cent under all four keys.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.commercial import CommercialConfig, Ratchet, Tariff, ValueFlowConfig
from services.commercial import hub_allocation as HA
from services.commercial import participants as P
from services.commercial import tariff_engine as TE

HUB_DIR = Path(__file__).resolve().parent / "fixtures" / "investment_case" / "hub"
V5 = json.loads((HUB_DIR / "v5_energy_hub.json").read_text())
MEMBERS = sorted(V5["profile"]["members"])


def test_the_v5_working_regenerates_unchanged(tmp_path):
    """The fixture is the output of its stdlib arithmetic (never hand-edited)."""
    subprocess.run([sys.executable, str(HUB_DIR / "v5_arithmetic.py"), str(tmp_path)],
                   check=True)
    assert (tmp_path / "v5_energy_hub.json").read_text() == \
        (HUB_DIR / "v5_energy_hub.json").read_text()


# ── V5 ─────────────────────────────────────────────────────────────────────


def _profile_series(spec, idx) -> np.ndarray:
    v = np.full(len(idx), float(spec.get("base", 0.0)))
    hour = np.asarray(idx.hour + idx.minute / 60.0)
    if "window" in spec:
        w = spec["window"]
        v[(hour >= w["from"]) & (hour < w["to"])] = w["mw"]
    if "spike" in spec:
        v[idx == pd.Timestamp(spec["spike"]["at"])] = spec["spike"]["mw"]
    return v


def _v5_dispatch():
    prof = V5["profile"]
    idx = pd.date_range(prof["start"], periods=prof["days"] * 96, freq="15min")
    members = {m: _profile_series(s, idx) for m, s in prof["members"].items()}
    export = _profile_series(prof["export"], idx)
    return idx, members, export


def _rate_group(tariff, idx, members, export):
    return TE.rate(pd.DataFrame({"import_mw": np.sum(list(members.values()), axis=0),
                                 "export_mw": export}, index=idx),
                   tariff, step_hours=0.25, timezone=None)


def _v5_inputs():
    tariff = Tariff.model_validate(V5["tariff"])
    idx, members, export = _v5_dispatch()
    group = _rate_group(tariff, idx, members, export)
    hp = HA.period_hub(idx, members, export, np.full(len(idx), 0.25), tariff, step_hours=0.25,
                       timezone=None, billing_period=None, represents_hours=None, group=group)
    items = {it.id: P.BillItem(it.id, it.kind, it.measured_on, it.direction)
             for it in tariff.items}
    inputs = P.LedgerInputs(
        periods=["_"], site_party="hub", bill_items=items,
        bill={"_": dict(group.per_item_sampled)}, bill_flags={}, retailer=None,
        settlement=[], connection_fee={"_": V5["connection_fee"]}, connection_fixed_fee={},
        curtailment_compensation=False, export_revenue={"_": V5["export_price_revenue"]},
        export_split=None, assets=[], cost_breakdown_total={"_": 0.0},
        lp_commercial={"_": 0.0}, disclosures={},
        hub=_hub_inputs(tariff, {"_": hp}))
    return inputs, group, hp


def _hub_inputs(tariff, periods, links=None):
    members = [(s["link"], m) for m, s in V5["profile"]["members"].items()]
    return P.HubInputs(members=members, periods=periods,
                       group_links=links or [link for link, _m in members],
                       metered_items=[i.id for i in tariff.items if HA.is_metered(i)],
                       peak_items=[i.id for i in tariff.items if HA.is_peak_item(i)])


def _vf(basis, *, shares=None):
    return ValueFlowConfig.model_validate({
        "template": "energy_hub",
        "participants": [{"id": "hub", "name": "Hub", "role": "site_owner"},
                         *({"id": m, "name": m, "role": "hub_member"} for m in MEMBERS)],
        "hub_members": [{"link": s["link"], "participant": m,
                         "contracted_mw": V5["keys"]["contracted_capacity"][m]}
                        for m, s in V5["profile"]["members"].items()],
        "allocation": {"basis": basis, **({"shares": shares} if shares else {})}})


def _shares(ledger) -> dict[str, dict[str, float]]:
    """source → member → site-view amount (+ the member pays the hub)."""
    out: dict[str, dict[str, float]] = {}
    for ln in ledger.periods["_"]:
        if ln.source != "allocation":
            continue
        member, sign = (ln.payer, 1.0) if ln.payee == "hub" else (ln.payee, -1.0)
        out.setdefault(ln.source_id, {})[member] = sign * ln.amount
    return out


def test_the_v5_group_bill_is_the_working_bill():
    _inputs, group, hp = _v5_inputs()
    for item, want in V5["bill"].items():
        assert abs(group.per_item_sampled[item] - want) < 0.005, item
    for m in MEMBERS:
        assert hp.energy_mwh[m] == pytest.approx(V5["energy_mwh"][m], abs=1e-9)
        for item, want in V5["metered"][m].items():
            assert abs(hp.metered[item][m] - want) < 0.005, (m, item)
    assert set(hp.metered) == {"energy", "levy"}           # linear import items only
    assert set(hp.peak) == {"demand"} and not hp.flags


@pytest.mark.parametrize("basis", ["contracted_capacity", "fixed_shares", "energy",
                                   "peak_contribution"])
def test_v5_every_share_to_the_cent_under_each_key(basis):
    inputs, _group, _hp = _v5_inputs()
    vf = _vf(basis, shares=V5["keys"]["fixed_shares"] if basis == "fixed_shares" else None)
    ledger = P.build_ledger(inputs, vf)
    got = _shares(ledger)
    want = V5["expected"][basis]
    assert set(got) == set(want)
    for sid, members in want.items():
        for m, v in members.items():
            assert abs(got[sid].get(m, 0.0) - v) < 0.005, (basis, sid, m, got[sid], v)
    res = P.check_conservation(ledger, inputs, vf)
    assert res.ok is True, res.periods["_"].checks          # checks 1–5 still close


def test_the_remainder_is_exact():
    """The members' lines re-add to the shared amount to the last bit."""
    inputs, _g, _hp = _v5_inputs()
    ledger = P.build_ledger(inputs, _vf("energy"))
    got = _shares(ledger)
    assert sum(got["bill:standing"][m] for m in MEMBERS) == inputs.bill["_"]["standing"]
    assert sum(got["connection:fee"][m] for m in MEMBERS) == V5["connection_fee"]


def test_a_revenue_share_runs_hub_to_member():
    inputs, _g, _hp = _v5_inputs()
    ledger = P.build_ledger(inputs, _vf("energy"))
    feed = [ln for ln in ledger.periods["_"]
            if ln.source == "allocation" and ln.source_id == "bill:feed_in"]
    assert feed and all(ln.payer == "hub" and ln.amount > 0 for ln in feed)
    assert {ln.value_stream for ln in feed} == {"energy_export"}
    demand = [ln for ln in ledger.periods["_"]
              if ln.source == "allocation" and ln.source_id == "bill:demand"]
    assert all(ln.payee == "hub" for ln in demand)
    assert {ln.value_stream for ln in demand} == {"demand_charge"}


def test_peak_contribution_discloses_its_energy_fallback():
    inputs, _g, _hp = _v5_inputs()
    ledger = P.build_ledger(inputs, _vf("peak_contribution"))
    by = {ln.source_id: ln.flags for ln in ledger.periods["_"] if ln.source == "allocation"}
    assert "allocation_fallback_energy" in by["bill:standing"]
    assert "allocation:peak_contribution" in by["bill:demand"]
    assert "allocation:metered" in by["bill:energy"]


def test_a_member_that_is_the_hub_gets_no_line_to_itself():
    inputs, _g, _hp = _v5_inputs()
    vf = _vf("energy")
    raw = vf.model_dump(mode="json")
    raw["participants"] = [p for p in raw["participants"] if p["id"] != "hub"]
    inputs.site_party = "member_a"
    ledger = P.build_ledger(inputs, ValueFlowConfig.model_validate(raw))
    for ln in ledger.periods["_"]:
        assert ln.payer != ln.payee
    res = P.check_conservation(ledger, inputs, ValueFlowConfig.model_validate(raw))
    assert res.ok is True, res.periods["_"].checks


# ── peak contribution: the tie rule and the ratchet floor's month ──────────


def _demand_tariff(**item):
    return Tariff.model_validate({
        "id": "d", "name": "d", "jurisdiction": "DE", "valid_from": "2029-01-01",
        "items": [{"id": "demand", "kind": "demand", "unit": "per_kw_month",
                   "periods": [{"name": "all", "rate": 10.0}], **item}]})


def _hub(tariff, idx, members, export=None):
    export = np.zeros(len(idx)) if export is None else export
    group = _rate_group(tariff, idx, members, export)
    return HA.period_hub(idx, members, export, np.full(len(idx), 0.25), tariff,
                         step_hours=0.25, timezone=None, billing_period=None,
                         represents_hours=None, group=group), group


def test_a_tie_splits_on_the_first_maximal_interval():
    idx = pd.date_range("2029-01-01", periods=8, freq="15min")
    a = np.array([1.0, 3.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    b = np.array([1.0, 1.0, 1.0, 1.0, 3.0, 1.0, 1.0, 1.0])
    a[4], b[1] = 1.0, 1.0                         # group 4 MW at rows 1 and 4
    hp, group = _hub(_demand_tariff(), idx, {"a": a, "b": b})
    assert group.per_item_sampled["demand"] == pytest.approx(40_000.0)
    # Row 1 (the first maximum): a 3, b 1 → 3 : 1, not row 4's 1 : 3.
    assert hp.peak["demand"] == pytest.approx({"a": 30_000.0, "b": 10_000.0})


def test_a_binding_ratchet_splits_on_the_month_that_set_the_floor():
    """February bills the January floor: its split is January's contributions."""
    idx = pd.date_range("2029-01-30", "2029-02-02 23:45", freq="15min")
    a = np.where(idx.month == 1, 1.0, 1.0)
    b = np.where(idx.month == 1, 0.5, 0.5)
    jan_peak = idx == pd.Timestamp("2029-01-30 12:00")
    feb_peak = idx == pd.Timestamp("2029-02-01 12:00")
    a[jan_peak], b[jan_peak] = 9.0, 1.0           # January: 10 MW, 9 : 1
    a[feb_peak], b[feb_peak] = 1.0, 3.0           # February: 4 MW, 1 : 3
    tariff = _demand_tariff(ratchet={"lookback_months": 1, "share": 0.8})
    hp, group = _hub(tariff, idx, {"a": a, "b": b})
    dl = group.demand_lines.set_index("month")
    assert dl.loc["2029-02", "billed_kw"] == pytest.approx(8000.0)     # 0.8 × 10 MW
    jan, feb = dl.loc["2029-01", "amount"], dl.loc["2029-02", "amount"]
    assert hp.peak["demand"]["a"] == pytest.approx(jan * 0.9 + feb * 0.9)
    assert hp.peak["demand"]["b"] == pytest.approx(jan * 0.1 + feb * 0.1)


def test_a_floor_from_meter_history_is_not_established():
    idx = pd.date_range("2029-02-01", periods=96, freq="15min")
    a, b = np.full(96, 1.0), np.full(96, 1.0)
    tariff = _demand_tariff(ratchet={"lookback_months": 1, "share": 1.0})
    group = TE.rate(pd.DataFrame({"import_mw": a + b, "export_mw": np.zeros(96)}, index=idx),
                    tariff, step_hours=0.25, timezone=None,
                    meter_history={"2029-01": 5000.0})
    assert group.demand_lines["billed_kw"].iloc[0] == pytest.approx(5000.0)
    hp = HA.period_hub(idx, {"a": a, "b": b}, np.zeros(96), np.full(96, 0.25), tariff,
                       step_hours=0.25, timezone=None, billing_period=None,
                       represents_hours=None, group=group)
    assert hp.peak["demand"] is None
    assert "allocation_not_established:demand:ratchet_floor_from_meter_history" in hp.flags


@pytest.mark.parametrize("ratchet", [
    {"lookback_months": 3, "share": 0.5},
    {"lookback_months": 11, "share": 0.5, "cyclic_year": True},
    {"months": [6, 7, 8], "share": 0.5},
], ids=["range", "cyclic", "months"])
def test_the_floor_month_is_the_engine_floor(ratchet):
    """`floor_source_month` reads the same candidate months as the engine: its
    month's peak is the engine's floor, in every mode."""
    rng = np.random.default_rng(7)
    months = [f"2029-{m:02d}" for m in range(1, 13)]
    actual = {(m, 0): float(v) for m, v in zip(months, rng.integers(1, 5, 12) * 100.0)}
    r = Ratchet.model_validate(ratchet)
    for m in months:
        prior, _missing = TE._ratchet_floor_prior(r, m, 0, actual, None, set(months))
        src = HA.floor_source_month(r, m, 0, actual)
        if prior is None:
            assert src is None
        else:
            assert actual[(src, 0)] == prior


def test_the_floor_month_is_the_earliest_on_ties():
    r = Ratchet.model_validate({"lookback_months": 3, "share": 1.0})
    actual = {("2029-01", 0): 500.0, ("2029-02", 0): 500.0, ("2029-03", 0): 100.0}
    assert HA.floor_source_month(r, "2029-04", 0, actual) == "2029-01"


# ── keys that cannot be established, config refusals ───────────────────────


def test_an_energy_key_with_no_member_energy_is_not_established():
    inputs, _g, hp = _v5_inputs()
    hp.energy_mwh = {m: 0.0 for m in MEMBERS}
    ledger = P.build_ledger(inputs, _vf("energy"))
    fee = [ln for ln in ledger.periods["_"]
           if ln.source == "allocation" and ln.source_id == "connection:fee"]
    assert fee and all(ln.amount is None for ln in fee)
    assert any(f.startswith("allocation_not_established:fee:energy_key_not_established")
               for f in fee[0].flags)
    res = P.check_conservation(ledger, inputs, _vf("energy"))
    assert res.ok is None                                   # never True on an unknown share


def test_an_unknown_member_rating_is_not_established():
    inputs, _g, hp = _v5_inputs()
    hp.metered["energy"]["member_b"] = None
    ledger = P.build_ledger(inputs, _vf("contracted_capacity"))
    lines = [ln for ln in ledger.periods["_"]
             if ln.source == "allocation" and ln.source_id == "bill:energy"]
    assert lines and all(ln.amount is None for ln in lines)


def _hub_commercial(**extra):
    return CommercialConfig.model_validate({
        "poc_link": "la", "site_party": "hub", "group_contract": "hub", "group_members": ["la", "lb", "lc"],
        "group_cap_mw": 20.0, **extra})


def _hub_network():
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2029-01-01", periods=4, freq="h"))
    for b in ("grid", "pa", "pb", "pc"):
        n.add("Bus", b, carrier="AC")
    for link, bus in (("la", "pa"), ("lb", "pb"), ("lc", "pc")):
        n.add("Link", link, bus0="grid", bus1=bus, p_nom=10.0)
    return n


def test_an_allocation_needs_every_group_member_as_a_hub_member():
    vf = _vf("energy")
    raw = vf.model_dump(mode="json")
    raw["hub_members"] = raw["hub_members"][:2]
    raw["participants"] = [p for p in raw["participants"] if p["id"] != "member_c"]
    problems = P.value_flows_problems(ValueFlowConfig.model_validate(raw), _hub_commercial(),
                                      _hub_network())
    assert any("every group member as a hub member" in p and "lc" in p for p in problems)
    assert P.value_flows_problems(vf, _hub_commercial(), _hub_network()) == []


def test_the_contracted_capacity_key_needs_every_contracted_mw():
    raw = _vf("contracted_capacity").model_dump(mode="json")
    raw["hub_members"][0]["contracted_mw"] = None
    problems = P.value_flows_problems(ValueFlowConfig.model_validate(raw), _hub_commercial(),
                                      _hub_network())
    assert any("contracted_mw on every hub member" in p for p in problems)


def test_no_allocation_key_leaves_the_bill_with_the_hub():
    inputs, _g, _hp = _v5_inputs()
    raw = _vf("energy").model_dump(mode="json")
    raw["allocation"] = None
    ledger = P.build_ledger(inputs, ValueFlowConfig.model_validate(raw))
    assert not [ln for ln in ledger.periods["_"] if ln.source == "allocation"]


# ── review round 1: stale hubs, reasons on the lines, export demand ────────


def _alloc(ledger, sid):
    return [ln for ln in ledger.periods["_"] if ln.source == "allocation" and ln.source_id == sid]


@pytest.mark.parametrize("links", [["la", "lb"], ["la", "lb", "lc", "ld"]],
                         ids=["member_left_the_group", "group_member_outside_the_hub"])
def test_a_hub_that_no_longer_matches_the_group_is_not_split(links):
    """#1: the solver-config route can change `group_members` under a saved
    value-flows config; the ledger then refuses the split, never renormalises."""
    inputs, _g, _hp = _v5_inputs()
    inputs.hub.group_links = links
    vf = _vf("energy")
    ledger = P.build_ledger(inputs, vf)
    lines = [ln for ln in ledger.periods["_"] if ln.source == "allocation"]
    assert lines and all(ln.amount is None for ln in lines)
    assert all(any(f.endswith(":hub_members_stale") for f in ln.flags) for ln in lines)
    assert P.check_conservation(ledger, inputs, vf).ok is None


def test_a_cleared_group_contract_leaves_the_hub_stale_not_silent():
    """Round 2 R2-1: the group contract cleared after the hub was saved."""
    from types import SimpleNamespace

    from services.results.value_flows import _hub_inputs

    tariff = Tariff.model_validate(V5["tariff"])
    parsed = SimpleNamespace(group_members=[], import_tariff=tariff, export_link=None)
    hub = _hub_inputs(None, parsed, _vf("energy"), None)
    assert hub is not None and hub.group_links == [] and hub.periods == {}
    inputs, _g, _hp = _v5_inputs()
    inputs.hub = hub
    ledger = P.build_ledger(inputs, _vf("energy"))
    lines = [ln for ln in ledger.periods["_"] if ln.source == "allocation"]
    assert lines and all(ln.amount is None for ln in lines)
    assert all(any(f.endswith(":hub_members_stale") for f in ln.flags) for ln in lines)


def test_fixed_shares_on_other_participants_are_stale():
    inputs, _g, _hp = _v5_inputs()
    vf = _vf("fixed_shares", shares=V5["keys"]["fixed_shares"])
    raw = vf.model_dump(mode="json")
    raw["allocation"]["shares"] = {"member_a": 0.5, "member_b": 0.25, "someone": 0.25}
    ledger = P.build_ledger(inputs, ValueFlowConfig.model_validate(raw))
    assert all(ln.amount is None for ln in _alloc(ledger, "connection:fee"))


def test_the_ledger_line_names_why_a_peak_split_is_unknown():
    """#2: the specific reason reaches the ledger, not a generic one."""
    idx = pd.date_range("2029-02-01", periods=96, freq="15min")
    a, b = np.full(96, 1.0), np.full(96, 1.0)
    tariff = _demand_tariff(ratchet={"lookback_months": 1, "share": 1.0})
    group = TE.rate(pd.DataFrame({"import_mw": a + b, "export_mw": np.zeros(96)}, index=idx),
                    tariff, step_hours=0.25, timezone=None, meter_history={"2029-01": 5000.0})
    hp = HA.period_hub(idx, {"member_a": a, "member_b": b}, np.zeros(96), np.full(96, 0.25),
                       tariff, step_hours=0.25, timezone=None, billing_period=None,
                       represents_hours=None, group=group)
    items = {"demand": P.BillItem("demand", "demand", "import", "cost")}
    inputs = P.LedgerInputs(
        periods=["_"], site_party="hub", bill_items=items,
        bill={"_": dict(group.per_item_sampled)}, bill_flags={}, retailer=None, settlement=[],
        connection_fee={}, connection_fixed_fee={}, curtailment_compensation=False,
        export_revenue={}, export_split=None, assets=[], cost_breakdown_total={"_": 0.0},
        lp_commercial={"_": 0.0}, disclosures={},
        hub=P.HubInputs(members=[("la", "member_a"), ("lb", "member_b")], periods={"_": hp},
                        group_links=["la", "lb"], metered_items=[], peak_items=["demand"]))
    vf = ValueFlowConfig.model_validate({
        "participants": [{"id": "hub", "name": "Hub", "role": "site_owner"},
                         {"id": "member_a", "name": "a", "role": "hub_member"},
                         {"id": "member_b", "name": "b", "role": "hub_member"}],
        "hub_members": [{"link": "la", "participant": "member_a"},
                        {"link": "lb", "participant": "member_b"}],
        "allocation": {"basis": "peak_contribution"}})
    (line, *_rest) = _alloc(P.build_ledger(inputs, vf), "bill:demand")
    assert "allocation_not_established:demand:ratchet_floor_from_meter_history" in line.flags


def test_an_export_demand_item_is_never_split_by_import():
    """#3: an export-measured demand item falls back to energy, disclosed."""
    item = {"id": "demx", "kind": "demand", "unit": "per_kw_month", "measured_on": "export",
            "periods": [{"name": "all", "rate": 3.0}]}
    tariff = Tariff.model_validate({**V5["tariff"], "items": [*V5["tariff"]["items"], item]})
    assert not HA.is_peak_item(tariff.items[-1])
    idx, members, export = _v5_dispatch()
    group = _rate_group(tariff, idx, members, export)
    hp = HA.period_hub(idx, members, export, np.full(len(idx), 0.25), tariff, step_hours=0.25,
                       timezone=None, billing_period=None, represents_hours=None, group=group)
    assert "demx" not in hp.peak
    inputs, _g, _hp = _v5_inputs()
    inputs.bill_items["demx"] = P.BillItem("demx", "demand", "export", "cost")
    inputs.bill["_"]["demx"] = group.per_item_sampled["demx"]
    inputs.hub = _hub_inputs(tariff, {"_": hp})
    ledger = P.build_ledger(inputs, _vf("peak_contribution"))
    lines = _alloc(ledger, "bill:demx")
    assert lines and all("allocation_fallback_energy" in ln.flags for ln in lines)
    assert P.check_conservation(ledger, inputs, _vf("peak_contribution")).ok is True


def test_a_metered_item_with_no_rating_is_never_keyed():
    """#4: a period that could not be rated leaves a linear item unknown."""
    inputs, _g, _hp = _v5_inputs()
    inputs.hub.periods["_"] = P.HubPeriod(energy_mwh={m: 1.0 for m in MEMBERS}, metered={},
                                          peak={}, reason="period_not_rated")
    ledger = P.build_ledger(inputs, _vf("contracted_capacity"))
    lines = _alloc(ledger, "bill:energy")
    assert lines and all(ln.amount is None for ln in lines)
    assert any(f.endswith(":period_not_rated") for f in lines[0].flags)
    assert all(ln.amount is not None for ln in _alloc(ledger, "connection:fee"))


def test_the_peak_split_is_computed_only_for_the_peak_key():
    tariff = Tariff.model_validate(V5["tariff"])
    idx, members, export = _v5_dispatch()
    group = _rate_group(tariff, idx, members, export)
    hp = HA.period_hub(idx, members, export, np.full(len(idx), 0.25), tariff, step_hours=0.25,
                       timezone=None, billing_period=None, represents_hours=None, group=group,
                       peak=False)
    assert hp.peak == {} and set(hp.metered) == {"energy", "levy"}


# ── live: a solved two-member hub closes under every key ───────────────────


@pytest.mark.live_solve
@pytest.mark.parametrize("basis,multi", [("energy", False), ("energy", True),
                                         ("contracted_capacity", False),
                                         ("fixed_shares", False),
                                         ("peak_contribution", False)])
def test_a_solved_hub_allocates_and_still_reconciles(reset_backend, basis, multi):
    from services.commercial import value_flow_templates as T
    from tests.test_value_flow_reconciliation import _commercial, _ledger, _solve
    from tests.test_value_flow_templates import _v1_hub

    n = _v1_hub()
    if multi:
        n.set_investment_periods([2030, 2040])
        n.investment_period_weightings["years"] = 10.0
        n.investment_period_weightings["objective"] = 10.0
        assert len(n.links_t["ic_export_price"]) == len(n.snapshots)   # expanded with them
    raw = _commercial(vf=None, contracts=[], group_contract="hub",
                      group_members=["import", "import2"], group_cap_mw=60.0)
    raw.pop("connection")
    built = T.build("energy_hub", n, CommercialConfig.model_validate(raw)).config
    vf = built.model_dump(mode="json")
    parts = [m["participant"] for m in vf["hub_members"]]
    for m, mw in zip(vf["hub_members"], (40.0, 10.0)):
        m["contracted_mw"] = mw
    vf["allocation"] = {"basis": basis}
    if basis == "fixed_shares":
        vf["allocation"]["shares"] = {parts[0]: 0.7, parts[1]: 0.3}
    raw["value_flows"] = vf
    n, cfg = _solve(n, raw, multi=multi)
    inputs, vf_obj, ledger, res = _ledger(n, cfg)
    assert inputs.hub is not None and not any(hp.flags for hp in inputs.hub.periods.values())
    for p in inputs.periods:
        lines = [ln for ln in ledger.periods[p] if ln.source == "allocation"]
        split: dict[str, float] = {}
        for ln in lines:
            assert ln.amount is not None, ln
            sign = 1.0 if ln.payee == "site" else -1.0      # + the member pays the hub
            split[ln.source_id] = split.get(ln.source_id, 0.0) + sign * ln.amount
        # Every billed item with money is split in full (the site holds no share).
        for item, v in inputs.bill[p].items():
            if v:
                assert split[f"bill:{item}"] == pytest.approx(v, abs=1e-6), (p, item)
        assert res.periods[p].ok is True, res.periods[p].checks
