"""
The jurisdiction packs resolved into tax layers (IC P4 plan WP4.3a): the hand
oracles F2 (Germany) and F3 (US federal), the missing-input paths (plan C12)
and the cited sources (plan C11).

F2 — a German single-owner case, COD 2031-01-01, 6 years, Hebesatz 400 %
(GewSt 14 %), a 2 M€ PV asset on 20-year AfA (100 k€/yr, full first year),
interest 600 k€/yr (below the €3m Zinsschranke Freigrenze). By hand:

  GewSt base = EBITDA − 100k − 600k + 25 % × (600k − 200k) = EBITDA − 600k
             = [−3.6, −1.6, 1.9, 3.4, 3.4, 3.4] m; own pool €1m + 60 %:
    y3 cap 1 + 0.6·0.9 = 1.54 → taxable 0.36, pool 3.66
    y4 cap 1 + 0.6·2.4 = 2.44 → taxable 0.96, pool 1.22
    y5 offset 1.22 → taxable 2.18; y6 taxable 3.4
    liability 14 % = [0, 0, 50.4k, 134.4k, 305.2k, 476k]
  KSt base = EBITDA − 100k − 600k (GewSt not deductible)
           = [−3.7, −1.7, 1.8, 3.3, 3.3, 3.3] m; €1m + 60 % (from 2028):
    y3 cap 1 + 0.6·0.8 = 1.48 → taxable 0.32, pool 3.92
    y4 cap 1 + 0.6·2.3 = 2.38 → taxable 0.92, pool 1.54
    y5 offset 1.54 → taxable 1.76; y6 taxable 3.3
    rate 2031 11 %, 2032 on 10 %; × 1.055 SolZ
    liability = [0, 0, 0.10·1.055·0.32m, 0.10·1.055·0.92m, 0.10·1.055·1.76m, 0.10·1.055·3.3m]

F3 — US federal, COD 2031-01-01, acquired 2030-06-01 (after 2025-01-19: 100 %
bonus), a 1 M$ asset on MACRS-7, no state layer (rate 0), §163(j) applies
(not a small business), carryforward. By hand:

  interest cap 30 % of EBITDA with the disallowed part carried forward:
    EBITDA [500, 600, 700, 800, 900] k, interest [300, 300, 100, 100, 100] k
    allowed [150, 180, 210, 240, 120] k (carry 150, 270, 160, 20, 0)
  base = EBITDA − depreciation (1 M in year 1) − allowed interest
       = [−650, 420, 490, 560, 780] k; NOL 80 %:
    y2 cap 336 → taxable 84, pool 314; y3 offset 314 → taxable 176
  liability 21 % = [0, 17.64, 36.96, 117.6, 163.8] k
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from models.finance import FinanceInputs
from services.finance.packs.base import load_pack
from services.finance.tax import compute_tax
from services.finance.tax_layers import OwnerAsset, resolve_tax_layers
from services.finance.timeline import Timeline

COD = date(2031, 1, 1)


def _tl(n):
    return Timeline(y0=2031, cod_year=2031, base_year=2031, analysis_years=n)


def test_f2_germany_by_hand():
    pack = load_pack("eu_de", as_of=date(2030, 6, 1).replace(year=2026))
    fin = FinanceInputs(financial_close=COD, tax_losses="carryforward", hebesatz_pct=400.0,
                        acquisition_date=date(2024, 6, 1))
    res = resolve_tax_layers(pack, fin, [OwnerAsset("pv", "solar", 2_000_000.0)], COD)
    assert res.missing == [] and [l.name for l in res.layers] == ["gewst", "kst"]
    t = compute_tax(_tl(6), res.layers, ebitda=np.array([-3e6, -1e6, 2.5e6, 4e6, 4e6, 4e6]),
                    basis=2_000_000.0, interest=np.full(6, 600_000.0), losses="carryforward")
    assert list(t.depreciation["kst"]) == pytest.approx([100_000.0] * 6)
    assert list(t.liability["gewst"]) == pytest.approx([0, 0, 50_400, 134_400, 305_200, 476_000])
    k = 0.10 * 1.055
    assert list(t.liability["kst"]) == pytest.approx([0, 0, k * 320_000, k * 920_000,
                                                      k * 1_760_000, k * 3_300_000])
    assert t.flags == []                         # below the Zinsschranke Freigrenze


def test_f3_us_federal_by_hand():
    pack = load_pack("us_federal", as_of=date(2026, 1, 1))
    fin = FinanceInputs(financial_close=COD, tax_losses="carryforward", state_rate=0.0,
                        small_business_163j=False, acquisition_date=date(2030, 6, 1),
                        depreciation_class_by_asset={"bess": "macrs_7"})
    res = resolve_tax_layers(pack, fin, [OwnerAsset("bess", "battery", 1_000_000.0)], COD)
    assert res.missing == [] and [l.name for l in res.layers] == ["federal"]
    t = compute_tax(_tl(5), res.layers, ebitda=np.array([500e3, 600e3, 700e3, 800e3, 900e3]),
                    basis=1_000_000.0, interest=np.array([300e3, 300e3, 100e3, 100e3, 100e3]),
                    losses="carryforward")
    assert list(t.depreciation["federal"]) == pytest.approx([1e6, 0, 0, 0, 0])
    assert list(t.taxable["federal"]) == pytest.approx([-650e3, 420e3, 490e3, 560e3, 780e3])
    assert list(t.liability["federal"]) == pytest.approx([0, 17_640, 36_960, 117_600, 163_800])
    assert t.flags == ["interest_capped:federal"]


def test_bonus_follows_the_acquisition_date():
    pack = load_pack("us_federal", as_of=date(2026, 1, 1))
    base = dict(financial_close=COD, tax_losses="offset_other_income", state_rate=0.0,
                depreciation_class_by_asset={"bess": "macrs_5"})
    early = FinanceInputs(**base, acquisition_date=date(2024, 12, 1))
    res = resolve_tax_layers(pack, early, [OwnerAsset("bess", "battery", 1.0)], date(2026, 6, 1))
    assert res.layers[0].depreciation[0].bonus == pytest.approx(0.2)   # TCJA phase-down, 2026
    late = FinanceInputs(**base, acquisition_date=date(2025, 2, 1))
    assert resolve_tax_layers(pack, late, [OwnerAsset("bess", "battery", 1.0)],
                              COD).layers[0].depreciation[0].bonus == 1.0


def test_the_german_degressive_window_and_the_state_layer():
    de = load_pack("eu_de", as_of=date(2026, 1, 1))
    fin = FinanceInputs(financial_close=COD, tax_losses="offset_other_income",
                        hebesatz_pct=400.0, acquisition_date=date(2026, 3, 1))
    res = resolve_tax_layers(de, fin, [OwnerAsset("pv", "solar", 1.0)], COD)
    assert res.layers[0].depreciation[0].name == "pv:db_0.15_20"      # min(3/20, 30 %)
    us = load_pack("us_federal", as_of=date(2026, 1, 1))
    fin2 = FinanceInputs(financial_close=COD, tax_losses="carryforward", state_rate=0.07,
                         small_business_163j=True, acquisition_date=date(2030, 1, 1),
                         depreciation_class_by_asset={"pv": "macrs_5"})
    res2 = resolve_tax_layers(us, fin2, [OwnerAsset("pv", "solar", 1.0)], COD)
    state, fed = res2.layers
    assert state.name == "state" and state.depreciation[0].bonus == 0.0 and fed.depreciation[0].bonus == 1.0
    assert state.interest_cap is None and fed.interest_cap is None      # small business exempt
    assert {"state_bonus_decoupled", "state_loss_rule_not_modelled"} <= set(res2.flags)


def test_missing_inputs_are_named_never_assumed():
    us = load_pack("us_federal", as_of=date(2026, 1, 1))
    res = resolve_tax_layers(us, FinanceInputs(financial_close=COD, tax_losses="carryforward"),
                             [OwnerAsset("pv", "solar", 1.0)], COD)
    assert {"state_rate", "acquisition_date", "small_business_163j",
            "depreciation_class:pv"} <= set(res.missing)
    de = load_pack("eu_de", as_of=date(2026, 1, 1))
    res2 = resolve_tax_layers(de, FinanceInputs(financial_close=COD),
                              [OwnerAsset("bess", "battery", 1.0)], COD)
    assert {"tax_losses", "hebesatz_pct", "depreciation_class:bess"} <= set(res2.missing)
    assert res2.layers == ()


def test_the_zinsschranke_freigrenze_caps_all_interest_once_exceeded():
    de = load_pack("eu_de", as_of=date(2026, 1, 1))
    fin = FinanceInputs(financial_close=COD, tax_losses="carryforward", hebesatz_pct=400.0,
                        acquisition_date=date(2024, 1, 1))
    res = resolve_tax_layers(de, fin, [OwnerAsset("pv", "solar", 0.0)], COD)
    t = compute_tax(_tl(1), res.layers, ebitda=np.array([10e6]), basis=0.0,
                    interest=np.array([3.5e6]), losses="carryforward")
    # Above the €3m Freigrenze the cap is 30 % × 10m = 3m for ALL interest.
    assert t.taxable["kst"][0] == pytest.approx(10e6 - 3e6)
    assert "interest_capped:kst" in t.flags
    t2 = compute_tax(_tl(1), res.layers, ebitda=np.array([10e6]), basis=0.0,
                     interest=np.array([2.9e6]), losses="carryforward")
    assert t2.taxable["kst"][0] == pytest.approx(10e6 - 2.9e6)          # below: all deductible


@pytest.mark.parametrize("pack_id", ["eu_de", "us_federal"])
def test_every_rule_cites_a_source(pack_id):
    pack = load_pack(pack_id, as_of=date(2026, 1, 1))
    assert pack.rules and all(len(r.source) > 10 for r in pack.rules.values())
