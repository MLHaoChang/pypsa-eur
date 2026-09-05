"""
Smoke test for Batch B fixes.

B1: cost.capex_expansion non-zero on networks with expansion
B2: DispatchStack tooltip sign (code-level check)
B3: /results/curtailment filtered to renewables (no thermal headroom)
B4: get_line_duals applies investment_period_weightings.years
B5: get_price_drivers tags high-price load_shedding correctly
B6: emissions endpoint includes storage units with co2_emissions > 0
B7: shared.tsx column type includes 'stores' (code-level check)
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


def _build_expansion_network() -> pypsa.Network:
    """
    Single-period network where the LP must EXPAND a renewable generator.

    snapshot_weightings.objective = 365 so the 24-hour profile represents
    an entire year — without it, the 24h LP would compare 24h of OPEX
    against annualised CAPEX and never build.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.snapshot_weightings.loc[:, ["objective", "generators", "stores"]] = 365.0
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    # Thermal — expensive enough that LP prefers building solar.
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=200.0, capital_cost=0.0)
    # Cheap-to-build solar — extendable.
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=200.0,
          marginal_cost=0.0, capital_cost=50_000.0,
          p_max_pu=0.6)
    return n


def test_b1_capex_expansion() -> None:
    print("\n[B1] cost.capex_expansion on expanding network")
    n = _build_expansion_network()
    n.optimize(solver_name="highs")
    _install(n)
    res = results_router.get_cost_breakdown()
    if not isinstance(res, dict):
        _step("cost_breakdown returns dict", False, str(res)[:200])
        return
    cap_exp = res.get("capex_expansion", 0.0)
    _step("capex_expansion is > 0 when solar expanded",
          cap_exp > 1.0,
          f"got €{cap_exp:.0f}")
    # Verify it matches hand-calc: solar built ~100 MW, capital_cost = 50_000
    # → expected ~€5M (with some LP variation).
    _step("capex_expansion is in the expected magnitude (€millions)",
          1_000_000 < cap_exp < 20_000_000,
          f"got €{cap_exp:.0f}")


def test_b2_dispatch_stack_tooltip() -> None:
    print("\n[B2] DispatchStack tooltip storage_charge label (code check)")
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    path = repo / "frontend" / "src" / "pages" / "results" / "DispatchStack.tsx"
    text = path.read_text(encoding="utf-8")
    # Must NOT have the old double-negate sign flip
    has_old_bug = "const sign = name === 'storage_charge' ? -1" in text
    # Must HAVE the new clear label
    has_new_label = "(charging)" in text
    _step("tooltip no longer double-negates storage_charge",
          not has_old_bug,
          "old sign-flip removed" if not has_old_bug else "still present")
    _step("tooltip labels storage charging clearly",
          has_new_label, "(charging) suffix present" if has_new_label else "missing")


def test_b3_curtailment_filtered() -> None:
    print("\n[B3] /results/curtailment includes only renewables + subsidised")
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=8, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=50.0, capital_cost=0.0)  # huge headroom
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=50.0, marginal_cost=0.0, capital_cost=0.0, p_max_pu=0.5)
    n.optimize(solver_name="highs")
    _install(n)
    res = results_router.get_curtailment()
    if not isinstance(res, dict):
        _step("curtailment endpoint returns payload", False, str(res)[:120])
        return
    cols = res.get("columns", [])
    _step("solar column present", "solar" in cols, f"cols={cols}")
    _step("thermal column EXCLUDED (no longer headroom)",
          "thermal" not in cols, f"cols={cols}")


def test_b4_line_duals_years_scaling() -> None:
    print("\n[B4] get_line_duals applies investment_period_weightings.years")
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    # `get_line_duals`' arithmetic lives in `services/results/line_duals.py`.
    # It has moved twice: out of `routers/simulation.py` into `routers/results.py`
    # when the read-only serializers were carved out, and out of the router into
    # a service by the 2026-09-04 decomposition. This assertion still named the
    # ORIGINAL file, so it read a source that no longer contains the code and
    # reported "missing year multiplier" no matter what the code did.
    path = repo / "backend" / "services" / "results" / "line_duals.py"
    text = path.read_text(encoding="utf-8")
    has_period_weight = "period_weight_series" in text and "row_w = row_w * period_weight_series" in text
    _step("line_duals multiplies congestion rent by years weights",
          has_period_weight,
          "period weighting wired" if has_period_weight else "missing year multiplier")


def test_b5_price_drivers_voll_heuristic() -> None:
    print("\n[B5] price_drivers detects high-price load_shedding heuristic")
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    # Same history as B4: `get_price_drivers`' body is now in
    # `services/results/prices.py`, not in the router it was first written in.
    path = repo / "backend" / "services" / "results" / "prices.py"
    text = path.read_text(encoding="utf-8")
    has_mc_multiplier = "MAX_MC_MULTIPLIER" in text
    has_load_shedding_check = 'diag = "load_shedding"' in text and "MAX_MC_MULTIPLIER * best_mc" in text
    _step("MAX_MC_MULTIPLIER constant present",
          has_mc_multiplier)
    _step("heuristic classifies price ≥ 10× MC as load_shedding",
          has_load_shedding_check)


def test_b6_storage_emissions() -> None:
    print("\n[B6] emissions includes StorageUnit with co2_emissions > 0")
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=8, freq="h"))
    n.add("Bus", "B1")
    # Carrier with non-zero CO2 — simulates fossil-derived hydrogen.
    n.add("Carrier", "dirty_h2", co2_emissions=0.3)
    n.add("Carrier", "gas", co2_emissions=0.2)
    # Demand exceeds thermal max so storage MUST discharge to meet it.
    n.add("Load", "L1", bus="B1", p_set=120.0)
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=100.0, marginal_cost=50.0, capital_cost=0.0, efficiency=0.5)
    # Storage with HUGE initial SoC and NOT cyclic — it can drain over the
    # horizon without needing to recharge.
    n.add("StorageUnit", "dirty_storage", bus="B1", carrier="dirty_h2",
          p_nom=30.0, max_hours=10.0,
          state_of_charge_initial=300.0,
          efficiency_store=1.0, efficiency_dispatch=1.0,
          marginal_cost=10.0, capital_cost=0.0,
          cyclic_state_of_charge=False)
    n.optimize(solver_name="highs")
    _install(n)
    res = results_router.get_emissions()
    if not isinstance(res, dict):
        _step("emissions returns dict", False, str(res)[:120])
        return
    by_carrier = {r.get("carrier"): r.get("tCO2", 0.0) for r in res.get("by_carrier", [])}
    print(f"  diag: by_carrier={by_carrier}")
    rows_storage = [r for r in res.get("by_generator", [])
                    if r.get("component") == "StorageUnit"]
    print(f"  diag: StorageUnit rows in by_generator: {len(rows_storage)}")
    _step("dirty_h2 carrier appears in by_carrier (storage discharged)",
          "dirty_h2" in by_carrier,
          f"by_carrier keys: {list(by_carrier)}")
    if "dirty_h2" in by_carrier:
        _step("dirty_h2 has positive tCO₂",
              by_carrier["dirty_h2"] > 0,
              f"dirty_h2={by_carrier['dirty_h2']:.2f}")


def test_b7_stores_column_type() -> None:
    print("\n[B7] shared.tsx weightedSum column type includes 'stores'")
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    path = repo / "frontend" / "src" / "pages" / "results" / "shared.tsx"
    text = path.read_text(encoding="utf-8")
    has_stores = "| 'stores'" in text
    _step("type union includes 'stores'", has_stores)


def main() -> int:
    for label, fn in [
        ("B1", test_b1_capex_expansion),
        ("B2", test_b2_dispatch_stack_tooltip),
        ("B3", test_b3_curtailment_filtered),
        ("B4", test_b4_line_duals_years_scaling),
        ("B5", test_b5_price_drivers_voll_heuristic),
        ("B6", test_b6_storage_emissions),
        ("B7", test_b7_stores_column_type),
    ]:
        try:
            fn()
        except Exception as e:
            _crashed("{label}", e)
    print()
    print("=" * 60)
    print(f"Total: {PASS_COUNT + FAIL_COUNT}  Pass: {PASS_COUNT}  Fail: {FAIL_COUNT}")
    print("=" * 60)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
