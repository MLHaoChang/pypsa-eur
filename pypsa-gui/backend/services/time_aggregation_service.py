"""
Representative-period aggregation for limited-foresight planning.

Phase 2 of the myopic-foresight rolling planner needs each iteration to look
like this:

    [ current period: 8760 snapshots at full hourly detail ]
    [ each future period: K representative periods × hoursPerPeriod each,
      with weight overrides so one representative stands in for N actual
      periods of operation ]

This module wraps `tsam` (already a transitive PyPSA dependency, verified at
import time) to do the clustering. Results are cached per-(period,
cfg-fingerprint) so repeat iterations of the rolling loop don't re-cluster
the same period.

Public entry-point:
    aggregate_period_snapshots(n, period, cfg) -> AggregationResult

The result carries the representative MultiIndex slice plus the per-snapshot
weight multipliers the LP must apply to ``snapshot_weightings`` for that
slice — the rolling driver writes those into ``n.snapshot_weightings`` and
records the originals in the shared undo list.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AggregationResult:
    """
    Output of one period's aggregation.

    Attributes
    ----------
    snapshots : pd.MultiIndex
        Slice of n.snapshots — `K × hoursPerPeriod` consecutive snapshots,
        one block per cluster centre. Already in the original (period,
        timestep) MultiIndex shape so it concatenates with the current
        period's slice via `pd.MultiIndex.append`.
    weights : pd.Series
        Per-snapshot multiplier (cluster size). The rolling driver writes
        these into every weighting column of `n.snapshot_weightings`
        (objective, generators, stores) so the LP's cost / energy / store
        terms scale up to represent the full period's operation.
    typical_periods : int
        How many clusters were produced (may differ from cfg.lf_k_periods
        when extreme periods are appended or when the period has fewer
        sub-periods than k).
    """

    snapshots: pd.MultiIndex
    weights: pd.Series
    typical_periods: int


# Cache keyed by (network-identity, period, fingerprint). The network-identity
# token (id(n)) namespaces entries PER NETWORK OBJECT — so two concurrent solves
# (a B4.3 background solve + a foreground /run, each on its OWN ProjectContext's
# network) can't collide on each other's representative-period clusters when they
# share the same (period, cfg_fingerprint). Without it the cache was keyed only
# `(period, fingerprint)` — network-identity-free — and the AggregationResult is
# DERIVED from the network's snapshots/profiles, so a same-period+same-cfg
# collision served the wrong network's clusters (C2 end-to-end QA finding).
# `_cache_lock` guards the dict against concurrent get/set from the two solve
# threads (plain dict ops are GIL-atomic, but the check-then-set in
# aggregate_period_snapshots is not). id(n) is safe as a key because a live
# network object is reachable for the whole solve (the solving ctx holds it);
# stale entries from GC'd networks are dropped by `_clear_swap_caches` on every
# network swap and are bounded by the resident cap regardless.
_cache: dict[tuple, AggregationResult] = {}
_cache_lock = threading.Lock()


def _cfg_fingerprint(cfg) -> tuple:
    """
    Stable hash of the aggregation-relevant cfg fields. New entries here
    when adding more knobs must be reflected in the fingerprint so cached
    entries from the previous cfg get invalidated.
    """
    return (
        int(getattr(cfg, "lf_k_periods", 8)),
        int(getattr(cfg, "lf_period_length_h", 168)),
        str(getattr(cfg, "lf_cluster_method", "hierarchical")),
        bool(getattr(cfg, "lf_include_extreme", True)),
    )


def _store(cache_key: tuple, result: AggregationResult) -> AggregationResult:
    """Cache the result under `_cache_lock` and return it, for tail-return use."""
    with _cache_lock:
        _cache[cache_key] = result
    return result


def clear_cache() -> None:
    """
    Drop every cached aggregation. Call after a network mutation that
    invalidates the load / renewable profiles — e.g. a fresh import or
    snapshot reshape. Cheap (just frees the dict).
    """
    with _cache_lock:
        _cache.clear()


def aggregate_period_snapshots(
    n,
    period,
    cfg,
) -> AggregationResult:
    """
    Cluster `period`'s hourly snapshots into representative periods.

    Falls back to returning the original (un-aggregated) period when:
      - `tsam` is unavailable,
      - the period is too short for the requested k × hoursPerPeriod,
      - clustering raises (e.g. degenerate features).
    Caller should treat the fallback as a no-op aggregation: full weights
    (1.0 everywhere), full snapshot set returned.
    """
    # Network-identity in the key so concurrent solves on different network
    # objects (background vs foreground) never collide on a shared (period, cfg).
    cache_key = (id(n), period, _cfg_fingerprint(cfg))
    with _cache_lock:
        cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    period_mask = n.snapshots.get_level_values(0) == period
    period_snapshots = n.snapshots[period_mask]
    if len(period_snapshots) == 0:
        # Defensive — should never happen for periods in n.investment_periods.
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(dtype=float),
            typical_periods=0,
        )
        return _store(cache_key, result)

    k = int(getattr(cfg, "lf_k_periods", 8))
    hours_per_period = int(getattr(cfg, "lf_period_length_h", 168))
    cluster_method = str(getattr(cfg, "lf_cluster_method", "hierarchical"))
    include_extreme = bool(getattr(cfg, "lf_include_extreme", True))

    n_periods_in_data = len(period_snapshots) // hours_per_period
    # No-aggregation cases: too few periods to cluster, or k would represent
    # >= 100% of the data so there's no compression. Return the full slice
    # with unit weights so the caller can treat the result uniformly.
    if n_periods_in_data <= k or k <= 0:
        logger.info(
            "limited_foresight: period %s has %d sub-periods of %dh, "
            "k=%d — no aggregation needed.",
            period, n_periods_in_data, hours_per_period, k,
        )
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(1.0, index=period_snapshots),
            typical_periods=n_periods_in_data,
        )
        return _store(cache_key, result)

    try:
        from tsam.timeseriesaggregation import TimeSeriesAggregation
    except ImportError:
        logger.warning("limited_foresight: tsam not available — falling back to full period.")
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(1.0, index=period_snapshots),
            typical_periods=n_periods_in_data,
        )
        return _store(cache_key, result)

    # Build the feature matrix tsam clusters on. The two signals that
    # actually drive multi-period capacity decisions in PyPSA networks are
    # (a) the aggregated load profile and (b) the renewable availability
    # profile. We include both so a "drought week" with low renewables
    # lands in its own cluster (especially when include_extreme=True picks
    # the renewable-min period).
    features = pd.DataFrame(index=period_snapshots)
    try:
        if hasattr(n, "loads_t") and not n.loads_t.p_set.empty:
            load_slice = n.loads_t.p_set.loc[period_snapshots]
            if not load_slice.empty:
                features["load_total"] = load_slice.sum(axis=1)
    except Exception:
        pass
    try:
        if hasattr(n, "generators_t"):
            pmax_pu = getattr(n.generators_t, "p_max_pu", None)
            if pmax_pu is not None and not pmax_pu.empty:
                pmax_slice = pmax_pu.loc[
                    pmax_pu.index.intersection(period_snapshots)
                ]
                if not pmax_slice.empty:
                    features["renew_avg"] = pmax_slice.mean(axis=1).reindex(
                        period_snapshots, fill_value=0.0,
                    )
    except Exception:
        pass

    if features.shape[1] == 0:
        logger.warning(
            "limited_foresight: period %s has no clusterable features "
            "(no load, no p_max_pu profiles) — falling back to full period.",
            period,
        )
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(1.0, index=period_snapshots),
            typical_periods=n_periods_in_data,
        )
        return _store(cache_key, result)

    # tsam needs either a DatetimeIndex or an explicit `resolution` (hours)
    # to slice the input into periods. Extract the timestep level off the
    # MultiIndex so tsam sees real timestamps — keeps `resolution=` inferred
    # automatically and matches what tsam.aggregate() expects internally.
    try:
        timestep_idx = period_snapshots.get_level_values(1)
    except Exception:
        timestep_idx = pd.RangeIndex(len(period_snapshots))
    features_ri = features.copy()
    features_ri.index = timestep_idx

    # Trim to whole periods — tsam rejects partial-period inputs. PyPSA's
    # snapshots are usually a clean year, but be defensive when the user
    # configured an irregular range.
    usable_len = (len(features_ri) // hours_per_period) * hours_per_period
    features_ri = features_ri.iloc[:usable_len]
    if usable_len == 0:
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(1.0, index=period_snapshots),
            typical_periods=n_periods_in_data,
        )
        return _store(cache_key, result)

    # Extreme-period configuration: include the highest-load period and the
    # lowest-renewable period as their own clusters. Without these, the
    # aggregation often under-sizes capacity because the LP only ever sees
    # the "average" peak.
    add_peak_max: list[str] | None = None
    add_peak_min: list[str] | None = None
    extreme_method: str | None = None
    if include_extreme:
        if "load_total" in features_ri.columns:
            add_peak_max = ["load_total"]
        if "renew_avg" in features_ri.columns:
            add_peak_min = ["renew_avg"]
        extreme_method = "append" if (add_peak_max or add_peak_min) else None

    # Build kwargs conditionally — tsam 3.0.0's validation rejects
    # `extremePeriodMethod=None` (only literal 'None' string OR omitted is
    # accepted) and complains when `addPeakMax`/`addPeakMin` are passed as
    # None instead of being absent. Cleaner to filter at the call site.
    tsam_kwargs: dict[str, object] = {
        "timeSeries": features_ri,
        "noTypicalPeriods": k,
        "hoursPerPeriod": hours_per_period,
        "clusterMethod": cluster_method,
        "rescaleClusterPeriods": False,
        "sortValues": False,
    }
    if extreme_method:
        tsam_kwargs["extremePeriodMethod"] = extreme_method
    if add_peak_max:
        tsam_kwargs["addPeakMax"] = add_peak_max
    if add_peak_min:
        tsam_kwargs["addPeakMin"] = add_peak_min
    try:
        agg = TimeSeriesAggregation(**tsam_kwargs)
        agg.createTypicalPeriods()
    except Exception as exc:  # noqa: BLE001 — broad on purpose: tsam raises a zoo of errors
        logger.warning(
            "limited_foresight: tsam clustering failed for period %s (%s) — "
            "falling back to full period.", period, exc,
        )
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(1.0, index=period_snapshots),
            typical_periods=n_periods_in_data,
        )
        return _store(cache_key, result)

    # `clusterCenterIndices` are integer period indices in 0..n_periods_in_data−1
    # identifying which ORIGINAL periods serve as the cluster medoids. Each
    # cluster ID matches a position in clusterCenterIndices; the number of
    # occurrences comes from clusterPeriodNoOccur.
    center_indices = list(agg.clusterCenterIndices)
    occur_dict = dict(agg.clusterPeriodNoOccur)

    repr_positions: list[int] = []
    repr_weights: list[float] = []
    for cluster_id, period_idx in enumerate(center_indices):
        start = int(period_idx) * hours_per_period
        end = min(start + hours_per_period, usable_len)
        count = float(occur_dict.get(cluster_id, 1))
        for pos in range(start, end):
            repr_positions.append(pos)
            repr_weights.append(count)

    if not repr_positions:
        logger.warning("limited_foresight: tsam returned 0 representative hours; falling back.")
        result = AggregationResult(
            snapshots=period_snapshots,
            weights=pd.Series(1.0, index=period_snapshots),
            typical_periods=n_periods_in_data,
        )
        return _store(cache_key, result)

    # Map positional indices back into the original MultiIndex so the slice
    # composes with the un-aggregated current-period slice via append().
    repr_snapshots = period_snapshots[repr_positions]
    weights_series = pd.Series(repr_weights, index=repr_snapshots)
    # Rescale so the per-period weight sum equals the period's ORIGINAL
    # snapshot count. tsam's clusterPeriodNoOccur sums to
    # `usable_len // hours_per_period`, which excludes any partial trailing
    # period (e.g. 8760 h / 168 h = 52.14 → tsam sees 52 periods, total
    # weight 52×168=8736 ≠ 8760). Without this rescale, `n.nyears` differs
    # between the un-aggregated current period (1.0) and the aggregated
    # future periods (0.997) and PyPSA's `periodized_cost` rejects the
    # combination with "overnight_cost cannot be used when investment
    # periods have different durations".
    weight_sum = float(weights_series.sum())
    if weight_sum > 0:
        scale = float(len(period_snapshots)) / weight_sum
        weights_series = weights_series * scale
    result = AggregationResult(
        snapshots=repr_snapshots,
        weights=weights_series,
        typical_periods=len(center_indices),
    )
    _store(cache_key, result)  # locked write (every cache write goes through _store)
    logger.info(
        "limited_foresight: period %s → %d representative period(s) × %d h "
        "= %d snapshots (was %d), avg weight %.2f.",
        period, len(center_indices), hours_per_period,
        len(repr_snapshots), len(period_snapshots),
        float(weights_series.mean()),
    )
    return result
