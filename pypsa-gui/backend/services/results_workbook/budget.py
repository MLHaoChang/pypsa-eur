"""
The cell budget and time coarsening of the result-view sheets (package 02,
slice R4; GK B's R4 conditions in
`.scratch/results-workbook/issues/02-result-view-sheets.md`).

A sheet whose snapshot tables would pass Excel's row limit or the workbook's
cell budget is written at the finest resolution of the ladder (snapshot →
day → week → month) at which it fits, the same for every run on the sheet.
When even the coarsest does not fit, the base run's tables are kept (at the
coarsest resolution) and the other scenarios are written at key-results level
(fallback (b)). The keys always come from full-resolution data: only the
tables are coarsened.

A snapshot table (`Table.time`) carries its rows' durations in hours
(`Table.weights`, `snapshot_weights(n, "generators")`, never the objective
weighting). A bucket never crosses an investment period. Within a bucket and
column a missing value drops out of both Σv·w and Σw; a bucket with no value
or Σw = 0 is written blank, never 0:

* "mean" (MW), "price" and "state" (the state of charge): Σv·w / Σw, a
  time-weighted mean; prices are not load-weighted, and a state stays a state;
* "sum" (energy per snapshot): Σv·w.

Snapshots that are not datetimes (integer timesteps) are cut into fixed
blocks of 24, 168 and 730 snapshots per period, named as such.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

__all__ = ["CELL_BUDGET", "EXCEL_ROWS", "KINDS", "LADDER", "coarsen", "fit"]

#: Excel's rows per sheet.
EXCEL_ROWS = 1_048_576
#: A heuristic for the cells the whole workbook may hold under the 25 MB cap
#: (about 8 bytes per zipped number cell); `MAX_BYTES` is the backstop.
CELL_BUDGET = 3_000_000
#: The coarser resolutions, finest first: (name, calendar frequency, block size).
LADDER = (("daily", "D", 24), ("weekly", "W", 168), ("monthly", "M", 730))
#: How each kind of snapshot table's coarsened cells are named.
KINDS = {"mean": "time-weighted means", "price": "time-weighted means, not load-weighted",
         "state": "weighted means of the state", "sum": "sums of value × hours"}
#: Rows a table adds around its data: its title, its header and the gap after.
_TABLE_ROWS = 3


def _bucket_starts(snapshots: pd.Series, freq: str) -> pd.Series | None:
    """Each snapshot's calendar bucket start, or None when they are not datetimes."""
    if pd.api.types.is_numeric_dtype(snapshots):
        return None
    try:
        ts = pd.to_datetime(snapshots, format="ISO8601")
    except (ValueError, TypeError):
        return None
    if freq == "D":
        return ts.dt.floor("D")
    if freq == "W":  # weeks start on Monday
        return ts.dt.floor("D") - pd.to_timedelta(ts.dt.weekday, unit="D")
    return ts.dt.to_period("M").dt.start_time


def coarsen(t, level: int):
    """`t` (a snapshot `Table`) at `LADDER[level]`; returns (table, resolution named)."""
    name, freq, block = LADDER[level]
    frame = t.frame.reset_index(drop=True)
    labels = list(frame.columns[:t.labels])
    snap = labels[-1]
    period = labels[0] if t.labels > 1 else None
    starts = _bucket_starts(frame[snap], freq)
    if starts is None:
        # Fixed-count blocks, counted from each period's first snapshot.
        pos = (frame.groupby(frame[period], sort=False).cumcount() if period
               else pd.Series(np.arange(len(frame))))
        first = pos // block * block
        starts = first.map(lambda i: f"snapshots {i + 1}–{i + block}")
        name = f"blocks of {block} snapshots"
    keys = [frame[period], starts] if period else [starts]
    values = frame[frame.columns[t.labels:]].apply(pd.to_numeric, errors="coerce")
    w = pd.Series(np.asarray(t.weights, dtype=float), index=frame.index)
    present = values.notna()
    vw = values.fillna(0.0).mul(w, axis=0)
    ww = present.mul(w, axis=0)
    by = pd.concat({"vw": vw, "ww": ww, "n": present.astype(int)}, axis=1).groupby(
        keys, sort=False)
    sums = by.sum()
    num, den, count = sums["vw"], sums["ww"], sums["n"]
    # A bucket with no value (or no hours) is blank, never 0.
    out = num.where((count > 0) & (den > 0)) if t.time == "sum" else num / den.where(den > 0)
    out = out.reset_index()
    out.columns = labels + list(values.columns)
    if pd.api.types.is_datetime64_any_dtype(out[snap]):
        out[snap] = out[snap].dt.date.map(lambda d: d.isoformat())
    return replace(t, frame=out, weights=None, resolution=f"{name}, {KINDS[t.time]}"), name


def _rows(tables) -> int:
    return sum(len(t.frame) + _TABLE_ROWS for t in tables if t is not None)


def _cells(tables) -> int:
    return sum(t.frame.size for t in tables if t is not None)


def fit(datas: list, key_rows: int, cells_left: int) -> tuple[list, str | None, str | None]:
    """The runs' `ViewData` with their snapshot tables at the finest resolution
    that keeps the sheet within Excel's rows and `cells_left` cells; returns
    (datas, the resolution named or None at full resolution, the fallback taken:
    None, "base" when (b) dropped the other scenarios' tables, or "keys" when even
    the base's tables pass Excel's row limit and every run is at key results)."""
    def size(ds):
        tabs = [t for d in ds if d for t in d.tables]
        return key_rows + _rows(tabs), _cells(tabs)

    def fits(ds) -> bool:
        rows, cells = size(ds)
        return rows <= EXCEL_ROWS and cells <= cells_left

    if fits(datas):
        return datas, None, None
    timed = any(t.time and t.weights is not None for d in datas if d for t in d.tables)
    candidates = []
    if timed:
        for level in range(len(LADDER)):
            named: set[str] = set()
            out = []
            for d in datas:
                if d is None:
                    out.append(None)
                    continue
                tabs = []
                for t in d.tables:
                    if t.time and t.weights is not None and len(t.frame):
                        t, n = coarsen(t, level)
                        named.add(n)
                    tabs.append(t)
                out.append(replace(d, tables=tabs))
            label = "; ".join(sorted(named)) or None
            candidates.append((out, label))
            if fits(out):
                return out, label, None
    # Fallback (b): the base in full (at the coarsest resolution tried), the
    # other scenarios at key-results level.
    out, label = candidates[-1] if candidates else (datas, None)
    out = [out[0], *[replace(d, tables=[]) if d else None for d in out[1:]]]
    if size(out)[0] <= EXCEL_ROWS:
        return out, label, "base"
    # A table that cannot be coarsened still passes Excel's row limit: no run
    # keeps its tables (a sheet is never truncated).
    return [replace(d, tables=[]) if d else None for d in out], None, "keys"
