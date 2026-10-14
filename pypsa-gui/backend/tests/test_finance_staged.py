"""
P5b: staged builds in the finance engine — per-asset COD and staged capex
(E2E demo plan 2026-10-14 §7.1, with the gatekeeper's changes in §7.4).

A hand case: financial close 2030, the case COD 2031 (pv), a 10-year analysis,
and a battery staged to 2035 (its first active period). By hand:

- the stage-1 capex is pv's only: 1000 × 1.1 contingency at y0;
- the stage capex is bess's: 500 × 1.1 × 1.02^(2034 − 2031) in 2034
  (COD − one lead year), escalated by the `capex` class from the base year;
- bess's degradation, replacement schedule, lifetime check and remaining-life
  terminal value run from ITS COD (2035), not the case's;
- the stage is equity-funded: debt, CFADS and DSCR are those of a case with
  no stage capex, while project and equity cash carry the outflow;
- tax depreciates the stage from its COD on bess's own class.

And the refusals the engine owns: the earliest asset COD is the case COD; a
typed `cod_by_asset` agrees with the resolved per-asset COD; a stage COD on
the operating axis; its capex year not before financial close; no incentive
on a later-stage asset; no dated tax schedule on one.
"""
from __future__ import annotations

import dataclasses
from datetime import date

import numpy as np
import pytest

from models.finance import DebtTranche, FinanceInputs, Incentive, TerminalValueRule
from services.finance.case import (
    CONTRACT_CLASS, AssetFinance, AssetPart, FinanceCase, FinanceRefused, LpBasis, Template,
    TemplateLine,
)
from services.finance.cashflow import build_operating
from services.finance.engine import run_case
from services.finance.replacements import remaining_life_terms, schedule
from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year
from services.finance.timeline import build_timeline

COD1, COD2 = date(2031, 1, 1), date(2035, 1, 1)
STAGE = 500.0 * 1.1 * 1.02 ** (2034 - 2031)
LAYER = (TaxLayer(name="corp", rate=0.25,
                  depreciation=(DepreciationClass("pv:sl_10", 1.0, sl_half_year(10)),
                                DepreciationClass("bess:sl_10", 0.0, sl_half_year(10)))),)


def _case(*, fin_over=None, asset_cod=None, bess_life=40.0, bess_cost=500.0, parts=(),
          lp=None) -> FinanceCase:
    kw = dict(currency="EUR", financial_close=date(2030, 1, 1),
              cod_by_asset={"pv": COD1, "bess": COD2}, capex_phasing=[1.0],
              contingency_share=0.1, analysis_years=10,
              escalation={"opex": 0.0, "tariff": 0.0, "export": 0.0, "capex": 0.02},
              degradation_by_asset={"pv": 0.0, "bess": 0.1},
              tax_losses="offset_other_income", financing_fee_tax="not_deducted",
              wacc_nominal=0.07, cost_of_equity=0.09, inflation=0.02)
    kw.update(fin_over or {})
    ppa = TemplateLine("ppa", "ppa_settlement", 200.0, CONTRACT_CLASS, indexation=0.0,
                       contract_id="c1")
    shift = TemplateLine("bess_rev", "energy_export", 50.0, "export", degrades_with="bess")
    t1 = Template(first_year=2031, lines=(ppa,), energy_mwh={"pv": 4.0})
    t2 = Template(first_year=2035, lines=(ppa, shift), energy_mwh={"pv": 4.0, "bess": 10.0})
    return FinanceCase(
        inputs=FinanceInputs(**kw), owner="o", base_year=2031, cod=COD1, templates=(t1, t2),
        assets=(AssetFinance("pv", "Generator", 1000.0, 40.0, carrier="solar"),
                AssetFinance("bess", "StorageUnit", bess_cost, bess_life, carrier="battery",
                             parts=tuple(parts))),
        asset_cod={"pv": COD1, "bess": COD2} if asset_cod is None else asset_cod, lp_basis=lp)


def _at(tl, arr, year):
    return arr[tl.index(year)]


# ── capex ────────────────────────────────────────────────────────────────────


def test_stage_capex_is_its_own_array_escalated_and_one_lead_year_early():
    case = _case()
    tl = build_timeline(case)
    op = build_operating(case, tl)
    assert (tl.y0, tl.cod_year, tl.n) == (2030, 2031, 11)
    assert op.capex.tolist() == pytest.approx([1100.0] + [0.0] * 10)       # stage 1: pv only
    expected = np.zeros(11)
    expected[tl.index(2034)] = STAGE
    assert op.stage_capex == pytest.approx(expected)
    assert op.stage_items == (("bess", 2035, 2034, pytest.approx(STAGE)),)
    assert "staged_capex_equity_funded" in op.flags
    later = build_operating(c := _case(fin_over={"stage_capex_lead_years": 0}), build_timeline(c))
    assert _at(tl, later.stage_capex, 2035) == pytest.approx(500.0 * 1.1 * 1.02 ** 4)


def test_without_staged_assets_nothing_changes():
    one = dataclasses.replace(_case(), asset_cod={})
    one = dataclasses.replace(one, inputs=one.inputs.model_copy(update={"cod_by_asset": {"pv": COD1}}))
    op = build_operating(one, build_timeline(one))
    assert op.stage_capex.sum() == 0.0 and op.stage_items == ()
    assert "staged_capex_equity_funded" not in op.flags
    assert op.capex[0] == pytest.approx((1000.0 + 500.0) * 1.1)            # as before P5


def test_a_missing_capex_escalation_leaves_capex_not_established():
    esc = {"opex": 0.0, "tariff": 0.0, "export": 0.0}
    op = build_operating(c := _case(fin_over={"escalation": esc}), build_timeline(c))
    assert op.capex is None and "escalation_missing:capex" in op.reasons["capex"]


# ── per-asset clocks ─────────────────────────────────────────────────────────


def test_degradation_and_energy_run_from_the_assets_own_cod():
    case = _case()
    tl = build_timeline(case)
    op = build_operating(case, tl)
    assert _at(tl, op.lines["bess_rev"], 2034) == 0.0
    assert _at(tl, op.lines["bess_rev"], 2035) == pytest.approx(50.0)          # k = 1
    assert _at(tl, op.lines["bess_rev"], 2037) == pytest.approx(50.0 * 0.9 ** 2)
    assert _at(tl, op.energy_mwh["bess"], 2036) == pytest.approx(9.0)
    assert _at(tl, op.energy_mwh["pv"], 2036) == pytest.approx(4.0)


def test_replacements_run_from_the_assets_own_cod():
    # bess lives 6 operating years in the case (2035 … 2040): a 4-year part is
    # bought again once, in its last service year 2035 + 4 − 1.
    part = AssetPart("cells", 500.0, 4.0)
    case = _case(parts=(part,), fin_over={"replacement_rule": "part_lifetimes"})
    tl = build_timeline(case)
    got = [(x.year, x.asset, x.part) for x in schedule(case, tl)]
    assert got == [(2038, "bess", "cells")]


def test_the_lifetime_check_reads_the_years_the_asset_operates():
    build_timeline(_case(bess_life=6.0))                     # 6 ≥ 2035 … 2040: no replacement owed
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case(bess_life=5.0))
    assert e.value.code == "asset_lifetime_short"


def test_the_remaining_life_terminal_value_starts_at_the_stage():
    part = AssetPart("all", 500.0, 8.0)
    case = _case(parts=(part,), lp=LpBasis(discount_rate=0.05),
                 fin_over={"replacement_rule": "part_lifetimes",
                           "terminal_value": TerminalValueRule(method="remaining_life_annuity")})
    tl = build_timeline(case)
    terms, reasons = remaining_life_terms(case, tl)
    (bess,) = [t for t in terms if t.asset == "bess"]
    assert reasons == []
    assert bess.start_year == 2035 and bess.remaining_years == pytest.approx(2035 + 8 - 2041)
    assert bess.base == pytest.approx(500.0 * 1.02 ** (2034 - 2031))   # escalated, no contingency


# ── cash, debt, tax ──────────────────────────────────────────────────────────


def _loan():
    return DebtTranche(kind="term_loan", amount=600.0, rate=0.05, tenor_years=8, upfront_fee=0.0,
                       commitment_fee=0.0, dsra_months=0, grace_years=0)


def test_the_stage_is_equity_funded_and_leaves_debt_and_dscr_alone():
    staged = run_case(_case(fin_over={"debt": [_loan()]}), layers=LAYER)
    free = run_case(_case(fin_over={"debt": [_loan()]}, bess_cost=0.0), layers=LAYER)
    assert staged.debt.amount == free.debt.amount
    assert staged.debt.cfads == pytest.approx(free.debt.cfads)
    assert np.allclose(staged.debt.dscr, free.debt.dscr, equal_nan=True)
    y = staged.tl.index(2034)
    diff = free.cash["project_pre_tax"] - staged.cash["project_pre_tax"]
    assert diff[y] == pytest.approx(STAGE) and np.delete(diff, y) == pytest.approx(0.0)
    eq = free.cash["equity_pre_tax"] - staged.cash["equity_pre_tax"]
    assert eq[y] == pytest.approx(STAGE)


def test_tax_depreciates_the_stage_from_its_cod_on_its_own_class():
    staged = run_case(_case(), layers=LAYER)
    free = run_case(_case(bess_cost=0.0), layers=LAYER)
    extra = staged.tax.depreciation["corp"] - free.tax.depreciation["corp"]
    i = staged.tl.index(2035)
    assert extra[:i].sum() == pytest.approx(0.0)
    assert extra[i] == pytest.approx(STAGE * sl_half_year(10)[0])
    assert extra[i + 1] == pytest.approx(STAGE * sl_half_year(10)[1])


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("over, asset_cod, code", [
    ({}, {"pv": date(2032, 1, 1), "bess": COD2}, "cod_mismatch"),             # earliest ≠ case COD
    ({"cod_by_asset": {"pv": COD1, "bess": date(2036, 1, 1)}}, None, "cod_mismatch"),
    ({"cod_by_asset": {"pv": COD1, "bess": date(2045, 1, 1)}},
     {"pv": COD1, "bess": date(2045, 1, 1)}, "staged_cod_outside_axis:bess"),
    ({"stage_capex_lead_years": 6}, None, "staged_capex_before_close:bess"),
])
def test_the_staged_refusals(over, asset_cod, code):
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case(fin_over=over, asset_cod=asset_cod))
    assert e.value.code == code


def test_an_incentive_on_a_later_stage_is_refused():
    grant = Incentive(kind="grant", amount=100.0, grant_tax_treatment="taxable")
    with pytest.raises(FinanceRefused) as e:
        run_case(_case(fin_over={"incentives": [grant]}), layers=LAYER)
    assert e.value.code == "staged_incentive_not_supported:bess"


def test_a_dated_tax_schedule_on_a_later_stage_is_refused():
    bonus = (TaxLayer(name="corp", rate=0.25,
                      depreciation=(DepreciationClass("pv:sl_10", 1.0, sl_half_year(10)),
                                    DepreciationClass("bess:macrs_5", 0.0, sl_half_year(5),
                                                      bonus=0.4))),)
    with pytest.raises(FinanceRefused) as e:
        run_case(_case(), layers=bonus)
    assert e.value.code == "staged_tax_schedule_not_supported:bess"


# ── LCOS ─────────────────────────────────────────────────────────────────────


def test_a_staged_storage_lcos_reads_its_own_capex_and_clock():
    """bess's LCOS capex is its stage capex in 2034, not a share of the
    stage-1 capex; its discharge degrades from its own COD."""
    from services.finance.case import StorageYear

    case = _case()
    sy = StorageYear(discharge_mwh=10.0, charge_mwh=11.0, charge_import_cost=0.0,
                     charge_surplus_cost=0.0)
    t1, t2 = case.templates
    case = dataclasses.replace(case, templates=(t1, dataclasses.replace(t2, storage={"bess": sy})))
    r = run_case(case, layers=LAYER)
    rec = r.lcos["assets"]["bess"]
    i = r.tl.index(2034)
    assert rec["pv_capex"] == pytest.approx(STAGE / 1.07 ** i)
    assert rec["reasons"] == [] and rec["lcos_nominal_per_mwh"] is not None, rec
    # Discharge degrades from bess's COD: year 1 of bess is 2035 (factor 1).
    d = 1.0 / 1.07 ** r.tl.index(2035) + 0.9 / 1.07 ** r.tl.index(2036)
    assert rec["pv_discharge_mwh"] > 10.0 * d
