"""
Tariff engine core — energy / TOU / fixed items (Edge Investment Case P1 WP1.2).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.2
Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §5.5

The ORACLE every LP binding is checked against, so its own truth comes from
hand-rated bills (fixtures/investment_case/bills/*.json, with the arithmetic
written out in each `_comment` and one case cross-checked row by row in a CSV),
never from the engine itself.

Conventions pinned here:
  * dispatch is MW per interval; energy = MW × step_hours × 1000 kWh;
  * `amount` is signed from the SITE's view: + cost, − revenue;
  * TOU periods match on the LOCAL clock of the tariff's timezone; the first
    matching period wins; an interval no period covers is NOT rated (NaN) and
    the item is flagged — never silently priced at 0 (ADR-0001);
  * fixed monthly items are pro-rated by calendar days covered;
  * demand / capacity items are P2 (WP2.1): reported as unsupported, not priced.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff

BILLS = Path(__file__).resolve().parent / "fixtures" / "investment_case" / "bills"


def _load(name):
    raw = json.loads((BILLS / name).read_text())
    tariff = Tariff.model_validate(raw["tariff"])
    tz = raw["timezone"]
    if "dispatch" in raw:
        idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in raw["dispatch"]]).tz_localize(tz)
        df = pd.DataFrame({"import_mw": [r[1] for r in raw["dispatch"]],
                           "export_mw": [r[2] for r in raw["dispatch"]]}, index=idx)
    else:
        d = raw["dispatch_range"]
        idx = pd.date_range(d["start"], d["end"], freq=d["freq"], tz=tz)
        df = pd.DataFrame({"import_mw": d["import_mw"], "export_mw": d["export_mw"]}, index=idx)
    return raw, tariff, df


@pytest.mark.parametrize("name", sorted(p.name for p in BILLS.glob("*.json")))
def test_hand_rated_bills_match_to_the_cent(name):
    from services.commercial.tariff_engine import rate

    raw, tariff, df = _load(name)
    res = rate(df, tariff, step_hours=raw["step_hours"], timezone=raw["timezone"])
    exp = raw["expected"]
    for item, amount in exp["per_item"].items():
        assert round(res.per_item[item], 2) == pytest.approx(amount, abs=1e-9), (name, item)
    assert round(res.total, 2) == pytest.approx(exp["total"], abs=1e-9), name
    for month, items in exp.get("monthly", {}).items():
        for item, amount in items.items():
            assert round(float(res.monthly.loc[month, item]), 2) == pytest.approx(amount, abs=1e-9)
    for item, amounts in exp.get("per_interval", {}).items():
        got = res.lines.loc[res.lines["tariff_item"] == item, "amount"].round(2).tolist()
        assert got == amounts, (name, item)


def test_the_csv_cross_check_agrees_with_the_json_fixture():
    csv = pd.read_csv(BILLS / "tou_weekday_evening_hand.csv")
    rows = csv[csv["interval_local"] != "TOTAL"]
    assert (rows["import_mw"] * rows["step_h"] * 1000 == rows["kwh"]).all()
    assert np.allclose(rows["kwh"] * rows["rate_eur_per_kwh"], rows["amount_eur"])
    raw = json.loads((BILLS / "tou_weekday_evening.json").read_text())
    assert rows["amount_eur"].round(2).tolist() == raw["expected"]["per_interval"]["energy_tou"]
    assert float(csv.loc[csv["interval_local"] == "TOTAL", "amount_eur"].iloc[0]) == raw["expected"]["total"]


def _flat_tariff(rate_eur=0.2, **item_kw):
    item = {"id": "flat", "kind": "energy", "unit": "per_kwh",
            "periods": [{"name": "all", "rate": rate_eur}],
            "settlement": "15min", "measured_on": "import", "direction": "cost", **item_kw}
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "DE",
                                  "valid_from": "2030-01-01", "items": [item]})


@pytest.mark.parametrize("day,n_intervals", [("2030-10-27", 100), ("2030-03-31", 92)])
def test_dst_days_count_every_real_quarter_hour_once(day, n_intervals):
    """Europe/Berlin: fall-back day has 100 quarter-hours, spring-forward 92.
    The engine must rate the REAL intervals — 1 MW flat at 0.20 €/kWh is
    250 kWh × 0.20 = 50.00 per interval."""
    from services.commercial.tariff_engine import rate

    start = pd.Timestamp(day).tz_localize("Europe/Berlin")
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize("Europe/Berlin")
    idx = pd.date_range(start, end, freq="15min", inclusive="left")
    assert len(idx) == n_intervals
    df = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx.tz_convert("UTC"))
    res = rate(df, _flat_tariff(), step_hours=0.25, timezone="Europe/Berlin")
    assert len(res.lines) == n_intervals
    assert res.total == pytest.approx(50.0 * n_intervals)


def test_tou_window_uses_the_local_clock_not_utc():
    """17:00 Berlin in January is 16:00 UTC. A UTC-indexed dispatch must still
    land in the 17–21 local peak window."""
    from services.commercial.tariff_engine import rate

    raw, tariff, df = _load("tou_weekday_evening.json")
    res = rate(df.tz_convert("UTC"), tariff, step_hours=0.25, timezone="Europe/Berlin")
    assert round(res.total, 2) == 1100.00


def test_an_interval_no_period_covers_is_flagged_not_priced_at_zero():
    from services.commercial.tariff_engine import rate

    t = Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "DE",
                               "valid_from": "2030-01-01", "items": [{
                                   "id": "peak_only", "kind": "energy", "unit": "per_kwh",
                                   "periods": [{"name": "peak", "rate": 0.3,
                                                "start_hour": 17, "end_hour": 21}],
                                   "settlement": "15min", "measured_on": "import",
                                   "direction": "cost"}]})
    idx = pd.date_range("2030-01-07 16:45", periods=2, freq="15min", tz="Europe/Berlin")
    df = pd.DataFrame({"import_mw": 4.0, "export_mw": 0.0}, index=idx)
    res = rate(df, t, step_hours=0.25, timezone="Europe/Berlin")
    amounts = res.lines["amount"].tolist()
    assert np.isnan(amounts[0]) and amounts[1] == pytest.approx(300.0)
    assert res.flags["peak_only"] == ["unrated_intervals:1"]
    assert res.per_item["peak_only"] is None or np.isnan(res.per_item["peak_only"])
    assert res.complete is False


def test_all_zero_dispatch_bills_only_fixed_items():
    from services.commercial.tariff_engine import rate

    raw, tariff, df = _load("leap_february_fixed.json")
    df[:] = 0.0
    res = rate(df, tariff, step_hours=0.25, timezone="Europe/Berlin")
    assert round(res.per_item["levy"], 2) == 0.00
    assert round(res.per_item["meter"], 2) == 155.17
    assert round(res.total, 2) == 155.17


def test_demand_and_capacity_items_are_reported_unsupported_not_priced():
    from services.commercial.tariff_engine import rate

    import json as _j
    us = Tariff.model_validate_json((BILLS.parent / "us_tariff_demand_charge.json").read_text())
    idx = pd.date_range("2030-07-01", periods=8, freq="15min", tz="America/Chicago")
    df = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(df, us, step_hours=0.25, timezone="America/Chicago")
    assert "demand_monthly" in res.unsupported_items
    assert "demand_monthly" not in res.per_item
    assert res.complete is False
    # A bill missing its demand charge is not a total; the partial is labelled.
    assert res.total is None
    assert res.total_supported == pytest.approx(
        res.per_item["energy_tou"] + res.per_item["customer_charge"])
    assert "energy_tou" in res.per_item and "customer_charge" in res.per_item


def test_step_hours_is_inferred_from_a_regular_index():
    from services.commercial.tariff_engine import rate

    raw, tariff, df = _load("leap_february_fixed.json")
    res = rate(df, tariff, timezone="Europe/Berlin")
    assert round(res.total, 2) == 7355.17


def test_a_15min_year_rates_in_under_a_second():
    from services.commercial.tariff_engine import rate

    de = Tariff.model_validate_json((BILLS.parent / "de_tariff_capacity_tou.json").read_text())
    idx = pd.date_range("2030-01-01", "2030-12-31 23:45", freq="15min", tz="Europe/Berlin")
    df = pd.DataFrame({"import_mw": np.random.default_rng(1).uniform(0, 50, len(idx)),
                       "export_mw": 0.0}, index=idx)
    t0 = time.perf_counter()
    res = rate(df, de, step_hours=0.25, timezone="Europe/Berlin")
    assert time.perf_counter() - t0 < 1.0
    assert len(res.lines) > 0 and "network_capacity" in res.unsupported_items


def test_engine_is_pure_no_lp_or_router_imports():
    import inspect

    from services.commercial import tariff_engine as m

    src = inspect.getsource(m)
    for bad in ("linopy", "pypsa", "routers", "solver_service"):
        assert f"import {bad}" not in src and f"from {bad}" not in src, bad
