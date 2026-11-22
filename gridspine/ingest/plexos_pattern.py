"""
PLEXOS timeslice patterns to masks over the snapshots (slice 3b).
Pure pandas: no ``pypsa`` (engine cage, IMPORT-CAGE-1). Plan and GK A's
conditions: ``.scratch/project-io/plexos-mapping.md``.

A pattern is terms joined by ``;`` (OR); a term is items joined by ``,``;
items of one symbol are OR-ed, symbols are AND-ed. So ``M1,W2;M3`` is
"January Mondays, or any day in March" (GK A P3). Symbols read:

- ``M`` month 1-12;
- ``W`` day of the week 1-7, 1 = Sunday (GK A P2);
- ``D`` day of the month 1-31;
- ``H`` hour of the day 1-24, H1 = 00:00-01:00 (GK A P1).

An item is a number or a range ``a-b`` with a <= b. Any other symbol (Y, Q, P,
week of the year …), an empty pattern or a number out of range gives a reason,
never a guess. A snapshot is in the mask when every hour it spans matches, out
of it when none does; a snapshot the pattern covers only partly (a 3 h step
under ``H8-9``) gives a reason, never a rounding.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

#: symbol -> (lowest, highest) number
SYMBOLS = {"M": (1, 12), "W": (1, 7), "D": (1, 31), "H": (1, 24)}
_ITEM = re.compile(r"^([A-Z])(\d+)(?:-(\d+))?$")

#: OR of terms; a term maps symbol -> its (lo, hi) ranges (OR-ed)
Parsed = list[dict[str, list[tuple[int, int]]]]


def parse(pattern: str) -> Parsed | str:
    """The pattern's terms, or why it cannot be read."""
    if not pattern or not pattern.strip():
        return "empty pattern"
    terms: Parsed = []
    for raw_term in pattern.split(";"):
        term: dict[str, list[tuple[int, int]]] = {}
        for raw in raw_term.split(","):
            item = raw.strip().upper()
            hit = _ITEM.match(item)
            if not hit:
                return f"pattern item {raw.strip()!r} not read"
            sym, lo_s, hi_s = hit.groups()
            if sym not in SYMBOLS:
                return f"pattern symbol {sym!r} not read (read are M, W, D, H)"
            lo, hi = int(lo_s), int(hi_s if hi_s is not None else lo_s)
            low, high = SYMBOLS[sym]
            if not (low <= lo <= hi <= high):
                return f"pattern item {item!r} outside {sym}{low}-{high} or reversed"
            term.setdefault(sym, []).append((lo, hi))
        terms.append(term)
    return terms


def _fields(hours: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    return {
        "M": hours.month.to_numpy(),
        "W": (hours.dayofweek.to_numpy() + 1) % 7 + 1,  # Monday 0 -> 2, Sunday -> 1
        "D": hours.day.to_numpy(),
        "H": hours.hour.to_numpy() + 1,
    }


def _match(terms: Parsed, hours: pd.DatetimeIndex) -> np.ndarray:
    f = _fields(hours)
    out = np.zeros(len(hours), dtype=bool)
    for term in terms:
        hit = np.ones(len(hours), dtype=bool)
        for sym, ranges in term.items():
            any_range = np.zeros(len(hours), dtype=bool)
            for lo, hi in ranges:
                any_range |= (f[sym] >= lo) & (f[sym] <= hi)
            hit &= any_range
        out |= hit
    return out


def mask(
    pattern: str, snapshots: pd.DatetimeIndex, step_minutes: float
) -> np.ndarray | str:
    """Bool per snapshot, or why the pattern cannot be applied to them."""
    terms = parse(pattern)
    if isinstance(terms, str):
        return terms
    if len(snapshots) == 0:
        return np.zeros(0, dtype=bool)
    step = pd.Timedelta(minutes=step_minutes)
    first = snapshots.min().floor("h")
    last = (snapshots.max() + step - pd.Timedelta(1, "ns")).floor("h")
    hours = pd.date_range(first, last, freq="h")
    hit = _match(terms, hours)
    total = np.concatenate([[0], np.cumsum(hit)])
    # hours [i0, i1] each snapshot spans
    i0 = hours.searchsorted(snapshots.floor("h"))
    i1 = hours.searchsorted((snapshots + step - pd.Timedelta(1, "ns")).floor("h"))
    count = total[i1 + 1] - total[i0]
    span = i1 - i0 + 1
    part = (count > 0) & (count < span)
    if part.any():
        return (
            f"pattern {pattern!r} covers snapshot {snapshots[part.argmax()]} only "
            "partly; not rounded"
        )
    return count == span
