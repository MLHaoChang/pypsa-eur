"""
U2 WP7 TARGET — `engine_adapter.option_case`: the guided `InvestmentCase` view
filled by IC's finance engine (`build_finance_case` + `run_case`) on the S5
site golden fixture.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §4,
WP2 (port of `test_proforma_golden.py`), WP7 (green since WP7: IC #90's S0b
read of the battery's two upfront parts, D9's part-lifetime replacements and
D10's remaining-life terminal value are on master).

The WP0 record (`golden_s5`) is the oracle: CAPEX, replacements, NPV, IRR,
paybacks, the salvage and the value streams equal it to 1e-9 (IRR 1e-9
absolute). Recorded deltas (`u2_deltas.json`, WP7 rows): the FOM line is
PyPSA's `n.statistics` figure, rounded to five decimals (1.3e-9 relative on
`bess_2h`); LCOS is the engine's storage metric (owner decision 6, charging
included), not GS's charging-free figure; an uncomputed salvage makes the
engine's case not established (IC C12) where GS reported an NPV without it.

WP7 adaptations of the WP2 guesses (the seam as built): the meter Links are
not typed (gate C5: IC's D11 skips them); §5.4's `terminal_value_eur` /
`levelised_cost` rename is WP8's, so the view still reads `salvage_eur` /
`lcos`; the salvage basis stays `annuity_pv` (C2 chose the engine's
`remaining_life_annuity`, which IS the PV of the remaining annuities, not a
`fixed` value); the bills travel on the `CaseBundle`.
"""
from __future__ import annotations

import json

import pytest

from tests.golden import oracle
from tests.golden import site_fixture as sf
from tests.u2_targets import FIXTURES, WP0, close, ic_case_option

OPTIONS = ("bess_2h", "bess_pv_2h")


def _A():
    from services.study import engine_adapter as A

    return A


def _ledger(defaults=None):
    from services.study import library as L
    from services.study import questions as Q

    return L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults or L.load_defaults())


def _solved_ic(option):
    """
    The option's fork as WP8's runner will make it (`u2_targets.ic_case_option`):
    the pack network (no prices, the battery's two parts, C1), the compiled
    commercial bound on it (C6) with its `single_owner` value flows, and a
    solve through `run_simulation` with `compile.solver_config`.
    """
    return ic_case_option(option)


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
    from services.study import packs

    case = bundles["bess_2h"].case
    [bat] = [a for a in case.assets if a.name == "battery"]
    assert close(bat.overnight_cost, _gold("bess_2h")["capex_total"])
    p = _gold("bess_2h")["battery_p_nom_mw"]
    up = packs.battery_upfront_eur_per_mw(_solved_ic("bess_2h")[3], 2.0)
    assert [x.name for x in bat.parts] == ["power", "energy"]
    assert close(bat.parts[0].overnight_cost, up["inverter"] * p, rel=1e-6)
    assert close(bat.parts[1].overnight_cost, up["storage"] * p, rel=1e-6)
    assert [x.lifetime_years for x in bat.parts] == [10.0, 25.0]


def test_inverter_replacements_land_at_its_lifetime_inside_the_horizon(bundles):
    for o in OPTIONS:
        reps = {str(y.year): y.replacements for y in bundles[o].view.years if y.replacements}
        assert reps.keys() == _gold(o)["replacements"].keys()
        for k, v in reps.items():
            assert close(v, _gold(o)["replacements"][k])


@pytest.mark.parametrize("option", OPTIONS)
def test_fixed_om_reconciles_to_asset_economics_and_cost_breakdown(bundles, option):
    """
    The engine's FOM line is `n.statistics.fom`, which PyPSA rounds to five
    decimals: equal to WP0 within half a unit of the fifth decimal (the
    recorded delta `fom_rounded_by_pypsa_statistics`).
    """
    assert abs(bundles[option].view.years[1].opex_fixed - _gold(option)["fom_annual"]) <= 5e-6
    rows = [r for r in json.loads((FIXTURES / "u2_deltas.json").read_text())["deltas"]
            if r.get("figure") == f"golden_s5.{option}.fom_annual"]
    assert rows and rows[0]["cause"] == "fom_rounded_by_pypsa_statistics"


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
    Owner decision 6 (Q8): the guided LCOS is the engine's storage LCOS
    (charging cost included, real basis), exactly; its delta against GS's
    charging-free pro forma figure and asset economics' charging-included one
    is recorded (`lcos_is_the_engines_storage_metric`).
    """
    for o in OPTIONS:
        b = bundles[o]
        assert b.view.kpis.lcos is not None
        assert b.view.kpis.lcos == b.result.metrics["lcos_real_per_mwh"]
        assert "lcos_includes_charging_energy_cost" in b.view.honesty_notes
        rows = [r for r in json.loads((FIXTURES / "u2_deltas.json").read_text())["deltas"]
                if r.get("figure") == f"golden_s5.{o}.lcos"]
        assert rows and rows[0]["cause"] == "lcos_is_the_engines_storage_metric"
        assert close(rows[0]["post"], b.view.kpis.lcos, rel=1e-9)


def test_market_revenue_at_duals_is_reported_and_excluded_from_net_cash_flow(bundles):
    for o in OPTIONS:
        mr = bundles[o].view.market_revenue_at_duals
        assert mr.excluded_from_net_cash_flow is True
        assert close(mr.annual_value, _gold(o)["market_revenue_at_duals"], rel=1e-6)


def test_salvage_is_the_present_value_of_the_remaining_annuities(bundles):
    """
    C2: the engine's `TerminalValueRule("remaining_life_annuity")` (IC D10) =
    GS's remaining-annuity salvage, exactly; the basis is the PV of the
    remaining annuities (`annuity_pv`).
    """
    for o in OPTIONS:
        b = bundles[o]
        assert b.case.inputs.terminal_value.method == "remaining_life_annuity"
        assert b.view.salvage_basis == "annuity_pv"
        assert close(b.view.kpis.salvage_eur, _gold(o)["salvage_eur"])
        assert close(b.view.years[-1].salvage, _gold(o)["salvage_eur"])


def test_an_uncomputed_salvage_is_flagged_never_a_zero():
    """
    A PV array with no finite lifetime has no remaining-life value: the
    engine leaves the terminal value, and with it the case, not established
    (`terminal_part_unknown`, IC C12; recorded delta: GS reported an NPV
    without the salvage) — never a zero salvage.
    """
    n = _solved_ic("bess_pv_2h")[0]
    saved = n.generators.at["pv", "lifetime"]
    n.generators.at["pv", "lifetime"] = float("inf")
    try:
        v = _bundle("bess_pv_2h").view
    finally:
        n.generators.at["pv", "lifetime"] = saved
    assert v.status == "not_established" and v.kpis is None
    assert "salvage_not_computed" in v.honesty_notes
    assert any(c.startswith("engine_reason:terminal_part_unknown") for c in v.honesty_notes)


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
    """The source's three branches: a money row, the tariff, the study."""
    import dataclasses

    led = _ledger()
    rows = [r.model_copy(update={"currency_year": 2023})
            if r.key == "battery_storage_eur_per_kwh" else r for r in led.rows]
    with pytest.raises(_A().EngineRefused) as exc:
        _bundle("bess_2h", ledger=led.model_copy(update={"rows": rows}))
    assert exc.value.code == "currency_year_mixed"

    n, cfg, compiled, ledger = _solved_ic("bess_2h")
    other = dataclasses.replace(compiled, tariff_meta={**compiled.tariff_meta,
                                                       "currency_year": 2026})
    with pytest.raises(_A().EngineRefused) as exc:
        _A().option_case(n, cfg, ledger, compiled=other, option_id="bess_2h",
                         study_id=sf.SITE_STUDY_ID)
    assert exc.value.code == "currency_year_mixed"

    with pytest.raises(_A().EngineRefused) as exc:
        _bundle("bess_2h", study_currency_year=2024)
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
    """
    The case carries `tariff_has_uncoded_notes` for a tariff's prose note
    (the compile half is green in `test_study_compile_commercial.py`).
    """
    import dataclasses

    n, cfg, compiled, ledger = _solved_ic("bess_2h")
    meta = {**compiled.tariff_meta,
            "honesty_notes": [*compiled.tariff_meta["honesty_notes"], "tariff_has_uncoded_notes"]}
    view = _A().option_case(n, cfg, ledger, compiled=dataclasses.replace(compiled, tariff_meta=meta),
                            option_id="bess_2h", study_id=sf.SITE_STUDY_ID,
                            fidelity="full_study").view
    assert "tariff_has_uncoded_notes" in view.honesty_notes


def test_bill_lives_in_the_models_with_a_fidelity(bundles):
    from models.study import Bill

    b = bundles["bess_2h"]
    assert isinstance(b.bills["option"], Bill) and b.bills["option"].fidelity == "full_study"
    assert b.bills["option"].engine == "tariff_engine"
