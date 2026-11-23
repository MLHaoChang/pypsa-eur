"""
PLEXOS data-file CSVs to time series over the network's snapshots (slice 2b).

Pure pandas: no ``pypsa`` (engine cage, IMPORT-CAGE-1). Plan and GK A's
conditions: ``.scratch/project-io/plexos-mapping.md``.

Layouts read (the header decides; the column for an object is its name):

- ``Datetime,<name>…``: one row per timestamp;
- ``Year,Month,Day,Period,<name>…``: period p of a day starts (p − 1) steps
  after midnight;
- ``Month,Day,Period,<name>…``: the same, repeated in every horizon year
  (slice 3b, GK A C1);
- ``Pattern,<name>…``: each row's timeslice pattern (``plexos_pattern``) sets
  the value on the snapshots it covers (slice 3b);
- ``NAME,M1..M12``: one row per object, a value per month (slice 3b).

Day/month order is decided once per file, never per cell (GK A T1): the Data
File's ``Locale`` when it is a known one, otherwise from the dates themselves
(a first field above 12 means day first, a second field above 12 month first);
a file where neither shows is ambiguous and not read.

A series is used only when it has exactly one readable number for every
snapshot (GK A Q10). A duplicate or unreadable timestamp, a missing snapshot,
a step finer than the snapshots or an empty or non-numeric cell makes the
series unusable, with the reason; nothing is filled, carried forward or
resampled. Timestamps outside the horizon are ignored.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Callable, Iterable, Mapping
from typing import IO

import numpy as np
import pandas as pd

from gridspine.ingest.plexos_pattern import mask

#: Windows locale ids (the Data File ``Locale`` attribute) whose short dates put
#: the day first, and those that put the month first.
DAY_FIRST_LOCALES = {2057: "en-GB", 3081: "en-AU", 5129: "en-NZ", 6153: "en-IE"}
MONTH_FIRST_LOCALES = {1033: "en-US"}

_ISO = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
)
_MONTHS = [f"M{i}" for i in range(1, 13)]
_DAY_FIRST = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")
_MONTH_FIRST = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y")

#: Caps per CSV (GK A T5); the zip guards of slice 1 apply on top.
MAX_CSV_BYTES = 256 * 2**20
MAX_CSV_ROWS = 1_100_000  # a year of minutes is 527,040
MAX_HEADER_BYTES = 2**24

#: name -> the series over the snapshots, or the reason it cannot be used
Result = dict[str, "pd.Series | str"]


def file_key(path: str) -> str:
    """The name a Data File path is matched by: its basename, case-folded (Q10)."""
    return path.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _order(text: pd.Series, locale: float | None) -> tuple[str, ...] | str:
    """The slash-date formats for the whole file, or why its order is unknown."""
    whole = locale is not None and math.isfinite(locale) and locale == int(locale)
    loc = int(locale) if whole else None
    if loc in DAY_FIRST_LOCALES:
        return _DAY_FIRST
    if loc in MONTH_FIRST_LOCALES:
        return _MONTH_FIRST
    parts = text.str.extract(r"^(\d{1,2})/(\d{1,2})/").dropna().astype(int)
    if parts.empty:
        return ()
    first, second = (parts[0] > 12).any(), (parts[1] > 12).any()
    if first and not second:
        return _DAY_FIRST
    if second and not first:
        return _MONTH_FIRST
    known = (
        f"Data File Locale {loc}" if loc is not None else "the Data File has no Locale"
    )
    return (
        "day/month order unknown: "
        + known
        + (" and the dates fit neither order" if first else " and no day is above 12")
    )


def _parse_dates(cells: pd.Series, locale: float | None) -> pd.DatetimeIndex | str:
    text = cells.str.strip()
    slash = _order(text, locale)
    if isinstance(slash, str):
        return slash
    out = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    for fmt in slash + _ISO:
        todo = out.isna()
        if not todo.any():
            break
        out[todo] = pd.to_datetime(text[todo], format=fmt, errors="coerce")
    bad = out.isna()
    if bad.any():
        return f"timestamp {text[bad].iloc[0]!r} unreadable"
    return pd.DatetimeIndex(out)


def _column(header: list[str], name: str) -> str | None:
    """The header cell for an object: exact, else the one case-insensitive match."""
    if name in header:
        return name
    hits = [h for h in header if h.casefold() == name.casefold()]
    return hits[0] if len(hits) == 1 else None


def _numbers(cells: pd.Series, where: pd.Index) -> pd.Series | str:
    values = pd.to_numeric(cells.str.strip(), errors="coerce")
    bad = ~np.isfinite(values.astype(float))  # NaN, empty, text and ±INF
    if bad.any():
        at = where[bad.to_numpy()][0]
        raw = cells[bad].iloc[0]
        return f"value {raw!r} at {at} is not a number"
    return values.astype(float)


def read_series(
    opener: Callable[[], IO[bytes]],
    names: Iterable[str],
    snapshots: pd.Index,
    step_minutes: float | None,
    locale: float | None = None,
) -> Result:
    """
    The series for each object name in one data file. A reason that applies
    to the whole file is given for every name.
    """
    names = sorted(set(names))
    if not isinstance(snapshots, pd.DatetimeIndex) or not step_minutes:
        return dict.fromkeys(names, "the network has no time horizon")
    steps = snapshots.to_series().diff().dropna().unique()
    if len(steps) > 1 or (
        len(steps) and steps[0] != pd.Timedelta(minutes=step_minutes)
    ):
        return dict.fromkeys(names, "the snapshots have no single step; none inferred")
    try:
        with opener() as raw:
            first = raw.readline(MAX_HEADER_BYTES).decode("utf-8-sig")
        header = [h.strip() for h in next(csv.reader([first]), [])]
    except UnicodeDecodeError:
        return dict.fromkeys(names, "file is not UTF-8 text")
    except csv.Error as exc:
        return dict.fromkeys(names, f"not a readable CSV ({exc})")
    if not any(header):
        return dict.fromkeys(names, "file is empty")
    if len(set(header)) != len(header):
        dup = next(h for h in header if header.count(h) > 1)
        return dict.fromkeys(names, f"column {dup!r} appears twice")
    low = [h.casefold() for h in header]
    if low[:1] in (["datetime"], ["date"]):
        keys = header[:1]
    elif low[:4] == ["year", "month", "day", "period"]:
        keys = header[:4]
    elif low[:3] == ["month", "day", "period"]:
        keys = header[:3]
    elif low[:1] == ["pattern"]:
        keys = header[:1]
    elif low[:1] == ["name"] and all(m.casefold() in low for m in _MONTHS):
        return _monthly(opener, names, snapshots, step_minutes)
    else:
        return dict.fromkeys(
            names,
            f"layout not read (header starts {', '.join(header[:4])}); read are "
            "Datetime, Year/Month/Day/Period, Month/Day/Period, Pattern and "
            "NAME,M1..M12",
        )
    cols = {n: _column(header[len(keys) :], n) for n in names}
    if all(c is None for c in cols.values()):
        return {n: f"no column {n!r}" for n in names}  # not parsed further
    wanted = set(keys) | {c for c in cols.values() if c is not None}
    try:
        with opener() as raw:
            body = pd.read_csv(
                raw,
                usecols=lambda c: c.strip() in wanted,
                dtype=str,
                keep_default_na=False,
                encoding="utf-8-sig",
                skipinitialspace=True,
                nrows=MAX_CSV_ROWS + 1,
            )
    except (
        ValueError,
        UnicodeDecodeError,
    ) as exc:  # pandas ParserError is a ValueError
        return dict.fromkeys(names, f"not a readable CSV ({str(exc).splitlines()[0]})")
    if len(body) > MAX_CSV_ROWS:
        return dict.fromkeys(names, f"more than {MAX_CSV_ROWS:,} rows; not read")
    body.columns = [c.strip() for c in body.columns]
    body = body.fillna("")
    if low[:1] == ["pattern"]:
        return _by_pattern(body, keys[0], cols, names, snapshots, step_minutes)
    if len(keys) == 1:
        index = _parse_dates(body[keys[0]], locale)
    elif len(keys) == 3:
        body, index = _year_free(body, keys, step_minutes, snapshots)
    else:
        index = _periods(body, keys, step_minutes)
    return _wide(body, cols, index, names, snapshots, step_minutes)


def _periods(
    body: pd.DataFrame, header: list[str], step: float
) -> pd.DatetimeIndex | str:
    parts = {}
    for col in header[:4]:
        v = pd.to_numeric(body[col].str.strip(), errors="coerce")
        bad = ~np.isfinite(v) | (v != v.round())  # NaN, text and ±inf (GK A N2)
        if bad.any():
            return f"{col} {body[col][bad].iloc[0]!r} is not a whole number"
        parts[col.casefold()] = v.astype(int)
    per_day = 1440 / step
    p = parts["period"]
    if ((p < 1) | (p > per_day)).any():
        return f"period {p[(p < 1) | (p > per_day)].iloc[0]} outside 1..{per_day:g} for this horizon"
    days = pd.to_datetime(
        pd.DataFrame({k: parts[k] for k in ("year", "month", "day")}), errors="coerce"
    )
    if days.isna().any():
        return "a Year/Month/Day is not a date"
    return pd.DatetimeIndex(days + pd.to_timedelta((p - 1) * step, unit="min"))


def _year_free(
    body: pd.DataFrame, keys: list[str], step: float, snapshots: pd.DatetimeIndex
) -> tuple[pd.DataFrame, pd.DatetimeIndex | str]:
    """
    ``Month,Day,Period``: the same days in every year of the horizon (GK A C1).
    The file step is a day over its periods per day; it must equal the
    snapshot step, and every day must have that many periods. A 29 February
    row is used only in leap years.
    """
    parts = {}
    for col in keys:
        v = pd.to_numeric(body[col].str.strip(), errors="coerce")
        bad = ~np.isfinite(v) | (v != v.round())  # NaN, text and ±inf (GK A N2)
        if bad.any():
            return body, f"{col} {body[col][bad].iloc[0]!r} is not a whole number"
        parts[col.casefold()] = v.astype(int)
    if body.empty:
        return body, pd.DatetimeIndex([])
    p = parts["period"]
    per_day = int(p.max())
    if p.min() < 1:
        return body, f"period {p.min()} is below 1"
    counts = p.groupby([parts["month"], parts["day"]]).size()
    if (counts != per_day).any():
        (m, d), c = next(iter(counts[counts != per_day].items()))
        return body, (
            f"month {m} day {d} has {c} periods, other days {per_day}; "
            "periods per day differ"
        )
    file_step = 1440 / per_day
    if not math.isclose(file_step, step):
        what = "finer" if file_step < step else "coarser"
        return body, (
            f"file step {file_step:g} minutes ({per_day} periods a day) is {what} "
            f"than the {step:g}-minute snapshots; not resampled"
        )
    years = sorted(set(snapshots.year))
    frames, stamps = [], []
    for year in years:
        days = pd.to_datetime(
            pd.DataFrame({"year": year, "month": parts["month"], "day": parts["day"]}),
            errors="coerce",
        )
        leap_day = (parts["month"] == 2) & (parts["day"] == 29)
        bad = days.isna() & ~leap_day
        if bad.any():
            i = bad.to_numpy().argmax()
            return body, (
                f"month {parts['month'].iloc[i]} day {parts['day'].iloc[i]} is not a date"
            )
        keep = days.notna().to_numpy()
        frames.append(body[keep])
        stamps.append(days[keep] + pd.to_timedelta((p[keep] - 1) * step, unit="min"))
    out = pd.concat(frames, ignore_index=True)
    return out, pd.DatetimeIndex(pd.concat(stamps, ignore_index=True))


def _by_pattern(
    body: pd.DataFrame,
    key: str,
    cols: dict[str, str | None],
    names: list[str],
    snapshots: pd.DatetimeIndex,
    step: float,
) -> Result:
    """
    ``Pattern,<name>…``: each row's value holds on the snapshots its pattern
    covers. Two rows giving one snapshot different values, or a snapshot no
    row covers, leave the name unread.
    """
    masks = []
    for i, pat in enumerate(body[key]):
        got = mask(pat.strip(), snapshots, step)
        if isinstance(got, str):
            return dict.fromkeys(names, f"row {i + 2}: {got}")
        masks.append(got)
    out: Result = {}
    for name in names:
        col = cols[name]
        if col is None:
            out[name] = f"no column {name!r}"
            continue
        got = _numbers(body[col], pd.Index([f"row {i + 2}" for i in range(len(body))]))
        if isinstance(got, str):
            out[name] = got
            continue
        values = np.full(len(snapshots), np.nan)
        why = None
        for i, (m, v) in enumerate(zip(masks, got.to_numpy())):
            clash = m & ~np.isnan(values) & (values != v)
            if clash.any():
                why = (
                    f"row {i + 2} gives {v:g} at {snapshots[clash.argmax()]}, "
                    "an earlier row another value"
                )
                break
            values[m] = v
        if why is None and np.isnan(values).any():
            gap = np.isnan(values)
            why = (
                f"no pattern covers {int(gap.sum())} of {len(snapshots)} snapshots, "
                f"first {snapshots[gap.argmax()]}"
            )
        out[name] = why if why else pd.Series(values, index=snapshots)
    return out


def _monthly(
    opener: Callable[[], IO[bytes]],
    names: list[str],
    snapshots: pd.DatetimeIndex,
    step: float,
) -> Result:
    """``NAME,M1..M12``: one row per object; a snapshot takes its month's value."""
    months = []
    for i in range(1, 13):
        got = mask(f"M{i}", snapshots, step)
        if isinstance(got, str):
            return dict.fromkeys(names, got)
        months.append(got)
    try:
        with opener() as raw:
            body = pd.read_csv(
                raw,
                dtype=str,
                keep_default_na=False,
                encoding="utf-8-sig",
                skipinitialspace=True,
                nrows=MAX_CSV_ROWS + 1,
            )
    except (ValueError, UnicodeDecodeError) as exc:
        return dict.fromkeys(names, f"not a readable CSV ({str(exc).splitlines()[0]})")
    if len(body) > MAX_CSV_ROWS:
        return dict.fromkeys(names, f"more than {MAX_CSV_ROWS:,} rows; not read")
    body.columns = [c.strip() for c in body.columns]
    body = body.fillna("")
    month_col = {c.casefold(): c for c in body.columns}
    keys = body[body.columns[0]].str.strip()
    out: Result = {}
    for name in names:
        hit = keys == name
        if not hit.any():
            hit = keys.str.casefold() == name.casefold()
        if not hit.any():
            out[name] = f"no row {name!r}"
            continue
        if hit.sum() > 1:
            out[name] = f"row {name!r} appears {int(hit.sum())} times"
            continue
        row = body[hit].iloc[0]
        cells = pd.Series([row[month_col[m.casefold()]] for m in _MONTHS])
        got = _numbers(cells, pd.Index(_MONTHS))
        if isinstance(got, str):
            out[name] = got
            continue
        values = np.full(len(snapshots), np.nan)
        for m, v in zip(months, got.to_numpy()):
            values[m] = v
        out[name] = pd.Series(values, index=snapshots)
    return out


def _wide(
    body: pd.DataFrame,
    cols: dict[str, str | None],
    index: pd.DatetimeIndex | str,
    names: list[str],
    snapshots: pd.DatetimeIndex,
    step: float,
) -> Result:
    if isinstance(index, str):
        return dict.fromkeys(names, index)
    if index.has_duplicates:
        return dict.fromkeys(
            names, f"timestamp {index[index.duplicated()][0]} appears twice"
        )
    diffs = index.sort_values().to_series().diff().dropna()
    if len(diffs) and diffs.min() < pd.Timedelta(minutes=step):
        return dict.fromkeys(
            names,
            f"file step {diffs.min()} is finer than the {step:g}-minute snapshots; not resampled",
        )
    missing = snapshots.difference(index)
    if len(missing):
        return dict.fromkeys(
            names,
            f"file has no value for {len(missing)} of {len(snapshots)} snapshots, "
            f"first {missing[0]}",
        )
    rows = pd.Series(range(len(index)), index=index)[snapshots].to_numpy()
    out: Result = {}
    for name in names:
        col = cols[name]
        if col is None:
            out[name] = f"no column {name!r}"
            continue
        cells = body[col].iloc[rows].reset_index(drop=True)
        got = _numbers(cells, snapshots)
        out[name] = (
            got if isinstance(got, str) else pd.Series(got.to_numpy(), index=snapshots)
        )
    return out


#: a given data file: how to open it, or why it is not read (over the cap)
Given = Callable[[], IO[bytes]] | str


def match_files(wanted: Iterable[str], given: Mapping[str, Given]) -> dict[str, Given]:
    """Each wanted Data File path to the given file it names, or why none (Q10)."""
    by_key: dict[str, list[str]] = {}
    for name in given:
        by_key.setdefault(file_key(name), []).append(name)
    out: dict[str, Given] = {}
    for path in wanted:
        hits = by_key.get(file_key(path), [])
        if len(hits) == 1:
            got = given[hits[0]]
            out[path] = f"data file {hits[0]!r} {got}" if isinstance(got, str) else got
        elif hits:
            out[path] = (
                f"{len(hits)} given files are named {file_key(path)!r}: "
                + ", ".join(sorted(hits))
            )
        else:
            out[path] = f"data file {path!r} not supplied"
    return out
