"""
PLEXOS timeslice patterns (slice 3b, ``gridspine/ingest/plexos_pattern.py``).

GK A's plan GO (P1-P3) is in ``.scratch/project-io/plexos-mapping.md``.
"""

from __future__ import annotations

import subprocess
import sys

import pandas as pd
import pytest

from gridspine.ingest.plexos_pattern import mask, parse


def at(pattern, *stamps, step=60):
    return mask(pattern, pd.DatetimeIndex(stamps), step).tolist()


def test_weekday_one_is_sunday():
    """GK A P2: 1 January 2017 was a Sunday, 2 January a Monday."""
    assert at("W1", "2017-01-01 12:00", "2017-01-02 12:00") == [True, False]
    assert at("W2", "2017-01-01 12:00", "2017-01-02 12:00") == [False, True]
    assert at("W7", "2016-12-31 12:00") == [True]  # a Saturday


def test_month_day_and_hour():
    assert at("M2", "2017-01-31 23:00", "2017-02-01 00:00") == [False, True]
    assert at("D31", "2017-01-31 23:00", "2017-02-01 00:00") == [True, False]
    # H1 is 00:00-01:00, H24 23:00-24:00
    assert at("H1", "2017-01-01 00:00", "2017-01-01 01:00") == [True, False]
    assert at("H24", "2017-01-01 23:00", "2017-01-02 00:00") == [True, False]


def test_comma_and_semicolon_precedence():
    """GK A P3: `M1,W2;M3` is (January and Monday) or March."""
    stamps = ("2017-01-01 12:00", "2017-01-02 12:00", "2017-03-05 12:00")
    assert at("M1,W2;M3", *stamps) == [False, True, True]  # Jan Sun, Jan Mon, Mar Sun


def test_items_of_one_symbol_are_or_and_ranges_are_inclusive():
    stamps = ("2017-01-15", "2017-02-15", "2017-03-15", "2017-04-15")
    assert at("M1,M3", *stamps) == [True, False, True, False]
    assert at("M2-3", *stamps) == [False, True, True, False]
    assert at("m2-3", *stamps) == [False, True, True, False]


@pytest.mark.parametrize(
    "pattern, reason",
    [
        ("", "empty pattern"),
        ("Y2015", "pattern symbol 'Y' not read"),
        ("Q1", "pattern symbol 'Q' not read"),
        ("P1", "pattern symbol 'P' not read"),
        ("M13", "outside M1-12"),
        ("M0", "outside M1-12"),
        ("H0", "outside H1-24"),
        ("W8", "outside W1-7"),
        ("D32", "outside D1-31"),
        ("M3-1", "reversed"),
        ("Mx", "pattern item 'Mx' not read"),
        ("M1;", "pattern item '' not read"),
    ],
)
def test_unreadable_patterns_say_why(pattern, reason):
    assert reason in parse(pattern)


def test_half_hour_step_takes_the_hour_it_starts_in():
    """GK A P1: on a step finer than an hour, H is the hour the snapshot starts in."""
    snaps = pd.date_range("2017-01-01", periods=4, freq="30min")
    assert mask("H1", snaps, 30).tolist() == [True, True, False, False]


def test_three_hour_step_partly_covered_is_refused():
    """GK A P1: a 3 h snapshot H covers only partly is refused, never rounded."""
    snaps = pd.date_range("2017-01-01", periods=8, freq="3h")
    got = mask("H8-9", snaps, 180)
    assert got == (
        "pattern 'H8-9' covers snapshot 2017-01-01 06:00:00 only partly; not rounded"
    )
    whole = mask("H7-9", snaps, 180)  # 06:00-09:00 is hours 7, 8 and 9
    assert whole.tolist() == [False, False, True, False, False, False, False, False]


def test_pattern_reader_loads_without_pypsa():
    code = (
        "import sys; sys.modules['pypsa'] = None; "
        "import gridspine.ingest.plexos_pattern"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_mask_passes_on_why_and_takes_no_snapshots():
    assert mask("Y1", pd.DatetimeIndex(["2017-01-01"]), 60) == (
        "pattern symbol 'Y' not read (read are M, W, D, H)"
    )
    assert mask("H1", pd.DatetimeIndex([]), 60).tolist() == []
    assert parse("   ") == "empty pattern"
