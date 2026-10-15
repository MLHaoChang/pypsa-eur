"""The counterfactual's reference purchase and CO2 price (generic-site E2E
H2e; owner Q3, gatekeeper review of the design).

An owner electrolyser serving an H2 load used to make the counterfactual
impossible (`counterfactual_not_established:owner_conversion_load`): the site
without the investment had no source for the H2. With a reference purchase
for that load, the counterfactual buys it (grey H2 at a stated price per kg),
and a CO2 price, by year, puts a certificate cost on the purchase and,
opt-in, on grid imports on BOTH sides. CO2 prices are real in their money
year: a nominal case escalates them at its inflation, a real case keeps them.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest

from services.finance.case import FinanceRefused
from tests.test_finance_case_adapter import OWNED, _case, _fin, _lines
from tests.test_value_flow_reconciliation import _commercial, _network, _solve

H2_MW = 3.0
LHV = 33.33
PRICE_KG = 2.0
CO2_KG = 0.010
H2_OWNED = {**copy.deepcopy(OWNED), "asset_owners": [
    *copy.deepcopy(OWNED["asset_owners"]),
    {"asset_id": "electrolyser", "component": "Link", "owner": "site"}]}
PURCHASE = {"load": "h2_load", "price_eur_per_kg": PRICE_KG, "co2_t_per_kg": CO2_KG,
            "source": "test: grey H2 by SMR"}


def _h2_site():
    n = _network(multi=True)
    n.add("Carrier", "H2")
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=10.0, efficiency=0.7,
          carrier="H2")
    n.add("Load", "h2_load", bus="h2", p_set=H2_MW)
    return n


def _solved(**com_extra):
    com = _commercial(H2_OWNED, contracts=[])
    com.pop("connection")
    com.update(com_extra)
    return _solve(_h2_site(), com, multi=True)


def _fin_h2(**over):
    from datetime import date
    return _fin(**{"cod_by_asset": {"pv": date(2030, 1, 1), "bess": date(2030, 1, 1),
                                    "electrolyser": date(2030, 1, 1)},
                   "annualise": True, **over})


def _kg(n, period) -> float:
    w = n.snapshot_weightings.objective.loc[period].to_numpy(float)
    return float((w * H2_MW).sum()) * 1000.0 / LHV


def _annual(n, period) -> float:
    return 8760.0 / float(n.snapshot_weightings.objective.loc[period].sum())


@pytest.mark.live_solve
def test_without_a_purchase_the_owner_conversion_still_blocks(reset_backend):
    n, cfg = _solved()
    case = _case(n, cfg, _fin_h2())
    assert "counterfactual_not_established:owner_conversion_load" in case.flags
    for t in case.counterfactual:
        assert _lines(t)["counterfactual:owner_conversion_load"].amount is None


@pytest.mark.live_solve
def test_the_counterfactual_buys_the_h2_at_the_reference_price(reset_backend):
    n, cfg = _solved(reference_purchases=[PURCHASE])
    case = _case(n, cfg, _fin_h2())
    assert "counterfactual_buys:h2_load" in case.flags
    assert not any(f.startswith("counterfactual_not_established") for f in case.flags)
    for t, p in zip(case.counterfactual, (2030, 2040)):
        ln = _lines(t)["counterfactual:purchase:h2_load"]
        # by hand: 3 MW over the period's weighted hours, 33.33 kWh/kg, 2 EUR/kg, annualised
        assert ln.amount == pytest.approx(-_kg(n, p) * PRICE_KG * _annual(n, p), rel=1e-9)
        assert ln.stream == "fuel" and ln.money_year == case.base_year
        # no CO2 price: no CO2 line
        assert not any(k.startswith("co2:") for k in _lines(t))


@pytest.mark.live_solve
def test_the_co2_cost_rises_by_exactly_kg_times_intensity_times_price(reset_backend):
    """The gatekeeper's test: two periods at CO2 0 and 100 EUR/t."""
    co2 = {"eur_per_t_by_year": {"2030": 0.0, "2040": 100.0}, "money_year": 2029,
           "source": "test: ETS-like trajectory"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    case = _case(n, cfg, _fin_h2())
    first, second = (_lines(t) for t in case.counterfactual)
    assert first["co2:purchase:h2_load"].amount == pytest.approx(0.0, abs=1e-9)
    assert second["co2:purchase:h2_load"].amount == pytest.approx(
        -_kg(n, 2040) * CO2_KG * 100.0 * _annual(n, 2040), rel=1e-9)
    assert {"co2_price_period:2030:2030:0", "co2_price_period:2040:2040:100"} <= set(case.flags)
    # the CO2 line is avoided cash, and says so
    assert second["co2:purchase:h2_load"].source_id == "co2_avoided_cash:h2_load"


@pytest.mark.live_solve
def test_a_period_before_the_first_listed_co2_year_is_refused(reset_backend):
    co2 = {"eur_per_t_by_year": {"2035": 90.0}, "money_year": 2029, "source": "test"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    with pytest.raises(FinanceRefused) as exc:
        _case(n, cfg, _fin_h2())
    assert exc.value.code == "co2_price_year_uncovered:2030"


@pytest.mark.live_solve
def test_grid_co2_is_on_both_sides_so_only_the_difference_counts(reset_backend):
    co2 = {"eur_per_t_by_year": {"2030": 50.0}, "money_year": 2029, "source": "test",
           "grid_t_per_mwh": 0.3, "grid_source": "test: grid average"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    case = _case(n, cfg, _fin_h2())
    p0 = n.links_t.p0["import"].to_numpy(float)
    for t_act, t_cf, p in zip(case.templates, case.counterfactual, (2030, 2040)):
        m = np.asarray(n.snapshots.get_level_values(0)) == p
        w = n.snapshot_weightings.objective.to_numpy(float)
        act = _lines(t_act)["co2:grid_import"]
        assert act.amount == pytest.approx(
            -float((w[m] * p0[m]).sum()) * 0.3 * 50.0 * _annual(n, p), rel=1e-6)
        cf = _lines(t_cf)["co2:grid_import"]
        assert cf.amount is not None and cf.amount < 0


@pytest.mark.live_solve
def test_co2_prices_are_real_a_nominal_case_escalates_them_a_real_case_keeps_them(
        reset_backend):
    from models.finance import ESCALATION_CLASSES
    from services.finance.engine import run_case

    co2 = {"eur_per_t_by_year": {"2030": 100.0}, "money_year": 2029, "source": "test"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    nominal = run_case(_case(n, cfg, _fin_h2(inflation=0.03)))
    arr = nominal.op_counterfactual.lines["co2:purchase:h2_load"]
    years = list(nominal.op.tl.years)
    i30, i31 = years.index(2030), years.index(2031)
    assert arr[i31] / arr[i30] == pytest.approx(1.03, rel=1e-9)      # inflation, not opex 2 %
    real = run_case(_case(n, cfg, _fin_h2(
        price_basis="real", inflation=0.0, escalation={c: 0.0 for c in ESCALATION_CLASSES})))
    arr = real.op_counterfactual.lines["co2:purchase:h2_load"]
    assert arr[i31] == pytest.approx(arr[i30], rel=1e-12)
    # the report shows the avoided CO2 cost apart
    from services.finance.report import _avoided_co2
    avoided = _avoided_co2(real)
    assert avoided is not None and avoided[i30] == pytest.approx(-arr[i30], rel=1e-9)


@pytest.mark.live_solve
def test_unset_inputs_leave_the_case_and_its_hash_unchanged(reset_backend):
    from services.results.finance_case import finance_case_hash

    n, cfg = _solved()
    before = _case(n, cfg, _fin_h2())
    cfg.commercial = {**cfg.commercial, "reference_purchases": [], "co2_price": None}
    after = _case(n, cfg, _fin_h2())
    assert finance_case_hash(after) == finance_case_hash(before)
    assert after.counterfactual_hash == before.counterfactual_hash


def test_the_models_refuse_what_the_case_cannot_use():
    from pydantic import ValidationError

    from models.commercial import Co2Price, ReferencePurchase

    with pytest.raises(ValidationError):
        ReferencePurchase(load="h", price_eur_per_kg=2.0, source="s")      # no co2 default
    with pytest.raises(ValidationError):
        Co2Price(eur_per_t_by_year={}, money_year=2029, source="s")
    with pytest.raises(ValidationError):
        Co2Price(eur_per_t_by_year={2030: 80.0}, money_year=2029, source="s", grid_t_per_mwh=0.3)
    assert ReferencePurchase(load="h", price_eur_per_kg=2.0, co2_t_per_kg=0.01,
                             source="s").lhv_kwh_per_kg == pytest.approx(33.33)
