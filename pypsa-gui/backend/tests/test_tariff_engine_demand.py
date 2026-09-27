"""
Monthly demand charges in the tariff engine (Edge Investment Case P1 WP1.5a).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.5a
(the LP demand cost must equal the engine's on the same dispatch, so the engine
rates single-rate demand items now; ratchets are WP1.5b, capacity items are the
connection agreement's).

A demand item (`kind="demand"`, `unit="per_kw_month"`) bills, per local month
and per period window (first match wins, as for energy), `rate × max kW`
measured on the item's quantity within that window. A period list without a
catch-all is a windowed demand charge: intervals outside every window carry no
demand, which is not an error. Each bill row is traceable in `demand_lines`.
"""
from __future__ import annotations

import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial.tariff_engine import rate


def _tariff(*items):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                                  "valid_from": "2030-01-01", "items": list(items)})


def _demand(item_id="demand", periods=None, measured_on="import", **kw):
    return {"id": item_id, "kind": "demand", "unit": "per_kw_month",
            "periods": periods or [{"name": "all", "rate": 10.0}],
            "measured_on": measured_on, **kw}


def _dispatch(imp, start="2030-01-30 00:00", freq="h", tz="America/Chicago", exp=0.0):
    idx = pd.date_range(start, periods=len(imp), freq=freq, tz=tz)
    return pd.DataFrame({"import_mw": imp, "export_mw": exp}, index=idx)


def test_demand_is_rate_times_the_monthly_max_in_kw():
    # 30 Jan–2 Feb: January peak 3 MW, February peak 5 MW.
    imp = [1.0] * 24 + [3.0] + [1.0] * 47 + [5.0] + [2.0] * 23
    res = rate(_dispatch(imp), _tariff(_demand()), step_hours=1.0, timezone="America/Chicago")
    assert res.per_item["demand"] == pytest.approx(10.0 * 3000 + 10.0 * 5000)
    assert res.monthly.loc["2030-01", "demand"] == pytest.approx(30_000.0)
    assert res.monthly.loc["2030-02", "demand"] == pytest.approx(50_000.0)
    dl = res.demand_lines
    assert set(dl.columns) >= {"month", "tariff_item", "period", "peak_kw", "rate", "amount"}
    assert dl.loc[dl.month == "2030-02", "peak_kw"].item() == pytest.approx(5000.0)
    assert res.complete is False or res.notes.get("demand")  # partial months are disclosed
    assert "demand_on_partial_month" in res.notes["demand"]


def test_a_windowed_demand_charge_measures_only_inside_its_window():
    # On-peak demand 16:00–21:00 local; the 5 MW spike at 03:00 is outside.
    imp = [1.0] * 24
    imp[3] = 5.0
    imp[18] = 2.5
    item = _demand(periods=[{"name": "on_peak", "rate": 20.0, "start_hour": 16, "end_hour": 21}])
    res = rate(_dispatch(imp, start="2030-07-01 00:00"), _tariff(item), step_hours=1.0,
               timezone="America/Chicago")
    assert res.per_item["demand"] == pytest.approx(20.0 * 2500)
    assert res.flags["demand"] == []  # outside the window is not "unrated"


def test_each_window_bills_its_own_peak():
    imp = [1.0] * 24
    imp[3] = 5.0   # off-peak peak
    imp[18] = 2.5  # on-peak peak
    item = _demand(periods=[{"name": "on_peak", "rate": 20.0, "start_hour": 16, "end_hour": 21},
                            {"name": "all_hours", "rate": 4.0}])
    res = rate(_dispatch(imp, start="2030-07-01 00:00"), _tariff(item), step_hours=1.0,
               timezone="America/Chicago")
    # First match wins: 16–21 intervals feed on_peak only; the rest feed all_hours.
    assert res.per_item["demand"] == pytest.approx(20.0 * 2500 + 4.0 * 5000)


def test_net_demand_is_floored_at_zero():
    res = rate(_dispatch([1.0, 3.0, 0.0], exp=[0.0, 1.0, 4.0]),
               _tariff(_demand(measured_on="net")), step_hours=1.0, timezone="America/Chicago")
    assert res.per_item["demand"] == pytest.approx(10.0 * 2000)


def test_peak_import_is_import():
    res = rate(_dispatch([1.0, 4.0]), _tariff(_demand(measured_on="peak_import")),
               step_hours=1.0, timezone="America/Chicago")
    assert res.per_item["demand"] == pytest.approx(10.0 * 4000)


def test_nan_import_makes_the_month_unrated():
    res = rate(_dispatch([1.0, float("nan")]), _tariff(_demand()), step_hours=1.0,
               timezone="America/Chicago")
    assert res.per_item["demand"] is None
    assert res.flags["demand"] == ["nan_quantity:1"]


def test_a_ratchet_without_history_bills_a_disclosed_lower_bound():
    # WP1.5b: ratchets are rated; unknown lookback months are disclosed and
    # the total is withheld (it is a lower bound).
    item = _demand(ratchet={"lookback_months": 11, "share": 0.8})
    res = rate(_dispatch([1.0, 2.0]), _tariff(item), step_hours=1.0, timezone="America/Chicago")
    assert res.flags["demand"] == []
    assert "ratchet_seed_missing" in res.notes["demand"]
    assert res.total is None and res.total_supported is not None


def test_a_full_month_is_not_flagged_partial():
    idx_len = 31 * 24
    res = rate(_dispatch([1.0] * idx_len, start="2030-01-01 00:00"), _tariff(_demand()),
               step_hours=1.0, timezone="America/Chicago")
    assert "demand_on_partial_month" not in res.notes.get("demand", [])
    assert res.per_item["demand"] == pytest.approx(10_000.0)


# ── WP1.5a review round 1 ──────────────────────────────────────────────────


def test_a_month_without_rows_inside_the_billing_period_is_not_established():
    """#1: never a 'complete' bill missing a month's demand charge."""
    jan = _dispatch([2.0] * 3, start="2030-01-10 00:00")
    mar = _dispatch([3.0] * 3, start="2030-03-10 00:00")
    res = rate(pd.concat([jan, mar]), _tariff(_demand()), step_hours=1.0,
               timezone="America/Chicago",
               billing_period=(pd.Timestamp("2030-01-01", tz="America/Chicago"),
                               pd.Timestamp("2030-04-01", tz="America/Chicago")))
    assert "demand_month_not_established:2030-02" in res.flags["demand"]
    assert res.per_item["demand"] is None and res.total is None and res.complete is False


def test_the_demand_interval_averages_finer_dispatch():
    """#5: an hourly demand interval on 15-min dispatch bills the max HOURLY mean."""
    imp = [1.0, 1.0, 1.0, 5.0] + [2.0] * 4  # hour 0 mean 2.0, hour 1 mean 2.0
    item = _demand(settlement="h")
    res = rate(_dispatch(imp, start="2030-07-01 00:00", freq="15min"), _tariff(item),
               step_hours=0.25, timezone="America/Chicago")
    assert res.per_item["demand"] == pytest.approx(10.0 * 2000)
    assert any(x.startswith("resolution:") for x in res.notes["demand"])


def test_a_nan_outside_every_window_does_not_unrate_the_month():
    """#8: only the window's own intervals matter."""
    imp = [1.0] * 24
    imp[3] = float("nan")
    imp[18] = 2.5
    item = _demand(periods=[{"name": "on_peak", "rate": 20.0, "start_hour": 16, "end_hour": 21}])
    res = rate(_dispatch(imp, start="2030-07-01 00:00"), _tariff(item), step_hours=1.0,
               timezone="America/Chicago")
    assert res.per_item["demand"] == pytest.approx(20.0 * 2500)


# ── WP1.5a review round 2 ──────────────────────────────────────────────────


@pytest.mark.parametrize("tz,day", [("America/New_York", "2030-11-03"),
                                    ("Europe/Berlin", "2030-10-27")])
def test_the_fall_back_hour_rates_without_crashing(tz, day):
    """#1: flooring the repeated wall-clock hour must not re-localise it."""
    idx = pd.date_range(f"{day} 00:00", periods=96, freq="15min", tz="UTC").tz_convert(tz)
    df = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(df, _tariff(_demand(settlement="h")), step_hours=0.25, timezone=tz)
    assert res.per_item["demand"] == pytest.approx(10_000.0)
