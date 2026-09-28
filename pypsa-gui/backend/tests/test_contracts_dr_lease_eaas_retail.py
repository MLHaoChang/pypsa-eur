"""
DR, lease, EaaS and retail settlement (Edge Investment Case P2 WP2.2b).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2b.
Expected values are the C1 fixture's arithmetic written out here, to the cent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models.commercial import (DrContract, EaasContract, LeaseContract, RetailContract,
                               Tariff)
from services.commercial import contracts as K
from tests.fixtures.investment_case import c1_contracts as C1

WEEK_H = 7 * 24


@pytest.fixture
def c1():
    return C1.build()


def _inputs(c1, *, dsr=True, weights=None, index=None, year=None):
    return K.SettlementInputs(
        period=None, index=index if index is not None else c1["index"],
        weights=c1["weights"] if weights is None else weights,
        generators=c1["generators"], export_mw=c1["export_mw"], loads=c1["loads"],
        load_bus=c1["load_bus"], dsr=(c1["dsr"], []) if dsr else (None, [
            "dr_activation_not_established"]),
        storage_discharge=c1["storage_discharge"], link_output=c1["link_output"],
        step_hours=np.full(len(c1["index"]), 0.25), modelled_year=year)


def _one(lines, stream):
    (line,) = [x for x in lines if x.value_stream == stream]
    return line


def _cents(x):
    return round(float(x), 2)


# ── DR ─────────────────────────────────────────────────────────────────────


def _dr(**kw):
    return DrContract(id="dr", availability_eur_per_mw_year=30_000.0,
                      activation_eur_per_mwh=250.0, load_ids=["site_load"],
                      counterparty="Aggregator", contracted_mw=3.0, **kw)


def test_dr_availability_is_prorated_to_the_represented_share_of_the_year(c1):
    line = _one(K.settle(_dr(), _inputs(c1)), "dr_availability")
    assert (line.payer, line.payee) == ("Aggregator", "site")
    assert _cents(line.amount) == _cents(30_000.0 * 3.0 * WEEK_H / 8760.0)


def test_dr_activation_is_the_bus_dsr_attributed_to_the_named_loads_share(c1):
    line = _one(K.settle(_dr(), _inputs(c1)), "dr_activation")
    on = c1["dsr"]["site"].to_numpy()
    share = 20.0 / (20.0 + 5.0)                                   # site_load of bus `site`
    mwh = float((on * share * 0.25).sum())
    assert line.quantity_mwh == pytest.approx(mwh)
    assert _cents(line.amount) == _cents(250.0 * mwh)
    assert mwh == pytest.approx(4.0 * share * (1.5 + 1.5 + 0.5))


def test_dr_events_are_counted_and_limits_flagged():
    idx = pd.date_range("2030-01-01", periods=10, freq="h")
    act = [0, 1, 1, 0, 0, 1, 0, 1, 1, 1]
    inp = K.SettlementInputs(
        period=None, index=idx, weights=np.ones(10), generators=pd.DataFrame(index=idx),
        loads=pd.DataFrame({"l": 1.0}, index=idx), load_bus={"l": "b"},
        dsr=(pd.DataFrame({"b": act}, index=idx, dtype=float), []), step_hours=np.ones(10))
    assert K._events(np.array(act, dtype=float), np.ones(10), idx) == (3, 3.0)
    # `max_events` is per calendar year: 3 events in 10 sampled hours are
    # 3 × 8760 / 10 = 2628 a year, and the estimate is disclosed.
    ok = DrContract(id="d", availability_eur_per_mw_year=0.0, activation_eur_per_mwh=1.0,
                    load_ids=["l"], counterparty="A", max_events=3000, max_duration_h=3.0)
    assert _one(K.settle(ok, inp), "dr_activation").flags == ["dr_events_extrapolated"]
    tight = ok.model_copy(update={"max_events": 2000, "max_duration_h": 2.0})
    assert _one(K.settle(tight, inp), "dr_activation").flags == [
        "dr_events_extrapolated", "dr_max_duration_exceeded", "dr_max_events_exceeded"]


def test_dr_without_a_dsr_record_or_contracted_mw_is_not_established(c1):
    no_mw = DrContract(id="dr", availability_eur_per_mw_year=1.0, activation_eur_per_mwh=1.0,
                       load_ids=["site_load"], counterparty="A")
    lines = K.settle(no_mw, _inputs(c1, dsr=False))
    assert _one(lines, "dr_availability").amount is None
    assert "contracted_mw_not_established" in _one(lines, "dr_availability").flags
    act = _one(lines, "dr_activation")
    assert act.amount is None and "dr_activation_not_established" in act.flags


def test_dr_on_assets_is_refused(c1):
    dr = DrContract(id="dr", availability_eur_per_mw_year=1.0, activation_eur_per_mwh=1.0,
                    asset_ids=["bess"], counterparty="A", contracted_mw=1.0)
    with pytest.raises(K.ContractError, match="asset_ids"):
        K.settle(dr, _inputs(c1))


def test_dr_on_representative_weeks_and_a_leap_year(c1):
    weights = np.full(len(c1["index"]), 8760.0 / len(c1["index"]))   # the week stands for a year
    line = _one(K.settle(_dr(), _inputs(c1, weights=weights)), "dr_availability")
    assert _cents(line.amount) == _cents(30_000.0 * 3.0)             # a full year's payment
    leap = _one(K.settle(_dr(), _inputs(c1, year=2032)), "dr_availability")
    assert _cents(leap.amount) == _cents(30_000.0 * 3.0 * WEEK_H / 8784.0)
    act = _one(K.settle(_dr(), _inputs(c1, weights=weights)), "dr_activation")
    share = 20.0 / 25.0
    assert act.quantity_mwh == pytest.approx(
        float((c1["dsr"]["site"].to_numpy() * share * weights).sum()))


# ── lease, EaaS, retail ────────────────────────────────────────────────────


def test_a_lease_pays_the_represented_share_of_the_annual_payment(c1):
    lease = LeaseContract(id="l", lessor="Owner", lessee="site", annual_payment=52_000.0,
                          tenor_years=10, asset_ids=["bess"])
    (line,) = K.settle(lease, _inputs(c1))
    assert (line.payer, line.payee, line.value_stream) == ("site", "Owner", "lease_payment")
    assert _cents(line.amount) == _cents(52_000.0 * WEEK_H / 8760.0)


def test_eaas_charges_delivered_energy_and_a_yearly_fee(c1):
    eaas = EaasContract(id="e", provider="ESCo", customer="site", fee_eur_per_mwh=12.0,
                        fee_eur_per_year=5_000.0, tenor_years=10, asset_ids=["pv", "bess", "chp"])
    (line,) = K.settle(eaas, _inputs(c1))
    delivered = float(((c1["generators"]["pv"] + c1["storage_discharge"]["bess"]
                        + c1["link_output"]["chp"]) * 0.25).sum())
    assert line.quantity_mwh == pytest.approx(delivered)
    assert _cents(line.amount) == _cents(12.0 * delivered + 5_000.0 * WEEK_H / 8760.0)
    bad = eaas.model_copy(update={"asset_ids": ["nope"]})
    with pytest.raises(K.ContractError, match="nope"):
        K.settle(bad, _inputs(c1))


def test_retail_adds_no_lines_and_names_the_payer_and_payee_of_the_tariff_bill(c1):
    tariff = Tariff.model_validate({"id": "nl-tou", "name": "t", "jurisdiction": "NL",
                                    "valid_from": "2030-01-01", "items": [
                                        {"id": "e", "kind": "energy", "unit": "per_kwh",
                                         "periods": [{"name": "all", "rate": 0.2}]}]})
    retail = RetailContract(id="r", retailer="Energie BV", customer="site", tariff_id="nl-tou",
                            tenor_years=1)
    assert K.settle(retail, _inputs(c1)) == []
    assert K.retail_parties(retail, tariff) == ("site", "Energie BV")
    with pytest.raises(K.ContractError, match="tariff"):
        K.retail_parties(retail.model_copy(update={"tariff_id": "other"}), tariff)



# ── WP2.2b review round 1 ──────────────────────────────────────────────────


def _hand(dsr_cols, loads, act, idx=None):
    idx = idx if idx is not None else pd.date_range("2030-01-01", periods=len(act), freq="h")
    return K.SettlementInputs(
        period=None, index=idx, weights=np.ones(len(idx)), generators=pd.DataFrame(index=idx),
        loads=pd.DataFrame(loads, index=idx), load_bus={"l": "b", "m": "c"},
        dsr=(pd.DataFrame({c: act for c in dsr_cols}, index=idx, dtype=float), []))


def _dr_on(load="l"):
    return DrContract(id="d", availability_eur_per_mw_year=0.0, activation_eur_per_mwh=1.0,
                      load_ids=[load], counterparty="A")


def test_dr_never_settles_a_silent_zero():
    """#1 (ADR-0001): a bus without DSR, NaN rows, activation with no load."""
    act = [1.0, 0.0, 1.0]
    line = _one(K.settle(_dr_on("m"), _hand(["b"], {"l": 1.0, "m": 1.0}, act)),
                "dr_activation")
    assert line.amount is None and "dr_bus_not_dsr_enabled" in line.flags
    line = _one(K.settle(_dr_on(), _hand(["b"], {"l": [1.0, np.nan, 1.0], "m": 1.0}, act)),
                "dr_activation")
    assert line.amount is None and "dr_activation_not_established" in line.flags
    line = _one(K.settle(_dr_on(), _hand(["b"], {"l": 0.0, "m": 1.0}, act)), "dr_activation")
    assert line.amount is None and "dr_attribution_not_established" in line.flags


def test_events_on_representative_days_break_at_the_gap():
    """#2: the last hour of one sampled day and the first of the next are two
    events, measured in real hours (not represented ones)."""
    idx = pd.DatetimeIndex(["2030-01-07 22:00", "2030-01-07 23:00",
                            "2030-07-07 00:00", "2030-07-07 01:00"])
    inp = _hand(["b"], {"l": 1.0, "m": 1.0}, [0.0, 1.0, 1.0, 0.0], idx=idx)
    inp.weights = np.full(4, 8760.0 / 4)                           # two days stand for a year
    dr = _dr_on().model_copy(update={"max_duration_h": 1.0})
    assert K._events(np.array([0.0, 1.0, 1.0, 0.0]), K._step_hours(inp), idx) == (2, 1.0)
    assert "dr_max_duration_exceeded" not in _one(K.settle(dr, inp), "dr_activation").flags


def test_an_eaas_contract_needs_a_fee():
    """#3: a fee-less EaaS would settle a confident 0."""
    with pytest.raises(ValueError, match="fee"):
        EaasContract(id="e", provider="p", customer="c", tenor_years=1, asset_ids=["pv"])


def test_every_contract_type_carries_base_year_and_library_ref():
    """#4."""
    for cls in (LeaseContract, EaasContract, RetailContract):
        assert {"base_year", "library_ref"} <= set(cls.model_fields)
