"""
Limited-foresight solves (myopic + rolling) must NOT report 'optimal' when a
period / window is actually infeasible.

Regression for two masking bugs:
  * MYOPIC — periods with no NEW extendable assets SKIPPED the LP entirely
    ("capacity already determined, skipping LP"), so a period whose frozen
    fleet can't serve its load was reported as a clean "optimal" run (and got
    no dispatch). The fix runs an operational dispatch solve for those periods.
  * ROLLING — `optimize_with_rolling_horizon` LOGS a warning + CONTINUES on a
    failed window (never raises); the wrapper hardcoded ("ok","optimal"). The
    fix captures per-window failures via `_RollingWindowFailureCatcher`.
"""
from __future__ import annotations

import logging
import pathlib
import tempfile

import pandas as pd
import pypsa

from services.solver_service import (
    SolverConfig,
    _RollingWindowFailureCatcher,
    _run_myopic_foresight,
)


def _two_period_network(load: float, gen_pnom: float) -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=3, freq="h")],
        names=["period", "timestep"],
    )
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=load)
    # Non-extendable → every period has "no new extendable assets" → exercises
    # the operational-solve branch the fix added.
    n.add("Generator", "g", bus="B", carrier="gas",
          p_nom=gen_pnom, marginal_cost=50.0, p_nom_extendable=False)
    return n


def _run_myopic(n: pypsa.Network):
    cfg = SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                       investment_periods=[2030, 2040], voll=0.0)
    tmp = pathlib.Path(tempfile.mktemp(suffix=".log"))
    tmp.touch()
    try:
        return _run_myopic_foresight(
            n, cfg, lambda m: None, merged_solver_options={}, extra_fn=None,
            tmp_log=tmp, stop_event=None, iteration_undo=[],
        )
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def test_myopic_infeasible_period_reports_failure():
    # Demand 1000 >> firm capacity 10, no extendable, no VOLL slack → every
    # period is operationally infeasible. Must NOT report optimal (the bug).
    n = _two_period_network(load=1000.0, gen_pnom=10.0)
    status, condition, _ = _run_myopic(n)
    assert status != "ok", f"infeasible myopic reported success: {status}/{condition}"
    assert "infeasible" in str(condition).lower()


def test_myopic_feasible_no_extendable_produces_dispatch():
    # Plenty of firm capacity → feasible. The no-extendable periods must SOLVE
    # operationally (not skip) → report optimal AND produce dispatch for every
    # snapshot (the skip used to leave non-expansion periods with no dispatch).
    n = _two_period_network(load=100.0, gen_pnom=2000.0)
    status, condition, _ = _run_myopic(n)
    assert (status, condition) == ("ok", "optimal"), f"{status}/{condition}"
    assert int(n.generators_t.p.notna().all(axis=1).sum()) == len(n.snapshots)


def test_rolling_failure_catcher_captures_infeasible_windows():
    # An infeasible flat network solved rolling: PyPSA logs a per-window failure
    # and continues. The catcher must record it so the caller can fail the run
    # instead of hardcoding "optimal".
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=12, freq="h"))
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=1000.0)
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=10.0, marginal_cost=50.0)
    catcher = _RollingWindowFailureCatcher()
    lg = logging.getLogger("pypsa.optimization.abstract")
    lg.addHandler(catcher)
    try:
        n.optimize.optimize_with_rolling_horizon(
            horizon=4, overlap=1, solver_name="highs", log_to_console=False,
        )
    finally:
        lg.removeHandler(catcher)
    assert len(catcher.failures) > 0, "catcher missed the infeasible windows"
    assert catcher.failures[0][1] == "infeasible", catcher.failures[0]


def test_rolling_catcher_is_thread_scoped():
    # The PyPSA logger is process-global; a concurrent solve on another thread
    # must NOT have its window failures attributed to this catcher.
    catcher = _RollingWindowFailureCatcher()  # _tid = this thread
    fail_msg = "Optimization failed with status %s and condition %s"

    # A record emitted from THIS thread is captured (LogRecord stamps .thread
    # with the creating thread's ident automatically).
    same = logging.LogRecord("pypsa.optimization.abstract", logging.WARNING,
                             "x", 0, fail_msg, ("warning", "infeasible"), None)
    catcher.emit(same)
    assert catcher.failures == [("warning", "infeasible")]

    # A record stamped with a DIFFERENT thread ident (a concurrent solve) is
    # ignored — no cross-attribution.
    other = logging.LogRecord("pypsa.optimization.abstract", logging.WARNING,
                              "x", 0, fail_msg, ("warning", "infeasible"), None)
    other.thread = catcher._tid + 1
    catcher.emit(other)
    assert len(catcher.failures) == 1, "captured a record from another thread"
