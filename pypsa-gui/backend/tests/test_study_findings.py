"""
S6 findings, unit level (plan S6 "Acceptance"): the value streams over the
bill's six components, the battery attribution, the verdict on constructed
cases, and the tornado's range semantics. No solve here; the solving tests
are `test_study_tornado_lp.py` (real LP on the site golden fixture) and
`test_study_tornado_routes.py` (the worker, the forks and the routes with the
fake solver).
"""
from __future__ import annotations

import pandas as pd
import pytest

from models.study import (
    CapacityCharge,
    DemandCharge,
    EnergyBand,
    ExportCompensation,
    NetworkCharge,
    Tariff,
)
from services.study import findings as F
from services.study import tariff as T

# ── the value streams (S3 N8: all six bill components) ────────────────────


def _toy_tariff() -> Tariff:
    return Tariff(
        tariff_id="toy", name="toy", source="test", currency_year=2020,
        energy_bands=[EnergyBand(label="all", price_per_mwh=100.0)],
        demand_charge=DemandCharge(price_per_mw_per_period=5000.0),
        capacity_charge=CapacityCharge(price_per_mw_per_year=1000.0),
        connection_limit_mw=2.0, fixed_charge_per_period=100.0,
        network_charges=[NetworkCharge(label="net", price=10.0, basis="per_mwh")],
        export=ExportCompensation(price_per_mwh=40.0))


def _toy_bills():
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    w = pd.Series(1.0, index=idx)
    load = pd.Series(1.0, index=idx)
    load[idx.hour == 17] = 1.8
    base = T.BillCalculator().bill(load, pd.Series(0.0, index=idx), _toy_tariff(), w)
    # The option: shaved peak, some export at noon (PV-like), round-trip loss.
    imp = load.clip(upper=1.2) + 0.02
    imp[idx.hour == 12] = 0.0
    exp = pd.Series(0.0, index=idx)
    exp[idx.hour == 12] = 0.5
    opt = T.BillCalculator().bill(imp, exp, _toy_tariff(), w)
    return base, opt


def test_streams_cover_the_six_components_and_sum_to_the_savings_on_the_toy():
    base, opt = _toy_bills()
    streams = F.value_streams(base, opt)
    savings = base.annual_bill - opt.annual_bill
    assert [s.key for s in streams] == [
        "demand_charge_reduction", "energy_shift", "export_credit", "fixed"]
    assert sum(s.annual_value for s in streams) == pytest.approx(savings, rel=1e-12)
    by = {s.key: s for s in streams}
    # More export is MORE value: the credit stream is positive.
    assert by["export_credit"].annual_value > 0
    assert by["export_credit"].annual_value == pytest.approx(
        -(opt.by_component.export_credit - base.by_component.export_credit))
    assert by["demand_charge_reduction"].annual_value == pytest.approx(
        (base.by_component.demand + base.by_component.capacity)
        - (opt.by_component.demand + opt.by_component.capacity))
    assert sum(s.share for s in streams) == pytest.approx(1.0)
    assert all(s.engine == "bill_calculator" for s in streams)


def test_zero_savings_leave_the_shares_null_with_a_flag():
    base, _opt = _toy_bills()
    streams = F.value_streams(base, base)
    assert all(s.annual_value == 0.0 for s in streams)
    assert all(s.share is None and s.unavailable == {"share": "zero_savings"} for s in streams)


def test_the_stream_map_reuses_the_proformas_six_components():
    from services.study.proforma import BILL_COMPONENTS

    mapped = [c for _k, _l, comps in F.STREAMS for c in comps]
    assert sorted(mapped) == sorted(k for k, _ in BILL_COMPONENTS)


# ── range semantics (gate S2 [S5]) ────────────────────────────────────────

def _ledger_row(value, low, high, key="battery_storage_eur_per_kwh"):
    from models.study import LedgerRange, LedgerRow

    return LedgerRow(key=key, label=key, value=value, unit="EUR/kWh", source="t",
                     range=LedgerRange(low=low, high=high, source="assumed"))


def test_a_value_inside_its_range_keeps_the_range():
    assert F.bounds_for(_ledger_row(190.0, 132.9, 246.8)) == (132.9, 246.8, ())


def test_a_value_outside_its_range_recentres_the_relative_width_on_it():
    low, high, notes = F.bounds_for(_ledger_row(400.0, 132.9, 246.8))
    mid = (132.9 + 246.8) / 2
    assert (low, high) == pytest.approx((400.0 * 132.9 / mid, 400.0 * 246.8 / mid))
    assert low < 400.0 < high
    assert notes == ("range_recentred_on_user_value",)
    # A symmetric +-30 % band stays +-30 % around the user's value.
    low, high, _ = F.bounds_for(_ledger_row(10.0, 0.7, 1.3))
    assert (low, high) == pytest.approx((7.0, 13.0))


def test_a_row_without_a_value_or_a_range_has_no_bounds():
    row = _ledger_row(1.0, 0.7, 1.3).model_copy(update={"range": None})
    assert F.bounds_for(row) is None


# ── the verdict on constructed cases ─────────────────────────────────────

def _att(option_id, npv, p=0.8, method=None, status="ok"):
    from models.study import BatteryAttribution

    method = method or ("battery_removed_same_pv" if "pv" in option_id else "battery_only")
    return BatteryAttribution(
        option_id=option_id, status=status, method=method, battery_p_nom_mw=p,
        battery_npv=npv, option_npv=npv, reference_npv=None if method == "battery_only" else 0.0,
        battery_payback_simple=7.5, currency_year=2020, fidelity="full_study",
        unavailable={"reference_npv": "not_applicable_battery_only"}
        if method == "battery_only" else {})


def _rob(option_id, *bounds, status="ok"):
    from models.study import Robustness, TornadoRow

    rows = [TornadoRow(key=f"k{i}", label="k", low_value=0.7, high_value=1.3,
                       npv_low=lo, npv_high=hi, swing=abs(hi - lo))
            for i, (lo, hi) in enumerate(bounds)]
    return Robustness(status=status, tornado=rows, option_id=option_id, npv_centre=1.0)


def test_recommended_when_positive_at_the_centre_and_every_bound():
    atts = [_att("bess_1h", 1e5), _att("bess_2h", 3e5), _att("bess_4h", 2e5)]
    v = F.verdict(atts, _rob("bess_2h", (4e5, 2e5), (3.5e5, 2.5e5)), fidelity="full_study")
    assert (v.status, v.class_, v.option_id) == ("ok", "recommended", "bess_2h")
    assert v.facts["battery_npv"].value == 3e5
    assert v.disclosures[:2] == F.BY_CONSTRUCTION


def test_marginal_when_the_sign_flips_at_a_bound():
    atts = [_att("bess_2h", 3e4)]
    v = F.verdict(atts, _rob("bess_2h", (5e4, 1e4), (9e4, -2e4)), fidelity="full_study")
    assert v.class_ == "marginal"
    assert v.drivers == ["k1"]


def test_not_recommended_when_the_best_battery_npv_is_not_positive_at_the_centre():
    atts = [_att("bess_1h", -5.0), _att("bess_2h", 0.0)]
    v = F.verdict(atts, None, fidelity="full_study")
    assert (v.status, v.class_) == ("ok", "not_recommended")
    assert v.sentence_template == "not_recommended_best"


def test_without_an_ok_tornado_the_verdict_is_not_established():
    atts = [_att("bess_2h", 3e5)]
    assert F.verdict(atts, None).reasons == ("tornado_not_run",)
    aborted = _rob("bess_2h", (4e5, 2e5), status="not_established").model_copy(
        update={"note": "tornado_aborted", "pending": ["discount_rate"]})
    v = F.verdict(atts, aborted)
    assert v.status == "not_established" and v.class_ is None
    assert v.reasons == ("tornado_not_established", "tornado_aborted")
    assert F.verdict(atts, _rob("bess_4h", (1.0, 2.0))).reasons == ("tornado_on_another_option",)


def test_an_unattributed_bess_pv_option_is_never_named():
    from models.study import BatteryAttribution

    code = "bess_pv_value_not_attributable_to_battery"
    pv = BatteryAttribution(
        option_id="bess_pv_2h", status="not_established", method="battery_removed_same_pv",
        battery_p_nom_mw=0.3, battery_npv=None, option_npv=1e6, reference_npv=None,
        battery_payback_simple=None, notes=(code,),
        unavailable={"battery_npv": code, "reference_npv": "reference_not_computed",
                     "battery_payback_simple": code})
    zero = BatteryAttribution(
        option_id="bess_2h", status="skipped", method="battery_only", battery_p_nom_mw=0.0,
        battery_npv=None, option_npv=0.0, reference_npv=None, battery_payback_simple=None,
        notes=("size_zero_no_investment",),
        unavailable={k: "size_zero_no_investment"
                     for k in ("battery_npv", "reference_npv", "battery_payback_simple")})
    v = F.verdict([zero, pv], None)
    assert v.status == "not_established" and v.option_id is None
    assert code in v.reasons


def test_the_sentence_carries_numbers_only_as_fact_references():
    atts = [_att("bess_2h", 3e4)]
    for rob in (_rob("bess_2h", (5e4, 1e4)), _rob("bess_2h", (5e4, -1e4))):
        v = F.verdict(atts, rob, fidelity="full_study")
        assert F.sentence_is_digit_free(v.sentence), v.sentence
        for ref in __import__("re").findall(r"\{\{([a-z_]+)\}\}", v.sentence):
            assert ref in v.facts, ref
    assert not F.sentence_is_digit_free("NPV of 4.1 M")
    for tpl in F._TEMPLATES.values():
        assert F.sentence_is_digit_free(tpl.replace("{pv}", F._PV_CLAUSE))


def test_headline_kpis_state_their_provenance():
    v = F.verdict([_att("bess_2h", 3e4)], _rob("bess_2h", (5e4, 1e4)), fidelity="full_study")
    assert len(v.headline_kpis) == 3
    for fig in v.headline_kpis:
        assert fig.fidelity == "full_study" and fig.engine in ("cash_flow_expander", "lp")
        if fig.unit == "EUR":
            assert fig.basis is not None and fig.currency_year == 2020


def test_a_money_figure_without_its_basis_is_refused():
    from pydantic import ValidationError

    from models.study import Figure

    with pytest.raises(ValidationError, match="basis and currency year"):
        Figure(key="npv", label="NPV", value=1.0, unit="EUR", engine="cash_flow_expander",
               fidelity="full_study")
    with pytest.raises(ValidationError, match="fidelity"):
        Figure(key="p", label="P", value=1.0, unit="MW", engine="lp")


def test_the_main_caveat_is_the_demand_charge_foresight_when_that_stream_leads():
    from models.study import ValueStream

    def streams(demand, energy):
        return [ValueStream(key=k, label=k, annual_value=v, share=None, engine="bill_calculator",
                            unavailable={"share": "zero_savings"})
                for k, v in (("demand_charge_reduction", demand), ("energy_shift", energy))]

    atts, rob = [_att("bess_2h", 3e4)], _rob("bess_2h", (5e4, 1e4))
    v = F.verdict(atts, rob, streams=streams(8e4, -2e3), fidelity="full_study")
    assert v.main_caveat == "demand_charge_perfect_foresight"
    v = F.verdict(atts, rob, streams=streams(1e3, 5e4), fidelity="full_study")
    assert v.main_caveat == "perfect_foresight_dispatch"


# ── gate S6 BC-S6-1 / BC-S6-2 ────────────────────────────────────────────

def test_unjudged_options_leave_only_what_the_judged_ones_decide():
    expected = ["bess_1h", "bess_2h", "bess_4h"]      # bess_4h never solved
    atts = [_att("bess_1h", 1e5), _att("bess_2h", 3e5)]
    v = F.verdict(atts, _rob("bess_2h", (4e5, 2e5)), fidelity="full_study", expected=expected)
    assert (v.status, v.class_) == ("ok", "recommended")
    assert "options_not_all_judged" in v.reasons
    v = F.verdict(atts, _rob("bess_2h", (4e5, -2e5)), fidelity="full_study", expected=expected)
    assert (v.status, v.class_) == ("not_established", None)
    assert "options_not_all_judged" in v.reasons
    v = F.verdict([_att("bess_1h", -5.0)], None, fidelity="full_study", expected=expected)
    assert (v.status, v.class_) == ("not_established", None)
    # Every option judged: the same inputs decide.
    v = F.verdict([_att(o, -5.0) for o in expected], None, fidelity="full_study",
                  expected=expected)
    assert (v.status, v.class_) == ("ok", "not_recommended")


def test_a_money_fact_without_a_currency_year_is_null_with_a_flag():
    att = _att("bess_2h", 3e4).model_copy(update={"currency_year": None})
    v = F.verdict([att], _rob("bess_2h", (5e4, 1e4)), fidelity="full_study")
    npv = v.facts["battery_npv"]
    assert npv.value is None and npv.unavailable == "currency_year_unknown"
