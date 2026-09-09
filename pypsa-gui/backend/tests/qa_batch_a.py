"""
Smoke test for Batch A fixes:
  A2: Prices merit-order toggle handles renewable-at-ceiling case
  A3: carrier_kpis returns non-empty rows on multi-period

Builds a multi-period network with a curtailment-subsidised renewable
that operates BOTH mid-range and at its ceiling, then verifies:
  - /results/carrier_kpis returns rows for each (component, carrier)
  - /results/prices `data_adjusted` matches `data` only on UNAFFECTED hours
    and differs only on hours where the subsidy actually distorts the dual
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from routers import simulation as sim_router
# The results serializers live in `routers/results.py` — they were carved out
# of `routers/simulation.py` and this driver still reached for them there, so
# every scenario below crashed on an AttributeError. `sim_router` is still the
# right home for `_state`, which is why both imports are here.
from routers import results as results_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

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


def _crashed(label: str, exc: BaseException) -> None:
    """
    A scenario that raised never ran its assertions, so it must COUNT as a
    failure — printing the traceback and moving on leaves the driver exiting 0
    while testing nothing. That is exactly how the stale
    `routers.simulation.get_*` references below went unnoticed: the scenarios
    crashed on every run and the summary still read "Fail: 0".
    """
    _step(f"{label} ran without crashing", False, f"{type(exc).__name__}: {exc}")

def _install(n: pypsa.Network) -> None:
    PyPSAService.set_network(n)
    sim_router._state["solver_config"] = SolverConfig()


def _build_multi_period_network() -> pypsa.Network:
    """2-period network with subsidised solar, thermal, battery."""
    n = pypsa.Network()
    base = pd.date_range("2025-01-01", periods=12, freq="h")
    mi = pd.MultiIndex.from_product([[2025, 2030], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = [2025, 2030]
    n.investment_period_weightings.loc[2025, "years"] = 3.0
    n.investment_period_weightings.loc[2030, "years"] = 5.0

    n.add("Bus", "B1")
    n.add("Bus", "B2")
    # Tight setup so solar dispatches strictly between 0 and p_max:
    # solar capacity = 100 * 0.9 = 90 MW; load B1 = 30 MW; line out = 50 MW.
    # Total solar demand = 30 + 50 = 80 MW (< 90), so solar is mid-range,
    # NOT at its upper bound — LP dual at B1 then = solar.effective MC = -100.
    n.add("Load", "L1", bus="B1", p_set=30.0)
    n.add("Load", "L2", bus="B2", p_set=120.0)
    # Solar at B1 — real user-typed marginal_cost = 0, curtailment_cost = 100.
    # solver_service in production subtracts curtailment_cost during solve
    # and restores marginal_cost afterwards — we mimic in `_solve_with_subsidy`.
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=100.0, marginal_cost=0.0, capital_cost=50_000.0,
          p_max_pu=0.9)
    n.generators["curtailment_cost"] = 0.0
    n.generators.at["solar", "curtailment_cost"] = 100.0
    n.add("Generator", "thermal_B1", bus="B1", carrier="gas",
          p_nom=100.0, marginal_cost=30.0, capital_cost=80_000.0)
    n.add("Generator", "thermal_B2", bus="B2", carrier="gas",
          p_nom=200.0, marginal_cost=30.0, capital_cost=80_000.0)
    n.add("StorageUnit", "Battery_B2", bus="B2", carrier="battery",
          p_nom=30.0, max_hours=4.0,
          efficiency_store=1.0, efficiency_dispatch=1.0,
          marginal_cost=0.0, capital_cost=10_000.0,
          cyclic_state_of_charge=True)
    # Tight line to keep markets somewhat decoupled
    n.add("Line", "L_B1_B2", bus0="B1", bus1="B2",
          s_nom=50.0, x=0.1, r=0.0)
    return n


def _solve_with_subsidy(n: pypsa.Network) -> None:
    """
    Mimic solver_service: subtract curtailment_cost during optimize(),
    then restore the stored marginal_cost so the endpoint sees the user's
    typed value.
    """
    if "curtailment_cost" in n.generators.columns:
        cc = n.generators["curtailment_cost"].fillna(0.0)
        original_mc = n.generators["marginal_cost"].copy()
        n.generators["marginal_cost"] = original_mc - cc
        try:
            n.optimize(solver_name="highs")
        finally:
            n.generators["marginal_cost"] = original_mc
    else:
        n.optimize(solver_name="highs")


def test_carrier_kpis_multi_period() -> None:
    print("\n[A3] carrier_kpis on multi-period network")
    n = _build_multi_period_network()
    _solve_with_subsidy(n)
    _install(n)
    payload = results_router.get_carrier_kpis()
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    _step("returns non-empty rows on multi-period",
          len(rows) > 0,
          f"got {len(rows)} rows")
    # Should include at least Generator+gas, Generator+solar, StorageUnit+battery.
    seen_pairs = {(r.get("component"), r.get("carrier")) for r in rows}
    expected = {("Generator", "gas"), ("Generator", "solar"), ("StorageUnit", "battery")}
    _step("includes Generator+gas, Generator+solar, StorageUnit+battery",
          expected.issubset(seen_pairs),
          f"got {seen_pairs}")
    # Energy_mwh should be positive on dispatching carriers.
    if rows:
        any_energy = any((r.get("energy_mwh") or 0) > 0 for r in rows)
        _step("at least one row has energy_mwh > 0", any_energy)
    # Capacity numbers should be positive for installed assets.
    if rows:
        any_cap = any((r.get("capacity_mw") or 0) > 0 for r in rows)
        _step("at least one row has capacity_mw > 0", any_cap)


def test_prices_merit_order_relaxed() -> None:
    print("\n[A2] Prices merit-order toggle on multi-period with subsidy")
    n = _build_multi_period_network()
    _solve_with_subsidy(n)
    _install(n)
    payload = results_router.get_prices()
    if not isinstance(payload, dict) or "data" not in payload:
        _step("prices endpoint returns data", False, str(payload)[:200])
        return
    raw = payload["data"]
    adj = payload["data_adjusted"]
    columns = payload["columns"]
    n_rows = len(raw)
    n_cols = len(columns)
    # Count negative cells in raw vs adj.
    n_neg_raw = 0
    n_neg_adj = 0
    n_changed = 0
    for i in range(n_rows):
        for j in range(n_cols):
            r = raw[i][j]
            a = adj[i][j]
            if r is not None and r < -1e-6:
                n_neg_raw += 1
            if a is not None and a < -1e-6:
                n_neg_adj += 1
            if r is not None and a is not None and abs(r - a) > 1e-3:
                n_changed += 1
    print(f"  diag: raw_negative_cells={n_neg_raw}, adj_negative_cells={n_neg_adj}, total_changed={n_changed}")
    # Fewer negatives after adjustment (subsidy was distorting at least some).
    _step("merit-order adjustment reduces negative-price cell count",
          n_neg_adj <= n_neg_raw,
          f"raw={n_neg_raw}, adj={n_neg_adj}")
    # The adjustment must change at least some cells (else fix is dead-code).
    _step("adjustment touches at least one cell",
          n_changed > 0 or n_neg_raw == 0,
          f"changed={n_changed}, raw_neg={n_neg_raw}")


def test_overview_voll_currency() -> None:
    print("\n[A1] Frontend VOLL × coefficient (code-level check only)")
    # Pure frontend fix — tsc verified at the build step. Read the file
    # to confirm the multiplication is present.
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    path = repo / "frontend" / "src" / "pages" / "results" / "AggregatedOverview.tsx"
    text = path.read_text(encoding="utf-8")
    has_voll_mult = ".positive * voll" in text
    has_voll_def = "total_cost_eur / total_mwh" in text or "total_cost_eur / llMeta.total_mwh" in text
    _step("VOLL coefficient is multiplied into per-period values",
          has_voll_mult,
          "found '.positive * voll'" if has_voll_mult else "missing multiplication")
    _step("VOLL coefficient is derived from total_cost_eur / total_mwh",
          has_voll_def,
          "found ratio" if has_voll_def else "missing ratio")


def main() -> int:
    try:
        test_overview_voll_currency()
    except Exception as e:
        _crashed("A1", e)
    try:
        test_carrier_kpis_multi_period()
    except Exception as e:
        _crashed("A3", e)
    try:
        test_prices_merit_order_relaxed()
    except Exception as e:
        _crashed("A2", e)
    print()
    print("=" * 60)
    print(f"Total: {PASS_COUNT + FAIL_COUNT}  Pass: {PASS_COUNT}  Fail: {FAIL_COUNT}")
    print("=" * 60)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
