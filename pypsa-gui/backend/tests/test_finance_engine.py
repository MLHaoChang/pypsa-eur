"""
The assembled single-owner engine (IC P4 WP4.5): all-case SAM parity through
the output mapping, solve-for-PPA (S1b), the metrics (payback, cover ratios,
IRR edges), the WACC gate's states, the counterfactual / lifecycle split, and
the not-established paths.
"""
from __future__ import annotations

import dataclasses
from datetime import date

import numpy as np
import pytest

from models.finance import DebtTranche, FinanceInputs, SolvePpa, TerminalValueRule
from services.finance.case import (
    CONTRACT_CLASS, AssetFinance, FinanceCase, LpBasis, Template, TemplateLine,
)
from services.finance.engine import payback, payback_sustained, run_case, solve_ppa, wacc_gate
from services.finance.metrics import irr
from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year
from tests.fixtures.investment_case.sam import sam_case as S

# S2t's debt deviates by construction (SAM sculpts on salvage) — sized in
# test_finance_debt; S3d / S3f run at SAM's debt amount (their sizing
# deviations are sized there) so everything downstream is compared.
PARITY = ["s1", "s1b", "s2", "s2c", "s3", "s3d", "s3f"]


def _sam_run(name):
    case = S.to_finance_case(name)
    if name in ("s3d", "s3f"):
        t = case.inputs.debt[0].model_copy(
            update={"gearing": None, "amount": S.sam_expected(name)["scalars"]["debt_size"]})
        case = dataclasses.replace(case, inputs=case.inputs.model_copy(update={"debt": [t]}))
    return run_case(case, layers=S.sam_tax_layers(name))


def _close(a, b, rel):
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert np.max(np.abs(a - b)) <= rel * np.max(np.abs(b)), np.max(np.abs(a - b))


@pytest.mark.parametrize("name", PARITY)
def test_all_case_sam_parity_through_the_output_mapping(name):
    r = _sam_run(name)
    e = S.sam_expected(name)
    a, sc = e["arrays"], e["scalars"]
    assert all(v == "ok" for v in r.sections.values()), r.reasons
    _close(r.cash["equity_pre_tax"], a["cf_project_return_pretax"], 1e-12)
    _close(r.cash["equity_post_tax"], a["cf_project_return_aftertax"], 1e-12)
    _close(-r.tax.liability["federal"], a["cf_fedtax"], 1e-9)
    _close(-r.tax.liability["state"], a["cf_statax"], 1e-9)
    m = r.metrics
    assert m["equity_post_tax_irr"] == pytest.approx(sc["equity_irr_post_tax"], abs=1e-6)
    assert m["equity_post_tax_npv"] == pytest.approx(sc["equity_npv_post_tax"], rel=1e-9)
    assert m["lcoe_nominal_per_mwh"] == pytest.approx(sc["lcoe_nominal_per_mwh"], rel=1e-10)
    assert m["lcoe_real_per_mwh"] == pytest.approx(sc["lcoe_real_per_mwh"], rel=1e-10)
    if sc["min_dscr"] is None:
        assert m["min_dscr"] is None and m["debt_size"] == 0.0
    else:
        assert m["min_dscr"] == pytest.approx(sc["min_dscr"], rel=1e-12)
        assert m["debt_size"] == pytest.approx(sc["debt_size"], rel=1e-12)


def test_s1b_solve_for_ppa_finds_sams_price():
    r = run_case(S.to_finance_case("s1b", solve=True), layers=S.sam_tax_layers("s1b"))
    sc = S.sam_expected("s1b")["scalars"]
    assert r.metrics["solve_ppa_status"] == "ok"
    assert r.metrics["solved_ppa_price"] == pytest.approx(sc["ppa_price_per_mwh"], rel=1e-10)
    assert r.metrics["solved_equity_irr_at_target_year"] == pytest.approx(0.11, abs=1e-10)
    # The stored inputs are not mutated.
    assert r.case.templates[0].lines[0].price == S.sam_params("s1b").ppa_price_per_mwh


def test_s1_all_equity_project_return_is_the_equity_return_and_s2_llcr_is_the_dscr():
    r = _sam_run("s1")
    np.testing.assert_allclose(r.cash["project_post_tax"], r.cash["equity_post_tax"])
    # A sculpted tranche's LLCR at COD = its DSCR (D = PV(CFADS / DSCR)).
    r2 = _sam_run("s2")
    assert r2.metrics["llcr"] == pytest.approx(1.3, rel=1e-12)
    assert r2.metrics["plcr"] > r2.metrics["llcr"]


def test_payback_and_irr_edges():
    assert payback(np.array([-100.0, 30.0, 30.0, 60.0])) == pytest.approx(2 + 40 / 60)
    assert payback(np.array([-100.0, 10.0])) is None and payback(np.array([5.0, -1.0])) == 0.0
    # A zero start is not a payback (WP4.5 review B6); an unsustained one is flagged.
    assert payback(np.array([0.0, -1000.0, 600.0, 600.0])) == pytest.approx(2 + 400 / 600)
    assert payback_sustained(np.array([-100.0, 60.0, 60.0, -50.0, 40.0])) is False
    assert irr([-1.0, -1.0, -1.0]) == (None, ["irr_not_established:no_sign_change"])
    # Two roots (10 % and 20 %): the one closest to 0, flagged.
    v, f = irr([-1.0, 2.3, -1.32])
    assert v == pytest.approx(0.1) and f == ["irr_multiple_sign_changes"]


# ── a hand case ──────────────────────────────────────────────────────────────

LAYER = (TaxLayer(name="corp", rate=0.25,
                  depreciation=(DepreciationClass("pv:sl_10", 1.0, sl_half_year(10)),)),)


def _case(*, lines=None, cf=(), debt=(), solve=None, fin_over=None, lp=None, years=10):
    kw = dict(currency="USD", financial_close=date(2030, 1, 1), cod_by_asset={"pv": date(2031, 1, 1)},
              capex_phasing=[1.0], contingency_share=0.0, analysis_years=years,
              escalation={"opex": 0.0, "tariff": 0.0, "export": 0.0}, degradation_by_asset={"pv": 0.0},
              tax_losses="offset_other_income", financing_fee_tax="not_deducted",
              wacc_nominal=0.07, cost_of_equity=0.09, inflation=0.02, debt=list(debt),
              solve_ppa=solve)
    kw.update(fin_over or {})
    lines = lines or (TemplateLine("ppa", "ppa_settlement", 200.0, CONTRACT_CLASS, indexation=0.0,
                                   contract_id="c1", price=50.0),
                      TemplateLine("bill", "energy_import", -60.0, "tariff"))
    return FinanceCase(inputs=FinanceInputs(**kw), owner="o", base_year=2031, cod=date(2031, 1, 1),
                       templates=(Template(first_year=2031, lines=tuple(lines),
                                           energy_mwh={"pv": 4.0}),),
                       counterfactual=tuple(cf), lp_basis=lp,
                       assets=(AssetFinance("pv", "Generator", 1000.0, 40.0, carrier="solar"),))


def test_the_counterfactual_makes_returns_incremental_and_the_lifecycle_total():
    """Actual bill 60, counterfactual bill 100: the investment saves 40 a year
    on top of its PPA; the lifecycle NPV keeps the whole (bill included)."""
    cf = (Template(first_year=2031, lines=(TemplateLine("bill", "energy_import", -100.0, "tariff"),)),)
    r = run_case(_case(cf=cf), layers=LAYER)
    assert r.op_incremental["net"][1] == pytest.approx(200.0 - 60.0 + 100.0)
    assert r.cash["ebitda"][1] == pytest.approx(240.0)
    total = run_case(_case(), layers=LAYER)
    assert r.metrics["lifecycle_npv"] == pytest.approx(total.metrics["equity_post_tax_npv"], rel=1e-12)
    assert r.metrics["equity_post_tax_irr"] > total.metrics["equity_post_tax_irr"]


def test_book_value_terminal_and_a_replacement_vintage():
    fin = {"terminal_value": TerminalValueRule(method="book_value"), "analysis_years": 6}
    r = run_case(_case(fin_over=fin, years=6), layers=LAYER)
    left = 1000.0 * (1 - sum(sl_half_year(10)[:6]))
    assert r.cash["ebitda"][-1] - r.op_incremental["net"][-1] == pytest.approx(left)
    assert "terminal_book_value:corp" in r.flags and "remaining_basis_written_off:corp" in r.flags
    # The sale at book value and the write-off offset: the last year is taxed
    # on the operating cash less the regular depreciation (1000 × 10 %) only.
    assert r.tax.taxable["corp"][-1] == pytest.approx(r.op_incremental["net"][-1] - 100.0)
    rep = run_case(_case(fin_over={"replacement_capex": [(2034, "pv", 100.0)],
                                   "escalation": {"opex": 0.0, "tariff": 0.0, "capex": 0.0}}),
                   layers=LAYER)
    extra = rep.tax.depreciation["corp"] - run_case(_case(), layers=LAYER).tax.depreciation["corp"]
    assert extra[4] == pytest.approx(100.0 * 0.05) and extra[:4].sum() == 0.0


def test_solve_ppa_refusals():
    base = SolvePpa(contract_id="c1", target_irr=0.10, target_year=8)

    def status(**line):
        ln = dict(key="ppa", stream="ppa_settlement", amount=200.0, esc_class=CONTRACT_CLASS,
                  indexation=0.0, contract_id="c1", price=50.0)
        ln.update(line)
        return solve_ppa(_case(lines=(TemplateLine(**ln),), solve=base), layers=LAYER)["solve_ppa_status"]

    assert status(amount=-200.0) == "solve_ppa_not_owner_sold"
    assert status(stream="cfd_settlement") == "solve_ppa_not_linear"
    assert status(changes_dispatch=True) == "solve_ppa_needs_redispatch"
    assert status(price=None) == "solve_ppa_price_unknown"
    assert solve_ppa(_case(solve=base.model_copy(update={"contract_id": "zz"})),
                     layers=LAYER)["solve_ppa_status"] == "solve_ppa_contract_not_found"
    rich = (TemplateLine("ppa", "ppa_settlement", 200.0, CONTRACT_CLASS, indexation=0.0,
                         contract_id="c1", price=50.0),
            TemplateLine("exp", "energy_export", 2000.0, "export"))
    assert solve_ppa(_case(lines=rich, solve=base), layers=LAYER)["solve_ppa_status"] == \
        "solve_ppa_no_root:target_met_at_zero_price"
    ok = solve_ppa(_case(solve=base), layers=LAYER)
    assert ok["solve_ppa_status"] == "ok" and ok["solved_equity_irr_at_target_year"] == pytest.approx(0.10)


@pytest.mark.parametrize("lp,fin,state,legs", [
    (LpBasis(0.07), {}, True, {"discount_rate": "ok", "asset_rates": "ok", "inflation": "n/a"}),
    (LpBasis(0.06), {}, False, {"discount_rate": "differs"}),
    (LpBasis(0.07, asset_discount_rates={"pv": 0.05, "bess": None}), {}, False, {"asset_rates": "differs"}),
    (LpBasis(0.07, 0.03, True), {}, False, {"inflation": "differs"}),
    (LpBasis(0.07, 0.02, True), {}, True, {"inflation": "ok"}),
    (LpBasis(0.07, 0.02, True), {"inflation": None}, None, {"inflation": None}),
    (None, {}, None, {"discount_rate": None}),
    (LpBasis(0.07), {"wacc_nominal": None}, None, {"discount_rate": None}),
])
def test_the_wacc_gate_states(lp, fin, state, legs):
    g = wacc_gate(_case(lp=lp, fin_over=fin))
    assert g["wacc_vs_discount_rate_consistent"] is state
    assert {k: g["legs"][k] for k in legs} == legs


def test_not_established_paths_leave_the_headlines_none():
    r = run_case(_case(fin_over={"tax_losses": None}), layers=LAYER)
    assert r.sections["tax"] == "not_established" and "input_missing:tax_losses" in r.reasons["tax"]
    assert r.cash["equity_post_tax"] is None and r.metrics["equity_post_tax_irr"] is None
    assert r.metrics["equity_pre_tax_irr"] is not None                  # pre-tax still stands
    loan = DebtTranche(kind="term_loan", amount=500.0, rate=0.05, tenor_years=5, upfront_fee=0.02,
                       dsra_months=0, grace_years=0, commitment_fee=0.0)
    r = run_case(_case(debt=[loan], fin_over={"financing_fee_tax": None}), layers=LAYER)
    assert "input_missing:financing_fee_tax" in r.reasons["tax"]
    r = run_case(_case(debt=[loan], fin_over={"financing_fee_tax": "amortised"}), layers=LAYER)
    nd = run_case(_case(debt=[loan]), layers=LAYER)
    assert r.tax.taxable["corp"][1] == pytest.approx(nd.tax.taxable["corp"][1] - 10.0 / 5)
    r = run_case(_case(fin_over={"cost_of_equity": None}), layers=LAYER)
    assert r.metrics["equity_post_tax_npv"] is None and r.metrics["lcoe_nominal_per_mwh"] is None
    assert "equity_post_tax_npv_not_established:cost_of_equity_missing" in r.flags
    r = run_case(_case())
    assert r.reasons["tax"] == ["tax_pack_missing"]



# ── WP4.5 review round 1 ────────────────────────────────────────────────────

def test_s1l_the_remaining_basis_write_off_deviation_is_exactly_its_tax_shield():
    """SL-39 over 25 years leaves (1 − 24.5/39) of the basis; SAM drops it, we
    deduct it in the last year: post-tax cash differs only there, by the
    state and federal shield (state deductible from federal)."""
    r = run_case(S.to_finance_case("s1l"), layers=S.sam_tax_layers("s1l"))
    e = S.sam_expected("s1l")
    assert e["deviations"][0]["name"] == "remaining_basis_written_off"
    post, sam = r.cash["equity_post_tax"], np.array(e["arrays"]["cf_project_return_aftertax"])
    _close(post[:-1], sam[:-1], 1e-12)
    p = S.sam_params("s1l")
    w = (1 - 24.5 / 39) * p.installed_cost
    shield = w * (p.state_rate + p.federal_rate - p.federal_rate * p.state_rate)
    assert post[-1] - sam[-1] == pytest.approx(shield, rel=1e-9)
    assert "remaining_basis_written_off:federal" in r.flags


def test_b1_the_ebitda_multiple_uses_the_incremental_ebitda():
    cf = (Template(first_year=2031, lines=(TemplateLine("bill", "energy_import", -100.0, "tariff"),)),)
    tv = TerminalValueRule(method="multiple_of_ebitda", value=5.0)
    r = run_case(_case(cf=cf, fin_over={"terminal_value": tv}), layers=LAYER)
    net = r.op_incremental["net"][-1]
    assert net == pytest.approx(240.0) and r.cash["ebitda"][-1] == pytest.approx(net + 5.0 * net)


def test_b2_lcoe_does_not_depend_on_how_the_value_is_earned():
    """The same incremental cash through a PPA of 240, or a PPA of 200 plus 40
    of bill savings, is the same energy cost."""
    ppa240 = (TemplateLine("ppa", "ppa_settlement", 240.0, CONTRACT_CLASS, indexation=0.0),
              TemplateLine("om", "fom", -20.0, "opex"))
    a = run_case(_case(lines=ppa240), layers=LAYER)
    ppa200 = (TemplateLine("ppa", "ppa_settlement", 200.0, CONTRACT_CLASS, indexation=0.0),
              TemplateLine("om", "fom", -20.0, "opex"),
              TemplateLine("bill", "energy_import", -60.0, "tariff"))
    cf = (Template(first_year=2031, lines=(TemplateLine("bill", "energy_import", -100.0, "tariff"),)),)
    b = run_case(_case(lines=ppa200, cf=cf), layers=LAYER)
    assert b.metrics["equity_post_tax_irr"] == pytest.approx(a.metrics["equity_post_tax_irr"])
    assert b.metrics["lcoe_nominal_per_mwh"] == pytest.approx(a.metrics["lcoe_nominal_per_mwh"])
    assert b.metrics["lcoe_nominal_per_mwh"] > 0 and "lcoe_on_incremental_value" in b.flags


def test_b3_the_solve_survives_sculpted_debt_above_the_uses():
    """S2 (sculpted, DSCR 1.3) solving for 12 % in year 15 from SAM's input
    price: at twice the price the sculpted debt exceeds the uses; the bracket
    shrinks back instead of refusing."""
    case = S.to_finance_case("s2")
    case = dataclasses.replace(case, inputs=case.inputs.model_copy(
        update={"solve_ppa": SolvePpa(target_irr=0.12, target_year=15)}))
    r = run_case(case, layers=S.sam_tax_layers("s2"))
    assert r.metrics["solve_ppa_status"] == "ok", r.metrics
    assert r.metrics["solved_equity_irr_at_target_year"] == pytest.approx(0.12, abs=1e-9)


def test_b4_an_unresolved_grant_leaves_the_pre_tax_headlines_none():
    from models.finance import Incentive
    r = run_case(_case(fin_over={"incentives": [Incentive(kind="grant")]}), layers=LAYER)
    assert r.sections["incentives"] == "not_established"
    assert r.cash["equity_pre_tax"] is None and r.metrics["project_pre_tax_irr"] is None


def test_b5_a_replacement_depreciates_on_its_own_assets_classes():
    layer = (TaxLayer(name="corp", rate=0.25, depreciation=(
        DepreciationClass("pv:sl_20", 0.5, sl_half_year(20)),
        DepreciationClass("bess:sl_5", 0.5, sl_half_year(5)))),)
    fin = {"replacement_capex": [(2034, "pv", 100.0)],
           "escalation": {"opex": 0.0, "tariff": 0.0, "capex": 0.0}}
    base = run_case(_case(), layers=layer).tax.depreciation["corp"]
    rep = run_case(_case(fin_over=fin), layers=layer).tax.depreciation["corp"]
    extra = rep - base
    assert extra[4:10] == pytest.approx([100 * f for f in sl_half_year(20)[:6]])


def test_the_pack_path_and_the_plcr_closed_form():
    from services.finance.packs.base import load_pack
    us = load_pack("us_federal", as_of=date(2026, 1, 1))
    fin = {"state_rate": 0.0, "acquisition_date": date(2029, 6, 1),
           "depreciation_class_by_asset": {"pv": "macrs_5"}, "small_business_163j": True}
    r = run_case(_case(fin_over=fin), us)
    assert r.sections["tax"] == "ok" and r.tax.liability["federal"][1] < 0      # 100 % bonus
    loan = DebtTranche(kind="term_loan", amount=500.0, rate=0.05, tenor_years=5, upfront_fee=0.0,
                       dsra_months=0, grace_years=0, commitment_fee=0.0)
    r = run_case(_case(debt=[loan]), layers=LAYER)
    c = 140.0                                                   # CFADS 200 − 60, flat
    assert r.metrics["plcr"] == pytest.approx(c * (1 - 1.05 ** -10) / 0.05 / 500.0)
    assert r.metrics["llcr"] == pytest.approx(c * (1 - 1.05 ** -5) / 0.05 / 500.0)



def test_an_impossible_basis_is_a_reason_never_an_exception():
    """Reductions above a class's basis (WP4.4 review r3-2) → tax not
    established with the reason."""
    layer = (TaxLayer(name="corp", rate=0.25, itc_basis_reduction=True, itc_basis_reduction_share=1.0,
                      depreciation=(DepreciationClass("pv:sl_10", 1.0, sl_half_year(10)),)),)
    from models.finance import Incentive
    r = run_case(_case(fin_over={"incentives": [Incentive(kind="itc", rate=1.0)]}), layers=layer)
    assert r.sections["tax"] == "ok"                      # ITC = basis: reduced to 0, fine
    r = run_case(_case(fin_over={"incentives": [Incentive(kind="itc", rate=1.5)]}), layers=layer)
    assert r.sections["tax"] == "not_established"
    assert any(x.startswith("tax_basis_invalid:") for x in r.reasons["tax"])
