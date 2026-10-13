"""I3b of the campus island plan (2026-10-07): island critical hours.

``island_metrics`` reads the I3a tables (``island_unit_hourly.csv``,
``island_hourly.csv``) and the campus hourly table, and gives one row per
(period, hour). ``select_campus_hours`` ranks its columns with the base
criteria when they are present. Minimum criteria are transformed to
(max − x) within each period, so the values stay ≥ 0 and "no variation"
still means "nothing to rank".

Each hand series below makes one criterion pick a known hour.
"""
import math

import pandas as pd
import pytest

from gridspine.ranking.campus import (
    ISLAND_CRITERIA,
    island_metrics,
    select_campus_hours,
)

NAN = math.nan
HOURS = 6
P = 2030


def _campus():
    return {"campus": {"units": {
        "B1": {"kind": "bess", "bus": "mv", "island": {"control": "gfm_droop"}},
        "B2": {"kind": "bess", "bus": "mv", "island": {"control": "gfl_pq"}},
        "PV": {"kind": "pv", "bus": "mv", "island": {"control": "gfl_pq"}},
        "PVF": {"kind": "pv", "bus": "mv", "island": {"control": "gfm_vsm"}},
        "L": {"kind": "load", "bus": "mv"},
    }}}


def _series(**over):
    base = {"dp_up": [5.0] * HOURS, "dp_down": [1.0] * HOURS, "inertia": [50.0] * HOURS,
            "crit": [10.0] * HOURS, "b1_up": [8.0] * HOURS, "b2_up": [1.0] * HOURS,
            "soc1": [20.0] * HOURS, "soc2": [5.0] * HOURS, "pv": [2.0] * HOURS,
            "pvf": [0.0] * HOURS, "load": [20.0] * HOURS}
    base.update(over)
    return base


def _tables(s, scenarios=("seamless",)):
    hours = list(range(HOURS))
    hourly = pd.concat([pd.DataFrame({
        "period": P, "hour": hours, "scenario": sc, "dp_up_mw": s["dp_up"],
        "dp_down_mw": s["dp_down"], "inertia_mws": s["inertia"], "k_mw_per_hz": 5.0,
        "critical_load_mw": s["crit"]}) for sc in scenarios], ignore_index=True)
    unit = pd.concat([
        pd.DataFrame({"period": P, "hour": hours, "unit_id": "B1", "soc_start_mwh": s["soc1"],
                      "online": 1.0, "headroom_up_mw": s["b1_up"], "headroom_down_mw": 1.0,
                      "p_ups_it_mw": NAN}),
        pd.DataFrame({"period": P, "hour": hours, "unit_id": "B2", "soc_start_mwh": s["soc2"],
                      "online": 1.0, "headroom_up_mw": s["b2_up"], "headroom_down_mw": 1.0,
                      "p_ups_it_mw": NAN}),
    ], ignore_index=True)
    campus_hourly = pd.concat([
        pd.DataFrame({"unit_id": "PV", "period": P, "hour": hours, "p_mw": s["pv"], "status": 1}),
        pd.DataFrame({"unit_id": "PVF", "period": P, "hour": hours, "p_mw": s["pvf"], "status": 1}),
        pd.DataFrame({"unit_id": "L", "period": P, "hour": hours,
                      "p_mw": [-x for x in s["load"]], "status": 1}),
    ], ignore_index=True)
    return campus_hourly, unit, hourly


def _picks(series, k=1, scenarios=("seamless",)):
    m = island_metrics(*_tables(series, scenarios), _campus())
    sel = select_campus_hours(m, k=k)
    return {r: sorted(int(h) for h, rs in zip(sel["hour"], sel["reasons"]) if r in rs)
            for r, _ in ISLAND_CRITERIA}


def _bump(value, at, base):
    out = [base] * HOURS
    out[at] = value
    return out


# ── each criterion picks its hour ──────────────────────────────────────────


@pytest.mark.parametrize("reason,key,value,base,hour", [
    ("max_island_dp_up", "dp_up", 9.0, 5.0, 3),
    ("max_island_dp_down", "dp_down", 4.0, 1.0, 2),
    ("max_island_load", "crit", 14.0, 10.0, 5),
])
def test_each_maximum_criterion_picks_its_hour(reason, key, value, base, hour):
    assert _picks(_series(**{key: _bump(value, hour, base)}))[reason] == [hour]


@pytest.mark.parametrize("reason,key,value,base,hour", [
    ("min_island_inertia", "inertia", 30.0, 50.0, 4),
    ("min_gfm_headroom", "b1_up", 2.0, 8.0, 1),
    ("min_stored_energy", "soc1", 3.0, 20.0, 0),
])
def test_each_minimum_criterion_picks_its_lowest_hour(reason, key, value, base, hour):
    assert _picks(_series(**{key: _bump(value, hour, base)}))[reason] == [hour]


def test_the_gfl_share_peaks_at_low_load():
    """PV 2 MW throughout; the load falls to 5 MW at hour 2, so the GFL
    share 2/5 is the largest there. A grid-forming PV does not count."""
    picks = _picks(_series(load=_bump(5.0, 2, 20.0), pvf=_bump(9.0, 4, 0.0)))
    assert picks["max_gfl_share"] == [2]


def test_only_gfm_storage_counts_as_gfm_headroom():
    """The GFL bess B2 dips at hour 3; the GFM headroom criterion ignores it."""
    picks = _picks(_series(b2_up=_bump(-5.0, 3, 1.0), b1_up=_bump(4.0, 1, 8.0)))
    assert picks["min_gfm_headroom"] == [1]


def test_stored_energy_sums_every_bess():
    """B1 is flat; B2 empties at hour 5: the total is lowest there."""
    picks = _picks(_series(soc2=_bump(0.0, 5, 5.0)))
    assert picks["min_stored_energy"] == [5]


def test_the_steps_take_the_worst_scenario():
    """Two scenarios with the same step; the max is per hour across them."""
    assert _picks(_series(dp_up=_bump(9.0, 3, 5.0)),
                  scenarios=("seamless", "ups_bridged"))["max_island_dp_up"] == [3]


def test_the_minimum_inertia_takes_the_worst_scenario():
    """The island table has one M per scenario; the lowest one counts."""
    campus_hourly, unit, hourly = _tables(_series(), scenarios=("seamless", "ups_bridged"))
    low = (hourly["scenario"] == "ups_bridged") & (hourly["hour"] == 4)
    hourly.loc[low, "inertia_mws"] = 10.0
    m = island_metrics(campus_hourly, unit, hourly, _campus())
    sel = select_campus_hours(m, k=1)
    assert [int(h) for h, rs in zip(sel["hour"], sel["reasons"])
            if "min_island_inertia" in rs] == [4]


# ── the (max − x) transform ────────────────────────────────────────────────


def test_a_minimum_criterion_is_transformed_to_max_minus_x():
    m = island_metrics(*_tables(_series(inertia=[50.0, 30.0, 40.0, 50.0, 50.0, 45.0])),
                       _campus())
    assert m["island_inertia_short_mws"].tolist() == pytest.approx(
        [0.0, 20.0, 10.0, 0.0, 0.0, 5.0])
    assert (m[[c for _, c in ISLAND_CRITERIA]] >= 0).all().all()


def test_the_transform_is_per_period():
    """2040's inertia is higher everywhere; its own lowest hour is still picked."""
    a, b, c = _tables(_series(inertia=_bump(30.0, 1, 50.0)))
    a2, b2, c2 = _tables(_series(inertia=_bump(90.0, 4, 100.0)))
    for df in (a2, b2, c2):
        df["period"] = 2040
    m = island_metrics(pd.concat([a, a2]), pd.concat([b, b2]), pd.concat([c, c2]), _campus())
    sel = select_campus_hours(m, k=1)
    picked = {(int(p), int(h)) for p, h, rs in zip(sel["period"], sel["hour"], sel["reasons"])
              if "min_island_inertia" in rs}
    assert picked == {(2030, 1), (2040, 4)}


# ── nothing to rank ─────────────────────────────────────────────────────────


def test_a_constant_series_picks_nothing():
    """Flat inertia, headroom and energy (0 after the transform); steps
    never above 0; no PV. Only the critical load, a flat positive maximum
    criterion, still picks, exactly as a flat positive base criterion does."""
    picks = _picks(_series(dp_up=[-1.0] * HOURS, dp_down=[0.0] * HOURS, pv=[0.0] * HOURS))
    assert {r for r, h in picks.items() if h} == {"max_island_load"}


def test_ties_are_spread():
    """Two hours tie for the lowest inertia; k = 1 takes one of them, k = 2
    both, as the base criteria spread ties."""
    s = _series(inertia=[50.0, 30.0, 50.0, 30.0, 50.0, 50.0])
    assert len(_picks(s, k=1)["min_island_inertia"]) == 1
    assert _picks(s, k=2)["min_island_inertia"] == [1, 3]


def test_without_island_columns_the_selection_is_the_base_one():
    """A campus with no island tables ranks exactly as before."""
    m = pd.DataFrame({"import_mw": [1.0, 3.0, 2.0], "export_mw": 0.0, "consumption_mw": 1.0,
                      "renewable_mw": 0.0},
                     index=pd.MultiIndex.from_product([[P], [0, 1, 2]], names=["period", "hour"]))
    sel = select_campus_hours(m, k=1)
    assert sel.to_dict("list") == {"period": [P, P], "hour": [0, 1],
                                   "reasons": [["max_consumption_mw"], ["max_import_mw"]]}
