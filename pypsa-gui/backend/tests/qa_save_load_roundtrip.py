"""
End-to-end save/load round-trip QA covering every recent SolverConfig
extension AND the network-level data they depend on.

Strategy:
  1. Build a non-trivial multi-period network with every new field set:
     - Custom `co2_emissions` on the gas carrier.
     - SolverConfig: `co2_price_per_period`, `capex_budget_per_period`,
       `load_scalers`, `user_objective_scale`, `auto_discount_periods`.
     - Global constraints: per-period CO2 cap + per-period fuel limit
       (operational_limit with investment_period set).
  2. Save the project via `routers.projects.save_project`.
  3. Wipe in-memory state (reset_network + clear _state["solver_config"]).
  4. Load the project via `routers.projects.load_project`.
  5. Assert every field comes back identical.

Files written: a temp project dir under projects/_qa_roundtrip/. Cleaned up
at the end. Test is hermetic — leaves no state in the live backend.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from routers import projects as projects_router
from routers import simulation as sim_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

PASS = 0
FAIL = 0
PROJECT_NAME = "_qa_roundtrip"


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _approx(a: float, b: float, rel: float = 1e-6, abs_eps: float = 1e-6) -> bool:
    if abs(a - b) <= abs_eps:
        return True
    if b == 0:
        return abs(a) <= abs_eps
    return abs(a - b) / max(abs(b), 1e-12) <= rel


def _build_pre_save_state() -> SolverConfig:
    """Populate the in-memory network + SolverConfig with every new extension."""
    n = pypsa.Network()
    base = pd.date_range("2025-01-01", periods=4, freq="h")
    mi = pd.MultiIndex.from_product([[2025, 2030], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = [2025, 2030]
    n.investment_period_weightings.loc[2025, "years"] = 3.0
    n.investment_period_weightings.loc[2030, "years"] = 5.0

    # Custom co2_emissions on the gas carrier.
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas_gen", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=30.0, capital_cost=0.0, efficiency=0.5)
    n.add("Generator", "solar_gen", bus="B1", carrier="solar",
          p_nom=50.0, marginal_cost=0.0, capital_cost=10000.0,
          p_nom_extendable=True, p_max_pu=1.0)

    # Per-period CO2 cap (primary_energy with investment_period set).
    n.add("GlobalConstraint", "co2_cap_2030",
          type="primary_energy", carrier_attribute="co2_emissions",
          sense="<=", constant=500.0, investment_period=2030)
    # Per-period fuel limit (operational_limit per carrier+period).
    n.add("GlobalConstraint", "fuel_limit_gas_2025",
          type="operational_limit", carrier="gas",
          sense="<=", constant=1_000_000.0, investment_period=2025)
    n.add("GlobalConstraint", "fuel_limit_gas_2030",
          type="operational_limit", carrier="gas",
          sense="<=", constant=800_000.0, investment_period=2030)

    PyPSAService.set_network(n)

    cfg = SolverConfig(
        discount_rate=0.05,
        default_lifetime=20.0,
        co2_price=15.0,
        co2_price_per_period={"2025": 20.0, "2030": 50.0},
        voll=0.0,
        multi_investment_periods=True,
        investment_periods=[2025, 2030],
        load_scalers={"2025": 1.0, "2030": 1.10},
        auto_discount_periods=True,
        capex_budget_per_period={"2025": 1e9, "2030": 5e8},
        user_objective_scale=1e-3,
        presolve_enabled=True,
    )
    sim_router._state["solver_config"] = cfg
    return cfg


def _wipe_in_memory() -> None:
    PyPSAService.reset_network()
    sim_router._state["solver_config"] = SolverConfig()


def _assert_cfg_round_trip(before: SolverConfig, after: SolverConfig) -> None:
    """Compare every dataclass field — scalar, dict, list."""
    from dataclasses import fields as _fields
    for f in _fields(SolverConfig):
        bv = getattr(before, f.name)
        av = getattr(after, f.name)
        if isinstance(bv, dict):
            ok = bv == av
            _step(f"SolverConfig.{f.name} dict survives round-trip", ok,
                  f"before={bv} after={av}" if not ok else None)
        elif isinstance(bv, float):
            ok = _approx(bv, av) if av is not None else False
            _step(f"SolverConfig.{f.name} float survives round-trip", ok,
                  f"before={bv} after={av}" if not ok else None)
        elif isinstance(bv, list):
            ok = list(bv) == list(av or [])
            _step(f"SolverConfig.{f.name} list survives round-trip", ok,
                  f"before={bv} after={av}" if not ok else None)
        else:
            ok = bv == av
            _step(f"SolverConfig.{f.name} survives round-trip", ok,
                  f"before={bv!r} after={av!r}" if not ok else None)


def _assert_network_round_trip() -> None:
    """Check the on-network state we wrote ended up in the loaded network."""
    n = PyPSAService.get_network()

    # Carriers + co2_emissions.
    _step("carrier 'gas' present",
          "gas" in n.carriers.index, f"index={list(n.carriers.index)}")
    if "gas" in n.carriers.index:
        co2 = float(n.carriers.at["gas", "co2_emissions"]) if "co2_emissions" in n.carriers.columns else 0
        _step("carrier 'gas' co2_emissions = 0.2", _approx(co2, 0.2), f"got {co2}")

    # Investment periods + years weightings.
    _step("investment_periods round-trip",
          list(n.investment_periods) == [2025, 2030],
          f"got {list(n.investment_periods)}")
    try:
        ipw = n.investment_period_weightings
        years_2025 = float(ipw.at[2025, "years"]) if "years" in ipw.columns else 0
        years_2030 = float(ipw.at[2030, "years"]) if "years" in ipw.columns else 0
        _step("ipw.years[2025] = 3.0", _approx(years_2025, 3.0), f"got {years_2025}")
        _step("ipw.years[2030] = 5.0", _approx(years_2030, 5.0), f"got {years_2030}")
    except Exception as exc:
        _step("ipw.years round-trip", False, f"crash: {exc}")

    # Global constraints — count + per-row fields.
    gc = n.global_constraints
    expected_names = {"co2_cap_2030", "fuel_limit_gas_2025", "fuel_limit_gas_2030"}
    _step("global_constraints all three rows present",
          expected_names.issubset(set(gc.index)),
          f"got {set(gc.index)}")
    if "co2_cap_2030" in gc.index:
        ip = gc.at["co2_cap_2030", "investment_period"] if "investment_period" in gc.columns else None
        cap = float(gc.at["co2_cap_2030", "constant"])
        ca = str(gc.at["co2_cap_2030", "carrier_attribute"]) if "carrier_attribute" in gc.columns else ""
        _step("co2_cap_2030 investment_period = 2030",
              ip == 2030, f"got {ip}")
        _step("co2_cap_2030 constant = 500.0", _approx(cap, 500.0), f"got {cap}")
        _step("co2_cap_2030 carrier_attribute = co2_emissions",
              ca == "co2_emissions", f"got '{ca}'")
    if "fuel_limit_gas_2025" in gc.index:
        ip = gc.at["fuel_limit_gas_2025", "investment_period"] if "investment_period" in gc.columns else None
        cap = float(gc.at["fuel_limit_gas_2025", "constant"])
        _step("fuel_limit_gas_2025 investment_period = 2025",
              ip == 2025, f"got {ip}")
        _step("fuel_limit_gas_2025 constant = 1e6", _approx(cap, 1_000_000.0), f"got {cap}")
    if "fuel_limit_gas_2030" in gc.index:
        cap = float(gc.at["fuel_limit_gas_2030", "constant"])
        _step("fuel_limit_gas_2030 constant = 8e5", _approx(cap, 800_000.0), f"got {cap}")


def test_round_trip() -> None:
    print("\n[1] Build → save → reset → load → verify")
    cfg_before = _build_pre_save_state()
    # Save (force=True bypasses the empty-network safety check).
    projects_router.save_project(PROJECT_NAME, force=True, clear_undo=False)
    print(f"  saved project '{PROJECT_NAME}'")
    _wipe_in_memory()
    print("  wiped in-memory state")
    summary = projects_router.load_project(PROJECT_NAME)
    print(f"  loaded project — {summary.buses} buses, "
          f"{summary.snapshots} snapshots")

    cfg_after = sim_router._state["solver_config"]
    _assert_cfg_round_trip(cfg_before, cfg_after)
    _assert_network_round_trip()


def _cleanup() -> None:
    """Remove the test project dir."""
    try:
        # Find project dir using the same helper save_project uses.
        project_dir = projects_router._safe_project_dir(PROJECT_NAME)  # type: ignore[attr-defined]
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass


def main() -> int:
    try:
        test_round_trip()
    except Exception as e:
        print(f"  test crashed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
    finally:
        _cleanup()
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
