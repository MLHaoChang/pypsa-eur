"""
U2 WP7 — the guided case on the Investment Case engine: `compile.finance_from_ledger`,
`engine_adapter.option_case` and `engine_adapter.bound_case` (C1, C2, C4, C5).

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §1.2
(rows 21-34), §2 C6, §4.2, §4.4, §4.5, §4.6, §4.9, WP7; the parent plan's C5
as amended at the IC S0b gate (rate bounds move the valuation basis too).
The WP2 port of the pro forma's golden tests is
`test_engine_adapter_case_golden.py`; this file holds WP7's own acceptance:

* C1 — the pack writes the battery as two upfront parts (`derive.apply_parts`):
  the derived `capital_cost` is today's two-annuity value to 1e-9, the parts
  sum to `packs.battery_upfront_eur_per_mw`, `overnight_cost` stays empty, and
  the LP is unchanged (the WP0 objective and size, recorded from a battery
  priced by `capital_cost` alone).
* C4 — the real pre-tax basis: escalation 0, inflation None, the WACC and
  the cost of equity = the real rate; the WACC gate is consistent at the
  centre; post-tax KPIs are None with the engine's reasons; a null row is the
  engine's reason, never a 0.
* C2 + §4.6 — NPV = (LP objective of `none` − of the option) × AF(r, H) on the
  live engine-solved objectives, to 1e-6.
* C5 — CAPEX and RATE bounds derived from the ONE `FinanceCase` equal GS's
  `_capex_bound` / `_rate_bound` (the pro forma on the variant ledger) and
  read no network; a rate bound's WACC gate reads `differs` (accepted).
* Gate C5 — the meter Links are IC's D11 non-investments.
"""
from __future__ import annotations

import dataclasses
import math

import pytest

from tests.golden import oracle
from tests.golden import site_fixture as sf
from tests.u2_targets import WP0, close, ic_case_option

OPTIONS = ("bess_2h", "bess_pv_2h")
GOLDEN = WP0["golden_s5"]


def _A():
    from services.study import engine_adapter as A

    return A


def _C():
    from services.study import compile as C

    return C


def _defaults():
    from services.study import library as L

    return L.load_defaults()


def _pack_ledger():
    from services.study import library as L
    from services.study import questions as Q

    return L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), _defaults())


_BUNDLES: dict = {}


def _bundle(option: str):
    if option not in _BUNDLES:
        n, cfg, compiled, ledger = ic_case_option(option)
        _BUNDLES[option] = _A().option_case(n, cfg, ledger, compiled=compiled,
                                            option_id=option, study_id=sf.SITE_STUDY_ID,
                                            fidelity="full_study")
    return _BUNDLES[option]


def _with_row(ledger, key, value):
    """The ledger with one row's value replaced (validation bypassed: a null row)."""
    rows = [r.model_copy(update={"value": value}) if r.key == key else r for r in ledger.rows]
    return ledger.model_copy(update={"rows": rows})


# ── C1: the battery's two upfront parts ───────────────────────────────────

@pytest.mark.parametrize("option,hours", [("bess_1h", 1.0), ("bess_2h", 2.0), ("bess_4h", 4.0),
                                          ("bess_pv_2h", 2.0)])
def test_the_pack_writes_the_battery_as_two_upfront_parts(option, hours):
    """
    C1 (§4.5): `packs.build_site_network` writes the battery through
    `derive.apply_parts` with the ledger's inverter and storage rows: the
    derived `capital_cost` equals today's two-annuity value to 1e-9, the FOM
    is the inverter's, `overnight_cost` stays empty (the LP is unchanged), and
    `upfront_parts` returns exactly the ledger's two parts, which sum to
    `packs.battery_upfront_eur_per_mw`.
    """
    from services.asset_schema import access
    from services.study import packs

    led = _pack_ledger()
    v = packs.ledger_values(led)
    n = packs.build_site_network(sf.site_intake(), led, option, library=_defaults())
    row = n.storage_units.loc["battery"]
    want = packs.battery_capital_cost_eur_per_mw(led, hours)
    assert abs(row["capital_cost"] - want) <= 1e-9 * want
    assert row["fom_cost"] == pytest.approx(packs.battery_fom_eur_per_mw(led), rel=1e-12)
    assert math.isnan(row["overnight_cost"]) and row["lifetime"] == 25.0
    parts = access.upfront_parts(n, "StorageUnit", "battery")
    up = packs.battery_upfront_eur_per_mw(led, hours)
    assert [p.name for p in parts] == ["power", "energy"]
    assert not any(p.derived_from_capital_cost for p in parts)
    assert parts[0].upfront_per_unit == pytest.approx(up["inverter"], rel=1e-12)
    assert parts[1].upfront_per_unit == pytest.approx(up["storage"], rel=1e-12)
    assert sum(p.upfront_per_unit for p in parts) == pytest.approx(up["total"], rel=1e-12)
    assert [p.lifetime for p in parts] == [v["battery_inverter_lifetime_years"],
                                           v["battery_storage_lifetime_years"]]
    assert parts[0].fom_share == pytest.approx(v["battery_inverter_fom_pct_per_year"] / 100.0)
    assert n.meta[packs.PACK_META_KEY]["cost_basis"]["battery"] == "two_upfront_parts"


@pytest.mark.live_solve
@pytest.mark.parametrize("option", OPTIONS)
def test_the_parts_leave_the_lp_identical(option):
    """
    C1 LP identity: the option solved with the battery's parts (the solve-time
    fill re-derives `capital_cost` from them) has WP0's objective and sizes,
    which were recorded from a battery priced by `capital_cost` alone: to
    1e-12 relative and 1e-9 MW. Mutation: a typed `overnight_cost` on the
    StorageUnit (PyPSA then annuitises it over ONE lifetime) turns this red.
    """
    n = ic_case_option(option)[0]
    assert abs(float(n.objective) - GOLDEN[option]["objective"]) \
        <= 1e-12 * GOLDEN[option]["objective"]
    assert abs(float(n.storage_units.at["battery", "p_nom_opt"])
               - GOLDEN[option]["battery_p_nom_mw"]) <= 1e-9
    if GOLDEN[option]["pv_p_nom_mw"] is not None:
        assert abs(float(n.generators.at["pv", "p_nom_opt"])
                   - GOLDEN[option]["pv_p_nom_mw"]) <= 1e-9


# ── C4: the real pre-tax basis; rows 21-34 ────────────────────────────────

def _compile(ledger, owned=("battery", "grid_import", "grid_export"), **kw):
    return _C().finance_from_ledger(ledger, model_year=sf.SITE_YEAR, owned_assets=owned,
                                    tariff_meta={"currency": "EUR", "currency_year": 2020,
                                                 "tariff_id": "t"}, **kw)


def test_the_finance_inputs_are_the_real_pre_tax_basis_from_rows_21_to_34():
    """
    C4 (§4.4) and rows 21-32: real (escalation 0 in all six classes,
    inflation None, `price_basis="real"`), the WACC and the cost of equity =
    the ledger's real rate, pre-tax (no tax pack), no subsidy, no debt;
    financial close one year before the model year, `capex_phasing [1.0]`,
    every owner asset's COD in the model year, `analysis_years` = the storage
    lifetime; C1 `part_lifetimes` replacements (no hand-booked capex), C2 the
    engine's `remaining_life_annuity`; PV degradation from row 30, the
    battery's 0 (rows 14-15 unused).
    """
    from datetime import date

    from models.finance import ESCALATION_CLASSES

    fin = _compile(_pack_ledger(), owned=("pv", "battery")).inputs
    assert fin.price_basis == "real" and fin.currency == "EUR" and fin.currency_year == 2020
    assert fin.escalation == {c: 0.0 for c in ESCALATION_CLASSES}
    assert fin.inflation is None
    assert fin.wacc_nominal == fin.cost_of_equity == 0.07
    assert fin.tax_pack_id is None and fin.incentives == [] and fin.debt == []
    assert fin.financial_close == date(sf.SITE_YEAR - 1, 1, 1) and fin.capex_phasing == [1.0]
    assert fin.cod_by_asset == {"pv": date(sf.SITE_YEAR, 1, 1),
                                "battery": date(sf.SITE_YEAR, 1, 1)}
    assert fin.analysis_years == 25 and fin.contingency_share == 0.0
    assert fin.replacement_rule == "part_lifetimes" and fin.replacement_capex == []
    assert fin.terminal_value.method == "remaining_life_annuity"
    assert fin.terminal_value.value is None
    assert fin.degradation_by_asset == {"pv": 0.0, "battery": 0.0}
    assert fin.annualise is False


def test_a_legacy_ledger_takes_the_guided_rules_disclosed():
    """
    A ledger seeded before rows 21-34 (the legacy `study_library` ledger,
    production until WP8) compiles to the same inputs and says so
    (`finance_rules_from_guided_defaults`), never silently.
    """
    from services.study import library as L
    from services.study import questions as Q

    legacy = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), L.load_library())
    assert not any(r.key == "contingency_share" for r in legacy.rows)
    got = _compile(legacy)
    want = _compile(_pack_ledger())
    assert got.inputs == want.inputs
    assert got.notes == ("finance_rules_from_guided_defaults",)
    assert want.notes == ()


def test_the_compile_refuses_typed():
    led = _pack_ledger()
    with pytest.raises(_C().CompileError) as exc:
        _compile(_with_row(led, "battery_inverter_lifetime_years", 9.5))
    assert exc.value.code == "lifetime_not_whole_years"
    with pytest.raises(_C().CompileError) as exc:
        _compile(led, study_currency_year=2024)
    assert exc.value.code == "currency_year_mixed"


@pytest.mark.live_solve
@pytest.mark.parametrize("option", OPTIONS)
def test_the_centre_is_on_the_real_basis_and_equals_wp0(option):
    """
    C4 mutation target (`escalation_tariff = 0.02` → red): on the real basis
    the engine's NPV is WP0's to 1e-9, every escalation class is 0, the
    engine sees no `real_basis_with_escalation` and no `inflation`.
    """
    b = _bundle(option)
    assert set(b.case.inputs.escalation.values()) == {0.0}
    assert b.case.inputs.inflation is None
    assert not [f for f in b.result.flags if f.startswith("real_basis_with_")]
    assert close(b.view.kpis.npv, GOLDEN[option]["npv"])
    assert close(b.view.kpis.irr, GOLDEN[option]["irr"], rel=0.0)


@pytest.mark.live_solve
def test_the_wacc_gate_is_consistent_at_the_centre_and_post_tax_kpis_say_why():
    """
    §4.4: at the centre the WACC gate is consistent (discount rate and asset
    rates `ok`, inflation `n/a`: `auto_discount_periods` off); the post-tax
    metrics are None, the tax section's reasons name the missing pack.
    """
    b = _bundle("bess_2h")
    assert b.result.gate["wacc_vs_discount_rate_consistent"] is True
    assert b.result.gate["legs"] == {"discount_rate": "ok", "asset_rates": "ok",
                                     "inflation": "n/a"}
    for k in ("project_post_tax_npv", "equity_post_tax_npv", "project_post_tax_irr"):
        assert b.result.metrics[k] is None
    assert b.result.sections["tax"] == "not_established"
    assert "tax_pack_missing" in b.result.reasons["tax"]
    assert "wacc_field_holds_the_real_rate" in b.view.honesty_notes


@pytest.mark.live_solve
def test_the_meter_links_are_d11_non_investments():
    """
    Gate C5: the meter Links `single_owner` gives the site are skipped by
    IC's D11 (`meter_link_not_investment:*`): the case's only asset is the
    battery, no capex or COD for the Links, no lifetime flag, the gate is
    unaffected; the view discloses it.
    """
    b = _bundle("bess_2h")
    assert [a.name for a in b.case.assets] == ["battery"]
    assert {"meter_link_not_investment:grid_import",
            "meter_link_not_investment:grid_export"} <= set(b.case.flags)
    assert not [f for f in b.result.flags if f.startswith("asset_lifetime_unknown")]
    assert "meter_links_are_not_investments" in b.view.honesty_notes


@pytest.mark.live_solve
@pytest.mark.parametrize("key,code", [("contingency_share", "contingency_share_missing"),
                                      ("escalation_tariff", "escalation_missing:tariff")])
def test_a_null_finance_row_is_the_engines_reason_never_a_zero(key, code):
    """
    Rows 23 and 24: a null row compiles to a missing input and the engine's
    reason makes the case not established (never a 0 contingency or
    escalation).
    """
    n, cfg, compiled, ledger = ic_case_option("bess_2h")
    view = _A().option_case(n, cfg, _with_row(ledger, key, None), compiled=compiled,
                            option_id="bess_2h", study_id=sf.SITE_STUDY_ID).view
    assert view.status == "not_established" and view.kpis is None
    # The engine names the class and the line (`escalation_missing:tariff:bill:energy`).
    assert any(c == f"engine_reason:{code}" or c.startswith(f"engine_reason:{code}:")
               for c in view.honesty_notes), view.honesty_notes


# ── §4.6: NPV = LP saving × AF(r, H), on the live objectives ──────────────

@pytest.mark.live_solve
@pytest.mark.parametrize("option", OPTIONS)
def test_npv_is_the_live_lp_saving_annuitised(option):
    """
    §4.6 (C1 + C2 + C4): the engine's NPV equals (objective of the
    engine-solved `none` − the option's) × AF(0.07, 25) to 1e-6 relative, so
    the BY_CONSTRUCTION codes stand at the centre. Mutation: `salvage_rule →
    none` (no terminal value) turns this red.
    """
    n_none = ic_case_option("none")[0]
    n = ic_case_option(option)[0]
    want = (float(n_none.objective) - float(n.objective)) * oracle.annuity_pv_factor(0.07, 25)
    got = _bundle(option).view.kpis.npv
    assert abs(got - want) <= 1e-6 * abs(want)
    assert set(_A().BY_CONSTRUCTION) <= set(_bundle(option).view.honesty_notes)


# ── C5: CAPEX and RATE bounds from the one FinanceCase ────────────────────

def _gs_bound_npv(option: str, key: str, value: float, kind: str) -> float:
    """
    GS's `_capex_bound` / `_rate_bound` (the pro forma on the variant ledger,
    the network copy's FOM or rates rewritten as `findings` does), the WP0
    oracle of C5.
    """
    from services.study import findings as F
    from services.study import packs, proforma

    n, cfg, _compiled, ledger = ic_case_option(option)
    vl = F._with_value(ledger, key, value)
    net = F.copy_network(n)
    if kind == "capex":
        net.storage_units.loc["battery", "fom_cost"] = packs.battery_fom_eur_per_mw(vl)
    else:
        net.storage_units.loc["battery", "discount_rate"] = float(value)
        if "pv" in net.generators.index:
            net.generators.loc["pv", "discount_rate"] = float(value)
    b = _bundle(option)
    tariff = packs.effective_tariff(sf.site_intake(), vl, _defaults(), n.snapshots)
    cfg_v = dataclasses.replace(cfg, discount_rate=float(packs.ledger_values(vl)["discount_rate"]))
    case = proforma.build_investment_case(
        net, cfg_v, None, vl, dict(b.bills), option, study_id=sf.SITE_STUDY_ID,
        tariff=tariff)
    return float(case.kpis.npv)


def _bounds(kind):
    from services.study import findings as F

    rows = F.CAPEX_ROWS if kind == "capex" else F.RATE_ROWS
    led = _pack_ledger()
    out = []
    for key in rows:
        row = next(r for r in led.rows if r.key == key)
        lo, hi, _ = F.bounds_for(row)
        out += [(key, lo), (key, hi)]
    return out


@pytest.mark.live_solve
@pytest.mark.parametrize("option", OPTIONS)
@pytest.mark.parametrize("kind", ["capex", "rate"])
def test_the_bounds_equal_gs_and_read_no_network(option, kind, monkeypatch):
    """
    C5 (§4.9, the parent's C5 as amended): every CAPEX bound (each battery
    cost row at its low and high, moving its part, the FOM with the
    inverter, replacements and terminal value following) and the RATE bound
    (WACC, cost of equity and the valuation basis) equal GS's bound NPV to
    1e-9 relative; nothing re-reads the network (the value-flow ledger and
    `build_finance_case` are not reached).
    """
    import services.results.finance_case as FC
    import services.results.value_flows as VF

    b = _bundle(option)
    want = {(k, v): _gs_bound_npv(option, k, v, kind) for k, v in _bounds(kind)}

    def boom(*_a, **_k):
        raise AssertionError("a bound re-read the network")

    monkeypatch.setattr(FC, "build_finance_case", boom)
    monkeypatch.setattr(VF, "value_flow_ledger", boom)
    for (key, value), gs in want.items():
        bound = _A().bound_case(b, {key: value}, kind=kind)
        assert bound.view.status == "ok"
        assert close(bound.view.kpis.npv, gs), (key, value, bound.view.kpis.npv, gs)
        assert not set(_A().BY_CONSTRUCTION) & set(bound.view.honesty_notes)
        if kind == "rate":
            assert bound.result.gate["wacc_vs_discount_rate_consistent"] is False
            assert "wacc_gate_differs_on_rate_bound" in bound.view.honesty_notes
        else:
            assert bound.result.gate["wacc_vs_discount_rate_consistent"] is True


@pytest.mark.live_solve
def test_a_capex_bound_moves_only_its_part():
    """
    C5 WORKAROUND (engine ask: `scale_capex` by asset and part): the
    inverter row moves the power part and the FOM, the storage row the energy
    part only; the PV asset never moves.
    """
    b = _bundle("bess_pv_2h")
    centre = {a.name: a for a in b.case.assets}
    inv = _A().bound_case(b, {"battery_inverter_eur_per_kw": 300.0}, kind="capex")
    bat = {a.name: a for a in inv.case.assets}
    v0 = b.context["centre_values"]["battery_inverter_eur_per_kw"]
    assert close(bat["battery"].parts[0].overnight_cost,
                 centre["battery"].parts[0].overnight_cost * 300.0 / v0)
    assert bat["battery"].parts[1] == centre["battery"].parts[1]
    assert bat["pv"] == centre["pv"]
    sto = _A().bound_case(b, {"battery_storage_eur_per_kwh": 100.0}, kind="capex")
    bat_s = {a.name: a for a in sto.case.assets}
    assert bat_s["battery"].parts[0] == centre["battery"].parts[0]
    assert bat_s["pv"] == centre["pv"]
    bat_fom = -next(ln.amount for t in b.case.templates for ln in t.lines
                    if ln.key == "asset:fom:StorageUnit:battery")
    fom = [v.years[1].opex_fixed for v in (b.view, inv.view, sto.view)]
    assert fom[2] == pytest.approx(fom[0], rel=1e-12)
    assert fom[1] - fom[0] == pytest.approx(bat_fom * (300.0 / v0 - 1.0), rel=1e-9)


def test_a_price_row_is_not_a_bound_case():
    """A price bound re-dispatches (`option_case` on the variant network)."""
    with pytest.raises(_A().EngineRefused):
        _A().bound_case(_A().CaseBundle(view=None, case=object()), {"x": 1.0}, kind="price")
