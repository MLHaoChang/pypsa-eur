"""
`periodized_capital_costs` (services/solver_service.py:3659) — the resolver
behind `GET /api/simulation/asset_costs` — used to substitute the ANNUALISED
rate (`capital_cost`, EUR/MW/yr) for the UPFRONT lump sum (`overnight_cost`,
EUR/MW) whenever the upfront figure could not be resolved:

    if math.isnan(v_upf) or math.isinf(v_upf):
        v_upf = v_ann

That is a unit error of roughly the annuity factor (~15-20x for a 40-year
asset), not a conservative estimate. It was silent — no flag, no log — and it
fed straight into `overnight_cost_pv`, which the frontend's Capacity
Expansion tab displays under "Total investment (PV)" whenever the user
switches to lifetime cost mode.

Two things change:

  1. The resolve is widened before the substitution is even needed: this
     function now enters `with_periodized_cost_defaults(..., for_back_
     calculation=True)`, the same opt-in fill `fd4009ae` added for
     `cost_breakdown`'s Line CAPEX. It supplies `discount_rate` (never
     `lifetime` — see the retirement-regression test below) to assets priced
     through `capital_cost` alone, letting PyPSA's own back-calculation
     succeed where it used to raise for the whole component class. Lines are
     the textbook case: PyPSA-Eur never sets `overnight_cost` on them.
  2. Where it genuinely still cannot be resolved, `overnight_cost` /
     `overnight_cost_pv` come back `None` and `overnight_cost_available` is
     `False` — never a number silently borrowed from a different unit.

Each test below names the mutation that makes it fail, verified by actually
making that change, not by reading the code.
"""
from __future__ import annotations

import math

import pypsa
import pytest

from services.solver_service import (
    SolverConfig,
    fill_periodized_cost_defaults,
    periodized_capital_costs,
)


def _line_only_network(discount_rate_fill_would_divide_by_zero: bool = False) -> pypsa.Network:
    """
    A Line priced through `capital_cost` alone — no `overnight_cost`, no
    `discount_rate`, `lifetime` at PyPSA's own default of +inf. Exactly the
    shape `fd4009ae` diagnosed: PyPSA-Eur never sets `overnight_cost` on
    Lines, so `n.c["Line"].overnight_cost` raises ValueError for the whole
    class unless something fills `discount_rate`.
    """
    n = pypsa.Network()
    n.add("Bus", "a")
    n.add("Bus", "b")
    n.add(
        "Line", "L",
        bus0="a", bus1="b", s_nom=500.0, x=0.1, r=0.01,
        capital_cost=1_000_000.0,
    )
    return n


# ── Test 2: derivable case gets a REAL, distinct number ────────────────────

def test_line_overnight_cost_is_derived_not_left_at_annualised_value():
    """
    Fails if: the `for_back_calculation=True` fill is dropped from the
    `with_periodized_cost_defaults` call inside `periodized_capital_costs` —
    without it, `n.c["Line"].overnight_cost` raises for the whole class (no
    `discount_rate`), `upfront_series` becomes `None`, and this asset falls
    into the "genuinely unresolved" branch instead of resolving for real.

    The oracle is independent of the function under test: a second, freshly
    built network gets the identical fill applied by hand and PyPSA's own
    `overnight_cost` accessor read directly.
    """
    n = _line_only_network()
    cfg = SolverConfig()  # discount_rate defaults to 0.07

    out = periodized_capital_costs(n, cfg)
    entry = out["lines"]["L"]

    # Independent oracle: same fill, read PyPSA's own accessor directly.
    oracle_n = _line_only_network()
    oracle_n.lines.loc["L", "discount_rate"] = cfg.discount_rate
    expected = float(oracle_n.c["Line"].overnight_cost.loc["L"])
    assert math.isfinite(expected) and expected > 0, (
        "test setup bug: the oracle itself failed to back-calculate"
    )

    assert entry["overnight_cost_available"] is True
    assert entry["overnight_cost"] == pytest.approx(expected, rel=1e-9)
    assert entry["overnight_cost_pv"] == pytest.approx(expected, rel=1e-9)  # build_year=0 -> PV factor 1

    # Guard the fixture actually discriminates: the annualised rate is a
    # completely different quantity (EUR/MW/yr vs EUR/MW), not a smaller
    # version of the same number. Old buggy code reports these EQUAL.
    assert entry["capital_cost"] == pytest.approx(1_000_000.0)
    assert entry["overnight_cost"] != pytest.approx(entry["capital_cost"], rel=1e-6)


# ── Test 1: genuinely unresolved is reported as such, never substituted ────

def test_genuinely_unresolvable_upfront_cost_is_not_the_annualised_rate():
    """
    Fails if: the `v_upf = v_ann` substitution is restored (the original
    defect) — `overnight_cost` would then equal `capital_cost` (1_000_000.0),
    an EUR/MW/yr rate silently reported as an EUR/MW lump sum.

    `discount_rate=0.0` against this Line's default `lifetime=+inf` makes
    PyPSA's own back-calculation divide by `annuity(0, inf) == 0`, producing
    `inf` — a genuinely unresolvable case even with the widened fill, since
    the fill supplies exactly the discount_rate that causes the division.
    """
    n = _line_only_network()
    cfg = SolverConfig(discount_rate=0.0)

    out = periodized_capital_costs(n, cfg)
    entry = out["lines"]["L"]

    assert entry["overnight_cost_available"] is False
    assert entry["overnight_cost"] is None
    assert entry["overnight_cost_pv"] is None
    # The annualised rate is unaffected (it never routes through overnight_cost
    # for a no-overnight_cost asset — see with_periodized_cost_defaults).
    assert entry["capital_cost"] == pytest.approx(1_000_000.0)


# ── Test 4: the lifetime-fill regression stays pinned ───────────────────────

def test_for_back_calculation_fill_never_touches_lifetime():
    """
    Fails if: `periodized_capital_costs` (or the fill it calls) starts
    supplying `lifetime` alongside `discount_rate` for the back-calculation
    population. PyPSA derives multi-period asset ACTIVITY from
    `build_year + lifetime` — substituting a finite default retires an asset
    at PyPSA's own default `build_year=0` clean out of any period beyond the
    substituted lifetime, which is exactly the bug `fd4009ae` fixed one layer
    up (Line's annualised CAPEX silently dropping to 0.00 on the golden
    fixture). The shipped fill writes `discount_rate` ONLY.

    Checked directly against the fill function `periodized_capital_costs`
    calls, before its `revert()` — mutation target is `fill_periodized_cost_
    defaults` filling `lifetime` for `back_calc` rows too.
    """
    n = _line_only_network()
    cfg = SolverConfig()
    assert math.isinf(float(n.lines.at["L", "lifetime"]))  # PyPSA's own default

    revert = fill_periodized_cost_defaults(n, cfg, for_back_calculation=True)
    try:
        assert float(n.lines.at["L", "discount_rate"]) == pytest.approx(cfg.discount_rate)
        assert math.isinf(float(n.lines.at["L", "lifetime"])), (
            "lifetime was filled for the back-calculation population — this "
            "retires assets at their default build_year in multi-period runs"
        )
    finally:
        revert()

    # And the revert genuinely reverts (no on-disk residue of the transient fill).
    assert math.isnan(float(n.lines.at["L", "discount_rate"]))
