"""The series family: other model series (ticket 03).

`generate_profile(family="series", technology=<kind>)`:

  * ``tou``      → marginal_cost: time-of-use bands, in currency/MWh with its
    money basis (currency_year required, as every price);
  * ``inflow``   → storage_units inflow: an annual MWh over a monthly shape;
  * ``cop``      → links efficiency: heat-pump COP from the source
    temperature (the assumed ambient for air source);
  * ``part_load`` → refused: a part-load curve depends on dispatch, which the
    linear LP cannot take as a time series.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from services import dc_load_profile
from services.profile_gen.apply import InvalidSeriesUnitError, assign_profile
from services.profile_gen.engine import generate_profile
from services.profile_gen.families import default_target
from services.profile_gen.series import (
    InvalidSeriesParamsError,
    PartLoadNotASeriesError,
    UnknownSeriesKindError,
)
from services.pypsa_service import PyPSAService

NOW = "2026-10-20T00:00:00+00:00"


def _gen(kind, params=None, *, ids=("X",), year=2023):
    return generate_profile(points=list(ids), technology=kind,
                            params=dict(params or {}), year=year, now=NOW,
                            family="series")


def _at(s, ts):
    return float(s.loc[pd.Timestamp(ts)])


_TOU = {"currency": "EUR", "price_basis": "real", "currency_year": 2024,
        "bands": [{"hours": [8, 20], "days": "weekday", "price": 120.0},
                  {"hours": [0, 24], "days": "all", "price": 60.0}]}


# ── time-of-use price ───────────────────────────────────────────────────────

def test_tou_bands_place_the_first_matching_band():
    res = _gen("tou", _TOU)
    s = res.series["X"]
    assert _at(s, "2023-03-08T10:00") == 120.0     # Wednesday, peak
    assert _at(s, "2023-03-08T21:00") == 60.0
    assert _at(s, "2023-03-12T10:00") == 60.0      # Sunday
    m = res.meta
    assert m["unit"] == "EUR/MWh"
    assert m["currency_year"] == 2024 and m["price_basis"] == "real"
    assert m["target"] == ["generators", "marginal_cost"]


def test_tou_needs_a_band_for_every_hour():
    params = dict(_TOU, bands=[{"hours": [8, 20], "days": "all", "price": 1.0}])
    with pytest.raises(InvalidSeriesParamsError, match="every hour"):
        _gen("tou", params)


def test_tou_may_be_seasonal():
    params = dict(_TOU, bands=[
        {"hours": [0, 24], "days": "all", "months": [12, 1, 2], "price": 90.0},
        {"hours": [0, 24], "days": "all", "price": 50.0}])
    s = _gen("tou", params).series["X"]
    assert _at(s, "2023-01-10T03:00") == 90.0
    assert _at(s, "2023-06-10T03:00") == 50.0


def test_a_tou_price_without_a_currency_year_is_refused_at_apply():
    n = PyPSAService.get_network()
    idx = pd.date_range("2023-01-01", "2023-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("Generator", "grid", bus="B1", p_nom=100.0)
    res = _gen("tou", {k: v for k, v in _TOU.items() if k != "currency_year"},
               ids=("grid",))
    ct, attr = default_target(res.meta, None, None)
    with pytest.raises(InvalidSeriesUnitError, match="currency_year"):
        assign_profile(PyPSAService.get_active_context(), "grid",
                       res.series["grid"], res.meta, component_type=ct,
                       attribute=attr, time_basis="UTC")


# ── hydro inflow ────────────────────────────────────────────────────────────

def test_inflow_holds_the_annual_energy_over_a_snowmelt_year():
    res = _gen("inflow", {"annual_mwh": 8760.0, "shape": "snowmelt"})
    s = res.series["X"] * res.meta["scale_mw"]
    assert float(s.sum()) == pytest.approx(8760.0)
    assert s["2023-05"].mean() > 2 * s["2023-01"].mean()
    assert res.meta["target"] == ["storage_units", "inflow"]
    assert res.meta["unit"] == "per_unit_mean"


def test_inflow_takes_twelve_monthly_shares():
    shares = [1.0] * 6 + [3.0] * 6
    s = _gen("inflow", {"annual_mwh": 1000.0, "monthly": shares}).series["X"]
    assert s["2023-08"].mean() == pytest.approx(3 * s["2023-02"].mean())


def test_inflow_applies_with_its_energy_record():
    n = PyPSAService.get_network()
    idx = pd.date_range("2023-01-01", "2023-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("StorageUnit", "dam", bus="B1", p_nom=10.0, max_hours=500.0)
    res = _gen("inflow", {"annual_mwh": 20000.0, "shape": "rain_fed"}, ids=("dam",))
    ct, attr = default_target(res.meta, None, None)
    out = assign_profile(PyPSAService.get_active_context(), "dam",
                         res.series["dam"], res.meta, component_type=ct,
                         attribute=attr, time_basis="UTC", now=NOW)
    assert out["profile_annual_mwh"] == pytest.approx(20000.0)


# ── heat-pump COP ───────────────────────────────────────────────────────────

def test_air_source_cop_follows_the_carnot_fraction_of_the_ambient():
    params = {"source": "air", "sink_c": 45.0, "eta": 0.45, "cop_max": 7.0}
    res = _gen("cop", params)
    s = res.series["X"]
    temp = dc_load_profile.ambient_c(SimpleNamespace(**res.meta["ambient"]), s.index)
    sink = 45.0 + 273.15
    expected = np.clip(0.45 * sink / (sink - (temp + 273.15)), 1.0, 7.0)
    assert np.allclose(s.to_numpy(), expected)
    assert s["2023-01"].mean() < s["2023-07"].mean()
    assert res.meta["target"] == ["links", "efficiency"]


def test_ground_source_cop_is_constant():
    s = _gen("cop", {"source": "ground", "source_c": 10.0, "sink_c": 35.0}).series["X"]
    expected = 0.45 * (35 + 273.15) / 25.0
    assert np.allclose(s.to_numpy(), min(expected, 7.0))


@pytest.mark.parametrize("params", [
    {"source": "air", "sink_c": -5.0},
    {"source": "water"},
    {"source": "air", "eta": 1.5},
])
def test_bad_cop_parameters_are_refused(params):
    with pytest.raises(InvalidSeriesParamsError):
        _gen("cop", params)


# ── refusals ────────────────────────────────────────────────────────────────

def test_part_load_curves_are_not_a_series():
    with pytest.raises(PartLoadNotASeriesError, match="dispatch"):
        _gen("part_load", {})


def test_an_unknown_series_kind_lists_the_known_ones():
    with pytest.raises(UnknownSeriesKindError, match="tou"):
        _gen("tides")
