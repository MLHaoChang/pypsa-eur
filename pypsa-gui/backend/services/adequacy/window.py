"""
The peak-coincidence window — ONE rule, shared (Phase 12b §2.3).

Extracted from the inline code in ``reserve_margin_facts`` so the gross
window (selected on demand) and the net-load window (selected on demand
minus profile-bearing availability) can only ever differ by the series they
are given. The rule is unchanged from Phase 8: the top 1 % of snapshots,
capped at 100 and never fewer than 1, with EVERY snapshot tied with the
Nth-highest value included — on a flat series that is the whole period, not
the first N by index order, which is what ``nlargest`` would give.

NaN is never selected: a NaN threshold makes ``>=`` false everywhere and the
window comes back EMPTY, which the payload reports as a status rather than
asserting against (the static-``p_set`` wart recorded in plan v5.1 §3).
"""
from __future__ import annotations

import pandas as pd


def peak_window(series: pd.Series, *, n_override: int | None = None) -> pd.Index:
    """The window's snapshot index for ``series``; ``n_override`` is the
    user's ``prm_peak_hours`` when set (spec §2.5)."""
    n_snaps = int(len(series))
    if not n_snaps:
        return series.index
    n_target = n_override or min(100, max(1, int(round(0.01 * n_snaps))))
    n_target = max(1, min(n_target, n_snaps))
    threshold = float(series.sort_values(ascending=False).iloc[n_target - 1])
    return series.index[series >= threshold]
