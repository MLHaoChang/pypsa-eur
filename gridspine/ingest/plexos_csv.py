"""
PLEXOS data-file CSVs to time series over the network's snapshots (slice 2b).

Pure pandas: no ``pypsa`` (engine cage, IMPORT-CAGE-1). Plan and GK A's
conditions: ``.scratch/project-io/plexos-mapping.md``.

Layouts read (the header decides; the column for an object is its name):

- ``Datetime,<name>…``: one row per timestamp;
- ``Year,Month,Day,Period,<name>…``: period p of a day starts (p − 1) steps
  after midnight;
- ``NAME,M1,…,M12``: one row per object, one value per calendar month.

A series is used only when it has exactly one readable number for every
snapshot (GK A Q10). A duplicate or unreadable timestamp, a missing snapshot,
a step finer than the snapshots or an empty or non-numeric cell makes the
series unusable, with the reason; nothing is filled, carried forward or
resampled. Timestamps outside the horizon are ignored.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Mapping
from typing import IO

import pandas as pd

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

#: name -> the series over the snapshots, or the reason it cannot be used
Result = dict[str, "pd.Series | str"]


def file_key(path: str) -> str:
    """The name a Data File path is matched by: its basename, case-folded (Q10)."""
    return path.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def date_formats(locale: float | None) -> tuple[tuple[str, ...], str | None]:
    """The formats tried for this Data File locale, and why slash dates are not read."""
    loc = int(locale) if locale is not None and locale == int(locale) else None
    if loc in DAY_FIRST_LOCALES:
        slash = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")
    elif loc in MONTH_FIRST_LOCALES:
        slash = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y")
    else:
        return _ISO, (
            f"day/month order unknown (Data File Locale {loc})"
            if loc is not None
            else "day/month order unknown (the Data File has no Locale)"
        )
    return slash + _ISO, None


def _parse_dates(cells: pd.Series, locale: float | None) -> pd.DatetimeIndex | str:
    formats, why = date_formats(locale)
    text = cells.str.strip()
    out = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    for fmt in formats:
        todo = out.isna()
        if not todo.any():
            break
        out[todo] = pd.to_datetime(text[todo], format=fmt, errors="coerce")
    bad = out.isna()
    if bad.any():
        first = text[bad].iloc[0]
        return f"timestamp {first!r} unreadable" + (
            f": {why}" if why and "/" in first else ""
        )
    return pd.DatetimeIndex(out)


def _column(header: list[str], name: str) -> str | None:
    """The header cell for an object: exact, else the one case-insensitive match."""
    if name in header:
        return name
    hits = [h for h in header if h.casefold() == name.casefold()]
    return hits[0] if len(hits) == 1 else None


def _numbers(cells: pd.Series, where: pd.Index) -> pd.Series | str:
    values = pd.to_numeric(cells.str.strip(), errors="coerce")
    bad = values.isna()
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
    try:
        with opener() as raw:
            first = raw.readline(2**24).decode("utf-8-sig")
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
        layout, keys = "wide", header[:1]
    elif low[:4] == ["year", "month", "day", "period"]:
        layout, keys = "wide", header[:4]
    elif low[:1] == ["name"] and all(m.casefold() in low for m in _MONTHS):
        layout = "monthly"
        keys = header[:1] + [
            next(h for h in header if h.casefold() == m.casefold()) for m in _MONTHS
        ]
    else:
        return dict.fromkeys(
            names,
            f"layout not read (header starts {', '.join(header[:4])}); read are "
            "Datetime, Year/Month/Day/Period and NAME/M1..M12",
        )
    cols = (
        {n: _column(header[len(keys) :], n) for n in names} if layout == "wide" else {}
    )
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
            )
    except (
        ValueError,
        UnicodeDecodeError,
    ) as exc:  # pandas ParserError is a ValueError
        return dict.fromkeys(names, f"not a readable CSV ({str(exc).splitlines()[0]})")
    body.columns = [c.strip() for c in body.columns]
    body = body.fillna("")
    if layout == "monthly":
        return _monthly(body, keys, names, snapshots)
    if len(keys) == 1:
        index = _parse_dates(body[keys[0]], locale)
    else:
        index = _periods(body, keys, step_minutes)
    return _wide(body, cols, index, names, snapshots, step_minutes)


def _periods(
    body: pd.DataFrame, header: list[str], step: float
) -> pd.DatetimeIndex | str:
    parts = {}
    for col in header[:4]:
        v = pd.to_numeric(body[col].str.strip(), errors="coerce")
        if v.isna().any() or (v != v.round()).any():
            return f"{col} {body[col][v.isna() | (v != v.round())].iloc[0]!r} is not a whole number"
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


def _monthly(
    body: pd.DataFrame, header: list[str], names: list[str], snapshots: pd.DatetimeIndex
) -> Result:
    key = body[header[0]].str.strip()
    months = header[1:]
    out: Result = {}
    for name in names:
        hit = key == name
        if not hit.any():
            hit = key.str.casefold() == name.casefold()
        if hit.sum() != 1:
            out[name] = (
                f"{int(hit.sum())} rows for {name!r}"
                if hit.any()
                else f"no row {name!r}"
            )
            continue
        row = body[hit].iloc[0][months].reset_index(drop=True)
        got = _numbers(row, pd.Index(_MONTHS))
        if isinstance(got, str):
            out[name] = got
            continue
        out[name] = pd.Series(got.to_numpy()[snapshots.month - 1], index=snapshots)
    return out


def match_files(
    wanted: Iterable[str], given: Mapping[str, Callable[[], IO[bytes]]]
) -> dict[str, Callable[[], IO[bytes]] | str]:
    """Each wanted Data File path to the given file it names, or why none (Q10)."""
    by_key: dict[str, list[str]] = {}
    for name in given:
        by_key.setdefault(file_key(name), []).append(name)
    out: dict[str, Callable[[], IO[bytes]] | str] = {}
    for path in wanted:
        hits = by_key.get(file_key(path), [])
        if len(hits) == 1:
            out[path] = given[hits[0]]
        elif hits:
            out[path] = (
                f"{len(hits)} given files are named {file_key(path)!r}: "
                + ", ".join(sorted(hits))
            )
        else:
            out[path] = f"data file {path!r} not supplied"
    return out
