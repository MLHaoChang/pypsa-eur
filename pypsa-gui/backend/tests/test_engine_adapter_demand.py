"""
U2 WP6 TARGET — the demand charge on IC's commercial chain (`lp_bindings.
add_demand_terms` through `run_simulation`), replacing GS's
`_wrap_with_demand_charge` and `SolverConfig.demand_charge`.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md
§4.7, §5.2, WP2 (port of `test_tariff_demand_charge.py`), WP6. Every target
goes through `compile.solver_config` (the LP switch), so all are
`pending("WP6")` until stage 2.

The toy is WP0's 3-month network (`tests/u2_record_pre_numbers.py::
toy_network(gs_prices=False)`: no prices on the Links), priced by the
compiled toy tariff. WP0's GS-wrapper record is the oracle: objective
≤ 1e-7 relative, equal monthly peaks, the same battery size.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from tests import u2_record_pre_numbers as REC
from tests.u2_targets import FAKE_REF, WP0, flat_resolver, pending

pytestmark = pending("WP6", "the LP switch (compile.solver_config, adapter demand amount)")
TOY = WP0["toy_3month"]


def _C():
    from services.study import compile as C

    return C


def _A():
    from services.study import engine_adapter as A

    return A


def _compiled(n, *, demand: bool = True):
    form = REC.toy_tariff()
    if not demand:
        form = form.model_copy(update={"demand_charge": None})
    c = _C().commercial_from_form(form, n.snapshots, export_series=FAKE_REF)
    return _C().bind_on_network(n, c, resolve_ref=flat_resolver(40.0, n.snapshots))


def _run(n, cfg):
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    PyPSAService.set_network(n)
    q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), q)
    lines = []
    while not q.empty():
        lines.append(str(q.get()))
    return status, condition, lines


def _solve(*, demand: bool = True, **over):
    n = REC.toy_network(gs_prices=False)
    c = _compiled(n, demand=demand)
    cfg = _C().solver_config(None, c, discount_rate=0.07, **over)
    status, condition, lines = _run(n, cfg)
    assert (status, condition) == ("ok", "optimal"), lines[-20:]
    return n, cfg, c


@pytest.fixture(scope="module")
def solved():
    base = _solve(demand=False)
    charged = _solve()
    return base, charged


def _monthly_max_import(n) -> pd.Series:
    p0 = n.links_t.p0["grid_import"]
    return p0.groupby(p0.index.strftime("%Y-%m")).max()


def test_the_solver_config_carries_the_commercial_block_through_the_queue_snapshot():
    import json
    from dataclasses import asdict

    from routers.projects import _solver_config_from_dict

    n = REC.toy_network(gs_prices=False)
    cfg = _C().solver_config(None, _compiled(n), discount_rate=0.07)
    assert cfg.demand_charge is None and cfg.commercial["poc_link"] == "grid_import"
    again = _solver_config_from_dict(json.loads(json.dumps(asdict(cfg))))
    assert again.commercial == cfg.commercial


def test_a_partial_put_keeps_the_commercial_block():
    from models.schemas import SolverConfigSchema
    from routers.simulation import _state, update_solver_config
    from services.pypsa_service import PyPSAService

    n = REC.toy_network(gs_prices=False)
    PyPSAService.set_network(n)
    _state["solver_config"] = _C().solver_config(None, _compiled(n), discount_rate=0.07)
    update_solver_config(SolverConfigSchema(voll=5000.0), db=None, user=None)
    assert _state["solver_config"].commercial["poc_link"] == "grid_import"


def test_peak_import_falls_with_the_charge(solved):
    (base, _, _), (charged, _, _) = solved
    assert float(charged.storage_units.at["bess", "p_nom_opt"]) > 0.5
    assert (_monthly_max_import(charged) < _monthly_max_import(base) - 0.5).all()


def test_the_committed_peaks_equal_the_max_import_per_month_and_wp0(solved):
    from services.commercial import lp_bindings as LP

    _, (charged, _, _) = solved
    peaks = charged.meta[LP.META_DEMAND]
    by_month = {v["month"]: float(v["peak_mw"]) for v in peaks.values()}
    mx = _monthly_max_import(charged)
    for m, want in TOY["peak_import_mw"].items():
        assert abs(by_month[m] - float(mx[m])) <= 1e-5
        assert abs(by_month[m] - want) <= 1e-6


def test_the_objective_equals_the_gs_wrappers_wp0_record(solved):
    """§4.7 / wrapper order → the IC chain: objective ≤ 1e-7, size 1e-4 MW."""
    _, (charged, _, _) = solved
    assert abs(float(charged.objective) - TOY["objective"]) <= 1e-7 * abs(TOY["objective"])
    assert abs(float(charged.storage_units.at["bess", "p_nom_opt"])
               - TOY["battery_p_nom_mw"]) <= 1e-4


def test_objective_delta_is_the_charge_plus_the_cost_delta(solved):
    from services.results.cost_breakdown import compute_cost_breakdown

    (base, base_cfg, _), (charged, charged_cfg, c) = solved
    dc = _A().demand_charge_eur(charged, c)
    assert dc == pytest.approx(TOY["demand_charge_eur"], rel=1e-6)
    d_obj = float(charged.objective) - float(base.objective)
    d_cost = (compute_cost_breakdown(charged, charged_cfg)["total"]
              - compute_cost_breakdown(base, base_cfg)["total"])
    assert d_obj == pytest.approx(dc + d_cost, rel=1e-6)


def test_objective_scale_leaves_sizes_and_the_charge_unchanged(solved):
    _, (charged, _, c) = solved
    scaled, _, cs = _solve(user_objective_scale=10.0)
    assert float(scaled.storage_units.at["bess", "p_nom_opt"]) == pytest.approx(
        float(charged.storage_units.at["bess", "p_nom_opt"]), rel=1e-5)
    assert _A().demand_charge_eur(scaled, cs) == pytest.approx(
        _A().demand_charge_eur(charged, c), rel=1e-5)


def test_the_decomposition_closes_with_the_commercial_component(solved):
    """F1-B4 port 1 (§5.2): the Commercial component replaces the GS term."""
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    _, (charged, cfg, _) = solved
    d = compute_objective_decomposition(charged, compute_cost_breakdown(charged, cfg), cfg)
    assert abs(d["residual_gap_eur"]) <= 1e-6 * abs(d["lp_total"])


def test_a_config_changed_after_the_solve_reads_the_committed_amount_and_flags_drift(solved):
    """
    F1-B4 ports 2 and 3: the committed record (`ic_demand_peaks`) prices
    the charge the solve carried; the bill flags `config_changed_since_solve`.
    """
    import dataclasses

    _, (charged, _, c) = solved
    committed = _A().demand_charge_eur(charged, c)
    cfg2 = c.config.model_copy(deep=True)
    [d] = [i for i in cfg2.import_tariff.items if i.id == "demand"]
    d.periods[0].rate = 1.0
    c2 = dataclasses.replace(c, config=cfg2)
    assert _A().demand_charge_eur(charged, c2) == pytest.approx(committed, rel=1e-12)
    assert "config_changed_since_solve" in _A().bill(charged, c2).unavailable["total"]


def test_a_failed_solve_leaves_the_previous_record_beside_the_previous_dispatch():
    from services.commercial import lp_bindings as LP

    n, cfg, _ = _solve()
    rec = dict(n.meta[LP.META_DEMAND])
    p0 = n.links_t.p0.copy()
    n.loads_t.p_set["site_load"] = 500.0
    status, _cond, _lines = _run(n, cfg)
    assert status not in ("ok", "optimal")
    assert n.meta[LP.META_DEMAND] == rec and n.links_t.p0.equals(p0)


@pytest.mark.parametrize("over, code", [
    ({"solve_strategy": "myopic"}, "commercial_strategy_myopic"),
    ({"solve_strategy": "rolling"}, "commercial_strategy_rolling"),
], ids=["myopic", "rolling"])
def test_refused_modes(over, code):
    n = REC.toy_network(gs_prices=False)
    cfg = _C().solver_config(None, _compiled(n), discount_rate=0.07, **over)
    status, condition, lines = _run(n, cfg)
    assert status not in ("ok", "optimal")
    assert any(code in line for line in lines), lines[-10:]


def test_the_pack_writes_no_prices_and_the_solve_materialises_them():
    """
    Port of `test_write_tariff_prices_writes_permanent_link_prices` (C2):
    after U2 the site pack's Links carry no tariff price; IC's
    `materialise_poc_prices` prices the PoC transiently at solve (import =
    band + per-MWh network, export = −`ic_export_price`) and undoes it.
    """
    import numpy as np

    from services.commercial.lp_bindings import materialise_poc_prices
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from tests.golden import site_fixture as SF

    defaults = L.load_defaults()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, SF.site_intake(), defaults)
    n = packs.build_site_network(SF.site_intake(), ledger, "bess_2h", library=defaults)
    assert "grid_import" not in n.links_t.marginal_cost.columns
    c = _C().commercial_from_ledger(SF.site_intake(), ledger, defaults, n.snapshots,
                                    export_series=FAKE_REF)
    c = _C().bind_on_network(n, c, resolve_ref=flat_resolver(40.0, n.snapshots))
    applied = materialise_poc_prices(n, c.config)
    try:
        assert np.allclose(n.links_t.marginal_cost["grid_import"].to_numpy(), 130.0)
        assert np.allclose(n.links_t.marginal_cost["grid_export"].to_numpy(), -40.0)
    finally:
        applied.undo()
    assert "grid_import" not in n.links_t.marginal_cost.columns
