"""
Two legs inside one difference line (IPP renewables + BESS ticket 01; gatekeeper, 2026-10-20).

A financial PPA or a CfD settles (price − ref) × q as ONE ledger line. Its legs escalate apart:
the price leg `price × price_qty_mwh` at the contract's own indexation (CONTRACT_CLASS, else
`ppa`), the reference leg `amount − price × qty` at the market-price class `export` — as the
export revenue it offsets does. So "merchant on every MWh + a financial PPA" equals "PPA price on
the share + merchant on the rest" in EVERY year of the case, not only the modelled one.

`solve_ppa` scales the price leg only (amount + (s − 1) · price · qty), and reads "owner-sold"
from price · qty > 0, so a financial PPA whose year-1 difference is negative still solves.
"""
from __future__ import annotations

from datetime import date

import pytest

from models.finance import FinanceInputs, SolvePpa
from services.finance.case import CONTRACT_CLASS, AssetFinance, FinanceCase, Template, TemplateLine
from services.finance.engine import run_case, solve_ppa
from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year

LAYER = (TaxLayer(name="corp", rate=0.25,
                  depreciation=(DepreciationClass("pv:sl_10", 1.0, sl_half_year(10)),)),)
YEARS = 25
P, REF, SHARE = 55.0, 60.0, 0.7          # the PPA price, the year-1 average reference, the share
GEN, EXPORT = 1000.0, 1000.0             # MWh a year: the plant's output and what it exports
INDEX = 0.02                             # the PPA's own indexation; export escalation is 0


def _case(lines, solve=None):
    fin = FinanceInputs(currency="EUR", financial_close=date(2030, 1, 1),
                        cod_by_asset={"pv": date(2031, 1, 1)}, capex_phasing=[1.0],
                        contingency_share=0.0, analysis_years=YEARS,
                        escalation={"opex": 0.0, "tariff": 0.0, "export": 0.0, "ppa": 0.0},
                        degradation_by_asset={"pv": 0.0}, tax_losses="offset_other_income",
                        financing_fee_tax="not_deducted", wacc_nominal=0.07, cost_of_equity=0.09,
                        inflation=0.02, solve_ppa=solve)
    return FinanceCase(inputs=fin, owner="o", base_year=2031, cod=date(2031, 1, 1),
                       templates=(Template(first_year=2031, lines=tuple(lines),
                                           energy_mwh={"pv": GEN}),),
                       assets=(AssetFinance("pv", "Generator", 600000.0, 40.0, carrier="solar"),))


def _physical():
    """PPA price on the share; merchant on the rest."""
    q = SHARE * GEN
    return (TemplateLine("contract:c1:ppa_energy", "ppa_settlement", P * q, CONTRACT_CLASS,
                         indexation=INDEX, contract_id="c1", price=P),
            TemplateLine("export_price:export_price", "energy_export", REF * (EXPORT - q),
                         "export"))


def _financial():
    """Merchant on every MWh; the PPA settles (P − ref) on the share."""
    q = SHARE * GEN
    return (TemplateLine("contract:c1:ppa_energy", "ppa_settlement", (P - REF) * q,
                         CONTRACT_CLASS, indexation=INDEX, contract_id="c1", price=P,
                         price_qty_mwh=q),
            TemplateLine("export_price:export_price", "energy_export", REF * EXPORT, "export"))


def test_the_cash_identity_holds_in_every_year_of_the_case():
    phys = run_case(_case(_physical()), layers=LAYER)
    fin = run_case(_case(_financial()), layers=LAYER)
    assert phys.cash["ebitda"] == pytest.approx(fin.cash["ebitda"], abs=1e-6)
    assert phys.metrics["equity_post_tax_npv"] == pytest.approx(
        fin.metrics["equity_post_tax_npv"], abs=1e-6)


def test_a_cfd_line_escalates_its_reference_leg_as_the_market_year_10():
    """The correction (gatekeeper): before, the whole (strike − ref) × q escalated at the
    indexation, (70 − 60) · 700 · 1.02^9 = 8365.65; now the strike leg does and the reference
    leg follows `export` (0 %): 70 · 700 · 1.02^9 − 60 · 700 = 16559.54."""
    q = SHARE * GEN
    cfd = TemplateLine("contract:c:cfd_difference", "cfd_settlement", (70.0 - REF) * q,
                       CONTRACT_CLASS, indexation=INDEX, contract_id="c", price=70.0,
                       price_qty_mwh=q)
    r = run_case(_case((cfd,)), layers=LAYER)
    tl_cod = 1                                      # financial close 2030, COD 2031
    year10 = r.op.lines["contract:c:cfd_difference"][tl_cod + 9]
    assert year10 == pytest.approx(70.0 * q * 1.02 ** 9 - REF * q)
    assert round(70.0 * q * 1.02 ** 9 - REF * q, 2) == 16559.54
    assert round((70.0 - REF) * q * 1.02 ** 9, 2) == 8365.65            # the old value


def test_a_line_without_a_volume_keeps_its_one_escalation():
    q = SHARE * GEN
    ln = TemplateLine("contract:c:cfd_difference", "cfd_settlement", (70.0 - REF) * q,
                      CONTRACT_CLASS, indexation=INDEX, contract_id="c")
    r = run_case(_case((ln,)), layers=LAYER)
    assert r.op.lines["contract:c:cfd_difference"][10] == pytest.approx(
        (70.0 - REF) * q * 1.02 ** 9)


def test_solve_ppa_on_a_financial_ppa_finds_the_physical_price():
    target = SolvePpa(contract_id="c1", target_irr=0.08, target_year=YEARS)
    phys = solve_ppa(_case(_physical(), solve=target), layers=LAYER)
    fin = solve_ppa(_case(_financial(), solve=target), layers=LAYER)
    assert phys["solve_ppa_status"] == "ok" and fin["solve_ppa_status"] == "ok"
    assert fin["solved_ppa_price"] == pytest.approx(phys["solved_ppa_price"], rel=1e-6)


def test_a_financial_ppa_below_its_reference_is_still_owner_sold():
    """Year 1: P 55 < ref 60, so the difference line is negative; the owner still sells."""
    target = SolvePpa(contract_id="c1", target_irr=0.08, target_year=YEARS)
    assert _financial()[0].amount < 0
    out = solve_ppa(_case(_financial(), solve=target), layers=LAYER)
    assert out["solve_ppa_status"] == "ok"
