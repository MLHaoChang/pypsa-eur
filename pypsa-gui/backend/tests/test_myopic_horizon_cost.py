"""
A myopic run must report the SAME system cost as the Economics tab.

Two defects are pinned here, both found by solving a network whose cost can be
computed by hand and comparing every surface that reports it.

D1 — the reported objective was the sum of the per-period LP objectives.
     Capacity frozen by an earlier period is `p_nom_extendable=False`, PyPSA
     charges CAPEX only for extendables, and the channel that should carry a
     non-extendable's capex (`n.objective_constant`) is IDENTICALLY ZERO under
     `multi_investment_periods=True` — PyPSA's `define_objective` builds the
     multi-invest constant but appends it to `terms` only in the single-period
     branch. So each asset's CAPEX was counted once, in its build period, and
     never for the rest of its life: -60.6% on the analytic network below.
     With `lf_aggregate_future=True` the error flips sign (the lookahead
     window's future-period OPEX is counted twice), so no constant correction
     factor could have papered over it.

D2 — the solve-log summary line reported `n.objective`, which after a myopic
     loop is the FINAL period's LP alone.

The perfect-foresight control in each test is the point: it must agree on every
surface, so a failure here is myopic-specific and not a change in the cost
basis itself.
"""
from __future__ import annotations

import pathlib
import tempfile

import pandas as pd
import pypsa
import pytest

from routers.simulation import _compute_run_objective
from services.cost_totals import horizon_system_cost
from services.solver_service import SolverConfig, _run_myopic_foresight

PERIODS = [2030, 2031, 2032]

# Hand-computed for the network below:
#   capex = 100 EUR/MW/a x 100 MW x (1 + 1 + 1 period weights) = 30 000
#   opex  = 100 MWh x 10 EUR/MWh x 3 periods                   =  3 000
TRUE_HORIZON_COST = 33_000.0
# What the old per-period-LP sum produced: CAPEX only in the build period.
OLD_BROKEN_SUM = 13_000.0


def _analytic_network() -> pypsa.Network:
    """One bus, three periods, one snapshot each, every weight 1.0."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [PERIODS, pd.date_range("2030-01-01", periods=1, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = PERIODS
    n.investment_period_weightings["years"] = 1.0
    n.investment_period_weightings["objective"] = 1.0
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=100.0)
    n.add("Generator", "g", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=100.0, marginal_cost=10.0, p_nom_max=10_000.0)
    return n


def _cfg(**kw) -> SolverConfig:
    return SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                        investment_periods=PERIODS, voll=0.0, **kw)


def _solve_myopic(n: pypsa.Network, cfg: SolverConfig) -> tuple[str, str]:
    tmp = pathlib.Path(tempfile.mktemp(suffix=".log"))
    tmp.touch()
    try:
        status, condition, _ = _run_myopic_foresight(
            n, cfg, lambda m: None, merged_solver_options={}, extra_fn=None,
            tmp_log=tmp, stop_event=None, iteration_undo=[],
        )
        return status, condition
    finally:
        tmp.unlink(missing_ok=True)


def test_myopic_objective_is_the_true_horizon_cost():
    n = _analytic_network()
    cfg = _cfg()
    assert _solve_myopic(n, cfg) == ("ok", "optimal")

    reported = _compute_run_objective(n, cfg)
    assert reported == pytest.approx(TRUE_HORIZON_COST, rel=1e-6), (
        f"myopic reported {reported:,.1f}, hand-computed cost is "
        f"{TRUE_HORIZON_COST:,.1f}"
    )


def test_myopic_objective_is_not_the_per_period_lp_sum():
    """
    Guards the specific regression: summing `_myopic_period_objectives` is the
    wrong basis, so the reported value must NOT equal that sum.
    """
    n = _analytic_network()
    cfg = _cfg()
    _solve_myopic(n, cfg)

    entries = getattr(n, "_myopic_period_objectives", [])
    assert entries, "the per-period accumulator should still be populated"
    lp_sum = sum(v + c for _, v, c in entries)
    assert lp_sum == pytest.approx(OLD_BROKEN_SUM, rel=1e-6), (
        "the LP-sum itself changed; re-derive the expected numbers"
    )
    # Every per-period constant is 0 — the PyPSA multi-invest behaviour that
    # makes the LP sum unusable. If this ever becomes non-zero, PyPSA fixed
    # `define_objective` and this whole approach can be revisited.
    assert all(c == 0.0 for _, _, c in entries)

    assert _compute_run_objective(n, cfg) != pytest.approx(lp_sum, rel=1e-6)


def test_perfect_foresight_objective_is_unchanged():
    """
    The control. A single LP already prices the whole horizon, so the LP total
    and the statistics total agree — and the fix must not disturb it.
    """
    n = _analytic_network()
    n.optimize(solver_name="highs", multi_investment_periods=True)

    assert not getattr(n, "_myopic_period_objectives", None), (
        "a full-horizon solve must not populate the myopic accumulator"
    )
    reported = _compute_run_objective(n, _cfg())
    assert reported == pytest.approx(TRUE_HORIZON_COST, rel=1e-6)
    assert reported == pytest.approx(float(n.objective), rel=1e-6)


def test_myopic_and_perfect_foresight_are_priced_on_the_same_basis():
    """
    The property that makes the number worth showing: two solve strategies on
    the SAME network must be comparable. Here both build 100 MW, so the two
    totals must match exactly.
    """
    n_my = _analytic_network()
    cfg = _cfg()
    _solve_myopic(n_my, cfg)
    n_pf = _analytic_network()
    n_pf.optimize(solver_name="highs", multi_investment_periods=True)

    assert float(n_my.generators.p_nom_opt.iloc[0]) == pytest.approx(
        float(n_pf.generators.p_nom_opt.iloc[0]), rel=1e-6)
    assert _compute_run_objective(n_my, cfg) == pytest.approx(
        _compute_run_objective(n_pf, cfg), rel=1e-6)


def test_limited_foresight_does_not_double_count_the_lookahead():
    """
    With `lf_aggregate_future=True` each iteration's LP also spans representative
    future-period snapshots. Those periods are then solved in their own
    iterations, so the per-period LP sum counted their OPEX twice and read HIGH
    — the opposite direction from the plain myopic case. The statistics basis is
    immune because it prices the solved network, not the LPs.
    """
    n = _analytic_network()
    cfg = _cfg(lf_aggregate_future=True)
    assert _solve_myopic(n, cfg) == ("ok", "optimal")

    reported = _compute_run_objective(n, cfg)
    assert reported == pytest.approx(TRUE_HORIZON_COST, rel=1e-6)


def test_horizon_system_cost_returns_none_on_an_unsolved_network():
    """Callers fall back rather than report a confident zero."""
    assert horizon_system_cost(pypsa.Network(), _cfg()) is None
