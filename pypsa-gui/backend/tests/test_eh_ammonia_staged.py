"""
`eh_ammonia_staged` (grid-to-process ticket 05, design §3.A "Acceptance checks (A)").

The H2 hub's staged plan with a fixed-size, committable Haber-Bosch loop, an NH3 tank and a per-period delivery.
Everything here is template data: the engine and the rules it must pass are tickets 03 and 04's.

The planning solves go through `run_simulation` (linearised UC, then the binary operational check), the way the app
runs them; a variant re-solves a changed copy of the template.
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading
import time

import numpy as np
import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_ammonia as A  # noqa: E402
import eh_templates as T  # noqa: E402

from services.process_template_rules import refusals  # noqa: E402

TID = "eh_ammonia_staged"
YEARS = A.YEARS
_PLANS: dict[str, tuple] = {}


def _plan(key: str, edit=None, **cfg_kw):
    """Solve the template (or a changed copy) once per module; returns (network, state, seconds)."""
    if key not in _PLANS:
        from services.pypsa_service import PyPSAService
        from services.solver_service import SolverConfig, run_simulation
        import routers.simulation as sim_router
        from tests.conftest import install_network_into_backend

        n = T.BUILDERS[TID]()
        if edit is not None:
            edit(n)
        install_network_into_backend(n)
        cfg = SolverConfig(**{**T.solver_config(TID), **cfg_kw})
        sim_router._state["solver_config"] = cfg
        n = PyPSAService.get_network()
        state: dict = {}
        t0 = time.monotonic()
        status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue(),
                                      state_update=lambda **k: state.update(k))
        assert status in ("ok", "optimal"), (status, cond)
        _PLANS[key] = (n, state, time.monotonic() - t0)
    return _PLANS[key]


@pytest.fixture(scope="module")
def n0():
    return T.BUILDERS[TID]()


def _by_period(series: pd.Series) -> pd.Series:
    return series.groupby(level=0).sum()


# ── the template as built ──────────────────────────────────────────────────


def test_it_is_registered_and_passes_the_template_rules(n0):
    assert TID in T.BUILDERS and TID in T.TEMPLATE_META and TID in T.STRESS_SCENARIOS
    assert refusals(n0, T.solver_config(TID)) == []


def test_haber_bosch_is_a_fixed_size_committable_process_unit(n0):
    hb = n0.links.loc["haber_bosch"]
    assert bool(hb.eh_process) and bool(hb.committable) and not bool(hb.p_nom_extendable)
    assert (hb.bus0, hb.bus1, hb.bus2) == ("hub", "nh3", "h2")
    assert hb.p_min_pu == 0.4 and hb.min_up_time == 24 and hb.min_down_time == 8
    assert hb.ramp_limit_up == 0.2 and hb.ramp_limit_down == 0.2
    assert hb.start_up_cost == 0.0 and hb.shut_down_cost == 0.0
    assert hb.criticality_tier == "process_critical"
    # 160 t/d of NH3 at 6.667 t/h: the link's p_nom is MW of electricity.
    t_per_h = 160.0 / 24.0
    assert hb.p_nom * hb.efficiency / A.MWH_PER_T == pytest.approx(t_per_h)
    assert n0.links.loc[[a for a in n0.links.index if a.startswith("electrolyser")],
                        "criticality_tier"].eq("process_flexible").all()


def test_the_fuel_cell_and_the_h2_offtake_are_gone(n0):
    assert "fuel_cell" not in n0.links.index and "h2_offtake" not in n0.loads.index
    assert "h2_offtake" not in n0.loads_t.p_set.columns


def test_the_nh3_bus_carries_its_tonne_factor_and_value(n0):
    b = n0.buses.loc["nh3"]
    assert b.unit == "MWh" and b.mwh_per_t == 5.166 and b.product_value == 97.0


def test_the_slacks_are_found_by_role_and_priced_above_the_buy_in(n0):
    from models.process_unit import product_role_generators as roles

    assert list(roles(n0, "buy_in")) == ["__buy_nh3"]
    assert list(roles(n0, "shortfall")) == ["__lost_nh3"]
    assert n0.generators.at["__buy_nh3", "marginal_cost"] * 5.166 == pytest.approx(501.1, abs=0.1)
    assert n0.generators.at["__lost_nh3", "marginal_cost"] * 5.166 == pytest.approx(800.0)
    assert n0.generators.at["__lost_nh3", "marginal_cost"] > n0.generators.at["__buy_nh3", "marginal_cost"]


def test_the_delivery_is_50_100_150_tonnes_a_day(n0):
    p = n0.loads_t.p_set["nh3_delivery"]
    for y, tpd in A.DELIVERY_TPD.items():
        assert p.loc[y].to_numpy() == pytest.approx(tpd / 24.0 * 5.166)


def test_both_tanks_are_extendable_priced_and_cyclic_per_period(n0):
    for s in ("h2_storage", "nh3_tank"):
        row = n0.stores.loc[s]
        assert bool(row.e_nom_extendable) and row.e_nom == 0.0
        assert bool(row.e_cyclic) and bool(row.e_cyclic_per_period)
        assert row.overnight_cost > 0 and row.lifetime >= A.HORIZON_END - YEARS[0]
    assert n0.stores.at["nh3_tank", "overnight_cost"] == pytest.approx(211.8256)
    assert n0.stores.at["h2_storage", "overnight_cost"] == pytest.approx(60046.9)


def test_the_ledger_has_a_row_for_every_ammonia_number(n0):
    ledger = " | ".join(f"{r['item']} {r['value']}" for r in T.TEMPLATE_META[TID]["ledger"])
    for needle in ("5.166", "5.93", "0.2473", "160 t/d", "0.4", "24 h / 8 h", "0.2", "97", "800", "50 / 100 / 150",
                   "211.83", "1842.99", "60.05"):
        assert needle in ledger, needle
    assert all(r["source"] in ("sourced", "synthetic", "assumed") for r in T.TEMPLATE_META[TID]["ledger"])


def test_the_electricity_unit_is_per_mwh_nh3_not_per_tonne():
    """D3: technology-data v0.14.0 `Haber-Bosch,electricity-input` is MWh_el per MWh_NH3."""
    assert A.EL_MWH_PER_MWH == 0.2473
    assert A.EL_MWH_PER_T == pytest.approx(1.2776, abs=1e-4)
    n = T.BUILDERS[TID]()
    assert 1.0 / n.links.at["haber_bosch", "efficiency"] * 5.166 == pytest.approx(1.2776, abs=1e-4)
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack(A.AMMONIA_PACK_VERSION)
    assert {"haber-bosch", "nh3-tank", "h2-tank"} <= {v.technology for v in pack.cost_values}


def test_the_sidecars_are_current():
    """The shipped sidecars match what the builders write (regenerate with `_build.py`)."""
    out = TPL_DIR / TID
    meta = json.loads((out / T.META_FILE).read_text())
    assert meta == json.loads(json.dumps(T.TEMPLATE_META[TID]))
    assert json.loads((out / T.SOLVER_FILE).read_text()) == json.loads(json.dumps(T.solver_config(TID)))


# ── the plan ───────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_it_solves_linearised_with_a_binary_check_per_period(reset_backend):
    n, state, _ = _plan("base")
    assert np.isfinite(n.objective) and n.objective > 0
    check = state["last_process_uc_check"]
    assert check["status"] == "ok"
    assert [r["period"] for r in check["periods"]] == list(YEARS)
    assert all(r["status"] == "ok" and r["pct"] is not None for r in check["periods"])


@pytest.mark.live_solve
def test_mass_balance_per_period_and_the_h2_ratio(reset_backend):
    n, _, _ = _plan("base")
    made = -n.links_t.p1["haber_bosch"]
    delivered = n.loads_t.p["nh3_delivery"]
    buy = n.generators_t.p["__buy_nh3"]
    lost = n.generators_t.p["__lost_nh3"]
    # Production + buy-in + lost = delivery + the tank's net flow over the period (0: cyclic).
    tank = _by_period(n.stores_t.p["nh3_tank"])
    scale = _by_period(delivered).max()
    assert tank.abs().max() < 1e-6 * scale
    assert _by_period(made + buy + lost - delivered).abs().max() < 1e-6 * scale
    assert n.links_t.p2["haber_bosch"].to_numpy() == pytest.approx(
        (A.H2_MWH_PER_T / A.MWH_PER_T) * made.to_numpy(), abs=1e-6)


@pytest.mark.live_solve
def test_haber_bosch_electricity_is_the_pinned_unit_times_the_tonnes(reset_backend):
    n, _, _ = _plan("base")
    tonnes = -n.links_t.p1["haber_bosch"] / A.MWH_PER_T
    assert n.links_t.p0["haber_bosch"].to_numpy() == pytest.approx(
        A.EL_MWH_PER_T * tonnes.to_numpy(), abs=1e-6)


@pytest.mark.live_solve
def test_when_it_runs_it_never_runs_below_the_minimum_load_times_u(reset_backend):
    n, _, _ = _plan("base")
    p, u = n.links_t.p0["haber_bosch"], n.links_t.status["haber_bosch"]
    assert ((u >= -1e-6) & (u <= 1 + 1e-6)).all()
    assert (p >= 0.4 * n.links.at["haber_bosch", "p_nom"] * u - 1e-6).all()


@pytest.mark.live_solve
def test_the_plan_pays_no_start_up_cost_and_the_check_reports_its_gap(reset_backend):
    n, state, _ = _plan("base")
    assert n.links.at["haber_bosch", "start_up_cost"] == 0.0
    for r in state["last_process_uc_check"]["periods"]:
        assert r["delta"] is not None and np.isfinite(r["delta"])
    items = {row["item"] for row in state["last_process_uc_check"]["ledger"]}
    assert {f"period {y}: result" for y in YEARS} <= items


# ── the binary operational windows ─────────────────────────────────────────


class _Cfg:
    solver_name = "highs"
    mip_gap = 1e-4
    mip_time_limit_s = 0
    process_uc = "auto"


def _windows(n):
    """The binary check's per-period windows on `n`: {period: (linearised model, binary model)}."""
    from services.solver import process_uc as PU

    models: dict = {}

    def capture(m, sns, multi, cfg, solver_options, kw):
        status, cond, cost = PU._solve_window(m, sns, multi, cfg, solver_options, kw)
        models.setdefault(int(sns[0][0]), {})["lin" if kw else "bin"] = (m, sns, cost)
        return status, cond, cost

    payload = PU.binary_check(n, _Cfg, solve=capture)
    return models, payload


def _runs(u: np.ndarray, value: float) -> list[int]:
    """Lengths of the closed runs of `value` in `u` (not touching either end of the window)."""
    out, cur = [], 0
    for k, v in enumerate(u):
        if v == value:
            cur += 1
        else:
            if cur and k - cur > 0:
                out.append(cur)
            cur = 0
    return out


@pytest.mark.live_solve
def test_in_the_binary_window_every_stop_lasts_the_restart_time(reset_backend):
    # 2027 delivers 50 t/d, below the 64 t/d minimum load: the loop has to cycle.
    n, _, _ = _plan("base")
    models, payload = _windows(n)
    assert payload["status"] == "ok", payload["message"]
    m, sns, _ = models[2027]["bin"]
    u = m.links_t.status["haber_bosch"].loc[sns].round().to_numpy()
    p = m.links_t.p0["haber_bosch"].loc[sns].to_numpy()
    assert set(np.unique(u)) <= {0.0, 1.0}
    assert (p >= 0.4 * m.links.at["haber_bosch", "p_nom"] * u - 1e-6).all()
    stops = _runs(u, 0.0)
    assert stops, "the 2027 window should stop the loop at least once"
    assert min(stops) >= 8
    assert all(r >= 24 for r in _runs(u, 1.0))


@pytest.mark.live_solve
def test_the_check_reports_a_finite_gap_in_every_period(reset_backend):
    n, state, _ = _plan("base")
    rows = state["last_process_uc_check"]["periods"]
    assert [r["period"] for r in rows] == list(YEARS)
    for r in rows:
        assert r["status"] == "ok" and np.isfinite(r["delta"]) and np.isfinite(r["pct"])
        assert r["delta"] >= -1e-6 * abs(r["cost_lin"])                # binary never beats its relaxation


def _week_2035(edit):
    """A re-plan of the 2035 week alone with every capacity free and the loop's ramp limits off (a non-committable
    loop with a ramp limit is infeasible here), binary UC where the loop is committable."""
    from services.adequacy.energization import apply_energization

    n = T.BUILDERS[TID]()
    n.links.loc["haber_bosch", ["ramp_limit_up", "ramp_limit_down"]] = float("nan")
    edit(n)
    n.snapshots.name = "snapshot"
    apply_energization(n)
    sns = n.snapshots[n.snapshots.get_level_values(0) == YEARS[-1]]
    status, cond = n.optimize(snapshots=sns, solver_name="highs", multi_investment_periods=True,
                              solver_options={"mip_rel_gap": 1e-4})
    assert (status, cond) == ("ok", "optimal")
    imported = float((n.links_t.p0["grid_import"].loc[sns] * n.snapshot_weightings.generators.loc[sns]).sum())
    return n.objective, float(n.stores.at["h2_storage", "e_nom_opt"]), imported


@pytest.mark.live_solve
def test_a_loop_that_never_stops_needs_at_least_as_much_h2_storage_or_grid_import(reset_backend):
    """Design §3.A, inflexible HB. In the plan (linearised) `p_min_pu 1.0` with a free fractional u makes the loop
    MORE flexible: the relaxation flips the sign, and a binary re-plan of the three periods does not finish in
    ten minutes. So the sign is pinned on the 2035 week alone (delivery between the minimum load and the size,
    where a loop that cannot stop is feasible), binary where the loop can stop: a loop that never stops is a
    restriction of one that can, so it costs at least as much and needs at least as much H2 storage or import."""
    flex = _week_2035(lambda n: None)
    never = _week_2035(lambda n: n.links.loc.__setitem__(("haber_bosch", "committable"), False))
    assert never[0] >= flex[0] - 1e-6 * abs(flex[0])
    assert never[1] >= flex[1] - 1e-6 or never[2] >= flex[2] - 1e-6 * flex[2]


# ── the limiting cases ─────────────────────────────────────────────────────


def _lin_plan(edit):
    from services.adequacy.energization import apply_energization

    n = T.BUILDERS[TID]()
    edit(n)
    n.snapshots.name = "snapshot"
    apply_energization(n)
    status, cond = n.optimize(solver_name="highs", multi_investment_periods=True,
                              linearized_unit_commitment=True)
    assert (status, cond) == ("ok", "optimal")
    return n


def _per_period(n, g):
    return (n.generators_t.p[g] * n.snapshot_weightings.generators).groupby(level=0).sum()


@pytest.mark.live_solve
def test_with_an_infinite_connection_and_free_tanks_nothing_is_bought_or_lost(reset_backend):
    def edit(n):
        n.links.loc["grid_import", "p_nom"] = 1e5
        n.links_t["eh_energization_mw"] = n.links_t["eh_energization_mw"] * 0 + 1e5
        n.stores.loc[["h2_storage", "nh3_tank"],
                     ["overnight_cost", "fom_cost", "capital_cost", "e_nom_max"]] = [0.0, 0.0, 0.0, 1e7]

    n = _lin_plan(edit)
    assert _per_period(n, "__buy_nh3").abs().max() < 1e-6
    assert _per_period(n, "__lost_nh3").abs().max() < 1e-6


@pytest.mark.live_solve
def test_without_electrolysers_the_whole_delivery_is_bought_and_nothing_is_lost(reset_backend):
    def edit(n):
        for link in [a for a in n.links.index if a.startswith("electrolyser")]:
            n.remove("Link", link)

    n = _lin_plan(edit)
    delivered = (n.loads_t.p["nh3_delivery"] * n.snapshot_weightings.generators).groupby(level=0).sum()
    assert (_per_period(n, "__buy_nh3") - delivered).abs().max() < 1e-6 * delivered.max()
    assert _per_period(n, "__lost_nh3").abs().max() < 1e-6


# ── the investment case against grey NH3 (ticket 06) ───────────────────────

BUY, LOST = "asset:opex:Generator:__buy_nh3", "asset:opex:Generator:__lost_nh3"


def _no_electrolysers(n):
    for link in [a for a in n.links.index if a.startswith("electrolyser")]:
        n.remove("Link", link)


def _half_buy_in(n):
    _no_electrolysers(n)
    n.generators_t.p_max_pu["__buy_nh3"] *= 0.5


def _ic_config(edit):
    """The template's solver config; for a changed network its value flows are rebuilt (as the app does when the
    template is stale) and the COD of a removed asset dropped."""
    from models.commercial import CommercialConfig
    from services.commercial import value_flow_templates as VFT

    raw = T.solver_config(TID)
    if edit is None:
        return raw
    n = T.BUILDERS[TID]()
    edit(n)
    base = CommercialConfig.model_validate({**raw["commercial"], "value_flows": None})
    vf = VFT.build("single_owner", n, base).config
    fin = {**raw["finance"], "cod_by_asset": {a: d for a, d in raw["finance"]["cod_by_asset"].items()
                                              if a in n.links.index or a in n.generators.index
                                              or a in n.storage_units.index or a in n.stores.index}}
    return {**raw, "commercial": {**raw["commercial"], "value_flows": vf.model_dump(mode="json")},
            "finance": fin}


def _case(key: str, edit=None, *, site_today: bool):
    """(network, case) on the site-today basis (the IC route's, with a baseline re-solve) or the analytic one."""
    import routers.results as R
    from models.finance import FinanceInputs
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case
    from services.solver_service import SolverConfig

    raw = _ic_config(edit)
    n, state, _ = _plan(key, edit, **{k: raw[k] for k in ("commercial", "finance")})
    cfg = SolverConfig(**raw)
    base = None
    if site_today:
        Bl.clear_cache()
        base = Bl.solve_baseline(n, cfg, baseline_plan(n, cfg))
    try:
        case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance), result_df=R._result_df,
                                  lost_load=state.get("last_lost_load"), baseline=base)
    finally:
        Bl.clear_cache()
    return n, case


def _line(case, key, year, side="templates"):
    (t,) = [t for t in getattr(case, side) if t.first_year == year]
    return sum(ln.amount for ln in t.lines if ln.key == key)


def _cost_per_year(n, gen, year):
    """Σ w·p·(dense marginal cost) of a Generator over one period, objective weights (one year of the period)."""
    mc = n.get_switchable_as_dense("Generator", "marginal_cost")[gen].loc[year]
    w = n.snapshot_weightings.objective.loc[year]
    return float((w * n.generators_t.p[gen].loc[year] * mc).sum())


def test_the_grey_purchase_carries_its_co2_only_in_its_cost_series(n0):
    """The CO2 lives in `__buy_nh3`'s cost series alone: its carriers emit nothing, so the solver's CO2 adder
    (which acts on emitting carriers) can never add it a second time (gatekeeper, ticket 06)."""
    for g in ("__buy_nh3", "__lost_nh3"):
        assert n0.carriers.at[n0.generators.at[g, "carrier"], "co2_emissions"] == 0.0
    mc = n0.get_switchable_as_dense("Generator", "marginal_cost")["__buy_nh3"]
    for y in YEARS:
        assert mc.loc[y].to_numpy() == pytest.approx(A.buy_cost_eur_per_mwh(y), rel=1e-12)


@pytest.mark.live_solve
def test_the_investment_case_computes_against_buying_grey_ammonia(reset_backend):
    n, case = _case("base", site_today=True)
    assert "counterfactual_basis:site_today" in case.flags
    assert not [f for f in case.flags if f.startswith("counterfactual_not_established")], case.flags
    assert not {"__buy_nh3", "__lost_nh3"} & {a.name for a in case.assets}
    # The solved purchase price still carries the CO2 once (no adder on top), and the case books the purchase at it.
    mc = n.get_switchable_as_dense("Generator", "marginal_cost")["__buy_nh3"]
    for y in YEARS:
        assert mc.loc[y].to_numpy() == pytest.approx(A.buy_cost_eur_per_mwh(y), rel=1e-12)
        assert _line(case, BUY, y) == pytest.approx(-_cost_per_year(n, "__buy_nh3", y), rel=1e-9, abs=1e-3)


@pytest.mark.live_solve
def test_without_electrolysers_the_case_is_worth_nothing_against_grey_ammonia(reset_backend):
    """The site buys the same grey NH3 either way."""
    from services.finance.engine import run_case

    _n, case = _case("no_electrolysers", _no_electrolysers, site_today=True)
    assert not [f for f in case.flags if f.startswith("counterfactual_not_established")], case.flags
    assert abs(run_case(case).metrics["project_pre_tax_npv"]) < 1e3


@pytest.mark.live_solve
def test_on_the_analytic_basis_the_purchase_and_its_co2_match_the_buy_in_per_period(reset_backend):
    """The buy-in (price plus CO2, in the plan) against the counterfactual's purchase plus its CO2 line: equal per
    period, so the CO2 is counted once on each side."""
    n, case = _case("no_electrolysers", _no_electrolysers, site_today=False)
    assert "counterfactual_buys:nh3_delivery" in case.flags
    for y in YEARS:
        cf = _line(case, "counterfactual:purchase:nh3_delivery", y, "counterfactual") \
            + _line(case, "co2:purchase:nh3_delivery", y, "counterfactual")
        assert _line(case, BUY, y) == pytest.approx(cf, rel=1e-9)
        assert _line(case, LOST, y) == 0.0


@pytest.mark.live_solve
def test_a_shortfall_is_lost_production_at_its_value_never_an_expense(reset_backend):
    """With half the delivery buyable and no electrolysis, the rest is short: the case books it once, as
    `lost_production` at the lost-production value the LP priced (never VoLL), beside the purchase."""
    from services.process_products import lost_production_value

    n, case = _case("half_buy_in", _half_buy_in, site_today=False)
    value = lost_production_value(n, "__lost_nh3")
    assert value == pytest.approx(A.LOST_EUR_PER_MWH)
    for y in YEARS:
        (t,) = [t for t in case.templates if t.first_year == y]
        (lost,) = [ln for ln in t.lines if ln.key == LOST]
        assert lost.stream == "lost_production"
        w = n.snapshot_weightings.objective.loc[y]
        tonnes_eur = float((w * n.generators_t.p["__lost_nh3"].loc[y]).sum()) * value
        assert tonnes_eur > 0
        assert lost.amount == pytest.approx(-tonnes_eur, rel=1e-9)
        assert _line(case, BUY, y) == pytest.approx(-_cost_per_year(n, "__buy_nh3", y), rel=1e-9)
        assert not [ln for ln in t.lines if "__lost_nh3" in ln.key and ln.stream in ("vom", "fuel", "capex")]
