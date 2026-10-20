"""A measured load upload, read into the load family (ticket 01, slice B3).

`read_load_upload` turns a CSV or xlsx meter export (a timestamp column, then
one column per load) into hourly MW columns over ONE calendar year on UTC
hours, with ``provenance: measured``. The result is a load-family artifact:
it goes to the pending store and through `apply.assign_profile` exactly like
a generated archetype.

Rules — strict on what cannot be known, explicit about every repair:

  * Table: CSV (comma, or semicolon with decimal commas, as European exports
    write it) or xlsx. With ``only`` (the network's load names), columns
    naming no load are reported as ``unmatched`` and never read, so a stray
    column cannot sink the upload.
  * Unit: from the column header ("office [kW]", "plant (mwh)", any case) or
    the caller's ``unit``; the two must agree; no unit is a refusal. A header
    that IS a load name ("pump (W)") is that load, in the stated unit. Power
    units (W, kW, MW, GW) are read as the step's mean power; energy units
    (Wh, kWh, MWh, GWh) as energy over the row's own step (the shorter of
    the gaps to its neighbours, so a file that changes resolution midway
    converts each part right).
  * Time: timestamps with offsets are converted to UTC (a stated timezone is
    then ignored, and that is noted); naive ones are the stated
    ``timezone``'s wall clock — DST's repeated hour inferred from the order,
    or dropped and filled when it was collapsed — else ``default_timezone``
    (the project's), else read as UTC and noted. Offsets on some rows only
    are refused.
  * Resolution: every reading is averaged into its UTC hour (a stray
    half-hour row joins its hour); a median step coarser than an hour is
    refused — a day mean loses the shape a load profile exists to carry.
  * Year: the calendar year the file covers, allowing up to a day missing at
    either end (a local-clock year seen in UTC); ``year`` picks one when it
    covers several. A partial year is refused.
  * Gaps: up to 6 h inside the series are interpolated; up to a week (or at
    the year's edges) are copied from the same hours a week before or after;
    longer is refused. Counts per column are recorded.
  * ``annual_mwh`` rescales EACH column to that source-year energy (the
    original energy recorded); it must be positive.
  * A negative reading is refused: a net export is a generator beside the
    load, not a negative load (the apply path's rule).
  * The series QA (`services/timeseries_qa.check_series`) runs on every
    column; findings ride along as warnings and never block.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.engine import ProfileGenError

PROVENANCE = "measured"
MAX_INTERPOLATE_H = 6
MAX_COPY_H = 168
EDGE_TOLERANCE_H = 24

# Power units as MW per unit; energy units as MWh per unit.
_POWER = {"W": 1e-6, "kW": 1e-3, "MW": 1.0, "GW": 1e3}
_ENERGY = {"Wh": 1e-6, "kWh": 1e-3, "MWh": 1.0, "GWh": 1e3}
_CANONICAL = {u.lower(): u for u in (*_POWER, *_ENERGY)}
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


class UploadNoLoadError(LoadUploadError):
    """No column of the file names a load of the network."""


@dataclass
class MeasuredLoad:
    frame: pd.DataFrame           # hourly MW, one column per load, UTC hours
    meta: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def _read_table(blob: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(io.BytesIO(blob), index_col=0)
        head = blob[:4096].decode("utf-8-sig", errors="replace").splitlines()[:1]
        first = head[0] if head else ""
        european = first.count(";") > first.count(",")
        return pd.read_csv(io.BytesIO(blob), index_col=0,
                           sep=";" if european else ",",
                           decimal="," if european else ".")
    except Exception as exc:  # noqa: BLE001 — every parser error is a refusal
        raise UploadFormatError(f"could not read {filename!r}: {exc}") from None


def _utc_index(raw: pd.Index, timezone: str | None, default_timezone: str | None,
               ) -> tuple[pd.DatetimeIndex, str, list[str], str | None]:
    """The timestamps as naive UTC, the basis in words, notes, and the zone
    actually used to read a wall clock."""
    as_text = pd.Index(raw).astype(str).str.strip()
    has_offset = as_text.str.contains(_OFFSET)
    if has_offset.any():
        if not has_offset.all():
            raise UploadFormatError(
                "some timestamps carry a UTC offset and some do not; export "
                "them all with offsets, or all on one wall clock")
        try:
            idx = pd.to_datetime(as_text, utc=True, format="mixed")
        except (TypeError, ValueError) as exc:
            raise UploadFormatError(f"unreadable timestamps ({exc})") from None
        notes = ["timezone_ignored_file_has_offsets"] if timezone else []
        return idx.tz_localize(None), "UTC (from the file's offsets)", notes, None
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(raw, format="mixed"))
    except (TypeError, ValueError) as exc:
        raise UploadFormatError(f"unreadable timestamps ({exc})") from None
    if idx.tz is not None:
        return idx.tz_convert("UTC").tz_localize(None), "UTC (from the file's offsets)", [], None
    zone = timezone or default_timezone
    if not zone:
        return (idx, "UTC (assumed; the file states no zone)",
                ["time_basis_assumed_utc"], None)
    try:
        local = idx.tz_localize(zone, ambiguous="infer", nonexistent="NaT")
        notes = []
    except Exception:  # noqa: BLE001 — pytz/zoneinfo raise their own types
        # The repeated autumn hour was collapsed (or out of order): drop the
        # ambiguous readings and let the gap fill cover those two hours.
        try:
            local = idx.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
        except Exception as exc:  # noqa: BLE001
            raise UploadFormatError(f"timezone {zone!r}: {exc}") from None
        notes = ["dst_ambiguous_hours_dropped"]
    return (local.tz_convert("UTC").tz_localize(None),
            f"UTC (read from {zone} wall clock)", notes, zone)


def _columns(columns, unit: str | None, only: set[str] | None,
             ) -> tuple[list[tuple[Any, str, str]], list[str]]:
    """[(raw header, load name, unit)] for the columns to read, and the
    headers that name no load."""
    stated = None
    if unit is not None:
        stated = _CANONICAL.get(str(unit).lower())
        if stated is None:
            raise UploadUnitError(
                f"unit {unit!r} is not one of {', '.join(_CANONICAL.values())}")
    keep, unmatched = [], []
    for col in columns:
        raw = str(col).strip()
        if only is not None and raw in only:
            name, named = raw, None
        else:
            m = _UNIT_IN_HEADER.match(raw)
            named = _CANONICAL.get(m.group(2).lower()) if m else None
            name = m.group(1).strip() if named else raw
            if only is not None and name not in only:
                unmatched.append(raw)
                continue
        if named and stated and named != stated:
            raise UploadUnitError(
                f"column {raw!r} says {named} but the upload says {stated}; "
                "correct one of them")
        used = named or stated
        if used is None:
            raise UploadUnitError(
                f"column {raw!r} names no unit; state the unit (e.g. kW or MWh)")
        keep.append((col, name, used))
    names = [k[1] for k in keep]
    if len(set(names)) != len(names):
        raise UploadFormatError(f"duplicate load columns: {names}")
    return keep, unmatched


def _row_steps_h(idx: pd.DatetimeIndex) -> np.ndarray:
    """Each row's own step in hours: the shorter gap to a neighbour, so a
    hole after a reading does not stretch it."""
    t = idx.asi8 / 3.6e12
    d = np.diff(t)
    prev = np.concatenate([[np.inf], d])
    nxt = np.concatenate([d, [np.inf]])
    return np.minimum(prev, nxt)


def _covered_years(idx: pd.DatetimeIndex) -> list[int]:
    if not len(idx):
        return []
    first, last = idx.min(), idx.max()
    slack = pd.Timedelta(hours=EDGE_TOLERANCE_H)
    return [y for y in range(first.year, last.year + 1)
            if first <= pd.Timestamp(year=y, month=1, day=1) + slack
            and last >= pd.Timestamp(year=y, month=12, day=31, hour=23) - slack]


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
                     timezone: str | None = None,
                     default_timezone: str | None = None,
                     year: int | None = None,
                     annual_mwh: float | None = None,
                     only: set[str] | None = None,
                     now: _dt.datetime | str | None = None) -> MeasuredLoad:
    """Read a meter export into hourly MW columns (see the module rules).
    ``now`` pins ``generated_at`` for reproducible records, as the engine's
    does."""
    from services.timeseries_qa import check_series

    if annual_mwh is not None and not (math.isfinite(float(annual_mwh))
                                       and float(annual_mwh) > 0):
        raise UploadFormatError(f"annual_mwh must be a positive number, got {annual_mwh!r}")
    table = _read_table(blob, filename)
    if table.shape[1] == 0:
        raise UploadFormatError("the file needs a timestamp column and at least one load column")
    keep, unmatched = _columns(table.columns, unit, only)
    if not keep:
        raise UploadNoLoadError(
            f"no column names a load on the network (columns {unmatched}; "
            f"loads {sorted(only or [])[:20]})")
    idx, basis, notes, zone = _utc_index(table.index, timezone, default_timezone)
    table = table[[k[0] for k in keep]]
    table.columns = [k[1] for k in keep]
    table.index = idx
    table = table[table.index.notna()].sort_index()
    if table.index.has_duplicates:
        dupes = table.index[table.index.duplicated()][:3]
        raise UploadFormatError(
            f"repeated timestamps {list(map(str, dupes))}; a wall-clock export "
            "needs its timezone stated so DST's repeated hour can be read")
    if len(table) < 2:
        raise UploadFormatError("the file holds fewer than two timestamps")

    steps_h = _row_steps_h(table.index)
    median_min = float(np.median(steps_h)) * 60.0
    if median_min > 60.0 + 1e-6:
        raise UploadFormatError(
            f"the file's step is {median_min:g} min; a load profile needs hourly "
            "or finer readings (a coarser mean loses the shape)")

    cols: dict[str, pd.Series] = {}
    for col, name, u in zip(table.columns, table.columns, (k[2] for k in keep)):
        raw = pd.to_numeric(table[col], errors="coerce")
        bad = raw.isna() & table[col].notna()
        if bad.any():
            raise UploadFormatError(
                f"column {name!r}: {table[col][bad].iloc[0]!r} is not a number")
        if (raw < 0).any():
            raise UploadFormatError(
                f"column {name!r} has a negative reading at {raw[raw < 0].index[0]}; "
                "a net export is a generator beside the load, not a negative load")
        cols[name] = (raw * _POWER[u] if u in _POWER
                      else raw * _ENERGY[u] / steps_h)
    frame = pd.DataFrame(cols).resample("h").mean()
    resolution_in = "hourly" if abs(median_min - 60.0) < 1e-6 else f"{median_min:g}min"
    if resolution_in != "hourly":
        notes.append("resampled_to_hourly")

    years = _covered_years(frame.dropna(how="all").index)
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
    for name in frame.columns:
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
                for name in frame.columns
                for i in check_series("Load", name, "p_set", frame[name].to_numpy())]

    if now is None:
        generated_at = _dt.datetime.now(_dt.UTC)
    elif isinstance(now, str):
        generated_at = _dt.datetime.fromisoformat(now)
    else:
        generated_at = now
    upload: dict[str, Any] = {
        "filename": filename, "sha256": hashlib.sha256(blob).hexdigest(),
        "units": {k[1]: k[2] for k in keep}, "timezone": zone,
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
    return MeasuredLoad(frame=frame, meta=meta, notes=notes, warnings=warnings,
                        unmatched=unmatched)
