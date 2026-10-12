"""
IC D9 (plan `docs/superpowers/plans/2026-10-06-ic-s0b-replacements-terminal.md` §4 WP-B; S4, S5, S9): one
replacement schedule, `finance.replacements.schedule(case, tl)`, the only reader of the replacement inputs —
the cash, the tax vintages, the storage LCOS and the `asset_lifetime_short` check read it.

`replacement_rule="part_lifetimes"`: each part with a finite lifetime L is re-bought at its current upfront
cost in the last service year of the previous purchase, `cod_year + k·L − 1` for every k ≥ 1 with
k·L < `analysis_years` (GS's t = k·L, financial close one year before COD). Under `fixed` the schedule is
`replacement_capex` exactly.

The battery: power 400,000 · 10 y, energy 1,200,000 · 15 y; COD 2030, 25 operating years (2030–2054):
power re-bought in 2039 and 2049, energy in 2044.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from models.finance import FinanceInputs
from services.finance.case import (
    AssetFinance, AssetPart, FinanceCase, FinanceRefused, StorageYear, Template, TemplateLine,
)
from services.finance.engine import run_case
from services.finance.replacements import Replacement, schedule
from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year
from services.finance.timeline import build_timeline

COD = date(2030, 1, 1)
LAYER = (TaxLayer(name="corp", rate=0.25,
                  depreciation=(DepreciationClass("all", 1.0, sl_half_year(10)),)),)
POWER = AssetPart("power", 400_000.0, 10.0, 0.01)
ENERGY = AssetPart("energy", 1_200_000.0, 15.0, 0.0)
BESS = AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery", (POWER, ENERGY))
OM_KEY = "asset:fom:StorageUnit:bess"


def _fin(**over) -> FinanceInputs:
    kw = dict(financial_close=date(2029, 1, 1), cod_by_asset={}, analysis_years=25,
              contingency_share=0.0, capex_phasing=[1.0],
              escalation={"opex": 0.0, "tariff": 0.0, "export": 0.0, "capex": 0.02},
              degradation_by_asset={"bess": 0.0}, tax_losses="offset_other_income",
              financing_fee_tax="not_deducted", wacc_nominal=0.07, cost_of_equity=0.09,
              inflation=0.02, replacement_rule="part_lifetimes")
    kw.update(over)
    return FinanceInputs(**kw)


def _case(fin=None, assets=(BESS,), storage=True) -> FinanceCase:
    st = {"bess": StorageYear(900.0, 1000.0, 40_000.0, 0.0, om_keys=(OM_KEY,))} if storage else {}
    lines = (TemplateLine("bill", "energy_import", 400_000.0, "tariff"),
             TemplateLine(OM_KEY, "fom", -2_000.0, "opex", source="asset"))
    return FinanceCase(inputs=fin or _fin(), owner="o", base_year=2030, cod=COD,
                       templates=(Template(2030, lines, storage=st),), assets=tuple(assets))


def _sched(case):
    return schedule(case, build_timeline(case))


# ── the schedule ─────────────────────────────────────────────────────────────


def test_part_lifetimes_replaces_each_part_in_the_last_service_year():
    assert _sched(_case()) == (
        Replacement(2039, "bess", "power", 400_000.0, "part_lifetimes"),
        Replacement(2044, "bess", "energy", 1_200_000.0, "part_lifetimes"),
        Replacement(2049, "bess", "power", 400_000.0, "part_lifetimes"),
    )


def test_fixed_is_exactly_the_replacement_capex():
    entries = [(2039, "bess", 400_000.0), (2044, "bess", 1_200_000.0), (2049, "bess", 400_000.0)]
    got = _sched(_case(_fin(replacement_rule="fixed", replacement_capex=entries)))
    assert got == tuple(Replacement(y, a, None, x, "fixed") for y, a, x in entries)
    assert FinanceInputs.model_fields["replacement_rule"].default == "fixed"


def test_an_asset_without_parts_is_replaced_through_its_one_effective_part():
    a = AssetFinance("gen", "Generator", 1_000_000.0, 10.0)
    assert _sched(_case(assets=(a,), storage=False)) == (
        Replacement(2039, "gen", "investment", 1_000_000.0, "part_lifetimes"),
        Replacement(2049, "gen", "investment", 1_000_000.0, "part_lifetimes"))
    # Under `fixed` the same asset is refused: a short life with no replacement.
    with pytest.raises(FinanceRefused) as exc:
        build_timeline(_case(_fin(replacement_rule="fixed"), assets=(a,), storage=False))
    assert exc.value.code == "asset_lifetime_short"


def test_an_infinite_part_lifetime_is_never_replaced():
    a = AssetFinance("pv", "Generator", 1_000_000.0, None,
                     parts=(AssetPart("investment", 1_000_000.0, math.inf, None),))
    assert _sched(_case(assets=(a,), storage=False)) == ()
    assert run_case(_case(assets=(a,), storage=False), layers=LAYER).op.status["capex"] == "ok"


def test_a_part_lifetime_at_or_beyond_the_axis_is_never_replaced():
    a = AssetFinance("pv", "Generator", 1_000_000.0, 25.0)
    assert _sched(_case(assets=(a,), storage=False)) == ()


# ── the readers: cash, tax vintages, LCOS ────────────────────────────────────


def test_the_cash_replacement_row_is_the_schedule_escalated_by_capex():
    r = run_case(_case(), layers=LAYER)
    tl = r.tl
    want = np.zeros(tl.n)
    want[tl.index(2039)] = 400_000.0 * 1.02 ** 9            # escalated from the base year 2030
    want[tl.index(2044)] = 1_200_000.0 * 1.02 ** 14
    want[tl.index(2049)] = 400_000.0 * 1.02 ** 19
    assert r.op.replacement == pytest.approx(want, rel=1e-12)
    assert r.op.status["capex"] == "ok"
    # No contingency on a replacement (the initial capex carries it).
    r2 = run_case(_case(_fin(contingency_share=0.1)), layers=LAYER)
    assert r2.op.replacement == pytest.approx(want, rel=1e-12)
    assert float(r2.op.capex.sum()) == pytest.approx(1_760_000.0)


def test_the_tax_vintages_read_the_schedule():
    from services.finance.engine import _replacement_vintages

    case = _case()
    tl = build_timeline(case)
    got = _replacement_vintages(case, tl)
    want = [(tl.index(y), x * 1.02 ** (y - 2030), "bess")
            for y, x in ((2039, 400_000.0), (2044, 1_200_000.0), (2049, 400_000.0))]
    assert [(i, a) for i, _v, a in got] == [(i, a) for i, _v, a in want]
    assert [v for _i, v, _a in got] == pytest.approx([v for _i, v, _a in want], rel=1e-12)


def test_the_lcos_with_part_lifetimes_equals_the_same_entries_typed():
    gen = run_case(_case(), layers=LAYER)
    typed = run_case(_case(_fin(replacement_rule="fixed", replacement_capex=[
        (2039, "bess", 400_000.0), (2044, "bess", 1_200_000.0), (2049, "bess", 400_000.0)])),
        layers=LAYER)
    assert gen.metrics["lcos_nominal_per_mwh"] is not None
    assert gen.metrics["lcos_nominal_per_mwh"] == pytest.approx(
        typed.metrics["lcos_nominal_per_mwh"], rel=1e-12)
    assert gen.lcos["assets"]["bess"]["pv_replacement"] == pytest.approx(
        typed.lcos["assets"]["bess"]["pv_replacement"], rel=1e-12)
    assert gen.cash["equity_post_tax"] == pytest.approx(typed.cash["equity_post_tax"], rel=1e-12)


# ── refusals and flags ───────────────────────────────────────────────────────


def test_a_replacement_entry_for_a_part_lifetimes_asset_is_a_conflict():
    with pytest.raises(FinanceRefused) as exc:
        run_case(_case(_fin(replacement_capex=[(2040, "bess", 1.0)])), layers=LAYER)
    assert exc.value.code == "replacement_rule_conflict:bess"


def test_an_entry_for_an_asset_the_rule_never_replaces_still_applies():
    pv = AssetFinance("pv", "Generator", 500_000.0, None,
                      parts=(AssetPart("investment", 500_000.0, math.inf, None),))
    case = _case(_fin(replacement_capex=[(2040, "pv", 50_000.0)]), assets=(BESS, pv))
    got = _sched(case)
    assert Replacement(2040, "pv", None, 50_000.0, "fixed") in got
    assert len([x for x in got if x.source == "part_lifetimes"]) == 3


def test_a_part_without_a_lifetime_is_not_established():
    bess = AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery",
                        (AssetPart("power", 400_000.0, None, None), ENERGY))
    # 15 operating years: the asset's own 15-y lifetime is not short, so the check passes.
    r = run_case(_case(_fin(analysis_years=15), assets=(bess,)), layers=LAYER)
    assert "part_lifetime_missing:bess:power" in r.op.reasons["capex"]
    assert r.op.capex is None and r.cash["project_pre_tax"] is None


def test_a_nan_part_lifetime_is_part_lifetime_missing():
    bess = AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery",
                        (AssetPart("power", 400_000.0, float("nan"), None), ENERGY))
    r = run_case(_case(_fin(analysis_years=15), assets=(bess,)), layers=LAYER)
    assert "part_lifetime_missing:bess:power" in r.op.reasons["capex"]


def test_a_non_whole_lifetime_rounds_half_up_and_is_flagged():
    a = AssetFinance("gen", "Generator", 1_000_000.0, 12.5)       # k·L = 12.5 → 13 → 2042
    case = _case(assets=(a,), storage=False)
    assert _sched(case) == (Replacement(2042, "gen", "investment", 1_000_000.0, "part_lifetimes"),)
    assert "part_lifetime_rounded:gen:investment" in run_case(case, layers=LAYER).flags
    b = AssetFinance("gen", "Generator", 1_000_000.0, 12.4)       # 12.4 → 2041, 24.8 → 25 → 2054
    assert [x.year for x in _sched(_case(assets=(b,), storage=False))] == [2041, 2054]


def test_a_part_lifetime_below_one_year_is_refused():
    a = AssetFinance("gen", "Generator", 1_000_000.0, 0.5)
    with pytest.raises(FinanceRefused) as exc:
        build_timeline(_case(assets=(a,), storage=False))
    assert exc.value.code == "part_lifetime_too_short:gen:investment"


def test_the_asset_lifetime_short_check_passes_for_a_part_lifetimes_asset():
    build_timeline(_case())                                   # bess 15 y < 25 y: replaced
    with pytest.raises(FinanceRefused) as exc:
        build_timeline(_case(_fin(replacement_rule="fixed")))
    assert exc.value.code == "asset_lifetime_short"
    # A part of unknown lifetime: not counted as replaced.
    bess = AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery",
                        (AssetPart("power", 400_000.0, None, None), ENERGY))
    with pytest.raises(FinanceRefused) as exc:
        build_timeline(_case(assets=(bess,)))
    assert exc.value.code == "asset_lifetime_short"


def test_fixed_flags_a_short_part_that_is_not_replaced():
    r = run_case(_case(_fin(replacement_rule="fixed", analysis_years=15)), layers=LAYER)
    assert "part_not_replaced:bess:power" in r.flags
    assert not any(f.startswith("part_not_replaced:bess:energy") for f in r.flags)
    r2 = run_case(_case(_fin(replacement_rule="fixed", analysis_years=15,
                             replacement_capex=[(2039, "bess", 400_000.0)])), layers=LAYER)
    assert not any(f.startswith("part_not_replaced") for f in r2.flags)


# ── the report (S9) ──────────────────────────────────────────────────────────


def test_a_generated_replacement_keeps_its_source_with_the_part():
    from services.finance.report import assemble_finance_sections

    case = _case()
    r = run_case(case, layers=LAYER)
    lines = [ln for ln in assemble_finance_sections(r, case).cashflow_lines
             if ln.provenance.source == "replacement_capex"]
    got = {(ln.year, ln.asset, ln.provenance.source_id): ln.amount for ln in lines}
    assert got == pytest.approx({
        (2039, "bess", "bess:power"): -400_000.0 * 1.02 ** 9,
        (2044, "bess", "bess:energy"): -1_200_000.0 * 1.02 ** 14,
        (2049, "bess", "bess:power"): -400_000.0 * 1.02 ** 19})
    assert all(ln.value_stream == "capex" for ln in lines)
