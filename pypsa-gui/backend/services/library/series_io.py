"""
Series upload parsing: the timestamp and zone rules of a Library series
(Edge Investment Case P1 WP1.1b; moved out of `routers/library.py` in P2
WP2.4b-0, P1 condition 4, so the P2 importers share them).

Timestamps are ISO strings. Either ALL carry an offset (instants; `timezone`,
if given, is the zone to keep them in, else UTC) or NONE do (naive wall-clock,
stored naive; a `timezone` is then refused, the tariff engine's rule: a naive
axis plus a zone is ambiguous across DST). A zone pandas recognises that the
offset pattern does not still makes instants.

Pure service: no FastAPI. `SeriesInputError` (a `ValueError`) carries the
message the route returns as a 422.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

# A 15-minute year is 35,040 points; this admits ~28 of them. JSON bodies have
# no upload guard in this repo (`upload_guard` covers UploadFile only).
MAX_POINTS = 1_000_000

# A zone marker at the end of an ISO time part: Z, UTC, GMT, +01, +0100,
# +01:00 — case-insensitive (pandas reads `...t00:00z` as UTC too).
OFFSET_RE = r"(?i)(?:Z|UTC|GMT|[+-]\d{2}(?::?\d{2})?)$"


class SeriesInputError(ValueError):
    """An upload the series rules refuse (the route's 422)."""


def series_from(timestamps: list[str], values: list[float], timezone: str | None = None,
                *, max_points: int = MAX_POINTS) -> pd.Series:
    """The float series `values` on the parsed `timestamps` (see module docstring)."""
    if max(len(timestamps), len(values)) > max_points:
        raise SeriesInputError(f"at most {max_points:,} points per series")
    if len(timestamps) != len(values):
        raise SeriesInputError("timestamps and values must have the same length")
    raw = pd.Series(timestamps, dtype=str).str.strip()
    time_part = raw.str.extract(r"[Tt ](.*)$")[0].fillna("")
    aware = time_part.str.contains(OFFSET_RE, regex=True)
    if aware.any() and not aware.all():
        raise SeriesInputError("timestamps mix offset-carrying and naive values; "
                               "send every timestamp with an offset, or none")
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(list(raw), utc=bool(aware.all())))
    except (ValueError, TypeError) as exc:
        raise SeriesInputError(f"unparseable timestamp: {exc}") from exc
    if idx.tz is None and timezone is not None:
        raise SeriesInputError("timestamps carry no UTC offset, so `timezone` is ambiguous "
                               "across DST; send ISO timestamps with an offset")
    if idx.tz is not None:
        try:
            idx = idx.tz_convert(timezone or "UTC")
        except Exception as exc:  # noqa: BLE001 — bad zone name
            raise SeriesInputError(f"unknown timezone {timezone!r}") from exc
    return pd.Series(np.asarray(values, dtype=float), index=idx)


# ── file uploads (P2 WP2.4b-ii) ──────────────────────────────────────────────


def _cell(v) -> str:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return "" if v is None else str(v)


def parse_upload(data: bytes, filename: str, timezone: str | None = None, *,
                 max_points: int = MAX_POINTS) -> pd.Series:
    """A `timestamp,value` CSV or the first sheet of an xlsx (header row
    `timestamp`, `value`), under `series_from`'s timestamp and zone rules."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        import csv

        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SeriesInputError("the CSV is not UTF-8 text") from exc
        rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    elif name.endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = [[_cell(c) for c in r] for r in wb.worksheets[0].iter_rows(values_only=True)
                    if any(c is not None and str(c).strip() for c in r)]
        except SeriesInputError:
            raise
        except Exception as exc:  # noqa: BLE001 — a corrupt workbook
            raise SeriesInputError(f"unreadable xlsx: {type(exc).__name__}") from exc
    else:
        raise SeriesInputError("upload a csv or xlsx file (timestamp,value)")
    if not rows or [c.strip().lower() for c in rows[0][:2]] != ["timestamp", "value"] \
            or any(str(c).strip() for c in rows[0][2:]):
        raise SeriesInputError("the first row must be the header timestamp,value")
    body = rows[1:]
    if not body:
        raise SeriesInputError("the upload has no rows")
    if len(body) > max_points:
        raise SeriesInputError(f"at most {max_points:,} points per series")
    stamps, values = [], []
    for i, r in enumerate(body, start=2):
        if len(r) < 2 or any(str(c).strip() for c in r[2:]):
            raise SeriesInputError(f"row {i}: expected two columns, timestamp and value")
        raw = str(r[1]).strip()
        try:
            values.append(float(raw) if raw else float("nan"))
        except ValueError as exc:
            raise SeriesInputError(f"row {i}: value {raw!r} is not a number") from exc
        stamps.append(str(r[0]).strip())
    return series_from(stamps, values, timezone, max_points=max_points)


# ── meter data → monthly history (P2 WP2.4b-ii) ──────────────────────────────


SETTLEMENTS = {"15min": 0.25, "30min": 0.5, "h": 1.0}
_GAP_FACTOR = 1.5   # a step longer than this × the meter step is a gap, not coverage

# The Library's meter-data help (carried from WP2.1a-iii review round 1).
METER_HISTORY_HELP = (
    "Meter history seeds demand ratchets and prices non-convex energy tiers. "
    "Peaks are the highest settlement-interval mean of each complete month on the "
    "site clock; energy is the month's import. A cyclic or months-mode ratchet reads "
    "history under the SAME rate-year key, so it resolves only when the modelled rate "
    "year is a metered year: for a future year use non-cyclic range mode with meter "
    "history, or cyclic_year (which reads the modelled months themselves).")


def meter_history(series: pd.Series, *, settlement: str = "15min",
                  timezone: str | None = None) -> dict:
    """Monthly import peaks (kW, on `settlement` interval means) and energy
    (kWh) of a kW meter series, on the site clock (`timezone`; a naive series
    is already wall-clock time). Only COMPLETE months are history: a month the
    rows do not cover, or a NaN in it, is named in `notes`. A meter coarser
    than the settlement cannot measure its peak: energy only, peaks named."""
    from services.commercial.tariff_engine import interval_key

    if settlement not in SETTLEMENTS:
        raise SeriesInputError(f"settlement is one of {', '.join(SETTLEMENTS)}, "
                               f"got {settlement!r}")
    s = series.sort_index()
    if s.index.has_duplicates:
        raise SeriesInputError("meter timestamps repeat")
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_convert(timezone or idx.tz)
    elif timezone is not None:
        raise SeriesInputError("a naive meter series is wall-clock time; it takes no timezone")
    notes: list[str] = []
    if len(idx) < 2:
        raise SeriesInputError("meter data needs at least two rows")
    d = np.diff(idx.asi8) / 3.6e12
    step = float(np.median(d))
    dur = np.append(d, step)
    dur = np.where(dur <= _GAP_FACTOR * step, dur, step)   # the row before a gap covers one step
    kw = s.to_numpy(dtype=float)
    neg = int((kw < 0).sum())
    if neg:
        notes.append(f"negative_rows_read_as_export:{neg}")
    imp = np.clip(kw, 0.0, None)
    months = np.asarray(idx.strftime("%Y-%m"))
    frame = pd.DataFrame({"month": months, "kw": imp, "dur": dur,
                          "known": ~np.isnan(kw)})
    covered = frame[frame["known"]].groupby("month")["dur"].sum()
    complete: list[str] = []
    for m in sorted(set(months)):
        start = pd.Timestamp(f"{m}-01")
        end = start + pd.offsets.MonthBegin(1)
        if idx.tz is not None:
            start, end = start.tz_localize(idx.tz), end.tz_localize(idx.tz)
        hours = (end - start).total_seconds() / 3600.0
        ok = frame.loc[frame["month"] == m, "known"].all() and \
            covered.get(m, 0.0) >= hours - 1e-6 and \
            idx[months == m].min() <= start and idx[months == m].max() + \
            pd.Timedelta(hours=float(dur[months == m][-1])) >= end
        (complete.append(m) if ok else notes.append(f"month_incomplete:{m}"))
    energy = {m: float((frame.loc[frame["month"] == m, "kw"]
                        * frame.loc[frame["month"] == m, "dur"]).sum()) for m in complete}
    peaks: dict[str, float] = {}
    settle_h = SETTLEMENTS[settlement]
    if step > settle_h + 1e-9:
        notes.append(f"peaks_not_established:meter_step_{step:g}h_coarser_than_settlement_"
                     f"{settlement}")
    else:
        key = interval_key(idx, {"15min": "15min", "30min": "30min", "h": "h"}[settlement])
        g = pd.DataFrame({"key": key, "e": imp * dur, "dur": dur, "month": months})
        per = g.groupby("key").agg(e=("e", "sum"), dur=("dur", "sum"), month=("month", "first"))
        per["mean"] = per["e"] / per["dur"]
        top = per.groupby("month")["mean"].max()
        peaks = {m: float(top[m]) for m in complete if m in top.index}
    return {"meter_history_peaks_kw": peaks, "meter_history_energy_kwh": energy,
            "settlement": settlement, "notes": notes}
