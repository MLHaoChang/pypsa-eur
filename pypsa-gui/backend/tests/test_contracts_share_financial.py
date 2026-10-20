"""
A contract's share of its assets' output, and the financial PPA the site sells
(IPP renewables + BESS ticket 01; `.scratch/ipp-renewables-bess/spec.md` §10 G1, G2).

* `share` (G1): a PPA or CfD settles `share × gen`, before any volume cap; a baseload PPA
  refuses a share other than 1 (its volume is `baseload_mw`).
* `settlement: financial` (G2): a fixed-price pay-as-produced PPA settles `(price − ref) ×
  share × gen` through the CfD's difference code, so a site that sells it and earns the export
  price on every MWh is paid "price on the share, merchant on the rest" — never twice. Its
  `suspend_on_negative_price` defaults to True (the LP curtails at a negative price).

Every expected value is the C1 fixture's arithmetic written out here, independently of
`services/commercial/contracts.py`.
"""
from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from models.commercial import CfdContract, PpaContract
from services.commercial import contracts as K
from services.commercial import hashing as H
from tests.fixtures.investment_case import c1_contracts as C1

REF = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
SOLD = dict(tenor_years=10, seller="site", buyer="Offtaker", asset_ids=["pv"])


@pytest.fixture
def c1():
    return C1.build()


def _inputs(c1, refs=None):
    return K.SettlementInputs(period=None, index=c1["index"], weights=c1["weights"],
                              generators=c1["generators"], export_mw=c1["export_mw"],
                              references=refs or {}, site_generators=c1["site_generators"])


def _one(lines, stream):
    (line,) = [x for x in lines if x.value_stream == stream]
    return line


def _cents(x):
    return round(float(x), 2)


# ── G1: share ──────────────────────────────────────────────────────────────


def test_a_pay_as_produced_share_settles_that_share_of_the_output(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=55.0, share=0.7, **SOLD)
    line = _one(K.settle(ppa, _inputs(c1)), "ppa_energy")
    mwh = 0.7 * c1["generators"]["pv"].to_numpy() * 0.25
    assert line.quantity_mwh == pytest.approx(mwh.sum())
    assert _cents(line.amount) == _cents(55.0 * mwh.sum())


def test_the_share_applies_before_the_volume_cap(c1):
    ppa = PpaContract(id="p", kind="pay_as_produced", price=55.0, share=0.5,
                      volume_cap_mwh_per_year=100.0, **SOLD)
    lines = K.settle(ppa, _inputs(c1))
    cap = 100.0 * (len(c1["index"]) * 0.25) / 8760.0
    settled, total = 0.0, 0.0
    for mwh in 0.5 * c1["generators"]["pv"].to_numpy() * 0.25:
        total += mwh
        settled += min(mwh, max(cap - settled, 0.0))
    assert _one(lines, "ppa_energy").quantity_mwh == pytest.approx(settled)
    assert _one(lines, "ppa_excess_mwh").quantity_mwh == pytest.approx(total - settled)


def test_a_cfd_share_settles_that_share_of_the_difference(c1):
    full = CfdContract(id="c", strike=70.0, tenor_years=15, asset_ids=["pv"],
                       reference_price=REF, generator_owner="site", counterparty="Agency")
    part = full.model_copy(update={"share": 0.4})
    refs = {"c": (c1["ref"], [])}
    a = _one(K.settle(full, _inputs(c1, refs)), "cfd_difference")
    b = _one(K.settle(part, _inputs(c1, refs)), "cfd_difference")
    assert b.amount == pytest.approx(0.4 * a.amount)
    assert b.quantity_mwh == pytest.approx(0.4 * a.quantity_mwh)


@pytest.mark.parametrize("share", [0.0, -0.1, 1.01])
def test_a_share_outside_zero_to_one_is_refused(share):
    with pytest.raises(ValidationError):
        PpaContract(id="p", kind="pay_as_produced", price=55.0, share=share, **SOLD)
    with pytest.raises(ValidationError):
        CfdContract(id="c", strike=70.0, tenor_years=15, asset_ids=["pv"], share=share)


def test_a_baseload_ppa_refuses_a_share():
    with pytest.raises(ValidationError, match="baseload"):
        PpaContract(id="p", kind="baseload", price=55.0, baseload_mw=2.0, share=0.5,
                    reference_price=REF, **SOLD)


# ── G2: the financial PPA the site sells ───────────────────────────────────


def _financial(**kw):
    base = dict(id="p", kind="pay_as_produced", price=55.0, share=0.7, settlement="financial",
                reference_price=REF, **SOLD)
    return PpaContract(**{**base, **kw})


def test_a_financial_ppa_settles_price_minus_reference_on_the_share(c1):
    ppa = _financial(suspend_on_negative_price=False)
    line = _one(K.settle(ppa, _inputs(c1, {"p": (c1["ref"], [])})), "ppa_energy")
    mwh = 0.7 * c1["generators"]["pv"].to_numpy() * 0.25
    ref = c1["ref"].to_numpy()
    assert (line.payer, line.payee) == ("Offtaker", "site")
    assert _cents(line.amount) == _cents(((55.0 - ref) * mwh).sum())
    assert line.quantity_mwh == pytest.approx(mwh.sum())


def test_the_financial_ppa_and_merchant_export_pay_price_on_the_share_and_merchant_on_the_rest(c1):
    """The cash identity with a battery (gatekeeper, 2026-10-20): Σ ref·export + Σ (P − ref)·s·gen
    = Σ P·s·gen + Σ ref·(export − s·gen). The merchant rest (export − s·gen) goes negative in the
    hours the battery charges from the PV and nothing is exported: the identity still holds."""
    ppa = _financial(suspend_on_negative_price=False)
    line = _one(K.settle(ppa, _inputs(c1, {"p": (c1["ref"], [])})), "ppa_energy")
    w = c1["weights"]
    ref = c1["ref"].to_numpy()
    gen = c1["generators"]["pv"].to_numpy()
    export = c1["export_mw"].to_numpy()
    merchant_all = float((ref * export * w).sum())
    rest = export - 0.7 * gen
    assert (rest < 0).any()                                  # the battery charges from the PV
    physical = float((55.0 * 0.7 * gen * w).sum() + (ref * rest * w).sum())
    assert _cents(merchant_all + line.amount) == _cents(physical)


def test_a_financial_ppa_suspends_at_negative_prices_by_default(c1):
    ppa = _financial()
    assert ppa.suspend_on_negative_price is True
    line = _one(K.settle(ppa, _inputs(c1, {"p": (c1["ref"], [])})), "ppa_energy")
    mwh = 0.7 * c1["generators"]["pv"].to_numpy() * 0.25
    ref = c1["ref"].to_numpy()
    paid = np.where(ref < 0, 0.0, mwh)
    assert (ref < 0).any() and (mwh[ref < 0] > 0).any()
    assert _cents(line.amount) == _cents(((55.0 - ref) * paid).sum())
    assert line.quantity_mwh == pytest.approx(paid.sum())


def test_the_financial_ppa_uses_the_cfd_difference_code(c1):
    """One calculation (gatekeeper): the financial PPA equals a CfD on the same share."""
    ppa = _financial()
    cfd = CfdContract(id="p", strike=55.0, tenor_years=10, asset_ids=["pv"], share=0.7,
                      reference_price=REF, generator_owner="site", counterparty="Offtaker",
                      suspend_on_negative_price=True)
    refs = {"p": (c1["ref"], [])}
    a = _one(K.settle(ppa, _inputs(c1, refs)), "ppa_energy")
    b = _one(K.settle(cfd, _inputs(c1, refs)), "cfd_difference")
    assert a.amount == pytest.approx(b.amount)
    assert a.quantity_mwh == pytest.approx(b.quantity_mwh)


def test_a_financial_ppa_without_its_reference_is_none_with_a_flag(c1):
    line = _one(K.settle(_financial(), _inputs(c1, {"p": (None, ["reference_price_missing"])})),
                "ppa_energy")
    assert line.amount is None and "reference_price_missing" in line.flags


@pytest.mark.parametrize("bad", [
    dict(reference_price=None),                                         # needs its reference
    dict(kind="sleeved", sleeving_fee_eur_per_mwh=1.0, sleeving_party="S"),
    dict(kind="as_consumed_btm"),
    dict(pricing="market_plus_premium", premium_eur_per_mwh=5.0),
    dict(changes_dispatch=True),
])
def test_financial_settlement_is_a_fixed_price_pay_as_produced_form_only(bad):
    with pytest.raises(ValidationError):
        _financial(**bad)


def test_the_negative_price_clause_belongs_to_the_financial_form_only():
    with pytest.raises(ValidationError, match="financial"):
        PpaContract(id="p", kind="pay_as_produced", price=55.0, suspend_on_negative_price=True,
                    **SOLD)


# ── hashes ─────────────────────────────────────────────────────────────────


#: Both recipes' digests of the two fixtures, computed on master `ceff6b1` before the fields existed.
PINNED = {"ppa": ("d6a2e6e22669bebe", "e11536d7a9b0601c"),
          "cfd": ("bfb9a833911b73b9", "f809e196ec5df3f2")}


def test_existing_ppa_and_cfd_hashes_do_not_move():
    """The new fields at their defaults leave both hash recipes as they were."""
    ppa = PpaContract(id="p", kind="pay_as_produced", price=55.0, **SOLD)
    cfd = CfdContract(id="c", strike=70.0, tenor_years=15, asset_ids=["pv"],
                      reference_price=REF)
    for key, obj in (("ppa", ppa), ("cfd", cfd)):
        assert (H.digest(obj, version=1), H.digest(obj, version=2)) == PINNED[key]
    assert H.FIELDS_AFTER_V1[("PpaContract", "share")] == 1.0
    assert H.FIELDS_AFTER_V1[("CfdContract", "share")] == 1.0
    assert H.FIELDS_AFTER_V1[("PpaContract", "settlement")] == "physical"
    assert H.FIELDS_AFTER_V1[("PpaContract", "suspend_on_negative_price")] is None
