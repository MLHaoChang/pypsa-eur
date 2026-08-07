"""
True horizon system cost, computed from an EXPLICIT (network, solver config).

Why this is a service and not just a call into
`routers/results.py::get_cost_breakdown`:

  * `get_cost_breakdown` resolves BOTH its inputs from ambient state —
    `PyPSAService.get_network()` and the foreground `_state["solver_config"]`.
  * The solve-queue dispatcher (`services/solve_queue.py`) prices a solved
    project whose network is NOT the resident one, using the QUEUE's config,
    and it does so OUTSIDE the `solving_context(ctx)` block. Calling the
    endpoint there would silently price the foreground project instead.

So the reporting path needs a function that takes what it should price.

The cost basis deliberately matches `/results/cost_breakdown`: PyPSA's
`n.statistics()` capital + operational expenditure per (component, carrier,
period), each cell scaled by `investment_period_weightings.years` for that
period. `tests/test_cost_totals_contract.py` asserts this function equals
`get_cost_breakdown()["total"]` on flat, multi-period and myopic networks so
the two implementations cannot drift apart unnoticed.

WHY THE LP OBJECTIVE IS NOT USABLE HERE (myopic): each myopic period is its
own LP. Capacity frozen by an earlier period is `p_nom_extendable=False`, and
PyPSA charges CAPEX only for extendables — the non-extendable capex is meant
to arrive via `n.objective_constant`, which is IDENTICALLY ZERO under
`multi_investment_periods=True` (its `define_objective` builds the multi-invest
constant but only appends it to `terms` in the single-period branch). Summing
the per-period LP objectives therefore counts each asset's CAPEX once, in its
build period, and never for the rest of its service life — measured at -42.9%
against this function on a 3-period system, and +22.2% the other way with
`lf_aggregate_future=True` (the lookahead window's future-period OPEX is
counted once in the lookahead and again when that period is solved).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from services.period_utils import (
    is_period_only,
    period_years_map,
    years_for_period,
)
from services.serialization import safe_float


def _detect_metric_period_levels(midx: pd.MultiIndex) -> tuple[int, int]:
    """
    Return ``(metric_level, period_level)`` for a statistics MultiIndex: the
    metric level holds strings like 'Capital Expenditure', the period level
    holds year ints. Mirrors the same helper in ``routers/results.py``.
    """
    metric_l: int | None = None
    period_l: int | None = None
    for i in range(midx.nlevels):
        vals = midx.get_level_values(i)
        if len(vals) == 0:
            continue
        if isinstance(vals[0], str):
            if metric_l is None:
                metric_l = i
        elif period_l is None:
            period_l = i
    if metric_l is None:
        metric_l = 0
    if period_l is None:
        period_l = 1 if metric_l == 0 else 0
    return metric_l, period_l


def _row_period(idx: Any, idx_is_multi: bool) -> int | None:
    """Year carried by the OUTERMOST row-index level, when there is one."""
    if not (idx_is_multi and isinstance(idx, tuple) and len(idx) >= 1):
        return None
    try:
        p = int(idx[0])
    except (TypeError, ValueError):
        return None
    return p if 1900 <= p <= 2200 else None


def horizon_system_cost(n, cfg) -> float | None:
    """
    Total system cost over the whole horizon: CAPEX + OPEX, years-weighted.

    Returns ``None`` when the network carries no usable statistics (unsolved,
    or PyPSA returned an empty frame) so callers can fall back rather than
    report a confident zero.

    Safe to call on any solve strategy — for full-horizon runs it agrees with
    the LP objective, which is why the perfect-foresight case needs no special
    handling in the callers.
    """
    # Local import: services.solver_service imports plenty of this package, and
    # routers.results imports solver_service — keep this module leaf-ish.
    from services.solver_service import with_periodized_cost_defaults

    try:
        with with_periodized_cost_defaults(n, cfg):
            stats = n.statistics()
    except Exception:
        return None
    if stats is None or stats.empty:
        return None

    period_years = period_years_map(n)

    def years(p: Any) -> float:
        return years_for_period(period_years, p)

    cols_are_multi = isinstance(stats.columns, pd.MultiIndex)
    idx_is_multi = isinstance(stats.index, pd.MultiIndex)

    capex_col = opex_col = None
    col_metric_level = col_period_level = 0
    if cols_are_multi:
        col_metric_level, col_period_level = _detect_metric_period_levels(stats.columns)
    else:
        capex_col = next(
            (c for c in stats.columns if isinstance(c, str) and "capital" in c.lower()),
            None,
        )
        opex_col = next(
            (c for c in stats.columns
             if isinstance(c, str) and "operational" in c.lower()),
            None,
        )
        if capex_col is None or opex_col is None:
            return None

    total = 0.0
    for idx, row in stats.iterrows():
        row_period = _row_period(idx, idx_is_multi)
        if isinstance(idx, tuple):
            levels = idx[1:] if row_period is not None else idx
            comp = str(levels[0]) if len(levels) >= 1 else ""
        else:
            comp = str(idx)
        # PyPSA emits bare-year "period total" rows alongside the real ones;
        # counting those would double the horizon.
        if not comp or is_period_only(comp):
            continue

        if cols_are_multi:
            for col, val in row.items():
                if not isinstance(col, tuple) or len(col) < 2:
                    continue
                metric = col[col_metric_level]
                if not isinstance(metric, str):
                    continue
                ml = metric.lower()
                if "capital" not in ml and "operational" not in ml:
                    continue
                period = col[col_period_level] if row_period is None else row_period
                total += (safe_float(val) or 0.0) * years(period)
        else:
            # Flat columns: PyPSA already aggregated across periods. A row that
            # still carries a period gets that period's years; otherwise the
            # value is a horizon total already.
            mul = years(row_period) if row_period is not None else 1.0
            cx = safe_float(row[capex_col]) or 0.0
            ox = safe_float(row[opex_col]) or 0.0
            total += (cx + ox) * mul

    return total
