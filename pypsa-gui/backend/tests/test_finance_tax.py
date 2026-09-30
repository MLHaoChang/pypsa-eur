"""
Tax and depreciation (IC P4 plan WP4.3a): SAM parity on depreciation, state and
federal taxable income and tax per layer (S1–S3f; S2/S3 fed SAM's own interest
and reserve interest until WP4.2 lands the debt), S1 after-tax equity cash,
IRR and NPV, and the schedule builders.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.finance.cashflow import build_operating
from services.finance.metrics import irr, npv
from services.finance.packs.base import load_pack
from services.finance.tax import (
    DepreciationClass, LossRule, TaxLayer, compute_tax, declining_balance, depreciation,
    normalised, sl_half_year, sl_pro_rata,
)
from services.finance.timeline import build_timeline
from tests.fixtures.investment_case.sam import sam_case as S


def _close(ours, sam, what=""):
    ours, sam = np.asarray(ours, dtype=float), np.asarray(sam, dtype=float)
    tol = np.maximum(1.0, 1e-6 * np.abs(sam))
    bad = np.abs(ours - sam) > tol
    assert not bad.any(), f"{what} years {np.flatnonzero(bad)}: ours {ours[bad]} sam {sam[bad]}"


def _run(name):
    case = S.to_finance_case(name)
    tl = build_timeline(case)
    op = build_operating(case, tl)
    e = S.sam_expected(name)["arrays"]
    p = S.sam_params(name)
    itc = p.itc_federal_percent * p.installed_cost
    tax = compute_tax(tl, S.sam_tax_layers(name), ebitda=op.ebitda, basis=p.installed_cost,
                      interest=np.asarray(e["cf_debt_payment_interest"]),
                      other_income=np.asarray(e["cf_reserve_interest"]),
                      itc_amount=itc, losses="offset_other_income")
    return tl, op, tax, e


@pytest.mark.parametrize("name", S.CASES)
def test_sam_parity_depreciation_and_tax_per_layer(name):
    tl, op, tax, e = _run(name)
    _close(tax.depreciation["state"], e["cf_stadepr_total"], "state depreciation")
    _close(tax.depreciation["federal"], e["cf_feddepr_total"], "federal depreciation")
    _close(tax.taxable["state"], e["cf_statax_income_with_incentives"], "state taxable")
    _close(tax.taxable["federal"], e["cf_fedtax_income_with_incentives"], "federal taxable")
    _close(-tax.liability["state"], e["cf_statax"], "state tax")
    _close(-tax.liability["federal"], e["cf_fedtax"], "federal tax")


@pytest.mark.parametrize("name", ["s1", "s1b"])
def test_sam_parity_all_equity_after_tax_cash_irr_and_npv(name):
    tl, op, tax, e = _run(name)
    pre = op.ebitda - op.capex
    post = pre - tax.total_liability
    _close(pre, e["cf_project_return_pretax"], "pre-tax equity cash")
    _close(post, e["cf_project_return_aftertax"], "after-tax equity cash")
    s = S.sam_expected(name)["scalars"]
    r, flags = irr(post)
    assert flags == [] and abs(r - s["equity_irr_post_tax"]) <= 1e-5
    assert npv(s["equity_discount_rate"], post) == pytest.approx(s["equity_npv_post_tax"], rel=1e-6)


def test_the_pack_macrs_tables_are_table_a1_and_sum_to_one():
    pack = load_pack("us_federal", as_of=__import__("datetime").date(2026, 1, 1))
    table = pack.rule("macrs_half_year_percent")
    assert table.status == "ok" and "Publication 946" in table.source
    for cls, pct in table.value.items():
        assert abs(sum(pct) - 100.0) < 1e-6, cls
        assert len(pct) == int(cls) + 1          # half-year convention: n + 1 years


def test_schedule_builders():
    assert sl_half_year(20)[:2] == (0.025, 0.05) and len(sl_half_year(20)) == 21
    assert sum(sl_half_year(7)) == pytest.approx(1.0)
    s = sl_pro_rata(20, months_first_year=7)
    assert s[0] == pytest.approx(7 / 12 / 20) and sum(s) == pytest.approx(1.0) and len(s) == 21
    assert sl_pro_rata(10, 12) == tuple([0.1] * 10)
    db = declining_balance(0.3, 10)
    assert db[0] == pytest.approx(0.3) and sum(db) == pytest.approx(1.0)
    # The switch to straight-line: once SL on the remaining life is larger.
    assert db[-1] == pytest.approx(db[-2])
    with pytest.raises(ValueError):
        normalised([0.5, 0.4])


def _flat_tl(n=6):
    from services.finance.timeline import Timeline
    return Timeline(y0=2030, cod_year=2030, base_year=2030, analysis_years=n)


def test_carryforward_with_an_allowance_and_a_limit_share():
    """German-style: €1m in full plus 60 % above it; a 5m loss then income."""
    tl = _flat_tl(4)
    layer = TaxLayer(name="kst", rate=0.15, depreciation=(),
                     loss=LossRule(allowance=1_000_000.0, limit_share=0.6))
    ebitda = np.array([-5_000_000.0, 3_000_000.0, 3_000_000.0, 3_000_000.0])
    t = compute_tax(tl, (layer,), ebitda=ebitda, basis=0.0, losses="carryforward")
    # Year 2: cap = 1m + 0.6 × 2m = 2.2m → taxable 0.8m; pool 2.8m.
    # Year 3: cap 2.2m → taxable 0.8m; pool 0.6m. Year 4: offset 0.6m → 2.4m.
    assert list(t.liability["kst"]) == pytest.approx([0.0, 0.12e6, 0.12e6, 0.36e6])
    assert list(t.loss_pool["kst"]) == pytest.approx([5e6, 2.8e6, 0.6e6, 0.0])
    off = compute_tax(tl, (layer,), ebitda=ebitda, basis=0.0, losses="offset_other_income")
    assert off.liability["kst"][0] == pytest.approx(-750_000.0)       # a benefit


def test_a_non_deductible_layer_an_addback_and_a_surcharge():
    """GewSt (not deductible, 25 % of interest above 200k added back) and KSt
    with SolZ 5.5 % on it."""
    tl = _flat_tl(1)
    gew = TaxLayer(name="gewst", rate=0.14, depreciation=(), deductible_in_later_layers=False,
                   interest_addback_share=0.25, interest_addback_allowance=200_000.0)
    kst = TaxLayer(name="kst", rate=0.15, depreciation=(), surcharge_share=0.055)
    t = compute_tax(tl, (gew, kst), ebitda=np.array([2_000_000.0]), basis=0.0,
                    interest=np.array([600_000.0]), losses="carryforward")
    assert t.taxable["gewst"][0] == pytest.approx(1_400_000.0 + 0.25 * 400_000.0)
    assert t.liability["gewst"][0] == pytest.approx(0.14 * 1_500_000.0)
    assert t.taxable["kst"][0] == pytest.approx(1_400_000.0)           # GewSt not deducted
    assert t.liability["kst"][0] == pytest.approx(0.15 * 1.055 * 1_400_000.0)


def test_a_rate_schedule_and_an_itc_basis_reduction():
    tl = _flat_tl(3)
    layer = TaxLayer(name="kst", rate={2020: 0.15, 2031: 0.14}, depreciation=(
        DepreciationClass("a", 1.0, (0.5, 0.5), itc_reduces=True),), itc_basis_reduction=True)
    t = compute_tax(tl, (layer,), ebitda=np.array([1000.0, 1000.0, 1000.0]), basis=1000.0,
                    itc_amount=300.0, losses="offset_other_income")
    assert list(t.depreciation["kst"]) == pytest.approx([425.0, 425.0, 0.0])   # basis 850
    assert t.liability["kst"][0] == pytest.approx(0.15 * 575.0)
    assert t.liability["kst"][1] == pytest.approx(0.14 * 575.0)
    d = depreciation(tl, 100.0, (DepreciationClass("b", 1.0, (1.0,), bonus=0.6),))
    assert list(d) == pytest.approx([100.0, 0.0, 0.0])                 # 60 bonus + 40 on schedule
