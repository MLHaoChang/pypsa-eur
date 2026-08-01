"""
Expected economics, computed from first principles.

This module MUST NOT import from `services/` or `routers/`. It exists to check
them from the outside; sharing their arithmetic would make every assertion a
tautology. `test_golden_oracle.py` enforces that with an AST scan.

The formulas below were verified empirically against PyPSA 1.1.2 on
2026-08-01. If PyPSA changes an upstream convention these tests fail and it
will be briefly ambiguous whether the app or this file is wrong. That is the
intended behaviour: a SILENT convention change is what produced NaN CAPEX
across every asset parameterised via overnight_cost.
"""
from __future__ import annotations

HOURS_PER_YEAR = 8760


def crf(rate: float, lifetime: float) -> float:
    """
    Capital recovery factor: r / (1 - (1+r)^-n).

    At r = 0 the closed form divides by zero, but the limit is straight-line
    depreciation, 1/n. PyPSA allows a zero discount rate, so the branch is
    reachable rather than defensive.
    """
    if rate == 0:
        return 1.0 / lifetime
    return rate / (1.0 - (1.0 + rate) ** -lifetime)


def annualised_capital_cost(
    overnight_cost: float,
    rate: float,
    lifetime: float,
    snapshots_per_period: int,
) -> float:
    """
    What PyPSA's components accessor returns for `capital_cost`, per MW.

    MEASURED: overnight x CRF x (snapshots_in_ONE_period / 8760). Exact at
    2, 24, 168 and 8760 snapshots.

    The scaling is the trap. Omit it and a 24-snapshot fixture is wrong by a
    factor of 365 — and the "fix" would be to break working code.

    Multi-period normalises by ONE period's snapshot count, not the total:
    2 periods x 24 snapshots gives 24/8760, never 48/8760.
    """
    return overnight_cost * crf(rate, lifetime) * (snapshots_per_period / HOURS_PER_YEAR)


def horizon_capex(rate_per_mw: float, p_nom_opt: float, years: tuple[int, ...]) -> float:
    """
    Total CAPEX over the planning horizon.

    `investment_period_weightings["years"]` is applied by the reporting layer,
    NOT baked into capital_cost — so it multiplies here rather than inside
    `annualised_capital_cost`.
    """
    return rate_per_mw * p_nom_opt * sum(years)
