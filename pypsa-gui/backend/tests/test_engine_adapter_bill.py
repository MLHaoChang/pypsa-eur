"""
U2 WP5 — `engine_adapter.bill` / `bill_meter`: GS's `Bill` view filled by IC's
tariff engine (`billing.bill_site` / `rate_meter`) and the value-flow export
line.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §3.2,
WP2 (port of `test_tariff_bill.py`'s bill expectations), WP5.

Every component of both seed tariffs equals the WP0 frozen record
(`tests/fixtures/u2_pre_numbers.json`, `seed_bills`, billed by GS's
`BillCalculator` on the golden dispatch) to 1e-9 relative. Where IC's
semantics differ from GS's (a partial month's fixed charge, a contracted
capacity without a stated limit, a year-billed peak) the test asserts IC's
figure and the delta is recorded in `tests/fixtures/u2_deltas.json`.
"""
from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.study import Tariff
from tests.golden import site_fixture as SF
from tests.u2_targets import DE, FAKE_REF, SEEDS, SIX, WP0, close, flat_resolver, golden_copy

JAN_FEB = pd.date_range("2030-01-01", "2030-02-28 23:00", freq="h")
SEVEN = (*SIX, "taxes_levies")



def _A():
    from services.study import engine_adapter as A

    return A


def _C():
    from services.study import compile as C

    return C


def _form(**over) -> Tariff:
    base = dict(
        tariff_id="t", name="toy", source="illustrative", currency="EUR",
        currency_year=2026, billing_period="month",
        energy_bands=[{"label": "flat", "price_per_mwh": 100.0, "applies": {}}],
        demand_charge=None, fixed_charge_per_period=0.0, network_charges=[],
        # A stated export price (gate U2-S1 C1: an unpriced export is refused).
        export={"price_per_mwh": 0.0, "series_ref": None, "cap_mw": None},
    )
    base.update(over)
    return Tariff.model_validate(base)


def _net(idx=JAN_FEB, *, p_nom: float = 10.0, weight: float | None = None) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(idx)
    if weight is not None:
        n.snapshot_weightings.loc[:, :] = weight
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=p_nom)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=p_nom)
    return n


def _meter(form, imp, exp, idx=JAN_FEB, *, connection_mw=None, weight=None, p_nom=10.0):
    n = _net(idx, p_nom=p_nom, weight=weight)
    compiled = _C().commercial_from_form(form, idx, connection_mw=connection_mw)
    return _A().bill_meter(n, compiled, imp, exp)


def _dispatched(form, imp, exp, idx=JAN_FEB, *, export_series=None):
    """
    A network carrying the given flows as its dispatch (not solved by the
    engine) with the compiled config bound: `bill` reads the export line.
    """
    n = _net(idx)
    n.links_t.p0 = pd.DataFrame({"grid_import": imp, "grid_export": exp}, index=idx)
    compiled = _C().commercial_from_form(form, idx, export_series=FAKE_REF)
    price = export_series if export_series is not None else pd.Series(
        float(form.export.price_per_mwh or 0.0), index=idx)
    compiled = _C().bind_on_network(n, compiled, resolve_ref=lambda _r: price)
    return n, compiled


def _ones(v=1.0, idx=JAN_FEB):
    return pd.Series(v, index=idx)


# ── the shape ─────────────────────────────────────────────────────────────

def test_a_bill_is_the_tariff_engines_with_seven_components():
    bill = _meter(_form(), _ones(2.0), _ones(0.0))
    assert bill.engine == "tariff_engine"
    assert tuple(type(bill.by_component)._figure_fields) == SEVEN
    assert [k for k, _ in _A().BILL_COMPONENTS] == list(SEVEN)
    assert bill.by_component.taxes_levies == 0.0
    assert bill.currency_year == 2026 and bill.currency == "EUR"
    assert bill.billing_periods == ["2030-01", "2030-02"]


# ── energy bands (ports) ──────────────────────────────────────────────────

def test_flat_band_prices_every_hour_at_one_price():
    bill = _meter(_form(), _ones(2.0), _ones(0.0))
    assert bill.by_component.energy == pytest.approx(100.0 * 2.0 * len(JAN_FEB))


def test_two_band_time_of_use():
    t = _form(energy_bands=[
        {"label": "peak", "price_per_mwh": 180.0,
         "applies": {"weekdays": [0, 1, 2, 3, 4], "hours": list(range(8, 20))}},
        {"label": "off-peak", "price_per_mwh": 95.0, "applies": {}}])
    peak = (JAN_FEB.weekday < 5) & (JAN_FEB.hour >= 8) & (JAN_FEB.hour < 20)
    bill = _meter(t, _ones(), _ones(0.0))
    assert bill.by_component.energy == pytest.approx(180.0 * peak.sum() + 95.0 * (~peak).sum())


# ── demand, fixed, network (ports) ────────────────────────────────────────

def test_monthly_demand_charge_charges_each_billing_periods_peak():
    t = _form(demand_charge={"price_per_mw_per_period": 9500.0, "basis": "billing_period_peak"})
    imp = _ones(3.0)
    imp.loc["2030-01-15 18:00"] = 10.0
    imp.loc["2030-02-03 09:00"] = 7.0
    bill = _meter(t, imp, _ones(0.0))
    assert bill.peak_mw_by_billing_period == pytest.approx({"2030-01": 10.0, "2030-02": 7.0})
    assert bill.by_component.demand == pytest.approx(9500.0 * (10.0 + 7.0))


def test_a_year_billed_peak_is_prorated_by_the_hours_it_represents():
    """
    Port of `test_yearly_billing_period_charges_the_one_peak` — DELTA: IC's
    annual measured peak (`capacity` on `peak_import`) is €/kW-year pro-rated
    by represented hours / 8760 (GS charged the whole year's price on two
    months); equal on a full year. Peaks by month are not reported for it.
    """
    t = _form(billing_period="year", demand_charge={"price_per_mw_per_period": 9500.0})
    imp = _ones(3.0)
    imp.loc["2030-01-15 18:00"] = 10.0
    bill = _meter(t, imp, _ones(0.0))
    assert bill.by_component.demand == pytest.approx(9500.0 * 10.0 * len(JAN_FEB) / 8760.0)
    assert "capacity_charge_prorated_by_hours" in bill.honesty_notes
    assert bill.billing_periods == ["2030"]


def test_no_demand_charge_in_the_tariff_is_a_real_zero():
    bill = _meter(_form(), _ones(3.0), _ones(0.0))
    assert bill.by_component.demand == 0.0
    assert "demand" not in bill.by_component.unavailable


def test_fixed_and_per_mwh_network_charges():
    t = _form(fixed_charge_per_period=120.0,
              network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"},
                               {"label": "meter", "price": 10.0, "basis": "per_period"}])
    bill = _meter(t, _ones(2.0), _ones(0.0))
    assert bill.by_component.fixed == pytest.approx(2 * 120.0)
    assert bill.by_component.network == pytest.approx(4.0 * 2.0 * len(JAN_FEB) + 2 * 10.0)


def test_snapshot_weightings_scale_energy_not_peaks():
    idx = pd.date_range("2030-01-01", periods=24 * 31 // 3, freq="3h")
    t = _form(demand_charge={"price_per_mw_per_period": 100.0})
    bill = _meter(t, _ones(2.0, idx), _ones(0.0, idx), idx, weight=3.0)
    assert bill.by_component.energy == pytest.approx(100.0 * 2.0 * 3.0 * len(idx))
    assert bill.by_component.demand == pytest.approx(100.0 * 2.0)


def test_a_contracted_capacity_charge_is_priced_on_the_connection():
    t = _form(capacity_charge={"price_per_mw_per_year": 8760.0, "basis": "contracted"},
              connection_limit_mw=12.0)
    bill = _meter(t, _ones(3.0), _ones(0.0), connection_mw=12.0, p_nom=12.0)
    assert bill.by_component.capacity == pytest.approx(12.0 * len(JAN_FEB))
    assert "capacity_charge_prorated_by_hours" in bill.honesty_notes


def test_a_contracted_capacity_without_a_stated_limit_rates_on_the_poc_size():
    """
    Port of `test_contracted_capacity_charge_without_a_limit_is_null_with_a_flag`
    — DELTA: IC rates contracted capacity on the PoC Link's size (the
    connection), so the component is established where GS's was null.
    """
    t = _form(capacity_charge={"price_per_mw_per_year": 8760.0, "basis": "contracted"})
    bill = _meter(t, _ones(3.0), _ones(0.0), p_nom=10.0)
    assert bill.by_component.capacity == pytest.approx(10.0 * len(JAN_FEB))
    assert bill.total is not None


# ── export (ports) ────────────────────────────────────────────────────────

def test_export_credit_is_negative_and_uses_the_price():
    exp = _ones(0.0)
    exp.iloc[:10] = 5.0
    n, c = _dispatched(_form(export={"price_per_mwh": 40.0}), _ones(0.0), exp)
    bill = _A().bill(n, c)
    assert bill.by_component.export_credit == pytest.approx(-40.0 * 50.0)


def test_a_market_indexed_export_series_is_the_credit():
    """
    Port of `test_export_series_ref_resolves_through_the_calculators_series`:
    the series reaches the network through binding (`ic_export_price`).
    """
    spot = pd.Series(np.linspace(10.0, 70.0, len(JAN_FEB)), index=JAN_FEB)
    n, c = _dispatched(_form(export={"series_ref": "spot"}), _ones(0.0), _ones(1.0),
                       export_series=spot)
    assert _A().bill(n, c).by_component.export_credit == pytest.approx(-float(spot.sum()))


def test_export_credit_with_nothing_exported_is_positive_zero():
    bill = _meter(_form(export={"price_per_mwh": 40.0}), _ones(1.0), _ones(0.0))
    assert math.copysign(1.0, bill.by_component.export_credit) == 1.0
    assert '"export_credit":0.0' in json.dumps(bill.by_component.model_dump(),
                                               separators=(",", ":"))


# ── ADR-0001: absent series → null with a flag (ports) ────────────────────

def test_absent_import_series_nulls_the_import_components_with_a_flag():
    t = _form(demand_charge={"price_per_mw_per_period": 100.0},
              network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"}],
              export={"price_per_mwh": 40.0})
    bill = _meter(t, None, _ones(0.0))
    c = bill.by_component
    assert c.energy is None and c.unavailable["energy"] == "no_import_series"
    assert c.demand is None and c.unavailable["demand"] == "no_import_series"
    assert c.network is None and c.unavailable["network"] == "no_import_series"
    assert c.export_credit == 0.0
    assert bill.total is None and bill.unavailable["total"] == "component_unavailable"
    assert bill.peak_mw_by_billing_period == {}


def test_absent_export_series_nulls_the_export_credit_with_a_flag():
    bill = _meter(_form(export={"price_per_mwh": 40.0}), _ones(1.0), None)
    assert bill.by_component.export_credit is None
    assert bill.by_component.unavailable["export_credit"] == "no_export_series"
    assert bill.by_component.energy == pytest.approx(100.0 * len(JAN_FEB))


def test_a_metered_export_is_not_priced_without_a_dispatch():
    """
    `bill_meter` rates a meter series without a solve; the export line is
    the value-flow ledger's, which needs the dispatch on the network — so a
    non-zero export there is not established (DELTA vs GS, which priced it).
    """
    bill = _meter(_form(export={"price_per_mwh": 40.0}), _ones(0.0), _ones(1.0))
    assert bill.by_component.export_credit is None
    assert bill.by_component.unavailable["export_credit"] == "export_line_needs_the_dispatch"


def test_annual_bill_only_for_a_one_year_horizon():
    two = _meter(_form(), _ones(1.0), _ones(0.0))
    assert two.total == pytest.approx(100.0 * len(JAN_FEB))
    assert two.annual_bill is None and two.unavailable["annual_bill"] == "horizon_not_one_year"
    year = pd.date_range("2030-01-01", periods=8760, freq="h")
    full = _meter(_form(), _ones(1.0, year), _ones(0.0, year), year)
    assert full.annual_bill == pytest.approx(full.total) and full.total == pytest.approx(876000.0)


# ── honesty notes (ports) ─────────────────────────────────────────────────

def test_honesty_notes_are_codes_without_digits_and_partial_periods_are_named():
    """
    Port of `test_honesty_notes_are_codes_without_digits` — DELTA: IC
    pro-rates a partial month's fixed charge by its covered hours (GS charged
    it in full, `partial_billing_period_charged_in_full`).
    """
    t = _form(fixed_charge_per_period=100.0, demand_charge={"price_per_mw_per_period": 10.0},
              capacity_charge={"price_per_mw_per_year": 1.0, "basis": "contracted"},
              connection_limit_mw=1.0)
    part = pd.date_range("2030-01-15", "2030-02-10 23:00", freq="h")
    bill = _meter(t, _ones(1.0, part), _ones(0.0, part), part, connection_mw=1.0, p_nom=1.0)
    assert {"fixed_charge_prorated_on_partial_period", "capacity_charge_prorated_by_hours",
            "demand_on_partial_month"} <= set(bill.honesty_notes)
    assert all(re.fullmatch(r"[a-z_]+", note) for note in bill.honesty_notes)
    assert bill.partial_billing_periods == ["2030-01", "2030-02"]
    hours = {"2030-01": 17 * 24, "2030-02": 10 * 24}
    assert bill.by_component.fixed == pytest.approx(100.0 * (hours["2030-01"] / 744.0
                                                             + hours["2030-02"] / 672.0))


def test_fully_covered_billing_periods_carry_no_partial_period_note():
    t = _form(fixed_charge_per_period=100.0, demand_charge={"price_per_mw_per_period": 10.0})
    bill = _meter(t, _ones(1.0), _ones(0.0))
    assert bill.honesty_notes == ()
    assert bill.partial_billing_periods == []


# ── decision 7: the seventh component; unmapped and unrated items ─────────

def _with_items(compiled, *items):
    """
    An Expert's items added to the compiled tariff (ids the compiler never
    assigns, so the adapter maps them by kind).
    """
    import dataclasses

    from models.commercial import TariffItem

    cfg = compiled.config.model_copy(deep=True)
    cfg.import_tariff.items.extend(TariffItem.model_validate(i) for i in items)
    return dataclasses.replace(compiled, config=cfg)


def test_tax_and_levy_items_are_the_seventh_component():
    """
    Owner decision 7: `tax_levy` / `certificate` items (an Expert's German
    levy) are "Taxes & levies"; a `network:*` id stays `network`.
    """
    n = _net()
    c = _with_items(_C().commercial_from_form(_form(), JAN_FEB),
                    {"id": "levy", "kind": "tax_levy", "unit": "per_kwh", "settlement": "h",
                     "periods": [{"name": "all", "rate": 0.03}]},
                    {"id": "goo", "kind": "certificate", "unit": "per_kwh", "settlement": "h",
                     "periods": [{"name": "all", "rate": 0.001}]},
                    {"id": "network:levy", "kind": "tax_levy", "unit": "per_kwh",
                     "settlement": "h", "periods": [{"name": "all", "rate": 0.002}]})
    bill = _A().bill_meter(n, c, _ones(1.0), _ones(0.0))
    mwh = len(JAN_FEB)
    assert bill.by_component.taxes_levies == pytest.approx((30.0 + 1.0) * mwh)
    assert bill.by_component.network == pytest.approx(2.0 * mwh)
    assert bill.total == pytest.approx(sum(getattr(bill.by_component, k) for k in SEVEN))


def test_an_item_the_engine_cannot_rate_makes_its_component_not_established():
    n = _net()
    c = _with_items(_C().commercial_from_form(_form(), JAN_FEB),
                    {"id": "winter_fee", "kind": "fixed", "unit": "per_month",
                     "periods": [{"name": "w", "rate": 5.0, "months": [1]}]})
    bill = _A().bill_meter(n, c, _ones(1.0), _ones(0.0))
    assert bill.by_component.fixed is None
    assert bill.by_component.unavailable["fixed"] == "item_unsupported"
    assert bill.total is None


# ── WP0 parity: both seeds, every component, 1e-9 ────────────────────────

def _defaults():
    from services.study import library as L

    return L.load_defaults()


def _compiled_seed(tid, snapshots):
    from services.study import library as L
    from services.study import questions as Q

    intake = {**SF.site_intake(), "tariff": {"tariff_id": tid}}
    defaults = _defaults()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, defaults)
    return _C().commercial_from_ledger(intake, ledger, defaults, snapshots,
                                       export_series=FAKE_REF)


def _bound_golden(option, tid):
    n = golden_copy(option)
    c = _compiled_seed(tid, n.snapshots)
    price = _defaults().tariffs[tid].export.price_per_mwh
    c = _C().bind_on_network(n, c, resolve_ref=flat_resolver(price, n.snapshots))
    return n, c


@pytest.mark.live_solve
@pytest.mark.parametrize("option", SF.SITE_OPTIONS)
@pytest.mark.parametrize("tid", SEEDS)
def test_every_seed_component_equals_wp0_on_the_golden_dispatch(tid, option):
    """
    WP5 acceptance: on the golden (GS-solved) dispatch, every bill component
    of both seeds equals WP0's `seed_bills` to 1e-9 relative; the seventh is
    0. The network was not solved by the engine, so `bill_site` cannot vouch
    for the dispatch: the total is not established (`solve_provenance_unknown`)
    while the components still sum to WP0's total.
    """
    n, c = _bound_golden(option, tid)
    bill = _A().bill(n, c)
    want = WP0["seed_bills"][tid][option]
    for k in SIX:
        assert close(getattr(bill.by_component, k), want["by_component"][k]), (
            k, getattr(bill.by_component, k), want["by_component"][k])
    assert bill.by_component.taxes_levies == 0.0
    assert bill.total is None and "solve_provenance_unknown" in bill.unavailable["total"]
    assert close(sum(getattr(bill.by_component, k) for k in SEVEN), want["total"])


@pytest.mark.live_solve
@pytest.mark.parametrize("tid", SEEDS)
def test_every_seed_bill_totals_wp0(tid):
    """
    The totals alone (the bill as a whole, whatever the component map): Σ of
    the seven components on each golden dispatch equals WP0's total to 1e-9.
    """
    for option in SF.SITE_OPTIONS:
        n, c = _bound_golden(option, tid)
        bill = _A().bill(n, c)
        got = sum(getattr(bill.by_component, k) for k in SEVEN)
        assert close(got, WP0["seed_bills"][tid][option]["total"]), (option, got)


@pytest.mark.parametrize("tid", SEEDS)
def test_the_preview_on_the_unsolved_baseline_equals_wp0(tid):
    """
    C4 preview: `bill_meter` on the UNSOLVED `none` pack with import = load,
    export = 0 is established and equals WP0's grid-only bill exactly.
    """
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q

    intake = {**SF.site_intake(), "tariff": {"tariff_id": tid}}
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, _defaults())
    n = packs.build_site_network(intake, ledger, "none", library=_defaults())
    c = _compiled_seed(tid, n.snapshots)
    load = n.loads_t.p_set["site_load"]
    bill = _A().bill_meter(n, c, load, load * 0.0)
    want = WP0["seed_bills"][tid]["none"]
    for k in SIX:
        assert close(getattr(bill.by_component, k), want["by_component"][k]), k
    assert close(bill.total, want["total"]) and close(bill.annual_bill, want["annual_bill"])
    assert bill.unavailable == {}


@pytest.mark.live_solve
def test_value_streams_still_sum_to_the_savings_on_engine_bills():
    """
    `findings.value_streams` on adapter bills (grid-only baseline and the
    `bess_2h` dispatch metered, DE: no export) covers the seven components
    and sums to the savings; `STREAMS` still imports with its assertion.
    """
    from services.study import findings as F

    assert sorted(c for _k, _l, cs in F.STREAMS for c in cs) == sorted(SEVEN)
    out = {}
    for option in ("none", "bess_2h"):
        n, c = _bound_golden(option, DE)
        p0 = n.links_t.p0
        out[option] = _A().bill_meter(n, c, p0["grid_import"], p0["grid_export"])
    streams = F.value_streams(out["none"], out["bess_2h"])
    savings = out["none"].annual_bill - out["bess_2h"].annual_bill
    assert savings == pytest.approx(WP0["golden_s5"]["bess_2h"]["savings_annual"], rel=1e-9)
    assert sum(s.annual_value for s in streams) == pytest.approx(savings, rel=1e-12)
    assert {s.key for s in streams} >= {"taxes_levies"}


@pytest.mark.live_solve
def test_on_an_engine_solved_fork_the_bill_is_established_and_export_is_the_ledger_line():
    """
    §3.2: on a fork solved through `run_simulation` with the compiled
    config bound, the bill is established (no drift, a solve record), the
    export credit is `commercial_cost_terms.block["energy_export"]` and it
    cross-checks with `value_flows.export_revenue` to 1e-9. The IC dispatch's
    components are compared with WP0 at the LP tolerance and recorded.
    """
    import queue
    import threading

    from services.commercial.cost_rows import commercial_cost_terms
    from services.pypsa_service import PyPSAService
    from services.results.value_flows import export_revenue
    from services.solver_service import SolverConfig, run_simulation
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from tests.conftest import install_network_into_backend

    intake = SF.site_intake()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, _defaults())
    n = packs.build_site_network(intake, ledger, "bess_pv_2h", library=_defaults())
    n.links_t.marginal_cost = n.links_t.marginal_cost.drop(
        columns=[x for x in ("grid_import", "grid_export") if x in n.links_t.marginal_cost])
    c = _compiled_seed(DE, n.snapshots)
    c = _C().bind_on_network(n, c, resolve_ref=flat_resolver(40.0, n.snapshots))
    install_network_into_backend(n)
    cfg = SolverConfig(solver_name="highs", discount_rate=0.07, default_lifetime=25.0,
                       commercial=c.config.model_dump(mode="json"))
    live = PyPSAService.get_network()
    status, _ = run_simulation(cfg, live, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal")
    bill = _A().bill(live, c)
    assert bill.unavailable == {} and bill.total is not None
    block = commercial_cost_terms(live, c.config.model_dump(mode="json"))["block"]
    assert bill.by_component.export_credit == pytest.approx(block["energy_export"], rel=1e-12)
    assert close(-export_revenue(live, c.config)["_"], bill.by_component.export_credit)
    want = WP0["seed_bills"][DE]["bess_pv_2h"]
    assert close(bill.total, want["total"], rel=1e-4)


@pytest.mark.live_solve
def test_a_pv_option_with_a_zero_export_price_bills_a_real_zero_credit():
    """
    Gate U2-S1 C1 on a PV option: a site whose tariff states export is paid
    0.0 compiles, is solved through `run_simulation`, and its bill is
    ESTABLISHED with `export_credit == 0.0` however much the PV exports —
    never null because some kWh left the site.
    """
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from tests.conftest import install_network_into_backend

    defaults = _defaults()
    form = defaults.tariffs[DE].model_copy(
        update={"export": defaults.tariffs[DE].export.model_copy(update={"price_per_mwh": 0.0})})
    intake = {**SF.site_intake(), "tariff": {"custom": form.model_dump(mode="json")}}
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, defaults)
    n = packs.build_site_network(intake, ledger, "bess_pv_2h", library=defaults)
    n.links_t.marginal_cost = n.links_t.marginal_cost.drop(
        columns=[x for x in ("grid_import", "grid_export") if x in n.links_t.marginal_cost])
    c = _C().commercial_from_ledger(intake, ledger, defaults, n.snapshots, export_series=FAKE_REF)
    c = _C().bind_on_network(n, c, resolve_ref=flat_resolver(0.0, n.snapshots))
    install_network_into_backend(n)
    cfg = SolverConfig(solver_name="highs", discount_rate=0.07, default_lifetime=25.0,
                       commercial=c.config.model_dump(mode="json"))
    live = PyPSAService.get_network()
    status, _ = run_simulation(cfg, live, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal")
    bill = _A().bill(live, c)
    assert bill.by_component.export_credit == 0.0
    assert "export_credit" not in bill.by_component.unavailable
    assert bill.total is not None and bill.unavailable == {}

