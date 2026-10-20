"""The weather year sliced to the snapshot window (owner decision; slice W).

A network whose snapshots sample the year — an Energy Hub study week, two
sampled weeks, a month — takes a generated series by month-day-hour, the same
mapping a full year uses:

* the values are the source year's same hours, and the window is recorded as
  ``window: {first, last, hours, runs}``;
* a power series' energy is held to what the window claims to represent:
  the source year's energy × Σ weightings / the source year's hours, per
  investment period on a tiled network — so an unweighted week, a week
  weighted ×52 and two weeks at ×26 all pass, and a summer week weighted to
  stand for the year is flagged;
* per-unit series and prices record the window with no energy check;
* an hour the source does not have (Feb 29, a DST-gap hour on a local
  clock), an off-the-hour or sub-hourly window, and a window that repeats a
  calendar hour are refused, never filled.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.profile_gen.apply import (
    ENERGY_MISMATCH_WARNING,
    LeapYearMismatchError,
    UnsupportedSnapshotsError,
    assign_profile,
)
from services.profile_gen.engine import generate_profile
from services.pypsa_service import PyPSAService
from services.snapshot_index import _build_period_multiindex
from services.user_timeseries import _user_ts, _user_ts_meta

NOW = "2026-10-20T00:00:00+00:00"
LOAD = ("loads", "p_set", "L1")


def _year(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")


def _week(start: str) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=168, freq="h")


def _daily(year: int = 2023) -> pd.Series:
    """Peak-normalised daily cycle: every week holds the year's mean."""
    idx = _year(year)
    v = 0.6 + 0.4 * np.sin(np.pi * idx.hour.to_numpy() / 24.0)
    return pd.Series(v / v.max(), index=idx, dtype=float)


def _seasonal(year: int = 2023) -> pd.Series:
    """Summer-heavy: a June week sits far above the year's mean."""
    idx = _year(year)
    v = np.where(idx.month.isin([6, 7, 8]), 1.0, 0.25)
    return pd.Series(v, index=idx, dtype=float)


def _load_meta(**over) -> dict:
    meta = {"family": "load", "source": "archetype", "archetype": "test",
            "unit": "per_unit_peak", "scale_mw": 50.0, "resolution": "hourly",
            "generated_at": NOW}
    meta.update(over)
    return meta


def _net(snapshots, weights=None):
    n = PyPSAService.get_network()
    snapshots = snapshots.copy()
    snapshots.name = "snapshot" if not isinstance(snapshots, pd.MultiIndex) else snapshots.name
    n.set_snapshots(snapshots)
    if weights is not None:
        for col in n.snapshot_weightings.columns:
            n.snapshot_weightings[col] = weights
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "gas")
    n.add("Carrier", "solar")
    n.add("Load", "L1", bus="B1")
    n.add("Generator", "CHP", bus="B1", carrier="gas", p_nom=100.0)
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=10.0)
    return n


def _ctx():
    return PyPSAService.get_active_context()


def _apply_load(shape, **kw):
    return assign_profile(_ctx(), "L1", shape, _load_meta(),
                          component_type="loads", attribute="p_set", now=NOW,
                          time_basis="UTC", **kw)


def _mismatch(out) -> list[str]:
    return [w for w in out["warnings"] if w.startswith(ENERGY_MISMATCH_WARNING)]


# ── one horizon ──────────────────────────────────────────────────────────────

def test_a_study_week_takes_the_source_years_same_hours():
    idx = _week("2023-06-05T00:00")
    _net(idx)
    shape = _daily()
    out = _apply_load(shape)
    stored = _user_ts[LOAD]
    assert list(stored.index) == list(idx)
    np.testing.assert_allclose(stored.to_numpy(), shape.loc[idx].to_numpy() * 50.0)
    window = {"first": "2023-06-05 00:00:00", "last": "2023-06-11 23:00:00",
              "hours": 168, "runs": 1}
    assert out["window"] == window
    assert _user_ts_meta[LOAD]["window"] == window
    # An unweighted week is held to 168/8760 of the year, which it is.
    assert out["modelled_mwh"]["total"] == pytest.approx(float(shape.loc[idx].sum()) * 50.0)
    assert _mismatch(out) == []


def test_a_week_weighted_to_the_year_is_checked_against_the_year():
    idx = _week("2023-06-05T00:00")
    _net(idx, weights=8760 / 168)
    out = _apply_load(_daily())
    assert out["modelled_mwh"]["total"] == pytest.approx(out["profile_annual_mwh"], rel=1e-3)
    assert _mismatch(out) == []


def test_two_sampled_weeks_at_non_uniform_weights_use_the_sum_of_weights():
    idx = _week("2023-01-09T00:00").append(_week("2023-07-10T00:00"))
    w = pd.Series(np.r_[np.full(168, 26.0), np.full(168, 26.0)], index=idx)
    w.iloc[:24] = 30.0                     # not one factor × the hour count
    _net(idx, weights=w.to_numpy())
    shape = _daily()
    out = _apply_load(shape)
    assert out["window"]["runs"] == 2
    assert out["window"]["hours"] == 336
    np.testing.assert_allclose(_user_ts[LOAD].to_numpy(), shape.loc[idx].to_numpy() * 50.0)
    expected = out["profile_annual_mwh"] * float(w.sum()) / 8760
    assert out["modelled_mwh"]["total"] == pytest.approx(expected, rel=0.02)
    assert _mismatch(out) == []


def test_a_summer_week_weighted_to_stand_for_the_year_is_flagged():
    idx = _week("2023-06-05T00:00")
    _net(idx, weights=8760 / 168)
    out = _apply_load(_seasonal())
    [w] = _mismatch(out)
    assert "2023-06-05 00:00:00 – 2023-06-11 23:00:00" in w
    assert "168 h in 1 run" in w
    assert "8,760 h" in w and "100.0%" in w
    assert f"{out['profile_annual_mwh']:,.0f} MWh" in w
    assert f"{out['modelled_mwh']['total']:,.0f} MWh" in w
    assert ("loads", "p_set", "L1") in _user_ts           # it stays a warning


def test_a_weather_profile_slices_onto_a_week_too():
    idx = _week("2024-03-04T00:00")
    _net(idx)
    result = generate_profile(points=[("site", 13.4, 52.5)], technology="solar",
                              source="synthetic", year=2024, now=NOW)
    series = result.series["site"]
    out = assign_profile(_ctx(), "Solar", series, result.meta, now=NOW, time_basis="UTC")
    stored = _user_ts[("generators", "p_max_pu", "Solar")]
    np.testing.assert_allclose(stored.to_numpy(), series.loc[idx].to_numpy())
    assert out["window"]["hours"] == 168


def test_a_per_unit_series_records_the_window_with_no_energy_check():
    idx = _week("2023-06-05T00:00")
    _net(idx, weights=8760 / 168)
    meta = {"family": "availability", "source": "rule", "resolution": "hourly",
            "generated_at": NOW}
    out = assign_profile(_ctx(), "CHP", pd.Series(0.9, index=_year(2023)), meta,
                         attribute="p_max_pu", now=NOW, time_basis="UTC")
    assert out["window"]["hours"] == 168
    assert "modelled_mwh" not in out
    assert _mismatch(out) == []


# ── refusals ─────────────────────────────────────────────────────────────────

def test_feb_29_on_a_non_leap_source_is_refused_by_the_hour():
    _net(_week("2024-02-26T00:00"))
    with pytest.raises(LeapYearMismatchError, match="2024-02-29 00:00:00 maps to 02-29 00:00"):
        _apply_load(_daily(2023))
    assert LOAD not in _user_ts


def test_the_spring_forward_hour_on_a_local_clock_is_refused():
    _net(_week("2023-03-24T00:00"))
    with pytest.raises(UnsupportedSnapshotsError,
                       match="2023-03-26 02:00:00 does not exist on the Europe/Berlin clock"):
        assign_profile(_ctx(), "L1", _daily(), _load_meta(), component_type="loads",
                       attribute="p_set", now=NOW, time_basis="Europe/Berlin")


def test_an_off_the_hour_window_is_refused():
    _net(pd.date_range("2023-06-05T00:30", periods=168, freq="h"))
    with pytest.raises(UnsupportedSnapshotsError, match="not on the hour"):
        _apply_load(_daily())


def test_a_sub_hourly_window_is_refused():
    _net(pd.date_range("2023-06-05T00:00", periods=336, freq="30min"))
    with pytest.raises(UnsupportedSnapshotsError, match="sub-hourly"):
        _apply_load(_daily())


def test_a_window_that_repeats_a_calendar_hour_is_refused():
    _net(_week("2023-06-05T00:00").append(_week("2024-06-05T00:00")))
    with pytest.raises(UnsupportedSnapshotsError, match="repeats a calendar hour"):
        _apply_load(_daily())


# ── investment periods ───────────────────────────────────────────────────────

def test_each_period_window_is_held_to_its_own_weights():
    mi = _build_period_multiindex([2030, 2040], [_week("2030-06-03T00:00"),
                                                 _week("2040-01-09T00:00")])
    weights = np.r_[np.ones(168), np.full(168, 8760 / 168)]
    _net(mi, weights=weights)
    shape = _seasonal()
    out = _apply_load(shape)
    windows = _user_ts_meta[LOAD]["window"]
    assert set(windows) == {"2030", "2040"} and windows["2040"]["hours"] == 168
    # 2030: an unweighted June week of a summer-heavy year sits far above
    # 168/8760 of the year; 2040: a January week weighted to the year sits
    # far below it. Each is flagged by its own period, not summed.
    warnings = _mismatch(out)
    assert len(warnings) == 2
    assert warnings[0].startswith(f"{ENERGY_MISMATCH_WARNING}: period 2030")
    assert warnings[1].startswith(f"{ENERGY_MISMATCH_WARNING}: period 2040")
    assert "1.9%" in warnings[0] and "100.0%" in warnings[1]


def test_a_representative_period_window_passes():
    mi = _build_period_multiindex([2030, 2040], [_week("2030-06-03T00:00"),
                                                 _week("2040-06-04T00:00")])
    _net(mi, weights=np.full(336, 8760 / 168))
    out = _apply_load(_daily())
    assert set(out["modelled_mwh"]) == {"2030", "2040"}
    for v in out["modelled_mwh"].values():
        assert v == pytest.approx(out["profile_annual_mwh"], rel=1e-3)
    assert _mismatch(out) == []
