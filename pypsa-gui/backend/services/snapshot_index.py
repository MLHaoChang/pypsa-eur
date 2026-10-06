"""
The (period, timestep) MultiIndex builder.

Its own module because BOTH `routers/network.py`'s snapshot routes and
`services/user_timeseries.py` build this index, so it belongs to neither and
sits below both — the same reasoning that gave `services/solver/vintage_store.py`
its own file in Phase 1. Extracted verbatim; `routers.network` re-exports it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _infer_snapshot_freq(n) -> str | None:
    """
    The snapshot index's resolution, as a pandas offset alias ("h", "3h", "D").

    The Model Horizon page used to render its own form state here, which was
    seeded to "h" at mount and never read back from the network — so a
    3-hourly MultiIndex and a Daily flat index both reported "Hourly (h)".

    MultiIndex networks are measured over the FIRST period's slice only: the
    flattened timestep level contains a discontinuity at each period seam
    (period P's last hour → period P+1's first), which would read as irregular.

    `pd.infer_freq` is tried first because it names calendar frequencies ("D",
    "MS", "W") that a raw timedelta cannot. It returns None for a
    representative-week index — contiguous 168-hour blocks separated by gaps —
    whose resolution is nevertheless hourly, so fall back to the modal
    successive delta. Returns None when neither resolves; the UI renders that
    as "irregular" rather than guessing.
    """
    sns = n.snapshots
    try:
        if isinstance(sns, pd.MultiIndex):
            level0 = sns.get_level_values(0)
            if len(level0) == 0:
                return None
            first = level0[0]
            idx = pd.DatetimeIndex(sns[level0 == first].get_level_values(1))
        else:
            idx = pd.DatetimeIndex(sns)
    except (ValueError, TypeError):
        # `pd.DatetimeIndex(...)` raises (e.g. `DateParseError`, a `ValueError`
        # subclass) on a non-parseable object index. No current GUI path
        # produces one, but this helper runs unconditionally at the top of
        # `get_snapshots` — degrade to "irregular" rather than 500ing the
        # page's primary endpoint.
        return None
    if len(idx) < 2:
        return None
    try:
        inferred = pd.infer_freq(idx)
    except (ValueError, TypeError):
        inferred = None
    if inferred:
        return inferred
    deltas = idx.to_series().diff().dropna()
    if deltas.empty:
        return None
    modal = deltas.mode()
    if modal.empty:
        return None
    hours = modal.iloc[0].total_seconds() / 3600.0
    if hours <= 0:
        return None
    if hours == 1.0:
        return "h"
    if float(hours).is_integer():
        return f"{int(hours)}h"
    # Sub-hourly modal steps (a 15-minute axis with gaps, where infer_freq
    # gives up): name them in minutes, so callers that gate on sub-hourly
    # resolution (sample-weeks) cannot mistake a gappy 15-min axis for
    # "irregular" (Edge Investment Case WP1.0 review).
    minutes = hours * 60.0
    if float(minutes).is_integer():
        return f"{int(minutes)}min"
    return None


def hours_per_step(freq) -> float | None:
    """
    Length of one snapshot step in HOURS for a fixed pandas frequency
    ("15min" → 0.25, "h" → 1.0, "3h" → 3.0, "D" → 24.0).

    Snapshot weightings are hours (Edge Investment Case spec decision 18):
    that is what makes Σ p × w an energy in MWh at any resolution. Calendar
    frequencies with no fixed length ("MS", "W-MON" …) return None, and the
    caller keeps PyPSA's default rather than inventing a length.
    """
    if freq is None:
        return None
    try:
        from pandas.tseries.frequencies import to_offset
        from pandas.tseries.offsets import Tick

        off = to_offset(freq)
    except (ValueError, TypeError):
        return None
    from pandas.tseries.offsets import Day

    # pandas 3 makes `Day` a calendar offset rather than a Tick; a model day is
    # still 24 hours (WP1.0 review).
    if isinstance(off, Day):
        return 24.0 * off.n if off.n > 0 else None
    if not isinstance(off, Tick):
        return None
    hours = off.nanos / 3_600_000_000_000
    return hours if hours > 0 else None


def is_sub_hourly(freq) -> bool:
    h = hours_per_step(freq)
    return h is not None and h < 1.0


def _build_period_multiindex(periods, blocks) -> pd.MultiIndex:
    """
    Build the `(period, timestep)` snapshot MultiIndex from parallel `periods`
    (year keys) + per-period `blocks` (each a DatetimeIndex of that period's
    operational timesteps). To replicate ONE operational range under every
    period, pass `[idx] * len(periods)`.

    ALWAYS sets `mi.name = "snapshot"`. PyPSA's `set_snapshots(MultiIndex)` only
    inherits the name on flat→multi transitions; on multi→multi REBUILDS the new
    MultiIndex's name=None wins and propagates to every `_t` table — xarray then
    emits dim `dim_0` and the next LP fails on `.sel(snapshot=sns)`. Centralising
    the builder makes that footgun impossible to forget.
    """
    period_level = np.concatenate([np.full(len(blk), p) for p, blk in zip(periods, blocks)])
    timestep_level = pd.DatetimeIndex(np.concatenate([blk.values for blk in blocks]))
    mi = pd.MultiIndex.from_arrays(
        [period_level, timestep_level], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    return mi
