"""
Smoke tests for /results/emissions per-period reporting.

Scenarios:
  1. Multi-period network with years=[3, 5] — confirm horizon total ≈
     Σ(by_period[].total_tCO2) and that each period's total is correct
     (= years × per-snapshot-year emissions).
  2. Per-period CO2 cap — assert `caps[]` includes one entry with
     scope=period and the correct investment_period.
  3. Horizon-wide cap — assert one entry with scope=horizon.
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from routers import simulation as sim_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

PASS = 0
FAIL = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _approx(a: float, b: float, rel: float = 0.01, abs_eps: float = 0.5) -> bool:
    if abs(a - b) <= abs_eps:
        return True
    if b == 0:
        return abs(a) <= abs_eps
    return abs(a - b) / max(abs(b), 1e-9) <= rel


def _build_multi_period() -> pypsa.Network:
    """
    2-period network, 6 hourly snapshots per period.

    Layout: 1 bus, 1 load=100MW, 1 gas generator (intensity 0.2 t/MWh primary,
    efficiency 0.5 → output intensity 0.4 tCO2/MWh_out). Years: 3 for 2025,
    5 for 2030.

    Expected per-period emissions:
      Period 2025: 100 MW × 6 h × 1 hour_weight × 3 years × 0.4 = 720 tCO2
      Period 2030: 100 MW × 6 h × 1 hour_weight × 5 years × 0.4 = 1200 tCO2
      Horizon total: 1920 tCO2
    """
    n = pypsa.Network()
    base = pd.date_range("2025-01-01", periods=6, freq="h")
    mi = pd.MultiIndex.from_product([[2025, 2030], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = [2025, 2030]
    n.investment_period_weightings.loc[2025, "years"] = 3.0
    n.investment_period_weightings.loc[2030, "years"] = 5.0
    # Snapshot weights default to 1.0 — no representative-week scaling.

    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=50.0, capital_cost=0.0, efficiency=0.5)
    return n


def _install(n: pypsa.Network) -> None:
    PyPSAService.set_network(n)
    sim_router._state["solver_config"] = SolverConfig()


def _solve(n: pypsa.Network) -> None:
    cfg = SolverConfig(multi_investment_periods=True)
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    if status not in ("ok", "optimal"):
        raise RuntimeError(f"solve failed: {status} / {condition}")


def test_multi_period_emissions() -> None:
    print("\n[1] Multi-period emissions: by_period totals + years scaling")
    n = _build_multi_period()
    _solve(n)
    _install(n)
    payload = sim_router.get_emissions()
    if not isinstance(payload, dict):
        _step("emissions endpoint returns dict", False, str(payload)[:120])
        return
    print(f"  diag: total_tCO2={payload['total_tCO2']:.1f}, periods={len(payload['by_period'])}")
    _step("is_multi_period flag set", payload["is_multi_period"] is True)
    _step("by_period contains 2 entries", len(payload["by_period"]) == 2,
          f"got {[p['period'] for p in payload['by_period']]}")

    by_p = {p["period"]: p["total_tCO2"] for p in payload["by_period"]}
    # Expected: 100 MW × 6 hours × 0.4 tCO2/MWh_out = 240 tCO2 per period-year
    # Period 2025 (3 years): 240 × 3 = 720
    # Period 2030 (5 years): 240 × 5 = 1200
    _step("period 2025 total = 720 tCO2",
          _approx(by_p.get(2025, 0), 720.0),
          f"got {by_p.get(2025)}")
    _step("period 2030 total = 1200 tCO2",
          _approx(by_p.get(2030, 0), 1200.0),
          f"got {by_p.get(2030)}")
    _step("horizon total = sum of per-period",
          _approx(payload["total_tCO2"], 720.0 + 1200.0),
          f"got {payload['total_tCO2']}")


def test_horizon_co2_cap() -> None:
    print("\n[2] Horizon-wide CO2 cap")
    n = _build_multi_period()
    # Use existing-attribute syntax — global_constraints expects an index name.
    n.add("GlobalConstraint", "co2_cap_horizon",
          type="primary_energy", carrier_attribute="co2_emissions",
          sense="<=", constant=10_000.0)
    _solve(n)
    _install(n)
    payload = sim_router.get_emissions()
    caps = payload.get("caps", []) if isinstance(payload, dict) else []
    _step("caps[] contains the horizon-wide cap",
          any(c["scope"] == "horizon" for c in caps),
          f"caps={[(c['name'], c['scope']) for c in caps]}")


def test_per_period_co2_cap() -> None:
    print("\n[3] Per-period CO2 cap (feasible — add cheap solar backup)")
    n = _build_multi_period()
    # Add cheap solar so the LP can curtail gas to meet a tight cap.
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=500.0, marginal_cost=0.0, capital_cost=0.0, p_max_pu=1.0)
    # Cap period 2030 at 800 tCO2 (below the natural 1200) — solar fills
    # the gap so the LP stays feasible.
    n.add("GlobalConstraint", "co2_cap_2030",
          type="primary_energy", carrier_attribute="co2_emissions",
          sense="<=", constant=800.0, investment_period=2030)
    _solve(n)
    _install(n)
    payload = sim_router.get_emissions()
    caps = payload.get("caps", []) if isinstance(payload, dict) else []
    per_period_caps = [c for c in caps if c["scope"] == "period"]
    _step("caps[] contains a period-scoped cap",
          len(per_period_caps) >= 1,
          f"got caps={caps}")
    if per_period_caps:
        cap = per_period_caps[0]
        _step("per-period cap targets period 2030",
              cap["investment_period"] == 2030,
              f"got investment_period={cap['investment_period']}")
        _step("per-period cap has cap_tCO2 = 800",
              _approx(cap["cap_tCO2"] or 0, 800.0),
              f"got cap_tCO2={cap['cap_tCO2']}")
        used = cap.get("used_tCO2", 0)
        # Cap binds at 800 — gas dispatch curtailed to fit within budget.
        _step("per-period cap shows used ≤ 800 (cap is satisfied)",
              used <= 800.5,
              f"got used_tCO2={used}")


def main() -> int:
    for fn in (test_multi_period_emissions, test_horizon_co2_cap, test_per_period_co2_cap):
        try:
            fn()
        except Exception as e:
            print(f"  scenario crashed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
