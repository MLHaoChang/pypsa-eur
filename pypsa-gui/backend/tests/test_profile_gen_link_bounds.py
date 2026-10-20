"""Availability bounds on Links: process shutdowns and floors (ticket 03 E).

Link bounds act on p0 (the bus0 flow). Gatekeeper conditions (2026-10-20):

  * p_min_pu on a link may be in [-1, 1];
  * a shutdown window on a two-way link (p_min_pu < 0) closes both
    directions: its reverse bound is scaled by the same availability;
  * a floor on a two-way link is refused (it would forbid reverse flow), and
    so is a floor on the point-of-connection links;
  * a committable link's floor is a min stable level, and a crossing bound
    warns bound_forces_off, as for generators;
  * the must-run sink check follows a link's output: p0 x efficiency on
    bus1, and bus2/bus3 with their efficiencies.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from services.profile_gen.apply import (
    MIN_STABLE_WARNING,
    MUST_RUN_SINK_WARNING,
    assign_profile,
)
from services.profile_gen.availability import TwoWayFloorError, PocBoundError
from services.profile_gen.engine import generate_profile
from services.profile_gen.families import default_target
from services.pypsa_service import PyPSAService
from services.user_timeseries import _user_ts, _user_ts_meta

NOW = "2026-10-20T00:00:00+00:00"


def _ctx():
    return PyPSAService.get_active_context()


def _net(*, two_way=False, committable=False, sink_mw=50.0, eff=0.9):
    n = PyPSAService.get_network()
    idx = pd.date_range("2023-01-01", "2023-12-31T23:00", freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "E", carrier="AC")
    n.add("Bus", "H2", carrier="H2")
    n.add("Load", "offtake", bus="H2", p_set=sink_mw)
    n.add("Link", "electrolyser", bus0="E", bus1="H2", p_nom=100.0,
          efficiency=eff, p_min_pu=-1.0 if two_way else 0.0,
          committable=committable)
    state = _ctx().solver_state.setdefault("solver_config", SimpleNamespace())
    state.commercial = {"poc_link": "grid_import", "export_link": None}
    return n


def _gen(kind, params):
    return generate_profile(points=["x"], technology=kind, params=params,
                            year=2023, now=NOW, family="availability")


def _apply(res, link="electrolyser"):
    ct, attr = default_target(res.meta, "links", None)
    return assign_profile(_ctx(), link, res.series["x"], res.meta,
                          component_type=ct, attribute=attr, time_basis="UTC",
                          now=NOW)


def test_a_shutdown_window_lands_on_the_links_p_max_pu():
    n = _net()
    out = _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04"}]}))
    assert out["component_type"] == "links" and out["attribute"] == "p_max_pu"
    p = n.links_t.p_max_pu["electrolyser"]
    assert float(p.loc["2023-07-03T12:00"]) == 0.0
    assert float(p.loc["2023-07-05T12:00"]) == 1.0


def test_a_shutdown_on_a_two_way_link_closes_both_directions():
    n = _net(two_way=True)
    _apply(_gen("maintenance", {"windows": [{"from": "07-03", "to": "07-04",
                                             "available": 0.0}]}))
    lo = n.links_t.p_min_pu["electrolyser"]
    assert float(lo.loc["2023-07-03T12:00"]) == 0.0
    assert float(lo.loc["2023-07-05T12:00"]) == -1.0
    assert ("links", "p_min_pu", "electrolyser") in _user_ts
    assert _user_ts_meta[("links", "p_min_pu", "electrolyser")]["companion_of"] == "p_max_pu"


def test_a_floor_on_a_two_way_link_is_refused():
    _net(two_way=True)
    with pytest.raises(TwoWayFloorError, match="reverse"):
        _apply(_gen("must_run", {"share": 0.3}))


def test_a_floor_on_the_poc_link_is_refused():
    n = _net()
    n.add("Link", "grid_import", bus0="E", bus1="H2", p_nom=10.0)
    with pytest.raises(PocBoundError, match="point-of-connection"):
        _apply(_gen("must_run", {"share": 0.3}), link="grid_import")


def test_a_committable_links_floor_is_a_min_stable_level():
    _net(committable=True)
    out = _apply(_gen("must_run", {"share": 0.3}))
    assert any(w.startswith(MIN_STABLE_WARNING) for w in out["warnings"])
    assert _user_ts_meta[("links", "p_min_pu", "electrolyser")]["kind"] == "min_stable_level"


def test_the_sink_check_follows_the_links_output_through_its_efficiency():
    # 0.8 x 100 MW of p0 at 90 % is 72 MW onto a bus taking 50 MW.
    _net(sink_mw=50.0, eff=0.9)
    out = _apply(_gen("must_run", {"share": 0.8}))
    warn = [w for w in out["warnings"] if w.startswith(MUST_RUN_SINK_WARNING)]
    assert warn and "72" in warn[0] and "50" in warn[0]


def test_a_floor_whose_output_the_bus_takes_does_not_warn():
    _net(sink_mw=80.0, eff=0.9)
    out = _apply(_gen("must_run", {"share": 0.8}))
    assert not any(w.startswith(MUST_RUN_SINK_WARNING) for w in out["warnings"])


def test_a_link_p_min_pu_may_be_negative_within_minus_one():
    n = _net()
    s = pd.Series(-0.5, index=n.snapshots)
    meta = {"family": "availability", "kind": "manual", "resolution": "hourly",
            "target": ["links", "p_min_pu"]}
    n.links.loc["electrolyser", "p_min_pu"] = -1.0
    out = assign_profile(_ctx(), "electrolyser", s, meta, component_type="links",
                         attribute="p_min_pu", time_basis="UTC", now=NOW)
    assert out["min"] == -0.5
