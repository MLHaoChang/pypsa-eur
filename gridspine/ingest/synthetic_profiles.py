"""Stage 0: synthetic year-long profiles for load and RES capacity factors.

Every series here is a closed-form function of the hour index — deterministic,
no RNG, no seed to carry — so a study re-run reproduces the same year exactly
and the profiles need no artifact of their own to be reproducible.

Calendar convention (the whole module depends on it, so it is stated once):

* the hour index is hour-of-year, 0-based;
* ``day = hour // 24`` (0-based day-of-year, so day 0 is 1 January);
* ``hod = hour % 24`` (0-based hour-of-day, so ``hod`` 19 is the evening peak);
* day 0 is a **Monday**, hence ``day % 7`` in ``{5, 6}`` is Sat/Sun;
* the seasonal terms divide by 365 — no leap-year handling, and ``hours`` is
  free to be any length (a partial year is simply a prefix).

numpy + pandas only: no engine import, and nothing from ``producers/``.
"""
import numpy as np
import pandas as pd

# DELIBERATE DUPLICATION of ``producers/pypsa_nodal.py::LOAD_SHAPE``.
# The engine cage forbids ingest/ importing from producers/ (producers/ is
# allowed to import pypsa; ingest/ must stay engine-free), so the 24 values are
# copied rather than shared. producers/ keeps its own copy as the source the
# dispatch model is built from; the copies are kept in step by
# ``tests/gridspine/test_synthetic_profiles.py::
# test_daily_shape_duplicates_the_producer_load_shape``.
DAILY_SHAPE = [
    0.62, 0.58, 0.56, 0.45, 0.56, 0.60, 0.68, 0.78, 0.86, 0.90, 0.92, 0.93,
    0.92, 0.90, 0.89, 0.90, 0.93, 0.97, 0.99, 1.00, 0.94, 0.86, 0.76, 0.67,
]

HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365

WEEKEND_FACTOR = 0.85  # applied to Saturday and Sunday
LOAD_SEASONAL_AMPLITUDE = 0.12
LOAD_SEASONAL_PEAK_DAY = 15  # mid-January, northern-hemisphere winter peak

WIND_BASE = 0.35
WIND_SYNOPTIC_AMPLITUDE = 0.25
WIND_SYNOPTIC_PERIOD_H = 72.0  # a 3-day synoptic weather cycle
WIND_SYNOPTIC_PHASE = 1.3
WIND_SEASONAL_AMPLITUDE = 0.15
WIND_CF_MIN = 0.02
WIND_CF_MAX = 0.95

SOLAR_SUNRISE_HOD = 6
SOLAR_SUNSET_HOD = 18
SOLAR_SHAPE_EXPONENT = 1.5
SOLAR_SEASONAL_BASE = 0.75
SOLAR_SEASONAL_AMPLITUDE = 0.25
SOLAR_SEASONAL_PEAK_DAY = 172  # ~21 June, summer solstice

PROFILE_LEDGER = [
    "year_load_shape: synthetic per-unit demand profile (assumed) — 24 h "
    "DAILY_SHAPE (peak hod 19, valley hod 3) x weekend factor 0.85 (Sat/Sun) "
    "x seasonal 1 + 0.12*cos(2pi*(day-15)/365), normalised to max 1.0; "
    "day 0 = 1 January = Monday, day = hour//24, hod = hour%24",
    "wind_cf: synthetic wind capacity factor (assumed) — "
    "0.35 + 0.25*sin(2pi*hour/72 + 1.3) + 0.15*cos(2pi*day/365), a 72 h "
    "synoptic cycle over an annual term, clipped to [0.02, 0.95]; no site "
    "data, no correlation between the wind sites",
    "solar_cf: synthetic solar capacity factor (assumed) — zero outside "
    "hod 6..18, otherwise sin(pi*(hod-6)/12)^1.5 x "
    "(0.75 + 0.25*cos(2pi*(day-172)/365)); clear-sky, no weather, no "
    "latitude/tilt model, daylight window fixed all year",
]


def _hour_index(hours: int) -> np.ndarray:
    if hours <= 0:
        raise ValueError(f"hours must be positive, got {hours}")
    return np.arange(hours, dtype=float)


def _series(values: np.ndarray, name: str) -> pd.Series:
    return pd.Series(values, index=pd.RangeIndex(len(values), name="hour"), name=name)


def year_load_shape(hours: int = 8760) -> pd.Series:
    """Per-unit demand for `hours` hours, normalised so the annual max is 1.0.

    Daily shape x weekday/weekend factor x seasonal factor.
    """
    h = _hour_index(hours)
    day = np.floor_divide(h, HOURS_PER_DAY)
    hod = np.mod(h, HOURS_PER_DAY).astype(int)

    daily = np.asarray(DAILY_SHAPE, dtype=float)[hod]
    weekly = np.where(np.mod(day, 7) >= 5, WEEKEND_FACTOR, 1.0)
    seasonal = 1.0 + LOAD_SEASONAL_AMPLITUDE * np.cos(
        2.0 * np.pi * (day - LOAD_SEASONAL_PEAK_DAY) / DAYS_PER_YEAR
    )

    raw = daily * weekly * seasonal
    # Divide by the max so the peak is EXACTLY 1.0 (x/x is exact in IEEE-754).
    return _series(raw / raw.max(), "load_pu")


def wind_cf(hours: int = 8760) -> pd.Series:
    """Wind capacity factor: a 72 h synoptic cycle over an annual term."""
    h = _hour_index(hours)
    day = np.floor_divide(h, HOURS_PER_DAY)

    cf = (
        WIND_BASE
        + WIND_SYNOPTIC_AMPLITUDE
        * np.sin(2.0 * np.pi * h / WIND_SYNOPTIC_PERIOD_H + WIND_SYNOPTIC_PHASE)
        + WIND_SEASONAL_AMPLITUDE * np.cos(2.0 * np.pi * day / DAYS_PER_YEAR)
    )
    return _series(np.clip(cf, WIND_CF_MIN, WIND_CF_MAX), "wind_cf")


def solar_cf(hours: int = 8760) -> pd.Series:
    """Solar capacity factor: a clear-sky diurnal arc scaled by season."""
    h = _hour_index(hours)
    day = np.floor_divide(h, HOURS_PER_DAY)
    hod = np.mod(h, HOURS_PER_DAY)

    span = float(SOLAR_SUNSET_HOD - SOLAR_SUNRISE_HOD)
    arc = np.sin(np.pi * (hod - SOLAR_SUNRISE_HOD) / span)
    # sin(pi) is ~1.2e-16 rather than 0 and can land negative for other
    # endpoints, so floor before the fractional power (which would give NaN).
    arc = np.power(np.clip(arc, 0.0, None), SOLAR_SHAPE_EXPONENT)
    # The window is 6..18 INCLUSIVE, but sin() is analytically zero at both
    # endpoints, so masking the open interval is the same function with one
    # fewer float artefact: sin(pi) evaluates to 1.2e-16, not 0, which would
    # otherwise leave a 1e-24 "capacity factor" at every 18:00.
    daylight = (hod > SOLAR_SUNRISE_HOD) & (hod < SOLAR_SUNSET_HOD)
    arc = np.where(daylight, arc, 0.0)

    seasonal = SOLAR_SEASONAL_BASE + SOLAR_SEASONAL_AMPLITUDE * np.cos(
        2.0 * np.pi * (day - SOLAR_SEASONAL_PEAK_DAY) / DAYS_PER_YEAR
    )
    return _series(np.clip(arc * seasonal, 0.0, None), "solar_cf")
