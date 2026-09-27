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


def test_step_hours_is_required():
    """Review WP1.2 #5: a median-inferred step hides irregular and overlapping
    intervals. The caller states the interval length (a float, or a per-row
    Series of hours)."""
    from services.commercial.tariff_engine import rate

    raw, tariff, df = _load("leap_february_fixed.json")
    with pytest.raises(TypeError):
        rate(df, tariff, timezone="Europe/Berlin")  # noqa: missing step_hours


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



# ── review WP1.2: the oracle must never price bad input as a confident number ──


def _dispatch(start, n, tz="Europe/Berlin", imp=1.0, exp=0.0, freq="15min"):
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    return pd.DataFrame({"import_mw": imp, "export_mw": exp}, index=idx)


def test_nan_quantity_is_flagged_not_priced_as_zero():
    from services.commercial.tariff_engine import rate

    df = _dispatch("2030-03-04 12:00", 4)
    df.iloc[1, 0] = np.nan
    res = rate(df, _flat_tariff(), step_hours=0.25, timezone="Europe/Berlin")
    assert res.flags["flat"] == ["nan_quantity:1"]
    assert res.per_item["flat"] is None and res.total is None
    assert res.complete is False
    assert np.isnan(res.monthly.loc["2030-03", "flat"])


def test_an_unrated_month_makes_the_year_unrated_too():
    from services.commercial.tariff_engine import rate

    t = Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "DE",
                               "valid_from": "2030-01-01", "items": [{
                                   "id": "jan_only", "kind": "energy", "unit": "per_kwh",
                                   "periods": [{"name": "jan", "rate": 0.1, "months": [1]}],
                                   "settlement": "15min", "measured_on": "import",
                                   "direction": "cost"}]})
    df = pd.concat([_dispatch("2030-01-15 00:00", 4), _dispatch("2030-12-15 00:00", 4)])
    res = rate(df, t, step_hours=0.25, timezone="Europe/Berlin")
    assert res.monthly.loc["2030-01", "jan_only"] == pytest.approx(4 * 250 * 0.1)
    assert np.isnan(res.monthly.loc["2030-12", "jan_only"])
    assert np.isnan(res.annual.loc["2030", "jan_only"])


def test_a_tz_aware_index_requires_a_timezone():
    from services.commercial.tariff_engine import rate

    df = _dispatch("2030-01-07 16:00", 4, tz="UTC")
    with pytest.raises(ValueError, match="timezone"):
        rate(df, _flat_tariff(), step_hours=0.25, timezone=None)


def test_a_naive_index_with_a_timezone_is_refused():
    """A naive LOCAL index cannot represent the repeated fall-back hour."""
    from services.commercial.tariff_engine import rate

    df = _dispatch("2030-01-07 16:00", 4, tz=None)
    with pytest.raises(ValueError, match="naive"):
        rate(df, _flat_tariff(), step_hours=0.25, timezone="Europe/Berlin")
    # Naive with no timezone is allowed: a wall clock without DST, documented.
    res = rate(df, _flat_tariff(), step_hours=0.25, timezone=None)
    assert res.total == pytest.approx(4 * 50.0)


@pytest.mark.parametrize("mutate,match", [
    (lambda d: pd.concat([d, d.iloc[[0]]]).sort_index(), "increasing"),
    (lambda d: d.iloc[::-1], "increasing"),
    (lambda d: d.assign(import_mw=-5.0), "negative"),
    (lambda d: d.drop(columns=["import_mw"]), "import_mw"),
    (lambda d: d.drop(columns=["export_mw"]), "export_mw"),
])
def test_invalid_dispatch_is_refused(mutate, match):
    from services.commercial.tariff_engine import rate

    df = mutate(_dispatch("2030-03-04 12:00", 4))
    with pytest.raises(ValueError, match=match):
        rate(df, _flat_tariff(), step_hours=0.25, timezone="Europe/Berlin")


def test_per_row_durations_are_honoured():
    """Time-segmented snapshots: rows of 1 h and 3 h."""
    from services.commercial.tariff_engine import rate

    idx = pd.DatetimeIndex(["2030-03-04 00:00", "2030-03-04 01:00"]).tz_localize("Europe/Berlin")
    df = pd.DataFrame({"import_mw": [1.0, 1.0], "export_mw": 0.0}, index=idx)
    dur = pd.Series([1.0, 3.0], index=idx)
    res = rate(df, _flat_tariff(), step_hours=dur, timezone="Europe/Berlin")
    assert res.total == pytest.approx((1000 + 3000) * 0.2)
    with pytest.raises(ValueError, match="overlap"):
        rate(df, _flat_tariff(), step_hours=pd.Series([2.0, 1.0], index=idx),
             timezone="Europe/Berlin")


def _fixed_tariff(monthly=310.0):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "DE",
                                  "valid_from": "2030-01-01", "items": [{
                                      "id": "meter", "kind": "fixed", "unit": "per_month",
                                      "periods": [{"name": "all", "rate": monthly}],
                                      "settlement": "15min", "measured_on": "import",
                                      "direction": "cost"}]})


def test_fixed_charge_prorates_by_local_hours_so_a_utc_year_bills_twelve_months():
    """A full UTC year rated in Berlin covers 23 h of 1 Jan (local) … and 1 h
    of 1 Jan 2031 (local). Pro-rating by HOURS gives 12 months less one hour
    plus one hour: 12 × 310 to within 310/744 × 2."""
    from services.commercial.tariff_engine import rate

    idx = pd.date_range("2030-01-01", "2030-12-31 23:45", freq="15min", tz="UTC")
    df = pd.DataFrame({"import_mw": 0.0, "export_mw": 0.0}, index=idx)
    res = rate(df, _fixed_tariff(), step_hours=0.25, timezone="Europe/Berlin")
    assert res.per_item["meter"] == pytest.approx(12 * 310.0, abs=1.0)
    # The 2031 sliver is one local hour of January, not a whole day.
    assert res.monthly.loc["2031-01", "meter"] == pytest.approx(310.0 / 744, rel=1e-9)


def test_dst_months_have_their_real_number_of_hours():
    from services.commercial.tariff_engine import rate

    # All of March 2030 in Berlin = 743 h; covering it fully bills exactly one month.
    idx = pd.date_range(pd.Timestamp("2030-03-01").tz_localize("Europe/Berlin"),
                        pd.Timestamp("2030-04-01").tz_localize("Europe/Berlin"),
                        freq="15min", inclusive="left")
    assert len(idx) == 743 * 4
    df = pd.DataFrame({"import_mw": 0.0, "export_mw": 0.0}, index=idx)
    res = rate(df, _fixed_tariff(), step_hours=0.25, timezone="Europe/Berlin")
    assert res.per_item["meter"] == pytest.approx(310.0, rel=1e-12)


def test_representative_weeks_bill_fixed_charges_on_the_billing_period():
    from services.commercial.tariff_engine import rate

    weeks = [_dispatch(f"2030-{m:02d}-07 00:00", 7 * 96, imp=0.0) for m in (1, 4, 7, 10)]
    df = pd.concat(weeks)
    partial = rate(df, _fixed_tariff(), step_hours=0.25, timezone="Europe/Berlin")
    assert "fixed_prorated_on_partial_coverage" in partial.notes["meter"]
    full = rate(df, _fixed_tariff(), step_hours=0.25, timezone="Europe/Berlin",
                billing_period=("2030-01-01", "2031-01-01"))
    assert full.per_item["meter"] == pytest.approx(12 * 310.0, rel=1e-9)
    assert "fixed_prorated_on_partial_coverage" not in full.notes.get("meter", [])


def test_unsupported_items_say_why():
    from services.commercial.tariff_engine import rate

    t = Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "NL",
                               "valid_from": "2030-01-01", "items": [
        {"id": "tiered", "kind": "energy", "unit": "per_kwh",
         "periods": [{"name": "all", "rate": 0.1}],
         "tiers": [{"threshold": 0, "rate": 0.12}, {"threshold": 10000, "rate": 0.05}],
         "settlement": "15min", "measured_on": "import", "direction": "cost"},
        {"id": "demand", "kind": "demand", "unit": "per_kw_month",
         "periods": [{"name": "all", "rate": 10.0}],
         "settlement": "15min", "measured_on": "peak_import", "direction": "cost"},
        {"id": "kw_energy", "kind": "energy", "unit": "per_kw_year",
         "periods": [{"name": "all", "rate": 1.0}],
         "settlement": "15min", "measured_on": "import", "direction": "cost"},
        {"id": "windowed_fixed", "kind": "fixed", "unit": "per_month",
         "periods": [{"name": "win", "rate": 5.0, "months": [1]}],
         "settlement": "15min", "measured_on": "import", "direction": "cost"}]})
    res = rate(_dispatch("2030-01-07 00:00", 4), t, step_hours=0.25, timezone="Europe/Berlin")
    assert res.flags["tiered"] == ["unsupported:tiers_P1_WP1.5c"]
    assert res.flags["demand"] == ["unsupported:demand_P2_WP2.1"]
    assert res.flags["kw_energy"] == ["unsupported:unit_per_kw_year_for_energy"]
    assert res.flags["windowed_fixed"] == ["unsupported:fixed_with_windows"]
    assert set(res.unsupported_items) == {"tiered", "demand", "kw_energy", "windowed_fixed"}


def test_a_month_outside_a_seasonal_period_falls_through_to_the_next():
    from services.commercial.tariff_engine import rate

    raw, tariff, df = _load("tou_weekday_evening.json")
    # Same Monday-evening hours in February: the January-only peak does not apply.
    feb = df.copy()
    feb.index = feb.index + pd.Timedelta(days=28)
    res = rate(feb, tariff, step_hours=0.25, timezone="Europe/Berlin")
    assert res.total == pytest.approx(5 * 100.0)


def test_tou_window_on_the_fall_back_day_counts_both_repeated_hours():
    """Europe/Berlin 2030-10-27: 02:00–03:00 local happens twice. A 02–03 window
    must see all 8 quarter-hours (2 × 4)."""
    from services.commercial.tariff_engine import rate

    t = Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "DE",
                               "valid_from": "2030-01-01", "items": [{
                                   "id": "night", "kind": "energy", "unit": "per_kwh",
                                   "periods": [{"name": "w", "rate": 1.0, "start_hour": 2, "end_hour": 3},
                                               {"name": "else", "rate": 0.0}],
                                   "settlement": "15min", "measured_on": "import",
                                   "direction": "cost"}]})
    idx = pd.date_range(pd.Timestamp("2030-10-27").tz_localize("Europe/Berlin"),
                        pd.Timestamp("2030-10-28").tz_localize("Europe/Berlin"),
                        freq="15min", inclusive="left")
    df = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx.tz_convert("UTC"))
    res = rate(df, t, step_hours=0.25, timezone="Europe/Berlin")
    assert res.total == pytest.approx(8 * 250 * 1.0)


def test_net_revenue_item_pays_on_net_export_floored_at_zero():
    """Per-interval (instantaneous) netting — documented semantics."""
    from services.commercial.tariff_engine import rate

    t = Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                               "valid_from": "2030-01-01", "items": [{
                                   "id": "net_credit", "kind": "energy", "unit": "per_kwh",
                                   "periods": [{"name": "all", "rate": 0.04}],
                                   "settlement": "15min", "measured_on": "net",
                                   "direction": "revenue"}]})
    idx = pd.date_range("2030-06-01 12:00", periods=2, freq="15min", tz="America/Chicago")
    df = pd.DataFrame({"import_mw": [1.0, 3.0], "export_mw": [3.0, 1.0]}, index=idx)
    res = rate(df, t, step_hours=0.25, timezone="America/Chicago")
    # interval 1: net export 2 MW → 500 kWh × 0.04 = 20 revenue; interval 2: net import → 0
    assert res.lines["amount"].tolist() == pytest.approx([-20.0, 0.0])


def test_a_dispatch_step_different_from_settlement_is_disclosed():
    from services.commercial.tariff_engine import rate

    df = _dispatch("2030-03-04 12:00", 4, freq="h")
    res = rate(df, _flat_tariff(), step_hours=1.0, timezone="Europe/Berlin")
    assert res.notes["flat"] == ["resolution:dispatch_1h_settlement_0.25h"]
    assert res.complete is True     # disclosed, not incomplete


# ── re-review WP1.2 conditions ────────────────────────────────────────────────


def _mixed_tariff():
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "DE",
                                  "valid_from": "2030-01-01", "items": [
        {"id": "meter", "kind": "fixed", "unit": "per_month",
         "periods": [{"name": "all", "rate": 310.0}],
         "settlement": "15min", "measured_on": "import", "direction": "cost"},
        {"id": "energy", "kind": "energy", "unit": "per_kwh",
         "periods": [{"name": "all", "rate": 0.2}],
         "settlement": "15min", "measured_on": "import", "direction": "cost"}]})


def _rep_weeks():
    return pd.concat([_dispatch(f"2030-{m:02d}-07 00:00", 7 * 96) for m in (1, 4, 7, 10)])


def test_billing_period_with_gappy_energy_is_partial_not_a_confident_total():
    """C1: 12 months of fixed charges + 4 weeks of energy is not a bill."""
    from services.commercial.tariff_engine import rate

    res = rate(_rep_weeks(), _mixed_tariff(), step_hours=0.25, timezone="Europe/Berlin",
               billing_period=("2030-01-01", "2031-01-01"))
    assert "energy_on_partial_coverage" in res.notes["energy"]
    assert res.total is None and res.complete is False
    assert res.per_item["meter"] == pytest.approx(12 * 310.0)
    # Annual per item is computed from its OWN months: a month with no rows is
    # not "unrated", so neither item's year is NaN.
    assert not np.isnan(res.annual.loc["2030", "meter"])
    assert not np.isnan(res.annual.loc["2030", "energy"])


def test_represents_hours_scales_representative_weeks_to_the_year():
    """With per-row represented hours (the snapshot weightings), 4 weeks of
    1 MW stand for 8760 MWh and the bill is complete."""
    from services.commercial.tariff_engine import rate

    df = _rep_weeks()
    represents = pd.Series(8760.0 / len(df), index=df.index)
    res = rate(df, _mixed_tariff(), step_hours=0.25, timezone="Europe/Berlin",
               billing_period=("2030-01-01", "2031-01-01"), represents_hours=represents)
    assert res.per_item["energy"] == pytest.approx(8760 * 1000 * 0.2)
    assert "energy_on_partial_coverage" not in res.notes.get("energy", [])
    assert res.total == pytest.approx(12 * 310.0 + 8760 * 1000 * 0.2)
    assert res.complete is True


@pytest.mark.parametrize("bad,match", [
    (lambda d: d.assign(import_mw=np.inf), "finite"),
    (lambda d: d.iloc[0:0], "empty"),
])
def test_infinite_and_empty_dispatch_are_refused(bad, match):
    from services.commercial.tariff_engine import rate

    with pytest.raises(ValueError, match=match):
        rate(bad(_dispatch("2030-03-04 12:00", 4)), _flat_tariff(), step_hours=0.25,
             timezone="Europe/Berlin")


def test_nan_step_hours_is_refused_and_arrays_are_accepted():
    from services.commercial.tariff_engine import rate

    df = _dispatch("2030-03-04 12:00", 4)
    with pytest.raises(ValueError, match="step_hours"):
        rate(df, _flat_tariff(), step_hours=float("nan"), timezone="Europe/Berlin")
    res = rate(df, _flat_tariff(), step_hours=np.full(4, 0.25), timezone="Europe/Berlin")
    assert res.total == pytest.approx(4 * 50.0)
    with pytest.raises(ValueError, match="length"):
        rate(df, _flat_tariff(), step_hours=np.full(3, 0.25), timezone="Europe/Berlin")


def test_represents_hours_on_gappy_data_requires_a_billing_period():
    """C4: energy scaled to a year with fixed charges on 4 weeks is refused."""
    from services.commercial.tariff_engine import rate

    df = _rep_weeks()
    represents = pd.Series(8760.0 / len(df), index=df.index)
    with pytest.raises(ValueError, match="billing_period"):
        rate(df, _mixed_tariff(), step_hours=0.25, timezone="Europe/Berlin",
             represents_hours=represents)
    res = rate(df, _mixed_tariff(), step_hours=0.25, timezone="Europe/Berlin",
               billing_period=("2030-01-01", "2031-01-01"), represents_hours=represents)
    assert "monthly_shows_sampled_months_only" in res.notes["energy"]
