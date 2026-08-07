"""
Verify the four fixes shipped 2026-08-05 against a LIVE backend.

Not under tests/ on purpose: it builds a network, runs real LP solves, and
would slow the unit suite for no benefit. Run it directly:

    .pixi/envs/test/bin/python -u pypsa-gui/backend/smoke/verify_myopic_ui.py

`-u` is load-bearing — without unbuffered stdout the prints never flush to a
background task's output file and a working run looks like a silent hang.
"""
import sys

import pandas as pd
import pypsa

sys.path.insert(0, "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/backend")

from routers.results import get_cost_breakdown
from routers.simulation import _compute_run_objective
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation
from services.validation_service import validate_for_run

PERIODS = [2030, 2035, 2040]
RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str) -> None:
    RESULTS.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {detail}", flush=True)


def build() -> pypsa.Network:
    """3-period network with 44% demand growth and one extendable generator."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [PERIODS, pd.date_range("2030-01-01", periods=6, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = PERIODS
    n.investment_period_weightings["years"] = 5.0
    n.investment_period_weightings["objective"] = 5.0
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    growth = {2030: 1.0, 2035: 1.2, 2040: 1.44}
    n.add("Load", "L", bus="B", p_set=pd.Series(
        [100.0 * growth[p] for p, _ in n.snapshots], index=n.snapshots))
    n.add("Generator", "GAS", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=25_000.0, marginal_cost=85.0, p_nom_max=5_000.0)
    n.add("Generator", "VOLL", bus="B", carrier="gas", p_nom=10_000.0,
          marginal_cost=5_000.0)
    return n


def install(n: pypsa.Network) -> None:
    PyPSAService.set_network(n)
    with PyPSAService._registry_lock:
        for k in [k for k in PyPSAService._contexts if k.startswith("scratch:")]:
            PyPSAService._contexts.pop(k, None)


def main() -> int:
    import queue
    import threading

    cfg = SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                       investment_periods=PERIODS, voll=0.0)

    # FIX 2 — the capacity-lock warning must fire BEFORE the solve.
    n = build()
    codes = [i.code for i in validate_for_run(n, cfg)]
    record("fix2 capacity-lock warning",
           "myopic_capacity_locked_after_first_period" in codes,
           f"codes={codes}")

    # Solve it for real, through run_simulation (not the myopic driver alone).
    install(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q)
    record("solve completed", status in ("ok", "optimal"), f"{status}/{condition}")

    # FIX 1 — status bar must equal the Economics tab.
    install(n)
    status_bar = _compute_run_objective(n, cfg)
    cb = get_cost_breakdown()
    economics = float(cb["total"]) if isinstance(cb, dict) else float("nan")
    agree = (economics == 0 and status_bar == 0) or abs(
        status_bar - economics) <= abs(economics) * 1e-6
    record("fix1 status bar == Economics", agree,
           f"status_bar={status_bar:,.0f} economics={economics:,.0f}")

    # FIX 3 — build periods recorded so the per-period chart is non-empty.
    vr = (n.meta or {}).get("vintage_results", {}).get("Generator", {})
    entry = vr.get("GAS")
    years = [p["build_year"] for p in entry["periods"]] if entry else []
    record("fix3 build period recorded", bool(years), f"build_years={years}")
    record("fix3 build_year column untouched",
           float(n.generators.at["GAS", "build_year"]) == 0.0,
           f"build_year={float(n.generators.at['GAS', 'build_year'])}")

    print("\n" + "=" * 60, flush=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
