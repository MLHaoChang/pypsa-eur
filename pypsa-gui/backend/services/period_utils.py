"""
Multi-investment-period weighting helpers — the single home for the
`investment_period_weightings.years` lookup that scales per-period quantities
into horizon totals.

Background: `n.statistics()` (and the per-period cost/energy roll-ups) return
values weighted by `snapshot_weightings` but NOT by
`investment_period_weightings.years`. To turn a per-period number into the
user-facing horizon total you multiply each period's value by that period's
`years` (e.g. a 2026+2031 horizon with years=5+5 is otherwise ~5× too small).

This logic was previously a verbatim-triplicated nested closure
(`_years_for_period`) plus an inline `{period: years}` build loop scattered
across the results endpoints. Collapsing it here keeps the weighting basis in
ONE place so a fix (or a PyPSA API change to `investment_period_weightings`)
lands once.

LEAF module: imports only stdlib + pandas. Must never import `routers.*` or
other `services.*` so anything can depend on it without a cycle.

What is intentionally NOT here (it is domain logic that legitimately differs
per caller, not mechanical lookup): the cost-vs-energy weighting-column choice
(`snapshot_weightings["objective"]` for cost vs `["generators"]` for energy —
see `routers/compare.py::_build_snapshot_weights`), and the INTENSIVE-metric
carve-out (capacity_factor / market_value are period-weighted-AVERAGED, not
years-scaled). Those stay with their callers.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def is_multi_period(n) -> bool:
    """True when the network's snapshots are a (period, timestep) MultiIndex."""
    return isinstance(getattr(n, "snapshots", None), pd.MultiIndex)


def period_years_map(n) -> dict:
    """
    `{period -> investment_period_weightings.years}` for a multi-period network.

    Keys are coerced to int (PyPSA's year convention) with a fall-back to the
    raw key if coercion fails; NaN year values collapse to 1.0. Returns an
    EMPTY dict for flat networks or on any error — callers treat "no map" as
    "every period weighs 1.0" (see `years_for_period`).
    """
    out: dict = {}
    if not is_multi_period(n):
        return out
    try:
        ipw = n.investment_period_weightings
        if "years" in ipw.columns:
            for p, y in ipw["years"].items():
                yv = float(y) if y == y else 1.0  # NaN (y != y) -> 1.0
                try:
                    out[int(p)] = yv
                except (TypeError, ValueError):
                    out[p] = yv
    except Exception:
        return {}
    return out


def years_for_period(years_map: dict, p: Any) -> float:
    """
    `years_map[p]` with int-coercion of the key, falling back to 1.0 when the
    period is unknown or the map is empty (flat network). Pair with
    `period_years_map(n)`.
    """
    if not years_map:
        return 1.0
    try:
        return years_map.get(int(p), 1.0)
    except (TypeError, ValueError):
        return years_map.get(p, 1.0)


def snapshot_weights(n, column: str = "objective", sns=None) -> pd.Series:
    """
    Σ-weight per snapshot = ``snapshot_weightings[column]`` × that snapshot's
    ``investment_period_weightings.years``.

    ``sns`` defaults to ``n.snapshots`` but may be an explicit SUBSET of them.
    That matters for the solver: myopic foresight and the SCLOPF path weight
    only the snapshots the current LP actually spans
    (``sns[period_level == current_period]``), and silently substituting the
    full horizon there would inflate every reported cost.

    This is THE weighting basis for turning per-snapshot quantities into
    horizon totals. Getting it wrong is the "~5× too small" bug class this
    module exists to prevent: `snapshot_weightings` alone omits the per-period
    years multiplier, so a 2026+2031 horizon with years=5+5 under-reports by 5×.

    ``column`` stays a caller parameter because the cost-vs-energy choice is
    domain logic, not mechanical lookup (see the module docstring):
      * ``"objective"`` — COST quantities (OPEX, revenue, CAPEX commitment).
      * ``"generators"`` — ENERGY quantities (dispatch GWh, served load,
        emissions). This is the column PyPSA's ``n.statistics()`` and the
        Results-tab energy KPIs use; summing energy on the ``objective``
        column diverges from the Results tab whenever the two differ (e.g.
        representative-week runs).

    Degrades in three steps so a malformed netcdf can't take a view down:
    requested column → ``"objective"`` → 1.0 per snapshot. A flat network
    returns the unscaled snapshot weights.
    """
    if sns is None:
        sns = getattr(n, "snapshots", None)
    if sns is None:
        return pd.Series(dtype=float)
    try:
        sw = n.snapshot_weightings.loc[sns, column].astype(float)
    except Exception:
        # Requested column missing (e.g. an older netcdf with no "generators"
        # column) — fall back to "objective" before the all-1.0 last resort so
        # a representative-week weighting isn't silently dropped.
        try:
            sw = n.snapshot_weightings.loc[sns, "objective"].astype(float)
        except Exception:
            sw = pd.Series(1.0, index=sns, dtype=float)
    if not isinstance(sns, pd.MultiIndex):
        return sw
    years_map = period_years_map(n)
    if not years_map:
        return sw
    try:
        years_s = pd.Series(
            [years_for_period(years_map, p) for p in sns.get_level_values(0)],
            index=sns, dtype=float,
        )
        return sw * years_s
    except Exception:
        return sw


def is_period_only(x: Any) -> bool:
    """
    True when `x` is a bare period/year key (e.g. `2025`) rather than a real
    component/carrier label. PyPSA's multi-period `n.statistics()` sometimes
    emits per-period TOTAL rows alongside the `(period, component, carrier)`
    breakdown; those show up as bare integer years and must be skipped so they
    don't leak into per-component buckets as a spurious "2025" entry.
    """
    s = str(x).strip()
    return s.isdigit() and 1900 <= int(s) <= 2200
