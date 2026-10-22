"""RP-03: the horizon follows the snapshots (spec R2).

* the default weather year is the snapshots' year when it is a completed past
  year, else the last completed year (`year_source`);
* regular multi-hour steps take the MEAN of the hourly weather series over
  each step (renewable and load families only), where they used to sample;
* partial-year windows stay sliced by month-day-hour.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from services.profile_gen import apply as ap
from services.profile_gen.horizon import default_weather_year

TODAY = dt.date(2026, 10, 22)


def _year_series(year: int = 2023) -> pd.Series:
    idx = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
    return pd.Series(np.arange(8760, dtype=float) % 97 / 97.0, index=idx)


def test_default_year_is_the_snapshots_year_when_completed():
    snaps = pd.date_range("2023-01-01", periods=8760, freq="h")
    assert default_weather_year(snaps, TODAY) == (2023, "snapshots")


def test_a_future_year_falls_back_to_the_last_completed_year():
    snaps = pd.date_range("2031-01-01", periods=8760, freq="h")
    assert default_weather_year(snaps, TODAY) == (2025, "default")
    assert default_weather_year(None, TODAY) == (2025, "default")


def test_a_multi_period_network_reads_the_timestep_level_not_the_period():
    def snaps(stamp_year):
        t = pd.date_range(f"{stamp_year}-01-01", periods=24, freq="h")
        return pd.MultiIndex.from_product([[2030, 2040], t])

    assert default_weather_year(snaps(2023), TODAY) == (2023, "snapshots")
    assert default_weather_year(snaps(2030), TODAY) == (2025, "default")


def test_three_hourly_snapshots_take_the_mean_of_their_three_hours():
    s = _year_series()
    snaps = pd.date_range("2023-01-01", periods=2920, freq="3h")
    aligned, meta = ap._align_to_snapshots(s, snaps, family="renewable")
    assert len(aligned) == 2920 and meta["step_hours"] == 3
    expected = s.to_numpy().reshape(2920, 3).mean(axis=1)
    np.testing.assert_allclose(aligned.to_numpy(), expected, rtol=1e-12)


def test_the_mean_conserves_energy_under_step_weightings():
    s = _year_series()
    snaps = pd.date_range("2023-01-01", periods=2920, freq="3h")
    aligned, _ = ap._align_to_snapshots(s, snaps, family="renewable")
    weighted = float((aligned.to_numpy() * 3.0).sum())
    assert weighted == pytest.approx(float(s.sum()), rel=1e-9)


def test_a_summer_window_still_slices_the_weather_year():
    s = _year_series()
    snaps = pd.date_range("2023-06-01", "2023-08-31 23:00", freq="h")
    aligned, meta = ap._align_to_snapshots(s, snaps, family="renewable")
    assert len(aligned) == 2208 and meta["window"] is not None
    np.testing.assert_allclose(aligned.to_numpy(), s.loc[snaps].to_numpy())


def test_non_leap_weather_onto_a_leap_target_is_still_refused():
    s = _year_series(2023)
    snaps = pd.date_range("2024-01-01", periods=8784, freq="h")
    with pytest.raises(ap.LeapYearMismatchError):
        ap._align_to_snapshots(s, snaps, family="renewable")


def test_irregular_steps_are_refused_by_name():
    s = _year_series()
    snaps = pd.DatetimeIndex(["2023-01-01 00:00", "2023-01-01 03:00", "2023-01-01 09:00"])
    with pytest.raises(ap.StepIrregularError):
        ap._align_to_snapshots(s, snaps, family="renewable")


def test_steps_that_are_not_whole_hours_are_refused_by_name():
    s = _year_series()
    snaps = pd.date_range("2023-01-01", periods=10, freq="90min")
    with pytest.raises(ap.StepNotWholeHoursError):
        ap._align_to_snapshots(s, snaps, family="renewable")


def test_a_family_without_a_step_mean_is_refused_by_name():
    s = _year_series()
    snaps = pd.date_range("2023-01-01", periods=2920, freq="3h")
    with pytest.raises(ap.StepAggregationUnsupportedError):
        ap._align_to_snapshots(s, snaps, family="availability")


def test_a_step_across_the_spring_forward_hour_of_a_local_clock_is_refused():
    s = _year_series()
    snaps = pd.date_range("2023-03-26 00:00", periods=8, freq="3h")  # 02:00 is skipped in Berlin
    with pytest.raises(ap.StepOverDstGapError):
        ap._align_to_snapshots(s, snaps, family="renewable", time_basis="Europe/Berlin")


# ── the route: year and year_source ────────────────────────────────────────


def _project(client, install_network, stamp_year, steps="h", periods=8760):
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range(f"{stamp_year}-01-01", periods=periods, freq=steps, name="snapshot"))
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", "wind")
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=10.0)
    install_network(n)
    assert client.post("/api/projects/Horizon Farm",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Horizon Farm"


def _generate(client, project, **extra):
    import time
    import uuid

    from services import solve_queue as sq

    resp = client.post(f"/api/projects/{project}/profiles/generate", json={
        "points": [{"id": "g_wind", "lon": 8.0, "lat": 50.0}], "technology": "wind", **extra})
    assert resp.status_code in (200, 202), resp.text
    jid = resp.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(jid))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job.get("error")
    pending = client.get(f"/api/projects/{project}/profiles/pending").json()["pending"]
    return next(p for p in pending if p["job_id"] == jid)


def test_generate_without_a_year_uses_the_snapshots_year(client, install_network):
    project = _project(client, install_network, 2023)
    rec = _generate(client, project)
    assert (rec["weather_year"], rec["year_source"]) == (2023, "snapshots")


def test_generate_with_future_snapshots_uses_the_last_completed_year(client, install_network):
    project = _project(client, install_network, 2041)
    rec = _generate(client, project)
    assert rec["year_source"] == "default"
    assert rec["weather_year"] == dt.datetime.now(dt.timezone.utc).year - 1


def test_an_explicit_year_wins(client, install_network):
    project = _project(client, install_network, 2023)
    rec = _generate(client, project, year=2019)
    assert (rec["weather_year"], rec["year_source"]) == (2019, "explicit")


def test_applying_onto_three_hourly_snapshots_writes_the_step_means(client, install_network, session_ctx):
    project = _project(client, install_network, 2023, steps="3h", periods=2920)
    rec = _generate(client, project, year=2023)
    resp = client.post(f"/api/projects/{project}/profiles/apply", json={
        "job_id": rec["job_id"], "component": "g_wind", "time_basis": "UTC"})
    assert resp.status_code == 200, resp.text
    stored = session_ctx(client).user_ts[("generators", "p_max_pu", "g_wind")]
    assert len(stored) == 2920
    assert resp.json()["hours"] == 2920
