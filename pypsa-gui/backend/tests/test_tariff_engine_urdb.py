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
    peak = 1e6 / 8760
    res = rate(_flat(peak), _load("r2_tiered_tou_demand.tariff.json"), step_hours=1.0,
               timezone=None)
    expected = 12 * (50 * 0.0 + (peak - 50) * 12.0)
    assert abs(res.per_item["demand"] - expected) < CENT


def test_r4a_blended_hourly_matches_reopt_to_the_cent():
    res = rate(_flat(10000 / 8760), _load("r4_blended.tariff.json"), step_hours=1.0,
               timezone=None)
    assert abs(res.per_item["energy"] - 1000.00) < CENT
    assert abs(res.per_item["demand"] - 12 * 10 * 10000 / 8760) < CENT


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
