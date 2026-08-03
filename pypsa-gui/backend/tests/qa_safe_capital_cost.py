"""
QA: `_safe_capital_cost` correctly looks up the pcc-resolved capital_cost.

Before Task 9 (2026-08-01), `_safe_capital_cost` hand-rolled
`overnight_cost * annuity(rate, lifetime)` itself, preferring overnight_cost
over a stale straight-line `capital_cost` column when both were set. That
hand-rolled formula omitted PyPSA's `nyears` (horizon-fraction) scaling and
overstated CAPEX by up to 365x on unit-weighted small-snapshot networks (see
docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md,
Sections 4 and 9). The fix makes `_safe_capital_cost` delegate entirely to
`services.solver_service.periodized_capital_costs` — the SAME resolution
`asset_economics` / `cost_breakdown` / `asset_costs` use, which already
implements the overnight-preferred-over-stale-capital_cost choice and the
correct `nyears` scaling. `_safe_capital_cost` itself is now a pure lookup
into that precomputed dict, so this QA script checks the LOOKUP contract
(present entry -> its value; absent / wrong-bucket -> 0.0) rather than
re-deriving annuity arithmetic that no longer lives in this function.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from routers.compare import _safe_capital_cost

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


def test_returns_the_pcc_resolved_value() -> None:
    print("\n[1] returns pcc's stored capital_cost for a known asset")
    row = pd.Series({"overnight_cost": 900_000.0, "capital_cost": 60_000.0}, name="gas")
    pcc = {"generators": {"gas": {"capital_cost": 12_345.67}}}
    got = _safe_capital_cost(row, pcc, "generators")
    _step(
        "returns pcc's value, not either of the row's own cost columns",
        abs(got - 12_345.67) < 1e-9,
        f"got {got}",
    )


def test_missing_asset_returns_zero() -> None:
    print("\n[2] asset absent from pcc's bucket -> 0.0, caller skips the contribution")
    row = pd.Series({"overnight_cost": 900_000.0}, name="unknown_asset")
    pcc = {"generators": {"gas": {"capital_cost": 12_345.67}}}
    got = _safe_capital_cost(row, pcc, "generators")
    _step("returns 0.0", got == 0.0, f"got {got}")


def test_wrong_comp_attr_bucket_returns_zero() -> None:
    print("\n[3] asset present under a DIFFERENT comp_attr bucket -> 0.0")
    row = pd.Series({"overnight_cost": 900_000.0}, name="gas")
    pcc = {"links": {"gas": {"capital_cost": 12_345.67}}}
    got = _safe_capital_cost(row, pcc, "generators")
    _step("bucket mismatch is treated as absent, not cross-matched", got == 0.0, f"got {got}")


def test_row_without_a_name_returns_zero() -> None:
    print("\n[4] a plain dict row (no `.name`) can't be looked up -> 0.0, not a crash")
    row = {"overnight_cost": 900_000.0}
    pcc = {"generators": {"gas": {"capital_cost": 12_345.67}}}
    got = _safe_capital_cost(row, pcc, "generators")
    _step("returns 0.0 instead of raising", got == 0.0, f"got {got}")


if __name__ == "__main__":
    test_returns_the_pcc_resolved_value()
    test_missing_asset_returns_zero()
    test_wrong_comp_attr_bucket_returns_zero()
    test_row_without_a_name_returns_zero()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
