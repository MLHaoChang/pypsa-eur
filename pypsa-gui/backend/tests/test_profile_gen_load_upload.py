"""A measured load upload, read into the load family (ticket 01, slice B3).

`load_upload.read_load_upload` turns a CSV/xlsx meter export into hourly MW
columns over one calendar year on UTC hours, with ``provenance: measured``,
then the series goes through `assign_profile` like a generated one. It is
strict on what it cannot know (the unit, a coarse resolution, a long hole)
and says what it did with what it could repair (sub-hourly means, short
gaps, a timezone, a rescale).
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from services.profile_gen.apply import assign_profile
from services.profile_gen.load_upload import (
    UploadCoverageError,
    UploadFormatError,
    UploadUnitError,
    read_load_upload,
)
from services.pypsa_service import PyPSAService

NOW = "2026-10-20T00:00:00+00:00"


def _year(year: int = 2023, freq: str = "h") -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq=freq,
                         inclusive="left")


def _csv(frame: pd.DataFrame) -> bytes:
    frame = frame.copy()
    frame.index.name = "timestamp"
    return frame.to_csv().encode()


def _ramp(idx) -> np.ndarray:
    return 1.0 + (np.arange(len(idx)) % 24) / 10.0


def test_an_hourly_mw_year_reads_as_is_with_measured_provenance():
    idx = _year()
    blob = _csv(pd.DataFrame({"office": _ramp(idx)}, index=idx))
    up = read_load_upload(blob, "meter.csv", unit="MW")
    assert list(up.frame.columns) == ["office"]
    assert len(up.frame) == 8760
    assert np.allclose(up.frame["office"].to_numpy(), _ramp(idx))
    m = up.meta
    assert m["family"] == "load"
    assert m["source"] == "upload"
    assert m["provenance"] == "measured"
    assert m["unit"] == "MW"
    assert m["weather_year"] == 2023
    assert m["upload"]["filename"] == "meter.csv"
    assert len(m["upload"]["sha256"]) == 64


def test_quarter_hourly_kw_becomes_hourly_mw_means():
    idx = _year(freq="15min")
    vals = np.tile([1000.0, 2000.0, 3000.0, 4000.0], len(idx) // 4)
    up = read_load_upload(_csv(pd.DataFrame({"plant": vals}, index=idx)),
                          "m.csv", unit="kW")
    assert len(up.frame) == 8760
    assert up.frame["plant"].iloc[0] == pytest.approx(2.5)
    assert up.meta["upload"]["resolution_in"] == "15min"
    assert "resampled_to_hourly" in up.notes


def test_energy_per_step_is_turned_into_power():
    idx = _year(freq="15min")
    up = read_load_upload(_csv(pd.DataFrame({"plant": 250.0}, index=idx)),
                          "m.csv", unit="kWh")
    # 250 kWh per 15 min is 1 MW.
    assert float(up.frame["plant"].mean()) == pytest.approx(1.0)


def test_the_unit_can_come_from_the_column_header():
    idx = _year()
    up = read_load_upload(_csv(pd.DataFrame({"office [kW]": 500.0}, index=idx)),
                          "m.csv", unit=None)
    assert list(up.frame.columns) == ["office"]
    assert float(up.frame["office"].iloc[0]) == pytest.approx(0.5)


def test_no_unit_anywhere_is_refused():
    idx = _year()
    with pytest.raises(UploadUnitError, match="unit"):
        read_load_upload(_csv(pd.DataFrame({"office": 1.0}, index=idx)),
                         "m.csv", unit=None)


def test_a_header_unit_disagreeing_with_the_stated_one_is_refused():
    idx = _year()
    with pytest.raises(UploadUnitError, match="kW"):
        read_load_upload(_csv(pd.DataFrame({"office (kW)": 1.0}, index=idx)),
                         "m.csv", unit="MW")


def test_an_unknown_unit_is_refused():
    idx = _year()
    with pytest.raises(UploadUnitError):
        read_load_upload(_csv(pd.DataFrame({"office": 1.0}, index=idx)),
                         "m.csv", unit="horsepower")


def test_offset_timestamps_land_on_utc_hours():
    idx = _year().tz_localize("UTC").tz_convert("Europe/Berlin")
    frame = pd.DataFrame({"office": _ramp(idx)}, index=idx)
    up = read_load_upload(_csv(frame), "m.csv", unit="MW")
    assert up.frame.index[0] == pd.Timestamp("2023-01-01T00:00")
    assert np.allclose(up.frame["office"].to_numpy(), _ramp(idx))
    assert up.meta["source_time_basis"] == "UTC (from the file's offsets)"


def test_a_stated_timezone_reads_naive_wall_clock_through_dst():
    # A Berlin meter export on its wall clock: 23 h on the spring Sunday and
    # 25 h on the autumn one (the repeated hour appears twice, in order).
    utc = pd.date_range("2022-12-31T23:00", "2024-01-01T00:00", freq="h", tz="UTC")
    local = utc.tz_convert("Europe/Berlin").tz_localize(None)
    frame = pd.DataFrame({"office": np.arange(len(utc), dtype=float) + 1.0},
                         index=local)
    up = read_load_upload(_csv(frame), "m.csv", unit="MW", timezone="Europe/Berlin")
    assert len(up.frame) == 8760
    # Local midnight on 1 Jan is 23:00 UTC on 31 Dec: outside the UTC year.
    assert float(up.frame["office"].iloc[0]) == pytest.approx(2.0)
    assert up.meta["source_time_basis"] == "UTC (read from Europe/Berlin wall clock)"


def test_naive_timestamps_without_a_timezone_are_read_as_utc_and_noted():
    idx = _year()
    up = read_load_upload(_csv(pd.DataFrame({"office": 1.0}, index=idx)),
                          "m.csv", unit="MW")
    assert up.meta["source_time_basis"] == "UTC (assumed; the file states no zone)"
    assert "time_basis_assumed_utc" in up.notes


def test_a_short_gap_is_interpolated():
    idx = _year()
    vals = pd.Series(_ramp(idx), index=idx)
    holed = vals.drop(idx[100:103])
    up = read_load_upload(_csv(holed.to_frame("office")), "m.csv", unit="MW")
    assert len(up.frame) == 8760
    assert up.meta["upload"]["gaps_filled"]["office"] == {"interpolated_h": 3,
                                                          "copied_week_h": 0}


def test_a_day_long_gap_is_copied_from_the_week_before():
    idx = _year()
    vals = pd.Series(_ramp(idx), index=idx)
    vals.iloc[1000:1024] = np.nan
    up = read_load_upload(_csv(vals.to_frame("office")), "m.csv", unit="MW")
    s = up.frame["office"]
    assert float(s.iloc[1000]) == pytest.approx(float(s.iloc[1000 - 168]))
    assert up.meta["upload"]["gaps_filled"]["office"]["copied_week_h"] == 24


def test_a_gap_longer_than_a_week_is_refused():
    idx = _year()
    vals = pd.Series(_ramp(idx), index=idx)
    vals.iloc[2000:2200] = np.nan
    with pytest.raises(UploadCoverageError, match="office"):
        read_load_upload(_csv(vals.to_frame("office")), "m.csv", unit="MW")


def test_a_daily_series_is_refused_as_too_coarse():
    idx = _year(freq="D")
    with pytest.raises(UploadFormatError, match="hourly"):
        read_load_upload(_csv(pd.DataFrame({"office": 24.0}, index=idx)),
                         "m.csv", unit="MW")


def test_two_full_years_need_the_year_named():
    idx = pd.date_range("2022-01-01", "2024-01-01", freq="h", inclusive="left")
    blob = _csv(pd.DataFrame({"office": 1.0}, index=idx))
    with pytest.raises(UploadCoverageError, match="2022, 2023"):
        read_load_upload(blob, "m.csv", unit="MW")
    up = read_load_upload(blob, "m.csv", unit="MW", year=2023)
    assert up.meta["weather_year"] == 2023


def test_a_partial_year_is_refused():
    idx = pd.date_range("2023-01-01", "2023-07-01", freq="h", inclusive="left")
    with pytest.raises(UploadCoverageError):
        read_load_upload(_csv(pd.DataFrame({"office": 1.0}, index=idx)),
                         "m.csv", unit="MW")


def test_scaling_to_an_annual_energy_is_recorded():
    idx = _year()
    up = read_load_upload(_csv(pd.DataFrame({"office": _ramp(idx)}, index=idx)),
                          "m.csv", unit="MW", annual_mwh=1000.0)
    assert float(up.frame["office"].sum()) == pytest.approx(1000.0)
    assert up.meta["upload"]["rescaled_from_mwh"]["office"] == pytest.approx(
        float(_ramp(idx).sum()))


def test_a_negative_reading_is_refused():
    idx = _year()
    vals = _ramp(idx)
    vals[5] = -1.0
    with pytest.raises(UploadFormatError, match="negative"):
        read_load_upload(_csv(pd.DataFrame({"office": vals}, index=idx)),
                         "m.csv", unit="MW")


def test_a_non_numeric_column_is_refused():
    idx = _year()
    frame = pd.DataFrame({"office": ["x"] * len(idx)}, index=idx)
    with pytest.raises(UploadFormatError, match="office"):
        read_load_upload(_csv(frame), "m.csv", unit="MW")


def test_qa_findings_ride_along():
    idx = _year()
    up = read_load_upload(_csv(pd.DataFrame({"office": 0.0}, index=idx)),
                          "m.csv", unit="MW")
    assert up.warnings and up.warnings[0]["column"] == "office"


def test_an_xlsx_export_reads_like_a_csv():
    idx = _year()
    buf = io.BytesIO()
    pd.DataFrame({"office": _ramp(idx)}, index=idx.rename("timestamp")).to_excel(buf)
    up = read_load_upload(buf.getvalue(), "meter.xlsx", unit="MW")
    assert np.allclose(up.frame["office"].to_numpy(), _ramp(idx))


def test_a_measured_series_applies_through_the_one_path():
    n = PyPSAService.get_network()
    idx = _year()
    snaps = idx.copy()
    snaps.name = "snapshot"
    n.set_snapshots(snaps)
    n.add("Bus", "B1")
    n.add("Load", "office", bus="B1")
    up = read_load_upload(_csv(pd.DataFrame({"office": _ramp(idx)}, index=idx)),
                          "m.csv", unit="MW")
    out = assign_profile(PyPSAService.get_active_context(), "office",
                         up.frame["office"], up.meta,
                         component_type="loads", attribute="p_set", now=NOW)
    assert out["profile_annual_mwh"] == pytest.approx(float(_ramp(idx).sum()))
    assert np.allclose(n.loads_t.p_set["office"].to_numpy(), _ramp(idx))
