"""
QA: `_safe_capital_cost` must match periodized / asset_economics CAPEX basis.

When both overnight_cost and a stale capital_cost column are set, prefer
overnight × annuity — otherwise Dispatch CAPEX (economics_by_carrier) drifts
from Economics Fixed / cost_breakdown (user-reported on 3_nodes_system).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from routers.compare import _safe_capital_cost
from services.solver_service import _annuity

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


def test_overnight_preferred_over_stale_capital_cost() -> None:
    print("\n[1] overnight_cost preferred when both columns set")
    row = {
        "overnight_cost": 900_000.0,
        "capital_cost": 60_000.0,  # stale overnight/lifetime straight-line
        "discount_rate": 0.07,
        "lifetime": 25.0,
    }
    got = _safe_capital_cost(row, 0.07, 25.0)
    expect = 900_000.0 * _annuity(0.07, 25.0)
    _step("uses overnight × annuity", abs(got - expect) < 1e-6, f"got {got}, expect {expect}")
    _step("not stale capital_cost", abs(got - 60_000.0) > 1.0)


def test_capital_cost_when_no_overnight() -> None:
    print("\n[2] capital_cost path when overnight missing / zero")
    row = {"capital_cost": 50_000.0, "overnight_cost": 0.0, "lifetime": 15.0}
    got = _safe_capital_cost(row, 0.07, 25.0)
    _step("uses capital_cost", abs(got - 50_000.0) < 1e-9, f"got {got}")


def test_overnight_with_default_dr_lt() -> None:
    print("\n[3] overnight uses config defaults when asset dr/lt missing")
    row = {"overnight_cost": 250_000.0}
    got = _safe_capital_cost(row, 0.07, 15.0)
    expect = 250_000.0 * _annuity(0.07, 15.0)
    _step("annuity at defaults", abs(got - expect) < 1e-6, f"got {got}, expect {expect}")


if __name__ == "__main__":
    test_overnight_preferred_over_stale_capital_cost()
    test_capital_cost_when_no_overnight()
    test_overnight_with_default_dr_lt()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
