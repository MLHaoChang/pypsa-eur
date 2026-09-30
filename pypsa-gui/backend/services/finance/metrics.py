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
    """NPV at `rate`, end-of-year discounting, index 0 undiscounted (SAM). None
    for an unknown rate, a rate ≤ −100 % or unknown cash."""
    if rate is None or rate <= -1.0:
        return None
    c = np.asarray(cash, dtype=float)
    if np.isnan(c).any():
        return None
    t = np.arange(len(c))
    return float(np.sum(c / (1.0 + rate) ** t))


def _sign_changes(c: np.ndarray) -> int:
    s = np.sign(c[np.abs(c) > 1e-12])
    return int(np.sum(s[1:] != s[:-1])) if len(s) > 1 else 0


_GRID = np.concatenate([np.linspace(-0.99, -0.1, 90), np.linspace(-0.1, 1.0, 1101),
                        np.linspace(1.0, 10.0, 901)[1:]])


def irr(cash) -> tuple[float | None, list[str]]:
    """The IRR of a cash series (plan C9): NPV scanned over r ∈ [−0.99, 10]
    (vectorised), each sign change refined by `brentq`, the root closest to 0
    returned (the conventional IRR). Flags: `irr_not_established:no_sign_change`
    when the cash never changes sign (an all-zero series included — never a
    0 IRR, WP4.1 review #1); `irr_not_established:no_root_in_scan` when it does
    but no root lies in the scan range; `irr_multiple_sign_changes` when the
    cash changes sign more than once (solved, flagged)."""
    c = np.asarray(cash, dtype=float)
    if np.isnan(c).any():
        return None, ["irr_not_established:unknown_cash"]
    changes = _sign_changes(c)
    if changes == 0:
        return None, ["irr_not_established:no_sign_change"]
    flags = ["irr_multiple_sign_changes"] if changes > 1 else []
    t = np.arange(len(c))
    vals = (c[None, :] / (1.0 + _GRID[:, None]) ** t[None, :]).sum(axis=1)
    roots = []
    for i in range(len(_GRID) - 1):
        a, b = vals[i], vals[i + 1]
        if a == 0.0:
            roots.append(_GRID[i])
        elif a * b < 0:
            roots.append(brentq(lambda r: npv(r, c), _GRID[i], _GRID[i + 1], xtol=1e-14,
                                rtol=1e-14, maxiter=200))
    if vals[-1] == 0.0:
        roots.append(_GRID[-1])
    if not roots:
        return None, flags + ["irr_not_established:no_root_in_scan"]
    return float(min(roots, key=abs)), flags
