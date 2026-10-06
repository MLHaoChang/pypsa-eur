"""
The demand-charge constraint (MVP-1 phase S3), solved through
`run_simulation`.

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S3)
Review deltas: B1 (refused modes), B2 (prices are network data, the charge is
a config field, `demand_charge_eur` recomputed from `links_t.p0`), S3 (wrapper
placement, names, scale).

The toy: two months hourly (January and February 2030), a site bus behind an
import Link and an export Link to a grid bus that a zero-cost Generator with
`p_min_pu = -1` balances (review v1 N13), a sinusoidal site load, and an
extendable 2-hour StorageUnit. Energy and export prices are written by
`write_tariff_prices` from a two-band tariff. The spread (110 vs 90 EUR/MWh)
is too narrow to pay for the battery on arbitrage alone, so without the
charge it builds nothing and with it the battery exists to shave the peak.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import asdict

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.study import Tariff
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation
from services.study.tariff import write_tariff_prices

PRICE = 15_000.0  # EUR per MW per month
DC = {"price_per_mw_per_period": PRICE, "basis": "billing_period_peak",
      "billing_period": "month", "import_links": ["grid_import"]}


def _tariff() -> Tariff:
    return Tariff.model_validate(dict(
        tariff_id="toy", name="toy two-band", source="illustrative",
        currency="EUR", currency_year=2026, billing_period="month",
        energy_bands=[
            {"label": "day", "price_per_mwh": 110.0,
             "applies": {"hours": list(range(8, 20))}},
            {"label": "night", "price_per_mwh": 90.0, "applies": {}},
        ],
        demand_charge={"price_per_mw_per_period": PRICE,
                       "basis": "billing_period_peak"},
        export={"price_per_mwh": 40.0},
    ))


def _network() -> pypsa.Network:
    sn = pd.date_range("2030-01-01", "2030-02-28 23:00", freq="h")
    n = pypsa.Network()
    n.set_snapshots(sn)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=1e3, p_min_pu=-1.0,
          marginal_cost=0.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=60.0)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=60.0)
    hr = np.asarray(sn.hour)
    rng = np.random.default_rng(0)
    load = 20 + 15 * np.sin((hr - 6) / 24 * 2 * np.pi).clip(0) + 5 * rng.random(len(sn))
    n.add("Load", "site_load", bus="site", p_set=pd.Series(load, index=sn))
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, p_nom_max=40.0,
          max_hours=2.0, overnight_cost=650_000.0, lifetime=15, discount_rate=0.07,
          fom_cost=5_000.0, efficiency_store=0.95 ** 0.5,
          efficiency_dispatch=0.95 ** 0.5, cyclic_state_of_charge=True)
    write_tariff_prices(n, _tariff(), "grid_import", "grid_export")
    return n


def _drain(q: queue.SimpleQueue) -> list[str]:
    out: list[str] = []
    while True:
        try:
            m = q.get_nowait()
        except queue.Empty:
            return out
        if m is not None:
            out.append(str(m))


def _run(n: pypsa.Network, **cfg_kw):
    PyPSAService.set_network(n)
    cfg = SolverConfig(**cfg_kw)
    q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), q)
    return status, condition, _drain(q), cfg


def _solve(**cfg_kw):
    n = _network()
    status, condition, lines, cfg = _run(n, **cfg_kw)
    assert (status, condition) == ("ok", "optimal"), (condition, lines[-20:])
    return n, cfg, lines


def _monthly_max_import(n) -> pd.Series:
    p0 = n.links_t.p0["grid_import"]
    return p0.groupby(p0.index.strftime("%Y-%m")).max()


def _demand_charge_eur(n, cfg) -> float:
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    d = compute_objective_decomposition(n, compute_cost_breakdown(n, cfg), cfg)
    return d["demand_charge_eur"]


@pytest.fixture(scope="module")
def solved():
    """(without, with) the charge — two solves shared by the binding tests."""
    base, base_cfg, _ = _solve()
    charged, charged_cfg, lines = _solve(demand_charge=DC)
    return base, base_cfg, charged, charged_cfg, lines


# ── the config field ──────────────────────────────────────────────────────

def test_solver_config_carries_the_demand_charge_through_the_queue_snapshot():
    import json

    from routers.projects import _solver_config_from_dict

    cfg = SolverConfig(demand_charge=DC)
    again = _solver_config_from_dict(json.loads(json.dumps(asdict(cfg))))
    assert again.demand_charge == DC
    assert SolverConfig().demand_charge is None


def test_partial_put_keeps_and_sets_the_demand_charge():
    from routers.simulation import _state, update_solver_config
    from models.schemas import SolverConfigSchema

    _state["solver_config"] = SolverConfig()
    update_solver_config(SolverConfigSchema(demand_charge=DC), db=None, user=None)
    assert _state["solver_config"].demand_charge == DC
    update_solver_config(SolverConfigSchema(voll=5000.0), db=None, user=None)
    assert _state["solver_config"].demand_charge == DC
    update_solver_config(SolverConfigSchema(demand_charge=None), db=None, user=None)
    assert _state["solver_config"].demand_charge is None


# ── the constraint binds ──────────────────────────────────────────────────

def test_peak_import_falls_with_the_charge(solved):
    base, _, charged, _, lines = solved
    assert float(base.storage_units.at["bess", "p_nom_opt"]) == pytest.approx(0.0, abs=1e-6)
    assert float(charged.storage_units.at["bess", "p_nom_opt"]) > 0.5
    before, after = _monthly_max_import(base), _monthly_max_import(charged)
    assert (after < before - 0.5).all(), (before.to_dict(), after.to_dict())
    assert any(line.startswith("[TARIFF]") for line in lines)


def test_peak_import_variable_equals_the_max_import_per_billing_period(solved):
    _, _, charged, _, _ = solved
    peak = charged.model.variables["peak_import"].solution.to_series()
    assert sorted(peak.index) == ["2030-01", "2030-02"]
    mx = _monthly_max_import(charged)
    for p in peak.index:
        assert float(peak[p]) == pytest.approx(float(mx[p]), abs=1e-5)


def test_objective_delta_is_the_charge_plus_the_cost_delta(solved):
    """
    Δobjective = Σ price × peak + Δ(energy and capacity cost). The plan
    names the energy-cost delta; the battery's capacity cost moves too,
    so the identity uses `cost_breakdown.total`, which carries both.
    """
    from services.results.cost_breakdown import compute_cost_breakdown

    base, base_cfg, charged, charged_cfg, _ = solved
    dc_eur = PRICE * float(_monthly_max_import(charged).sum())
    assert _demand_charge_eur(charged, charged_cfg) == pytest.approx(dc_eur, rel=1e-9)
    d_obj = float(charged.objective) - float(base.objective)
    d_cost = (compute_cost_breakdown(charged, charged_cfg)["total"]
              - compute_cost_breakdown(base, base_cfg)["total"])
    assert d_obj == pytest.approx(dc_eur + d_cost, rel=1e-6)
    assert _demand_charge_eur(base, base_cfg) == 0.0


def test_objective_scale_leaves_sizes_and_the_charge_unchanged(solved):
    _, _, charged, charged_cfg, _ = solved
    scaled, scaled_cfg, _ = _solve(demand_charge=DC, user_objective_scale=10.0)
    assert float(scaled.storage_units.at["bess", "p_nom_opt"]) == pytest.approx(
        float(charged.storage_units.at["bess", "p_nom_opt"]), rel=1e-5)
    assert _demand_charge_eur(scaled, scaled_cfg) == pytest.approx(
        _demand_charge_eur(charged, charged_cfg), rel=1e-5)
    assert float(scaled.objective) == pytest.approx(float(charged.objective), rel=1e-6)


def test_decomposition_closes_with_the_demand_charge_term(solved):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    _, _, charged, cfg, _ = solved
    d = compute_objective_decomposition(charged, compute_cost_breakdown(charged, cfg), cfg)
    assert d["demand_charge_eur"] > 0
    # The golden fixture's tolerance (test_fom_reconciliation.py).
    assert abs(d["residual_gap_eur"]) <= 1e-6 * abs(d["lp_total"])
    assert abs(d["residual_gap_pct"]) <= 1e-4


# ── F1 B4 (gate S3 [N4]): the bridge reads the solve's demand charge ──────

def _decompose(n, cfg) -> dict:
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    return compute_objective_decomposition(n, compute_cost_breakdown(n, cfg), cfg)


def test_the_bridge_reads_the_demand_charge_the_solve_used_not_the_current_one(
        solved, tmp_path):
    """
    The Expert view's bridge passes the CURRENT solver config. A project
    re-configured after its solve (a charge removed, or one added) must
    still bridge with the charge the LP carried, which the solve records on
    the network (`n.meta`) and which survives a save and reload.
    """
    base, _base_cfg, charged, charged_cfg, _ = solved
    expected = _decompose(charged, charged_cfg)["demand_charge_eur"]
    assert expected > 0

    # The charge was removed from the config after the solve.
    d = _decompose(charged, SolverConfig())
    assert d["demand_charge_eur"] == pytest.approx(expected, rel=1e-12)
    assert abs(d["residual_gap_pct"]) <= 1e-4

    # A charge was added to the config after a solve without one.
    d = _decompose(base, SolverConfig(demand_charge=DC))
    assert d["demand_charge_eur"] == 0.0
    assert abs(d["residual_gap_pct"]) <= 1e-4

    # Saved and reloaded, the record holds.
    path = tmp_path / "charged.nc"
    charged.export_to_netcdf(str(path))
    reloaded = pypsa.Network(str(path))
    d = _decompose(reloaded, SolverConfig())
    assert d["demand_charge_eur"] == pytest.approx(expected, rel=1e-12)


def test_a_network_solved_before_the_record_falls_back_to_the_given_config(solved):
    from services.study.tariff import SOLVE_DEMAND_CHARGE_META

    _, _, charged, charged_cfg, _ = solved
    recorded = charged.meta.pop(SOLVE_DEMAND_CHARGE_META)  # as if solved before F1
    try:
        assert _decompose(charged, charged_cfg)["demand_charge_eur"] > 0
        assert _decompose(charged, SolverConfig())["demand_charge_eur"] == 0.0
    finally:
        charged.meta[SOLVE_DEMAND_CHARGE_META] = recorded


def test_a_failed_solve_leaves_the_previous_record_beside_the_previous_dispatch():
    """
    Gate F1 [S2]. The record is written only on a solve that produced a
    dispatch. A failed solve keeps the previous dispatch in `links_t.p0`, so
    it must keep the record that describes it: stamping the failed run's
    config (here: no charge) would bridge the old dispatch without its charge.
    """
    from services.study.tariff import SOLVE_DEMAND_CHARGE_META

    n, _cfg, _ = _solve(demand_charge=DC)
    assert n.meta[SOLVE_DEMAND_CHARGE_META] == DC
    p0_before = n.links_t.p0.copy()
    # More load than the connection can carry, with no slack: infeasible.
    n.loads_t.p_set["site_load"] = 500.0
    status, condition, lines, _ = _run(n)
    assert status not in ("ok", "optimal"), (status, condition, lines[-10:])
    assert n.meta[SOLVE_DEMAND_CHARGE_META] == DC
    assert n.links_t.p0.equals(p0_before)


def test_wrapper_composes_after_capex_budget_and_before_ens_cap():
    import inspect

    from services import solver_service

    src = inspect.getsource(solver_service.run_simulation)
    order = [src.index(f"_wrap_with_{w}(") for w in (
        "curtailment_cost", "capex_budget", "demand_charge", "ens_cap",
        "reserve_margin", "objective_scale")]
    assert order == sorted(order)


# ── refusals (review v1 B1; S1 gate: annual_peak and ratchet) ─────────────

def _flat(periods: int = 48) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=periods, freq="h"))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "g", bus="grid", p_nom=100.0, marginal_cost=10.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=100.0)
    n.add("Load", "l", bus="site", p_set=5.0)
    return n


def _integer_snapshots() -> pypsa.Network:
    n = _flat()
    m = pypsa.Network()
    m.set_snapshots(range(48))
    for c in ("Bus", "Generator", "Link", "Load"):
        df = n.c[c].static
        for name in df.index:
            m.add(c, name, **{k: v for k, v in df.loc[name].items()
                              if k in ("bus", "bus0", "bus1", "p_nom",
                                       "marginal_cost", "p_set")})
    return m


@pytest.mark.parametrize("net, cfg_kw, dc_over, code", [
    (_flat, {"solve_strategy": "myopic"}, {}, "demand_charge_strategy_myopic"),
    (_flat, {"solve_strategy": "rolling"}, {}, "demand_charge_strategy_rolling"),
    (_flat, {"sclopf": True}, {}, "demand_charge_sclopf"),
    (_flat, {"multi_investment_periods": True}, {}, "demand_charge_multi_period"),
    (_integer_snapshots, {}, {}, "demand_charge_snapshots_not_flat"),
    (_flat, {}, {"basis": "annual_peak"}, "demand_charge_basis_annual_peak"),
    (_flat, {}, {"basis": "ratchet", "ratchet": {"months": 11, "share": 0.8}},
     "demand_charge_basis_ratchet"),
], ids=["myopic", "rolling", "sclopf", "multi_period", "not_flat",
        "annual_peak", "ratchet"])
def test_refused_modes_and_bases(net, cfg_kw, dc_over, code):
    from services.solver.objective import DemandChargeRefused, _wrap_with_demand_charge

    dc = {**DC, **dc_over}
    n = net()
    cfg = SolverConfig(demand_charge=dc, **cfg_kw)
    # The typed error, directly.
    with pytest.raises(DemandChargeRefused) as exc:
        _wrap_with_demand_charge(n, None, cfg, log_queue=None)
    assert exc.value.code == code
    # Through run_simulation: a refusal (no traceback), never a silent solve.
    status, condition, lines, _ = _run(n, demand_charge=dc, **cfg_kw)
    assert (status, condition) == ("error", "validation_failed")
    assert any(line.startswith("[TARIFF]") and code in line for line in lines), lines
    assert not any(line.startswith("TRACEBACK:") for line in lines)
    assert "peak_import" not in getattr(getattr(n, "model", None), "variables", {})
