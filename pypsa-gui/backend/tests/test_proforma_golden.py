"""
The pro forma against the site golden fixture (plan S5; review v1 B5, B6,
N12; review v2 BC-7; gates S1, S2, S3 and S4 carries).

Every expected number here comes from `tests/golden/oracle.py` (first
principles, no `services/` import) fed with the LEDGER's values and the
solved network's sizes, or from an existing reporting surface the pro forma
must reconcile with (`asset_economics`, `cost_breakdown`). The fixture is the
production site shape (`tests/golden/site_fixture.py`), solved for real.
"""
from __future__ import annotations

import math
import re

import pytest

from tests.golden import oracle
from tests.golden import site_fixture as sf

BILL_COMPONENTS = {"energy", "demand", "fixed", "network", "capacity", "export_credit"}


def _v(ledger, key):
    return next(r.value for r in ledger.rows if r.key == key)


def _live_econ(n, cfg):
    from services.adequacy.eh_report import _live_result_df
    from services.results.asset_economics import compute_asset_economics

    return compute_asset_economics(n, cfg, result_df=_live_result_df)


def _case(option_id, **kw):
    from services.study import proforma as P

    n, cfg = sf.solve_site_option(option_id)
    nb, _ = sf.solve_site_option("none")
    ledger = kw.pop("ledger", None) or sf.site_ledger()
    tariff = kw.pop("tariff", None) or sf.site_tariff(ledger)
    return P.build_investment_case(
        n, cfg, nb, ledger, kw.pop("bills", None), option_id,
        study_id=sf.SITE_STUDY_ID, tariff=tariff, fidelity="full_study", **kw)


@pytest.fixture(scope="module")
def cases():
    return {o: _case(o) for o in ("bess_2h", "bess_pv_2h")}


def _sizes(option_id):
    n, _ = sf.solve_site_option(option_id)
    p_bat = float(n.storage_units.at["battery", "p_nom_opt"])
    p_pv = float(n.generators.at["pv", "p_nom_opt"]) if "pv" in n.generators.index else 0.0
    return p_bat, p_pv


def _rate_and_horizon(ledger):
    return _v(ledger, "discount_rate"), int(_v(ledger, "battery_storage_lifetime_years"))


# ── the fixture is not vacuous ───────────────────────────────────────────

def test_the_fixture_sizes_a_battery_and_pv_at_an_interior_optimum():
    ledger = sf.site_ledger()
    cap = sf.CONNECTION_MW * _v(ledger, "sizing_limit_connection_multiple")
    for option in ("bess_2h", "bess_pv_2h"):
        p_bat, p_pv = _sizes(option)
        assert 0.1 < p_bat < cap - 1e-3, (option, p_bat)
    assert 0.1 < _sizes("bess_pv_2h")[1] < cap - 1e-3


# ── CAPEX: the ledger upfront in year 0, never an annuity ────────────────

@pytest.mark.parametrize("option", ["bess_2h", "bess_pv_2h"])
def test_capex_is_the_ledger_upfront_booked_in_year_zero(cases, option):
    case, ledger = cases[option], sf.site_ledger()
    p_bat, p_pv = _sizes(option)
    expected = oracle.battery_upfront_per_mw(
        _v(ledger, "battery_inverter_eur_per_kw"),
        _v(ledger, "battery_storage_eur_per_kwh"), 2.0) * p_bat
    expected += _v(ledger, "pv_rooftop_eur_per_kw") * 1000.0 * p_pv
    assert sum(y.capex for y in case.years) == pytest.approx(expected, rel=1e-9)
    assert case.years[0].year == 0 and case.years[0].capex == pytest.approx(expected, rel=1e-9)
    assert all(y.capex == 0.0 for y in case.years[1:])
    assert case.kpis.capex_total == pytest.approx(expected, rel=1e-9)


def test_the_battery_capex_is_not_the_single_lifetime_back_calculation(cases):
    """
    Gate S4: the case books the ledger's upfront, never a single-lifetime
    back-calculation. Since U2 WP7 (C1) the pack writes the battery as its two
    upfront parts and `upfront_cost_series` sums them, so the S4 gap is CLOSED
    (recorded delta `c1_parts_close_the_s4_gap`): the figure other screens show
    equals the case's, and the case says so (`battery_upfront_from_two_parts`).
    """
    from services.solver.periodized_costs import upfront_cost_series
    from services.solver_service import with_periodized_cost_defaults

    n, cfg = sf.solve_site_option("bess_2h")
    with with_periodized_cost_defaults(n, cfg, for_back_calculation=True):
        back = float(upfront_cost_series(n, "StorageUnit")["battery"]) * _sizes("bess_2h")[0]
    case = cases["bess_2h"]
    assert back == pytest.approx(case.kpis.capex_total, rel=1e-9)
    [gap] = [g for g in case.upfront_gaps if g.asset == "battery"]
    assert gap.ledger_upfront_eur == pytest.approx(case.kpis.capex_total, rel=1e-9)
    assert gap.back_calculated_upfront_eur == pytest.approx(back, rel=1e-9)
    assert "battery_upfront_from_two_parts" in case.honesty_notes
    assert "battery_upfront_from_ledger_not_back_calculated" not in case.honesty_notes


def test_inverter_replacements_land_at_its_lifetime_inside_the_horizon(cases):
    ledger = sf.site_ledger()
    _r, horizon = _rate_and_horizon(ledger)
    life = int(_v(ledger, "battery_inverter_lifetime_years"))
    for option, case in cases.items():
        p_bat, _ = _sizes(option)
        inverter = _v(ledger, "battery_inverter_eur_per_kw") * 1000.0 * p_bat
        booked = {y.year: y.replacements for y in case.years if y.replacements}
        assert set(booked) == set(range(life, horizon, life)) == {10, 20}
        assert all(v == pytest.approx(inverter, rel=1e-9) for v in booked.values())
        assert "inverter_replaced_at_its_lifetime" in case.honesty_notes
        assert "no_replacement_within_horizon" not in case.honesty_notes


# ── fixed and variable O&M: the option's own assets ──────────────────────

@pytest.mark.parametrize("option", ["bess_2h", "bess_pv_2h"])
def test_fixed_om_reconciles_to_asset_economics_and_cost_breakdown(cases, option):
    from services.results.cost_breakdown import compute_cost_breakdown

    case, ledger = cases[option], sf.site_ledger()
    n, cfg = sf.solve_site_option(option)
    _r, horizon = _rate_and_horizon(ledger)
    econ = _live_econ(n, cfg)
    mine = [row for cls in ("storage_units", "generators") for row in econ[cls]
            if row["name"] in ("battery", "pv")]
    assert {r["name"] for r in mine} == ({"battery", "pv"} if option == "bess_pv_2h" else {"battery"})
    annual = sum(r["fom_cost_eur"] for r in mine)
    assert annual > 0
    operating = [y for y in case.years if y.year >= 1]
    assert len(operating) == horizon
    assert all(y.opex_fixed == pytest.approx(annual, rel=1e-9) for y in operating)
    assert sum(y.opex_fixed for y in case.years) == pytest.approx(annual * horizon, rel=1e-9)

    # BC-7 cross-check: the StorageUnit class row plus the PV carrier row.
    cb = compute_cost_breakdown(n, cfg)
    su = next(c["fom"] for c in cb["by_component"] if c["component"] == "StorageUnit")
    pv = sum(r["fom"] for r in cb["by_carrier"]
             if r["component"] == "Generator" and r["carrier"] == "solar")
    # (`cost_breakdown` reads `n.statistics()`, which rounds to five decimals.)
    assert su + pv == pytest.approx(annual, abs=1e-4)

    # …and the oracle, from the ledger rows.
    p_bat, p_pv = _sizes(option)
    expected = (_v(ledger, "battery_inverter_fom_pct_per_year") / 100.0
                * _v(ledger, "battery_inverter_eur_per_kw") * 1000.0 * p_bat)
    expected += (_v(ledger, "pv_rooftop_fom_pct_per_year") / 100.0
                 * _v(ledger, "pv_rooftop_eur_per_kw") * 1000.0 * p_pv)
    assert annual == pytest.approx(expected, rel=1e-9)


def test_variable_opex_is_the_assets_own_vom_never_the_system_opex(cases):
    from services.results.cost_breakdown import compute_cost_breakdown

    for option, case in cases.items():
        n, cfg = sf.solve_site_option(option)
        econ = _live_econ(n, cfg)
        vom = sum(row["vom_cost_eur"] for cls in ("storage_units", "generators")
                  for row in econ[cls] if row["name"] in ("battery", "pv"))
        assert all(y.opex_variable == pytest.approx(vom, abs=1e-9) for y in case.years[1:])
        # The system figure carries the grid energy bill; it must not appear.
        system = compute_cost_breakdown(n, cfg)["opex"]
        assert system > 1e5
        assert all(abs(y.opex_variable - system) > 1e4 for y in case.years[1:])


# ── flat-network path (N12) ──────────────────────────────────────────────

def test_the_flat_network_path_expands_annual_values_over_the_horizon(cases):
    n, cfg = sf.solve_site_option("bess_2h")
    econ = _live_econ(n, cfg)
    assert all(row["by_period"] == [] for row in econ["storage_units"])
    case = cases["bess_2h"]
    ops = [y for y in case.years if y.year >= 1]
    assert len({round(y.savings, 6) for y in ops}) == 1
    assert "single_year_extrapolated" in case.honesty_notes


# ── discounting and KPIs ─────────────────────────────────────────────────

@pytest.mark.parametrize("option", ["bess_2h", "bess_pv_2h"])
def test_npv_irr_and_payback_equal_the_oracle_on_the_same_vector(cases, option):
    case, ledger = cases[option], sf.site_ledger()
    rate, horizon = _rate_and_horizon(ledger)
    assert case.discount_rate == rate and case.horizon_years == horizon
    cf = [y.net_cash_flow for y in case.years]
    assert [y.year for y in case.years] == list(range(horizon + 1))
    assert case.kpis.npv == pytest.approx(oracle.npv(rate, cf), rel=1e-9, abs=1e-6)
    disc = oracle.discounted(rate, cf)
    assert [y.discounted_cash_flow for y in case.years] == pytest.approx(disc, rel=1e-9, abs=1e-6)
    cum = 0.0
    for y, d in zip(case.years, disc):
        cum += d
        assert y.cumulative_discounted == pytest.approx(cum, rel=1e-9, abs=1e-6)
    want_irr = oracle.irr(cf)
    if want_irr is None:
        assert case.kpis.irr is None and case.kpis.unavailable["irr"] == "irr_undefined"
    else:
        assert case.kpis.irr == pytest.approx(want_irr, abs=1e-7)
    for got, want, key in ((case.kpis.payback_simple, oracle.payback(cf), "payback_simple"),
                           (case.kpis.payback_discounted, oracle.payback(disc), "payback_discounted")):
        if want is None:
            assert got is None and key in case.kpis.unavailable
        else:
            assert got == pytest.approx(want, rel=1e-9)


def test_npv_is_the_lp_saving_annuitised_by_construction(cases):
    """
    One basis with the LP (BC-7): upfront CAPEX, replacements and the annuity
    salvage discount to exactly the annuities the LP charged, so the NPV is
    the LP objective's saving over the baseline times the annuity factor.
    """
    ledger = sf.site_ledger()
    rate, horizon = _rate_and_horizon(ledger)
    nb, _ = sf.solve_site_option("none")
    for option, case in cases.items():
        n, _ = sf.solve_site_option(option)
        saving = float(nb.objective) - float(n.objective)
        assert saving > 0
        assert case.kpis.npv == pytest.approx(
            saving * oracle.annuity_pv_factor(rate, horizon), rel=1e-6)
    # Gate S5 BC-S5-2: exact for EVERY option on the annuity salvage basis,
    # PV included — and so are IRR >= r and discounted payback <= horizon.
    for option, case in cases.items():
        assert "npv_nonnegative_at_optimum_by_construction" in case.honesty_notes, option
        assert "npv_nonnegative_approximate_with_pv" not in case.honesty_notes, option
        assert ("irr_and_discounted_payback_bounded_at_optimum_by_construction"
                in case.honesty_notes), option
        assert case.kpis.irr >= rate
        assert case.kpis.payback_discounted <= horizon


def test_lcos_is_on_discounted_energy(cases):
    ledger = sf.site_ledger()
    rate, horizon = _rate_and_horizon(ledger)
    case = cases["bess_2h"]
    n, cfg = sf.solve_site_option("bess_2h")
    [row] = [r for r in _live_econ(n, cfg)["storage_units"] if r["name"] == "battery"]
    energy = row["discharge_mwh"] * oracle.annuity_pv_factor(rate, horizon)
    costs = oracle.npv(rate, [y.capex + y.replacements + y.opex_fixed + y.opex_variable
                              - (y.salvage or 0.0) for y in case.years])
    assert case.kpis.levelised_cost == pytest.approx(costs / energy, rel=1e-9)


# ── market revenue at duals: reported, never in the cash flow ────────────

def test_market_revenue_at_duals_is_reported_and_excluded_from_net_cash_flow(cases):
    for option, case in cases.items():
        n, cfg = sf.solve_site_option(option)
        econ = _live_econ(n, cfg)
        [bat] = [r for r in econ["storage_units"] if r["name"] == "battery"]
        expected = bat["discharge_revenue_eur"] - bat["charge_cost_eur"]
        expected += sum(r["revenue_eur"] for r in econ["generators"] if r["name"] == "pv")
        mr = case.market_revenue_at_duals
        assert mr.engine == "lp_duals" and mr.excluded_from_net_cash_flow is True
        assert mr.annual_value == pytest.approx(expected, rel=1e-9)
        assert abs(expected) > 1.0
        for y in case.years:
            assert y.market_revenue_at_duals == (0.0 if y.year == 0 else pytest.approx(expected))
            assert y.net_cash_flow == pytest.approx(
                y.savings + (y.salvage or 0.0) - y.capex - y.replacements
                - y.opex_fixed - y.opex_variable, rel=1e-12, abs=1e-9)
        assert "market_revenue_at_duals_excluded_from_cash_flow" in case.honesty_notes


# ── salvage on the annuity basis (BC-7, gate S1 re-gate, gate S4) ────────

def test_salvage_is_the_present_value_of_the_remaining_annuities(cases):
    ledger = sf.site_ledger()
    rate, horizon = _rate_and_horizon(ledger)
    inv_life = _v(ledger, "battery_inverter_lifetime_years")
    pv_life = _v(ledger, "pv_rooftop_lifetime_years")
    for option, case in cases.items():
        p_bat, p_pv = _sizes(option)
        # The inverter bought in year 20 has 5 of its 10 years left at 25.
        last_buy = max(range(0, horizon, int(inv_life)))
        inverter = (_v(ledger, "battery_inverter_eur_per_kw") * 1000.0 * p_bat
                    * oracle.crf(rate, inv_life)
                    * oracle.annuity_pv_factor(rate, last_buy + inv_life - horizon))
        pv = (_v(ledger, "pv_rooftop_eur_per_kw") * 1000.0 * p_pv
              * oracle.crf(rate, pv_life) * oracle.annuity_pv_factor(rate, pv_life - horizon))
        assert case.salvage_basis == "annuity_pv"
        assert case.kpis.terminal_value_eur == pytest.approx(inverter + pv, rel=1e-9)
        assert case.years[-1].salvage == pytest.approx(inverter + pv, rel=1e-9)
        assert all(y.salvage == 0.0 for y in case.years[:-1])


def test_an_uncomputed_salvage_is_flagged_never_a_zero():
    n, cfg = sf.solve_site_option("bess_pv_2h")
    saved = n.generators.at["pv", "lifetime"]
    n.generators.at["pv", "lifetime"] = float("inf")
    try:
        case = _case("bess_pv_2h")
    finally:
        n.generators.at["pv", "lifetime"] = saved
    assert case.kpis.terminal_value_eur is None
    assert case.kpis.unavailable["terminal_value_eur"].startswith("salvage_not_computed")
    assert case.years[-1].salvage is None
    assert case.years[-1].unavailable["salvage"].startswith("salvage_not_computed")
    assert case.salvage_basis is None
    assert "salvage_not_computed" in case.honesty_notes
    # The NPV then books no residual value: said, not implied (gate S5 N1).
    assert "npv_excludes_uncomputed_salvage" in case.honesty_notes


def test_a_salvage_without_a_basis_or_a_null_without_a_flag_is_refused(cases):
    from pydantic import ValidationError

    from models.study import CaseKpis, InvestmentCase

    good = cases["bess_2h"].model_dump(mode="json", by_alias=True)
    InvestmentCase.model_validate(good)
    no_basis = {**good, "salvage_basis": None}
    with pytest.raises(ValidationError, match="salvage_basis"):
        InvestmentCase.model_validate(no_basis)
    kpis = {**good["kpis"], "terminal_value_eur": None}
    with pytest.raises(ValidationError, match="terminal_value_eur"):
        CaseKpis.model_validate(kpis)


# ── value streams: the bill's six components, summing to savings ────────

def test_value_streams_cover_the_bill_and_sum_to_the_savings(cases):
    for case in cases.values():
        streams = {s.key: s for s in case.value_streams}
        assert set(streams) == BILL_COMPONENTS
        assert all(s.engine == "bill_calculator" for s in streams.values())
        savings = case.years[1].savings
        assert savings > 0
        assert sum(s.annual_value for s in streams.values()) == pytest.approx(savings, rel=1e-12)
        assert sum(s.share for s in streams.values()) == pytest.approx(1.0, rel=1e-12)
        assert streams["demand"].annual_value > 0
        y1 = case.years[1]
        assert y1.savings == pytest.approx(y1.bill_baseline - y1.bill_option, rel=1e-12)


def test_a_null_bill_is_not_established_never_zero_savings():
    from services.study import tariff as T

    n, _ = sf.solve_site_option("bess_2h")
    nb, _ = sf.solve_site_option("none")
    tariff = sf.site_tariff()
    calc = T.BillCalculator()
    base = calc.bill(nb.links_t.p0["grid_import"], nb.links_t.p0["grid_export"],
                     tariff, nb.snapshot_weightings)
    null = calc.bill(None, n.links_t.p0["grid_export"], tariff, n.snapshot_weightings)
    assert null.annual_bill is None
    case = _case("bess_2h", bills={"baseline": base, "option": null})
    assert case.status == "not_established" and case.kpis is None and case.years == []
    assert case.completeness["cash_flow"] == "not_established"
    assert any(n.startswith("bill_unavailable") for n in case.honesty_notes)


# ── one currency year, refused otherwise (gate S2 BC-S2-4 carry) ─────────

def test_a_second_currency_year_is_refused_typed():
    from services.study import proforma as P

    ledger = sf.site_ledger()
    tariff = sf.site_tariff(ledger).model_copy(update={"currency_year": 2026})
    with pytest.raises(P.ProformaError) as exc:
        _case("bess_2h", tariff=tariff)
    assert exc.value.code == "currency_year_mixed"

    rows = [r.model_copy(update={"currency_year": 2023})
            if r.key == "battery_storage_eur_per_kwh" else r for r in ledger.rows]
    mixed = ledger.model_copy(update={"rows": rows})
    with pytest.raises(P.ProformaError) as exc:
        _case("bess_2h", ledger=mixed, tariff=sf.site_tariff(ledger))
    assert exc.value.code == "currency_year_mixed"

    with pytest.raises(P.ProformaError) as exc:
        _case("bess_2h", study_currency_year=2024)
    assert exc.value.code == "currency_year_mixed"


def test_the_currency_year_is_a_structured_field(cases):
    for case in cases.values():
        assert case.currency_year == 2020
        assert "currency_year_stated" in case.honesty_notes


# ── honesty notes are codes ──────────────────────────────────────────────

REQUIRED_NOTES = {
    "basis_real_pre_tax_no_subsidy", "currency_year_stated", "single_year_extrapolated",
    "perfect_foresight_dispatch", "demand_charge_perfect_foresight", "no_degradation",
    "inverter_replaced_at_its_lifetime", "duals_include_demand_charge",
    "market_revenue_at_duals_excluded_from_cash_flow",
    "battery_upfront_from_two_parts",          # U2 WP7 C1 (the S4 gap closed)
}


def test_honesty_notes_are_codes_without_digits(cases):
    for option, case in cases.items():
        assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", n) for n in case.honesty_notes), case.honesty_notes
        assert REQUIRED_NOTES <= set(case.honesty_notes), REQUIRED_NOTES - set(case.honesty_notes)
    assert "synthetic_pv_profile" in cases["bess_pv_2h"].honesty_notes
    assert "synthetic_pv_profile" not in cases["bess_2h"].honesty_notes
    # The fixture's load is an upload, not a sector profile.
    assert "synthetic_load_profile" not in cases["bess_2h"].honesty_notes


def test_the_case_is_ok_with_provenance(cases):
    for option, case in cases.items():
        assert case.status == "ok" and case.option_id == option
        assert case.engine == "cash_flow_expander" and case.fidelity == "full_study"
        assert case.basis.terms == "real" and case.basis.tax == "pre" and case.basis.subsidy == "excl"
        assert case.provenance.ledger_hash and case.provenance.library_version
        assert math.isfinite(case.kpis.npv)


# ── the KPI helpers ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cf", [
    [-100.0, 30.0, 40.0, 50.0, 20.0],
    [-1000.0, 100.0, 100.0, 1200.0],
    [-500.0, 600.0],
])
def test_the_kpi_helpers_equal_the_oracle(cf):
    from services.study import proforma as P

    assert P.npv(0.07, cf) == pytest.approx(oracle.npv(0.07, cf), rel=1e-12)
    assert P.irr(cf) == pytest.approx(oracle.irr(cf), abs=1e-9)
    assert P.payback(cf) == pytest.approx(oracle.payback(cf), rel=1e-12)


def test_irr_and_payback_are_null_without_a_sign_change():
    from services.study import proforma as P

    assert P.irr([-100.0, -10.0, -5.0]) is None
    assert P.irr([0.0, 10.0]) is None
    assert P.payback([-100.0, 10.0, 10.0]) is None


def test_a_lifetime_that_is_not_whole_years_is_refused_not_rounded():
    """Gate S2 nit: Python's round turns 12.5 into 12; the case refuses instead."""
    from services.study import proforma as P

    ledger = sf.site_ledger()
    rows = [r.model_copy(update={"value": 12.5}) if r.key == "battery_inverter_lifetime_years"
            else r for r in ledger.rows]
    with pytest.raises(P.ProformaError) as exc:
        _case("bess_2h", ledger=ledger.model_copy(update={"rows": rows}),
              tariff=sf.site_tariff(ledger))
    assert exc.value.code == "lifetime_not_whole_years"


def test_a_rate_other_than_the_lps_is_refused():
    from services.study import proforma as P

    ledger = sf.site_ledger()
    rows = [r.model_copy(update={"value": 0.05}) if r.key == "discount_rate" else r
            for r in ledger.rows]
    with pytest.raises(P.ProformaError) as exc:
        _case("bess_2h", ledger=ledger.model_copy(update={"rows": rows}),
              tariff=sf.site_tariff(ledger))
    assert exc.value.code == "discount_rate_differs_from_lp"


def test_the_baseline_has_no_case():
    from services.study import proforma as P

    with pytest.raises(P.ProformaError) as exc:
        _case("none")
    assert exc.value.code == "baseline_has_no_case"


# ── gate S5 BC-S5-3: the tariff's own honesty notes reach the case ───────

def test_the_tariffs_notes_reach_the_case_as_codes(cases):
    for option, case in cases.items():
        assert "tariff_illustrative" in case.honesty_notes, option
        assert "tariff_demand_charge_monthly_peak_not_annual" in case.honesty_notes, option


def test_a_supplied_tariffs_prose_note_is_flagged_never_dropped():
    ledger = sf.site_ledger()
    tariff = sf.site_tariff(ledger)
    prose = tariff.model_copy(update={"honesty_notes": [
        *tariff.honesty_notes, "Our 2026 supplier contract, renegotiated in May."]})
    case = _case("bess_2h", ledger=ledger, tariff=prose)
    assert "tariff_has_uncoded_notes" in case.honesty_notes
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", n) for n in case.honesty_notes)


def test_bill_lives_in_the_models_with_a_fidelity():
    from models import study as M
    from services.study import tariff as T

    assert T.Bill is M.Bill and T.BillComponents is M.BillComponents
    assert "fidelity" in M.Bill.model_fields
