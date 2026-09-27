"""
Snapshot weightings follow the snapshot frequency (Edge Investment Case P1 WP1.0).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.0
Spec decision 18: 15-minute settlement requires weightings from frequency.

`set_snapshots` used to leave `snapshot_weightings` at PyPSA's default 1.0
whatever the frequency, so a 15-minute axis counted every quarter-hour as a
full hour: energy KPIs, annualised capex (via n.nyears) and every weighted sum
came out 4× too large. Weights are in HOURS; this pins that.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from tests.conftest import build_network
from tests.fixtures.investment_case.edge_15min import (
    DAYS,
    STEPS_PER_DAY,
    build_edge_15min,
    solved_edge_15min,
)


def _set(client, freq, **extra):
    body = {"start": "2030-01-01 00:00", "end": "2030-01-01 23:59", "freq": freq, **extra}
    return client.post("/api/network/snapshots", json=body)


@pytest.mark.parametrize("freq,hours,count", [
    ("15min", 0.25, 96),
    ("30min", 0.5, 48),
    ("h", 1.0, 24),
    ("3h", 3.0, 8),
])
def test_weightings_are_the_step_length_in_hours(client, install_network, freq, hours, count):
    install_network(build_network())
    r = _set(client, freq)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == count
    sw = client.get("/api/network/snapshots").json()["weightings"]
    df = pd.DataFrame(sw)
    for col in ("objective", "generators", "stores"):
        assert np.allclose(df[col].astype(float), hours), (freq, col)


def test_an_explicit_weighting_still_wins(client, install_network):
    install_network(build_network())
    assert _set(client, "15min", weightings=2.0).status_code == 200
    df = pd.DataFrame(client.get("/api/network/snapshots").json()["weightings"])
    assert np.allclose(df["objective"].astype(float), 2.0)


def test_multi_period_axis_gets_frequency_weights(client, install_network):
    install_network(build_network())
    r = client.post("/api/network/snapshots/multi_period", json={
        "periods": [2030, 2035], "start": "2030-01-01 00:00",
        "end": "2030-01-01 23:45", "freq": "15min"})
    assert r.status_code == 200, r.text
    df = pd.DataFrame(client.get("/api/network/snapshots").json()["weightings"])
    assert len(df) == 2 * 96
    assert np.allclose(df["objective"].astype(float), 0.25)


def test_switching_resolution_does_not_carry_the_old_weights(client, install_network):
    """An hourly axis's 1.0 weights re-broadcast onto a 15-minute axis would
    put 1.0 on every :00 quarter-hour — 4× too much there, 1× elsewhere."""
    install_network(build_network())
    assert client.post("/api/network/snapshots/multi_period", json={
        "periods": [2030], "start": "2030-01-01 00:00", "end": "2030-01-01 23:00",
        "freq": "h"}).status_code == 200
    assert client.post("/api/network/snapshots/multi_period", json={
        "periods": [2030], "start": "2030-01-01 00:00", "end": "2030-01-01 23:45",
        "freq": "15min"}).status_code == 200
    df = pd.DataFrame(client.get("/api/network/snapshots").json()["weightings"])
    assert np.allclose(df["objective"].astype(float), 0.25)


def test_sample_weeks_is_refused_on_a_sub_hourly_axis(client, install_network):
    install_network(build_edge_15min())
    body = client.get("/api/network/snapshots").json()
    assert body["freq"] == "15min"
    assert body["can_sample_weeks"] is False
    assert body.get("sample_weeks_reason") == "not_supported_for_freq"
    r = client.post("/api/network/snapshots/sample_weeks", json={"n_weeks": 1})
    assert r.status_code == 400
    assert "not_supported_for_freq" in r.text


def test_template_on_a_15min_axis_has_96_points_per_day(client, install_network):
    install_network(build_edge_15min())
    r = client.get("/api/network/loads/template", params={"use_snapshots": True})
    assert r.status_code == 200, r.text
    xl = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
    sheet = next(iter(xl.values()))
    assert len(sheet) == STEPS_PER_DAY * DAYS


def test_fixture_horizon_is_seven_days_in_years():
    from services.adequacy.metrics import horizon_years

    n = build_edge_15min()
    assert horizon_years(n) == pytest.approx(7 / 365, abs=1e-9)


@pytest.mark.live_solve
def test_fixture_solves_fast_and_carrier_energy_is_power_times_quarter_hours():
    import time

    from services.results.carrier_kpis import compute_carrier_kpis

    t0 = time.time()
    n = solved_edge_15min()
    assert time.time() - t0 < 20.0

    def plain(net, accessor, attr, source="lopf"):
        return getattr(getattr(net, accessor), attr, None)

    rows = compute_carrier_kpis(n, result_df=plain)["rows"]
    solar = next(r for r in rows if r.get("carrier") == "solar")
    assert solar["energy_mwh"] == pytest.approx(float(n.generators_t.p["pv"].sum() * 0.25), rel=1e-9)
