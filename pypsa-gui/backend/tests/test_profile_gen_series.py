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


_TOU = {"currency": "EUR", "price_basis": "nominal", "currency_year": 2024,
        "periods": [{"name": "peak", "rate": 120.0, "weekdays": [0, 1, 2, 3, 4],
                     "start_hour": 8, "end_hour": 20},
                    {"name": "base", "rate": 60.0}]}


def _price_net(*, finance=None):
    n = PyPSAService.get_network()
    idx = pd.date_range("2023-01-01", "2023-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", carrier="AC")
    n.add("Bus", "H1", carrier="heat")
    n.add("Generator", "grid", bus="B1", p_nom=100.0)
    n.add("Link", "hp", bus0="B1", bus1="H1", p_nom=5.0)
    n.add("Link", "poc", bus0="H1", bus1="B1", p_nom=5.0)
    ctx = PyPSAService.get_active_context()
    state = ctx.solver_state.setdefault("solver_config", SimpleNamespace())
    state.finance = finance
    state.commercial = {"poc_link": "poc", "export_link": None}
    return n


def _apply_series(res, component, **kw):
    ct, attr = default_target(res.meta, kw.pop("component_type", None), None)
    return assign_profile(PyPSAService.get_active_context(), component,
                          res.series[res.series.columns[0]], res.meta,
                          component_type=ct, attribute=attr, time_basis="UTC",
                          now=NOW, **kw)


# ── time-of-use price ───────────────────────────────────────────────────────

def test_tou_uses_the_tariff_period_model_first_match_wins():
    res = _gen("tou", _TOU)
    s = res.series["X"]
    assert _at(s, "2023-03-08T10:00") == 120.0     # Wednesday, peak
    assert _at(s, "2023-03-08T21:00") == 60.0
    assert _at(s, "2023-03-12T10:00") == 60.0      # Sunday
    m = res.meta
    assert m["unit"] == "EUR/MWh"
    assert m["currency_year"] == 2024 and m["price_basis"] == "nominal"
    assert m["target"] == ["generators", "marginal_cost"]


def test_tou_needs_a_period_for_every_hour():
    params = dict(_TOU, periods=[{"name": "day", "rate": 1.0,
                                  "start_hour": 8, "end_hour": 20}])
    with pytest.raises(InvalidSeriesParamsError, match="every hour"):
        _gen("tou", params)


def test_tou_refuses_a_malformed_period():
    params = dict(_TOU, periods=[{"name": "x", "rate": 1.0, "start_hour": 20,
                                  "end_hour": 8}])
    with pytest.raises(InvalidSeriesParamsError, match="periods"):
        _gen("tou", params)


def test_a_tou_price_without_a_currency_year_is_refused_at_apply():
    _price_net()
    res = _gen("tou", {k: v for k, v in _TOU.items() if k != "currency_year"},
               ids=("grid",))
    with pytest.raises(InvalidSeriesUnitError, match="currency_year"):
        _apply_series(res, "grid")


def test_a_price_in_another_currency_than_the_projects_is_refused():
    _price_net(finance={"currency": "USD", "price_basis": "nominal",
                        "currency_year": 2024})
    with pytest.raises(InvalidSeriesUnitError, match="USD"):
        _apply_series(_gen("tou", _TOU, ids=("grid",)), "grid")


def test_a_price_on_another_money_basis_is_refused():
    _price_net(finance={"currency": "EUR", "price_basis": "real",
                        "currency_year": 2020})
    with pytest.raises(InvalidSeriesUnitError, match="real"):
        _apply_series(_gen("tou", _TOU, ids=("grid",)), "grid")


def test_a_matching_price_applies_with_its_range_and_negatives():
    _price_net(finance={"currency": "EUR", "price_basis": "nominal",
                        "currency_year": 2024})
    params = dict(_TOU, periods=[{"name": "neg", "rate": -15.0, "start_hour": 12,
                                  "end_hour": 14}, {"name": "base", "rate": 60.0}])
    out = _apply_series(_gen("tou", params, ids=("grid",)), "grid")
    assert out["min"] == -15.0 and out["peak"] == 60.0


def test_a_link_price_is_per_mwh_of_its_bus0_flow():
    _price_net()
    params = dict(_TOU, component_type="links")
    _apply_series(_gen("tou", params, ids=("hp",)), "hp")
    from services.user_timeseries import _user_ts_meta
    assert _user_ts_meta[("links", "marginal_cost", "hp")]["per"] == "MWh of p0 (bus0 flow)"


def test_a_price_on_the_poc_link_is_refused_naming_the_tariff():
    from services.profile_gen.series import PocTariffError

    _price_net()
    params = dict(_TOU, component_type="links")
    with pytest.raises(PocTariffError, match="tariff"):
        _apply_series(_gen("tou", params, ids=("poc",)), "poc")


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
    # The network solve takes inflow (PyPSA spills the excess); only the
    # assistant's storage calculator does not.
    assert any("unsupported_storage_inflow" in w for w in out["warnings"])


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


def test_cop_matches_a_hand_value():
    # T_sink 35 C, T_source 0 C, eta 0.45: 0.45 * 308.15 / 35 = 3.962
    s = _gen("cop", {"source": "ground", "source_c": 0.0, "sink_c": 35.0,
                     "eta": 0.45}).series["X"]
    assert float(s.iloc[0]) == pytest.approx(3.962, abs=1e-3)


@pytest.mark.parametrize("params", [
    {"source": "air", "sink_c": -5.0},
    {"source": "water"},
    {"source": "air", "eta": 1.5},
    {"source": "air", "eta": 0.0},
    {"source": "air", "cop_max": 0.5},
    {"source": "ground", "source_c": 40.0, "sink_c": 35.0},
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


def test_cop_is_refused_on_a_link_whose_bus0_is_not_electricity():
    from services.profile_gen.series import CopBusError

    _price_net()
    res = _gen("cop", {"source": "ground", "source_c": 5.0, "sink_c": 35.0},
               ids=("poc",))   # bus0 is the heat bus
    with pytest.raises(CopBusError, match="bus0"):
        _apply_series(res, "poc")


def test_cop_applies_on_an_electricity_link():
    n = _price_net()
    res = _gen("cop", {"source": "ground", "source_c": 5.0, "sink_c": 35.0},
               ids=("hp",))
    _apply_series(res, "hp")
    assert float(n.links_t.efficiency["hp"].iloc[0]) == pytest.approx(0.45 * 308.15 / 30.0)
