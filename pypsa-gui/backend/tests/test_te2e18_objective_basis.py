"""Template-e2e 18: the LP objective and the reported total system cost reconcile.

The staged templates solve with `auto_discount_periods`, which weights each period's
LP terms by PV × years for the solve and restores the network's weights afterwards
(`services.solver.assumptions`). The objective (toast, status bar) is that discounted
total; `cost_breakdown.total` (Summary) is the undiscounted one. The decomposition
must price the LP side with the weights the solve used, so its residual is ~0 and the
discounting shows as `period_weighting_adjustment_eur`.
"""

from __future__ import annotations

import dataclasses
import pathlib
import queue
import sys
import threading

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402


def _solved(tid):
    from routers.projects import _solver_config_from_dict
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = T.BUILDERS[tid]()
    PyPSAService.set_network(n)
    cfg = _solver_config_from_dict(T.solver_config(tid))
    status, condition = run_simulation(
        cfg,
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: None,
    )
    assert status in ("ok", "optimal"), (status, condition)
    return n, cfg


def test_auto_discount_factors_are_pv_times_years():
    from routers.projects import _solver_config_from_dict
    from services.solver.assumptions import auto_discount_factors

    tid = "eh_h2_hub_staged"
    n = T.BUILDERS[tid]()
    cfg = _solver_config_from_dict(T.solver_config(tid))
    f = auto_discount_factors(n, cfg)
    years = n.investment_period_weightings["years"]
    r = (1 + cfg.discount_rate) / (1 + cfg.inflation_rate) - 1
    p0 = min(int(p) for p in n.investment_periods)
    assert f["ref_year"] == p0 and f["real_rate"] == pytest.approx(r)
    for p in n.investment_periods:
        assert f["factors"][int(p)] == pytest.approx(
            (1 + r) ** -(int(p) - p0) * float(years[p])
        )
    # Off: no factors.
    assert (
        auto_discount_factors(n, dataclasses.replace(cfg, auto_discount_periods=False))
        is None
    )


@pytest.mark.live_solve
@pytest.mark.parametrize("tid", ["eh_h2_hub_staged", "eh_ammonia_staged"])
def test_the_decomposition_closes_on_a_discounted_solve(tid, reset_backend):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n, cfg = _solved(tid)
    assert cfg.auto_discount_periods
    cb = compute_cost_breakdown(n, cfg)
    dec = compute_objective_decomposition(n, cb, cfg)
    # The objective is discounted, the reported total is not: a real gap.
    assert dec["gap_pct"] < -5, dec
    # ... and the bridge explains all of it.
    assert abs(dec["residual_gap_eur"]) <= max(1.0, 1e-6 * abs(dec["lp_total"])), dec
    assert dec["period_weighting_adjustment_eur"] < 0, dec
    assert dec["discount"]["ref_year"] == min(int(p) for p in n.investment_periods)
    assert dec["discount"]["real_rate"] > 0


def _status_with(monkeypatch, n, objective=1.0e6):
    from routers import simulation

    monkeypatch.setattr(simulation.PyPSAService, "get_network", staticmethod(lambda: n))
    monkeypatch.setattr(simulation, "_dispatch_status", lambda _n: "fresh")
    monkeypatch.setitem(simulation._state, "objective", objective)
    st = simulation.get_status()
    return st["objective_basis"], st["objective_discounted_to"]


@pytest.mark.live_solve
def test_the_status_reads_the_solves_stamp_not_todays_config(
    monkeypatch, reset_backend
):
    from routers import simulation

    n, cfg = _solved("eh_h2_hub_staged")
    p0 = min(int(p) for p in n.investment_periods)
    assert _status_with(monkeypatch, n) == ("lp_objective", p0)
    # The toggle and strategy changed after the solve (GK B F3: full, then myopic): the figure
    # is still the one solved.
    for strategy in ("myopic", "rolling"):
        monkeypatch.setitem(
            simulation._state,
            "solver_config",
            dataclasses.replace(
                cfg, auto_discount_periods=False, solve_strategy=strategy
            ),
        )
        assert _status_with(monkeypatch, n) == ("lp_objective", p0)


def test_the_status_names_a_myopic_or_rolling_figure_the_system_cost(monkeypatch):
    from services.solver.assumptions import SOLVE_DISCOUNT_META, SOLVE_STRATEGY_META

    n = T.BUILDERS["eh_h2_hub_staged"]()
    n.meta[SOLVE_DISCOUNT_META] = None
    for strategy in ("rolling", "myopic"):
        n.meta[SOLVE_STRATEGY_META] = strategy
        assert _status_with(monkeypatch, n) == ("system_cost", None)
    n.meta[SOLVE_STRATEGY_META] = "full"
    assert _status_with(monkeypatch, n) == ("lp_objective", None)


def test_a_result_solved_before_the_stamp_reads_unknown(monkeypatch):
    n = T.BUILDERS["eh_h2_hub_staged"]()
    assert _status_with(monkeypatch, n) == ("unknown", None)
    assert _status_with(monkeypatch, n, objective=None) == (None, None)


@pytest.mark.live_solve
def test_a_rate_changed_after_the_solve_does_not_move_the_bridge(reset_backend):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n, cfg = _solved("eh_h2_hub_staged")
    cb = compute_cost_breakdown(n, cfg)
    before = compute_objective_decomposition(n, cb, cfg)
    later = dataclasses.replace(cfg, discount_rate=0.15, auto_discount_periods=False)
    after = compute_objective_decomposition(n, compute_cost_breakdown(n, later), later)
    assert (
        after["discount"] == before["discount"] and before["discount"]["real_rate"] > 0
    )
    assert abs(after["residual_gap_eur"]) <= max(1.0, 1e-6 * abs(after["lp_total"])), (
        after
    )


@pytest.mark.live_solve
def test_a_failed_discount_setup_stamps_none_and_the_gap_stays_unexplained(
    monkeypatch, reset_backend
):
    from services.solver import assumptions
    from services.solver.assumptions import SOLVE_DISCOUNT_META
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    def boom(n, cfg):
        raise RuntimeError("forced")

    monkeypatch.setattr(assumptions, "auto_discount_factors", boom)
    n, cfg = _solved("eh_h2_hub_staged")
    assert n.meta[SOLVE_DISCOUNT_META] is None
    dec = compute_objective_decomposition(n, compute_cost_breakdown(n, cfg), cfg)
    assert dec["discount"] is None
    # Undiscounted weights: the LP is the reported total less the existing fixed cost.
    assert abs(dec["residual_gap_eur"]) <= max(1.0, 1e-6 * abs(dec["lp_total"])), dec
    assert abs(dec["period_weighting_adjustment_eur"]) <= 1.0, dec


def test_the_decomposition_reads_unknown_without_a_stamp_and_keeps_the_rolling_path():
    from routers.projects import _solver_config_from_dict
    from services.results.objective_decomposition import compute_objective_decomposition

    tid = "eh_h2_hub_staged"
    n = T.BUILDERS[tid]()
    cfg = _solver_config_from_dict(T.solver_config(tid))
    cb = {"total": 2.0e6, "opex": 1.0e6, "by_period": []}
    dec = compute_objective_decomposition(
        n, cb, dataclasses.replace(cfg, solve_strategy="rolling")
    )
    assert dec["lp_total_scope"] == "last_window" and dec["residual_gap_eur"] is None
    assert (
        dec["discount"] is None
    )  # no bridge on the rolling path: nothing is said about weights
    from services.results import objective_decomposition as od

    assert od._bridge(n, cb, cfg)["discount"] == "unknown"


def test_a_stamp_missing_a_period_reads_the_discount_unknown_not_a_500():
    """GK B F5: the network's periods changed after the solve; the bridge says it cannot tell."""
    from routers.projects import _solver_config_from_dict
    from services.results import objective_decomposition as od
    from services.solver.assumptions import SOLVE_DISCOUNT_META

    tid = "eh_h2_hub_staged"
    n = T.BUILDERS[tid]()
    cfg = _solver_config_from_dict(T.solver_config(tid))
    first = min(int(p) for p in n.investment_periods)
    n.meta[SOLVE_DISCOUNT_META] = {
        "ref_year": first,
        "real_rate": 0.07,
        "factors": {str(first): 3.0},
    }
    cb = {"total": 2.0e6, "opex": 1.0e6, "by_period": []}
    assert od._bridge(n, cb, cfg)["discount"] == "unknown"


@pytest.mark.live_solve
def test_the_stamp_is_json_scalars_survives_a_save_and_leaves_the_fingerprint(
    tmp_path, reset_backend
):
    import json

    import pypsa
    from services.solver.assumptions import (
        SOLVE_DISCOUNT_META,
        SOLVE_STRATEGY_META,
        solve_discount,
    )
    from services.workflow_tools import _network_input_fingerprint
    from services.pypsa_service import PyPSAService

    n, cfg = _solved("eh_h2_hub_staged")
    stamp = n.meta[SOLVE_DISCOUNT_META]
    assert json.loads(json.dumps(stamp)) == stamp
    assert set(stamp) == {"ref_year", "real_rate", "factors"}
    assert n.meta[SOLVE_STRATEGY_META] == "full"
    # The solve wrote these weights into ipw and the stamp from the one dict.
    from services.solver.assumptions import auto_discount_factors

    assert {int(p): f for p, f in stamp["factors"].items()} == auto_discount_factors(
        n, cfg
    )["factors"]

    ctx = PyPSAService.build_context()
    ctx.network = n
    ctx.solver_state["solver_config"] = cfg
    with_stamp = (
        _network_input_fingerprint(ctx),
        _network_input_fingerprint(ctx, exact=True),
    )
    saved = {k: n.meta.pop(k) for k in (SOLVE_DISCOUNT_META, SOLVE_STRATEGY_META)}
    without = (
        _network_input_fingerprint(ctx),
        _network_input_fingerprint(ctx, exact=True),
    )
    n.meta.update(saved)
    assert with_stamp == without

    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    assert solve_discount(m) == solve_discount(n)


def _assumed(tid, **over):
    """The template's solve set up (its transforms applied), and the basis it holds for the stamp."""
    from routers.projects import _solver_config_from_dict
    from services.solver.assumptions import (
        SOLVE_BASIS_CAPTURED,
        _apply_modelling_assumptions,
    )

    n = T.BUILDERS[tid]()
    n.snapshots.name = "snapshot"
    cfg = dataclasses.replace(_solver_config_from_dict(T.solver_config(tid)), **over)
    original = n.investment_period_weightings["objective"].copy()
    restore, captured = _apply_modelling_assumptions(n, cfg, lambda _m: None)
    return n, cfg, original, restore, captured[SOLVE_BASIS_CAPTURED]


def test_the_basis_is_held_with_the_discount_and_stamped_only_on_commit():
    from services.solver.assumptions import (
        SOLVE_BASIS_CAPTURED,
        SOLVE_DISCOUNT_META,
        SOLVE_STRATEGY_META,
        _apply_modelling_assumptions,
        commit_solve_basis,
    )

    # Rolling on a multi-period template runs the full horizon (GK B F2): the stamp says so.
    n, cfg, original, restore, basis = _assumed(
        "eh_h2_hub_staged", solve_strategy="rolling"
    )
    assert basis["strategy"] == "full"
    applied = {int(p): f for p, f in basis["discount"]["factors"].items()}
    assert applied == {
        int(p): float(v) for p, v in n.investment_period_weightings["objective"].items()
    }
    assert SOLVE_DISCOUNT_META not in n.meta and SOLVE_STRATEGY_META not in n.meta
    commit_solve_basis(n, {SOLVE_BASIS_CAPTURED: basis})
    assert n.meta[SOLVE_STRATEGY_META] == "full"
    assert n.meta[SOLVE_DISCOUNT_META] == basis["discount"]
    restore()
    assert n.investment_period_weightings["objective"].equals(original)
    # A later solve with the discount off holds a null discount: never the previous solve's.
    _r, captured = _apply_modelling_assumptions(
        n, dataclasses.replace(cfg, auto_discount_periods=False), lambda _m: None
    )
    commit_solve_basis(n, captured)
    assert n.meta[SOLVE_DISCOUNT_META] is None


def test_a_failure_part_way_restores_the_weights_and_holds_none(monkeypatch):
    from services.solver import assumptions

    real = assumptions.auto_discount_factors

    class _Partial(dict):
        """The factors with the last period's missing: the write fails after the first period."""

        def __getitem__(self, k):
            if k == max(self):
                raise KeyError(k)
            return super().__getitem__(k)

    def short(n, cfg):
        f = real(n, cfg)
        return {**f, "factors": _Partial(f["factors"])}

    monkeypatch.setattr(assumptions, "auto_discount_factors", short)
    n, _cfg, original, restore, basis = _assumed("eh_h2_hub_staged")
    assert basis["discount"] is None
    restore()
    assert n.investment_period_weightings["objective"].equals(original)


class _Cfg:
    def __init__(self, strategy, multi, sclopf=False):
        self.solve_strategy, self.multi_investment_periods, self.sclopf = (
            strategy,
            multi,
            sclopf,
        )


@pytest.mark.parametrize(
    "strategy, multi, sclopf, ran",
    [
        ("myopic", True, False, "myopic"),
        ("myopic", True, True, "myopic"),  # myopic subsumes SCLOPF
        ("myopic", False, False, "full"),  # GK B F1: myopic without periods runs full
        ("rolling", False, False, "rolling"),
        ("rolling", True, False, "full"),  # GK B F1: rolling with periods falls back
        ("rolling", False, True, "full"),  # GK B F1: rolling with SCLOPF runs full
        ("full", True, False, "full"),
    ],
)
def test_the_stamp_records_the_strategy_dispatched_not_the_one_asked(
    strategy, multi, sclopf, ran
):
    from services.solver.assumptions import dispatched_strategy

    assert dispatched_strategy(_Cfg(strategy, multi), sclopf=sclopf) == ran


@pytest.mark.live_solve
def test_a_failed_solve_leaves_the_previous_solves_stamp_with_its_results(
    monkeypatch, reset_backend
):
    """GK B F4: a solve that fails after its set-up leaves the last solve's results, so the stamp
    the status and the decomposition read must still be that solve's."""
    from services import solver_service as ss
    from services.dispatch_status import dispatch_status
    from services.pypsa_service import PyPSAService
    from services.solver.assumptions import solve_discount, solve_strategy

    n, cfg = _solved("eh_h2_hub_staged")
    before = (solve_discount(n), solve_strategy(n), n.objective)

    def boom(*_a, **_k):
        raise RuntimeError("solver died")

    # Raised after `_apply_modelling_assumptions` has priced the new rate.
    monkeypatch.setattr(ss._operational, "fallback_refusal", boom)
    status, _ = ss.run_simulation(
        dataclasses.replace(cfg, discount_rate=0.15),
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: None,
    )
    assert status == "error"
    assert dispatch_status(n) == "fresh"
    assert (solve_discount(n), solve_strategy(n), n.objective) == before


@pytest.mark.parametrize(
    "raw",
    [
        {"ref_year": "x", "real_rate": 0.05, "factors": {}},
        {"real_rate": 0.05},
        "nope",
        3,
    ],
)
def test_a_malformed_stamp_reads_unknown(raw, monkeypatch):
    from services.solver.assumptions import (
        SOLVE_DISCOUNT_META,
        SOLVE_STRATEGY_META,
        solve_discount,
    )

    n = T.BUILDERS["eh_h2_hub_staged"]()
    n.meta[SOLVE_DISCOUNT_META] = raw
    n.meta[SOLVE_STRATEGY_META] = "full"
    assert solve_discount(n) == "unknown"
    n.meta[SOLVE_STRATEGY_META] = "sideways"
    assert _status_with(monkeypatch, n) == ("unknown", None)


def test_the_stamp_takes_the_resolved_sclopf_flag(monkeypatch, reset_backend):
    """GK B M9: set-up decides the stamp's strategy on the SCLOPF flag the solve resolved, not the
    config's. Today the two never differ on a run that starts: the pre-flight refuses SCLOPF with
    no branch (`sclopf_no_branches`) and rolling with SCLOPF (`rolling_with_sclopf`). With those
    checks stubbed, a rolling single-period solve whose SCLOPF falls back to plain LOPF runs
    rolling, and the stamp must say so: the wiring holds if the pre-flight ever loosens."""
    from routers.projects import _solver_config_from_dict
    from services import solver_service as ss
    from services.pypsa_service import PyPSAService
    from services.solver.assumptions import SOLVE_BASIS_CAPTURED

    n = T.BUILDERS["eh_h2_hub"]()
    cfg = dataclasses.replace(
        _solver_config_from_dict(T.solver_config("eh_h2_hub")),
        sclopf=True,
        solve_strategy="rolling",
    )
    assert not cfg.multi_investment_periods
    monkeypatch.setattr(ss, "resolve_branch_outages", lambda *_a, **_k: [])
    monkeypatch.setattr(ss, "validate_for_run", lambda *_a, **_k: [])
    seen = {}
    real = ss._apply_modelling_assumptions

    def spy(*a, **k):
        seen["sclopf"] = k.get("sclopf", "not passed")
        restore, captured = real(*a, **k)
        seen["strategy"] = captured[SOLVE_BASIS_CAPTURED]["strategy"]
        return restore, captured

    def stop(*_a, **_k):
        raise RuntimeError("stop after set-up")

    monkeypatch.setattr(ss, "_apply_modelling_assumptions", spy)
    monkeypatch.setattr(ss._operational, "fallback_refusal", stop)
    PyPSAService.set_network(n)
    out = ss.run_simulation(
        cfg,
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: None,
    )
    assert out == ("error", "stop after set-up")
    assert seen == {"sclopf": False, "strategy": "rolling"}


def test_a_commit_without_a_held_basis_leaves_the_stamp():
    from services.solver.assumptions import (
        SOLVE_DISCOUNT_META,
        SOLVE_STRATEGY_META,
        commit_solve_basis,
    )

    n = T.BUILDERS["eh_h2_hub"]()
    n.meta[SOLVE_STRATEGY_META], n.meta[SOLVE_DISCOUNT_META] = "myopic", None
    commit_solve_basis(n, {})
    assert (n.meta[SOLVE_STRATEGY_META], n.meta[SOLVE_DISCOUNT_META]) == (
        "myopic",
        None,
    )


def test_set_up_without_a_resolved_flag_reads_the_configs_sclopf():
    """A caller that resolves no SCLOPF flag (the sizing loop) gets the config's: rolling with
    SCLOPF on runs the full horizon."""
    *_n, restore, basis = _assumed("eh_h2_hub", sclopf=True, solve_strategy="rolling")
    restore()
    assert basis["strategy"] == "full"
    *_n, restore, basis = _assumed("eh_h2_hub", sclopf=False, solve_strategy="rolling")
    restore()
    assert basis["strategy"] == "rolling"


def test_no_basis_when_the_network_cannot_be_read(monkeypatch):
    from routers import simulation
    from services import study_solve

    def unreadable():
        raise RuntimeError("no network")

    monkeypatch.setattr(
        simulation.PyPSAService, "get_network", staticmethod(unreadable)
    )
    monkeypatch.setattr(study_solve, "fallback", lambda *_a, **_k: None)
    monkeypatch.setitem(simulation._state, "objective", 1.0e6)
    st = simulation.get_status()
    assert (st["objective_basis"], st["objective_discounted_to"]) == (None, None)


# GK B3 on #2140: the Summary names the constant "existing assets' fixed cost". With a curtailment cost on a
# fixed-size generator the constant also carries the wrapper's implied capacity term, which is not that.
def _curtailment_network(curtailment_cost: float):
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    n.add("Bus", "b")
    n.add("Load", "d", bus="b", p_set=4.0)
    n.add("Generator", "pv", bus="b", p_nom=10.0, p_max_pu=[1.0, 0.5, 0.2], marginal_cost=0.0)
    n.add("Generator", "gas", bus="b", p_nom=2.0, p_nom_extendable=True, capital_cost=50.0, marginal_cost=30.0)
    n.generators["curtailment_cost"] = 0.0
    n.generators.loc["pv", "curtailment_cost"] = curtailment_cost
    return n


def _solve_with_wrapper(n):
    from services.solver.objective import _wrap_with_curtailment_cost

    fn = _wrap_with_curtailment_cost(n, None)
    n.optimize(solver_name="highs", extra_functionality=fn)


def test_with_a_curtailment_cost_only_the_baseline_is_existing_assets_fixed_cost():
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _curtailment_network(5.0)
    _solve_with_wrapper(n)
    dec = compute_objective_decomposition(n, None)
    baseline = n._baseline_objective_constant
    assert dec["n_objective_constant"] > baseline + 1.0, "the wrapper added its implied capacity term"
    assert dec["existing_asset_constant"] == pytest.approx(baseline)


def test_after_a_reload_the_curtailment_baseline_is_unknown_never_the_whole_constant():
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _curtailment_network(5.0)
    _solve_with_wrapper(n)
    del n._baseline_objective_constant  # a reloaded network carries no baseline
    assert compute_objective_decomposition(n, None)["existing_asset_constant"] is None


def test_with_no_curtailment_cost_the_whole_constant_is_existing_assets_fixed_cost():
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _curtailment_network(0.0)
    _solve_with_wrapper(n)
    dec = compute_objective_decomposition(n, None)
    assert dec["existing_asset_constant"] == pytest.approx(dec["n_objective_constant"])
