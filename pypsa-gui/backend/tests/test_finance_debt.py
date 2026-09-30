"""
Debt (IC P4 WP4.2a / WP4.2b): SAM parity on the gearing (S3), sculpted (S2)
and capped sculpted (S2c) schedules, the recorded gearing-with-fee deviation
(S3f), the F1 IDC hand oracle, repayment shapes, several tranches, the
total-uses fixed point, and every not-established path.
"""
from __future__ import annotations

import dataclasses
from datetime import date

import numpy as np
import pytest

from models.finance import DebtTranche, FinanceInputs, TerminalValueRule
from services.finance.case import AssetFinance, FinanceCase, Template, TemplateLine
from services.finance.cashflow import build_operating
from services.finance.debt import MAX_ITER, build_debt
from services.finance.timeline import build_timeline
from tests.fixtures.investment_case.sam import sam_case as S


def _close(a, b, tol=1e-6):
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert a.shape == b.shape
    assert np.max(np.abs(a - b)) <= tol * max(1.0, np.max(np.abs(b))), np.max(np.abs(a - b))


def _sam(name):
    case = S.to_finance_case(name)
    tl = build_timeline(case)
    op = build_operating(case, tl)
    return build_debt(case.inputs, op, tl), S.sam_expected(name), op


@pytest.mark.parametrize("name", ["s2", "s2c", "s3"])
def test_sam_debt_schedule_parity(name):
    d, e, op = _sam(name)
    a = e["arrays"]
    assert d.established(), d.reasons
    assert d.amount == pytest.approx(e["scalars"]["debt_size"], rel=1e-12)
    _close(d.interest, a["cf_debt_payment_interest"])
    _close(d.principal, a["cf_debt_payment_principal"])
    _close(d.tranches[0].balance, a["cf_debt_balance"])
    _close(d.dsra_balance, a["cf_reserve_debtservice"])
    # SAM books the release at maturity as a disbursement, the rest as funding.
    _close(d.dsra_funding, np.add(a["cf_funding_debtservice"], a["cf_disbursement_debtservice"]))
    _close(d.reserve_interest, a["cf_reserve_interest"])
    tenor = d.tranches[0].tranche.tenor_years
    _close(d.cfads[1:tenor + 1], a["cf_cash_for_ds"][1:tenor + 1])       # CFADS = EBITDA (C8)
    sam_dscr = np.array([np.nan if x is None else x for x in a["cf_pretax_dscr"]])
    m = ~np.isnan(sam_dscr)
    _close(d.dscr[m], sam_dscr[m], 1e-12)
    assert np.isnan(d.dscr[~m]).all()
    assert d.min_dscr() == pytest.approx(e["scalars"]["min_dscr"], rel=1e-12)
    # Sources and uses at COD = SAM's investing activities in year 0.
    assert d.uses["total"] == pytest.approx(-a["cf_project_investing_activities"][0], rel=1e-12)
    assert d.sources["equity"] == pytest.approx(d.uses["total"] - d.amount)


def test_s2c_the_cap_binds_and_scales_the_sculpted_service():
    d, e, _ = _sam("s2c")
    assert "max_gearing_binding:0" in d.flags
    assert d.amount == pytest.approx(0.6 * 112_068_000.0 * 1.0275, rel=1e-12)
    dscr = d.dscr[~np.isnan(d.dscr)]
    assert np.ptp(dscr) < 1e-9 and dscr[0] > 1.3              # a flat, higher DSCR
    assert "max_gearing_binding:0" not in _sam("s2")[0].flags


def test_s3f_the_gearing_with_fee_deviation_is_exactly_the_sam_one_step_term():
    """Plan C8: SAM debt = g·TIC·(1 + g·f); ours (`gearing_base="capex"`) =
    g·TIC; the difference is exactly g²·f·TIC = 0.36·f·TIC."""
    d, e, _ = _sam("s3f")
    tic, f = 112_068_000.0, 0.0275
    assert d.amount == pytest.approx(0.6 * tic, rel=1e-12)
    assert e["scalars"]["debt_size"] - d.amount == pytest.approx(0.36 * f * tic, rel=1e-9)
    assert e["deviations"][0]["name"] == "gearing_with_fee"


# ── hand-built cases ───────────────────────────────────────────────────────

def _case(debt, *, cod=date(2033, 1, 1), close=date(2031, 3, 1), phasing=(0.6, 0.4),
          years=20, revenue=200_000.0, capex=1_000_000.0, reserves_rate=0.02, lines=None):
    fin = FinanceInputs(financial_close=close, cod_by_asset={"pv": cod}, capex_phasing=list(phasing),
                        contingency_share=0.0, analysis_years=years, escalation={"ppa": 0.0},
                        degradation_by_asset={"pv": 0.0}, debt=debt, reserves_rate=reserves_rate)
    lines = lines or (TemplateLine("rev", "ppa_settlement", revenue, "ppa"),)
    return FinanceCase(inputs=fin, owner="o", base_year=cod.year, cod=cod,
                       templates=(Template(first_year=cod.year, lines=tuple(lines),
                                           energy_mwh={"pv": 1.0}),),
                       assets=(AssetFinance("pv", "Generator", capex, 40.0),))


def _run(case):
    tl = build_timeline(case)
    return build_debt(case.inputs, build_operating(case, tl), tl), tl


def _t(**kw):
    base = dict(kind="term_loan", rate=0.08, tenor_years=10, upfront_fee=0.0, dsra_months=0,
                grace_years=0, commitment_fee=0.0)
    base.update(kw)
    return DebtTranche(**base)


def test_f1_idc_two_construction_years():
    """F1 by hand: capex 1 M over two construction years 60/40, one loan at 8 %
    geared 60 % (debt at COD, IDC inside). Draws at the construction points;
    the year-0 draw compounds one year to the COD point, the year-1 draw none.
    A 0.5 % commitment fee on the undrawn 40 % over year 0 → year 1; a 1 %
    upfront fee on the debt at close."""
    d, tl = _run(_case([_t(gearing=0.6, commitment_fee=0.005, upfront_fee=0.01)]))
    D = 600_000.0
    P = D / (0.6 * 1.08 + 0.4)                        # the cash drawn
    assert d.tranches[0].principal_drawn == pytest.approx(P, rel=1e-12)
    assert d.draws[:2] == pytest.approx([0.6 * P, 0.4 * P], rel=1e-12)
    assert d.idc[0] == pytest.approx(0.6 * P * 0.08, rel=1e-12) and d.idc_total == pytest.approx(D - P)
    assert d.fees[0] == pytest.approx(0.01 * D) and d.fees[1] == pytest.approx(0.005 * 0.4 * P)
    assert d.tranches[0].balance[:2] == pytest.approx([0.6 * P, D], rel=1e-12)
    assert d.uses == pytest.approx({"capex": 1e6, "idc": D - P, "fees": 0.01 * D + 0.002 * P,
                                    "dsra": 0.0, "total": 1e6 + D - P + 0.01 * D + 0.002 * P})
    # First service at COD (index 2): the annuity on D.
    pay = D * 0.08 / (1 - 1.08 ** -10)
    assert d.service[2:12] == pytest.approx([pay] * 10, rel=1e-12)
    assert d.tranches[0].balance[11] == pytest.approx(0.0, abs=1e-6)
    assert "idc_axis_point_draws" in d.flags and "idc_at_first_rate" not in d.flags


def test_level_principal_and_an_annuity_with_a_rate_per_year():
    d, _ = _run(_case([_t(amount=500_000.0, sculpting="level", grace_years=2)]))
    assert d.principal[2:4] == pytest.approx([0.0, 0.0])                 # grace inside the tenor
    assert d.interest[2:4] == pytest.approx([40_000.0, 40_000.0])
    assert d.principal[4:12] == pytest.approx([62_500.0] * 8)
    rates = [0.05] * 5 + [0.07] * 5
    d, _ = _run(_case([_t(amount=500_000.0, rate=rates)]))
    bal, r = 500_000.0, np.array(rates)
    for m in range(10):
        pay = bal * r[m] / (1 - (1 + r[m]) ** -(10 - m))
        assert d.service[2 + m] == pytest.approx(pay, rel=1e-12)
        bal -= pay - bal * r[m]
    assert d.tranches[0].balance[11] == pytest.approx(0.0, abs=1e-6)


def test_two_tranches_senior_first_and_the_senior_dscr():
    senior = _t(amount=400_000.0, tenor_years=8)
    junior = _t(sculpting="dscr_target", dscr_target=2.0, rate=0.10, tenor_years=8)
    d, _ = _run(_case([senior, junior]))
    s = d.tranches[0].service
    left = d.cfads - s
    assert d.tranches[1].service[2:10] == pytest.approx(left[2:10] / 2.0, rel=1e-12)
    assert d.dscr_senior[2] == pytest.approx(d.cfads[2] / s[2])
    assert d.dscr[2] == pytest.approx(d.cfads[2] / (s[2] + d.tranches[1].service[2]))
    assert d.min_dscr(senior=True) > d.min_dscr()


def test_total_uses_fixed_point_converges_and_reports_the_residual_when_forced():
    t = _t(gearing=0.7, gearing_base="total_uses", upfront_fee=0.02, dsra_months=6,
           commitment_fee=0.004)
    d, _ = _run(_case([t]))
    assert d.established() and 1 <= d.iterations < MAX_ITER and d.residual <= 1e-6
    assert d.amount == pytest.approx(0.7 * d.uses["total"], rel=1e-5)
    assert d.uses["dsra"] > 0 and d.uses["fees"] > 0 and d.uses["idc"] > 0
    # Debt funds every use pro rata: the fee and DSRA points draw too.
    assert d.draws.sum() == pytest.approx(d.tranches[0].principal_drawn)
    assert d.iterations <= 8                                    # Aitken: a few steps, not ~15
    # A slow contraction (fee 0.9, g = 1) still has a fixed point (review B6) …
    slow = _t(gearing=1.0, gearing_base="total_uses", upfront_fee=0.9)
    d, _ = _run(_case([slow]))
    assert d.established() and d.amount == pytest.approx(d.uses["total"], rel=1e-6)
    # … a contraction ratio ≥ 1 has none: refused, never iterated to MAX_ITER.
    bad = _t(gearing=1.0, gearing_base="total_uses", upfront_fee=1.0)
    d, _ = _run(_case([bad]))
    assert not d.established() and d.iterations < MAX_ITER
    assert d.reasons[0].startswith("debt_fixed_point_diverges:contraction=")


def test_dsra_and_reserve_interest():
    d, _ = _run(_case([_t(amount=500_000.0, dsra_months=6)], reserves_rate=0.03))
    svc = d.service
    assert d.dsra_balance[1] == pytest.approx(0.5 * svc[2])             # funded at the COD point
    assert d.dsra_balance[11] == 0.0 and d.dsra_funding[11] == pytest.approx(-d.dsra_balance[10])
    assert d.reserve_interest[2] == pytest.approx(0.03 * d.dsra_balance[1])
    assert d.uses["dsra"] == pytest.approx(d.dsra_balance[1])


def test_a_negative_cfads_year_pays_nothing_under_sculpting():
    lines = (TemplateLine("rev", "ppa_settlement", 200_000.0, "ppa", tenor_years=3),
             TemplateLine("om", "fom", -50_000.0, "opex"))
    case = _case([_t(sculpting="dscr_target", dscr_target=1.3, tenor_years=6)], lines=lines)
    case = dataclasses.replace(case, inputs=case.inputs.model_copy(
        update={"escalation": {"ppa": 0.0, "opex": 0.0}}))
    d, tl = _run(case)
    assert d.established()
    assert d.tranches[0].service[5:8] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
    assert "sculpt_basis_negative_no_service:2036" in d.flags
    assert not any(f.startswith("sculpted_interest_capitalised") for f in d.flags)


def test_cod_in_the_close_year_draws_at_point_zero_without_idc():
    d, tl = _run(_case([_t(amount=500_000.0, dsra_months=3)], cod=date(2031, 6, 1),
                       phasing=(1.0,)))
    assert d.idc_total == 0.0 and d.draws[0] == pytest.approx(500_000.0)
    assert d.interest[0] == pytest.approx(40_000.0)                        # year-1 service at point 0
    assert "dsra_first_service_unreserved:0" in d.flags and "first_service_at_draw_point" in d.flags


@pytest.mark.parametrize("tranche,reason", [
    (dict(upfront_fee=None, amount=1.0), "input_missing:upfront_fee:0:term_loan"),
    (dict(dsra_months=None, amount=1.0), "input_missing:dsra_months:0:term_loan"),
    (dict(grace_years=None, amount=1.0), "input_missing:grace_years:0:term_loan"),
    (dict(commitment_fee=None, amount=1.0), "input_missing:commitment_fee:0:term_loan"),
    (dict(grace_years=1, sculpting="dscr_target", dscr_target=1.3), "grace_with_sculpting:0:term_loan"),
    (dict(grace_years=10, amount=1.0), "grace_not_inside_tenor:0:term_loan"),
    (dict(rate=[0.05, 0.06], amount=1.0), "rate_list_length:0:term_loan:2!=10"),
    (dict(tenor_years=30, amount=1.0), "debt_tenor_beyond_analysis:0:term_loan"),
    (dict(amount=5e6), "debt_exceeds_uses:5000000.00>"),
])
def test_not_established_paths(tranche, reason):
    d, _ = _run(_case([_t(**tranche)]))
    assert not d.established() and any(r.startswith(reason) for r in d.reasons), d.reasons


def test_missing_reserves_rate_and_missing_capex_or_cfads():
    d, _ = _run(_case([_t(amount=1.0, dsra_months=6)], reserves_rate=None))
    assert d.reasons == ["input_missing:reserves_rate"]
    case = _case([_t(sculpting="dscr_target", dscr_target=1.3)])
    case = dataclasses.replace(case, assets=(AssetFinance("pv", "Generator", None, 40.0),))
    d, _ = _run(case)
    assert "capex_not_established" in d.reasons
    case = _case([_t(sculpting="dscr_target", dscr_target=1.3)],
                 lines=(TemplateLine("rev", "ppa_settlement", None, "ppa"),))
    assert "cfads_not_established" in _run(case)[0].reasons


def test_no_debt_is_all_equity():
    d, _ = _run(_case([]))
    assert d.established() and d.amount == 0.0 and d.sources == {"debt": 0.0, "equity": 1e6}
    assert d.min_dscr() is None


def test_s2t_the_salvage_in_cfads_deviation_is_exactly_its_pv_over_the_dscr():
    """SAM sculpts on CFADS incl. salvage when the tenor reaches the last year
    (review B2a); ours excludes the terminal: SAM D − ours = salvage / 1.3 /
    1.07^25 exactly."""
    d, e, _ = _sam("s2t")
    assert d.established() and e["deviations"][0]["name"] == "salvage_in_cfads"
    salvage = 0.1 * 112_068_000.0
    assert e["scalars"]["debt_size"] - d.amount == pytest.approx(salvage / 1.3 / 1.07 ** 25,
                                                                  rel=1e-9)


def test_s3d_sams_gearing_base_holds_the_dsra_total_uses_reproduces_it():
    """SAM D = g·(TIC + DSRA(D)) (review B2b): `gearing_base="capex"` gives
    g·TIC (the recorded deviation); `"total_uses"` matches SAM's schedule."""
    d, e, _ = _sam("s3d")
    assert e["deviations"][0]["name"] == "dsra_in_gearing_base"
    assert d.amount == pytest.approx(0.6 * 112_068_000.0, rel=1e-12)
    case = S.to_finance_case("s3d")
    t = case.inputs.debt[0].model_copy(update={"gearing_base": "total_uses"})
    case = dataclasses.replace(case, inputs=case.inputs.model_copy(update={"debt": [t]}))
    tl = build_timeline(case)
    d = build_debt(case.inputs, build_operating(case, tl), tl)
    a = e["arrays"]
    assert d.amount == pytest.approx(e["scalars"]["debt_size"], rel=1e-7)
    _close(d.interest, a["cf_debt_payment_interest"], 1e-7)
    _close(d.dsra_balance, a["cf_reserve_debtservice"], 1e-7)


def test_cfads_ignores_the_terminal_value_and_an_unknown_cfads_only_flags_the_dscr():
    """Review B1: a book-value terminal (unknown until the tax basis) never
    blocks sculpting; an unknown operating line keeps an amount schedule and
    flags the DSCR."""
    case = _case([_t(sculpting="dscr_target", dscr_target=1.3)])
    case = dataclasses.replace(case, inputs=case.inputs.model_copy(
        update={"terminal_value": TerminalValueRule(method="book_value")}))
    d, _ = _run(case)
    assert d.established() and d.amount > 0
    case = _case([_t(amount=500_000.0)], lines=(TemplateLine("rev", "ppa_settlement", None, "ppa"),))
    d, _ = _run(case)
    assert d.established() and d.service.sum() > 0
    assert "dscr_not_established:operating_not_established" in d.flags and d.min_dscr() is None
