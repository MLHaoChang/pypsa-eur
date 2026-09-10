"""Synthetic year profiles: shape, range, determinism and calendar conventions.

The profiles are closed-form (no RNG), so every property below is an exact
pin rather than a statistical one.
"""
import numpy as np
import pandas as pd
import pytest

from gridspine.ingest.synthetic_profiles import (
    DAILY_SHAPE,
    PROFILE_LEDGER,
    solar_cf,
    wind_cf,
    year_load_shape,
)

PROFILES = (year_load_shape, wind_cf, solar_cf)


# --- shape / index -------------------------------------------------------

@pytest.mark.parametrize("fn", PROFILES)
@pytest.mark.parametrize("hours", [168, 8760])
def test_length_follows_hours_param(fn, hours):
    s = fn(hours)
    assert isinstance(s, pd.Series)
    assert len(s) == hours


@pytest.mark.parametrize("fn", PROFILES)
def test_default_is_a_full_year(fn):
    assert len(fn()) == 8760


@pytest.mark.parametrize("fn", PROFILES)
def test_index_is_hour_of_year_0_based(fn):
    s = fn(168)
    assert list(s.index) == list(range(168))


@pytest.mark.parametrize("fn", PROFILES)
def test_values_within_global_bound(fn):
    v = fn(8760).to_numpy()
    assert np.isfinite(v).all()
    assert v.min() >= 0.0
    assert v.max() <= 1.05


@pytest.mark.parametrize("fn", PROFILES)
def test_deterministic_across_calls(fn):
    pd.testing.assert_series_equal(fn(8760), fn(8760))


# --- load ----------------------------------------------------------------

def test_load_is_normalised_to_exactly_one():
    s = year_load_shape(8760)
    assert s.max() == 1.0
    assert s.min() > 0.0


def test_load_daily_peak_and_valley_follow_daily_shape():
    # Convention pinned here: day = h // 24, hour-of-day = h % 24.
    s = year_load_shape(168)
    day0 = s.iloc[0:24]
    assert int(np.argmax(day0.to_numpy())) == 19
    assert int(np.argmin(day0.to_numpy())) == 3


def test_load_weekend_is_damped_relative_to_midweek():
    # Day 0 of the year is a Monday, so day 5 is Saturday and day 2 Wednesday.
    s = year_load_shape(168)
    saturday = s.iloc[5 * 24:6 * 24].mean()
    wednesday = s.iloc[2 * 24:3 * 24].mean()
    assert saturday < wednesday


def test_load_sunday_is_damped_too():
    s = year_load_shape(168)
    sunday = s.iloc[6 * 24:7 * 24].mean()
    thursday = s.iloc[3 * 24:4 * 24].mean()
    assert sunday < thursday


def test_load_seasonal_term_peaks_near_day_15():
    # 1 + 0.12*cos(2*pi*(day-15)/365) is maximal at day 15, minimal half a year on.
    s = year_load_shape(8760)
    hod19 = s.to_numpy().reshape(365, 24)[:, 19]
    assert int(np.argmax(hod19)) == 15


# --- wind ----------------------------------------------------------------

def test_wind_range_and_lower_clip_binds():
    s = wind_cf(8760)
    assert s.min() >= 0.02
    assert s.max() <= 0.95
    # 0.35 - 0.25 - 0.15 = -0.05 < 0.02, so the floor must actually bind.
    assert s.min() == pytest.approx(0.02)


def test_wind_has_a_72_hour_synoptic_cycle():
    s = wind_cf(8760).to_numpy()
    # Same hour three days apart differs only by the slow seasonal term.
    assert s[100] == pytest.approx(s[100 + 72], abs=2e-3)
    assert s[100] != pytest.approx(s[100 + 36], abs=1e-2)


# --- solar ---------------------------------------------------------------

def test_solar_is_zero_at_night_and_positive_at_noon():
    s = solar_cf(8760)
    assert s.iloc[3] == 0.0
    assert s.iloc[12] > 0.0


def test_solar_is_zero_outside_the_daylight_window():
    v = solar_cf(8760).to_numpy().reshape(365, 24)
    for hod in list(range(0, 6)) + list(range(19, 24)):
        assert (v[:, hod] == 0.0).all(), hod
    # The window endpoints are zero by construction (sin(0) and sin(pi)).
    assert (v[:, 6] == 0.0).all()
    assert (v[:, 18] == 0.0).all()
    assert (v[:, 12] > 0.0).all()


def test_solar_summer_noon_beats_winter_noon():
    s = solar_cf(8760)
    assert s.iloc[172 * 24 + 12] > s.iloc[350 * 24 + 12]


def test_solar_seasonal_peak_is_day_172():
    noon = solar_cf(8760).to_numpy().reshape(365, 24)[:, 12]
    assert int(np.argmax(noon)) == 172


# --- ledger / duplication -------------------------------------------------

def test_daily_shape_duplicates_the_producer_load_shape():
    # Deliberate duplication: the engine cage forbids ingest/ importing
    # producers/. This test is the drift guard for the copy.
    from gridspine.producers.pypsa_nodal import LOAD_SHAPE  # test-only import

    assert list(DAILY_SHAPE) == list(LOAD_SHAPE)
    assert len(DAILY_SHAPE) == 24
    assert DAILY_SHAPE[19] == 1.00
    assert DAILY_SHAPE[3] == 0.45


def test_profile_ledger_names_all_three_profiles_as_assumed():
    assert isinstance(PROFILE_LEDGER, list)
    assert len(PROFILE_LEDGER) == 3
    assert all(isinstance(e, str) and e for e in PROFILE_LEDGER)
    text = " ".join(PROFILE_LEDGER)
    for token in ("year_load_shape", "wind_cf", "solar_cf"):
        assert token in text
    for entry in PROFILE_LEDGER:
        assert "synthetic" in entry and "assumed" in entry


def test_synthetic_profiles_imports_no_engine():
    """numpy/pandas only — no engine, and nothing from producers/.

    Parsed rather than grepped: the module's own comments discuss the cage,
    so a substring scan of the source flags its own documentation.
    """
    import ast

    import gridspine.ingest.synthetic_profiles as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert imported == {"numpy", "pandas"}
