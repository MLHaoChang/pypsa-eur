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


def _h2_site(*, multi=True, h2_series=False):
    n = _network(multi=multi)
    n.add("Carrier", "H2")
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "electrolyser", bus0="site", bus1="h2", p_nom=10.0, efficiency=0.7,
          carrier="H2")
    n.add("Load", "h2_load", bus="h2", p_set=H2_MW)
    if h2_series:
        n.loads_t.p_set["h2_load"] = np.full(len(n.snapshots), H2_MW)
    return n


def _solved(*, multi=True, h2_series=False, solve_kw=None, network=None, **com_extra):
    com = _commercial(H2_OWNED, contracts=[])
    com.pop("connection")
    com.update(com_extra)
    n = network if network is not None else _h2_site(multi=multi, h2_series=h2_series)
    return _solve(n, com, multi=multi, **(solve_kw or {}))


def _fin_h2(**over):
    from datetime import date
    return _fin(**{"cod_by_asset": {"pv": date(2030, 1, 1), "bess": date(2030, 1, 1),
                                    "electrolyser": date(2030, 1, 1)},
                   "annualise": True, **over})


def _w(n, period):
    w = n.snapshot_weightings.objective
    return (w if period is None else w.loc[period]).to_numpy(float)


def _kg(n, period) -> float:
    return float((_w(n, period) * H2_MW).sum()) * 1000.0 / LHV


def _annual(n, period) -> float:
    return 8760.0 / float(_w(n, period).sum())


def _real_fin(**over):
    from models.finance import ESCALATION_CLASSES
    return _fin_h2(price_basis="real", inflation=0.0,
                   escalation={c: 0.0 for c in ESCALATION_CLASSES}, **over)


def _cf_line_by_year(result, key):
    years = list(result.op.tl.years)
    arr = result.op_counterfactual.lines[key]
    return {y: arr[i] for i, y in enumerate(years)}


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
    # absolute: the 2030 template's −kg × intensity × 100, escalated 2029 → 2030 at 3 %
    assert arr[i30] == pytest.approx(
        -_kg(n, 2030) * CO2_KG * 100.0 * _annual(n, 2030) * 1.03, rel=1e-9)
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


# ── gatekeeper review of #305 ────────────────────────────────────────────────

@pytest.mark.live_solve
def test_a_listed_co2_year_inside_a_single_period_steps_the_price_that_year(reset_backend):
    """Snapshots in 2030, COD 2031, prices {2030: 80, 2035: 150}: 2031-34 at
    80, 2035 on at 150 (real case, no escalation)."""
    from datetime import date

    from services.finance.engine import run_case

    co2 = {"eur_per_t_by_year": {"2030": 80.0, "2035": 150.0}, "money_year": 2029,
           "source": "test"}
    n, cfg = _solved(multi=False, reference_purchases=[PURCHASE], co2_price=co2)
    cod = date(2031, 1, 1)
    fin = _real_fin(financial_close=date(2030, 1, 1),
                    cod_by_asset={"pv": cod, "bess": cod, "electrolyser": cod})
    case = _case(n, cfg, fin)
    assert {"co2_price_period:2031:2030:80", "co2_price_period:2035:2035:150"} <= set(case.flags)
    by_year = _cf_line_by_year(run_case(case), "co2:purchase:h2_load")
    per_t = -_kg(n, None) * CO2_KG * _annual(n, None)
    for y in range(2031, 2035):
        assert by_year[y] == pytest.approx(per_t * 80.0, rel=1e-9), y
    for y in range(2035, 2041):
        assert by_year[y] == pytest.approx(per_t * 150.0, rel=1e-9), y


@pytest.mark.live_solve
def test_listed_years_inside_multi_year_periods_step_too(reset_backend):
    """Two periods (2030, 2040), prices {2030: 100, 2032: 200, 2040: 300}."""
    from services.finance.engine import run_case

    co2 = {"eur_per_t_by_year": {"2030": 100.0, "2032": 200.0, "2040": 300.0},
           "money_year": 2029, "source": "test"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    by_year = _cf_line_by_year(run_case(_case(n, cfg, _real_fin())), "co2:purchase:h2_load")
    unit = -_kg(n, 2030) * CO2_KG * _annual(n, 2030)
    assert by_year[2030] == pytest.approx(unit * 100.0, rel=1e-9)
    assert by_year[2031] == pytest.approx(unit * 100.0, rel=1e-9)
    for y in (2032, 2035, 2039):
        assert by_year[y] == pytest.approx(unit * 200.0, rel=1e-9), y
    unit40 = -_kg(n, 2040) * CO2_KG * _annual(n, 2040)
    assert by_year[2040] == pytest.approx(unit40 * 300.0, rel=1e-9)


@pytest.mark.live_solve
def test_coverage_is_checked_against_the_first_operating_year(reset_backend):
    """Snapshots in 2030 but COD 2032: a price from 2032 covers every case
    year; one from 2033 does not."""
    from datetime import date

    n, cfg = _solved(multi=False, reference_purchases=[PURCHASE],
                     co2_price={"eur_per_t_by_year": {"2032": 90.0}, "money_year": 2029,
                                "source": "test"})
    cod = date(2032, 1, 1)
    fin = _real_fin(financial_close=date(2031, 1, 1),
                    cod_by_asset={"pv": cod, "bess": cod, "electrolyser": cod})
    case = _case(n, cfg, fin)
    assert "co2_price_period:2032:2032:90" in case.flags
    cfg.commercial = {**cfg.commercial, "co2_price": {"eur_per_t_by_year": {"2033": 90.0},
                                                      "money_year": 2029, "source": "test"}}
    with pytest.raises(FinanceRefused) as exc:
        _case(n, cfg, fin)
    assert exc.value.code == "co2_price_year_uncovered:2032"


@pytest.mark.live_solve
def test_one_of_two_fed_loads_without_a_purchase_still_blocks(reset_backend):
    n = _h2_site()
    n.add("Load", "h2_second", bus="h2", p_set=1.0)
    n, cfg = _solved(network=n, reference_purchases=[PURCHASE])
    case = _case(n, cfg, _fin_h2())
    assert "counterfactual_not_established:owner_conversion_load" in case.flags
    assert "counterfactual_reference_purchase_missing:h2_second" in case.flags
    assert not any(f.startswith("counterfactual_buys") for f in case.flags)


@pytest.mark.live_solve
def test_the_purchase_follows_the_load_the_lp_solved(reset_backend):
    """`load_scalers` scale the H2 load for the solve: the purchase reads the
    scaled demand (`lp_demand_frame`), not the raw p_set."""
    n, cfg = _solved(h2_series=True, reference_purchases=[PURCHASE],
                     solve_kw={"load_scalers": {"2040": 1.5}})
    case = _case(n, cfg, _fin_h2())
    first, second = (_lines(t)["counterfactual:purchase:h2_load"] for t in case.counterfactual)
    assert first.amount == pytest.approx(-_kg(n, 2030) * PRICE_KG * _annual(n, 2030), rel=1e-9)
    assert second.amount == pytest.approx(-1.5 * _kg(n, 2040) * PRICE_KG * _annual(n, 2040),
                                          rel=1e-9)


@pytest.mark.live_solve
def test_money_basis_flags(reset_backend):
    co2 = {"eur_per_t_by_year": {"2030": 50.0}, "money_year": 2024, "source": "test"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    real = _case(n, cfg, _real_fin(currency_year=2020))
    assert "co2_price_money_year_differs:2024:2020" in real.flags
    nominal = _case(n, cfg, _fin_h2(inflation=None))
    assert "co2_price_not_established:no_inflation" in nominal.flags
    for t in nominal.counterfactual:
        assert _lines(t)["co2:purchase:h2_load"].amount is None


@pytest.mark.live_solve
def test_a_blocked_counterfactual_adds_no_grid_co2_on_either_side(reset_backend):
    co2 = {"eur_per_t_by_year": {"2030": 50.0}, "money_year": 2029, "source": "test",
           "grid_t_per_mwh": 0.3, "grid_source": "test"}
    n, cfg = _solved(co2_price=co2)                          # no purchase: blocked
    case = _case(n, cfg, _fin_h2())
    assert "counterfactual_not_established:owner_conversion_load" in case.flags
    for t in [*case.templates, *case.counterfactual]:
        assert "co2:grid_import" not in _lines(t)



@pytest.mark.live_solve
def test_a_template_in_force_only_before_the_first_operating_year_is_never_priced(reset_backend):
    """#305 review: periods 2030/2040, COD 2041, a CO2 price from 2041. The
    2030 and 2040 templates' own years precede operation: no IndexError, no
    flag for a year with no operation, and 2041 on priced at 90."""
    from datetime import date

    from services.finance.engine import run_case

    co2 = {"eur_per_t_by_year": {"2041": 90.0}, "money_year": 2029, "source": "test"}
    n, cfg = _solved(reference_purchases=[PURCHASE], co2_price=co2)
    cod = date(2041, 1, 1)
    fin = _real_fin(financial_close=date(2040, 1, 1),
                    cod_by_asset={"pv": cod, "bess": cod, "electrolyser": cod})
    case = _case(n, cfg, fin)
    periods = [f for f in case.flags if f.startswith("co2_price_period:")]
    assert periods == ["co2_price_period:2041:2041:90"], periods
    by_year = _cf_line_by_year(run_case(case), "co2:purchase:h2_load")
    unit = -_kg(n, 2040) * CO2_KG * _annual(n, 2040)
    for y in (2041, 2045, 2050):
        assert by_year[y] == pytest.approx(unit * 90.0, rel=1e-9), y
    # with an earlier listed year too, no flag for 2040 (it never operates)
    cfg.commercial = {**cfg.commercial, "co2_price": {
        "eur_per_t_by_year": {"2035": 50.0, "2041": 90.0}, "money_year": 2029, "source": "t"}}
    case = _case(n, cfg, fin)
    assert [f for f in case.flags if f.startswith("co2_price_period:")] == \
        ["co2_price_period:2041:2041:90"]


@pytest.mark.live_solve
def test_a_blocked_counterfactual_with_a_co2_price_reads_not_established(reset_backend):
    from services.finance.engine import run_case
    from services.finance.report import _counterfactual_block

    co2 = {"eur_per_t_by_year": {"2030": 50.0}, "money_year": 2029, "source": "test"}
    n, cfg = _solved(co2_price=co2)                          # no purchase: blocked
    case = _case(n, cfg, _fin_h2())
    assert "co2_price_not_applied:counterfactual_not_established" in case.flags
    block = _counterfactual_block(run_case(case), case)
    assert block["co2_priced"] is True and block["avoided_co2_cost"] is None
