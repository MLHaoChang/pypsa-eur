"""The availability family: generator bounds over the year (ticket 02).

`generate_profile(family="availability", technology=<kind>)` makes one
column per generator for one target bound:

  * ``maintenance`` → p_max_pu: windows out of service (or derated);
  * ``must_run``    → p_min_pu: a floor, constant, seasonal or monthly;
  * ``derating``    → p_max_pu: capacity falling with the assumed ambient
    temperature above a threshold;
  * ``forced_outages`` → refused: the reliability study samples them from
    each asset's own rate and MTTR; one draw in the dispatch would be a
    single arbitrary scenario (gatekeeper review 2026-10-20).

Rules pinned here (same review): a month-day maintenance window applies in
every investment period (tiled like every profile); a dated window applies
only on a single-period network of that year, and is refused otherwise,
never shifted. Writing over an existing p_max_pu series replaces it and says
so in meta and a warning (undo keeps the old one). A must-run floor above
what its bus can absorb warns. On a committable unit a floor is a minimum
stable level when committed, not a must-run, and is labelled so. Wind and
solar generators are refused: an availability series would overwrite their
weather profile.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from services import dc_load_profile
from services.profile_gen.apply import (
    CarrierMismatchError,
    MIN_STABLE_WARNING,
    MUST_RUN_SINK_WARNING,
    REPLACES_SERIES_WARNING,
    assign_profile,
)
from services.profile_gen.availability import (
    ForcedOutagesNotWrittenError,
    InvalidAvailabilityParamsError,
    MaintenanceYearError,
    UnknownAvailabilityKindError,
)
from services.profile_gen.engine import generate_profile
from services.profile_gen.families import default_target
from services.pypsa_service import PyPSAService
from services.snapshot_index import _build_period_multiindex
from services.user_timeseries import _user_ts_meta

NOW = "2026-10-20T00:00:00+00:00"


def _gen(kind: str, params: dict | None = None, *, year: int = 2023,
         ids=("CHP",)):
    return generate_profile(points=list(ids), technology=kind,
                            params=dict(params or {}), year=year, now=NOW,
                            family="availability")


def _at(s: pd.Series, ts: str) -> float:
    return float(s.loc[pd.Timestamp(ts)])


def _ctx():
    return PyPSAService.get_active_context()


def _net(year: int = 2023, *, carrier: str = "gas", committable: bool = False,
         load_mw: float = 50.0):
    n = PyPSAService.get_network()
    idx = pd.date_range(f"{year}-01-01", f"{year}-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("Carrier", carrier)
    n.add("Load", "L1", bus="B1", p_set=load_mw)
    n.add("Generator", "CHP", bus="B1", carrier=carrier, p_nom=100.0,
          committable=committable)
    return n


def _apply(res, component="CHP"):
    ct, attr = default_target(res.meta, None, None)
    return assign_profile(_ctx(), component, res.series[component], res.meta,
                          component_type=ct, attribute=attr, time_basis="UTC",
                          now=NOW)


# ── maintenance ─────────────────────────────────────────────────────────────

def test_a_month_day_window_takes_the_unit_out():
    res = _gen("maintenance", {"windows": [{"from": "07-03", "to": "07-16"}]})
    s = res.series["CHP"]
    assert len(s) == 8760
    assert _at(s, "2023-07-03T00:00") == 0.0
    assert _at(s, "2023-07-16T23:00") == 0.0
    assert _at(s, "2023-07-17T00:00") == 1.0
    assert _at(s, "2023-07-02T23:00") == 1.0
    assert res.meta["target"] == ["generators", "p_max_pu"]
    assert res.meta["provenance"] == "synthetic/assumed"


def test_a_window_can_derate_instead_of_stopping():
    s = _gen("maintenance", {"windows": [{"from": "03-01", "to": "03-01",
                                          "available": 0.5}]}).series["CHP"]
    assert _at(s, "2023-03-01T12:00") == 0.5


def test_a_window_may_wrap_the_new_year():
    s = _gen("maintenance", {"windows": [{"from": "12-30", "to": "01-02"}]}).series["CHP"]
    assert _at(s, "2023-12-31T12:00") == 0.0
    assert _at(s, "2023-01-01T12:00") == 0.0
    assert _at(s, "2023-01-03T00:00") == 1.0


def test_a_seasonal_rule_places_n_weeks_mid_season():
    s = _gen("maintenance", {"rule": {"weeks": 2, "season": "summer"}}).series["CHP"]
    out = s[s == 0.0]
    assert len(out) == 14 * 24
    assert out.index.min().month in (7, 8)


@pytest.mark.parametrize("params", [
    {"windows": [{"from": "07-20", "to": "07-03"}]},     # not wrapping, reversed
    {"windows": [{"from": "13-01", "to": "13-02"}]},
    {"windows": [{"from": "07-01", "to": "07-02", "available": 1.5}]},
    {"rule": {"weeks": 60, "season": "summer"}},
    {"rule": {"weeks": 2, "season": "monsoon"}},
    {},
    {"windows": "07-01"},
])
def test_bad_maintenance_parameters_are_typed_refusals(params):
    with pytest.raises(InvalidAvailabilityParamsError):
        _gen("maintenance", params)


def test_month_day_windows_tile_into_every_period():
    n = PyPSAService.get_network()
    idx = [pd.date_range(f"{y}-01-01", f"{y}-12-31T23:00", freq="h")
           for y in (2030, 2031)]
    n.set_snapshots(_build_period_multiindex([2030, 2031], idx))
    n.add("Bus", "B1")
    n.add("Carrier", "gas")
    n.add("Generator", "CHP", bus="B1", carrier="gas", p_nom=100.0)
    res = _gen("maintenance", {"windows": [{"from": "07-03", "to": "07-03"}]}, year=2030)
    _apply(res)
    col = n.generators_t.p_max_pu["CHP"]
    assert float(col.loc[(2030, pd.Timestamp("2030-07-03T12:00"))]) == 0.0
    assert float(col.loc[(2031, pd.Timestamp("2031-07-03T12:00"))]) == 0.0


def test_a_dated_window_in_the_source_year_applies():
    _net(2023)
    res = _gen("maintenance", {"windows": [{"from": "2023-07-03", "to": "2023-07-04"}]})
    _apply(res)
    assert _user_ts_meta[("generators", "p_max_pu", "CHP")]["windows"][0]["from"] == "2023-07-03"


def test_a_dated_window_outside_the_source_year_is_refused():
    with pytest.raises(MaintenanceYearError, match="2024"):
        _gen("maintenance", {"windows": [{"from": "2024-07-03", "to": "2024-07-04"}]})


def test_a_dated_window_is_refused_on_a_multi_period_network():
    n = PyPSAService.get_network()
    idx = [pd.date_range(f"{y}-01-01", f"{y}-12-31T23:00", freq="h")
           for y in (2030, 2031)]
    n.set_snapshots(_build_period_multiindex([2030, 2031], idx))
    n.add("Bus", "B1")
    n.add("Carrier", "gas")
    n.add("Generator", "CHP", bus="B1", carrier="gas", p_nom=100.0)
    res = _gen("maintenance", {"windows": [{"from": "2030-07-03", "to": "2030-07-04"}]},
               year=2030)
    with pytest.raises(MaintenanceYearError, match="period"):
        _apply(res)


# ── forced outages ──────────────────────────────────────────────────────────

def test_forced_outages_are_refused_with_the_reason():
    with pytest.raises(ForcedOutagesNotWrittenError, match="reliability study"):
        _gen("forced_outages", {"rate": 0.05})


def test_an_unknown_kind_lists_the_known_ones():
    with pytest.raises(UnknownAvailabilityKindError, match="maintenance"):
        _gen("strike")


# ── must-run ────────────────────────────────────────────────────────────────

def test_a_constant_must_run_floor_lands_on_p_min_pu():
    n = _net()
    res = _gen("must_run", {"share": 0.3})
    assert res.meta["target"] == ["generators", "p_min_pu"]
    out = _apply(res)
    assert out["attribute"] == "p_min_pu"
    assert float(n.generators_t.p_min_pu["CHP"].iloc[0]) == pytest.approx(0.3)
    assert _user_ts_meta[("generators", "p_min_pu", "CHP")]["meaning"] == "must-run floor"


def test_a_seasonal_must_run_is_higher_in_winter():
    s = _gen("must_run", {"winter_share": 0.6, "summer_share": 0.1}).series["CHP"]
    assert s["2023-01"].mean() > s["2023-07"].mean()
    assert float(s.max()) <= 0.6 + 1e-9 and float(s.min()) >= 0.1 - 1e-9


def test_monthly_must_run_minima():
    shares = [0.5] * 3 + [0.2] * 6 + [0.5] * 3
    s = _gen("must_run", {"monthly": shares}).series["CHP"]
    assert _at(s, "2023-02-10T05:00") == 0.5
    assert _at(s, "2023-06-10T05:00") == 0.2


def test_a_floor_above_what_the_bus_can_take_warns():
    # 0.8 x 100 MW = 80 MW of must-run onto a bus with a 50 MW load and
    # nothing else to absorb it.
    _net(load_mw=50.0)
    out = _apply(_gen("must_run", {"share": 0.8}))
    warn = [w for w in out["warnings"] if w.startswith(MUST_RUN_SINK_WARNING)]
    assert warn and "80" in warn[0] and "50" in warn[0]


def test_a_floor_the_bus_can_take_does_not_warn():
    n = _net(load_mw=50.0)
    n.add("Bus", "B2")
    n.add("Link", "export", bus0="B1", bus1="B2", p_nom=40.0)
    out = _apply(_gen("must_run", {"share": 0.8}))
    assert not any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_on_a_committable_unit_a_floor_is_a_min_stable_level():
    _net(committable=True)
    out = _apply(_gen("must_run", {"share": 0.3}))
    meta = _user_ts_meta[("generators", "p_min_pu", "CHP")]
    assert meta["meaning"] == "min stable level when committed"
    assert any(w.startswith(MIN_STABLE_WARNING) for w in out["warnings"])


# ── derating ────────────────────────────────────────────────────────────────

def test_derating_follows_the_dc_models_ambient():
    params = {"threshold_c": 15.0, "per_k": 0.01, "floor": 0.8}
    res = _gen("derating", params)
    s = res.series["CHP"]
    temp = dc_load_profile.ambient_c(SimpleNamespace(**res.meta["ambient"]), s.index)
    expected = np.clip(1.0 - 0.01 * np.maximum(0.0, temp - 15.0), 0.8, 1.0)
    assert np.allclose(s.to_numpy(), expected)
    assert s["2023-07"].mean() < s["2023-01"].mean()


def test_derating_records_its_clock():
    res = _gen("derating", {"timezone": "Europe/Berlin"})
    assert res.meta["source_time_basis"] == "UTC (from Europe/Berlin wall clock)"


# ── the apply path ──────────────────────────────────────────────────────────

def test_writing_over_an_existing_p_max_pu_series_replaces_it_and_says_so():
    n = _net()
    n.generators_t.p_max_pu = pd.DataFrame(
        {"CHP": pd.Series(0.9, index=n.snapshots)})
    out = _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04"}]}))
    assert any(w.startswith(REPLACES_SERIES_WARNING) for w in out["warnings"])
    meta = _user_ts_meta[("generators", "p_max_pu", "CHP")]
    assert meta["replaced_series"] == "network series"


def test_regenerating_the_same_family_replaces_quietly():
    _net()
    _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04"}]}))
    out = _apply(_gen("maintenance", {"windows": [{"from": "08-03", "to": "08-04"}]}))
    assert not any(w.startswith(REPLACES_SERIES_WARNING) for w in out["warnings"])
    assert _user_ts_meta[("generators", "p_max_pu", "CHP")]["replaced_series"] == "availability profile"


@pytest.mark.parametrize("carrier", ["onwind", "solar"])
def test_wind_and_solar_are_refused(carrier):
    _net(carrier=carrier)
    with pytest.raises(CarrierMismatchError, match="weather profile"):
        _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04"}]}))


# ── review round 1 ──────────────────────────────────────────────────────────

def test_default_derating_derates_on_the_default_ambient():
    s = _gen("derating").series["CHP"]
    assert float(s.min()) < 1.0


def test_an_explicit_target_cannot_turn_a_floor_into_a_cap():
    from services.profile_gen.apply import UnsupportedTargetError

    res = _gen("must_run", {"share": 0.3})
    with pytest.raises(UnsupportedTargetError, match="p_min_pu"):
        default_target(res.meta, "generators", "p_max_pu")


def test_a_profile_timezone_is_the_snapshot_clock_when_the_project_states_none():
    # Without commercial.timezone the snapshots are the site clock; a profile
    # drawn on a stated zone lands back on that zone's wall clock, once.
    n = _net()
    res = _gen("maintenance", {"windows": [{"from": "07-03", "to": "07-03"}],
                               "timezone": "Asia/Tokyo"})
    out = assign_profile(_ctx(), "CHP", res.series["CHP"], res.meta,
                         component_type="generators", attribute="p_max_pu", now=NOW)
    p = n.generators_t.p_max_pu["CHP"]
    assert _at(p, "2023-07-03T00:00") == 0.0 and _at(p, "2023-07-03T23:00") == 0.0
    assert _at(p, "2023-07-02T23:00") == 1.0 and _at(p, "2023-07-04T00:00") == 1.0
    assert out["time_basis"] == "Asia/Tokyo"
    assert not any(w.startswith("time_basis_assumed_utc") for w in out["warnings"])


def test_one_availability_kind_over_another_warns():
    _net()
    _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04"}]}))
    out = _apply(_gen("derating"))
    assert any(w.startswith(REPLACES_SERIES_WARNING) for w in out["warnings"])


def test_a_static_bound_that_is_not_the_default_is_replaced_with_a_warning():
    n = _net()
    n.generators.loc["CHP", "p_max_pu"] = 0.5
    out = _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04"}]}))
    assert any(w.startswith(REPLACES_SERIES_WARNING) for w in out["warnings"])
    assert _user_ts_meta[("generators", "p_max_pu", "CHP")]["replaced_series"] == "static value 0.5"


def test_a_link_closed_by_a_series_absorbs_nothing():
    n = _net(load_mw=50.0)
    n.add("Bus", "B2")
    n.add("Link", "export", bus0="B1", bus1="B2", p_nom=40.0)
    n.links_t.p_max_pu["export"] = pd.Series(0.0, index=n.snapshots)
    out = _apply(_gen("must_run", {"share": 0.8}))
    assert any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_a_two_way_link_into_the_bus_from_its_far_end_counts():
    n = _net(load_mw=50.0)
    n.add("Bus", "B2")
    n.add("Link", "tie", bus0="B2", bus1="B1", p_nom=40.0, p_min_pu=-1.0)
    out = _apply(_gen("must_run", {"share": 0.8}))
    assert not any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_other_floors_on_the_bus_add_up():
    n = _net(load_mw=50.0)
    n.add("Generator", "CHP2", bus="B1", carrier="gas", p_nom=100.0, p_min_pu=0.4)
    out = _apply(_gen("must_run", {"share": 0.4}))
    assert any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_an_extendable_unit_is_checked_at_its_largest_size():
    n = _net(load_mw=50.0)
    n.generators.loc["CHP", ["p_nom", "p_nom_extendable", "p_nom_max"]] = [0.0, True, 100.0]
    out = _apply(_gen("must_run", {"share": 0.8}))
    assert any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_lines_off_the_bus_count_as_sink():
    n = _net(load_mw=50.0)
    n.add("Bus", "B2")
    n.add("Line", "ln", bus0="B1", bus1="B2", s_nom=40.0, x=0.1)
    out = _apply(_gen("must_run", {"share": 0.8}))
    assert not any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_on_a_committable_unit_the_stored_kind_is_min_stable_level():
    _net(committable=True)
    _apply(_gen("must_run", {"share": 0.3}))
    assert _user_ts_meta[("generators", "p_min_pu", "CHP")]["kind"] == "min_stable_level"


@pytest.mark.parametrize("carrier, refused", [
    ("offwind-ac", True), ("solar rooftop", True), ("PV", True),
    ("pvc plant", False), ("gas", False)])
def test_the_weather_carrier_rule(carrier, refused):
    from services.profile_gen.families import is_weather_carrier

    assert is_weather_carrier(carrier) is refused
