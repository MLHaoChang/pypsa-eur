"""
`services.cost_totals.horizon_system_cost` and `/results/cost_breakdown` must
report the same total.

They are separate implementations on purpose: the endpoint resolves its network
and config from ambient state (`PyPSAService.get_network()`,
`_state["solver_config"]`), while the solve-queue dispatcher has to price an
explicit background network with that job's config, outside
`solving_context(ctx)`. Two implementations of one number drift, so this file is
the fence: change the cost basis in one and these tests fail.

Covers the three network shapes whose statistics frames differ in structure —
flat, multi-period perfect foresight, multi-period myopic — because the
period-level detection and years-weighting is where they would diverge.
"""
from __future__ import annotations

import pathlib
import tempfile

import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from routers.results import get_cost_breakdown
from services.cost_totals import horizon_system_cost
from services.solver_service import SolverConfig, _run_myopic_foresight

PERIODS = [2030, 2035, 2040]


def _flat_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=100.0)
    n.add("Generator", "g", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=120.0, marginal_cost=10.0, p_nom_max=10_000.0)
    n.add("Generator", "firm", bus="B", carrier="gas", p_nom=50.0,
          marginal_cost=200.0)
    return n


def _multi_period_network() -> pypsa.Network:
    """Uneven period weights so a missing years-scaling cannot pass by luck."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [PERIODS, pd.date_range("2030-01-01", periods=6, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = PERIODS
    n.investment_period_weightings["years"] = [5.0, 5.0, 3.0]
    n.investment_period_weightings["objective"] = [5.0, 5.0, 3.0]
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Carrier", "solar")
    growth = {2030: 1.0, 2035: 1.25, 2040: 1.5}
    n.add("Load", "L", bus="B", p_set=pd.Series(
        [100.0 * growth[p] for p, _ in n.snapshots], index=n.snapshots))
    n.add("Generator", "g", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=120.0, marginal_cost=10.0, p_nom_max=10_000.0)
    n.add("Generator", "pv", bus="B", carrier="solar", p_nom_extendable=True,
          capital_cost=300.0, marginal_cost=0.1, p_nom_max=10_000.0)
    n.add("Generator", "firm", bus="B", carrier="gas", p_nom=50.0,
          marginal_cost=200.0)
    return n


def _myopic_cfg() -> SolverConfig:
    return SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                        investment_periods=PERIODS, voll=0.0)


def _solve_myopic(n: pypsa.Network, cfg: SolverConfig) -> None:
    tmp = pathlib.Path(tempfile.mktemp(suffix=".log"))
    tmp.touch()
    try:
        status, condition, _ = _run_myopic_foresight(
            n, cfg, lambda m: None, merged_solver_options={}, extra_fn=None,
            tmp_log=tmp, stop_event=None, iteration_undo=[])
        assert (status, condition) == ("ok", "optimal")
    finally:
        tmp.unlink(missing_ok=True)


def _endpoint_total(n, install_network, cfg: SolverConfig) -> float:
    install_network(n)
    sim_router._state["solver_config"] = cfg
    payload = get_cost_breakdown()
    assert isinstance(payload, dict), (
        f"cost_breakdown returned {getattr(payload, 'status_code', payload)!r}, "
        "not a payload — the network is not being seen as solved"
    )
    return float(payload["total"])


def test_flat_network_totals_agree(install_network):
    n = _flat_network()
    n.optimize(solver_name="highs")
    cfg = SolverConfig()
    assert horizon_system_cost(n, cfg) == pytest.approx(
        _endpoint_total(n, install_network, cfg), rel=1e-9)


def test_multi_period_perfect_foresight_totals_agree(install_network):
    n = _multi_period_network()
    n.optimize(solver_name="highs", multi_investment_periods=True)
    cfg = SolverConfig(multi_investment_periods=True, investment_periods=PERIODS)
    assert horizon_system_cost(n, cfg) == pytest.approx(
        _endpoint_total(n, install_network, cfg), rel=1e-9)


def test_multi_period_myopic_totals_agree(install_network):
    n = _multi_period_network()
    cfg = _myopic_cfg()
    _solve_myopic(n, cfg)
    assert horizon_system_cost(n, cfg) == pytest.approx(
        _endpoint_total(n, install_network, cfg), rel=1e-9)


def test_the_reported_objective_matches_the_economics_tab_for_myopic(install_network):
    """
    The end-to-end statement of the bug this fixes: the number in the status bar
    and the number on the Economics tab are the same number.
    """
    n = _multi_period_network()
    cfg = _myopic_cfg()
    _solve_myopic(n, cfg)

    status_bar = sim_router._compute_run_objective(n, cfg)
    economics = _endpoint_total(n, install_network, cfg)
    assert status_bar == pytest.approx(economics, rel=1e-9), (
        f"status bar {status_bar:,.0f} vs Economics {economics:,.0f}"
    )


def test_uneven_period_weights_are_actually_applied(install_network):
    """
    Sanity floor for the shared basis: with years=[5,5,3] the horizon total must
    exceed the unweighted statistics sum, so neither implementation can be
    silently dropping the years-weighting and still pass the agreement tests.
    """
    n = _multi_period_network()
    n.optimize(solver_name="highs", multi_investment_periods=True)
    cfg = SolverConfig(multi_investment_periods=True, investment_periods=PERIODS)

    total = horizon_system_cost(n, cfg)
    unweighted = float(n.statistics().sum().sum())
    assert total > unweighted * 2.0, (
        f"years-weighting looks absent: total={total:,.0f} vs raw sum "
        f"{unweighted:,.0f}"
    )
