"""The weather year a generation defaults to (RP-03, spec R2).

The snapshots' calendar year when it is a completed past year, else the last
completed year. For a multi-period network the investment-period level is NOT
a calendar year (2030, 2040…); the year is read from the timestep level, the
year of its first stamp.
"""
from __future__ import annotations

import datetime as _dt
from typing import Literal

import pandas as pd

YearSource = Literal["snapshots", "default", "explicit"]


def last_completed_year(today: _dt.date | None = None) -> int:
    return (today or _dt.datetime.now(_dt.timezone.utc).date()).year - 1


def snapshot_year(snapshots) -> int | None:
    if snapshots is None or len(snapshots) == 0:
        return None
    stamps = snapshots.get_level_values(-1) if isinstance(snapshots, pd.MultiIndex) else snapshots
    try:
        return int(pd.Timestamp(stamps[0]).year)
    except (TypeError, ValueError):
        return None


def default_weather_year(snapshots, today: _dt.date | None = None) -> tuple[int, YearSource]:
    """(year, "snapshots"|"default")."""
    last = last_completed_year(today)
    year = snapshot_year(snapshots)
    if year is not None and year <= last:
        return year, "snapshots"
    return last, "default"
