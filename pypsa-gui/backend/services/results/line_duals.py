"""
Lifted from `routers.results` (get_line_duals).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

from services.period_utils import (
    period_years_map,
    years_for_period,
)



def compute_line_duals(n, *, result_df):
    """
    Congestion rent from the LP's line-flow duals.

    Lifted from `routers.results.get_line_duals`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import math as _math
    mu_up = result_df(n, "lines_t", "mu_upper", "lopf")
    mu_lo = result_df(n, "lines_t", "mu_lower", "lopf")
    p0    = result_df(n, "lines_t", "p0",       "lopf")
    if (mu_up is None or mu_up.empty) and (mu_lo is None or mu_lo.empty):
        return {"rows": [], "note": "No LP duals captured. Re-run the solve "
                "(assign_all_duals is enabled by default in this build)."}

    try:
        weights = n.snapshot_weightings.objective
    except Exception:
        weights = None

    # Per-period years scaling — mirror get_cost_breakdown so congestion rent
    # is comparable to other € totals shown in the UI. Without this, networks
    # with non-unit `investment_period_weightings.years` understate rent by
    # the years factor.
    import pandas as _pd_ld
    is_multi_period_ld = isinstance(n.snapshots, _pd_ld.MultiIndex)
    period_weight_series = None
    if is_multi_period_ld:
        try:
            years_lookup = period_years_map(n)
            if years_lookup:
                period_lvl = n.snapshots.get_level_values(0)
                period_weight_series = _pd_ld.Series(
                    [years_for_period(years_lookup, p) for p in period_lvl],
                    index=n.snapshots, dtype=float,
                )
        except Exception:
            period_weight_series = None

    # Take the absolute value once — mu_lower is reported as ≤ 0 by PyPSA's
    # sign convention; in plain English a binding lower bound has positive
    # rent magnitude. Pre-fill NaN with 0 so missing-dual snapshots don't
    # propagate through the aggregations.
    mu_up_abs = mu_up.abs().fillna(0.0) if mu_up is not None else None
    mu_lo_abs = mu_lo.abs().fillna(0.0) if mu_lo is not None else None

    line_names = list(n.lines.index)
    rows: list[dict] = []
    for name in line_names:
        u = mu_up_abs[name] if (mu_up_abs is not None and name in mu_up_abs.columns) else None
        l = mu_lo_abs[name] if (mu_lo_abs is not None and name in mu_lo_abs.columns) else None
        # Skip lines that have no dual data at all (e.g. infeasible solves).
        if u is None and l is None:
            continue
        # Use max(|mu_upper|, |mu_lower|) per snapshot — only one bound binds
        # at a time, never both.
        combined = u if l is None else (l if u is None else u.where(u >= l, l))
        # Use a small tolerance for "non-zero" — LP duals can carry numerical
        # noise from interior-point solvers at the 1e-12 level.
        binding = combined > 1e-6
        n_binding = int(binding.sum())
        max_mu = float(combined.max()) if not combined.empty else 0.0
        if n_binding > 0:
            mean_mu = float(combined[binding].mean())
        else:
            mean_mu = 0.0
        # Congestion rent: ∫ |mu| × |p0| dt. The LP dual is shadow price per
        # unit capacity; multiplied by actual flow it approximates the
        # annual welfare value of redispatching out of this congestion.
        # Multi-period: also scale by investment_period_weightings.years
        # so the total reads as horizon-cumulative (matches cost_breakdown).
        if p0 is not None and name in p0.columns:
            p_abs = p0[name].abs().fillna(0.0)
            row_w = combined * p_abs
            if weights is not None:
                row_w = row_w * weights
            if period_weight_series is not None:
                row_w = row_w * period_weight_series
            rent = float(row_w.sum())
        else:
            rent = 0.0
        if not _math.isfinite(max_mu): max_mu = 0.0
        if not _math.isfinite(mean_mu): mean_mu = 0.0
        if not _math.isfinite(rent): rent = 0.0
        # Look up s_nom for context — "this line binds 50 % of hours at 100 MW
        # is more interesting than at 10 GW".
        try:
            s_nom = float(n.lines.at[name, "s_nom"])
        except Exception:
            s_nom = 0.0
        # Detect VOLL-bound binding hours: dual ≥ 10,000 €/MWh is almost
        # never physical congestion — it's a load-shedding signal where
        # the LP is choosing to shed rather than relax the line. Surface
        # this count so the UI can warn users that "congestion rent" on
        # this line largely reflects the cost of unmet demand, not the
        # value of transmission expansion.
        VOLL_THRESHOLD = 10_000.0
        voll_bound = int((combined > VOLL_THRESHOLD).sum())
        rows.append({
            "name": str(name),
            "s_nom_MW": s_nom if _math.isfinite(s_nom) else 0.0,
            "binding_hours": n_binding,
            "voll_bound_hours": voll_bound,
            "max_mu_eur_per_MWh": max_mu,
            "mean_mu_when_binding_eur_per_MWh": mean_mu,
            "congestion_rent_eur": rent,
        })

    rows.sort(key=lambda r: r["congestion_rent_eur"], reverse=True)
    total_rent = sum(r["congestion_rent_eur"] for r in rows)
    return {
        "rows": rows,
        "total_congestion_rent_eur": float(total_rent),
        "n_snapshots": len(n.snapshots),
    }
