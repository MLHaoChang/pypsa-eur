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
    TARGETS,
    WINDOW_MEAN_BASIS,
    CurrentSeriesError,
    ProfileApplyError,
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


class SeriesUploadError(ProfileApplyError):
    """A measured file that does not fit its field: the column, the model's
    hours or the values' units. The message says what to change."""


def series_from_upload(ctx, component_type: str, attribute: str, component: str,
                       frame: pd.DataFrame) -> tuple[pd.Series, dict[str, Any]]:
    """(source-year series, meta) of a measured file for one field, on the
    snapshot clock like the current series: the editor's "Measured file" tab.
    The file is the row's template layout (the model's snapshots, one column
    named after the component, or a single column); values are in the field's
    own units. Nothing is clipped here: a value outside the field's range is
    refused, so a percentage is not read as a share."""
    spec = TARGETS.get((component_type, attribute))
    if spec is None:
        raise SeriesUploadError(f"{attribute} takes no measured file")
    cols = [c for c in frame.columns if str(c) == component]
    if not cols and len(frame.columns) == 1:
        cols = list(frame.columns)
    if not cols:
        raise SeriesUploadError(
            f"the file has {len(frame.columns)} columns and none is named "
            f"{component!r}: name one column after it, or upload a single column "
            "(the row's template has the layout)")
    values = pd.to_numeric(frame[cols[0]], errors="coerce")
    n = ctx.network
    if isinstance(n.snapshots, pd.MultiIndex):
        raise SeriesUploadError(
            "the model has several investment periods and a measured file here "
            "covers one year; generate the series instead")
    snaps = pd.DatetimeIndex(n.snapshots)
    idx = pd.DatetimeIndex(frame.index)
    if idx.has_duplicates:
        raise SeriesUploadError(
            f"the file gives the hour {idx[idx.duplicated()][0]} twice (a local "
            "clock's autumn hour?): give each model hour once, in the model's "
            "time (the row's template has them)")
    missing = snaps.difference(idx)
    if len(missing):
        raise SeriesUploadError(
            f"the file has no value for {len(missing)} of the model's "
            f"{len(snaps)} hours (first: {missing[0]}): give one row per model "
            "hour (the row's template has them)")
    v = values.reindex(snaps)
    if not np.isfinite(v.to_numpy(dtype=float)).all():
        bad = v.index[~np.isfinite(v.to_numpy(dtype=float))][0]
        raise SeriesUploadError(
            f"the value at {bad} is not a finite number: fill every hour with a number")
    if spec.kind == "per_unit" and (v.max() > spec.hi or v.min() < spec.lo):
        hint = (" — it looks like a percentage: divide by 100"
                if v.max() > spec.hi and v.max() <= 100 * spec.hi else "")
        raise SeriesUploadError(
            f"values run {v.min():g} to {v.max():g}, outside {spec.lo:g} to "
            f"{spec.hi:g} (a share of the rating){hint}")
    if spec.kind == "ratio" and v.min() <= 0:
        raise SeriesUploadError(
            f"values must be above 0 (a ratio); the lowest is {v.min():g}")
    if spec.kind == "power" and v.min() < 0:
        raise SeriesUploadError(
            f"values must be 0 MW or more; the lowest is {v.min():g}")
    stored = {"provenance": "measured", "source": "upload"}
    return _pending_on_snapshot_clock(
        pd.Series(v.to_numpy(dtype=float), index=snaps, name=component), stored,
        component_type, attribute, component, what="the measured file",
        source="upload", based_on=None)


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
    return _pending_on_snapshot_clock(series, stored, component_type, attribute,
                                      component, what="the current series",
                                      source="current", based_on=current_digest(series))


def _pending_on_snapshot_clock(series: pd.Series, stored: dict[str, Any],
                               component_type: str, attribute: str, component: str,
                               *, what: str, source: str,
                               based_on: str | None) -> tuple[pd.Series, dict[str, Any]]:
    """A snapshot-clock series as a pending artifact's (source year, meta).
    ``based_on`` fingerprints the current series it came from (apply refuses
    the artifact once that changes); an upload has none."""
    idx = pd.DatetimeIndex(series.index)
    window = _hourly_window(idx, what=what)
    meta: dict[str, Any] = {k: v for k, v in stored.items() if k not in _APPLY_RECORD}
    meta.update({
        "family": stored.get("family") or FAMILY_BY_ATTRIBUTE.get(attribute, "series"),
        "target": [component_type, attribute],
        "source": source,
        "source_clock": "snapshot",
        "provenance": stored.get("provenance") or "measured",
        "resolution": "hourly",
        "time_basis": stored.get("time_basis"),
    })
    if based_on is not None:
        meta["based_on_current"] = based_on
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
