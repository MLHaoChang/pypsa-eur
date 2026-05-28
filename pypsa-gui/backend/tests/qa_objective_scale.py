"""
Smoke test: user_objective_scale = s should produce the SAME reported
n.objective and dispatch as scale = 1.0 (the LP optimum is invariant).

Builds a tiny network, solves it twice with different scales, asserts
results match within numerical tolerance.
"""
from __future__ import annotations

import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import queue

import pandas as pd
import pypsa
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

PASS_COUNT = 0
FAIL_COUNT = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _build_tiny_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=6, freq="h"))
    n.snapshot_weightings.loc[:, ["objective", "generators", "stores"]] = 1460.0
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=120.0)
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=50.0, capital_cost=80_000.0)
    return n


def _solve_with_scale(scale: float) -> tuple[float, pd.DataFrame, pd.DataFrame]:
    n = _build_tiny_network()
    cfg = SolverConfig(user_objective_scale=scale)
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop_event = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop_event, log_q)
    if status not in ("ok", "optimal"):
        raise RuntimeError(f"solve failed: status={status} cond={condition}")
    return float(n.objective), n.generators_t.p.copy(), n.buses_t.marginal_price.copy()


def main() -> int:
    print("[scale = 1.0]")
    obj_a, p_a, mp_a = _solve_with_scale(1.0)
    print(f"  objective = €{obj_a:,.2f}")
    print(f"  marginal_price[B1, t0] = {mp_a.iloc[0, 0]:.4f} €/MWh")
    print()
    print("[scale = 1e-6]")
    obj_b, p_b, mp_b = _solve_with_scale(1e-6)
    print(f"  objective = €{obj_b:,.2f}")
    print(f"  marginal_price[B1, t0] = {mp_b.iloc[0, 0]:.4f} €/MWh")
    print()
    print("[scale = 1e3]")
    obj_c, p_c, mp_c = _solve_with_scale(1e3)
    print(f"  objective = €{obj_c:,.2f}")
    print(f"  marginal_price[B1, t0] = {mp_c.iloc[0, 0]:.4f} €/MWh")
    print()

    def _close(a: float, b: float, rel: float = 1e-6, abs_eps: float = 0.01) -> bool:
        if abs(a - b) <= abs_eps:
            return True
        return abs(a - b) / max(abs(b), 1e-9) <= rel

    _step("objective at scale=1e-6 matches scale=1",
          _close(obj_a, obj_b),
          f"|{obj_a:,.2f} - {obj_b:,.2f}|")
    _step("objective at scale=1e3 matches scale=1",
          _close(obj_a, obj_c),
          f"|{obj_a:,.2f} - {obj_c:,.2f}|")
    _step("dispatch at scale=1e-6 matches scale=1",
          ((p_a - p_b).abs() < 1e-3).all().all(),
          f"max delta = {(p_a - p_b).abs().max().max():.6f}")
    _step("marginal_price at scale=1e-6 matches scale=1",
          ((mp_a - mp_b).abs() < 1e-3).all().all(),
          f"max delta = {(mp_a - mp_b).abs().max().max():.6f}")
    _step("marginal_price at scale=1e3 matches scale=1",
          ((mp_a - mp_c).abs() < 1e-3).all().all(),
          f"max delta = {(mp_a - mp_c).abs().max().max():.6f}")
    print()
    print("=" * 60)
    print(f"Total: {PASS_COUNT + FAIL_COUNT}  Pass: {PASS_COUNT}  Fail: {FAIL_COUNT}")
    print("=" * 60)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
