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


# ── WP1.0 review round 1 (FAIL) — regressions ────────────────────────────────


def _weights(client):
    return pd.DataFrame(client.get("/api/network/snapshots").json()["weightings"])


def test_resampling_the_same_day_leaves_no_old_weight_behind(client, install_network):
    """#1: PyPSA only fills NEW rows; overlapping :00 rows kept 1.0."""
    install_network(build_network())
    assert _set(client, "h").status_code == 200
    assert _set(client, "15min").status_code == 200
    w = _weights(client)
    assert sorted(w["objective"].astype(float).unique()) == [0.25]
    assert _set(client, "3h").status_code == 200
    assert _set(client, "h").status_code == 200
    assert sorted(_weights(client)["objective"].astype(float).unique()) == [1.0]


def test_multi_period_extension_pads_with_the_step_not_one(client, install_network):
    """#2: a flat 15-min day extended to a two-day 15-min multi-period axis."""
    install_network(build_network())
    assert _set(client, "15min").status_code == 200
    r = client.post("/api/network/snapshots/multi_period", json={
        "periods": [2030, 2035], "start": "2030-01-01 00:00",
        "end": "2030-01-02 23:45", "freq": "15min"})
    assert r.status_code == 200, r.text
    w = _weights(client)
    assert len(w) == 2 * 192
    assert sorted(w["objective"].astype(float).unique()) == [0.25]


def test_an_hourly_upload_is_held_across_a_15min_axis(client, install_network, session_ctx):
    """#4: hourly p_set on a 15-min axis must not be 3/4 NaN."""
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01 00:00", periods=24, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1",
          p_set=pd.Series(np.arange(24, dtype=float) + 100.0, index=n.snapshots))
    n.add("Generator", "gas", bus="B1", p_nom=500.0, marginal_cost=50.0)
    install_network(n)
    assert _set(client, "15min").status_code == 200
    ps = session_ctx(client).network.loads_t.p_set["L1"]
    assert len(ps) == 96 and not ps.isna().any()
    assert (ps.iloc[0:4] == 100.0).all() and (ps.iloc[4:8] == 101.0).all()


def test_a_gappy_15min_axis_is_still_refused_by_sample_weeks(client, install_network):
    """#5: infer_freq gives up on gaps; the modal step must still read 15min."""
    n = build_edge_15min()
    keep = np.ones(len(n.snapshots), dtype=bool)
    keep[[10, 50, 90, 200, 300, 400, 500, 510, 520, 530]] = False
    n.set_snapshots(n.snapshots[keep])
    install_network(n)
    body = client.get("/api/network/snapshots").json()
    assert body["freq"] == "15min"
    assert body["can_sample_weeks"] is False
    assert body["sample_weeks_reason"] == "not_supported_for_freq"


def test_sample_weeks_refusal_detail_is_readable_by_the_frontend(client, install_network):
    """#6: the frontend's formatApiDetail reads `message` from an object."""
    install_network(build_edge_15min())
    r = client.post("/api/network/snapshots/sample_weeks", json={"n_weeks": 1})
    detail = r.json()["detail"]
    assert detail["code"] == "not_supported_for_freq"
    assert isinstance(detail["message"], str) and "15min" in detail["message"]


def test_day_is_24_hours_whatever_pandas_calls_it():
    """#10: pandas 3 makes Day a calendar offset, not a Tick."""
    from services.snapshot_index import hours_per_step

    assert hours_per_step("D") == 24.0
    assert hours_per_step("2D") == 48.0
    assert hours_per_step("MS") is None


def test_limited_foresight_aggregation_counts_steps_not_hours_and_keeps_hours():
    """#8: tsam periods of `lf_period_length_h` HOURS on a 15-min axis; weights
    rescale to the period's original HOURS, and the no-aggregation fallback
    returns the original weights, not 1.0."""
    import types

    from services import time_aggregation_service as T
    from services.snapshot_index import _build_period_multiindex

    T.clear_cache()
    n = __import__("pypsa").Network()
    ts = pd.date_range("2030-01-01", periods=4 * 7 * 96, freq="15min")  # 4 weeks
    mi = _build_period_multiindex([2030, 2035], [ts, ts])
    n.set_snapshots(mi)
    n.investment_periods = [2030, 2035]
    n.snapshot_weightings.loc[:, :] = 0.25
    n.add("Bus", "b")
    rng = np.random.default_rng(3)
    n.add("Load", "l", bus="b", p_set=pd.Series(rng.uniform(10, 50, len(mi)), index=mi))
    cfg = types.SimpleNamespace(lf_k_periods=2, lf_period_length_h=168,
                                lf_cluster_method="hierarchical", lf_include_extreme=False)
    res = T.aggregate_period_snapshots(n, 2035, cfg)
    period_hours = 4 * 7 * 24.0
    assert res.weights.sum() == pytest.approx(period_hours)
    # Representative periods are whole WEEKS of 672 quarter-hours.
    assert len(res.snapshots) % (7 * 96) == 0
    # Fallback (k ≥ periods): original weights, not 1.0.
    T.clear_cache()
    cfg.lf_k_periods = 8
    fb = T.aggregate_period_snapshots(n, 2035, cfg)
    assert fb.weights.sum() == pytest.approx(period_hours)
    assert set(fb.weights.round(9).unique()) == {0.25}


def test_aggregation_cache_sees_a_weight_edit():
    """Re-review C1: the cache key must change when the period's objective
    weights do — `run_simulation` solves the live network, so an edit
    followed by a re-solve otherwise reused the old weights."""
    import types

    from services import time_aggregation_service as T
    from services.snapshot_index import _build_period_multiindex

    T.clear_cache()
    n = __import__("pypsa").Network()
    ts = pd.date_range("2030-01-01", periods=4 * 7 * 24, freq="h")
    mi = _build_period_multiindex([2030, 2035], [ts, ts])
    n.set_snapshots(mi)
    n.investment_periods = [2030, 2035]
    n.snapshot_weightings.loc[:, :] = 2.0
    n.add("Bus", "b")
    rng = np.random.default_rng(4)
    n.add("Load", "l", bus="b", p_set=pd.Series(rng.uniform(10, 50, len(mi)), index=mi))
    cfg = types.SimpleNamespace(lf_k_periods=2, lf_period_length_h=168,
                                lf_cluster_method="hierarchical", lf_include_extreme=False)
    first = T.aggregate_period_snapshots(n, 2035, cfg)
    assert first.weights.sum() == pytest.approx(2.0 * 4 * 7 * 24)
    n.snapshot_weightings.loc[:, :] = 1.0
    second = T.aggregate_period_snapshots(n, 2035, cfg)
    assert second.weights.sum() == pytest.approx(4 * 7 * 24)


def test_hold_positions_tolerates_a_tz_aware_source_on_a_naive_axis():
    """Re-review C2: a tz-aware coarse source against a naive finer target
    must fall back to exact matching, not raise TypeError."""
    from services.user_timeseries import _hold_positions

    src = pd.date_range("2030-01-01", periods=24, freq="h", tz="UTC")
    tgt = pd.date_range("2030-01-01", periods=96, freq="15min")
    pos = _hold_positions(src, tgt)
    assert len(pos) == 96  # no raise; unmatched rows are -1


def test_a_calendar_frequency_leaves_no_old_weight_behind(client, install_network):
    """Re-review C3: `W`/`MS` has no fixed step; overlapping rows must not
    keep a previously-set weight while new rows get PyPSA's 1.0."""
    install_network(build_network())
    body = {"start": "2030-01-01 00:00", "end": "2030-03-31 00:00", "freq": "h",
            "weightings": 52.14}
    assert client.post("/api/network/snapshots", json=body).status_code == 200
    body = {"start": "2030-01-01 00:00", "end": "2030-12-31 00:00", "freq": "MS"}
    assert client.post("/api/network/snapshots", json=body).status_code == 200
    w = _weights(client)
    assert sorted(w["objective"].astype(float).unique()) == [1.0]
