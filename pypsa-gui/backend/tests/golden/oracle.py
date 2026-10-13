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


def fixed_cost_rate(annualised_investment: float, fom_cost: float) -> float:
    """
    What the LP objective multiplies each MW of optimised capacity by.

    MEASURED against PyPSA 1.1.2 on 2026-09-26: `Component.periodized_cost`
    (the accessor `optimize.py` reads) is `capital_cost + fom_cost`;
    `Component.capital_cost` — and therefore `statistics.capex()` — is the
    investment share alone. Every surface's "fixed cost" / "CAPEX" must use
    the sum, or it disagrees with the objective by `fom_cost x p_nom_opt`.
    """
    return annualised_investment + fom_cost


def fom_per_horizon(annual_fom: float, snapshots_per_period: int) -> float:
    """
    An annual fixed O&M (EUR/MW/yr, what the GUI asks for) on the per-period
    basis the LP charges: scaled by the share of a year one period models,
    exactly like `annualised_capital_cost` scales an overnight investment.

    PyPSA itself adds `fom_cost` UNSCALED (MEASURED 2026-09-27); the GUI's
    periodized-cost fill applies this scaling around the solve and every
    report so a typed annual FOM is not charged as if one day were a year.
    """
    return annual_fom * (snapshots_per_period / HOURS_PER_YEAR)


def capital_cost_per_horizon(annual_capital_cost: float, snapshots_per_period: int) -> float:
    """
    A `capital_cost` typed directly (annualised EUR/MW/yr — the GUI's unit)
    on the per-period basis the LP charges. Same share-of-a-year scaling as
    `annualised_capital_cost` applies to an overnight investment and
    `fom_per_horizon` to FOM.

    PyPSA itself uses a directly typed `capital_cost` UNSCALED, per modelled
    horizon (MEASURED 2026-09-27); the GUI's periodized-cost fill applies this
    scaling around the solve and every report.
    """
    return annual_capital_cost * (snapshots_per_period / HOURS_PER_YEAR)


# ── Pro forma (plan S5): discounting, IRR and payback, from first principles ──
#
# The cash-flow vector is indexed by year, year 0 first (the build year, not
# discounted). These are the textbook definitions, written out here so the
# pro forma is checked against arithmetic it does not share.

def npv(rate: float, cash_flows: list[float]) -> float:
    """Σ_t CF_t / (1 + r)^t, t = 0 .. n (year 0 undiscounted)."""
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cash_flows))


def irr(cash_flows: list[float], lo: float = -0.99, hi: float = 10.0,
        tol: float = 1e-12) -> float | None:
    """
    The rate at which `npv` is zero, by bisection on [lo, hi]; None when the
    NPV has the same sign at both ends (no root bracketed, e.g. no sign
    change in the cash flows).
    """
    f_lo, f_hi = npv(lo, cash_flows), npv(hi, cash_flows)
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None
    for _ in range(500):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, cash_flows)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


def payback(cash_flows: list[float]) -> float | None:
    """
    Years until the cumulative cash flow first reaches zero, interpolated
    linearly inside the crossing year; None when it never does.
    """
    cum = 0.0
    for t, cf in enumerate(cash_flows):
        before = cum
        cum += cf
        if t > 0 and before < 0.0 <= cum:
            return (t - 1) + (-before) / cf
    return None


def discounted(rate: float, cash_flows: list[float]) -> list[float]:
    return [cf / (1.0 + rate) ** t for t, cf in enumerate(cash_flows)]


def annuity_pv_factor(rate: float, years: float) -> float:
    """Present value, one period before the first, of 1 per year for `years`."""
    if rate == 0:
        return float(years)
    return (1.0 - (1.0 + rate) ** -years) / rate


def battery_upfront_per_mw(inverter_eur_per_kw: float, storage_eur_per_kwh: float,
                           max_hours: float) -> float:
    """The battery's upfront investment per MW of power: inverter + hours x storage."""
    return (inverter_eur_per_kw + max_hours * storage_eur_per_kwh) * 1000.0
