"""
Returns arithmetic (IC P4 plan C9). WP4.1 lands the core — `npv` and `irr`;
WP4.5 adds the rest (payback, DSCR / LLCR / PLCR, LCOE, solve-for-PPA).

Conventions (SAM, verified): end-of-year discounting with index 0 (the
financial-close year) undiscounted.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def npv(rate: float | None, cash) -> float | None:
    if rate is None:
        return None
    c = np.asarray(cash, dtype=float)
    if np.isnan(c).any():
        return None
    t = np.arange(len(c))
    return float(np.sum(c / (1.0 + rate) ** t))


def _sign_changes(c: np.ndarray) -> int:
    s = np.sign(c[np.abs(c) > 1e-12])
    return int(np.sum(s[1:] != s[:-1])) if len(s) > 1 else 0


def irr(cash) -> tuple[float | None, list[str]]:
    """The IRR of a cash series (plan C9): `brentq` on NPV over a bracket
    found by scanning r ∈ (−0.99, 10] for a sign change; the root closest to 0
    from above −0.99 (the conventional IRR). Returns (irr, flags): no sign
    change → (None, ["irr_not_established:no_sign_change"]); more than one sign
    change in the cash series → solved and flagged `irr_multiple_sign_changes`."""
    c = np.asarray(cash, dtype=float)
    if np.isnan(c).any():
        return None, ["irr_not_established:unknown_cash"]
    flags = []
    if _sign_changes(c) > 1:
        flags.append("irr_multiple_sign_changes")
    grid = np.concatenate([np.linspace(-0.99, -0.1, 90), np.linspace(-0.1, 1.0, 1101),
                           np.linspace(1.0, 10.0, 181)[1:]])
    vals = np.array([npv(r, c) for r in grid])
    roots = []
    for i in range(len(grid) - 1):
        a, b = vals[i], vals[i + 1]
        if a == 0.0:
            roots.append(grid[i])
        elif a * b < 0:
            roots.append(brentq(lambda r: npv(r, c), grid[i], grid[i + 1], xtol=1e-14,
                                rtol=1e-14, maxiter=200))
    if not roots:
        return None, flags + ["irr_not_established:no_sign_change"]
    best = min(roots, key=lambda r: abs(r))
    return float(best), flags
