"""The load family: archetype shapes through the one engine (ticket 01 slice B1).

`generate_profile(family="load", technology=<archetype>, ...)` returns a
per-unit-peak shape on the source year's hourly axis, one column per load id,
with the scale the user stated (peak MW or annual MWh) in its meta. The shape
then goes through `assign_profile` like any other profile.

Every archetype is a stated, assumed shape (provenance ``synthetic/assumed``),
never a measurement. The data-centre archetype wraps
`services.dc_load_profile.hourly_profile`; it does not repeat that maths.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from services import dc_load_profile
from services.profile_gen import load_archetypes
from services.profile_gen.apply import (
    DcLoadOwnedError,
    assign_profile,
)
from services.profile_gen.engine import UnsupportedResolutionError, generate_profile
from services.profile_gen.load_archetypes import (
    InvalidArchetypeParamsError,
    UnknownArchetypeError,
)
from services.pypsa_service import PyPSAService
from services.user_timeseries import _user_ts_meta

NOW = "2026-10-20T00:00:00+00:00"


def _gen(archetype: str, params: dict | None = None, *, year: int = 2023,
         ids=("L1",), resolution: str = "hourly"):
    return generate_profile(points=list(ids), technology=archetype,
                            params={"peak_mw": 10.0, **(params or {})},
                            year=year, resolution=resolution, now=NOW,
                            family="load")


def _at(series: pd.Series, ts: str) -> float:
    return float(series.loc[pd.Timestamp(ts)])


# ── the engine contract ─────────────────────────────────────────────────────

def test_a_load_archetype_is_a_peak_normalised_year_with_its_scale_in_meta():
    res = _gen("industrial", {"shifts": 2})
    s = res.series["L1"]
    assert len(s) == 8760
    assert s.index[0] == pd.Timestamp("2023-01-01T00:00")
    assert float(s.max()) == pytest.approx(1.0)
    m = res.meta
    assert m["family"] == "load"
    assert m["archetype"] == "industrial"
    assert m["unit"] == "per_unit_peak"
    assert m["scale_mw"] == 10.0
    assert m["provenance"] == "synthetic/assumed"
    assert m["weather_year"] == 2023
    assert m["generated_at"] == NOW


def test_the_renewable_path_is_unchanged_by_default():
    res = generate_profile(points=[("site", 13.4, 52.5)], technology="solar",
                           source="synthetic", year=2023, now=NOW)
    assert "family" not in res.meta or res.meta["family"] == "renewable"
    assert res.meta["technology"] == "solar"


def test_same_inputs_give_identical_series_and_a_new_seed_changes_the_noise():
    a = _gen("residential", {"noise": 0.05, "seed": 1}).series
    b = _gen("residential", {"noise": 0.05, "seed": 1}).series
    c = _gen("residential", {"noise": 0.05, "seed": 2}).series
    pd.testing.assert_frame_equal(a, b)
    assert not np.allclose(a["L1"], c["L1"])


def test_two_loads_get_their_own_noise_stream():
    res = _gen("residential", {"noise": 0.05}, ids=("A", "B"))
    assert list(res.series.columns) == ["A", "B"]
    assert not np.allclose(res.series["A"], res.series["B"])


def test_an_annual_energy_scales_so_the_source_year_holds_it():
    res = _gen("commercial", {"peak_mw": None, "annual_mwh": 8000.0})
    s = res.series["L1"]
    assert float((s * res.meta["scale_mw"]).sum()) == pytest.approx(8000.0)


@pytest.mark.parametrize("params", [{"peak_mw": None},
                                    {"annual_mwh": 1000.0}])
def test_exactly_one_of_peak_and_annual_energy_is_required(params):
    with pytest.raises(InvalidArchetypeParamsError, match="peak_mw or annual_mwh"):
        _gen("industrial", params)


def test_an_unknown_parameter_is_refused_rather_than_ignored():
    with pytest.raises(InvalidArchetypeParamsError, match="shfits"):
        _gen("industrial", {"shfits": 3})


def test_an_unknown_archetype_lists_the_known_ones():
    with pytest.raises(UnknownArchetypeError, match="industrial"):
        _gen("bakery")


@pytest.mark.parametrize("resolution", ["daily", "weekly"])
def test_a_load_archetype_is_hourly_only(resolution):
    # A day or week mean of a load shape loses the shape the archetype
    # exists to state; the step-hold upsample at apply would flatten it.
    with pytest.raises(UnsupportedResolutionError, match="hourly"):
        _gen("industrial", resolution=resolution)


def test_an_annual_energy_holds_for_every_load_even_with_noise():
    res = _gen("residential", {"peak_mw": None, "annual_mwh": 1000.0,
                               "noise": 0.3}, ids=("A", "B"))
    assert res.meta["unit"] == "per_unit_mean"
    for col in ("A", "B"):
        energy = float((res.series[col] * res.meta["scale_mw"]).sum())
        assert energy == pytest.approx(1000.0)


def test_the_noise_seed_used_is_recorded():
    res = _gen("residential", {"noise": 0.05})
    assert isinstance(res.meta["params"]["seed"], int)


@pytest.mark.parametrize("holidays", ["12-25", ["1"], ["2022-12-25"], [12]])
def test_holidays_must_be_a_list_of_dates_in_the_source_year(holidays):
    with pytest.raises(InvalidArchetypeParamsError, match="holiday"):
        _gen("industrial", {"holidays": holidays})


def test_a_full_date_holiday_in_the_source_year_is_accepted():
    s = _gen("industrial", {"shifts": 2, "base_share": 0.2,
                            "holidays": ["2023-12-27"]}).series["L1"]
    assert _at(s, "2023-12-27T10:00") == pytest.approx(0.2)


def test_a_shutdown_week_is_the_source_years_iso_week_only():
    # 2024-12-30/31 are ISO week 1 of 2025: a week-1 shutdown in 2024 is
    # 1-7 January 2024 only.
    s = _gen("industrial", {"shifts": "continuous", "base_share": 0.1,
                            "shutdown_weeks": [1]}, year=2024).series["L1"]
    assert _at(s, "2024-01-03T12:00") == pytest.approx(0.1)
    assert _at(s, "2024-12-31T12:00") == pytest.approx(1.0)


@pytest.mark.parametrize("params", [
    {"shutdown_weeks": ["summer"]},
    {"shutdown_weeks": [54]},
    {"seed": "abc", "noise": 0.1},
    {"base_share": -0.2},
    {"base_share": 1.5},
])
def test_bad_numbers_are_typed_refusals(params):
    with pytest.raises(InvalidArchetypeParamsError):
        _gen("industrial", params)


def test_a_bad_cooling_share_names_the_parameter():
    with pytest.raises(InvalidArchetypeParamsError, match="cooling_share"):
        _gen("commercial", {"cooling_share": -1.0})


def test_seasonality_is_a_common_parameter():
    s = _gen("industrial", {"shifts": "continuous",
                            "seasonal_amplitude": 0.2}).series["L1"]
    assert s["2023-01"].mean() > s["2023-07"].mean()


# ── archetype shapes ────────────────────────────────────────────────────────

def test_industrial_two_shift_runs_weekdays_06_to_22():
    s = _gen("industrial", {"shifts": 2, "base_share": 0.2}).series["L1"]
    assert _at(s, "2023-03-08T10:00") == pytest.approx(1.0)   # Wednesday
    assert _at(s, "2023-03-08T23:00") == pytest.approx(0.2)
    assert _at(s, "2023-03-12T10:00") == pytest.approx(0.2)   # Sunday


def test_industrial_continuous_runs_every_hour_except_shutdowns():
    s = _gen("industrial", {"shifts": "continuous", "base_share": 0.1,
                            "shutdown_weeks": [32]}).series["L1"]
    assert _at(s, "2023-03-12T03:00") == pytest.approx(1.0)   # Sunday night
    assert _at(s, "2023-08-09T12:00") == pytest.approx(0.1)   # ISO week 32


def test_a_holiday_runs_like_a_sunday():
    s = _gen("industrial", {"shifts": 2, "base_share": 0.2,
                            "holidays": ["12-25"]}).series["L1"]
    assert _at(s, "2023-12-25T10:00") == pytest.approx(0.2)   # a Monday
    assert _at(s, "2023-12-27T10:00") == pytest.approx(1.0)


def test_a_timezone_maps_working_hours_on_the_local_clock():
    # The series stays on UTC snapshots; 06:00 Berlin (CET) is 05:00 UTC.
    s = _gen("industrial", {"shifts": 2, "base_share": 0.2,
                            "timezone": "Europe/Berlin"}).series["L1"]
    assert _at(s, "2023-03-08T05:00") == pytest.approx(1.0)
    assert _at(s, "2023-03-08T04:00") == pytest.approx(0.2)


def test_the_commercial_office_preset_is_the_study_library_shape():
    res = _gen("library", {"profile_id": "commercial_office"})
    s = res.series["L1"]
    lib = pd.read_csv(load_archetypes.LIBRARY_DIR / "commercial_office.csv")
    row = lib[(lib.month == 3) & (lib.daytype == "weekday") & (lib.hour == 10)]
    expected = float(row.factor.iloc[0]) / float(lib.factor.max())
    assert _at(s, "2023-03-08T10:00") == pytest.approx(expected)
    assert res.meta["library_profile"] == "commercial_office"


def test_presets_name_their_archetype_and_parameters():
    presets = {p["name"]: p for p in load_archetypes.list_presets()}
    assert presets["commercial_office"]["archetype"] == "library"
    assert presets["industrial_two_shift"]["archetype"] == "library"
    assert presets["industrial_three_shift"]["params"]["shifts"] == 3
    res = _gen("industrial", {"preset": "industrial_three_shift"})
    assert res.meta["preset"] == "industrial_three_shift"
    assert res.meta["params"]["shifts"] == 3


def test_residential_with_heat_pumps_peaks_in_winter():
    s = _gen("residential", {"heating": "heat_pump"}).series["L1"]
    assert s["2023-01"].mean() > 1.3 * s["2023-07"].mean()


def test_residential_ev_share_lifts_the_evening():
    base = _gen("residential", {"ev_share": 0.0, "peak_mw": 1.0}).series["L1"]
    ev = _gen("residential", {"ev_share": 0.5, "peak_mw": 1.0}).series["L1"]
    # Compare relative to the morning, since both are peak-normalised.
    def ratio(s):
        return _at(s, "2023-03-08T21:00") / _at(s, "2023-03-08T08:00")
    assert ratio(ev) > ratio(base)


def test_depot_ev_charging_is_a_night_load():
    s = _gen("ev_charging", {"mode": "depot"}).series["L1"]
    assert _at(s, "2023-03-08T02:00") > 3 * _at(s, "2023-03-08T12:00")


def test_heat_from_temperature_is_winter_heavy_with_a_hot_water_floor():
    s = _gen("heat", {"hot_water_share": 0.1}).series["L1"]
    assert s["2023-01"].mean() > 3 * s["2023-07"].mean()
    assert float(s.min()) > 0.0


def test_the_data_centre_archetype_wraps_the_dc_load_model():
    res = _gen("data_centre", {"pue_at_design": 1.3,
                               "pue_temperature_slope_per_k": 0.02})
    s = res.series["L1"]
    spec = load_archetypes.dc_spec(res.meta["params"], 2023)
    raw = dc_load_profile.hourly_profile(spec, s.index, 1.0)
    assert np.allclose(s.to_numpy(), raw / raw.max())


# ── through the apply path ──────────────────────────────────────────────────

def _net(year: int = 2023):
    n = PyPSAService.get_network()
    idx = pd.date_range(f"{year}-01-01", f"{year}-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1")
    n.add("Load", "it_load", bus="B1")
    return n


def test_an_archetype_applies_to_a_load_at_its_annual_energy():
    n = _net()
    res = _gen("commercial", {"peak_mw": None, "annual_mwh": 8000.0})
    out = assign_profile(PyPSAService.get_active_context(), "L1",
                         res.series["L1"], res.meta,
                         component_type="loads", attribute="p_set", now=NOW)
    assert float(n.loads_t.p_set["L1"].sum()) == pytest.approx(8000.0)
    assert out["profile_annual_mwh"] == pytest.approx(8000.0)
    assert _user_ts_meta[("loads", "p_set", "L1")]["archetype"] == "commercial"


def _dc_project(tmp_path):
    (tmp_path / "eh_template.json").write_text(json.dumps(
        {dc_load_profile.META_KEY: {"envelope": {"schema": 1}}}))
    ctx = PyPSAService.get_active_context()
    ctx.storage_dir = str(tmp_path)
    return ctx


def test_a_data_centre_profile_is_refused_on_a_dc_load_project(tmp_path):
    _net()
    ctx = _dc_project(tmp_path)
    res = _gen("data_centre")
    with pytest.raises(DcLoadOwnedError, match="dc_load"):
        assign_profile(ctx, "L1", res.series["L1"], res.meta,
                       component_type="loads", attribute="p_set", now=NOW)


def test_any_load_profile_onto_the_dc_split_loads_is_refused(tmp_path):
    _net()
    ctx = _dc_project(tmp_path)
    res = _gen("industrial")
    with pytest.raises(DcLoadOwnedError, match="it_load"):
        assign_profile(ctx, "it_load", res.series["L1"], res.meta,
                       component_type="loads", attribute="p_set", now=NOW)


def test_other_loads_on_a_dc_load_project_still_apply(tmp_path):
    _net()
    ctx = _dc_project(tmp_path)
    res = _gen("industrial")
    assign_profile(ctx, "L1", res.series["L1"], res.meta,
                   component_type="loads", attribute="p_set", now=NOW)


def test_the_series_clock_is_recorded_as_the_source_time_basis():
    on_zone = _gen("industrial", {"timezone": "Europe/Oslo"}).meta
    assert on_zone["source_time_basis"] == "UTC (from Europe/Oslo wall clock)"
    no_zone = _gen("industrial").meta
    assert no_zone["source_time_basis"] == "UTC (assumed; site clock unknown)"
