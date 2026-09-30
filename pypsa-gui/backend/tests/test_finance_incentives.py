"""
Incentives (IC P4 WP4.4): the S3 ITC against SAM, the §45Y PTC by hand
(published factor, rounding, projection), eligibility on both sides of each
dated cliff, the phase-out, FEOC's three states, the PWA multiplier, grants,
and the refusals.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from models.finance import EligibilityRule, FinanceInputs, Incentive
from services.finance.case import AssetFinance, FinanceCase, Template, TemplateLine
from services.finance.cashflow import build_operating
from services.finance.incentives import build_incentives, round_to
from services.finance.packs.base import load_pack
from services.finance.tax import compute_tax
from services.finance.timeline import build_timeline
from tests.fixtures.investment_case.sam import sam_case as S

US = load_pack("us_federal", as_of=date(2026, 1, 1))


def test_s3_itc_matches_sam_and_feeds_the_tax_basis_reduction():
    case = S.to_finance_case("s3")
    tl = build_timeline(case)
    op = build_operating(case, tl)
    inc = build_incentives(case, tl, op)
    e = S.sam_expected("s3")
    assert inc.established(), inc.reasons
    assert inc.itc_amount == pytest.approx(e["scalars"]["itc_total"], rel=1e-12)
    assert inc.itc == pytest.approx(e["arrays"]["cf_itc_total"], rel=1e-12)   # year 1, after tax
    assert inc.itc_assets == ("pv",)
    a = e["arrays"]
    tax = compute_tax(tl, S.sam_tax_layers("s3"), ebitda=op.ebitda,
                      basis=S.sam_params("s3").installed_cost,
                      interest=np.asarray(a["cf_debt_payment_interest"]),
                      other_income=np.asarray(a["cf_reserve_interest"]),
                      itc_amount=inc.itc_amount, losses="offset_other_income")
    # SAM's tax arrays are signed as cash (tax owed < 0).
    assert -tax.liability["federal"] == pytest.approx(a["cf_fedtax"], rel=1e-9, abs=1e-3)
    assert -tax.liability["state"] == pytest.approx(a["cf_statax"], rel=1e-9, abs=1e-3)


def _case(incentives, *, carrier="solar", cod=date(2026, 6, 1), close=date(2025, 3, 1),
          start=date(2025, 3, 1), pwa=True, currency="USD", inflation=0.02, years=12,
          assets=None, energy=None):
    fin = FinanceInputs(currency=currency, financial_close=close, cod_by_asset={},
                        capex_phasing=[1.0 / max(cod.year - close.year, 1)] * max(
                            cod.year - close.year, 1),
                        contingency_share=0.1, analysis_years=years,
                        escalation={"ppa": 0.0}, degradation_by_asset={"pv": 0.0, "bess": 0.0, "x": 0.0},
                        construction_start=start, pwa_met=pwa, inflation=inflation,
                        incentives=incentives)
    assets = assets or (AssetFinance("pv", "Generator", 1_000_000.0, 40.0, carrier=carrier),)
    energy = energy or {"pv": 1000.0}
    return FinanceCase(inputs=fin, owner="o", base_year=cod.year, cod=cod,
                       templates=(Template(first_year=cod.year, energy_mwh=energy, lines=(
                           TemplateLine("rev", "ppa_settlement", 1.0, "ppa"),)),),
                       assets=assets)


def _run(case, pack=US):
    tl = build_timeline(case)
    return build_incentives(case, tl, build_operating(case, tl), pack), tl


def test_ptc_by_hand_published_factor_rounding_and_projection():
    """§45Y: 1.5 ¢ × 2.0570 = 3.0855 → 3.1 ¢ (the published 2026 amount); 2027
    and later project the factor at 2 %: 2.09814 × 1.5 = 3.147 → 3.1 ¢;
    2.14010 × 1.5 = 3.210 → 3.2 ¢ … for 10 operating years, 1000 MWh each."""
    inc, tl = _run(_case([Incentive(kind="ptc")]))
    assert inc.established(), inc.reasons
    c = tl.index(2026)
    for k in range(12):
        y = 2026 + k
        f = 2.0570 * 1.02 ** (y - 2026)
        cents = math.floor(1.5 * f / 0.1 + 0.5) * 0.1
        want = cents * 10.0 * 1000.0 if k < 10 else 0.0
        assert inc.ptc[c + k] == pytest.approx(want), y
    assert inc.ptc[c] == pytest.approx(31_000.0)
    assert "ptc_factor_projected:2027" in inc.flags and "ptc_factor_projected:2026" not in inc.flags
    base, _ = _run(_case([Incentive(kind="ptc")], pwa=False))
    assert base.ptc[c] == pytest.approx(6_000.0)                          # 0.6 ¢ published
    assert round_to(0.625, 0.05) == pytest.approx(0.65) and round_to(0.62, 0.05) == pytest.approx(0.6)


@pytest.mark.parametrize("carrier,start,cod,eligible", [
    ("solar", date(2026, 7, 4), date(2028, 1, 1), True),     # began by 2026-07-04
    ("solar", date(2026, 7, 5), date(2028, 1, 1), False),    # later start, in service after 2027
    ("solar", date(2026, 7, 5), date(2027, 12, 31), True),   # in service by 2027-12-31
    ("onwind", date(2027, 1, 1), date(2028, 6, 1), False),
    ("battery", date(2027, 1, 1), date(2028, 6, 1), True),   # storage is not terminated
])
def test_the_wind_solar_termination_cliff(carrier, start, cod, eligible):
    asset = AssetFinance("pv", "Generator", 1_000_000.0, 40.0, carrier=carrier)
    inc, _ = _run(_case([Incentive(kind="itc", feoc_flag=False)], carrier=carrier, start=start,
                        cod=cod, close=start, assets=(asset,)))
    assert inc.established(), inc.reasons
    assert (inc.itc_amount > 0) is eligible
    if not eligible:
        assert any("wind_solar_termination" in f for f in inc.flags)


@pytest.mark.parametrize("start_year,share", [(2032, 1.0), (2033, 1.0), (2034, 0.75), (2035, 0.5),
                                              (2036, 0.0)])
def test_the_phase_out_after_2032(start_year, share):
    battery = AssetFinance("b", "StorageUnit", 1_000_000.0, 40.0, carrier="battery")
    start = date(start_year, 2, 1)
    inc, _ = _run(_case([Incentive(kind="itc", feoc_flag=False)], carrier="battery", start=start,
                        close=start, cod=date(start_year + 1, 1, 1), assets=(battery,),
                        energy={"b": 0.0}))
    assert inc.itc_amount == pytest.approx(0.30 * 1.1e6 * share)


@pytest.mark.parametrize("flag,outcome", [(None, "missing"), (True, "ineligible"), (False, "ok")])
def test_feoc_three_states(flag, outcome):
    """Construction beginning after 2025-12-31 needs the FEOC flag stated."""
    inc, _ = _run(_case([Incentive(kind="itc", feoc_flag=flag)], start=date(2026, 2, 1),
                        close=date(2026, 2, 1), cod=date(2027, 6, 1)))
    if outcome == "missing":
        assert not inc.established() and "feoc_flag_missing:0:itc" in inc.reasons
    elif outcome == "ineligible":
        assert inc.established() and inc.itc_amount == 0 and "incentive_ineligible:0:itc:feoc" in inc.flags
    else:
        assert inc.itc_amount == pytest.approx(0.30 * 1.1e6)
    # Before 2026 the flag is not required.
    inc, _ = _run(_case([Incentive(kind="itc")]))
    assert inc.established() and inc.itc_amount == pytest.approx(0.30 * 1.1e6)


@pytest.mark.parametrize("pwa,rate", [(True, 0.30), (False, 0.06), (None, None)])
def test_the_pwa_multiplier_is_never_assumed(pwa, rate):
    inc, _ = _run(_case([Incentive(kind="itc")], pwa=pwa))
    if rate is None:
        assert "input_missing:pwa_met:0:itc" in inc.reasons and inc.itc_amount == 0
    else:
        assert inc.itc_amount == pytest.approx(rate * 1.1e6)          # basis incl. contingency
        assert inc.lines[0].rate == rate


def test_a_stated_rate_cap_grant_and_user_rules():
    inc, tl = _run(_case([Incentive(kind="itc", rate=0.4, amount=100_000.0),
                          Incentive(kind="grant", amount=50_000.0,
                                    grant_tax_treatment="reduces_basis")]), pack=None)
    assert inc.itc_amount == 100_000.0 and "itc_capped:0:itc" in inc.flags
    assert inc.grant[tl.index(2025)] == 50_000.0 and inc.grant_basis_reduction == 50_000.0
    inc, _ = _run(_case([Incentive(kind="grant", rate=0.2, grant_tax_treatment="reduces_basis")]),
                  pack=None)
    assert inc.grant_basis_reduction == pytest.approx(0.2 * 1.1e6)
    # The incentive's own dated rules: a begin-construction date and a phase-out.
    rule = EligibilityRule(begin_construction_by=date(2025, 1, 1))
    inc, _ = _run(_case([Incentive(kind="itc", rate=0.3, eligibility=rule)]), pack=None)
    assert inc.itc_amount == 0 and "incentive_ineligible:0:itc:begin_construction_by" in inc.flags
    po = [(date(2024, 1, 1), 1.0), (date(2025, 1, 1), 0.5)]
    inc, _ = _run(_case([Incentive(kind="itc", rate=0.3, phase_out=po)]), pack=None)
    assert inc.itc_amount == pytest.approx(0.15 * 1.1e6)
    inc, _ = _run(_case([Incentive(kind="itc", rate=0.3, phase_out=po)], start=None), pack=None)
    assert "input_missing:construction_start:0:itc" in inc.reasons
    only_wind = EligibilityRule(asset_classes=["onwind"])
    inc, _ = _run(_case([Incentive(kind="itc", rate=0.3, eligibility=only_wind)]), pack=None)
    assert inc.itc_amount == 0 and "incentive_no_eligible_asset:0:itc" in inc.flags


@pytest.mark.parametrize("incentives,pack,currency,reason", [
    ([Incentive(kind="cfd")], US, "USD", "incentive_expressed_elsewhere:0:cfd:a contract (P2)"),
    ([Incentive(kind="accelerated_depreciation")], US, "USD",
     "incentive_expressed_elsewhere:0:accelerated_depreciation:depreciation_class_by_asset"),
    ([Incentive(kind="ptc"), Incentive(kind="itc")], US, "USD", "itc_and_ptc_same_asset:1:itc"),
    ([Incentive(kind="ptc")], None, "USD", "ptc_needs_a_pack_rule:0:ptc"),
    ([Incentive(kind="ptc")], US, "EUR", "ptc_currency_mismatch:EUR"),
    ([Incentive(kind="itc")], None, "USD", "incentive_rate_missing:0:itc"),
])
def test_refusals(incentives, pack, currency, reason):
    inc, _ = _run(_case(incentives, currency=currency), pack=pack)
    assert not inc.established() and reason in inc.reasons, inc.reasons


def test_no_incentives_and_every_us_rule_cites_its_source():
    inc, _ = _run(_case([]))
    assert inc.established() and inc.itc_amount == 0 and not inc.lines
    inc, _ = _run(_case([Incentive(kind="itc")]))
    assert {"clean_electricity_itc", "clean_electricity_phase_out", "wind_solar_termination",
            "feoc_material_assistance"} <= set(inc.sources)
    assert "48E" in inc.sources["clean_electricity_itc"]



# ── WP4.4 review round 1 ────────────────────────────────────────────────────

@pytest.mark.parametrize("carrier,kind,outcome", [
    ("diesel", "itc", "ineligible"), ("gas", "ptc", "ineligible"),
    ("fuel cell", "itc", "unclassified"), ("mystery", "itc", "unclassified"),
    ("battery", "ptc", "ineligible"), ("battery", "itc", "ok"), ("ror", "ptc", "ok"),
])
def test_b1_technology_eligibility_fails_closed(carrier, kind, outcome):
    asset = AssetFinance("x", "Generator", 1_000_000.0, 40.0, carrier=carrier)
    inc, _ = _run(_case([Incentive(kind=kind)], carrier=carrier, assets=(asset,),
                        energy={"x": 1000.0}))
    total = inc.itc_amount + inc.ptc.sum()
    if outcome == "unclassified":
        assert not inc.established() and f"incentive_technology_unclassified:x:{carrier}" in inc.reasons
    elif outcome == "ineligible":
        assert inc.established() and total == 0 and any("ineligible" in f for f in inc.flags)
    else:
        assert inc.established() and total > 0


@pytest.mark.parametrize("carrier", ["wind", "pv", "offwind", "solar rooftop", "solar-hsat"])
def test_b2_every_wind_solar_name_meets_the_termination(carrier):
    asset = AssetFinance("x", "Generator", 1_000_000.0, 40.0, carrier=carrier)
    inc, _ = _run(_case([Incentive(kind="itc", feoc_flag=False)], carrier=carrier, assets=(asset,),
                        start=date(2026, 7, 5), close=date(2026, 7, 5), cod=date(2028, 1, 1),
                        energy={"x": 0.0}))
    assert inc.itc_amount == 0 and any("wind_solar_termination" in f for f in inc.flags)


def test_b3_the_grant_states_its_treatment_and_a_basis_grant_reduces_the_itc_base():
    grant = Incentive(kind="grant", amount=100_000.0)
    inc, _ = _run(_case([Incentive(kind="itc"), grant]))
    assert "input_missing:grant_tax_treatment:1:grant" in inc.reasons
    inc, tl = _run(_case([Incentive(kind="itc"),
                          grant.model_copy(update={"grant_tax_treatment": "reduces_basis"})]))
    assert inc.itc_amount == pytest.approx(0.30 * (1.1e6 - 100_000.0))     # SAM deprbas = 1
    assert inc.grant_basis_reduction == 100_000.0 and inc.grant_taxable.sum() == 0
    inc, tl = _run(_case([Incentive(kind="itc"),
                          grant.model_copy(update={"grant_tax_treatment": "taxable"})]))
    assert inc.itc_amount == pytest.approx(0.30 * 1.1e6) and inc.grant_basis_reduction == 0
    assert inc.grant_taxable[tl.index(2025)] == 100_000.0


def test_b5_a_second_itc_on_the_same_asset_is_refused():
    inc, _ = _run(_case([Incentive(kind="itc"), Incentive(kind="itc", rate=0.1)]))
    assert "itc_twice_same_asset:1:itc" in inc.reasons


def test_feoc_true_before_the_rule_date_and_the_base_step_and_ptc_phase_out():
    inc, _ = _run(_case([Incentive(kind="itc", feoc_flag=True)]))       # construction 2025
    assert inc.itc_amount == pytest.approx(0.30 * 1.1e6)
    # Base amount projected in 2027: 0.3 × 2.0570 × 1.02 = 0.6294 → 0.65 ¢ (0.05 ¢ step).
    inc, tl = _run(_case([Incentive(kind="ptc")], pwa=False))
    assert inc.ptc[tl.index(2027)] == pytest.approx(6.5 * 1000.0)
    # The phase-out applies to the PTC too (construction 2034: 75 %).
    battery_free = AssetFinance("pv", "Generator", 1e6, 40.0, carrier="ror")
    inc, tl = _run(_case([Incentive(kind="ptc", feoc_flag=False)], carrier="ror",
                         assets=(battery_free,), start=date(2034, 2, 1), close=date(2034, 2, 1),
                         cod=date(2035, 1, 1)))
    f = 2.0570 * 1.02 ** (2035 - 2026)
    cents = math.floor(1.5 * f / 0.1 + 0.5) * 0.1
    assert inc.ptc[tl.index(2035)] == pytest.approx(cents * 10 * 1000 * 0.75)


def test_review_round_2_guards():
    """A basis grant above its assets' cost, a second PTC on one asset;
    carriers case-insensitive; a pypsa-eur battery's charger is storage."""
    inc, _ = _run(_case([Incentive(kind="itc"), Incentive(kind="grant", amount=2e6,
                                                          grant_tax_treatment="reduces_basis")]))
    assert "grant_exceeds_cost:1:grant" in inc.reasons and inc.itc_amount == 0
    inc, _ = _run(_case([Incentive(kind="ptc"), Incentive(kind="ptc")]))
    assert "ptc_twice_same_asset:1:ptc" in inc.reasons
    solar = AssetFinance("x", "Generator", 1e6, 40.0, carrier="Solar")
    inc, _ = _run(_case([Incentive(kind="itc")], assets=(solar,), energy={"x": 0.0}))
    assert inc.itc_amount == pytest.approx(0.30 * 1.1e6)
    charger = AssetFinance("x", "Link", 1e6, 40.0, carrier="battery charger")
    inc, _ = _run(_case([Incentive(kind="itc")], assets=(charger,), energy={"x": 0.0}))
    assert inc.itc_amount == pytest.approx(0.30 * 1.1e6)
