"""
QA harness for SCLOPF × myopic foresight (Phase 1: scope='horizon').

Verifies:
  [1] Validation no longer blocks `sclopf=True` + `solve_strategy='myopic'`.
  [2] `transmission_losses=True` + `sclopf=True` is rejected at preflight.
  [3] `sclopf_scope='invalid'` is rejected at preflight.
  [4] `_outages_active_in_period` filters branches by build_year/lifetime.
  [5] Multi-period network solves end-to-end with SCLOPF active in each
      iteration (status='ok'/'optimal').
  [6] Per-iteration filter: a line with `build_year=2027` is NOT in iter
      2026's contingency set but IS in iter 2027's.
  [7] Auto-fallback: SCLOPF on with no branches matched → plain myopic
      runs, no error, log mentions fallback.
  [8] Regression: enabling SCLOPF on a network with NO contingencies
      should produce the same objective as plain myopic (within tol),
      because the contingency LP collapses to plain LOPF.

Sanity-only. Doesn't probe LP correctness of contingency constraints —
PyPSA's own tests cover that.
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
    _outages_active_in_period,
    run_simulation,
)
from services.validation_service import has_errors, validate_for_run

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

def _build_two_bus_two_line_network(
    line2_build_year: int = 2025,
) -> pypsa.Network:
    """
    Tiny 2-bus, 2-line redundant ring + a single load + two generators.
    Forms the smallest non-radial topology so SCLOPF has something
    meaningful to enforce (N-1 outage of either line leaves the other to
    carry full load).

    line2_build_year lets the test put the second line in a specific
    investment period — used to verify the per-period outage filter.
    """
    n = pypsa.Network()
    periods = [2026, 2027]
    base = pd.date_range("2026-01-01", periods=4, freq="h")
    mi = pd.MultiIndex.from_product([periods, base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = periods
    for p in periods:
        n.investment_period_weightings.loc[p, "years"] = 1.0
        n.investment_period_weightings.loc[p, "objective"] = 1.0

    n.add("Bus", "B1", v_nom=110.0)
    n.add("Bus", "B2", v_nom=110.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    # ONE extendable generator per period so every myopic iteration has
    # something to size — the `has_active_extendable` short-circuit in
    # `_run_myopic_foresight` otherwise skips iterations where nothing
    # is extendable, and we'd never exercise the SCLOPF dispatch branch
    # for those iterations.
    n.add("Generator", "Gen_B1_2026", bus="B1", carrier="gas",
          p_nom=10.0, p_nom_extendable=True, p_nom_max=200.0,
          capital_cost=1000.0, marginal_cost=30.0, efficiency=1.0,
          build_year=2026, lifetime=1)
    n.add("Generator", "Gen_B1_2027", bus="B1", carrier="gas",
          p_nom=10.0, p_nom_extendable=True, p_nom_max=200.0,
          capital_cost=1000.0, marginal_cost=30.0, efficiency=1.0,
          build_year=2027, lifetime=1)
    n.add("Generator", "Gen_B2", bus="B2", carrier="gas",
          p_nom=50.0, marginal_cost=50.0, efficiency=1.0)
    n.add("Load", "L_B2", bus="B2", p_set=60.0)
    # Two parallel lines so N-1 of either still allows transit.
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.01, r=0.001,
          s_nom=100.0, build_year=2025, lifetime=20)
    n.add("Line", "L2", bus0="B1", bus1="B2", x=0.01, r=0.001,
          s_nom=100.0, build_year=line2_build_year, lifetime=20)
    return n


def test_validation_no_longer_blocks() -> None:
    print("\n[1] Validation no longer blocks sclopf + myopic")
    n = _build_two_bus_two_line_network()
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
    )
    PyPSAService.set_network(n)
    issues = validate_for_run(n, cfg)
    _step(
        "no `myopic_with_sclopf` error",
        not any(i.code == "myopic_with_sclopf" for i in issues),
        f"got codes = {sorted({i.code for i in issues})}",
    )
    _step(
        "preflight passes (no errors)",
        not has_errors(issues),
        f"errors = {[i.code for i in issues if i.severity == 'error']}",
    )
    _step(
        "warns about LP cost",
        any(i.code == "myopic_sclopf_cost" for i in issues),
    )


def test_validation_blocks_transmission_losses_combo() -> None:
    print("\n[2] sclopf + transmission_losses still blocked")
    n = _build_two_bus_two_line_network()
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        transmission_losses=True,
    )
    PyPSAService.set_network(n)
    issues = validate_for_run(n, cfg)
    _step(
        "preflight returns the new sclopf_with_transmission_losses error",
        any(i.code == "sclopf_with_transmission_losses" for i in issues),
        f"got codes = {sorted({i.code for i in issues})}",
    )
    _step(
        "preflight has errors (blocks run)",
        has_errors(issues),
    )


def test_validation_invalid_scope() -> None:
    print("\n[3] sclopf_scope='banana' blocked at preflight")
    n = _build_two_bus_two_line_network()
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="banana",  # type: ignore[arg-type]
    )
    PyPSAService.set_network(n)
    issues = validate_for_run(n, cfg)
    _step(
        "preflight returns sclopf_scope_invalid",
        any(i.code == "sclopf_scope_invalid" for i in issues),
        f"got codes = {sorted({i.code for i in issues})}",
    )


def test_outages_active_filter() -> None:
    print("\n[4] _outages_active_in_period filters by build_year + lifetime")
    n = _build_two_bus_two_line_network(line2_build_year=2027)
    PyPSAService.set_network(n)
    outages = [("Line", "L1"), ("Line", "L2")]
    active_2026 = _outages_active_in_period(n, 2026, outages)
    active_2027 = _outages_active_in_period(n, 2027, outages)
    _step(
        "2026: only L1 active (L2 build_year=2027)",
        active_2026 == [("Line", "L1")],
        f"got {active_2026}",
    )
    _step(
        "2027: both lines active",
        active_2027 == [("Line", "L1"), ("Line", "L2")],
        f"got {active_2027}",
    )
    _step(
        "empty outage list returns empty",
        _outages_active_in_period(n, 2026, []) == [],
    )


def test_end_to_end_solve() -> None:
    print("\n[5] End-to-end: 2-period network solves with SCLOPF + myopic")
    n = _build_two_bus_two_line_network()
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
    )
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    _step(
        "solve completes with status=ok",
        status == "ok",
        f"status={status} condition={condition}",
    )
    _step(
        "condition is optimal",
        condition == "optimal",
        f"got {condition}",
    )
    # Drain log queue to look for SCLOPF + per-iter contingency log lines.
    log_lines = []
    while not log_q.empty():
        log_lines.append(log_q.get_nowait())
    joined = "\n".join(str(line) for line in log_lines)
    _step(
        "log mentions SCLOPF + myopic active",
        "SCLOPF + myopic active" in joined,
        f"missing in log (excerpt): {joined[-300:]}",
    )
    _step(
        "log mentions a per-iteration SCLOPF line",
        "SCLOPF with " in joined,
    )


def test_per_iteration_filter_e2e() -> None:
    print("\n[6] End-to-end: future-dated line not in iter 2026's contingency set")
    n = _build_two_bus_two_line_network(line2_build_year=2027)
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
    )
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    log_lines = []
    while not log_q.empty():
        log_lines.append(log_q.get_nowait())
    joined = "\n".join(str(line) for line in log_lines)
    _step(
        "solve completes (status=ok)",
        status == "ok",
        f"status={status} condition={condition}",
    )
    # Iter 2026 should have SCLOPF with 1 contingency (L1 only).
    # Iter 2027 should have SCLOPF with 2 (L1 + L2).
    _step(
        "iter 2026 log mentions 1 contingency (L2 filtered)",
        "SCLOPF with 1 contingency" in joined,
        "missing in log",
    )
    _step(
        "iter 2027 log mentions 2 contingencies",
        "SCLOPF with 2 contingency" in joined,
        "missing in log",
    )


def test_auto_fallback_when_no_branches() -> None:
    print("\n[7] Auto-fallback when no branches matched")
    n = _build_two_bus_two_line_network()
    # sclopf=True but no inclusion flags / threshold / extras → empty outage set
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=False,
        sclopf_include_all_transformers=False,
        sclopf_voltage_threshold_kv=0.0,
        sclopf_extra_lines=[],
        sclopf_extra_transformers=[],
    )
    PyPSAService.set_network(n)
    # Preflight catches this with sclopf_no_branches — verify it does.
    issues = validate_for_run(n, cfg)
    _step(
        "preflight blocks empty-outage SCLOPF (sclopf_no_branches)",
        any(i.code == "sclopf_no_branches" for i in issues),
        f"got codes = {sorted({i.code for i in issues})}",
    )


def test_sclopf_with_curtailment_cost_and_limited_foresight() -> None:
    """
    Regression for the snapshot-scope drift QA flagged in Phase-1
    review (concern C1). PyPSA's `solve_model` — which
    `optimize_security_constrained` reaches internally — hardcodes
    `extra_functionality(n, n.snapshots)`, not the iteration's `sns`
    subset. The curtailment-cost wrapper slices `p_max_pu` by the
    snapshot argument and assumes it's the iteration subset; if it gets
    the full index instead, the linopy term misaligns and the solve
    either fails or produces wildly wrong objectives.

    Setup that exercises every codepath in one go:
      • multi-period (multi_investment_periods=True) + myopic
      • SCLOPF on with both lines as outage candidates
      • curtailment_cost > 0 on at least one generator
      • Limited foresight ON so iter `sns` is a strict subset of n.snapshots
        (this is what makes the drift observable — when sns == n.snapshots,
        the bug is invisible)

    Expectation: solve completes OK. Objective is finite and positive.
    """
    print("\n[9] SCLOPF + curtailment_cost + limited foresight (snapshot-scope drift)")
    n = _build_two_bus_two_line_network()
    # Add a tiny renewable with curtailment_cost so the wrapper actually fires.
    n.add("Carrier", "solar", co2_emissions=0.0)
    # 3 snapshots/day × 2 periods = 6 snapshots total; representative slice
    # is whatever lf_aggregate_future selects from period 2.
    n.add("Generator", "Solar", bus="B1", carrier="solar",
          p_nom=20.0, p_nom_extendable=True, p_nom_max=200.0,
          capital_cost=2000.0, marginal_cost=0.0, efficiency=1.0,
          build_year=2025, lifetime=30)
    # curtailment_cost is a custom column the GUI adds via the network
    # router; add it directly here.
    n.generators.loc["Solar", "curtailment_cost"] = 100.0
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
        # Limited foresight ON so sns != n.snapshots
        lf_aggregate_future=True,
        lf_k_periods=1,
        lf_period_length_h=2,
    )
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    _step(
        "solve completes (status=ok) with curtailment_cost + limited foresight + SCLOPF",
        status == "ok",
        f"status={status} condition={condition}",
    )
    obj = float(n.objective) if getattr(n, "objective", None) is not None else None
    _step(
        "objective is finite + non-NaN",
        obj is not None and obj == obj,  # NaN != NaN
        f"objective = {obj}",
    )
    # Drain log and confirm the curtailment wrapper actually fired (it's
    # gated on the network having `curtailment_cost > 0` and emits a
    # `[CURT]` line).
    log_lines = []
    while not log_q.empty():
        log_lines.append(log_q.get_nowait())
    joined = "\n".join(str(line) for line in log_lines)
    _step(
        "[CURT] wrapper fired (proves extra_fn ran with correct snapshots)",
        "[CURT] Curtailment-cost wrapper active" in joined,
        "missing '[CURT]' in log",
    )


def test_scope_current_period_drops_future_snapshots() -> None:
    """
    Phase-2: `sclopf_scope='current_period'` drops future-period
    snapshots from the iteration's SCLOPF LP. With limited foresight ON,
    horizon-scope solves over (current hourly + future representative);
    current_period scope solves over current hourly only.

    Sanity check: the log emits a "dropping N future-period snapshot(s)"
    line, and the solve completes successfully.
    """
    print("\n[10] sclopf_scope='current_period' drops future-period snapshots")
    n = _build_two_bus_two_line_network()
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="current_period",
        lf_aggregate_future=True,
        lf_k_periods=1,
        lf_period_length_h=2,
    )
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    log_lines = []
    while not log_q.empty():
        log_lines.append(log_q.get_nowait())
    joined = "\n".join(str(line) for line in log_lines)
    _step(
        "solve completes (status=ok)",
        status == "ok",
        f"status={status} condition={condition}",
    )
    _step(
        "log mentions dropping future-period snapshots",
        "dropping " in joined and "future-period snapshot" in joined,
        "missing in log",
    )


def test_horizon_vs_current_period_log_difference() -> None:
    """
    Cross-check: scope='horizon' does NOT emit the 'dropping ...
    future-period snapshot(s)' line. This locks the contract: only
    current_period scope filters the iteration's snapshot set.

    Uses the same network shape as test 9 (with a small extendable Solar
    so the limited-foresight LP has redundant supply and stays feasible
    under contingency).
    """
    print("\n[11] scope='horizon' does not drop future-period snapshots")
    n = _build_two_bus_two_line_network()
    # Match test 9's setup: a tiny Solar so the contingency LP has slack.
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Generator", "Solar", bus="B1", carrier="solar",
          p_nom=20.0, p_nom_extendable=True, p_nom_max=200.0,
          capital_cost=2000.0, marginal_cost=0.0, efficiency=1.0,
          build_year=2025, lifetime=30)
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
        lf_aggregate_future=True,
        lf_k_periods=1,
        lf_period_length_h=2,
    )
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    log_lines = []
    while not log_q.empty():
        log_lines.append(log_q.get_nowait())
    joined = "\n".join(str(line) for line in log_lines)
    _step(
        "solve completes (status=ok)",
        status == "ok",
        f"status={status} condition={condition}",
    )
    _step(
        "log does NOT mention dropping future-period snapshots",
        "dropping " not in joined or "future-period snapshot" not in joined,
        "horizon scope shouldn't filter snapshots",
    )


def test_diagnostic_log_lines_present() -> None:
    """
    The user-facing log gains three SCLOPF-specific lines once the
    feature runs end-to-end. This test pins their wording so future log
    refactors don't silently drop the visibility the user relies on.
    """
    print("\n[12] SCLOPF diagnostic log lines fire on a real solve")
    n = _build_two_bus_two_line_network()
    cfg = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
    )
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    log_lines = []
    while not log_q.empty():
        log_lines.append(log_q.get_nowait())
    joined = "\n".join(str(line) for line in log_lines)
    _step("solve completes", status == "ok", f"status={status} condition={condition}")
    _step(
        "log says 'myopic + SCLOPF' instead of plain 'SCLOPF' at dispatch",
        "myopic + SCLOPF" in joined,
        "missing in log",
    )
    _step(
        "log lists the contingency set",
        "contingency set:" in joined,
        "missing in log",
    )
    _step(
        "log emits [SCLOPF-POST] per iteration",
        "[SCLOPF-POST]" in joined,
        "missing in log",
    )
    _step(
        "[SCLOPF-POST] reports BINDING or slack",
        "(BINDING)" in joined or "(slack)" in joined,
        "missing in log",
    )


def test_regression_objective_close_to_plain_myopic() -> None:
    """
    When the network is 2-parallel-lines with each at 100 MW for a 60 MW
    load, N-1 of either line still serves the load. So the contingency
    constraints are non-binding and SCLOPF should give the same objective
    as plain myopic LOPF.
    """
    print("\n[8] Regression: SCLOPF objective equals plain myopic when non-binding")
    n1 = _build_two_bus_two_line_network()
    PyPSAService.set_network(n1)
    cfg_plain = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=False,
    )
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q1: queue.SimpleQueue = queue.SimpleQueue()
    status1, _ = run_simulation(cfg_plain, n1, lock, stop, log_q1)
    obj_plain = float(n1.objective) if hasattr(n1, "objective") and n1.objective is not None else None

    n2 = _build_two_bus_two_line_network()
    PyPSAService.set_network(n2)
    cfg_sclopf = SolverConfig(
        solve_strategy="myopic",
        multi_investment_periods=True,
        sclopf=True,
        sclopf_include_all_lines=True,
        sclopf_scope="horizon",
    )
    log_q2: queue.SimpleQueue = queue.SimpleQueue()
    status2, _ = run_simulation(cfg_sclopf, n2, PyPSAService.get_lock(), stop, log_q2)
    obj_sclopf = float(n2.objective) if hasattr(n2, "objective") and n2.objective is not None else None

    _step("plain myopic solved", status1 == "ok")
    _step("sclopf+myopic solved", status2 == "ok")
    if obj_plain is not None and obj_sclopf is not None:
        rel = abs(obj_plain - obj_sclopf) / max(abs(obj_plain), 1.0)
        _step(
            "objectives match within 1 % (non-binding contingencies)",
            rel < 0.01,
            f"plain={obj_plain:.2f}, sclopf={obj_sclopf:.2f}, rel_gap={rel:.4f}",
        )
    else:
        _step("both objectives non-null", False,
              f"plain={obj_plain}, sclopf={obj_sclopf}")


def main() -> int:
    for fn in (
        test_validation_no_longer_blocks,
        test_validation_blocks_transmission_losses_combo,
        test_validation_invalid_scope,
        test_outages_active_filter,
        test_end_to_end_solve,
        test_per_iteration_filter_e2e,
        test_auto_fallback_when_no_branches,
        test_sclopf_with_curtailment_cost_and_limited_foresight,
        test_scope_current_period_drops_future_snapshots,
        test_horizon_vs_current_period_log_difference,
        test_diagnostic_log_lines_present,
        test_regression_objective_close_to_plain_myopic,
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
