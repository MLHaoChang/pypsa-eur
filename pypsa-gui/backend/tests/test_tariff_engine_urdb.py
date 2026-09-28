"""
Billing engine: the URDB constructs P1 left out (IC P2 WP2.1a-i).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.1a-i.
Oracles: tests/fixtures/investment_case/oracles (PROVENANCE.md) — REopt.jl's
tiered TOU demand (R2) and blended-rate cases (R4a hourly, R4b 15-min), to the
cent. Per-day fixed charges pro-rate by covered hours on the local clock (a
leap February has 29 days); tariff capacity items rate on the capacity the
caller passes (the adapter passes the PoC `p_nom_opt`), pro-rated like the
LP's fee by represented hours / 8760; `per_kva_year` needs a power factor.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial.tariff_engine import rate

ORACLES = pathlib.Path(__file__).parent / "fixtures" / "investment_case" / "oracles"
CENT = 0.005


def _load(name: str) -> Tariff:
    return Tariff.model_validate(json.loads((ORACLES / name).read_text()))


def _flat(kw: float, *, year=2017, freq="h") -> pd.DataFrame:
    idx = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:59", freq=freq)
    return pd.DataFrame({"import_mw": np.full(len(idx), kw / 1000.0), "export_mw": 0.0},
                        index=idx)


def test_r2_tiered_demand_matches_reopt_to_the_cent():
    # REopt's own expectation, read from the verbatim scenario (runtests.jl L2025–2037).
    scenario = json.loads((ORACLES / "r2_tiered_tou_demand.reopt.json").read_text())
    tiers = scenario["ElectricTariff"]["urdb_response"]["demandratestructure"][0]
    peak = scenario["ElectricLoad"]["annual_kwh"] / 8760
    tier1_max, r1, r2 = tiers[0]["max"], tiers[0]["rate"], tiers[1]["rate"]
    res = rate(_flat(peak), _load("r2_tiered_tou_demand.tariff.json"), step_hours=1.0,
               timezone=None)
    expected = 12 * (tier1_max * r1 + (peak - tier1_max) * r2)
    assert abs(res.per_item["demand"] - expected) < CENT


def test_r4a_blended_hourly_matches_reopt_to_the_cent():
    scenario = json.loads((ORACLES / "r4_no_techs.reopt.json").read_text())
    kwh = scenario["ElectricLoad"]["annual_kwh"]
    t = scenario["ElectricTariff"]
    res = rate(_flat(kwh / 8760), _load("r4_blended.tariff.json"), step_hours=1.0,
               timezone=None)
    assert abs(res.per_item["energy"] - t["blended_annual_energy_rate"] * kwh) < CENT
    assert abs(res.per_item["demand"] - 12 * t["blended_annual_demand_rate"] * kwh / 8760) < CENT


def test_r4b_blended_15min_matches_the_formula_to_the_cent():
    res = rate(_flat(1.0, freq="15min"), _load("r4_blended.tariff.json"), step_hours=0.25,
               timezone=None)
    assert abs(res.per_item["energy"] - 876.00) < CENT
    assert abs(res.per_item["demand"] - 120.00) < CENT


def _tariff(*items):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "NL",
                                  "valid_from": "2024-01-01", "items": list(items)})


def test_a_per_day_fixed_charge_counts_the_days_of_a_leap_february():
    item = {"id": "fixed", "kind": "fixed", "unit": "per_day", "periods": [{"name": "all",
                                                                           "rate": 2.5}]}
    idx = pd.date_range("2024-02-01", "2024-02-29 23:00", freq="h", tz="Europe/Amsterdam")
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=1.0, timezone="Europe/Amsterdam")
    assert res.per_item["fixed"] == pytest.approx(29 * 2.5)


def test_a_capacity_item_rates_the_passed_capacity_over_the_represented_hours():
    item = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
            "periods": [{"name": "all", "rate": 40.0}]}
    idx = pd.date_range("2024-03-04", periods=24 * 7, freq="h")
    week = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(week, _tariff(item), step_hours=1.0, timezone=None, capacity_kw=5000.0)
    assert res.per_item["cap"] == pytest.approx(40.0 * 5000.0 * 168 / 8760)
    # represented hours (e.g. a week standing for a year) scale it like the LP fee
    year = rate(week, _tariff(item), step_hours=1.0, timezone=None, capacity_kw=5000.0,
                represents_hours=8760 / 168, billing_period=("2024-01-01", "2025-01-01"))
    assert year.per_item["cap"] == pytest.approx(40.0 * 5000.0)


def test_capacity_without_a_capacity_or_kva_without_a_power_factor_is_not_established():
    kw = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
          "periods": [{"name": "all", "rate": 40.0}]}
    kva = {**kw, "id": "kva", "unit": "per_kva_year"}
    idx = pd.date_range("2024-03-04", periods=24, freq="h")
    d = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(d, _tariff(kw, kva), step_hours=1.0, timezone=None, capacity_kw=None)
    assert res.per_item["cap"] is None and res.per_item["kva"] is None
    assert res.total is None
    res = rate(d, _tariff(kva), step_hours=1.0, timezone=None, capacity_kw=900.0,
               power_factor=0.9)
    assert res.per_item["kva"] == pytest.approx(40.0 * 1000.0 * 24 / 8760)


def test_a_tariff_capacity_item_and_a_connection_fee_is_a_double_count():
    from services.commercial import lp_bindings as L
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    cap = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
           "periods": [{"name": "all", "rate": 40.0}]}
    fee = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01",
           "capacity_fee": {"id": "fee", "kind": "capacity", "unit": "per_kw_year",
                            "periods": [{"name": "all", "rate": 60.0}]}}
    tariff = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
              "items": [cap]}
    with pytest.raises(L.CommercialBindingError, match="capacity"):
        L.validate_for_network(build_edge_15min(), {"poc_link": "import", "import_tariff": tariff,
                                                    "connection": fee})


def test_the_power_factor_config_field_is_registered_for_recipe_1():
    from services.commercial.hashing import FIELDS_AFTER_V1

    assert FIELDS_AFTER_V1[("CommercialConfig", "power_factor")] is None


def test_a_peak_import_capacity_item_bills_the_annual_measured_peak():
    """The DE Leistungspreis: €/kW-year on the year's measured peak (the
    settlement-interval mean), not on contracted capacity — and once a year,
    never per month."""
    item = {"id": "lp", "kind": "capacity", "unit": "per_kw_year", "measured_on": "peak_import",
            "settlement": "15min", "periods": [{"name": "all", "rate": 85.0}]}
    idx = pd.date_range("2030-01-01", "2030-12-31 23:45", freq="15min", tz="Europe/Berlin")
    load = np.full(len(idx), 10.0)
    load[5000] = 42.0                              # the year's peak, one interval
    res = rate(pd.DataFrame({"import_mw": load, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=0.25, timezone="Europe/Berlin")
    assert res.per_item["lp"] == pytest.approx(85.0 * 42_000.0)
    assert res.monthly["lp"].sum() == pytest.approx(85.0 * 42_000.0)


# ── WP2.1a-i review round 1 ────────────────────────────────────────────────

_CAP = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
        "periods": [{"name": "all", "rate": 40.0}]}
_LP = {"id": "lp", "kind": "capacity", "unit": "per_kw_year", "measured_on": "peak_import",
       "settlement": "15min", "periods": [{"name": "all", "rate": 85.0}]}


def _year(load, freq="15min", tz="Europe/Berlin"):
    idx = pd.date_range("2030-01-01", "2030-12-31 23:59", freq=freq, tz=tz)
    return pd.DataFrame({"import_mw": load if np.ndim(load) else np.full(len(idx), load),
                         "export_mw": 0.0}, index=idx)


@pytest.mark.parametrize("settlement,step", [("15min", 0.25), ("h", 0.25)])
def test_a_nan_import_never_drops_out_of_the_measured_peak(settlement, step):
    """#1: a NaN interval is unknown, not 0 — the annual peak is not established."""
    d = _year(10.0)
    d.iloc[5000, 0] = np.nan
    res = rate(d, _tariff({**_LP, "settlement": settlement}), step_hours=step,
               timezone="Europe/Berlin")
    assert res.per_item["lp"] is None and res.total is None
    assert any(f.startswith("nan_quantity:") for f in res.flags["lp"])


def test_a_ratchet_applies_to_a_tiered_demand_item_with_a_zero_period_rate():
    """#2: tiers replace the period rate (R2's convention: period rate 0), so the
    window is charged and its ratchet applies."""
    item = {"id": "d", "kind": "demand", "unit": "per_kw_month", "settlement": "h",
            "periods": [{"name": "0", "rate": 0.0}],
            "tiers": [{"threshold": 0, "rate": 0.0}, {"threshold": 50.0, "rate": 12.0}],
            "ratchet": {"lookback_months": 11, "share": 0.8}}
    idx = pd.date_range("2030-01-01", "2030-02-28 23:00", freq="h")
    load = np.where(idx.month == 1, 0.2, 0.1)     # 200 kW January, 100 kW February
    history = {f"2029-{m:02d}": 0.0 for m in range(2, 13)}
    res = rate(pd.DataFrame({"import_mw": load, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=1.0, timezone=None, meter_history=history)
    feb = res.demand_lines[res.demand_lines["month"] == "2030-02"].iloc[0]
    assert feb["billed_kw"] == pytest.approx(160.0)
    assert feb["amount"] == pytest.approx((160.0 - 50.0) * 12.0)


@pytest.mark.parametrize("cap,pf,outcome", [
    (float("nan"), None, "not_established"), (-500.0, None, "refused"),
    (1000.0, 0.0, "refused"), (1000.0, 1.5, "refused"), (1000.0, -0.9, "refused")])
def test_capacity_inputs_are_validated_at_the_engine_boundary(cap, pf, outcome):
    """#3: the model's bounds hold at the engine boundary too."""
    item = {**_CAP, "unit": "per_kva_year"} if pf is not None else _CAP
    d = _year(1.0, freq="h")
    if outcome == "refused":
        with pytest.raises(ValueError):
            rate(d, _tariff(item), step_hours=1.0, timezone="Europe/Berlin", capacity_kw=cap,
                 power_factor=pf)
    else:
        res = rate(d, _tariff(item), step_hours=1.0, timezone="Europe/Berlin",
                   capacity_kw=cap, power_factor=pf)
        assert res.per_item[item["id"]] is None and res.total is None


def test_tiers_on_a_capacity_item_are_unsupported():
    item = {**_CAP, "tiers": [{"threshold": 0, "rate": 10.0}, {"threshold": 1000, "rate": 50.0}]}
    res = rate(_year(1.0, freq="h"), _tariff(item), step_hours=1.0, timezone="Europe/Berlin",
               capacity_kw=500.0)
    assert res.flags["cap"] == ["unsupported:tiers_on_capacity"]
    assert res.total is None


def test_a_capacity_item_over_gappy_weeks_is_not_a_complete_year():
    """#5: two sampled weeks billed against a full billing period without
    represents_hours must not look complete (the C4 invariant)."""
    fixed = {"id": "fixed", "kind": "fixed", "unit": "per_month",
             "periods": [{"name": "all", "rate": 100.0}]}
    parts = [pd.date_range(f"2030-{m:02d}-07", periods=24 * 7, freq="h") for m in (1, 7)]
    idx = parts[0].append(parts[1])
    d = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(d, _tariff(fixed, _CAP), step_hours=1.0, timezone=None, capacity_kw=1000.0,
               billing_period=("2030-01-01", "2031-01-01"))
    assert res.total is None
    assert "capacity_on_partial_coverage" in (res.notes.get("cap") or [])


def test_a_per_day_charge_counts_real_days_across_dst():
    """#6: the spring-forward day is one day (23 h), the fall-back day one day
    (25 h) — calendar days on the local clock, not hours / 24."""
    item = {"id": "fixed", "kind": "fixed", "unit": "per_day",
            "periods": [{"name": "all", "rate": 1.0}]}
    idx = pd.date_range("2024-03-01", "2024-10-31 23:00", freq="h", tz="Europe/Amsterdam")
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=1.0, timezone="Europe/Amsterdam")
    assert res.monthly.loc["2024-03", "fixed"] == pytest.approx(31.0)
    assert res.monthly.loc["2024-10", "fixed"] == pytest.approx(31.0)
    one = idx[(idx.month == 3) & (idx.day == 31)]
    day = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=one), _tariff(item),
               step_hours=1.0, timezone="Europe/Amsterdam")
    assert day.per_item["fixed"] == pytest.approx(1.0)


def test_capacity_prorating_is_disclosed():
    """#10: a partial or leap year is pro-rated by represented hours / 8760 (the
    LP's convention); say so."""
    res = rate(_year(1.0, freq="h").iloc[: 24 * 59], _tariff(_LP), step_hours=1.0,
               timezone="Europe/Berlin")
    assert "capacity_prorated_by_represented_hours" in res.notes["lp"]
    assert "peak_from_partial_year" in res.notes["lp"]


def test_a_capacity_double_count_has_its_own_preflight_code():
    """#8: the spec's code, not a generic binding error."""
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    fee = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01",
           "capacity_fee": {"id": "fee", "kind": "capacity", "unit": "per_kw_year",
                            "periods": [{"name": "all", "rate": 60.0}]}}
    tariff = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
              "items": [_CAP]}
    issues = validate_for_run(build_edge_15min(), SolverConfig(commercial={
        "poc_link": "import", "import_tariff": tariff, "connection": fee}))
    assert {i.code for i in issues if i.severity == "error"} == {"commercial.capacity_double_count"}


def test_a_peak_import_capacity_item_is_out_of_the_lp_as_capacity():
    """#7: the LP's reason names capacity, not demand."""
    from services.commercial import lp_bindings as L

    assert L._lp_reason(_tariff(_LP).items[0]) == "capacity_not_in_lp_until_WP2.1c"


# ── WP2.1a-i review round 2 ────────────────────────────────────────────────


def test_a_full_leap_year_discloses_the_8760_divisor():
    """R2 #1: 8784 represented hours / 8760 bill 1.00274 × the annual charge."""
    idx = pd.date_range("2024-01-01", "2024-12-31 23:00", freq="h")
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx), _tariff(_CAP),
               step_hours=1.0, timezone=None, capacity_kw=1000.0)
    assert res.per_item["cap"] == pytest.approx(40.0 * 1000.0 * 8784 / 8760)
    assert "capacity_prorated_by_represented_hours" in res.notes["cap"]


@pytest.mark.parametrize("lo,hi,days", [("2030-03-04 00:30", "2030-03-05 00:00", 23.5 / 24),
                                        ("2030-03-04 00:00", "2030-03-04 12:30", 12.5 / 24)])
def test_a_per_day_charge_over_a_billing_period_counts_the_exact_overlap(lo, hi, days):
    """R2 #2: the billing period's own bounds, not an hourly walk from its start."""
    item = {"id": "fixed", "kind": "fixed", "unit": "per_day",
            "periods": [{"name": "all", "rate": 1.0}]}
    idx = pd.date_range("2030-03-04", periods=24, freq="h")
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=1.0, timezone=None, billing_period=(lo, hi))
    assert res.per_item["fixed"] == pytest.approx(days)


# ── P2 WP2.1a-ii: tiers inside TOU windows ─────────────────────────────────
# URDB semantics: the tier position is the month's TOTAL energy across periods
# (a tier `max` is cumulative); each period's energy is split into the tiers in
# proportion to the month's total. One threshold list per item; each period
# carries its own rates (`tier_rates`), and every `Tier.rate` is then 0.

_THRESH = [{"threshold": 0, "rate": 0.0}, {"threshold": 1000, "rate": 0.0},
           {"threshold": 3000, "rate": 0.0}]


def _windowed_energy():
    import copy

    return {"id": "e", "kind": "energy", "unit": "per_kwh", "tiers": copy.deepcopy(_THRESH),
            "periods": [{"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21,
                         "tier_rates": [0.30, 0.35, 0.40]},
                        {"name": "off", "rate": 0.0, "tier_rates": [0.10, 0.12, 0.15]}]}


def test_windowed_energy_tiers_split_the_month_in_proportion_to_the_total():
    """Hand bill: January, 1000 kWh in the peak window, 4000 kWh off-peak. Month
    total 5000 kWh → tiers [1000, 2000, 2000] kWh; peak share 0.2:
    0.2 × (1000×0.30 + 2000×0.35 + 2000×0.40) + 0.8 × (1000×0.10 + 2000×0.12 + 2000×0.15)
    = 360 + 512 = 872.00."""
    idx = pd.date_range("2030-01-01", "2030-01-31 23:00", freq="h")
    peak = (idx.hour >= 17) & (idx.hour < 21)
    kw = np.where(peak, 1000.0 / peak.sum(), 4000.0 / (~peak).sum())
    res = rate(pd.DataFrame({"import_mw": kw / 1000.0, "export_mw": 0.0}, index=idx),
               _tariff(_windowed_energy()), step_hours=1.0, timezone=None)
    assert abs(res.per_item["e"] - 872.00) < CENT
    assert abs(res.lines["amount"].sum() - 872.00) < CENT   # the interval lines add up


@pytest.mark.parametrize("mutate,match", [
    (lambda i: i["periods"][1].pop("tier_rates"), "tier_rates"),
    (lambda i: i["tiers"][0].update(rate=0.1), "Tier.rate"),
    (lambda i: i["periods"][0].update(tier_rates=[0.3, 0.35]), "tier_rates"),
])
def test_a_malformed_windowed_tier_item_is_refused(mutate, match):
    item = _windowed_energy()
    mutate(item)
    with pytest.raises(ValueError, match=match):
        _tariff(item)


def test_windowed_demand_tiers_price_each_window_with_its_own_rates():
    """Peak window 80 kW, off window 30 kW; thresholds [0, 50] kW;
    peak rates [5, 10], off rates [1, 2]: 50×5 + 30×10 + 30×1 = 580."""
    item = {"id": "d", "kind": "demand", "unit": "per_kw_month", "settlement": "h",
            "tiers": [{"threshold": 0, "rate": 0.0}, {"threshold": 50, "rate": 0.0}],
            "periods": [{"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21,
                         "tier_rates": [5.0, 10.0]},
                        {"name": "off", "rate": 0.0, "tier_rates": [1.0, 2.0]}]}
    idx = pd.date_range("2030-01-07", periods=24, freq="h")
    kw = np.where((idx.hour >= 17) & (idx.hour < 21), 80.0, 30.0)
    res = rate(pd.DataFrame({"import_mw": kw / 1000.0, "export_mw": 0.0}, index=idx),
               _tariff(item), step_hours=1.0, timezone=None)
    assert res.per_item["d"] == pytest.approx(580.0)


def test_same_name_demand_fragments_must_agree_on_tier_rates():
    item = {"id": "d", "kind": "demand", "unit": "per_kw_month",
            "tiers": [{"threshold": 0, "rate": 0.0}, {"threshold": 50, "rate": 0.0}],
            "periods": [{"name": "peak", "rate": 0.0, "start_hour": 12, "end_hour": 14,
                         "tier_rates": [5.0, 10.0]},
                        {"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 19,
                         "tier_rates": [5.0, 12.0]},
                        {"name": "off", "rate": 0.0, "tier_rates": [1.0, 2.0]}]}
    with pytest.raises(ValueError, match="peak"):
        _tariff(item)


def test_tier_rates_is_registered_for_hash_recipe_1():
    from services.commercial.hashing import FIELDS_AFTER_V1

    assert FIELDS_AFTER_V1[("TariffPeriod", "tier_rates")] is None


def test_windowed_tiers_stay_out_of_the_lp_until_wp2_1c():
    from services.commercial import lp_bindings as L

    assert L._lp_reason(_tariff(_windowed_energy()).items[0]) == "tiers_with_windows"


def test_a_single_period_item_with_tier_rates_is_not_priced_at_zero_in_the_lp():
    """Its `Tier.rate` are 0 by rule; the LP must not read them as the P1 rates."""
    from services.commercial import lp_bindings as L

    item = {"id": "e", "kind": "energy", "unit": "per_kwh",
            "tiers": [{"threshold": 0, "rate": 0.0}, {"threshold": 1000, "rate": 0.0}],
            "periods": [{"name": "all", "rate": 0.0, "tier_rates": [0.1, 0.2]}]}
    assert L._lp_reason(_tariff(item).items[0]) == "tiers_with_windows"


# ── WP2.1a-ii review round 1 ───────────────────────────────────────────────

_P1_TIERS = [{"threshold": 0, "rate": 0.1}, {"threshold": 100, "rate": 0.2}]


def test_p1_windowed_tiers_migrate_to_the_same_rates_in_every_window():
    """#2: the P1 shape (windows + rates on Tier.rate) means one rate set for
    every window; it validates by migrating losslessly to per-period rates."""
    item = _tariff({"id": "e", "kind": "energy", "unit": "per_kwh", "tiers": _P1_TIERS,
                    "periods": [{"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21},
                                {"name": "rest", "rate": 0.0}]}).items[0]
    assert [t.rate for t in item.tiers] == [0.0, 0.0]
    assert [p.tier_rates for p in item.periods] == [[0.1, 0.2], [0.1, 0.2]]


def test_a_single_windowed_tier_period_bills_only_inside_its_window():
    """#1: P1 refused this shape; it must never bill outside the window. Outside
    intervals are unrated, like any windowed item without a catch-all."""
    item = {"id": "e", "kind": "energy", "unit": "per_kwh", "tiers": _P1_TIERS,
            "periods": [{"name": "day", "rate": 0.0, "start_hour": 8, "end_hour": 20}]}
    idx = pd.date_range("2030-01-07", periods=24, freq="h")
    res = rate(pd.DataFrame({"import_mw": 0.01, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=1.0, timezone=None)
    assert res.per_item["e"] is None
    assert "unrated_intervals:12" in res.flags["e"]
    summer = {**item, "periods": [{"name": "summer", "rate": 0.0, "months": [6, 7, 8]}]}
    res = rate(pd.DataFrame({"import_mw": 0.01, "export_mw": 0.0}, index=idx), _tariff(summer),
               step_hours=1.0, timezone=None)
    assert res.per_item["e"] is None                      # January is outside the window


def test_one_nan_names_its_month_and_counts_one_interval():
    """#3: the flags say what is unknown, not the month's row count."""
    idx = pd.date_range("2030-01-01", "2030-01-31 23:00", freq="h")
    d = pd.DataFrame({"import_mw": 0.005, "export_mw": 0.0}, index=idx)
    d.iloc[10, 0] = np.nan
    res = rate(d, _tariff(_windowed_energy()), step_hours=1.0, timezone=None)
    assert res.per_item["e"] is None
    assert sorted(res.flags["e"]) == ["nan_quantity:1", "tier_month_not_established:2030-01"]


def test_tiers_on_represented_volume_are_disclosed():
    """#4: with represents_hours the tiers see the represented volume."""
    idx = pd.date_range("2030-01-07", periods=24 * 7, freq="h")
    d = pd.DataFrame({"import_mw": 0.005, "export_mw": 0.0}, index=idx)
    res = rate(d, _tariff(_windowed_energy()), step_hours=1.0, timezone=None,
               represents_hours=8760 / 168, billing_period=("2030-01-01", "2031-01-01"))
    assert "tiers_on_represented_volume:2030-01" in res.notes["e"]


def test_a_week_weighted_to_its_own_month_sees_the_real_monthly_volume():
    """WP2.1b: one sampled week per month weighted to that month's hours is the
    month's real volume — nothing to disclose."""
    parts = [pd.date_range(f"2030-{m:02d}-07", periods=24 * 7, freq="h") for m in (1, 2)]
    idx = parts[0].append(parts[1])
    w = np.r_[np.full(168, 31 * 24 / 168), np.full(168, 28 * 24 / 168)]
    d = pd.DataFrame({"import_mw": 0.005, "export_mw": 0.0}, index=idx)
    res = rate(d, _tariff(_windowed_energy()), step_hours=1.0, timezone=None,
               represents_hours=w, billing_period=("2030-01-01", "2030-03-01"))
    assert not any(x.startswith("tiers_on_represented_volume") for x in res.notes.get("e", []))


@pytest.mark.parametrize("tiers", [[{"threshold": 0}], [{"threshold": 0, "rate": None}], "bad",
                                   [{"threshold": 0, "rate": True}]])
def test_malformed_tiers_on_a_windowed_item_are_a_validation_error(tiers):
    """WP2.1a-ii round 2: the migration never turns bad input into a raw TypeError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _tariff({"id": "e", "kind": "energy", "unit": "per_kwh", "tiers": tiers,
                 "periods": [{"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21},
                             {"name": "rest", "rate": 0.0}]})


# ── P2 WP2.1a-iii: designated-month and cyclic ratchets ────────────────────
# REopt's lookback cases (runtests.jl L1893–1956, 0-based hours below).

R3_RATES = [10, 10, 20, 50, 20, 10, 20, 20, 20, 20, 20, 5]


def _r3_load():
    idx = pd.date_range("2022-01-01", "2022-12-31 23:00", freq="h")
    kw = np.full(len(idx), 100.0)
    kw[21], kw[2402], kw[4087], kw[8332] = 200.0, 400.0, 500.0, 300.0
    return pd.DataFrame({"import_mw": kw / 1000.0, "export_mw": 0.0}, index=idx)


@pytest.mark.parametrize("fixture,peaks", [
    ("r3_case2.tariff.json", [300, 300, 300, 400, 300, 500, 300, 300, 300, 300, 300, 300]),
    ("r3_case3.tariff.json", [225, 225, 225, 400, 300, 500, 375, 375, 375, 375, 375, 375]),
])
def test_r3_lookbacks_match_reopt_to_the_cent(fixture, peaks):
    res = rate(_r3_load(), _load(fixture), step_hours=1.0, timezone=None)
    expected = sum(p * r for p, r in zip(peaks, R3_RATES))
    assert abs(res.per_item["demand"] - expected) < CENT
    billed = res.demand_lines.sort_values("month")["billed_kw"].tolist()
    assert billed == pytest.approx(peaks)
    assert "ratchet_seed_missing" not in (res.notes.get("demand") or [])


def _r3prime_load():
    idx = pd.date_range("2022-01-01", "2022-12-31 23:00", freq="h")
    kw = np.ones(len(idx))
    kw[7] = 100.0
    return pd.DataFrame({"import_mw": kw / 1000.0, "export_mw": 0.0}, index=idx)


def test_r3prime_cyclic_range_matches_reopt_to_the_cent():
    res = rate(_r3prime_load(), _load("r3prime.tariff.json"), step_hours=1.0, timezone=None)
    expected = 100 * (10.5 + 0.35 * 10.5 * 5 + 0.35 * 11.5 * 6)
    assert abs(res.per_item["demand"] - expected) < CENT
    assert res.total is not None


def test_r3prime_without_cyclic_year_and_history_is_a_lower_bound():
    t = json.loads((ORACLES / "r3prime.tariff.json").read_text())
    t["items"][0]["ratchet"]["cyclic_year"] = False
    res = rate(_r3prime_load(), Tariff.model_validate(t), step_hours=1.0, timezone=None)
    assert "ratchet_seed_missing" in res.notes["demand"]
    assert res.total is None


def test_months_mode_reads_same_rate_year_history_for_an_unmodelled_month():
    """A designated month outside the dispatch reads meter history under ITS
    rate-year key; absent, the ratchet is a lower bound."""
    t = _load("r3_case2.tariff.json")
    d = _r3_load()
    d = d[d.index.month >= 2]                         # January (designated) not modelled
    res = rate(d, t, step_hours=1.0, timezone=None)
    assert "ratchet_seed_missing" in res.notes["demand"]
    res = rate(d, t, step_hours=1.0, timezone=None, meter_history={"2022-01": 1000.0})
    feb = res.demand_lines[res.demand_lines["month"] == "2022-02"].iloc[0]
    assert feb["billed_kw"] == pytest.approx(750.0)   # 0.75 × the metered January 1000 kW


@pytest.mark.parametrize("ratchet,match", [
    ({"share": 0.5}, "lookback_months"),                                  # neither
    ({"lookback_months": 3, "months": [1], "share": 0.5}, "lookback_months"),  # both
    ({"months": [1], "share": 0.5, "cyclic_year": True}, "cyclic_year"),
    ({"months": [13], "share": 0.5}, "months"),
    ({"lookback_months": 12, "share": 0.5, "cyclic_year": True}, "cyclic_year"),
])
def test_ratchet_modes_are_validated(ratchet, match):
    with pytest.raises(ValueError, match=match):
        _tariff({"id": "d", "kind": "demand", "unit": "per_kw_month",
                 "periods": [{"name": "all", "rate": 10.0}], "ratchet": ratchet})


def test_new_ratchet_modes_stay_out_of_the_lp_until_wp2_1c():
    from services.commercial import lp_bindings as L

    for f in ("r3_case2.tariff.json", "r3_case3.tariff.json"):
        assert L._lp_reason(_load(f).items[0]) == "ratchet_mode_not_in_lp_until_WP2.1c"


def test_new_ratchet_fields_are_registered_for_hash_recipe_1():
    from services.commercial.hashing import FIELDS_AFTER_V1

    assert FIELDS_AFTER_V1[("Ratchet", "months")] is None
    assert FIELDS_AFTER_V1[("Ratchet", "cyclic_year")] is False


# ── WP2.1a-iii review round 1 ──────────────────────────────────────────────


@pytest.mark.parametrize("nan_hour", [24, 8300])            # January 2, then December
def test_a_nan_lookback_month_makes_the_dependent_months_unknown(nan_hour):
    """#1: a NaN among the lookback peaks is unknown for every month that reads
    it — never a floor that appears or vanishes with the NaN's position."""
    d = _r3_load()
    d.iloc[nan_hour, 0] = np.nan
    res = rate(d, _load("r3_case2.tariff.json"), step_hours=1.0, timezone=None)
    assert res.per_item["demand"] is None
    nan_month = str(d.index[nan_hour])[:7]
    others = [m for m in res.monthly.index if m != nan_month]
    assert res.monthly.loc[others, "demand"].isna().all()
    assert any(f.startswith("ratchet_prior_unknown:") for f in res.flags["demand"])


def test_months_mode_on_representative_weeks_missing_a_designated_month():
    """#3: the spec's case — sampled weeks standing for the year, the designated
    January not sampled and not metered ⇒ a lower bound."""
    parts = [pd.date_range(f"2022-{m:02d}-07", periods=24 * 7, freq="h") for m in (4, 7, 10)]
    idx = parts[0].append(parts[1]).append(parts[2])
    d = pd.DataFrame({"import_mw": 0.3, "export_mw": 0.0}, index=idx)
    res = rate(d, _load("r3_case2.tariff.json"), step_hours=1.0, timezone=None,
               represents_hours=8760 / len(idx), billing_period=("2022-01-01", "2023-01-01"))
    assert "ratchet_seed_missing" in res.notes["demand"]
    assert res.total is None
