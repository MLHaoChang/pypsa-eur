"""
The solve log's Summary line must not report the final myopic period's LP as
if it were the whole horizon.

`run_simulation` ends with `Summary — objective=…`. After a myopic loop,
`n.objective` holds only the LAST iteration's LP value, so that line read ~-75%
of the true horizon cost — a third number, disagreeing with both the status bar
and the Economics tab for one solve.

This goes through `run_simulation` rather than `_run_myopic_foresight` on
purpose: the Summary line lives in the outer driver, so a test that called the
myopic function directly would not exercise it at all.
"""
from __future__ import annotations

import queue
import re
import threading

import pandas as pd
import pypsa
import pytest

from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

PERIODS = [2030, 2031, 2032]
TRUE_HORIZON_COST = 33_000.0
FINAL_PERIOD_LP_ONLY = 1_000.0


def _network() -> pypsa.Network:
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


def _drain(q: queue.SimpleQueue) -> list[str]:
    out: list[str] = []
    while True:
        try:
            out.append(str(q.get_nowait()))
        except queue.Empty:
            return out


def _summary_line(lines: list[str]) -> str:
    hits = [ln for ln in lines if "Summary — objective=" in ln]
    assert hits, f"no Summary line in {len(lines)} log lines"
    return hits[-1]


def _run(cfg: SolverConfig, n: pypsa.Network) -> list[str]:
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q)
    assert status in ("ok", "optimal"), f"{status}/{condition}"
    return _drain(log_q)


def _euros(line: str) -> float:
    m = re.search(r"€([\d,]+\.\d{2})", line)
    assert m, f"no euro amount in {line!r}"
    return float(m.group(1).replace(",", ""))


def test_myopic_summary_reports_the_horizon_total_not_the_last_period():
    cfg = SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                       investment_periods=PERIODS, voll=0.0)
    line = _summary_line(_run(cfg, _network()))

    assert _euros(line) == pytest.approx(TRUE_HORIZON_COST, rel=1e-6), line
    assert _euros(line) != pytest.approx(FINAL_PERIOD_LP_ONLY, rel=1e-6)
    # Labelled, so the per-period LP value stays identifiable in the log.
    assert "myopic horizon total" in line, line


def test_full_horizon_summary_keeps_the_lp_wording():
    """The control: a single LP prices the horizon, so nothing should change."""
    cfg = SolverConfig(solve_strategy="full", multi_investment_periods=True,
                       investment_periods=PERIODS, voll=0.0)
    line = _summary_line(_run(cfg, _network()))

    assert _euros(line) == pytest.approx(TRUE_HORIZON_COST, rel=1e-6), line
    assert "solver=" in line and "constant=" in line, line
    assert "myopic" not in line, line


def test_a_full_run_after_a_myopic_run_is_not_still_flagged_myopic():
    """
    `_myopic_period_objectives` is the marker every downstream consumer keys off
    ("did this run go myopic?"), and only the myopic driver used to write it —
    so re-solving the SAME network full-horizon left the previous run's marker
    in place and the full run was reported through the myopic path.
    """
    n = _network()
    _run(SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                      investment_periods=PERIODS, voll=0.0), n)
    assert getattr(n, "_myopic_period_objectives", None), "myopic marker expected"

    line = _summary_line(_run(
        SolverConfig(solve_strategy="full", multi_investment_periods=True,
                     investment_periods=PERIODS, voll=0.0), n))
    assert not getattr(n, "_myopic_period_objectives", None), (
        "the full-horizon run must clear the previous run's myopic marker"
    )
    assert "myopic" not in line, line
    assert _euros(line) == pytest.approx(TRUE_HORIZON_COST, rel=1e-6), line
