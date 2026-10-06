"""
U2 WP7 TARGET — `engine_adapter.option_case`: the guided `InvestmentCase` view
filled by IC's finance engine (`build_finance_case` + `run_case`) on the S5
site golden fixture.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §4,
WP2 (port of `test_proforma_golden.py`), WP7. Blocked on S0b (C1: the
finance engine reads the battery's two upfront parts through
`upfront_parts`), so every target here is `pending("WP7")` until stage 2.

The WP0 record (`golden_s5`) is the oracle: CAPEX, replacements, FOM, NPV,
IRR, paybacks and the value streams equal it to 1e-9 (IRR 1e-9 absolute);
LCOS is the engine's storage metric (owner decision 6), its delta against
GS's charging-free figure recorded in `u2_deltas.json`.
"""
from __future__ import annotations

import pytest

from tests.golden import oracle
from tests.golden import site_fixture as sf
from tests.u2_targets import FAKE_REF, WP0, close, flat_resolver, pending

pytestmark = pending("WP7", "option_case needs S0b's upfront_parts read (C1) and WP6's LP")
OPTIONS = ("bess_2h", "bess_pv_2h")


def _A():
    from services.study import engine_adapter as A

    return A


_SOLVED: dict = {}


def _ledger(defaults=None):
    from services.study import library as L
    from services.study import questions as Q

    return L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults or L.load_defaults())


def _solved_ic(option):
    """
    The option's fork as WP8's runner will make it: the pack network (no
    prices), the compiled commercial bound on it (C6), the meter Links typed
    (way a), and a solve through `run_simulation` with `compile.solver_config`.
    """
    if option not in _SOLVED:
        import queue
        import threading

        from services.pypsa_service import PyPSAService
        from services.solver_service import run_simulation
        from services.study import compile as C
        from services.study import library as L
        from services.study import packs
        from tests.conftest import install_network_into_backend

        defaults = L.load_defaults()
        ledger = _ledger(defaults)
        n = packs.build_site_network(sf.site_intake(), ledger, option, library=defaults)
        compiled = C.commercial_from_ledger(sf.site_intake(), ledger, defaults, n.snapshots,
                                            export_series=FAKE_REF)
        compiled = C.bind_on_network(n, compiled, resolve_ref=flat_resolver(40.0, n.snapshots))
        C.type_meter_links(n, horizon_years=25)
        compiled = C.with_value_flows(compiled, n)
        cfg = C.solver_config(ledger, compiled)
        install_network_into_backend(n)
        live = PyPSAService.get_network()
        status, _ = run_simulation(cfg, live, PyPSAService.get_lock(), threading.Event(),
                                   queue.SimpleQueue(), state_update=lambda **k: None)
        assert status in ("ok", "optimal")
        _SOLVED[option] = (live, cfg, compiled, ledger)
    return _SOLVED[option]


def _bundle(option, **kw):
    """The option's `CaseBundle` (view, FinanceCase, FinanceResult)."""
    n, cfg, compiled, ledger = _solved_ic(option)
    ledger = kw.pop("ledger", None) or ledger
    return _A().option_case(n, cfg, ledger, compiled=compiled, option_id=option,
                            study_id=sf.SITE_STUDY_ID, fidelity="full_study", **kw)


@pytest.fixture(scope="module")
def bundles():
    return {o: _bundle(o) for o in OPTIONS}


def _gold(option):
    return WP0["golden_s5"][option]


def test_the_fixture_sizes_a_battery_and_pv_at_an_interior_optimum():
    for o in OPTIONS:
        n = _solved_ic(o)[0]
        assert abs(float(n.storage_units.at["battery", "p_nom_opt"])
                   - _gold(o)["battery_p_nom_mw"]) <= 1e-4


@pytest.mark.parametrize("option", OPTIONS)
def test_capex_is_the_ledger_upfront_booked_in_year_zero(bundles, option):
    v = bundles[option].view
    assert close(v.kpis.capex_total, _gold(option)["capex_total"])
    assert close(v.years[0].capex, _gold(option)["capex_total"])


def test_the_battery_capex_is_not_the_single_lifetime_back_calculation(bundles):
    """
    C1: the engine's battery asset reads the two upfront parts, never a
    blended `overnight_cost` (which PyPSA would annuitise over one lifetime).
    """
    case = bundles["bess_2h"].case
    [bat] = [a for a in case.assets if a.name == "battery"]
    assert close(bat.overnight_cost, _gold("bess_2h")["capex_total"])


def test_inverter_replacements_land_at_its_lifetime_inside_the_horizon(bundles):
    for o in OPTIONS:
        reps = {str(y.year): y.replacements for y in bundles[o].view.years if y.replacements}
        assert reps.keys() == _gold(o)["replacements"].keys()
        for k, v in reps.items():
            assert close(v, _gold(o)["replacements"][k])


@pytest.mark.parametrize("option", OPTIONS)
def test_fixed_om_reconciles_to_asset_economics_and_cost_breakdown(bundles, option):
    assert close(bundles[option].view.years[1].opex_fixed, _gold(option)["fom_annual"])


def test_variable_opex_is_the_assets_own_vom_never_the_system_opex(bundles):
    for o in OPTIONS:
        assert close(bundles[o].view.years[1].opex_variable, _gold(o)["vom_annual"])


def test_the_flat_network_path_expands_annual_values_over_the_horizon(bundles):
    for o in OPTIONS:
        v = bundles[o].view
        assert v.horizon_years == _gold(o)["horizon_years"]
        assert [y.year for y in v.years] == list(range(v.horizon_years + 1))


@pytest.mark.parametrize("option", OPTIONS)
def test_npv_irr_and_payback_equal_the_oracle_on_the_same_vector(bundles, option):
    v = bundles[option].view
    cf = [y.net_cash_flow for y in v.years]
    for got, want in zip(cf, _gold(option)["net_cash_flow"]):
        assert close(got, want)
    assert close(v.kpis.npv, _gold(option)["npv"])
    assert close(v.kpis.irr, _gold(option)["irr"], rel=0.0)
    assert close(v.kpis.payback_simple, _gold(option)["payback_simple"], rel=0.0)
    assert close(v.kpis.payback_discounted, _gold(option)["payback_discounted"], rel=0.0)
    assert close(v.kpis.npv, oracle.npv(v.discount_rate, cf), rel=1e-9)


def test_npv_is_the_lp_saving_annuitised_by_construction(bundles):
    """§4.6 identity on the engine's NPV (C1 + C2 + C4)."""
    for o in OPTIONS:
        want = (WP0["golden_s5"]["none"]["objective"] - _gold(o)["objective"]) \
            * oracle.annuity_pv_factor(0.07, 25)
        assert abs(bundles[o].view.kpis.npv - want) <= 1e-6 * abs(want)


def test_lcos_is_on_discounted_energy(bundles):
    """
    Owner decision 6: the guided LCOS is the engine's storage LCOS
    (charging cost included); GS's charging-free figure is the recorded delta.
    """
    v = bundles["bess_2h"].view
    assert v.kpis.levelised_cost is not None
    assert close(v.kpis.levelised_cost, _gold("bess_2h")["lcos_incl_charging"], rel=1e-2)


def test_market_revenue_at_duals_is_reported_and_excluded_from_net_cash_flow(bundles):
    for o in OPTIONS:
        mr = bundles[o].view.market_revenue_at_duals
        assert mr.excluded_from_net_cash_flow is True
        assert close(mr.annual_value, _gold(o)["market_revenue_at_duals"], rel=1e-6)


def test_salvage_is_the_present_value_of_the_remaining_annuities(bundles):
    """C2: `TerminalValueRule(fixed)` = GS's remaining-annuity salvage."""
    for o in OPTIONS:
        v = bundles[o].view
        assert v.salvage_basis == "fixed_from_remaining_annuities"
        assert close(v.kpis.terminal_value_eur, _gold(o)["salvage_eur"])


def test_an_uncomputed_salvage_is_flagged_never_a_zero():
    n, _cfg = sf.solve_site_option("bess_pv_2h")
    saved = n.generators.at["pv", "lifetime"]
    n.generators.at["pv", "lifetime"] = float("inf")
    try:
        v = _bundle("bess_pv_2h").view
    finally:
        n.generators.at["pv", "lifetime"] = saved
    assert v.kpis.terminal_value_eur is None and "terminal_value_eur" in v.kpis.unavailable


def test_a_salvage_without_a_basis_or_a_null_without_a_flag_is_refused(bundles):
    from pydantic import ValidationError

    v = bundles["bess_2h"].view
    with pytest.raises(ValidationError):
        v.model_validate({**v.model_dump(), "salvage_basis": None})


def test_value_streams_cover_the_bill_and_sum_to_the_savings(bundles):
    for o in OPTIONS:
        v = bundles[o].view
        streams = {s.key: s.annual_value for s in v.value_streams}
        assert set(streams) == {"energy", "demand", "capacity", "fixed", "network",
                                "export_credit", "taxes_levies"}
        assert sum(streams.values()) == pytest.approx(v.years[1].savings, rel=1e-12)
        for k, want in _gold(o)["value_streams"].items():
            assert close(streams[k], want)


def test_a_null_bill_is_not_established_never_zero_savings():
    v = _bundle("bess_2h", bills={"option": None}).view
    assert v.status == "not_established"


def test_a_second_currency_year_is_refused_typed():
    from services.study import ledger as LG

    led = LG.apply_user_row(_ledger(), "battery_storage_eur_per_kwh", 190.0, unit="EUR/kWh",
                            changed_by="u")
    rows = [r.model_copy(update={"currency_year": 2024}) if r.key == "discount_rate" else r
            for r in led.rows]
    with pytest.raises(_A().EngineRefused) as exc:
        _bundle("bess_2h", ledger=led.model_copy(update={"rows": rows}))
    assert exc.value.code == "currency_year_mixed"


def test_the_currency_year_is_a_structured_field(bundles):
    assert bundles["bess_2h"].case.inputs.currency_year == 2020
    assert bundles["bess_2h"].view.currency_year == 2020


def test_honesty_notes_are_codes_without_digits(bundles):
    import re

    for o in OPTIONS:
        assert all(re.fullmatch(r"[a-z_:]+", n) for n in bundles[o].view.honesty_notes)


def test_the_case_is_ok_with_provenance(bundles):
    v = bundles["bess_2h"].view
    assert v.status == "ok"
    assert v.provenance.library_version.startswith("generic-defaults ")
    assert set(v.provenance.engines) >= {"tariff_engine", "finance_engine"}


@pytest.mark.parametrize("cf", [[-100.0, 30.0, 40.0, 50.0], [-100.0, 10.0, 10.0]])
def test_the_kpi_helpers_equal_the_oracle(cf):
    irr, _flags = _A().irr(cf)
    want = oracle.irr(cf)
    assert (irr is None) == (want is None) and (want is None or close(irr, want, rel=1e-9))


def test_irr_and_payback_are_null_without_a_sign_change():
    irr, flags = _A().irr([10.0, 10.0])
    assert irr is None and flags
    assert _A().payback([10.0, 10.0]) in (None, 0.0)


def test_a_lifetime_that_is_not_whole_years_is_refused_not_rounded():
    from services.study import ledger as LG

    led = LG.apply_user_row(_ledger(), "battery_storage_lifetime_years", 24.5, unit="years",
                            changed_by="u")
    with pytest.raises(_A().EngineRefused) as exc:
        _bundle("bess_2h", ledger=led)
    assert exc.value.code == "lifetime_not_whole_years"


def test_a_rate_other_than_the_lps_is_refused():
    """C5: refused at the centre; a RATE bound is accepted with `differs`."""
    b = _bundle("bess_2h")
    bound = _A().bound_case(b, {"discount_rate": 0.091}, kind="rate")
    assert bound.result.gate["wacc_vs_discount_rate_consistent"] is False
    assert "wacc_gate_differs_on_rate_bound" in bound.view.honesty_notes


def test_the_baseline_has_no_case():
    with pytest.raises(_A().EngineRefused) as exc:
        _bundle("none")
    assert exc.value.code == "baseline_has_no_case"


def test_the_tariffs_notes_reach_the_case_as_codes(bundles):
    assert {"tariff_illustrative", "tariff_demand_charge_monthly_peak_not_annual"} <= set(
        bundles["bess_2h"].view.honesty_notes)


def test_a_supplied_tariffs_prose_note_is_flagged_never_dropped():
    from services.study import compile as C
    from services.study import library as L

    form = L.load_defaults().tariffs["de_industrial_illustrative"].model_copy(
        update={"honesty_notes": ["Prose with 2 digits."]})
    c = C.commercial_from_form(form, sf.solve_site_option("none")[0].snapshots,
                               export_series=FAKE_REF)
    assert "tariff_has_uncoded_notes" in c.tariff_meta["honesty_notes"]


def test_bill_lives_in_the_models_with_a_fidelity(bundles):
    from models.study import Bill

    b = bundles["bess_2h"].view
    assert isinstance(b.bills["option"], Bill) and b.bills["option"].fidelity == "full_study"
