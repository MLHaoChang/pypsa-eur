"""A measured load upload, read into the load family (ticket 01, slice B3).

`read_load_upload` turns a CSV or xlsx meter export (a timestamp column, then
one column per load) into hourly MW columns over ONE calendar year on UTC
hours, with ``provenance: measured``. The result is a load-family artifact:
it goes to the pending store and through `apply.assign_profile` exactly like
a generated archetype.

Rules — strict on what cannot be known, explicit about every repair:

  * Unit: from the column header ("office [kW]", "plant (MWh)") or the
    caller's ``unit``; the two must agree; no unit is a refusal. Power units
    (W, kW, MW, GW) are read as the step's mean power; energy units (Wh, kWh,
    MWh, GWh) as energy per step, divided by the step length.
  * Time: timestamps carrying offsets are converted to UTC; naive ones are a
    stated ``timezone``'s wall clock (DST's repeated hour inferred from the
    order), or, with no zone, read as UTC and noted.
  * Resolution: sub-hourly steps are averaged to hourly power (noted); a step
    coarser than an hour is refused — a day mean loses the shape a load
    profile exists to carry.
  * Year: the calendar year the file covers in full (``year`` picks one when
    it covers several). A partial year is refused.
  * Gaps: up to 6 h are interpolated; up to a week are copied from the same
    hours a week earlier (or later at the year's start); longer is refused.
    Counts per column are recorded.
  * ``annual_mwh`` rescales every column to that source-year energy (the
    original energy recorded).
  * A negative reading is refused: a net export is a generator beside the
    load, not a negative load (the apply path's rule).
  * The series QA (`services/timeseries_qa.check_series`) runs on every
    column; findings ride along as warnings and never block.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.engine import ProfileGenError

PROVENANCE = "measured"
MAX_INTERPOLATE_H = 6
MAX_COPY_H = 168

# Power units as MW per unit; energy units as MWh per unit.
_POWER = {"W": 1e-6, "kW": 1e-3, "MW": 1.0, "GW": 1e3}
_ENERGY = {"Wh": 1e-6, "kWh": 1e-3, "MWh": 1.0, "GWh": 1e3}
_UNIT_IN_HEADER = re.compile(r"^(.*?)\s*[\[(]\s*([A-Za-z]+)\s*[\])]\s*$")
_OFFSET = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")


class LoadUploadError(ProfileGenError):
    """Base of the typed refusals of a load upload."""


class UploadFormatError(LoadUploadError):
    """The file is not a readable timestamp + numeric load table."""


class UploadUnitError(LoadUploadError):
    """The unit is missing, unknown, or the header and the caller disagree."""


class UploadCoverageError(LoadUploadError):
    """The file does not cover one calendar year, or holds a hole too long
    to fill honestly."""


@dataclass
class LoadUpload:
    frame: pd.DataFrame           # hourly MW, one column per load, UTC hours
    meta: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


def _read_table(blob: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(io.BytesIO(blob), index_col=0)
        return pd.read_csv(io.BytesIO(blob), index_col=0)
    except Exception as exc:  # noqa: BLE001 — every parser error is a refusal
        raise UploadFormatError(f"could not read {filename!r}: {exc}") from None


def _utc_index(raw: pd.Index, timezone: str | None) -> tuple[pd.DatetimeIndex, str, list[str]]:
    """The file's timestamps as naive UTC, the basis in words, and notes."""
    as_text = pd.Index(raw).astype(str)
    if as_text.str.strip().str.contains(_OFFSET).any():
        try:
            idx = pd.to_datetime(as_text, utc=True, format="mixed")
        except (TypeError, ValueError) as exc:
            raise UploadFormatError(f"unreadable timestamps ({exc})") from None
        return idx.tz_localize(None), "UTC (from the file's offsets)", []
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(raw, format="mixed"))
    except (TypeError, ValueError) as exc:
        raise UploadFormatError(f"unreadable timestamps ({exc})") from None
    if idx.tz is not None:
        return idx.tz_convert("UTC").tz_localize(None), "UTC (from the file's offsets)", []
    if timezone:
        try:
            try:
                local = idx.tz_localize(timezone, ambiguous="infer", nonexistent="NaT")
            except ValueError:
                local = idx.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
        except Exception as exc:  # noqa: BLE001 — pytz/zoneinfo raise many types
            raise UploadFormatError(f"timezone {timezone!r}: {exc}") from None
        return (local.tz_convert("UTC").tz_localize(None),
                f"UTC (read from {timezone} wall clock)", [])
    return idx, "UTC (assumed; the file states no zone)", ["time_basis_assumed_utc"]


def _column_units(columns, unit: str | None) -> tuple[list[str], list[str]]:
    if unit is not None and unit not in _POWER and unit not in _ENERGY:
        raise UploadUnitError(
            f"unit {unit!r} is not one of {', '.join([*_POWER, *_ENERGY])}")
    names, units = [], []
    for col in columns:
        m = _UNIT_IN_HEADER.match(str(col))
        named = m.group(2) if m and (m.group(2) in _POWER or m.group(2) in _ENERGY) else None
        name = m.group(1).strip() if named else str(col).strip()
        if named and unit and named != unit:
            raise UploadUnitError(
                f"column {col!r} says {named} but the upload says {unit}; "
                "correct one of them")
        used = named or unit
        if used is None:
            raise UploadUnitError(
                f"column {col!r} names no unit; state the unit (e.g. kW or MWh)")
        names.append(name)
        units.append(used)
    if len(set(names)) != len(names):
        raise UploadFormatError(f"duplicate load columns: {names}")
    return names, units


def _step_minutes(idx: pd.DatetimeIndex) -> float:
    diffs = np.diff(idx.asi8) / 6e10
    diffs = diffs[diffs > 0]
    if not diffs.size:
        raise UploadFormatError("the file holds fewer than two timestamps")
    return float(np.median(diffs))


def _full_years(idx: pd.DatetimeIndex) -> list[int]:
    first, last = idx.min(), idx.max()
    return [y for y in range(first.year, last.year + 1)
            if first <= pd.Timestamp(year=y, month=1, day=1)
            and last >= pd.Timestamp(year=y, month=12, day=31, hour=23)]


def _fill_gaps(s: pd.Series, name: str) -> tuple[pd.Series, dict[str, int]]:
    v = s.to_numpy(dtype=float).copy()
    missing = np.isnan(v)
    counts = {"interpolated_h": 0, "copied_week_h": 0}
    if not missing.any():
        return s, counts
    edges = np.flatnonzero(np.diff(np.concatenate([[0], missing.astype(int), [0]])))
    for start, end in zip(edges[::2], edges[1::2]):
        length = end - start
        if length > MAX_COPY_H:
            raise UploadCoverageError(
                f"{name!r} has {length} missing hours from {s.index[start]}; "
                f"gaps longer than {MAX_COPY_H} h are not filled")
        inner = start > 0 and end < len(v)
        if length <= MAX_INTERPOLATE_H and inner:
            left, right = v[start - 1], v[end]
            v[start:end] = left + (right - left) * (np.arange(1, length + 1) / (length + 1))
            counts["interpolated_h"] += int(length)
            continue
        src = np.arange(start, end) - MAX_COPY_H
        if src[0] < 0 or np.isnan(v[src]).any():
            src = np.arange(start, end) + MAX_COPY_H
        if src[-1] >= len(v) or np.isnan(v[src]).any():
            raise UploadCoverageError(
                f"{name!r}: the {length} missing hours from {s.index[start]} have "
                "no complete week before or after to copy from")
        v[start:end] = v[src]
        counts["copied_week_h"] += int(length)
    return pd.Series(v, index=s.index, name=s.name), counts


def read_load_upload(blob: bytes, filename: str, *, unit: str | None,
                     timezone: str | None = None, year: int | None = None,
                     annual_mwh: float | None = None,
                     now: _dt.datetime | str | None = None) -> LoadUpload:
    """Read a meter export into hourly MW columns (see the module rules)."""
    from services.timeseries_qa import check_series

    table = _read_table(blob, filename)
    if table.shape[1] == 0:
        raise UploadFormatError("the file needs a timestamp column and at least one load column")
    names, units = _column_units(table.columns, unit)
    idx, basis, notes = _utc_index(table.index, timezone)
    table.index = idx
    table.columns = names
    table = table[table.index.notna()].sort_index()
    if table.index.has_duplicates:
        dupes = table.index[table.index.duplicated()][:3]
        raise UploadFormatError(
            f"repeated timestamps {list(map(str, dupes))}; a wall-clock export "
            "needs its timezone stated so DST's repeated hour can be read")

    step_min = _step_minutes(table.index)
    if step_min > 60.0 + 1e-6:
        raise UploadFormatError(
            f"the file's step is {step_min:g} min; a load profile needs hourly "
            "or finer readings (a coarser mean loses the shape)")

    cols: dict[str, pd.Series] = {}
    for name, u in zip(names, units):
        raw = pd.to_numeric(table[name], errors="coerce")
        bad = raw.isna() & table[name].notna()
        if bad.any():
            raise UploadFormatError(
                f"column {name!r}: {table[name][bad].iloc[0]!r} is not a number")
        if (raw < 0).any():
            raise UploadFormatError(
                f"column {name!r} has a negative reading at {raw[raw < 0].index[0]}; "
                "a net export is a generator beside the load, not a negative load")
        mw = raw * _POWER[u] if u in _POWER else raw * _ENERGY[u] / (step_min / 60.0)
        cols[name] = mw
    frame = pd.DataFrame(cols)
    resolution_in = "hourly" if abs(step_min - 60.0) < 1e-6 else f"{step_min:g}min"
    if resolution_in != "hourly":
        frame = frame.resample("h").mean()
        notes.append("resampled_to_hourly")
    else:
        frame.index = frame.index.floor("h")

    years = _full_years(frame.index)
    if year is not None:
        if int(year) not in years:
            raise UploadCoverageError(
                f"the file does not cover {year} in full; it covers "
                f"{', '.join(map(str, years)) or 'no full calendar year'}")
        year = int(year)
    elif len(years) == 1:
        year = years[0]
    elif not years:
        raise UploadCoverageError(
            f"the file runs {frame.index.min()} to {frame.index.max()}; a load "
            "upload must cover one full calendar year")
    else:
        raise UploadCoverageError(
            f"the file covers {', '.join(map(str, years))}; name the year to use")

    hours = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left")
    frame = frame.reindex(hours)
    gaps: dict[str, dict[str, int]] = {}
    rescaled: dict[str, float] = {}
    for name in names:
        frame[name], gaps[name] = _fill_gaps(frame[name], name)
        if annual_mwh is not None:
            energy = float(frame[name].sum())
            if not energy > 0:
                raise UploadFormatError(f"column {name!r} holds no energy to rescale")
            rescaled[name] = energy
            frame[name] = frame[name] * (float(annual_mwh) / energy)
    if any(c["interpolated_h"] or c["copied_week_h"] for c in gaps.values()):
        notes.append("gaps_filled")
    if annual_mwh is not None:
        notes.append("rescaled_to_annual_mwh")

    warnings = [{"column": name, "code": i.code, "message": i.message}
                for name in names
                for i in check_series("Load", name, "p_set", frame[name].to_numpy())]

    if now is None:
        generated_at = _dt.datetime.now(_dt.UTC)
    elif isinstance(now, str):
        generated_at = _dt.datetime.fromisoformat(now)
    else:
        generated_at = now
    upload: dict[str, Any] = {
        "filename": filename, "sha256": hashlib.sha256(blob).hexdigest(),
        "units": dict(zip(names, units)), "timezone": timezone,
        "resolution_in": resolution_in, "gaps_filled": gaps,
    }
    if rescaled:
        upload["rescaled_from_mwh"] = rescaled
    meta = {
        "family": "load", "source": "upload", "provenance": PROVENANCE,
        "unit": "MW", "weather_year": year, "resolution": "hourly",
        "leap_day": "kept", "generated_at": generated_at.isoformat(),
        "source_time_basis": basis, "upload": upload,
    }
    return LoadUpload(frame=frame, meta=meta, notes=notes, warnings=warnings)
