"""`assign_profile`: apply a generated profile to a generator (spec §4/§5, Task 5).

The apply path is where a generated weather-year series meets the network.
Spec §4's alignment table is implemented EXACTLY — never silent reindexing:

  * target spans the weather year, hourly → align by timestamp;
  * leap weather year onto a non-leap target → drop Feb 29 (meta
    ``leap_day: dropped``);
  * non-leap weather year onto a leap target → typed refusal (fabricating
    24 h is worse than refusing);
  * a partial-year or segmented hourly window → sliced from the weather
    year (test_profile_gen_snapshot_window.py); sub-hourly → typed refusal naming
    the supported shapes;
  * MultiIndex (multi-investment-period) snapshots → tile the weather year
    into EVERY period by month-day-hour, leap rules per period (owner
    ruling D7=B), meta ``tiled: true``.

Plus the write-path contract: store + meta entry, `_t` reapply, changelog,
dispatch invalidation, overwrite semantics, and undo restoring the prior
state — including a network-native profile when no user entry existed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services import change_log_service
from services.dispatch_status import dispatch_status
from services.network_undo import apply_undo, push_undo_snapshot
from services.profile_gen.apply import (
    CarrierMismatchError,
    ComponentNotFoundError,
    InvalidTimeBasisError,
    LeapYearMismatchError,
    ProfileApplyError,
    UnsupportedSnapshotsError,
    assign_profile,
)
from services.profile_gen.engine import generate_profile
from services.pypsa_service import PyPSAService
from services.snapshot_index import _build_period_multiindex
from services.user_timeseries import _user_ts, _user_ts_meta

KEY = ("generators", "p_max_pu", "Solar")
NOW = "2026-10-18T00:00:00+00:00"


def _weather(technology: str = "solar", year: int = 2024):
    """A real engine series: one point, hourly, the full weather year."""
    result = generate_profile(
        points=[("site", 13.4, 52.5)],
        technology=technology,
        source="synthetic",
        year=year,
        now=NOW,
    )
    return result.series["site"], result.meta


def _net(year: int = 2024, name: str = "Solar", carrier: str = "solar"):
    """Fresh singleton network (conftest reset) with one generator on
    full-year hourly snapshots."""
    n = PyPSAService.get_network()
    idx = pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", carrier)
    n.add("Generator", name, bus="B1", carrier=carrier, p_nom=100.0)
    return n


def _ctx():
    return PyPSAService.get_active_context()


# ── flat snapshot alignment ─────────────────────────────────────────────────

def test_exact_year_aligns_by_timestamp():
    n = _net(year=2024)
    series, meta = _weather(year=2024)
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["hours"] == 8784
    assert out["leap_day"] == "kept"
    assert out["tiled"] is False
    stored = _user_ts[KEY]
    pd.testing.assert_series_equal(
        stored.sort_index(), series.sort_index(), check_names=False,
    )
    col = n.generators_t.p_max_pu["Solar"]
    assert len(col) == 8784
    ts = pd.Timestamp("2024-02-29T12:00")
    assert float(col.loc[ts]) == float(series.loc[ts])
    m = _user_ts_meta[KEY]
    assert m["weather_year"] == 2024
    assert m["leap_day"] == "kept"
    assert m["tiled"] is False


def test_leap_source_on_nonleap_target_drops_feb29():
    n = _net(year=2025)                      # non-leap target
    series, meta = _weather(year=2024)       # leap weather year, 8784 h
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["hours"] == 8760
    assert out["leap_day"] == "dropped"
    stored = _user_ts[KEY]
    assert len(stored) == 8760
    # Mar 1 on the target carries the SOURCE's Mar 1 value — not Feb 29's.
    assert float(stored.loc[pd.Timestamp("2025-03-01T00:00")]) == float(
        series.loc[pd.Timestamp("2024-03-01T00:00")]
    )
    assert float(stored.loc[pd.Timestamp("2025-02-28T23:00")]) == float(
        series.loc[pd.Timestamp("2024-02-28T23:00")]
    )
    assert _user_ts_meta[KEY]["leap_day"] == "dropped"
    assert len(n.generators_t.p_max_pu["Solar"]) == 8760


def test_nonleap_source_on_leap_target_refuses():
    _net(year=2024)                          # leap target
    series, meta = _weather(year=2025)       # non-leap weather year
    with pytest.raises(LeapYearMismatchError, match="Feb 29"):
        assign_profile(_ctx(), "Solar", series, meta)
    assert KEY not in _user_ts               # a refused apply writes nothing
    assert KEY not in _user_ts_meta


def test_partial_year_snapshots_take_the_weather_years_same_hours():
    n = PyPSAService.get_network()
    idx = pd.date_range("2024-01-01T00:00", "2024-01-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    series, meta = _weather(year=2024)
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["window"] == {"first": "2024-01-01 00:00:00",
                             "last": "2024-01-31 23:00:00", "hours": 744, "runs": 1}
    assert float(_user_ts[KEY].loc["2024-01-15T12:00"]) == float(
        series.loc["2024-01-15T12:00"])


def test_subhourly_snapshots_refuse():
    n = PyPSAService.get_network()
    idx = pd.date_range("2024-01-01T00:00", "2024-12-31T23:30", freq="30min")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    series, meta = _weather(year=2024)
    with pytest.raises(UnsupportedSnapshotsError, match="hourly"):
        assign_profile(_ctx(), "Solar", series, meta)


def test_segmented_snapshots_are_a_window_of_two_runs():
    n = PyPSAService.get_network()
    week1 = pd.date_range("2024-01-01T00:00", periods=168, freq="h")
    week2 = pd.date_range("2024-07-01T00:00", periods=168, freq="h")
    idx = week1.append(week2)
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    series, meta = _weather(year=2024)
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert (out["window"]["hours"], out["window"]["runs"]) == (336, 2)


# ── multi-investment-period tiling (owner ruling D7=B) ──────────────────────

def test_multi_period_tiling_applies_leap_rules_per_period():
    n = PyPSAService.get_network()
    idx2030 = pd.date_range("2030-01-01T00:00", "2030-12-31T23:00", freq="h")  # 8760
    idx2032 = pd.date_range("2032-01-01T00:00", "2032-12-31T23:00", freq="h")  # 8784, leap
    mi = _build_period_multiindex([2030, 2032], [idx2030, idx2032])
    n.set_snapshots(mi)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)

    series, meta = _weather(year=2024)  # leap weather year, 8784 h
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["tiled"] is True
    assert out["periods"] == [2030, 2032]

    stored = _user_ts[KEY]
    assert isinstance(stored.index, pd.MultiIndex)
    s2030 = stored.loc[2030]
    s2032 = stored.loc[2032]
    assert len(s2030) == 8760      # Feb 29 dropped in the non-leap period
    assert len(s2032) == 8784      # kept in the leap period

    # Both periods carry identical month-day-hour values for common hours.
    ts_common = ("06-01T12:00",)
    assert float(s2030.loc[pd.Timestamp(f"2030-{ts_common[0]}")]) == float(
        s2032.loc[pd.Timestamp(f"2032-{ts_common[0]}")]
    ) == float(series.loc[pd.Timestamp(f"2024-{ts_common[0]}")])
    # The leap period keeps the weather year's Feb 29 values.
    assert float(s2032.loc[pd.Timestamp("2032-02-29T10:00")]) == float(
        series.loc[pd.Timestamp("2024-02-29T10:00")]
    )
    # The non-leap period maps Mar 1 to the weather year's Mar 1.
    assert float(s2030.loc[pd.Timestamp("2030-03-01T00:00")]) == float(
        series.loc[pd.Timestamp("2024-03-01T00:00")]
    )

    m = _user_ts_meta[KEY]
    assert m["tiled"] is True
    assert m["weather_year"] == 2024
    assert m["leap_day"] == "dropped"  # dropped in at least one period
    # …and the tiled profile reached the network's _t table.
    col = n.generators_t.p_max_pu["Solar"]
    assert len(col) == 8760 + 8784


def test_multi_period_with_leap_target_period_and_nonleap_source_refuses():
    n = PyPSAService.get_network()
    idx2030 = pd.date_range("2030-01-01T00:00", "2030-12-31T23:00", freq="h")
    idx2032 = pd.date_range("2032-01-01T00:00", "2032-12-31T23:00", freq="h")  # leap
    mi = _build_period_multiindex([2030, 2032], [idx2030, idx2032])
    n.set_snapshots(mi)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    series, meta = _weather(year=2025)  # non-leap weather year
    with pytest.raises(LeapYearMismatchError, match="Feb 29"):
        assign_profile(_ctx(), "Solar", series, meta)
    assert KEY not in _user_ts


# ── carrier / component validation (spec §4: refuse at apply) ───────────────

def test_non_wind_solar_carrier_refused():
    _net(year=2024, name="CCGT", carrier="gas")
    series, meta = _weather(year=2024)
    with pytest.raises(CarrierMismatchError, match="wind|solar"):
        assign_profile(_ctx(), "CCGT", series, meta)
    assert ("generators", "p_max_pu", "CCGT") not in _user_ts


def test_technology_must_match_the_carrier():
    _net(year=2024, carrier="solar")
    series, meta = _weather(technology="wind", year=2024)
    with pytest.raises(CarrierMismatchError, match="wind"):
        assign_profile(_ctx(), "Solar", series, meta)


def test_wind_profile_on_wind_carrier_applies():
    n = _net(year=2024, name="Wind1", carrier="onwind")
    series, meta = _weather(technology="wind", year=2024)
    out = assign_profile(_ctx(), "Wind1", series, meta)
    assert out["hours"] == 8784
    assert ("generators", "p_max_pu", "Wind1") in _user_ts
    assert len(n.generators_t.p_max_pu["Wind1"]) == 8784


def test_missing_component_refused():
    _net(year=2024)
    series, meta = _weather(year=2024)
    with pytest.raises(ComponentNotFoundError, match="Nope"):
        assign_profile(_ctx(), "Nope", series, meta)


def test_a_dataframe_instead_of_a_series_is_refused():
    _net(year=2024)
    series, meta = _weather(year=2024)
    with pytest.raises(ProfileApplyError, match="Series"):
        assign_profile(_ctx(), "Solar", series.to_frame(), meta)


def test_non_finite_values_are_refused():
    _net(year=2024)
    series, meta = _weather(year=2024)
    bad = series.copy()
    bad.iloc[100] = np.nan
    with pytest.raises(ProfileApplyError, match="non-finite"):
        assign_profile(_ctx(), "Solar", bad, meta)
    assert KEY not in _user_ts


# ── write-path contract ─────────────────────────────────────────────────────

def test_assign_clears_dispatch():
    n = _net(year=2024)
    # Fake a fresh solve on this topology.
    n.generators_t.p = pd.DataFrame(
        {"Solar": np.zeros(len(n.snapshots))}, index=n.snapshots,
    )
    assert dispatch_status(n) == "fresh"
    series, meta = _weather(year=2024)
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["dispatch_cleared"] is True
    assert dispatch_status(n) == "none"


def test_changelog_records_the_apply():
    _net(year=2024)
    before = {e["id"] for e in change_log_service.get_all()}
    series, meta = _weather(year=2024)
    assign_profile(_ctx(), "Solar", series, meta)
    new = [e for e in change_log_service.get_all() if e["id"] not in before]
    assert any(
        e["action"] == "timeseries" and "Solar" in e["name"] and "2024" in e["description"]
        for e in new
    ), f"no apply changelog entry in {new}"


def test_regeneration_overwrites_the_same_key():
    _net(year=2024)
    series, meta = _weather(year=2024)
    assign_profile(_ctx(), "Solar", series, meta)
    replacement = pd.Series(0.5, index=series.index)
    meta2 = dict(meta, preset="hand-tuned")
    assign_profile(_ctx(), "Solar", replacement, meta2)
    stored = _user_ts[KEY]
    assert (stored == 0.5).all()
    assert _user_ts_meta[KEY]["preset"] == "hand-tuned"


def test_undo_restores_the_network_native_profile():
    n = _net(year=2024)
    # A network-native profile (as from a netcdf import), no user entry.
    n.generators_t.p_max_pu = pd.DataFrame(
        {"Solar": pd.Series(0.42, index=n.snapshots)},
    )
    push_undo_snapshot()
    series, meta = _weather(year=2024)
    assign_profile(_ctx(), "Solar", series, meta)
    assert KEY in _user_ts and KEY in _user_ts_meta
    assert float(PyPSAService.get_network().generators_t.p_max_pu["Solar"].iloc[0]) != 0.42

    apply_undo()
    n2 = PyPSAService.get_network()
    assert KEY not in _user_ts_meta
    restored = n2.generators_t.p_max_pu["Solar"]
    assert (restored == 0.42).all(), "the network-native profile must come back"
    # Whatever the store holds after the restore (the undo payload's backup
    # copy is legitimate), it must carry the NATIVE values, not the generated
    # ones.
    if KEY in _user_ts:
        assert (_user_ts[KEY] == 0.42).all()


# ── C2: daily / weekly sources (aggregate-at-generation, upsample-at-apply) ──

def _weather_res(technology: str = "solar", year: int = 2024,
                 resolution: str = "daily"):
    result = generate_profile(
        points=[("site", 13.4, 52.5)],
        technology=technology,
        source="synthetic",
        year=year,
        resolution=resolution,
        now=NOW,
    )
    return result.series["site"], result.meta


def test_daily_upsample_holds_the_day_mean_for_24_hours():
    _net(year=2024)
    hourly, _ = _weather(year=2024)
    daily, meta = _weather_res(year=2024, resolution="daily")
    out = assign_profile(_ctx(), "Solar", daily, meta)
    assert out["resolution"] == "daily"
    assert out["hours"] == 8784
    stored = _user_ts[KEY]
    # Every hour of a day carries that day's mean of the hourly source.
    day = pd.Timestamp("2024-03-05")
    day_hours = stored.loc[day:day + pd.Timedelta(hours=23)]
    assert (day_hours == day_hours.iloc[0]).all()
    expected = hourly.loc[day:day + pd.Timedelta(hours=23)].mean()
    assert abs(float(day_hours.iloc[0]) - float(expected)) < 1e-12
    assert _user_ts_meta[KEY]["resolution"] == "daily"


def test_daily_leap_source_on_nonleap_target_drops_feb29():
    _net(year=2025)
    daily, meta = _weather_res(year=2024, resolution="daily")
    out = assign_profile(_ctx(), "Solar", daily, meta)
    assert out["hours"] == 8760
    assert out["leap_day"] == "dropped"
    stored = _user_ts[KEY]
    # Mar 1 2025 carries the SOURCE's Mar 1 daily mean, not Feb 29's.
    assert float(stored.loc[pd.Timestamp("2025-03-01T12:00")]) == float(
        daily.loc[pd.Timestamp("2024-03-01")]
    )


def test_daily_nonleap_source_on_leap_target_refuses():
    _net(year=2024)
    daily, meta = _weather_res(year=2025, resolution="daily")
    with pytest.raises(LeapYearMismatchError, match="Feb 29"):
        assign_profile(_ctx(), "Solar", daily, meta)
    assert KEY not in _user_ts


def test_daily_tiles_multi_period_snapshots():
    n = PyPSAService.get_network()
    idx2030 = pd.date_range("2030-01-01T00:00", "2030-12-31T23:00", freq="h")
    idx2032 = pd.date_range("2032-01-01T00:00", "2032-12-31T23:00", freq="h")
    mi = _build_period_multiindex([2030, 2032], [idx2030, idx2032])
    n.set_snapshots(mi)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    daily, meta = _weather_res(year=2024, resolution="daily")  # leap source
    out = assign_profile(_ctx(), "Solar", daily, meta)
    assert out["tiled"] is True
    assert out["resolution"] == "daily"
    stored = _user_ts[KEY]
    assert float(stored.loc[(2030, pd.Timestamp("2030-06-01T03:00"))]) == float(
        daily.loc[pd.Timestamp("2024-06-01")]
    )
    assert float(stored.loc[(2032, pd.Timestamp("2032-02-29T03:00"))]) == float(
        daily.loc[pd.Timestamp("2024-02-29")]
    )


def test_weekly_same_year_applies_step_hold():
    _net(year=2023)
    weekly, meta = _weather_res(year=2023, resolution="weekly")
    out = assign_profile(_ctx(), "Solar", weekly, meta)
    assert out["resolution"] == "weekly"
    assert out["hours"] == 8760
    stored = _user_ts[KEY]
    # The week labelled Monday 2023-06-05 covers Jun 5 00:00 – Jun 11 23:00;
    # every one of those hours holds the week's mean.
    wk = float(weekly.loc[pd.Timestamp("2023-06-05")])
    week_hours = stored.loc[
        pd.Timestamp("2023-06-05T00:00"):pd.Timestamp("2023-06-11T23:00")
    ]
    assert len(week_hours) == 168
    assert (week_hours == wk).all()
    # …and the neighbouring hour belongs to the next week.
    assert float(stored.loc[pd.Timestamp("2023-06-12T00:00")]) == float(
        weekly.loc[pd.Timestamp("2023-06-12")]
    )


def test_weekly_cross_year_refuses():
    _net(year=2024)
    weekly, meta = _weather_res(year=2023, resolution="weekly")
    with pytest.raises(UnsupportedSnapshotsError, match="own weather year"):
        assign_profile(_ctx(), "Solar", weekly, meta)
    assert KEY not in _user_ts


def test_weekly_multi_period_refuses():
    n = PyPSAService.get_network()
    idx2030 = pd.date_range("2030-01-01T00:00", "2030-12-31T23:00", freq="h")
    idx2032 = pd.date_range("2032-01-01T00:00", "2032-12-31T23:00", freq="h")
    mi = _build_period_multiindex([2030, 2032], [idx2030, idx2032])
    n.set_snapshots(mi)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    weekly, meta = _weather_res(year=2030, resolution="weekly")
    with pytest.raises(UnsupportedSnapshotsError, match="multi-investment"):
        assign_profile(_ctx(), "Solar", weekly, meta)
    assert KEY not in _user_ts


def test_weekly_leap_year_applies_in_its_own_year():
    _net(year=2024)  # leap target, same year as the weather year
    weekly, meta = _weather_res(year=2024, resolution="weekly")
    out = assign_profile(_ctx(), "Solar", weekly, meta)
    assert out["hours"] == 8784  # Feb 29 is covered by its week's step-hold
    assert out["leap_day"] == "kept"


def test_missing_resolution_meta_defaults_to_hourly():
    """Artifacts written before C2 carry no `resolution` — the apply path
    must treat them as hourly, never refuse."""
    _net(year=2024)
    series, meta = _weather(year=2024)
    meta = {k: v for k, v in meta.items() if k != "resolution"}
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["resolution"] == "hourly"
    assert out["hours"] == 8784


# ── time basis: generated series are UTC; snapshots may be a wall clock ────
#
# Gatekeeper review of the apply path (2026-10-19): mapping UTC hours onto
# local wall-clock snapshots shifts solar by the UTC offset. Naive snapshots
# follow lp_bindings' rule: UTC when commercial.timezone is set, else the
# site clock of unknown offset (applied unshifted, recorded as assumed, with
# a warning); `time_basis="<IANA zone>"` declares a zone's wall clock and
# the series is converted before the month-day-hour map.


def _flat(year: int = 2025):
    n = _net(year=year)
    series, meta = _weather(year=year)
    return n, series, meta


def test_unstated_basis_without_a_site_timezone_is_assumed_utc_and_warned():
    n, series, meta = _flat()
    out = assign_profile(_ctx(), "Solar", series, meta)
    assert out["time_basis"] == "UTC (assumed; site clock unknown)"
    assert _user_ts_meta[KEY]["time_basis"] == "UTC (assumed; site clock unknown)"
    assert len(out["warnings"]) == 1
    assert out["warnings"][0].startswith("time_basis_assumed_utc")
    assert "set the time zone" in out["warnings"][0]
    ts = pd.Timestamp("2025-07-15T12:00")  # unshifted
    assert float(n.generators_t.p_max_pu["Solar"].loc[ts]) == float(series.loc[ts])


def test_unstated_basis_with_a_commercial_timezone_reads_snapshots_as_utc():
    n, series, meta = _flat()
    ctx = _ctx()
    ctx.solver_state["solver_config"].commercial = {"timezone": "Europe/Berlin"}
    out = assign_profile(ctx, "Solar", series, meta)
    assert out["time_basis"] == "UTC"
    assert out["warnings"] == []
    ts = pd.Timestamp("2025-07-15T12:00")  # unshifted: the snapshots are UTC
    assert float(n.generators_t.p_max_pu["Solar"].loc[ts]) == float(series.loc[ts])


def test_a_berlin_wall_clock_shifts_the_series_by_the_utc_offset():
    n, series, meta = _flat()
    out = assign_profile(_ctx(), "Solar", series, meta,
                         time_basis="Europe/Berlin")
    col = n.generators_t.p_max_pu["Solar"]
    # CET (+1 h) in January, CEST (+2 h) in July
    assert float(col.loc[pd.Timestamp("2025-01-15T13:00")]) == float(
        series.loc[pd.Timestamp("2025-01-15T12:00")])
    assert float(col.loc[pd.Timestamp("2025-07-15T14:00")]) == float(
        series.loc[pd.Timestamp("2025-07-15T12:00")])
    assert out["time_basis"] == "Europe/Berlin"
    assert out["warnings"] == []
    assert _user_ts_meta[KEY]["time_basis"] == "Europe/Berlin"


def test_local_new_year_midnight_wraps_to_the_weather_years_last_hour():
    n, series, meta = _flat()
    assign_profile(_ctx(), "Solar", series, meta, time_basis="Europe/Berlin")
    col = n.generators_t.p_max_pu["Solar"]
    assert float(col.loc[pd.Timestamp("2025-01-01T00:00")]) == float(
        series.loc[pd.Timestamp("2025-12-31T23:00")])


def test_dst_hours_take_the_mean_or_the_next_valid_hour():
    n, series, meta = _flat(year=2025)
    assign_profile(_ctx(), "Solar", series, meta, time_basis="Europe/Berlin")
    col = n.generators_t.p_max_pu["Solar"]
    # 2025-10-26 02:00 Berlin happens twice: 00:00 and 01:00 UTC → mean
    both = series.loc[pd.Timestamp("2025-10-26T00:00"):
                      pd.Timestamp("2025-10-26T01:00")].mean()
    assert float(col.loc[pd.Timestamp("2025-10-26T02:00")]) == pytest.approx(
        float(both))
    # 2025-03-30 02:00 Berlin never happens: the next valid hour, 03:00
    # CEST = 01:00 UTC
    assert float(col.loc[pd.Timestamp("2025-03-30T02:00")]) == float(
        series.loc[pd.Timestamp("2025-03-30T01:00")])


def test_an_unknown_time_basis_is_a_typed_refusal():
    _net(year=2025)
    series, meta = _weather(year=2025)
    with pytest.raises(InvalidTimeBasisError, match="Mars/Olympus"):
        assign_profile(_ctx(), "Solar", series, meta,
                       time_basis="Mars/Olympus")
    assert KEY not in _user_ts_meta


def test_a_template_week_takes_an_hourly_profile_and_refuses_a_daily_one():
    n = PyPSAService.get_network()
    idx = pd.date_range("2025-01-06T00:00", periods=168, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=13.4, y=52.5)
    n.add("Carrier", "solar")
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=100.0)
    series, meta = _weather(year=2025)
    assert assign_profile(_ctx(), "Solar", series, meta)["window"]["hours"] == 168
    daily, dmeta = _weather_res(year=2025, resolution="daily")
    with pytest.raises(UnsupportedSnapshotsError) as err:
        assign_profile(_ctx(), "Solar", daily, dmeta)
    msg = str(err.value)
    assert "daily or weekly" in msg
    assert "hourly profile, sliced to its snapshot window" in msg


# ── gatekeeper notes on #396 (2026-10-20) ───────────────────────────────────


def test_weekly_partial_first_week_holds_the_prior_mondays_mean():
    """2023 starts on a Sunday: Jan 1 belongs to the week labelled Monday
    2022-12-26, and Monday Jan 2 starts the next week."""
    _net(year=2023)
    weekly, meta = _weather_res(year=2023, resolution="weekly")
    assign_profile(_ctx(), "Solar", weekly, meta)
    stored = _user_ts[KEY]
    first = float(weekly.loc[pd.Timestamp("2022-12-26")])
    jan1 = stored.loc[pd.Timestamp("2023-01-01T00:00"):
                      pd.Timestamp("2023-01-01T23:00")]
    assert len(jan1) == 24 and (jan1 == first).all()
    assert float(stored.loc[pd.Timestamp("2023-01-02T00:00")]) == float(
        weekly.loc[pd.Timestamp("2023-01-02")])


def test_a_coarse_profile_records_that_the_time_basis_was_not_re_binned():
    _net(year=2024)
    daily, meta = _weather_res(year=2024, resolution="daily")
    assign_profile(_ctx(), "Solar", daily, meta, time_basis="Europe/Berlin")
    m = _user_ts_meta[KEY]
    assert m["time_basis"] == "Europe/Berlin"
    assert "not re-binned" in m["time_basis_note"]


def test_an_hourly_profile_carries_no_re_binning_note():
    _net(year=2025)
    series, meta = _weather(year=2025)
    assign_profile(_ctx(), "Solar", series, meta, time_basis="Europe/Berlin")
    assert "time_basis_note" not in _user_ts_meta[KEY]
