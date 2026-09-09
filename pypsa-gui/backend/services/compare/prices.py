"""
Prices comparison: bus marginal price statistics per carrier.

Moved from `routers/compare.py`. `routers.compare` re-exports every name here
under the same name — or wraps it, where the function now takes the solver
config / result lookup as keyword-only arguments instead of reading router
state — so no call site changed. See the decomposition spec, Phase 3 addendum.

math / pandas are imported locally inside functions where the router did the
same; module-level imports below are only what the bodies reference at module
scope.
"""
from __future__ import annotations

import numpy as np
from models.schemas import (
    CarrierPeriodValue,
    CarrierPriceStats,
    PricesComparison,
)
from services.compare.support import (
    _build_snapshot_weights,
)
from services.results.load_frames import corrected_marginal_prices


def _compute_prices_summary(n, periods, is_multi, has_solve) -> PricesComparison:
    """
    Marginal price view. Builds:

      * A sampled duration curve (101 points) over the flattened
        (bus × snapshot) marginal-price matrix. Sorted descending so
        index 0 is the peak-price point and index 100 the trough.
      * Per-period mean / median / p90 (top-decile, peak-hour proxy).

    Two design choices worth recording:

      1. Snapshot weighting is INCLUDED for the per-period mean/median/p90
         via ``np.repeat`` weights, but NOT for the duration-curve sampling
         (sampling 101 quantiles after a weighted sort gets fiddly fast,
         and the chart's purpose is shape comparison — small per-hour
         weight differences don't change the visual story).

      2. Bus-axis is also flattened — we treat every (bus, snapshot) pair
         as an independent observation. For locational-pricing networks
         that's exactly the spread the user wants to see; for single-bus
         networks each "row" of the matrix is identical and the curve
         degenerates to the snapshot price profile.
    """

    if not has_solve:
        return PricesComparison()
    # Curtailment-cost-corrected duals (same merit-order correction the Results
    # Prices tab applies), so the duration curve / mean / max / min match.
    # from_state=False → read this bundle's own buses_t, not the live snapshot.
    try:
        p_t = corrected_marginal_prices(n, from_state=False)
    except Exception:
        p_t = getattr(n.buses_t, "marginal_price", None) if hasattr(n, "buses_t") else None
    if p_t is None or p_t.empty:
        return PricesComparison()

    weights = _build_snapshot_weights(n)
    sns = n.snapshots

    # Flatten to 1-D array, dropping NaN/inf. For a 4×26k network this is
    # ~100k floats — fine for in-memory percentile.
    vals_flat = np.asarray(p_t.values).reshape(-1)
    finite_mask = np.isfinite(vals_flat)
    vals_clean = vals_flat[finite_mask]
    if vals_clean.size == 0:
        return PricesComparison(bus_count=len(p_t.columns))

    # Per-period stats setup. Replicate snapshot weight across the bus
    # dimension so the mean treats each bus-snapshot cell as one weighted
    # observation.
    n_buses = p_t.shape[1]
    w_arr = np.asarray(weights.values).reshape(-1, 1)  # snapshots × 1
    w_full = np.broadcast_to(w_arr, p_t.shape).reshape(-1)[finite_mask]

    # Weighted duration curve: sort descending, accumulate weights, sample
    # at 101 cumulative-weight points. For multi-period horizons the
    # weights already include `investment_period_weightings.years` via
    # `_build_snapshot_weights`, so hours from longer periods correctly
    # contribute more density to the curve. The previous implementation
    # used an unweighted np.sort + linear index sampling which under-
    # represented hours from periods with high `ipw.years` (e.g., a 5-year
    # period was treated equal to a 1-year period in the curve shape).
    order_desc = np.argsort(vals_clean)[::-1]
    sorted_desc = vals_clean[order_desc]
    sorted_weights_desc = w_full[order_desc]
    cum_w = np.cumsum(sorted_weights_desc)
    total_w = float(cum_w[-1]) if cum_w.size > 0 else 0.0
    if total_w > 0 and sorted_desc.size > 0:
        targets = np.linspace(0, total_w, 101)
        sample_idx = np.clip(np.searchsorted(cum_w, targets), 0, sorted_desc.size - 1)
        duration_curve = [float(x) for x in sorted_desc[sample_idx]]
    else:
        duration_curve = []

    def _stats(values, weights_arr):
        if values.size == 0 or weights_arr.sum() <= 0:
            return 0.0, 0.0, 0.0
        # Weighted mean (analytic), median + p90 from sorted + cumulative weight.
        mean = float((values * weights_arr).sum() / weights_arr.sum())
        order = np.argsort(values)
        sv = values[order]
        sw = weights_arr[order]
        cum = np.cumsum(sw)
        total = cum[-1]
        def _quantile(q):
            target = q * total
            idx = int(np.searchsorted(cum, target))
            idx = min(max(idx, 0), sv.size - 1)
            return float(sv[idx])
        return mean, _quantile(0.5), _quantile(0.9)

    mean_total, median_total, p90_total = _stats(vals_clean, w_full)
    mean_pp: dict[str, float] = {}
    median_pp: dict[str, float] = {}
    p90_pp: dict[str, float] = {}

    if is_multi:
        # Mask per period — repeat snapshot's period across the bus axis.
        period_lvl = np.asarray(sns.get_level_values(0))
        period_full = np.broadcast_to(period_lvl.reshape(-1, 1), p_t.shape).reshape(-1)[finite_mask]
        for p in periods:
            mask = period_full == p
            if not mask.any():
                continue
            mp, mdp, p9p = _stats(vals_clean[mask], w_full[mask])
            ps = str(p)
            mean_pp[ps] = mp
            median_pp[ps] = mdp
            p90_pp[ps] = p9p

    # Per-bus-carrier price stats. Group bus columns by their `carrier`
    # attribute, then compute the same weighted mean/median/p90 within
    # each group. Sector-coupled networks where electrical / H2 / heat
    # buses carry very different price regimes get a per-carrier table
    # row in the Compare View. Falls back gracefully to empty dict for
    # networks without a `buses.carrier` column.
    by_carrier_stats: dict[str, CarrierPriceStats] = {}
    buses_df = getattr(n, "buses", None)
    if (buses_df is not None and not buses_df.empty
            and "carrier" in buses_df.columns):
        carrier_for_bus: dict[str, str] = {}
        for bus_name in p_t.columns:
            if bus_name in buses_df.index:
                try:
                    raw_c = buses_df.at[bus_name, "carrier"]
                    c = str(raw_c).lower() if raw_c not in (None, "") else "unspecified"
                except Exception:
                    c = "unspecified"
                carrier_for_bus[bus_name] = c
        carrier_buses: dict[str, list[str]] = {}
        for bus_name, c in carrier_for_bus.items():
            carrier_buses.setdefault(c, []).append(bus_name)
        period_lvl_full = None
        if is_multi:
            try:
                period_lvl_full = np.asarray(sns.get_level_values(0))
            except Exception:
                period_lvl_full = None
        for c, bus_list in carrier_buses.items():
            if not bus_list:
                continue
            try:
                sub = p_t[bus_list].values
                sub_flat = sub.reshape(-1)
                sub_finite = np.isfinite(sub_flat)
                sub_clean = sub_flat[sub_finite]
                if sub_clean.size == 0:
                    continue
                sub_w_arr = np.broadcast_to(w_arr, sub.shape).reshape(-1)[sub_finite]
                cm, cmd, cp90 = _stats(sub_clean, sub_w_arr)
                cmean_pp: dict[str, float] = {}
                cmedian_pp: dict[str, float] = {}
                cp90_pp: dict[str, float] = {}
                if is_multi and period_lvl_full is not None:
                    sub_period = np.broadcast_to(
                        period_lvl_full.reshape(-1, 1), sub.shape
                    ).reshape(-1)[sub_finite]
                    for p in periods:
                        pmask = sub_period == p
                        if not pmask.any():
                            continue
                        mp, mdp, p9p = _stats(sub_clean[pmask], sub_w_arr[pmask])
                        ps = str(p)
                        cmean_pp[ps] = mp
                        cmedian_pp[ps] = mdp
                        cp90_pp[ps] = p9p
                by_carrier_stats[c] = CarrierPriceStats(
                    bus_count=len(bus_list),
                    mean_price=CarrierPeriodValue(total=cm, by_period=cmean_pp),
                    median_price=CarrierPeriodValue(total=cmd, by_period=cmedian_pp),
                    p90_price=CarrierPeriodValue(total=cp90, by_period=cp90_pp),
                )
            except Exception:
                pass

    # Extreme tail markers — the duration curve's first/last points can be
    # extreme single-bus-snapshot LP duals (constraint scarcity moments) that
    # dominate the y-axis and visually flatten the rest. Expose max/min
    # separately so the Compare View can show them as tooltips and optionally
    # clip the chart y-axis to a sensible band (e.g. p99.5).
    if sorted_desc.size > 0:
        try:
            max_price = float(sorted_desc[0])
            min_price = float(sorted_desc[-1])
        except (IndexError, TypeError, ValueError):
            max_price, min_price = 0.0, 0.0
    else:
        max_price, min_price = 0.0, 0.0
    return PricesComparison(
        duration_curve=duration_curve,
        mean_price=CarrierPeriodValue(total=mean_total, by_period=mean_pp),
        median_price=CarrierPeriodValue(total=median_total, by_period=median_pp),
        p90_price=CarrierPeriodValue(total=p90_total, by_period=p90_pp),
        max_price=max_price,
        min_price=min_price,
        bus_count=n_buses,
        by_carrier_stats=by_carrier_stats,
    )
