"""
PPA (four kinds) and CfD settlement (Edge Investment Case P2 WP2.2a).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2a.
Every expected value is the C1 fixture's arithmetic written out here, to the
cent, independently of `services/commercial/contracts.py`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models.commercial import CfdContract, CommercialConfig, PpaContract
from services.commercial import contracts as K
from tests.fixtures.investment_case import c1_contracts as C1

REF = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
BASE = dict(tenor_years=10, seller="Wind BV", buyer="site", asset_ids=["pv"])


def _inputs(c1, *, refs=None, period=None, year=None):
    return K.SettlementInputs(period=period, index=c1["index"], weights=c1["weights"],
                              generators=c1["generators"], export_mw=c1["export_mw"],
                              references=refs or {}, modelled_year=year)


def _cents(x):
    return round(float(x), 2)


@pytest.fixture
def c1():
    return C1.build()


def _one(lines, stream="ppa_energy"):
    (line,) = [x for x in lines if x.value_stream == stream]
    return line


# ── PPA ────────────────────────────────────────────────────────────────────


def test_pay_as_produced_fixed_price_to_the_cent(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=55.0, **BASE)
    line = _one(K.settle(ppa, _inputs(c1)))
    pv_mwh = c1["generators"]["pv"].to_numpy() * 0.25
    assert (line.payer, line.payee) == ("site", "Wind BV")
    assert _cents(line.amount) == _cents(55.0 * pv_mwh.sum())
    assert line.quantity_mwh == pytest.approx(pv_mwh.sum())
    assert line.flags == []


def test_indexation_to_the_modelled_year(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=50.0, indexation_pct_per_year=2.0,
                      base_year=2028, **BASE)
    line = _one(K.settle(ppa, _inputs(c1)))                     # the snapshots' year: 2030
    pv_mwh = (c1["generators"]["pv"] * 0.25).sum()
    assert _cents(line.amount) == _cents(50.0 * 1.02 ** 2 * pv_mwh)
    no_base = PpaContract(id="p", kind="pay_as_produced", price=50.0,
                          indexation_pct_per_year=2.0, **BASE)
    assert _cents(_one(K.settle(no_base, _inputs(c1))).amount) == _cents(50.0 * pv_mwh)


def test_a_volume_cap_is_filled_chronologically_and_the_excess_is_not_settled(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=55.0, volume_cap_mwh_per_year=100.0,
                      **BASE)
    lines = K.settle(ppa, _inputs(c1))
    cap = 100.0 * (len(c1["index"]) * 0.25) / 8760.0             # the week's share of 2030
    settled, total = 0.0, 0.0
    for mwh in c1["generators"]["pv"].to_numpy() * 0.25:
        total += mwh
        settled += min(mwh, max(cap - settled, 0.0))
    energy, excess = _one(lines), _one(lines, "ppa_excess_mwh")
    assert _cents(energy.amount) == _cents(55.0 * settled)
    assert excess.quantity_mwh == pytest.approx(total - settled)
    assert excess.amount == 0.0 and "ppa_volume_cap_exceeded" in excess.flags


def test_market_plus_premium_clamps_to_floor_and_cap(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=0.0, pricing="market_plus_premium",
                      premium_eur_per_mwh=5.0, floor=0.0, cap=80.0, reference_price=REF, **BASE)
    line = _one(K.settle(ppa, _inputs(c1, refs={"p": (c1["ref"], [])})))
    eff = np.clip(c1["ref"].to_numpy() + 5.0, 0.0, 80.0)
    expected = sum(e * g * 0.25 for e, g in zip(eff, c1["generators"]["pv"]))
    assert _cents(line.amount) == _cents(expected)


def test_a_financial_baseload_ppa_settles_the_difference_on_its_shape(c1):
    ppa = PpaContract(id="p", kind="baseload", price=55.0, baseload_mw=1.5,
                      reference_price=REF, **BASE)
    line = _one(K.settle(ppa, _inputs(c1, refs={"p": (c1["ref"], [])})))
    expected = sum((55.0 - r) * 1.5 * 0.25 for r in c1["ref"])
    assert _cents(line.amount) == _cents(expected)
    assert line.quantity_mwh == pytest.approx(1.5 * 0.25 * len(c1["index"]))


def test_as_consumed_btm_attributes_export_pro_rata_and_counts_bess_charging(c1):
    ppa = PpaContract(id="p", kind="as_consumed_btm", price=55.0, **BASE)
    line = _one(K.settle(ppa, _inputs(c1)))
    consumed = 0.0
    for pv, wind, exp in zip(c1["generators"]["pv"], c1["generators"]["wind"], c1["export_mw"]):
        share = pv / (pv + wind) if pv + wind > 0 else 0.0
        consumed += (pv - min(pv, exp * share)) * 0.25
    assert _cents(line.amount) == _cents(55.0 * consumed)
    produced = (c1["generators"]["pv"] * 0.25).sum()
    assert consumed < produced                                    # export days attribute some


def test_a_sleeved_ppa_adds_the_sleeving_fee_line(c1):
    ppa = PpaContract(id="p", kind="sleeved", price=55.0, sleeving_fee_eur_per_mwh=2.0,
                      sleeving_party="Sleever", **BASE)
    lines = K.settle(ppa, _inputs(c1))
    pv_mwh = (c1["generators"]["pv"] * 0.25).sum()
    fee = _one(lines, "ppa_sleeving_fee")
    assert (fee.payer, fee.payee) == ("site", "Sleever")
    assert _cents(fee.amount) == _cents(2.0 * pv_mwh)
    assert _cents(_one(lines).amount) == _cents(55.0 * pv_mwh)
    no_party = PpaContract(id="p", kind="sleeved", price=55.0, sleeving_fee_eur_per_mwh=2.0,
                           **BASE)
    assert "party_not_established" in _one(K.settle(no_party, _inputs(c1)),
                                           "ppa_sleeving_fee").flags


def test_a_missing_reference_is_none_with_a_flag(c1):
    ppa = PpaContract(id="p", kind="baseload", price=55.0, baseload_mw=1.5,
                      reference_price=REF, **BASE)
    line = _one(K.settle(ppa, _inputs(c1, refs={"p": (None, ["reference_price_missing"])})))
    assert line.amount is None and line.flags == ["reference_price_missing"]


def test_an_asset_that_is_not_a_generator_is_refused(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=55.0,
                      **{**BASE, "asset_ids": ["bess"]})
    with pytest.raises(K.ContractError, match="bess"):
        K.settle(ppa, _inputs(c1))


@pytest.mark.parametrize("bad", [
    dict(kind="baseload", pricing="market_plus_premium", premium_eur_per_mwh=1.0,
         reference_price=REF),
    dict(kind="pay_as_produced", pricing="market_plus_premium", reference_price=REF),
    dict(kind="pay_as_produced", pricing="market_plus_premium", premium_eur_per_mwh=1.0),
])
def test_disallowed_ppa_combinations_are_refused(bad):
    with pytest.raises(ValueError):
        PpaContract(id="p", price=55.0, **{**BASE, **bad})


# ── CfD ────────────────────────────────────────────────────────────────────


def _cfd(**kw):
    return CfdContract(id="c", strike=70.0, tenor_years=15, asset_ids=["pv"],
                       generator_owner="Owner", counterparty="Agency", reference_price=REF, **kw)


def test_an_interval_cfd_pays_the_difference_both_ways(c1):
    line = _one(K.settle(_cfd(), _inputs(c1, refs={"c": (c1["ref"], [])})), "cfd_difference")
    expected = sum((70.0 - r) * g * 0.25 for r, g in zip(c1["ref"], c1["generators"]["pv"]))
    assert (line.payer, line.payee) == ("Agency", "Owner")
    assert _cents(line.amount) == _cents(expected)


def test_suspension_on_negative_prices_settles_those_intervals_at_zero(c1):
    line = _one(K.settle(_cfd(suspend_on_negative_price=True),
                         _inputs(c1, refs={"c": (c1["ref"], [])})), "cfd_difference")
    expected = sum((70.0 - r) * g * 0.25 for r, g in zip(c1["ref"], c1["generators"]["pv"])
                   if r >= 0)
    assert _cents(line.amount) == _cents(expected)
    assert (c1["ref"] < 0).any()


def test_monthly_capture_uses_each_months_generation_weighted_reference():
    idx = pd.DatetimeIndex(["2030-01-31 12:00", "2030-01-31 13:00",
                            "2030-02-01 12:00", "2030-02-01 13:00"])
    gens = pd.DataFrame({"pv": [1.0, 3.0, 2.0, 2.0]}, index=idx)
    ref = pd.Series([10.0, 50.0, 20.0, 60.0], index=idx)
    inp = K.SettlementInputs(period=None, index=idx, weights=np.ones(4), generators=gens,
                             references={"c": (ref, [])})
    line = _one(K.settle(_cfd(reference="monthly_capture"), inp), "cfd_difference")
    jan = (10 * 1 + 50 * 3) / 4                                   # 40
    feb = (20 * 2 + 60 * 2) / 4                                   # 40
    assert line.amount == pytest.approx((70 - jan) * 4 + (70 - feb) * 4)
    interval = _one(K.settle(_cfd(), inp), "cfd_difference").amount
    assert interval == pytest.approx(line.amount)                 # one ref per month: equal here
    inp.generators = pd.DataFrame({"pv": [3.0, 1.0, 2.0, 2.0]}, index=idx)
    monthly = _one(K.settle(_cfd(reference="monthly_capture"), inp), "cfd_difference").amount
    assert monthly == pytest.approx((70 - 20) * 4 + (70 - 40) * 4)


def test_a_cfd_without_parties_is_flagged_and_indexed_by_its_own_rate(c1):
    cfd = CfdContract(id="c", strike=70.0, tenor_years=15, asset_ids=["pv"], reference_price=REF,
                      indexation_pct_per_year=3.0, base_year=2029)
    line = _one(K.settle(cfd, _inputs(c1, refs={"c": (c1["ref"], [])})), "cfd_difference")
    assert line.payer is None and "party_not_established" in line.flags
    strike = 70.0 * 1.03
    expected = sum((strike - r) * g * 0.25 for r, g in zip(c1["ref"], c1["generators"]["pv"]))
    assert _cents(line.amount) == _cents(expected)


def test_multi_period_settles_per_period_indexed_to_each_period(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=50.0, indexation_pct_per_year=2.0,
                      base_year=2030, **BASE)
    pv_mwh = (c1["generators"]["pv"] * 0.25).sum()
    for period in (2030, 2040):
        line = _one(K.settle(ppa, _inputs(c1, period=period)))
        assert line.period == period
        assert _cents(line.amount) == _cents(50.0 * 1.02 ** (period - 2030) * pv_mwh)


def test_new_contract_fields_keep_p0_payloads_valid():
    cfg = CommercialConfig.model_validate({"poc_link": "import", "contracts": [
        {"id": "p", "kind": "pay_as_produced", "price": 55.0, **BASE},
        {"id": "c", "strike": 70.0, "tenor_years": 15, "asset_ids": ["pv"]}]})
    ppa, cfd = cfg.contracts
    assert (ppa.pricing, ppa.base_year, ppa.library_ref) == ("fixed", None, None)
    assert (cfd.reference, cfd.suspend_on_negative_price, cfd.indexation_pct_per_year) == \
        ("interval", False, 0.0)
