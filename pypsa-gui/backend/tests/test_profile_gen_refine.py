"""Refining a generated series by deterministic ops (ticket 04).

`refine.refine_series` runs a closed list of ops over an artifact's
source-year series in the target's own units, and returns the new series,
its meta (the op list appended to ``refinements``) and per-op stats. The
model emits ops, never numbers for the series. Gatekeeper conditions
(2026-10-20):

  1. ``scale_peak_to`` measures the peak on the series as apply aligns it
     to the model's snapshots;
  2. a later op that breaks an earlier target warns ``earlier_target_broken``;
  3. a window is on the site's clock (commercial timezone, else the
     profile's own), DST-aware; with neither, ``window_clock_unknown``;
  4. after clipping, the stats report the energy actually kept;
  5. ``keep_energy: true`` is refused in v1;
  6. a replay refuses naming the op and its index, and when the target's
     units changed; ``fraction_of: connection`` is resolved server-side from
     the point-of-connection link, recorded, and warned on when it changes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from services.profile_gen.engine import generate_profile
from services.profile_gen.refine import (
    CONNECTION_CHANGED_WARNING,
    EARLIER_TARGET_WARNING,
    InvalidRefineOpError,
    RefineReplayError,
    WindowClockUnknownError,
    refine_series,
    replay,
)
from services.pypsa_service import PyPSAService

NOW = "2026-10-20T00:00:00+00:00"


def _ctx():
    return PyPSAService.get_active_context()


def _net(*, tz=None, poc_mw=100.0):
    n = PyPSAService.get_network()
    idx = pd.date_range("2025-01-01", "2025-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Load", "plant", bus="site")
    n.add("Link", "poc", bus0="grid", bus1="site", p_nom=poc_mw)
    state = _ctx().solver_state.setdefault("solver_config", SimpleNamespace())
    state.commercial = {"poc_link": "poc", "timezone": tz}
    return n


def _load(archetype="industrial", params=None):
    res = generate_profile(points=["plant"], technology=archetype,
                           params={"peak_mw": 80.0, **(params or {})},
                           year=2025, now=NOW, family="load")
    return res.series["plant"], res.meta


def _absolute(series, meta):
    return series * float(meta.get("scale_mw") or 1.0)


def _refine(series, meta, ops, **kw):
    return refine_series(_ctx(), series, meta, ops, component_type="loads",
                         attribute="p_set", **kw)


# ── the owner's sentence ────────────────────────────────────────────────────

@pytest.mark.parametrize("archetype", ["data_centre", "industrial", "residential"])
def test_the_owners_sentence_as_two_ops(archetype):
    # "the peak reaches 80 %, it should reach 75 %; and 30 % more demand
    # between 05 and 10" — window first, then the peak, so both hold.
    _net(tz="Europe/Berlin", poc_mw=100.0)
    s, meta = _load(archetype, {"timezone": "Europe/Berlin"})
    out = _refine(s, meta, [
        {"op": "scale_window", "hours": [5, 10], "factor": 1.3},
        {"op": "scale_peak_to", "fraction": 0.75, "of": "connection"},
    ])
    new = out.series
    assert float(new.max()) == pytest.approx(75.0)
    assert out.meta["unit"] == "MW"
    assert [r["op"] for r in out.meta["refinements"]] == ["scale_window", "scale_peak_to"]
    assert out.meta["refinements"][1]["resolved_mw"] == pytest.approx(75.0)
    assert out.meta["refinements"][1]["connection"]["source"] == "links.p_nom of 'poc'"
    assert out.meta["provenance"].endswith("user-refined")
    assert not out.warnings


def test_a_window_after_the_peak_target_breaks_it_and_says_so():
    _net(tz="Europe/Berlin")
    s, meta = _load("industrial", {"timezone": "Europe/Berlin", "shifts": 3})
    out = _refine(s, meta, [
        {"op": "scale_peak_to", "mw": 70.0},
        {"op": "scale_window", "hours": [5, 10], "factor": 1.3},
    ])
    warn = [w for w in out.warnings if w.startswith(EARLIER_TARGET_WARNING)]
    assert warn and "op 0" in warn[0] and "op 1" in warn[0]


# ── the window clock ────────────────────────────────────────────────────────

def test_a_local_window_lands_on_utc_hours_through_dst():
    _net(tz="Europe/Oslo")
    s, meta = _load("industrial", {"shifts": "continuous",
                                   "timezone": "Europe/Oslo"})
    out = _refine(s, meta, [{"op": "scale_window", "hours": [5, 10], "factor": 2.0}])
    new = out.series

    def doubled(day):
        hours = new.loc[day]
        return [int(t.hour) for t in hours.index[hours > hours.min() + 1e-9]]

    assert doubled("2025-01-08") == [4, 5, 6, 7, 8]      # CET
    assert doubled("2025-07-09") == [3, 4, 5, 6, 7]      # CEST


def test_a_window_with_no_known_clock_is_refused():
    _net(tz=None)
    s, meta = _load("industrial")
    with pytest.raises(WindowClockUnknownError):
        _refine(s, meta, [{"op": "scale_window", "hours": [5, 10], "factor": 1.3}])


# ── op maths, bounds and stats ──────────────────────────────────────────────

def test_the_peak_is_measured_on_the_aligned_series():
    # A leap source on a non-leap model: Feb 29 is dropped at apply, so a
    # peak only on Feb 29 is not the peak the model sees.
    _net()
    idx = pd.date_range("2024-01-01", "2024-12-31T23:00", freq="h")
    s = pd.Series(10.0, index=idx)
    s.loc["2024-02-29T12:00"] = 50.0
    meta = {"family": "load", "unit": "MW", "resolution": "hourly", "weather_year": 2024}
    out = _refine(s, meta, [{"op": "scale_peak_to", "mw": 20.0}])
    assert float(out.series.loc["2024-03-01T12:00"]) == pytest.approx(20.0)
    assert out.stats[0]["peak_before"] == pytest.approx(10.0)


def test_rescale_energy_reports_the_requested_and_the_kept_energy():
    _net()
    s, meta = _load()
    out = _refine(s, meta, [{"op": "set_cap", "value": 60.0},
                            {"op": "rescale_energy", "mwh": 600000.0}])
    st = out.stats[1]
    assert st["energy_requested_mwh"] == pytest.approx(600000.0)
    assert st["energy_after_mwh"] == pytest.approx(float(out.series.sum()))
    assert st["hours_clipped"] == 0  # a cap is a set op, not a bound


def test_rescale_into_the_bound_reports_the_energy_actually_kept():
    _net()
    meta = {"family": "availability", "unit": "per_unit", "resolution": "hourly",
            "weather_year": 2025, "target": ["generators", "p_max_pu"]}
    idx = pd.date_range("2025-01-01", "2025-12-31T23:00", freq="h")
    s = pd.Series(0.9, index=idx)
    out = refine_series(_ctx(), s, meta, [{"op": "scale_peak_to", "value": 1.2}],
                        component_type="generators", attribute="p_max_pu")
    assert float(out.series.max()) == 1.0
    assert out.stats[0]["hours_clipped"] == len(idx)


def test_shift_rolls_and_smooth_keeps_energy():
    _net()
    s, meta = _load()
    out = _refine(s, meta, [{"op": "shift", "hours": 2},
                            {"op": "smooth", "window_h": 3}])
    assert float(out.series.sum()) == pytest.approx(float(_absolute(s, meta).sum()))


@pytest.mark.parametrize("op", [
    {"op": "scale_peak_to", "mw": 70.0, "keep_energy": True},
    {"op": "rescale_energy", "mwh": -1.0},
    {"op": "set_floor", "value": -5.0},
    {"op": "smooth", "window_h": 1},
    {"op": "shift", "hours": 1.5},
    {"op": "scale_window", "hours": [10, 5], "factor": 1.3},
    {"op": "teleport"},
])
def test_bad_ops_are_typed_refusals(op):
    _net(tz="UTC")
    s, meta = _load()
    with pytest.raises(InvalidRefineOpError):
        _refine(s, meta, [op])


def test_rescale_energy_is_refused_on_a_price():
    _net()
    idx = pd.date_range("2025-01-01", "2025-12-31T23:00", freq="h")
    meta = {"family": "series", "unit": "EUR/MWh", "resolution": "hourly",
            "weather_year": 2025, "target": ["generators", "marginal_cost"]}
    with pytest.raises(InvalidRefineOpError, match="rescale_energy"):
        refine_series(_ctx(), pd.Series(50.0, index=idx), meta,
                      [{"op": "rescale_energy", "mwh": 1.0}],
                      component_type="generators", attribute="marginal_cost")


# ── replay ──────────────────────────────────────────────────────────────────

def test_replay_reproduces_the_refined_series():
    _net(tz="Europe/Berlin")
    s, meta = _load("industrial", {"timezone": "Europe/Berlin"})
    first = _refine(s, meta, [{"op": "scale_window", "hours": [5, 10], "factor": 1.3},
                              {"op": "scale_peak_to", "fraction": 0.75, "of": "connection"}])
    again = replay(_ctx(), s, meta, first.meta["refinements"],
                   component_type="loads", attribute="p_set")
    pd.testing.assert_series_equal(again.series, first.series)


def test_replay_keeps_the_recorded_connection_and_warns_when_it_moved():
    n = _net(tz="Europe/Berlin", poc_mw=100.0)
    s, meta = _load("industrial", {"timezone": "Europe/Berlin"})
    first = _refine(s, meta, [{"op": "scale_peak_to", "fraction": 0.75, "of": "connection"}])
    n.links.loc["poc", "p_nom"] = 120.0
    again = replay(_ctx(), s, meta, first.meta["refinements"],
                   component_type="loads", attribute="p_set")
    assert float(again.series.max()) == pytest.approx(75.0)
    assert any(w.startswith(CONNECTION_CHANGED_WARNING) for w in again.warnings)


def test_replay_refuses_naming_the_op_and_its_index():
    _net(tz=None)
    s, meta = _load()
    ops = [{"op": "scale_peak_to", "mw": 70.0, "target": ["loads", "p_set"]},
           {"op": "scale_window", "hours": [5, 10], "factor": 1.3,
            "target": ["loads", "p_set"]}]
    with pytest.raises(RefineReplayError, match="op 1.*scale_window"):
        replay(_ctx(), s, meta, ops, component_type="loads", attribute="p_set")


def test_replay_refuses_when_the_target_changed():
    _net()
    s, meta = _load()
    ops = [{"op": "set_cap", "value": 0.5, "target": ["generators", "p_max_pu"]}]
    with pytest.raises(RefineReplayError, match="p_max_pu"):
        replay(_ctx(), s, meta, ops, component_type="loads", attribute="p_set")


# ── the server resolves the connection ──────────────────────────────────────

@pytest.mark.parametrize("smuggled", [{"resolved_mw": 500.0},
                                      {"connection": {"value": 1000.0, "hash": "x"}}])
def test_refine_refuses_a_caller_supplied_resolution(smuggled):
    _net(poc_mw=100.0)
    s, meta = _load()
    with pytest.raises(InvalidRefineOpError, match="recorded by the server"):
        _refine(s, meta, [{"op": "scale_peak_to", "fraction": 0.75, "of": "connection",
                           **smuggled}])


def test_a_fraction_names_what_it_is_a_fraction_of():
    _net()
    s, meta = _load()
    with pytest.raises(InvalidRefineOpError, match="of must be current_peak or connection"):
        _refine(s, meta, [{"op": "scale_peak_to", "fraction": 0.75}])


def test_replay_refuses_a_non_positive_recorded_mw():
    _net(tz="Europe/Berlin")
    s, meta = _load("industrial", {"timezone": "Europe/Berlin"})
    first = _refine(s, meta, [{"op": "scale_peak_to", "fraction": 0.75, "of": "connection"}])
    ops = [{**first.meta["refinements"][0], "resolved_mw": -5.0}]
    with pytest.raises(RefineReplayError, match="op 0.*scale_peak_to"):
        replay(_ctx(), s, meta, ops, component_type="loads", attribute="p_set")


def test_replay_keeps_the_recorded_mw_when_the_connection_link_is_gone():
    n = _net(tz="Europe/Berlin", poc_mw=100.0)
    s, meta = _load("industrial", {"timezone": "Europe/Berlin"})
    first = _refine(s, meta, [{"op": "scale_peak_to", "fraction": 0.75, "of": "connection"}])
    n.remove("Link", "poc")
    again = replay(_ctx(), s, meta, first.meta["refinements"],
                   component_type="loads", attribute="p_set")
    assert float(again.series.max()) == pytest.approx(75.0)
    assert any(w.startswith(CONNECTION_CHANGED_WARNING) for w in again.warnings)
