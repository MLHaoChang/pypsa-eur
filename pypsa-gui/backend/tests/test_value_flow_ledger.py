"""
The value-flow ledger: sources → lines, and the four checks (Edge Investment
Case P3 WP3.1). Pure unit tests on hand-built `LedgerInputs`; the solved-site
reconciliation (V1, V1b) is in `test_value_flow_reconciliation.py`.

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.1.
Every money flow is one double-entry line payer → payee (amount ≥ 0 or None);
checks: (1) double entry, (2) internal streams net to zero, (3) coverage — every
source maps to its lines, compared SIGNED so a swapped direction fails, (4)
reconciliation: Σ participants' net outflow to externals = cost_breakdown −
the LP commercial rows + the external-side bill, fees and contract lines − the
export-price revenue.
"""
from __future__ import annotations

import copy

import pytest

from models.commercial import ValueFlowConfig
from services.commercial import participants as P

VF = ValueFlowConfig.model_validate({
    "participants": [{"id": "site", "name": "Site", "role": "site_owner"},
                     {"id": "developer", "name": "Dev", "role": "developer"}],
    "asset_owners": [{"asset_id": "pv", "component": "Generator", "owner": "developer"}]})


def _inputs(**over) -> P.LedgerInputs:
    """One flat period. Bill: energy 100 (cost), export item −20 (revenue),
    demand 30, a levy 5. A PPA site → developer 40. Connection fee 12.
    Export price revenue 8. Assets: pv (site side, developer) capex 50 fom 5
    opex 0; bess (site side, unassigned) capex 20 fom 0 opex 1; grid_supply
    (grid side) opex 60. cost_breakdown total = assets 136 + LP commercial rows
    (energy 90, export −25, demand 30, fee 12, ppa 40 = 147) = 283."""
    base = dict(
        periods=["_"], site_party="site",
        bill_items={
            "energy": P.BillItem("energy", "energy", "import", "cost"),
            "export": P.BillItem("export", "energy", "export", "revenue"),
            "demand": P.BillItem("demand", "demand", "import", "cost"),
            "levy": P.BillItem("levy", "tax_levy", "import", "cost")},
        bill={"_": {"energy": 100.0, "export": -20.0, "demand": 30.0, "levy": 5.0}},
        bill_flags={"_": []},
        retailer=None,
        settlement=[{"period": "_", "contract_id": "ppa1", "payer": "site",
                     "payee": "developer", "value_stream": "ppa_energy", "amount": 40.0,
                     "quantity_mwh": 1.0, "flags": []}],
        connection_fee={"_": 12.0}, connection_fixed_fee={},
        curtailment_compensation=False,
        export_revenue={"_": 8.0}, export_split=None,
        assets=[
            P.AssetCost("Generator", "pv", "site", [], {"_": 50.0}, {"_": 5.0}, {"_": 0.0}),
            P.AssetCost("StorageUnit", "bess", "site", [], {"_": 20.0}, {"_": 0.0}, {"_": 1.0}),
            P.AssetCost("Generator", "grid_supply", "grid", [], {"_": 0.0}, {"_": 0.0},
                        {"_": 60.0})],
        cost_breakdown_total={"_": 283.0},
        lp_commercial={"_": 147.0},
        disclosures={"_": {"dsr_slack": None, "voll": None}},
    )
    base.update(over)
    return P.LedgerInputs(**base)


def _lines(ledger, **match):
    return [ln for ln in ledger.periods["_"]
            if all(getattr(ln, k) == v for k, v in match.items())]


def _check(result, name):
    (c,) = [c for c in result.periods["_"].checks if c["name"] == name]
    return c


# ── sources → lines ────────────────────────────────────────────────────────


def test_bill_items_run_between_the_site_and_their_resolved_payee():
    led = P.build_ledger(_inputs(), VF)
    (energy,) = _lines(led, tariff_item="energy")
    assert (energy.payer, energy.payee, energy.amount) == ("site", "retailer", 100.0)
    assert energy.value_stream == "energy_import"
    (export,) = _lines(led, tariff_item="export")          # a revenue item: reversed
    assert (export.payer, export.payee, export.amount) == ("retailer", "site", 20.0)
    assert export.value_stream == "energy_export"
    (demand,) = _lines(led, tariff_item="demand")
    assert (demand.payee, demand.value_stream) == ("dso", "demand_charge")
    (levy,) = _lines(led, tariff_item="levy")
    assert (levy.payee, levy.value_stream, levy.tariff_item_kind) == \
        ("tax_authority", "tax", "tax_levy")
    assert led.tariff_payees == {"energy": "retailer", "export": "retailer",
                                 "demand": "dso", "levy": "tax_authority"}


def test_a_negative_cost_item_is_reversed_not_absolute_valued():
    led = P.build_ledger(_inputs(bill={"_": {"energy": -7.0, "export": -20.0,
                                             "demand": 30.0, "levy": 5.0}}), VF)
    (energy,) = _lines(led, tariff_item="energy")
    assert (energy.payer, energy.payee, energy.amount) == ("retailer", "site", 7.0)


def test_payee_precedence_rule_then_retail_contract_then_default():
    vf = VF.model_copy(update={"tariff_payees": [
        P.TariffPayeeRule(item_id="demand", payee="retailer"),
        P.TariffPayeeRule(kind="energy", payee="dso")]})
    led = P.build_ledger(_inputs(retailer="Big Utility"), vf.model_copy(update={
        "externals": [*vf.externals, "Big Utility"]}))
    assert led.tariff_payees["demand"] == "retailer"          # item rule
    assert led.tariff_payees["energy"] == "dso"                # kind rule
    assert led.tariff_payees["levy"] == "Big Utility"          # the retail contract
    (energy,) = _lines(led, tariff_item="energy")
    assert energy.value_stream == "network_energy"             # a DSO-paid energy item


def test_an_unrated_item_stays_a_none_line_with_its_flag():
    led = P.build_ledger(_inputs(bill={"_": {"energy": None, "export": -20.0,
                                             "demand": 30.0, "levy": 5.0}},
                                 bill_flags={"_": ["energy:unrated_intervals:3"]}), VF)
    (energy,) = _lines(led, tariff_item="energy")
    assert energy.amount is None and "bill_item_not_established" in energy.flags


def test_settlement_lines_map_their_streams_and_reverse_negative_amounts():
    settlement = [
        {"period": "_", "contract_id": "cfd", "payer": "State", "payee": "site",
         "value_stream": "cfd_difference", "amount": -3.0, "quantity_mwh": 1.0, "flags": []},
        {"period": "_", "contract_id": "ppa1", "payer": "site", "payee": "developer",
         "value_stream": "ppa_excess_mwh", "amount": 0.0, "quantity_mwh": 2.0,
         "flags": ["ppa_volume_cap_exceeded"]},
        {"period": "_", "contract_id": "x", "payer": "site", "payee": "developer",
         "value_stream": "mystery", "amount": 1.0, "quantity_mwh": None, "flags": []}]
    vf = VF.model_copy(update={"externals": [*VF.externals, "State"]})
    led = P.build_ledger(_inputs(settlement=settlement), vf)
    (cfd,) = _lines(led, contract_id="cfd")
    assert (cfd.payer, cfd.payee, cfd.amount, cfd.value_stream) == \
        ("site", "State", 3.0, "cfd_settlement")
    assert not _lines(led, contract_id="ppa1")                # a volume, no money
    assert any("ppa_excess_mwh" in n for n in led.notes)
    (odd,) = _lines(led, contract_id="x")
    assert odd.value_stream == "other" and "unmapped_stream:mystery" in odd.flags


def test_assets_pay_their_suppliers_by_owner_and_basis():
    led = P.build_ledger(_inputs(), VF)
    pv = _lines(led, asset="pv")
    assert {(ln.payer, ln.payee, ln.value_stream, ln.basis, ln.amount) for ln in pv} == {
        ("developer", "capex_supplier", "capex", "annuity", 50.0),
        ("developer", "om_contractor", "fom", "cash", 5.0)}           # zero opex: no line
    bess = _lines(led, asset="bess")
    assert {ln.payer for ln in bess} == {"site"}                      # unassigned → site
    assert any("asset_owner_defaulted" in n for n in led.notes)
    (grid,) = _lines(led, asset="grid_supply")
    assert (grid.payer, grid.payee, grid.value_stream) == ("site", "market", "energy_import")
    assert "commodity_from_grid_side_generator" in grid.flags


def test_grid_side_fixed_cost_is_model_only_and_unclassified_is_flagged():
    assets = [P.AssetCost("Link", "far", "grid", [], {"_": 9.0}, {"_": 0.0}, {"_": 0.0}),
              P.AssetCost("Generator", "lost", "unclassified", [], {"_": 0.0}, {"_": 0.0},
                          {"_": 2.0})]
    led = P.build_ledger(_inputs(assets=assets), VF)
    (far,) = _lines(led, asset="far")
    assert (far.payee, far.basis, far.value_stream) == ("market", "model_only", "other")
    assert "grid_side_asset_cost" in far.flags
    (lost,) = _lines(led, asset="lost")
    assert lost.payer == "site" and "asset_side_unclassified" in lost.flags


def test_connection_fees_export_revenue_and_curtailment_compensation():
    led = P.build_ledger(_inputs(connection_fixed_fee={"_": 4.0},
                                 curtailment_compensation=True), VF)
    fees = _lines(led, source="connection")
    assert {(ln.payer, ln.payee, ln.amount) for ln in fees} == {("site", "dso", 12.0),
                                                              ("site", "dso", 4.0)}
    (exp,) = _lines(led, source="export_price")
    assert (exp.payer, exp.payee, exp.amount) == ("market", "site", 8.0)
    (cc,) = _lines(led, source="curtailment_compensation")
    assert cc.amount is None and "curtailment_compensation_not_computed" in cc.flags


def test_a_negative_export_price_revenue_runs_from_the_site():
    led = P.build_ledger(_inputs(export_revenue={"_": -3.0}), VF)
    (exp,) = _lines(led, source="export_price")
    assert (exp.payer, exp.payee, exp.amount) == ("site", "market", 3.0)


def test_export_revenue_to_the_asset_owner_splits_each_source():
    split = {"_": {"export_price": {("Generator", "pv"): 6.0, None: 2.0},
                   "export": {("Generator", "pv"): -15.0, None: -5.0}}}
    vf = VF.model_copy(update={"export_revenue_to": "asset_owner"})
    led = P.build_ledger(_inputs(export_split=split), vf)
    price = {(ln.payee, ln.amount) for ln in _lines(led, source="export_price")}
    assert price == {("developer", 6.0), ("site", 2.0)}
    item = {(ln.payer, ln.payee, ln.amount) for ln in _lines(led, tariff_item="export")}
    assert item == {("retailer", "developer", 15.0), ("retailer", "site", 5.0)}


def test_a_party_that_no_longer_resolves_is_flagged():
    settlement = [{"period": "_", "contract_id": "ppa1", "payer": "site",
                   "payee": "Gone Ltd", "value_stream": "ppa_energy", "amount": 40.0,
                   "quantity_mwh": 1.0, "flags": []}]
    led = P.build_ledger(_inputs(settlement=settlement), VF)
    (ppa,) = _lines(led, contract_id="ppa1")
    assert "party_not_established:Gone Ltd" in ppa.flags


# ── the four checks ────────────────────────────────────────────────────────


def test_the_fixture_ledger_passes_every_check():
    inputs = _inputs()
    res = P.check_conservation(P.build_ledger(inputs, VF), inputs, VF)
    assert res.ok is True, res.periods["_"].checks
    assert [c["name"] for c in res.periods["_"].checks] == \
        ["double_entry", "internal_nets_to_zero", "coverage", "reconciliation"]


def _corrupt(fn):
    inputs = _inputs()
    led = P.build_ledger(inputs, VF)
    fn(led.periods["_"])
    return P.check_conservation(led, inputs, VF)


def test_a_dropped_source_fails_coverage_and_reconciliation():
    res = _corrupt(lambda lines: lines.remove(next(l for l in lines if l.asset == "bess")))
    assert res.ok is False
    assert not _check(res, "coverage")["ok"] and not _check(res, "reconciliation")["ok"]


def test_a_duplicated_source_fails():
    res = _corrupt(lambda lines: lines.append(copy.copy(
        next(l for l in lines if l.tariff_item == "demand"))))
    assert not _check(res, "coverage")["ok"] and not _check(res, "reconciliation")["ok"]


def test_a_swapped_direction_fails():
    def swap(lines):
        ln = next(l for l in lines if l.tariff_item == "energy")
        ln.payer, ln.payee = ln.payee, ln.payer
    res = _corrupt(swap)
    assert not _check(res, "coverage")["ok"] and not _check(res, "reconciliation")["ok"]


def test_an_amount_off_by_a_cent_fails():
    def nudge(lines):
        next(l for l in lines if l.contract_id == "ppa1").amount += 0.01
    res = _corrupt(nudge)
    assert not _check(res, "coverage")["ok"]


def test_a_phantom_internal_party_fails_double_entry():
    def phantom(lines):
        ln = next(l for l in lines if l.contract_id == "ppa1")
        ln.payee = ln.payer
    res = _corrupt(phantom)
    assert not _check(res, "double_entry")["ok"]


def test_a_mis_resolved_payee_fails_reconciliation():
    """A demand charge paid to an internal DSO leaves the external total short."""
    raw = VF.model_dump(mode="json")
    vf = ValueFlowConfig.model_validate({
        **raw, "participants": [*raw["participants"],
                                {"id": "dso", "name": "DSO", "role": "dso"}],
        "externals": [e for e in raw["externals"] if e != "dso"]})
    inputs = _inputs()
    led = P.build_ledger(inputs, vf)
    (demand,) = _lines(led, tariff_item="demand")
    demand.payee = "retailer"                 # as if resolved to an external
    res = P.check_conservation(led, inputs, vf)
    assert not _check(res, "reconciliation")["ok"]


def test_an_internal_dso_still_reconciles():
    """V4's shape: the demand charge and the fee run between participants and
    drop out of both sides of check 4."""
    vf = ValueFlowConfig.model_validate({
        "participants": [{"id": "site", "name": "S", "role": "developer"},
                         {"id": "dso", "name": "DSO", "role": "dso"},
                         {"id": "developer", "name": "D", "role": "developer"}],
        "externals": ["retailer", "tso", "market", "tax_authority", "capex_supplier",
                      "om_contractor"]})
    inputs = _inputs()
    res = P.check_conservation(P.build_ledger(inputs, vf), inputs, vf)
    assert res.ok is True, res.periods["_"].checks


def test_a_none_line_makes_the_result_none_never_true():
    inputs = _inputs(bill={"_": {"energy": None, "export": -20.0, "demand": 30.0,
                                 "levy": 5.0}})
    res = P.check_conservation(P.build_ledger(inputs, VF), inputs, VF)
    assert res.ok is None
    assert any(f.startswith("ledger_incomplete:") for f in res.flags)


def test_an_unresolved_party_makes_the_result_none():
    settlement = [{"period": "_", "contract_id": "ppa1", "payer": "site",
                   "payee": None, "value_stream": "ppa_energy", "amount": 40.0,
                   "quantity_mwh": 1.0, "flags": ["party_not_established"]}]
    inputs = _inputs(settlement=settlement)
    res = P.check_conservation(P.build_ledger(inputs, VF), inputs, VF)
    assert res.ok is None


def test_a_line_between_two_externals_is_excluded_and_noted():
    settlement = [{"period": "_", "contract_id": "sleeve", "payer": "retailer",
                   "payee": "market", "value_stream": "ppa_sleeving_fee", "amount": 2.0,
                   "quantity_mwh": 1.0, "flags": []}]
    inputs = _inputs(settlement=_inputs().settlement + settlement)
    led = P.build_ledger(inputs, VF)
    res = P.check_conservation(led, inputs, VF)
    assert res.ok is True, res.periods["_"].checks
    assert any("between_externals" in n for n in led.notes)


def test_dsr_and_voll_are_disclosed_not_lines():
    inputs = _inputs(disclosures={"_": {"dsr_slack": 11.0, "voll": 0.0}})
    led = P.build_ledger(inputs, VF)
    assert not _lines(led, source="dsr_slack")
    assert led.disclosures["_"]["dsr_slack"] == 11.0
    assert "dsr_slack_not_a_cash_flow" in led.flags


def test_participant_and_stream_totals():
    inputs = _inputs()
    led = P.build_ledger(inputs, VF)
    tot = P.by_participant(led)["_"]
    dev = tot["developer"]
    assert dev["received"] == pytest.approx(40.0)
    assert dev["paid"] == pytest.approx(55.0)
    assert dev["net"] == pytest.approx(-15.0)
    assert dev["by_stream"]["ppa_settlement"] == pytest.approx(40.0)
