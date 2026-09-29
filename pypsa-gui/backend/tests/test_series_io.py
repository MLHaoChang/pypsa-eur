"""
Series and meter-data import (Edge Investment Case P2 WP2.4b-ii).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.4b-ii.
CSV (`timestamp,value`) and xlsx (first sheet) uploads follow the WP1.1b
timestamp and zone rules (`series_io.series_from`). Meter data (kW) becomes
monthly peaks measured on a stated settlement (default 15 min, recorded in the
stored series' meta) → `meter_history_peaks_kw`, and monthly energy →
`meter_history_energy_kwh`. Only complete months are history; an incomplete
month, or peaks a coarser meter cannot resolve, are named, never guessed.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from services.library import series_io as SIO


def _csv(idx, values, header="timestamp,value") -> bytes:
    lines = [header] + [f"{t.isoformat()},{v}" for t, v in zip(idx, values)]
    return ("\n".join(lines) + "\n").encode()


def _xlsx(idx, values) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["timestamp", "value"])
    for t, v in zip(idx, values):
        ws.append([t.to_pydatetime(), float(v)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── parsing ────────────────────────────────────────────────────────────────


def test_csv_with_offsets_round_trips_on_a_dst_day():
    """The spring-forward day in Berlin has 92 quarter hours, the fall-back
    day 100; offsets keep every instant."""
    for day, rows in (("2030-03-31", 92), ("2030-10-27", 100)):
        idx = pd.date_range(day, f"{pd.Timestamp(day) + pd.Timedelta(days=1):%Y-%m-%d}",
                            freq="15min", tz="Europe/Berlin", inclusive="left")
        assert len(idx) == rows
        s = SIO.parse_upload(_csv(idx, np.arange(rows)), "x.csv", timezone="Europe/Berlin")
        assert len(s) == rows and s.index.equals(idx)
        assert s.to_numpy().tolist() == list(range(rows))


def test_xlsx_first_sheet_with_naive_datetimes():
    idx = pd.date_range("2030-01-01", periods=48, freq="h")
    s = SIO.parse_upload(_xlsx(idx, np.linspace(1, 2, 48)), "x.xlsx")
    assert s.index.tz is None and s.index.equals(idx)
    assert s.iloc[-1] == pytest.approx(2.0)


@pytest.mark.parametrize("data,match", [
    (b"time,val\n2030-01-01T00:00,1\n", "timestamp,value"),
    (b"timestamp,value\n2030-01-01T00:00,abc\n", "value"),
    (b"timestamp,value\n", "no rows"),
])
def test_malformed_uploads_are_refused_with_a_reason(data, match):
    with pytest.raises(SIO.SeriesInputError, match=match):
        SIO.parse_upload(data, "x.csv")


def test_an_unknown_file_type_is_refused():
    with pytest.raises(SIO.SeriesInputError, match="csv or xlsx"):
        SIO.parse_upload(b"...", "x.json")


# ── meter data ─────────────────────────────────────────────────────────────


def _month(freq="5min", start="2030-01-01", tz=None, base=300.0):
    end = pd.Timestamp(start) + pd.offsets.MonthBegin(1)
    idx = pd.date_range(start, end, freq=freq, tz=tz, inclusive="left")
    return pd.Series(base, index=idx)


def test_five_minute_meter_data_is_averaged_to_the_stated_settlement():
    s = _month()
    s.iloc[100] = 900.0                         # one 5-min spike in a 15-min interval
    h = SIO.meter_history(s, settlement="15min")
    assert h["meter_history_peaks_kw"] == {"2030-01": pytest.approx((900 + 300 + 300) / 3)}
    hourly = SIO.meter_history(s, settlement="h")
    assert hourly["meter_history_peaks_kw"]["2030-01"] == pytest.approx((900 + 11 * 300) / 12)
    energy = 300.0 * 31 * 24 + (900 - 300) * 5 / 60
    assert h["meter_history_energy_kwh"] == {"2030-01": pytest.approx(energy)}
    assert h["settlement"] == "15min" and h["notes"] == []


def test_incomplete_months_are_named_not_used():
    s = pd.concat([_month(), _month(start="2030-02-01").iloc[:-12]])     # Feb misses an hour
    h = SIO.meter_history(s)
    assert set(h["meter_history_peaks_kw"]) == {"2030-01"}
    assert set(h["meter_history_energy_kwh"]) == {"2030-01"}
    assert "month_incomplete:2030-02" in h["notes"]


def test_a_meter_coarser_than_the_settlement_gives_energy_but_no_peaks():
    h = SIO.meter_history(_month(freq="h"), settlement="15min")
    assert h["meter_history_peaks_kw"] == {}
    assert h["meter_history_energy_kwh"] == {"2030-01": pytest.approx(300.0 * 744)}
    assert "peaks_not_established:meter_step_1h_coarser_than_settlement_15min" in h["notes"]


def test_months_are_on_the_site_clock_and_dst_months_have_their_real_hours():
    """UTC meter rows, a Berlin site: the local month; March has 743 hours."""
    s = _month(freq="15min", start="2030-03-01", tz="Europe/Berlin")
    utc = pd.Series(s.to_numpy(), index=s.index.tz_convert("UTC"))
    h = SIO.meter_history(utc, timezone="Europe/Berlin")
    assert h["meter_history_energy_kwh"] == {"2030-03": pytest.approx(300.0 * 743)}
    assert h["meter_history_peaks_kw"] == {"2030-03": pytest.approx(300.0)}


def test_export_rows_are_not_import():
    s = _month()
    s.iloc[:12] = -50.0                        # an hour of export
    h = SIO.meter_history(s)
    assert h["meter_history_energy_kwh"]["2030-01"] == pytest.approx(300.0 * (744 - 1))
    assert "negative_rows_read_as_export:12" in h["notes"]


def test_the_help_states_the_metered_year_rule():
    assert "metered" in SIO.METER_HISTORY_HELP and "cyclic_year" in SIO.METER_HISTORY_HELP


# ── routes ─────────────────────────────────────────────────────────────────


def test_the_series_upload_route_stores_a_csv(client):
    idx = pd.date_range("2030-01-01", periods=8, freq="15min", tz="Europe/Berlin")
    r = client.post("/api/library/series/upload",
                    files={"file": ("px.csv", _csv(idx, range(8)), "text/csv")},
                    data={"name": "px", "timezone": "Europe/Berlin"})
    assert r.status_code == 200, r.text
    got = client.get("/api/library/series/px").json()
    assert got["values"] == [float(v) for v in range(8)]
    assert got["timezone"] == "Europe/Berlin"
    bad = client.post("/api/library/series/upload",
                      files={"file": ("px.csv", b"nope\n", "text/csv")}, data={"name": "q"})
    assert bad.status_code == 422


def test_the_meter_route_returns_the_history_and_records_the_settlement(client):
    s = _month()
    s.iloc[100] = 900.0
    body = _csv(s.index, s.to_numpy())
    r = client.post("/api/library/meter_data",
                    files={"file": ("meter.csv", body, "text/csv")},
                    data={"name": "site-meter-2030", "settlement": "15min", "unit": "kW"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["meter_history_peaks_kw"] == {"2030-01": pytest.approx(500.0)}
    assert out["settlement"] == "15min" and "metered" in out["help"]
    assert out["ref"]["id"] == "site-meter-2030"
    bad = client.post("/api/library/meter_data",
                      files={"file": ("meter.csv", body, "text/csv")},
                      data={"name": "m2", "settlement": "5min", "unit": "kW"})
    assert bad.status_code == 422
    # The history drops straight into the commercial config.
    from models.commercial import CommercialConfig

    CommercialConfig.model_validate({"poc_link": "import", **{
        k: out[k] for k in ("meter_history_peaks_kw", "meter_history_energy_kwh")}})


# ── review round 1 ─────────────────────────────────────────────────────────


def _post_meter(client, body, name="m", **data):
    return client.post("/api/library/meter_data",
                       files={"file": ("meter.csv", body, "text/csv")},
                       data={"name": name, "unit": "kW", **data})


def test_offset_timestamps_need_the_site_timezone_and_then_land_in_site_months(client):
    """F1: a Los Angeles spike at Jan 31 20:00 local is 04:00 UTC on Feb 1."""
    idx = pd.date_range("2030-01-01", "2030-03-01", freq="15min", tz="America/Los_Angeles",
                        inclusive="left")
    v = np.full(len(idx), 100.0)
    v[idx.get_loc(pd.Timestamp("2030-01-31 20:00", tz="America/Los_Angeles"))] = 999.0
    body = _csv(idx, v)
    assert _post_meter(client, body).status_code == 422
    r = _post_meter(client, body, timezone="America/Los_Angeles")
    assert r.status_code == 200, r.text
    assert r.json()["meter_history_peaks_kw"] == {"2030-01": 999.0, "2030-02": 100.0}


@pytest.mark.parametrize("body", [
    b'timestamp,value\n"2030' + b"x" * 200_000 + b"\n",                  # csv.Error
    b"timestamp,value\n2030-01-01T00:00,1\n,2\n",                         # empty timestamp
    b"timestamp,value\n01/02/2030 00:00,1\n01/02/2030 00:15,1\n",         # not ISO
])
def test_malformed_meter_files_are_422_never_500(client, body):
    """F2 / F14."""
    r = _post_meter(client, body)
    assert r.status_code == 422, r.text


def test_a_dst_change_at_midnight_on_the_first_is_handled():
    """F2: Asunción's DST began 2023-10-01 00:00."""
    idx = pd.date_range("2023-09-01", "2023-11-01", freq="15min", tz="America/Asuncion",
                        inclusive="left")
    h = SIO.meter_history(pd.Series(100.0, index=idx))
    assert set(h["meter_history_energy_kwh"]) == {"2023-09", "2023-10"}
    assert h["meter_history_energy_kwh"]["2023-10"] == pytest.approx(100.0 * (31 * 24 - 1))


def test_a_blank_meter_value_names_its_month_and_a_blank_series_value_is_refused(client):
    """F3."""
    s = _month(freq="15min")
    rows = [f"{t.isoformat()},{'' if i == 50 else 300.0}" for i, t in enumerate(s.index)]
    body = ("timestamp,value\n" + "\n".join(rows) + "\n").encode()
    r = _post_meter(client, body)
    assert r.status_code == 200, r.text
    assert "month_incomplete:2030-01" in r.json()["notes"]
    assert "empty_rows_not_stored:1" in r.json()["notes"]
    bad = client.post("/api/library/series/upload",
                      files={"file": ("s.csv", body, "text/csv")}, data={"name": "s"})
    assert bad.status_code == 422 and "row 52" in bad.text


def test_units_and_end_labels_are_converted():
    """F4 / F5: 25 kWh per quarter hour is 100 kW; end labels move back a step."""
    idx = pd.date_range("2030-01-01 00:15", "2030-02-01 00:00", freq="15min")   # end-labelled
    s, _ = SIO.meter_series(pd.Series(25.0, index=idx), timezone=None,
                            unit="kWh_per_interval", label="end")
    assert s.index[0] == pd.Timestamp("2030-01-01 00:00")
    h = SIO.meter_history(s)
    assert h["meter_history_peaks_kw"] == {"2030-01": pytest.approx(100.0)}
    assert h["meter_history_energy_kwh"] == {"2030-01": pytest.approx(100.0 * 744)}
    w, _ = SIO.meter_series(pd.Series(2000.0, index=idx), timezone=None, unit="W")
    assert float(w.iloc[0]) == pytest.approx(2.0)


def test_plain_local_times_are_read_on_the_site_clock():
    """F6: Berlin wall-clock data — March has 743 real hours; October's
    repeated hour is resolved by row order."""
    mar = pd.date_range("2030-03-01", "2030-04-01", freq="15min", tz="Europe/Berlin",
                        inclusive="left")
    oct_ = pd.date_range("2030-10-01", "2030-11-01", freq="15min", tz="Europe/Berlin",
                         inclusive="left")
    for idx, m, hours in ((mar, "2030-03", 743), (oct_, "2030-10", 745)):
        naive = pd.Series(100.0, index=idx.tz_localize(None))
        s, notes = SIO.meter_series(naive, timezone="Europe/Berlin")
        assert "local_times_read_on_the_site_clock" in notes
        h = SIO.meter_history(s)
        assert h["meter_history_energy_kwh"] == {m: pytest.approx(100.0 * hours)}


def test_identical_data_with_another_settlement_is_a_conflict_and_meta_is_readable(client):
    """F8."""
    body = _csv(_month().index, _month().to_numpy())
    assert _post_meter(client, body, name="mm").status_code == 200
    r = _post_meter(client, body, name="mm", settlement="h")
    assert r.status_code == 409 and "meter_meta_conflict" in r.text
    meta = client.get("/api/library/series/mm").json()["meta"]
    assert (meta["settlement"], meta["source_unit"], meta["label"]) == ("15min", "kW", "start")


def test_rows_that_do_not_fit_the_settlement_intervals_give_no_peaks():
    """F10: a 10-min meter against 15-min intervals."""
    h = SIO.meter_history(_month(freq="10min"), settlement="15min")
    assert h["meter_history_peaks_kw"] == {}
    assert "peaks_not_established:meter_rows_cross_15min_intervals" in h["notes"]
    assert h["meter_history_energy_kwh"] == {"2030-01": pytest.approx(300.0 * 744)}


def test_mixed_steps_jitter_missing_months_and_negative_files_are_handled():
    """F11 / F12 / F15."""
    mixed = pd.concat([_month(freq="15min"), _month(freq="5min", start="2030-02-01")])
    h = SIO.meter_history(mixed)
    assert set(h["meter_history_peaks_kw"]) == {"2030-01", "2030-02"}
    jitter = _month(freq="15min")
    rng = np.random.default_rng(0)
    jitter.index = jitter.index + pd.to_timedelta(rng.integers(-5, 6, len(jitter)), unit="s")
    jitter.index = jitter.index.where(jitter.index >= pd.Timestamp("2030-01-01"),
                                      pd.Timestamp("2030-01-01"))
    assert set(SIO.meter_history(jitter)["meter_history_energy_kwh"]) == {"2030-01"}
    gap = pd.concat([_month(), _month(start="2030-03-01")])
    assert "month_missing:2030-02" in SIO.meter_history(gap)["notes"]
    neg = SIO.meter_history(-_month())
    assert "mostly_negative_rows_check_the_sign_convention" in neg["notes"]
    assert "month_without_import:2030-01" in neg["notes"]
