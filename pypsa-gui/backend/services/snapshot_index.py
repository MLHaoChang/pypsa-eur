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
