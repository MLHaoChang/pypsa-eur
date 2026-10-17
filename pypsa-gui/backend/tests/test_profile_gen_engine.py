"""Contract tests for the profile_gen engine interface + synthetic source.

Plan: docs/superpowers/plans/2026-10-17-renewable-profile-generator.md, Task 1.
Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md.

These tests pin the engine contract that every later task (atlite source,
presets, apply path, queue job, router, chat tools) builds on:

- hourly, tz-naive UTC index; length == hours of the requested year
  (8760 / 8784 for leap years) — generation always covers a full weather year;
- values in [0, 1], no NaN;
- solar is exactly 0.0 at night (mutation target);
- determinism: same inputs -> byte-identical series (mutation target);
- meta completeness per spec D6;
- typed refusals at the boundary (carrier guard, unknown source, bad
  coordinates).

Run with the pixi env python:
    pixi run python -m pytest pypsa-gui/backend/tests/test_profile_gen_engine.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from services.profile_gen import (
    InvalidCoordinatesError,
    ProfileGenError,
    ProfilePoint,
    UnknownSourceError,
    UnsupportedTechnologyError,
    generate_profile,
)

BERLIN = ("berlin", 13.4, 52.5)
LISBON = ("lisbon", -9.1, 38.7)
FIXED_NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


def _solar(points=(BERLIN,), year=2023, params=None, **kw):
    return generate_profile(
        points=list(points),
        technology="solar",
        params=params if params is not None else {"seed": 42},
        source="synthetic",
        year=year,
        now=FIXED_NOW,
        **kw,
    )


def _wind(points=(BERLIN,), year=2023, params=None, **kw):
    return generate_profile(
        points=list(points),
        technology="wind",
        params=params if params is not None else {"seed": 42},
        source="synthetic",
        year=year,
        now=FIXED_NOW,
        **kw,
    )


# --- index contract ---------------------------------------------------------


def test_index_is_hourly_tz_naive_utc_full_year():
    res = _solar()
    idx = res.series.index
    assert isinstance(idx, pd.DatetimeIndex)
    assert idx.tz is None, "snapshots in this codebase are tz-naive UTC"
    assert idx[0] == pd.Timestamp("2023-01-01 00:00")
    assert idx[-1] == pd.Timestamp("2023-12-31 23:00")
    assert (idx[1] - idx[0]) == pd.Timedelta(hours=1)
    assert idx.is_monotonic_increasing and idx.is_unique
    assert len(idx) == 8760


def test_leap_year_has_8784_hours_and_keeps_feb29():
    res = _solar(year=2020)
    assert len(res.series) == 8784
    assert pd.Timestamp("2020-02-29 12:00") in res.series.index
    assert res.meta["leap_day"] == "kept"  # dropping happens at apply time


# --- value contract ---------------------------------------------------------


def test_values_bounded_no_nan():
    for res in (_solar(), _wind(), _solar(year=2020), _wind(year=2024)):
        vals = res.series.to_numpy()
        assert np.isfinite(vals).all()
        assert vals.min() >= 0.0 and vals.max() <= 1.0


def test_solar_is_exactly_zero_at_night():
    # Berlin in January: deep night is 21:00-03:00 UTC. The synthetic model
    # must put an exact 0.0 there — this is the mutation target (dropping the
    # night-zero step turns this test red).
    res = _solar()
    jan = res.series[res.series.index.month == 1]
    night = jan[(jan.index.hour >= 21) | (jan.index.hour <= 3)]
    assert len(night) > 0
    assert (night.to_numpy() == 0.0).all()


def test_solar_is_positive_at_summer_noon():
    res = _solar()
    jul = res.series[res.series.index.month == 7]
    noon = jul[(jul.index.hour >= 9) & (jul.index.hour <= 14)]
    assert (noon.to_numpy() > 0.3).all(), "clear-sky July noon in Berlin"


def test_wind_varies_and_has_sane_mean():
    res = _wind()
    s = res.series[BERLIN[0]]
    assert s.std() > 0.01, "wind profile must not be constant"
    assert 0.05 < s.mean() < 0.8


def test_latitude_changes_solar_shape():
    north = _solar(points=(BERLIN,)).series[BERLIN[0]]
    south = _solar(points=(LISBON,)).series[LISBON[0]]
    assert not np.allclose(north.to_numpy(), south.to_numpy())


# --- determinism (mutation target) -----------------------------------------


def test_same_inputs_byte_identical():
    a = _wind(points=(BERLIN, LISBON))
    b = _wind(points=(BERLIN, LISBON))
    pd.testing.assert_frame_equal(a.series, b.series, check_exact=True)
    assert a.meta == b.meta  # `now` is pinned, so even meta is reproducible


def test_seed_changes_series_but_not_contract():
    a = _wind(params={"seed": 1}).series
    b = _wind(params={"seed": 2}).series
    assert not np.allclose(a.to_numpy(), b.to_numpy())
    assert len(a) == len(b) == 8760


# --- shape / points handling -------------------------------------------------


def test_multiple_points_yield_one_column_each():
    res = _wind(points=(BERLIN, LISBON))
    assert list(res.series.columns) == ["berlin", "lisbon"]
    # Coordinates feed the noise seed: two sites must not share one series.
    assert not np.allclose(
        res.series["berlin"].to_numpy(), res.series["lisbon"].to_numpy()
    )


def test_points_accept_tuples_dicts_and_profile_points():
    res = _solar(points=[BERLIN, {"id": "x", "lon": 0.0, "lat": 40.0},
                         ProfilePoint(id="y", lon=10.0, lat=50.0)])
    assert list(res.series.columns) == ["berlin", "x", "y"]


def test_empty_points_refused():
    with pytest.raises(ProfileGenError, match="at least one point"):
        _solar(points=[])


def test_out_of_range_coordinates_refused():
    with pytest.raises(InvalidCoordinatesError):
        _solar(points=[("bad", 200.0, 52.5)])
    with pytest.raises(InvalidCoordinatesError):
        _solar(points=[("bad", 13.4, 95.0)])


def test_duplicate_point_ids_refused():
    with pytest.raises(ProfileGenError, match="unique"):
        _solar(points=[("a", 13.4, 52.5), ("a", 0.0, 40.0)])


# --- boundary guards ----------------------------------------------------------


def test_carrier_guard_refuses_non_renewable():
    with pytest.raises(UnsupportedTechnologyError, match="gas"):
        generate_profile(
            points=[BERLIN], technology="gas", params={},
            source="synthetic", year=2023, now=FIXED_NOW,
        )


def test_unknown_source_refused_with_available_list():
    with pytest.raises(UnknownSourceError, match="synthetic"):
        generate_profile(
            points=[BERLIN], technology="wind", params={},
            source="atlite",  # lands in Task 3 — until then: typed refusal
            year=2023, now=FIXED_NOW,
        )


# --- meta contract (spec D6) ---------------------------------------------------


def test_meta_completeness():
    res = _solar(points=(BERLIN, LISBON), year=2020, params={"seed": 7})
    meta = res.meta
    assert meta["source"] == "synthetic"
    assert meta["technology"] == "solar"
    assert meta["preset"] == "default"
    assert meta["params"] == {"seed": 7}
    assert meta["weather_year"] == 2020
    assert meta["bbox"] == [-9.1, 38.7, 13.4, 52.5]  # [min lon, min lat, max lon, max lat]
    assert meta["leap_day"] == "kept"
    assert meta["generated_at"] == FIXED_NOW.isoformat()
    assert meta["atlite_version"] is None  # synthetic never touches atlite


def test_generated_at_defaults_to_real_time():
    res = generate_profile(
        points=[BERLIN], technology="wind", params={}, source="synthetic",
        year=2023,
    )
    parsed = datetime.fromisoformat(res.meta["generated_at"])
    assert parsed.tzinfo is not None
