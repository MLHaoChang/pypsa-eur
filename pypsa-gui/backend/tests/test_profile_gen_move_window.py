"""Moving a planned maintenance window (TSG 07).

``move_window {window?, by}`` moves one recorded outage window of a
``maintenance`` availability series by whole days. It rewrites the window's
dates in the recorded parameters and re-generates through the availability
generator; it never post-transforms the series. Gatekeeper conditions
(2026-10-28, #857):

  1. a replay re-applies the move relative to the params it regenerates
     from, never from the moved params (another weather year moves it there);
  2. moved MM-DD dates are computed in the source year's calendar (leap days);
  3. ``by`` is 1-365 days or 1-52 weeks, signed; anything else is refused;
  4. the refusals are ``invalid_refine_op``, a year refusal stays
     ``maintenance_year``;
  5. a rule window is converted to its explicit window, the row keeps the rule;
  6. an op recorded before the move keeps its own hours, it does not move;
  7. the stats name the window's dates before and after;
  8. a window with one dated and one MM-DD end moves both, and stays dated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.profile_gen.availability import MaintenanceYearError
from services.profile_gen.engine import generate_profile
from services.profile_gen.refine import (
    NO_WINDOW_TO_MOVE,
    InvalidRefineOpError,
    RefineReplayError,
    refine_series,
    replay,
)
from services.pypsa_service import PyPSAService

NOW = "2026-10-28T00:00:00+00:00"


def _ctx():
    return PyPSAService.get_active_context()


@pytest.fixture(autouse=True)
def _net():
    n = PyPSAService.get_network()
    idx = pd.date_range("2023-01-01", "2023-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "b")
    n.add("Generator", "CHP", bus="b", p_nom=10.0)
    return n


def _gen(params, *, kind="maintenance", year=2023):
    res = generate_profile(points=["CHP"], technology=kind, params=dict(params),
                           year=year, now=NOW, family="availability")
    return res.series["CHP"], res.meta


def _refine(series, meta, ops, attribute="p_max_pu"):
    return refine_series(_ctx(), series, meta, ops, component_type="generators",
                         attribute=attribute)


def _replay(series, meta, ops):
    return replay(_ctx(), series, meta, ops, component_type="generators",
                  attribute="p_max_pu")


def _day_mask(s, start, end):
    days = s.index.normalize()
    return (days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))


ONE = {"windows": [{"from": "06-01", "to": "06-07", "available": 0.0}]}
TWO = {"windows": [{"from": "06-01", "to": "06-07", "available": 0.0},
                   {"from": "09-01", "to": "09-03", "available": 0.5}]}


# ── the owner's sentence ────────────────────────────────────────────────────

def test_two_weeks_later_moves_only_that_outage():
    s, meta = _gen(ONE)
    out = _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    new = out.series
    old_w = _day_mask(s, "2023-06-01", "2023-06-07")
    new_w = _day_mask(s, "2023-06-15", "2023-06-21")
    assert (new[new_w] == 0.0).all() and (new[old_w] == 1.0).all()
    outside = ~(old_w | new_w)
    assert np.array_equal(new[outside].to_numpy(), s[outside].to_numpy())
    # It is the generator's output for the moved params, not a transform.
    direct, _ = _gen({"windows": [{"from": "06-15", "to": "06-21", "available": 0.0}]})
    assert new.to_numpy().tobytes() == direct.to_numpy().tobytes()
    assert out.meta["params"]["windows"][0]["from"] == "06-15"
    assert out.meta["windows"][0]["to"] == "06-21"
    row = out.meta["refinements"][-1]
    assert row["op"] == "move_window" and row["window"] == 0
    assert row["moved"] == {"from": ["06-01", "06-15"], "to": ["06-07", "06-21"]}


def test_weeks_and_earlier():
    s, meta = _gen(ONE)
    out = _refine(s, meta, [{"op": "move_window", "by": "-2w"}])
    assert out.meta["params"]["windows"][0] == {"from": "05-18", "to": "05-24",
                                                "available": 0.0}


def test_the_stats_name_both_dates():
    s, meta = _gen(ONE)
    st = _refine(s, meta, [{"op": "move_window", "by": "14d"}]).stats[0]
    assert st["window_before"] == {"from": "06-01", "to": "06-07"}
    assert st["window_after"] == {"from": "06-15", "to": "06-21"}
    assert st["hours_touched"] == 14 * 24


# ── determinism ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("params", [
    ONE,
    {"windows": [{"from": "12-20", "to": "12-30", "available": 0.2}]},
    {"windows": [{"from": "2023-03-01", "to": "2023-03-05", "available": 0.0}]},
])
def test_forth_and_back_is_bit_for_bit(params):
    s, meta = _gen(params)
    there = _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    back = _refine(there.series, there.meta, [{"op": "move_window", "by": "-14d"}])
    assert back.series.to_numpy().tobytes() == s.to_numpy().tobytes()
    assert back.meta["params"] == meta["params"]
    assert [r["op"] for r in back.meta["refinements"]] == ["move_window"] * 2


def test_a_new_year_crossing_wraps_inside_the_source_year():
    s, meta = _gen({"windows": [{"from": "12-20", "to": "12-28", "available": 0.0}]})
    out = _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    assert out.meta["params"]["windows"][0] == {"from": "01-03", "to": "01-11",
                                                "available": 0.0}
    out = _refine(s, meta, [{"op": "move_window", "by": "7d"}])
    w = out.meta["params"]["windows"][0]
    assert (w["from"], w["to"]) == ("12-27", "01-04")
    assert (out.series[_day_mask(s, "2023-12-27", "2023-12-31")] == 0).all()
    assert (out.series[_day_mask(s, "2023-01-01", "2023-01-04")] == 0).all()


def test_a_wrap_over_half_a_year_is_the_generators_refusal():
    s, meta = _gen({"windows": [{"from": "06-01", "to": "12-20", "available": 0.0}]})
    with pytest.raises(InvalidRefineOpError, match="half a year"):
        _refine(s, meta, [{"op": "move_window", "by": "14d"}])


# ── leap days (condition 2) ─────────────────────────────────────────────────

def test_a_move_can_land_on_the_leap_day():
    s, meta = _gen({"windows": [{"from": "02-15", "to": "02-15", "available": 0.0}]},
                   year=2024)
    out = _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    assert out.meta["params"]["windows"][0]["from"] == "02-29"
    assert (out.series[_day_mask(s, "2024-02-29", "2024-02-29")] == 0).all()


def test_a_leap_move_replayed_onto_a_common_year_moves_relatively():
    s, meta = _gen({"windows": [{"from": "02-15", "to": "02-15", "available": 0.0}]},
                   year=2024)
    rows = _refine(s, meta, [{"op": "move_window", "by": "14d"}]).meta["refinements"]
    s23, meta23 = _gen({"windows": [{"from": "02-15", "to": "02-15", "available": 0.0}]})
    out = _replay(s23, meta23, rows)
    assert out.meta["params"]["windows"][0]["from"] == "03-01"
    assert (out.series[_day_mask(s23, "2023-03-01", "2023-03-01")] == 0).all()


def test_forth_and_back_across_the_leap_day():
    s, meta = _gen({"windows": [{"from": "02-27", "to": "02-28", "available": 0.0}]},
                   year=2024)
    there = _refine(s, meta, [{"op": "move_window", "by": "2d"}])
    w = there.meta["params"]["windows"][0]
    assert (w["from"], w["to"]) == ("02-29", "03-01")
    back = _refine(there.series, there.meta, [{"op": "move_window", "by": "-2d"}])
    assert back.series.to_numpy().tobytes() == s.to_numpy().tobytes()


# ── replay (condition 1) ────────────────────────────────────────────────────

def test_replay_onto_another_year_moves_from_that_years_params():
    s, meta = _gen(ONE)
    rows = _refine(s, meta, [{"op": "move_window", "by": "14d"}]).meta["refinements"]
    s25, meta25 = _gen(ONE, year=2025)
    out = _replay(s25, meta25, rows)
    direct, _ = _gen({"windows": [{"from": "06-15", "to": "06-21", "available": 0.0}]},
                     year=2025)
    assert out.series.to_numpy().tobytes() == direct.to_numpy().tobytes()


def test_a_replayed_dated_move_into_another_year_names_the_op():
    s, meta = _gen({"windows": [{"from": "2023-06-01", "to": "2023-06-07",
                                 "available": 0.0}]})
    rows = _refine(s, meta, [{"op": "set_cap", "value": 0.9},
                             {"op": "move_window", "by": "14d"}]).meta["refinements"]
    s2, meta2 = _gen({"windows": [{"from": "2023-12-20", "to": "2023-12-25",
                                   "available": 0.0}]})
    with pytest.raises(RefineReplayError) as exc:
        _replay(s2, meta2, rows)
    assert exc.value.index == 1 and exc.value.op == "move_window"


# ── which window ────────────────────────────────────────────────────────────

def test_two_windows_without_an_index_are_refused_naming_both():
    s, meta = _gen(TWO)
    with pytest.raises(InvalidRefineOpError) as exc:
        _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    assert "06-01" in str(exc.value) and "09-01" in str(exc.value)


def test_an_index_picks_one_of_two():
    s, meta = _gen(TWO)
    out = _refine(s, meta, [{"op": "move_window", "window": 1, "by": "7d"}])
    assert out.meta["params"]["windows"][0]["from"] == "06-01"
    assert out.meta["params"]["windows"][1] == {"from": "09-08", "to": "09-10",
                                                "available": 0.5}


@pytest.mark.parametrize("window", [2, -1, True, "0", 0.0])
def test_a_bad_index_is_refused(window):
    s, meta = _gen(TWO)
    with pytest.raises(InvalidRefineOpError, match="window"):
        _refine(s, meta, [{"op": "move_window", "window": window, "by": "7d"}])


# ── by (condition 3) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("by", ["0d", "+0w", "366d", "53w", "14", 14, "1.5d", "14h",
                                True, None, "d", "--2d"])
def test_by_is_bounded(by):
    s, meta = _gen(ONE)
    with pytest.raises(InvalidRefineOpError, match="by"):
        _refine(s, meta, [{"op": "move_window", "by": by}])


@pytest.mark.parametrize("by", ["365d", "-365d", "52w", "+1d"])
def test_by_at_its_bounds(by):
    s, meta = _gen(ONE)
    _refine(s, meta, [{"op": "move_window", "by": by}])


# ── which series ────────────────────────────────────────────────────────────

def test_a_load_series_is_refused():
    load = generate_profile(points=["L"], technology="industrial",
                            params={"peak_mw": 5.0}, year=2023, now=NOW, family="load")
    with pytest.raises(InvalidRefineOpError, match=NO_WINDOW_TO_MOVE):
        refine_series(_ctx(), load.series["L"], load.meta,
                      [{"op": "move_window", "by": "14d"}],
                      component_type="loads", attribute="p_set")


def test_a_must_run_series_is_refused():
    s, meta = _gen({"share": 0.3}, kind="must_run")
    with pytest.raises(InvalidRefineOpError, match=NO_WINDOW_TO_MOVE):
        _refine(s, meta, [{"op": "move_window", "by": "14d"}], attribute="p_min_pu")


# ── dated windows ───────────────────────────────────────────────────────────

def test_a_dated_window_past_the_source_year_is_refused():
    s, meta = _gen({"windows": [{"from": "2023-12-20", "to": "2023-12-28",
                                 "available": 0.0}]})
    with pytest.raises(MaintenanceYearError) as exc:
        _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    assert "31 Dec 2023" in str(exc.value) and "never shifted" in str(exc.value)
    assert isinstance(exc.value, InvalidRefineOpError) is False


def test_a_dated_window_before_the_source_year_is_refused():
    s, meta = _gen({"windows": [{"from": "2023-01-03", "to": "2023-01-05",
                                 "available": 0.0}]})
    with pytest.raises(MaintenanceYearError, match="the first day is 1 Jan 2023"):
        _refine(s, meta, [{"op": "move_window", "by": "-7d"}])


def test_mixed_ends_both_move_and_stay_dated():
    s, meta = _gen({"windows": [{"from": "2023-06-01", "to": "06-07", "available": 0.0}]})
    out = _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    assert out.meta["params"]["windows"][0] == {"from": "2023-06-15", "to": "06-21",
                                                "available": 0.0}
    assert out.meta["windows"][0]["dated"] is True
    s, meta = _gen({"windows": [{"from": "12-20", "to": "2023-12-28", "available": 0.0}]})
    with pytest.raises(MaintenanceYearError):
        _refine(s, meta, [{"op": "move_window", "by": "7d"}])


# ── rule (condition 5) ──────────────────────────────────────────────────────

def test_a_rule_is_moved_as_its_window_and_comes_back():
    rule = {"rule": {"weeks": 2, "season": "summer"}}
    s, meta = _gen(rule)
    there = _refine(s, meta, [{"op": "move_window", "by": "14d"}])
    assert "rule" not in there.meta["params"]
    assert there.meta["params"]["windows"] == [{"from": "08-08", "to": "08-21",
                                                "available": 0.0}]
    row = there.meta["refinements"][-1]
    assert row["converted_from_rule"] == {"weeks": 2, "season": "summer"}
    back = _refine(there.series, there.meta, [{"op": "move_window", "by": "-14d"}])
    assert np.array_equal(back.series.to_numpy(), s.to_numpy())
    # The row keeps the rule: a replay converts it again from the rule.
    out = _replay(s, meta, there.meta["refinements"])
    assert out.series.to_numpy().tobytes() == there.series.to_numpy().tobytes()


# ── earlier ops (condition 6) ───────────────────────────────────────────────

def test_an_earlier_cap_is_kept_over_the_regenerated_series():
    s, meta = _gen(ONE)
    out = _refine(s, meta, [{"op": "set_cap", "value": 0.8},
                            {"op": "move_window", "by": "14d"}])
    assert out.series.max() == 0.8
    assert (out.series[_day_mask(s, "2023-06-15", "2023-06-21")] == 0).all()
    assert (out.series[_day_mask(s, "2023-06-01", "2023-06-07")] == 0.8).all()


def test_an_earlier_refinement_is_kept_when_a_refined_series_is_moved():
    s, meta = _gen(ONE)
    capped = _refine(s, meta, [{"op": "set_cap", "value": 0.8}])
    out = _refine(capped.series, capped.meta, [{"op": "move_window", "by": "14d"}])
    assert (out.series[_day_mask(s, "2023-06-01", "2023-06-07")] == 0.8).all()
    assert [r["op"] for r in out.meta["refinements"]] == ["set_cap", "move_window"]


def test_an_earlier_hour_window_keeps_its_hours():
    params = {**ONE, "timezone": "UTC"}
    s, meta = _gen(params)
    scale = {"op": "scale_window", "hours": [0, 6], "months": [6], "factor": 0.5}
    out = _refine(s, meta, [scale, {"op": "move_window", "by": "14d"}])
    moved, mmeta = _gen({"windows": [{"from": "06-15", "to": "06-21", "available": 0.0}],
                         "timezone": "UTC"})
    expected = _refine(moved, mmeta, [scale]).series
    assert out.series.to_numpy().tobytes() == expected.to_numpy().tobytes()
    # June's early hours outside both windows stay scaled: the window moved, not them.
    assert out.series[pd.Timestamp("2023-06-10T03:00")] == 0.5
