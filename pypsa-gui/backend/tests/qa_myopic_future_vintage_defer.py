"""
QA harness for `_defer_future_vintage_builds` in solver_service.

Tests the fix for the myopic-foresight bug where iter P decided future-
period vintages (build_year > P) — leading to two failure modes:

  Bug 1 — Decision lost: iter P's choice for a future vintage gets
          overwritten by iter (build_year), because `_freeze_period_capacities`
          only freezes vintages with build_year ≤ current_period.
  Bug 2 — Auto-discount arbitrage: in iter P, future-vintage CAPEX is
          PV-discounted relative to current-vintage CAPEX, so the LP
          prefers to "build later" even when current-period costs are
          worse — anchoring the build on a tiny representative-week
          view of the future period.

The fix is to pin future vintages (build_year > current_period) to
`p_nom = 0, p_nom_extendable = False` BEFORE each iteration's LP, then
revert after — so each vintage's capacity is decided EXACTLY ONCE, by
the iteration whose `current_period == build_year`.

Test plan:
  [1] Unit: defer at P=2026 pins Battery@2027/Battery@2028, leaves
      Battery@2026 untouched.
  [2] Unit: undo entries restore originals exactly.
  [3] Workflow: simulate three myopic iterations (defer → simulate LP →
      revert defer → freeze active) and verify the final p_nom values
      match the per-iteration decisions, with NO cross-iteration
      overwrites.
  [4] Integration: actual LP runs with co2_price_per_period that makes
      2027 dispatch expensive — verify Battery@2027 is sized non-trivially
      (proves the LP can respond to per-period signals when not blocked
      by spurious future-vintage decisions).
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
from services.pypsa_service import PyPSAService
from services.solver_service import (
    SolverConfig,
    _defer_future_vintage_builds,
    _freeze_period_capacities,
    run_simulation,
)

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


def _crashed(label: str, exc: BaseException) -> None:
    """
    A scenario that raised never ran its assertions, so it must COUNT as a
    failure — printing the traceback and moving on leaves the driver exiting 0
    while testing nothing. That is exactly how the stale
    `routers.simulation.get_*` references below went unnoticed: the scenarios
    crashed on every run and the summary still read "Fail: 0".
    """
    _step(f"{label} ran without crashing", False, f"{type(exc).__name__}: {exc}")

def _silent_phase(_msg: str) -> None:
    """Phase logger that swallows output — keeps test output compact."""
    pass


def _apply_undo(n, undo_actions) -> None:
    """Run the per-iteration undo loop exactly as solver_service does."""
    for action in reversed(undo_actions):
        _, attr_name, col, idx, orig = action
        target = getattr(n, attr_name, None)
        if target is None:
            continue
        valid = [i for i in idx if i in target.index]
        if valid:
            target.loc[valid, col] = orig.loc[valid]


def _build_three_vintage_network() -> pypsa.Network:
    """Network with three storage vintages: Bat@2026, Bat@2027, Bat@2028."""
    n = pypsa.Network()
    base = pd.date_range("2026-01-01", periods=24, freq="h")  # 1 day each
    periods = [2026, 2027, 2028]
    mi = pd.MultiIndex.from_product([periods, base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = periods
    for p in periods:
        n.investment_period_weightings.loc[p, "years"] = 1.0
        n.investment_period_weightings.loc[p, "objective"] = 1.0

    n.add("Bus", "B1")
    n.add("Carrier", "battery", co2_emissions=0.0)
    n.add("Carrier", "gas", co2_emissions=0.2)

    # Three extendable vintage rows — exactly the layout vintage_service
    # produces from `n.meta["vintage_bounds"]`.
    for year in periods:
        n.add(
            "StorageUnit", f"Battery@{year}",
            bus="B1", carrier="battery",
            p_nom=0.0, p_nom_extendable=True,
            p_nom_min=0.0, p_nom_max=1000.0,
            max_hours=4.0,
            capital_cost=100_000.0,
            build_year=year,
            lifetime=20.0,
        )

    # A gas generator so the LP has something to dispatch.
    n.add("Generator", "Gas", bus="B1", carrier="gas",
          p_nom=500.0, marginal_cost=30.0, efficiency=0.5,
          build_year=2025, lifetime=30)
    n.add("Load", "L", bus="B1", p_set=200.0)
    return n


def test_defer_pins_future_vintages_only() -> None:
    print("\n[1] _defer_future_vintage_builds pins future vintages only")
    n = _build_three_vintage_network()
    undo = []
    deferred = _defer_future_vintage_builds(n, 2026, undo, _silent_phase)
    su = n.storage_units

    _step("returned count = 2 (Battery@2027, Battery@2028)",
          deferred == 2, f"got {deferred}")
    _step("Battery@2026 still extendable",
          bool(su.at["Battery@2026", "p_nom_extendable"]) is True)
    _step("Battery@2027 now NOT extendable",
          bool(su.at["Battery@2027", "p_nom_extendable"]) is False)
    _step("Battery@2027 p_nom = 0",
          float(su.at["Battery@2027", "p_nom"]) == 0.0)
    _step("Battery@2028 now NOT extendable",
          bool(su.at["Battery@2028", "p_nom_extendable"]) is False)
    _step("Battery@2028 p_nom = 0",
          float(su.at["Battery@2028", "p_nom"]) == 0.0)


def test_undo_restores_originals() -> None:
    print("\n[2] Undo restores originals exactly")
    n = _build_three_vintage_network()
    # Set a non-default p_nom so we can verify restoration.
    n.storage_units.at["Battery@2027", "p_nom"] = 12.5
    n.storage_units.at["Battery@2028", "p_nom"] = 33.0
    undo = []
    _defer_future_vintage_builds(n, 2026, undo, _silent_phase)

    # Defer mutated them — confirm.
    _step("p_nom mutated to 0 during defer",
          float(n.storage_units.at["Battery@2027", "p_nom"]) == 0.0
          and float(n.storage_units.at["Battery@2028", "p_nom"]) == 0.0)

    _apply_undo(n, undo)
    _step("Battery@2027 p_nom restored to 12.5",
          float(n.storage_units.at["Battery@2027", "p_nom"]) == 12.5)
    _step("Battery@2028 p_nom restored to 33.0",
          float(n.storage_units.at["Battery@2028", "p_nom"]) == 33.0)
    _step("Battery@2027 extendable restored to True",
          bool(n.storage_units.at["Battery@2027", "p_nom_extendable"]) is True)
    _step("Battery@2028 extendable restored to True",
          bool(n.storage_units.at["Battery@2028", "p_nom_extendable"]) is True)


def test_defer_freeze_interleave_three_iterations() -> None:
    """
    Walk through what _run_myopic_foresight does each iteration:
    1. defer future vintages (per_iter_undo)
    2. LP solves → p_nom_opt is set (we simulate this manually)
    3. revert defer (per_iter_undo)
    4. freeze active vintages (iteration_undo)
    """
    print("\n[3] Three-iteration interleave: defer → solve → revert → freeze")
    n = _build_three_vintage_network()
    iteration_undo = []
    su = n.storage_units

    # ── Iteration 2026 ────────────────────────────────────────────────────
    per_iter = []
    _defer_future_vintage_builds(n, 2026, per_iter, _silent_phase)
    # Simulate LP: only Battery@2026 was extendable, so only it gets a
    # p_nom_opt. The LP couldn't have touched Battery@2027/2028.
    su.at["Battery@2026", "p_nom_opt"] = 100.0
    # Defensive check: ensure the LP can't have touched future vintages.
    _step("iter 2026: Battery@2027/@2028 pinned at p_nom=0 during LP",
          float(su.at["Battery@2027", "p_nom"]) == 0.0
          and float(su.at["Battery@2028", "p_nom"]) == 0.0)
    _apply_undo(n, per_iter)
    _freeze_period_capacities(n, 2026, iteration_undo, _silent_phase)
    _step("iter 2026: Battery@2026 frozen at p_nom=100",
          float(su.at["Battery@2026", "p_nom"]) == 100.0)
    _step("iter 2026: Battery@2026 no longer extendable",
          bool(su.at["Battery@2026", "p_nom_extendable"]) is False)
    _step("iter 2026: Battery@2027 still extendable (not frozen)",
          bool(su.at["Battery@2027", "p_nom_extendable"]) is True)

    # ── Iteration 2027 ────────────────────────────────────────────────────
    per_iter = []
    deferred = _defer_future_vintage_builds(n, 2027, per_iter, _silent_phase)
    _step("iter 2027: defers only Battery@2028 (Battery@2026 frozen, Battery@2027 active)",
          deferred == 1, f"got {deferred}")
    # Simulate LP: Battery@2027 extendable & active in 2027 → decides 250.
    su.at["Battery@2027", "p_nom_opt"] = 250.0
    _step("iter 2027: Battery@2026 still frozen at 100 (decision persists)",
          float(su.at["Battery@2026", "p_nom"]) == 100.0)
    _apply_undo(n, per_iter)
    _freeze_period_capacities(n, 2027, iteration_undo, _silent_phase)
    _step("iter 2027: Battery@2027 frozen at p_nom=250",
          float(su.at["Battery@2027", "p_nom"]) == 250.0)
    _step("iter 2027: Battery@2028 still extendable",
          bool(su.at["Battery@2028", "p_nom_extendable"]) is True)

    # ── Iteration 2028 ────────────────────────────────────────────────────
    per_iter = []
    deferred = _defer_future_vintage_builds(n, 2028, per_iter, _silent_phase)
    _step("iter 2028: no future vintages to defer", deferred == 0,
          f"got {deferred}")
    # Simulate LP: Battery@2028 extendable & active → decides 75.
    su.at["Battery@2028", "p_nom_opt"] = 75.0
    _apply_undo(n, per_iter)
    _freeze_period_capacities(n, 2028, iteration_undo, _silent_phase)
    _step("iter 2028: Battery@2028 frozen at p_nom=75",
          float(su.at["Battery@2028", "p_nom"]) == 75.0)

    # ── Final state ───────────────────────────────────────────────────────
    _step("FINAL: Battery@2026 = 100 (decided in iter 2026)",
          float(su.at["Battery@2026", "p_nom"]) == 100.0)
    _step("FINAL: Battery@2027 = 250 (decided in iter 2027, persisted)",
          float(su.at["Battery@2027", "p_nom"]) == 250.0)
    _step("FINAL: Battery@2028 = 75 (decided in iter 2028)",
          float(su.at["Battery@2028", "p_nom"]) == 75.0)


def test_integration_each_iteration_decides_its_own_vintage() -> None:
    """
    End-to-end smoke test: each myopic iteration decides exactly one
    vintage (its own period's), and earlier decisions persist forward.

    Setup: lifetime=1 vintages so each is active in only its own period.
    Heterogeneous p_nom_max bounds per vintage — Battery@2026 can build
    up to 100 MW, Battery@2027 up to 300 MW, Battery@2028 up to 200 MW.
    Capacity-constrained gas (peak load 400, gas 200) FORCES at least some
    battery in every period. The defer fix ensures each iteration's LP
    sees only ITS vintage as extendable — so the binding upper bound is
    the vintage's own p_nom_max, and the final pattern reflects
    per-vintage bounds (not cross-iteration arbitrage).

    Pre-fix behaviour: iter 2026's LP can decide Battery@2027 + Battery@2028
    too, then iter 2027/2028 re-decide → unstable build pattern that
    doesn't match per-vintage bounds.

    Post-fix behaviour: build pattern = each vintage at its p_nom_max
    (since peak load > gas alone, so storage is forced to max). We verify:
      • Battery@2027 receives a non-trivial build (the year with highest
        p_nom_max should attract the largest investment, NOT zero like
        pre-fix).
      • Each vintage's p_nom_opt respects its OWN p_nom_max bound
        (no cross-vintage spillover).
    """
    print("\n[4] Integration: each iteration decides its own vintage to its bound")
    n = pypsa.Network()
    base = pd.date_range("2026-01-01", periods=4, freq="h")  # 4h per period
    periods = [2026, 2027, 2028]
    mi = pd.MultiIndex.from_product([periods, base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = periods
    for p in periods:
        n.investment_period_weightings.loc[p, "years"] = 1.0
        n.investment_period_weightings.loc[p, "objective"] = 1.0

    n.add("Bus", "B1")
    n.add("Carrier", "diesel", co2_emissions=0.5)
    n.add("Carrier", "gas",    co2_emissions=0.2)
    # Three generator vintages — each with a DIFFERENT p_nom_max bound.
    # lifetime=1 → each only active in its own build_year. The LP MUST
    # build the vintage in its own period to serve a load that exceeds
    # the always-on diesel baseline.
    bounds_per_year = {2026: 150.0, 2027: 300.0, 2028: 200.0}
    for year in periods:
        n.add(
            "Generator", f"Gas@{year}", bus="B1", carrier="gas",
            p_nom=0.0, p_nom_extendable=True,
            p_nom_min=0.0, p_nom_max=bounds_per_year[year],
            marginal_cost=30.0, efficiency=1.0,
            capital_cost=1.0,  # tiny CAPEX so LP hits the upper bound
            build_year=year, lifetime=1.0,
        )
    # Diesel: always-on baseline at 100 MW. Expensive (200 €/MWh) so the
    # LP prefers gas where available.
    n.add("Generator", "Diesel", bus="B1", carrier="diesel",
          p_nom=500.0, marginal_cost=200.0, efficiency=1.0,
          build_year=2025, lifetime=30)
    # Per-period load: each period has a DIFFERENT demand level so the
    # LP's optimal gas vintage capacity differs per iteration. Pre-fix:
    # cross-iteration leak distorts these. Post-fix: each iteration sees
    # its own load → builds its own optimum.
    load_per_period = {2026: 100.0, 2027: 250.0, 2028: 150.0}
    load_t = pd.Series(
        [load_per_period[int(p)] for p in mi.get_level_values(0)],
        index=mi, dtype=float,
    )
    n.add("Load", "L", bus="B1")
    n.loads_t.p_set = pd.DataFrame({"L": load_t}, index=mi)

    cfg = SolverConfig(
        multi_investment_periods=True,
        solve_strategy="myopic",
        # Pure myopic (no limited foresight): each iteration sees ONLY its
        # own period's snapshots. With lifetime=1 vintages, this is the
        # natural choice — Battery@2026 isn't active in 2027 anyway, so
        # there's no informational benefit to including 2027 in iter 2026.
        lf_aggregate_future=False,
        co2_price_per_period={"2026": 5.0, "2027": 500.0, "2028": 5.0},
    )

    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    try:
        status, condition = run_simulation(cfg, n, lock, stop, log_q)
    except Exception as exc:
        print(f"    solve raised: {type(exc).__name__}: {exc}")
        status, condition = "raised", str(exc)
    _step("solve completed", status in ("ok", "optimal"),
          f"status={status} condition={condition}")
    if status not in ("ok", "optimal"):
        return

    # Read p_nom_opt directly from each vintage row. p_nom_opt is set by
    # n.optimize() and isn't touched by the iteration_undo restore loop
    # (which only reverts p_nom and p_nom_extendable). So p_nom_opt holds
    # each vintage's decision from the iteration that actually had it
    # extendable.
    per_vintage = {}
    for year in periods:
        name = f"Gas@{year}"
        if name in n.generators.index:
            per_vintage[year] = float(n.generators.at[name, "p_nom_opt"])

    print("    generators summary:")
    for year in periods:
        name = f"Gas@{year}"
        if name in n.generators.index:
            row = n.generators.loc[name]
            print(f"      {name}: p_nom={float(row['p_nom']):.3f} "
                  f"p_nom_opt={float(row['p_nom_opt']):.3f} "
                  f"ext={bool(row['p_nom_extendable'])} "
                  f"build_year={int(row['build_year'])} "
                  f"p_nom_max={float(row['p_nom_max']):.1f}")

    print(f"    Per-vintage p_nom: {per_vintage}")

    # Acceptance: each iteration builds exactly its OWN period's load —
    # not the load of some other period (which a cross-iteration leak
    # would produce). 2027 has the highest load (250) → should build the
    # largest vintage. 2026 the smallest (100). 2028 in between (150).
    p26 = per_vintage.get(2026, 0.0)
    p27 = per_vintage.get(2027, 0.0)
    p28 = per_vintage.get(2028, 0.0)
    _step("Gas@2026 ≈ 100 (matches its period's load)",
          abs(p26 - 100.0) < 1.0, f"p_nom_2026 = {p26:.2f} (expected ~100)")
    _step("Gas@2027 ≈ 250 (matches its period's load)",
          abs(p27 - 250.0) < 1.0, f"p_nom_2027 = {p27:.2f} (expected ~250)")
    _step("Gas@2028 ≈ 150 (matches its period's load)",
          abs(p28 - 150.0) < 1.0, f"p_nom_2028 = {p28:.2f} (expected ~150)")
    _step("Gas@2027 > Gas@2026 (responds to higher load in 2027)",
          p27 > p26 + 1.0,
          f"p_nom_2027={p27:.2f}, p_nom_2026={p26:.2f}")
    _step("Gas@2027 > Gas@2028 (responds to higher load in 2027)",
          p27 > p28 + 1.0,
          f"p_nom_2027={p27:.2f}, p_nom_2028={p28:.2f}")


def main() -> int:
    for fn in (
        test_defer_pins_future_vintages_only,
        test_undo_restores_originals,
        test_defer_freeze_interleave_three_iterations,
        test_integration_each_iteration_decides_its_own_vintage,
    ):
        try:
            fn()
        except Exception as e:
            _crashed("scenario", e)
            import traceback; traceback.print_exc()
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
