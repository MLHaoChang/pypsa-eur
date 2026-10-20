"""The series a component carries now, as a pending artifact the refinement
ops act on (TS UI; gatekeeper plan M(a), 2026-10-20).

A raw upload or an earlier apply sits in the user-ts store on the SNAPSHOT
clock — converted once, at its first apply. The artifact keeps it there
(``source_clock: snapshot``): `apply.time_basis_for` maps it by identity, so
applying it with no ops writes back the same series, bit for bit.

* A full-year network: the stored series is the source year.
* A snapshot window (slice W): the source year is padded with NaN outside
  the window (``profile_annual_basis: window_mean``). Apply reads only the
  window's hours; shift and smooth are refused on it, rescale_energy states
  the window's MWh, and the energy check is recorded as not applicable.
* ``based_on_current`` fingerprints the stored series; apply refuses the
  artifact once that series has changed (`apply.CurrentChangedError`).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.apply import (
    WINDOW_MEAN_BASIS,
    CurrentSeriesError,
    _hourly_window,
    current_digest,
)

FAMILY_BY_ATTRIBUTE = {
    "p_set": "load", "p_max_pu": "availability", "p_min_pu": "availability",
    "marginal_cost": "series", "efficiency": "series", "inflow": "series",
}
# What a stored meta says about its own write, not about the shape: the new
# apply records these afresh.
_APPLY_RECORD = {"applied_at", "warnings", "modelled_mwh", "profile_annual_mwh",
                 "profile_annual_basis", "energy_check", "window", "dst_gap_shifted",
                 "leap_day", "tiled", "periods", "clipped", "time_basis_note",
                 "job_id", "points", "based_on", "project_id"}


def describe_current(ctx, component_type: str, attribute: str, component: str) -> dict:
    """What the component carries now: none, a generated series (its family
    and provenance) or an upload, which has no generator record."""
    key = (component_type, attribute, component)
    series = ctx.user_ts.get(key)
    if series is None:
        return {"exists": False}
    meta = ctx.user_ts_meta.get(key) or {}
    idx = series.index
    stamps = pd.DatetimeIndex(idx.get_level_values(-1) if isinstance(idx, pd.MultiIndex) else idx)
    return {
        "exists": True,
        "provenance": meta.get("provenance") or ("uploaded" if not meta else "synthetic/assumed"),
        "family": meta.get("family"),
        "source": meta.get("source") or ("upload" if not meta else None),
        "kind": meta.get("kind") or meta.get("archetype"),
        "rows": int(len(series)),
        "first": str(stamps[0]) if len(stamps) else None,
        "last": str(stamps[-1]) if len(stamps) else None,
        "applied_at": meta.get("applied_at"),
        "refinements": len(meta.get("refinements") or []),
    }


def series_from_current(ctx, component_type: str, attribute: str,
                        component: str) -> tuple[pd.Series, dict[str, Any]]:
    """(source-year series, meta) of the component's current series."""
    key = (component_type, attribute, component)
    series = ctx.user_ts.get(key)
    if series is None:
        raise CurrentSeriesError(
            f"{component!r} has no {attribute} series to refine; generate or "
            "upload one first")
    if isinstance(series.index, pd.MultiIndex):
        raise CurrentSeriesError(
            f"{component!r}'s {attribute} spans several investment periods; "
            "refining a tiled series is not supported — regenerate it instead")
    stored = dict(ctx.user_ts_meta.get(key) or {})
    idx = pd.DatetimeIndex(series.index)
    window = _hourly_window(idx, what="the current series")
    meta: dict[str, Any] = {k: v for k, v in stored.items() if k not in _APPLY_RECORD}
    meta.update({
        "family": stored.get("family") or FAMILY_BY_ATTRIBUTE.get(attribute, "series"),
        "target": [component_type, attribute],
        "source": "current",
        "source_clock": "snapshot",
        "based_on_current": current_digest(series),
        "provenance": stored.get("provenance") or "measured",
        "resolution": "hourly",
        "time_basis": stored.get("time_basis"),
    })
    if component_type == "loads" or attribute == "inflow":
        meta["unit"] = "MW"           # the stored values are already absolute
        meta.pop("scale_mw", None)
    values = series.to_numpy(dtype=float)
    if window is None:
        meta["weather_year"] = int(idx[0].year)
        return pd.Series(values, index=idx, name=component), meta
    leap = any(t.month == 2 and t.day == 29 for t in idx)
    year = int(idx[0].year)
    if leap and not pd.Timestamp(year=year, month=1, day=1).is_leap_year:
        year = next(y for y in range(year, year + 8)
                    if pd.Timestamp(year=y, month=1, day=1).is_leap_year)
    full = pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")
    padded = pd.Series(np.nan, index=full, name=component)
    for t, v in zip(idx, values):
        padded.loc[pd.Timestamp(year=year, month=t.month, day=t.day, hour=t.hour)] = v
    meta.update({"weather_year": year, "profile_annual_basis": WINDOW_MEAN_BASIS,
                 "source_window": window})
    return padded, meta
