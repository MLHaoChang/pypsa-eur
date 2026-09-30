"""
The `eu_nl` and `ca_federal` packs resolved into tax layers (IC P4 WP4.3b):
hand cases per pack — tax per layer and depreciation per class — plus the
Canadian Clean Technology ITC and its 100 % capital-cost reduction.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from models.finance import FinanceInputs, Incentive
from services.finance.case import AssetFinance, FinanceCase, Template, TemplateLine
from services.finance.cashflow import build_operating
from services.finance.incentives import build_incentives
from services.finance.packs.base import load_pack
from services.finance.tax import compute_tax
from services.finance.tax_layers import OwnerAsset, resolve_tax_layers
from services.finance.timeline import Timeline, build_timeline

NL = load_pack("eu_nl", as_of=date(2026, 1, 1))
CA = load_pack("ca_federal", as_of=date(2026, 1, 1))


def _tl(n, cod=2031):
    return Timeline(y0=cod, cod_year=cod, base_year=cod, analysis_years=n)


def _nl(**kw):
    base = dict(financial_close=date(2031, 1, 1), tax_losses="carryforward",
                depreciation_class_by_asset={"pv": "slm_10"})
    base.update(kw)
    return FinanceInputs(**base)


def test_nl_brackets_losses_and_straight_line_by_hand():
    """1 M€ on 10-year straight line (COD January: 100 k€ a year). EBITDA
    [−1.9, 3.1, 0.6] m: base [−2.0, 3.0, 0.5] m. Year 2 offsets €1m + 50 % of
    the €2m excess = 2.0 m → taxable 1.0 m; year 3 taxable 0.5 m. VPB: 19 % to
    €200k, 25.8 % above."""
    res = resolve_tax_layers(NL, _nl(), [OwnerAsset("pv", "solar", 1_000_000.0)], date(2031, 1, 1))
    assert res.missing == [] and [ly.name for ly in res.layers] == ["vpb"]
    t = compute_tax(_tl(3), res.layers, ebitda=np.array([-1.9e6, 3.1e6, 0.6e6]), basis=1e6,
                    losses="carryforward")
    assert t.depreciation["vpb"] == pytest.approx([1e5, 1e5, 1e5])
    vpb = lambda x: 0.19 * min(x, 2e5) + 0.258 * max(0.0, x - 2e5)     # noqa: E731
    assert t.liability["vpb"] == pytest.approx([0.0, vpb(1.0e6), vpb(0.5e6)])
    assert t.loss_pool["vpb"] == pytest.approx([2e6, 0.0, 0.0])
    assert {"nl_residual_value_not_modelled", "nl_loss_carryback_not_modelled"} <= set(res.flags)


def test_nl_earnings_stripping_keeps_the_1m_threshold_and_carries_the_excess():
    """EBITDA 3 m, interest 1.5 m: cap = max(24.5 % × 3 m, €1m) = 1 m; 0.5 m
    carries into year 2 (EBITDA 10 m: cap 2.45 m ≥ 0.5 + 0.2)."""
    res = resolve_tax_layers(NL, _nl(), [OwnerAsset("pv", "solar", 0.0)], date(2031, 1, 1))
    t = compute_tax(_tl(2), res.layers, ebitda=np.array([3e6, 10e6]), basis=0.0,
                    interest=np.array([1.5e6, 0.2e6]), losses="carryforward")
    assert t.taxable["vpb"] == pytest.approx([3e6 - 1e6, 10e6 - 0.7e6])


def test_nl_the_20pct_a_year_cap_refuses_a_shorter_life():
    res = resolve_tax_layers(NL, _nl(depreciation_class_by_asset={"pv": "slm_4"}),
                             [OwnerAsset("pv", "solar", 1.0)], date(2031, 1, 1))
    assert res.layers == () and any("20% a year cap" in m for m in res.missing)
    res = resolve_tax_layers(NL, _nl(depreciation_class_by_asset={"pv": "slm_5"}),
                             [OwnerAsset("pv", "solar", 1.0)], date(2031, 7, 1))
    assert res.layers[0].depreciation[0].schedule[0] == pytest.approx(0.2 * 6 / 12)


def _ca(**kw):
    base = dict(financial_close=date(2026, 1, 1), tax_losses="offset_other_income",
                state_rate=0.115, acquisition_date=date(2025, 6, 1),
                depreciation_class_by_asset={"pv": "cca_43.1"})
    base.update(kw)
    return FinanceInputs(**base)


@pytest.mark.parametrize("cod,first", [(date(2026, 6, 1), 0.55), (date(2025, 3, 1), 0.75),
                                       (date(2023, 3, 1), 1.0), (date(2028, 1, 1), 0.15)])
def test_ca_cca_43_1_enhanced_first_year_then_declining_balance(cod, first):
    """Class 43.1 at 30 %: the enhanced first year by the available-for-use
    year (100 / 75 / 55 %), else the half-year rule (½ × 30 %); then 30 % of
    the undepreciated capital cost."""
    acq = date(2022, 6, 1) if cod.year < 2025 else date(2025, 6, 1)
    res = resolve_tax_layers(CA, _ca(acquisition_date=acq), [OwnerAsset("pv", "solar", 1.0)], cod)
    s = res.layers[0].depreciation[0].schedule
    ucc = 1.0 - first
    assert s[0] == pytest.approx(first)
    for k in range(1, 5):
        assert s[k] == pytest.approx(ucc * 0.30)
        ucc -= s[k]


def test_ca_layers_class_43_2_window_and_eifel_flag():
    res = resolve_tax_layers(CA, _ca(), [OwnerAsset("pv", "solar", 1.0)], date(2026, 6, 1))
    fed, prov = res.layers
    assert (fed.rate, prov.rate) == (0.15, 0.115)
    assert not fed.deductible_in_later_layers and fed.itc_basis_reduction_share == 1.0
    assert fed.itc_basis_reduction_lag == 1
    t = compute_tax(_tl(1, 2026), res.layers, ebitda=np.array([1e6]), basis=0.0,
                    losses="offset_other_income")
    assert t.total_liability[0] == pytest.approx(1e6 * (0.15 + 0.115))
    res = resolve_tax_layers(CA, _ca(depreciation_class_by_asset={"pv": "cca_43.2"}),
                             [OwnerAsset("pv", "solar", 1.0)], date(2026, 6, 1))
    assert res.layers == () and any("before 2025-01-01" in m for m in res.missing)
    res = resolve_tax_layers(CA, _ca(tax_losses="carryforward", state_rate=0.0),
                             [OwnerAsset("pv", "solar", 1.0)], date(2026, 6, 1))
    assert [ly.name for ly in res.layers] == ["federal"] and "ca_eifel_not_modelled" in res.flags


def _ca_case(cod, pwa=True, cls="cca_43.1"):
    fin = FinanceInputs(currency="CAD", financial_close=date(cod.year, 1, 1), capex_phasing=[1.0],
                        contingency_share=0.0, analysis_years=5, escalation={"ppa": 0.0},
                        degradation_by_asset={"pv": 0.0}, pwa_met=pwa,
                        acquisition_date=date(cod.year, 1, 1),
                        depreciation_class_by_asset={"pv": cls}, incentives=[Incentive(kind="itc")])
    return FinanceCase(inputs=fin, owner="o", base_year=cod.year, cod=cod,
                       templates=(Template(first_year=cod.year, energy_mwh={"pv": 1.0}, lines=(
                           TemplateLine("rev", "ppa_settlement", 1.0, "ppa"),)),),
                       assets=(AssetFinance("pv", "Generator", 1_000_000.0, 40.0, carrier="solar"),))


@pytest.mark.parametrize("cod,pwa,cls,rate", [
    (date(2030, 1, 1), True, "cca_43.1", 0.30), (date(2030, 1, 1), False, "cca_43.1", 0.20),
    (date(2034, 6, 1), True, "cca_43.1", 0.15), (date(2035, 6, 1), True, "cca_43.1", 0.0),
    (date(2030, 1, 1), True, "slm_10", 0.0),
])
def test_ca_clean_technology_itc(cod, pwa, cls, rate):
    case = _ca_case(cod, pwa, cls)
    tl = build_timeline(case)
    inc = build_incentives(case, tl, build_operating(case, tl), CA)
    assert inc.established(), inc.reasons
    assert inc.itc_amount == pytest.approx(rate * 1e6)
    if cls != "cca_43.1":
        assert any("not_clean_technology" in f for f in inc.flags)



# ── WP4.3b review round 1 ────────────────────────────────────────────────────

CA2 = load_pack("ca_federal", as_of=date(2026, 6, 1))


@pytest.mark.parametrize("acq,cod,first", [
    (date(2025, 6, 1), date(2026, 6, 1), 1.0), (date(2025, 6, 1), date(2028, 6, 1), 1.0),
    (date(2026, 3, 1), date(2030, 6, 1), 0.75), (date(2026, 3, 1), date(2033, 6, 1), 0.55),
    (date(2026, 3, 1), date(2034, 6, 1), 0.15),                    # after 2033: half-year rule
    (date(2024, 6, 1), date(2026, 6, 1), 0.55),                    # the old incentive, acquired < 2025
])
def test_b1_the_reaccelerated_first_year_from_2026_03_26(acq, cod, first):
    """S.C. 2026, c. 3: Class 43.1 acquired after 2024 — 100 % before 2030,
    75 % 2030–31, 55 % 2032–33 (Reg. 1100(2) A.1(b), 1104(4.01))."""
    assert CA2.valid_from == date(2026, 3, 26)
    res = resolve_tax_layers(CA2, _ca(acquisition_date=acq), [OwnerAsset("pv", "solar", 1.0)], cod)
    assert res.layers[0].depreciation[0].schedule[0] == pytest.approx(first)
    # The 2026-01-01 version keeps the law then enacted.
    old = resolve_tax_layers(CA, _ca(acquisition_date=date(2025, 6, 1)),
                             [OwnerAsset("pv", "solar", 1.0)], date(2026, 6, 1))
    assert old.layers[0].depreciation[0].schedule[0] == pytest.approx(0.55)


def test_b1_class_43_2_keeps_the_old_incentive_inside_its_window():
    res = resolve_tax_layers(CA2, _ca(acquisition_date=date(2024, 6, 1),
                                      depreciation_class_by_asset={"pv": "cca_43.2"}),
                             [OwnerAsset("pv", "solar", 1.0)], date(2025, 3, 1))
    assert res.layers[0].depreciation[0].schedule[:2] == pytest.approx([0.75, 0.25 * 0.5])


def test_b2_the_itc_reduces_the_ucc_the_following_year_with_recapture():
    """100 % first-year CCA on the unreduced 1.0; the next year the UCC (0)
    falls by the 0.3 ITC → 0.3 recaptured (negative depreciation)."""
    from services.finance.tax import DepreciationClass, TaxLayer, cca_declining
    layer = (TaxLayer(name="federal", rate=0.15, itc_basis_reduction=True,
                      itc_basis_reduction_share=1.0, itc_basis_reduction_lag=1,
                      depreciation=(DepreciationClass("pv:cca_43.1", 1.0, cca_declining(0.3, 1.0)),)),)
    t = compute_tax(_tl(3), layer, ebitda=np.zeros(3), basis=1.0, itc_amount=0.3,
                    losses="offset_other_income")
    assert t.depreciation["federal"] == pytest.approx([1.0, -0.3, 0.0])
    layer = (layer[0].__class__(**{**layer[0].__dict__, "depreciation": (
        DepreciationClass("pv:cca_43.1", 1.0, cca_declining(0.3)),)}),)
    t = compute_tax(_tl(3), layer, ebitda=np.zeros(3), basis=1.0, itc_amount=0.3,
                    losses="offset_other_income")
    assert t.depreciation["federal"] == pytest.approx([0.15, 0.55 * 0.3, 0.55 * 0.7 * 0.3])


def _itc_case(acq, carrier="solar", cls="cca_43.1", cod=date(2030, 1, 1)):
    case = _ca_case(cod, True, cls)
    fin = case.inputs.model_copy(update={"acquisition_date": acq})
    import dataclasses
    return dataclasses.replace(case, inputs=fin, assets=(
        AssetFinance("pv", "Generator", 1_000_000.0, 40.0, carrier=carrier),))


@pytest.mark.parametrize("acq,carrier,outcome", [
    (date(2022, 6, 1), "solar", "ineligible"),         # acquired before 2023-03-28 (B3)
    (None, "solar", "missing"),
    (date(2029, 1, 1), "gas_chp", "ineligible"),       # not clean technology (B4)
    (date(2029, 1, 1), "mystery", "unclassified"),
    (date(2029, 1, 1), "solar", "ok"),
])
def test_b3_b4_the_clean_technology_itc_needs_the_acquisition_date_and_the_property(acq, carrier, outcome):
    case = _itc_case(acq, carrier)
    tl = build_timeline(case)
    inc = build_incentives(case, tl, build_operating(case, tl), CA2)
    if outcome == "ok":
        assert inc.itc_amount == pytest.approx(0.3e6)
    elif outcome == "ineligible":
        assert inc.established() and inc.itc_amount == 0
    elif outcome == "missing":
        assert "input_missing:acquisition_date:0:itc" in inc.reasons
    else:
        assert "incentive_technology_unclassified:pv:mystery" in inc.reasons


def test_nl_flags_and_citations():
    res = resolve_tax_layers(NL, _nl(tax_losses="offset_other_income"),
                             [OwnerAsset("pv", "solar", 1.0)], date(2031, 1, 1))
    assert "nl_brackets_standalone_in_offset_mode" in res.flags
    assert "3.30 lid 2" in NL.rule("depreciation_max_rate").source
    res = resolve_tax_layers(CA2, _ca(tax_losses="carryforward"), [OwnerAsset("pv", "solar", 1.0)],
                             date(2026, 6, 1))
    assert "ca_loss_carryback_not_modelled" in res.flags
