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
