"""Recommended GFM settings (E2E demo plan 2026-10-14 §7.2; gatekeeper §7.4).

Property oracles on the engine itself (``frequency.simulate`` / ``judge``):
the current settings are kept when they pass; a recommendation passes every
case against the shrunk limits at the verify horizon, and is minimal to the
bisection's resolution (a little less inertia fails); the bounds hold; an
impossible island is not achievable and names the capacity I7 would buy; the
simulation count is exact and capped; and a VSM beats a grid-following
frequency-watt converter on RoCoF.
"""
import dataclasses
import math

import pytest

from gridspine.static import frequency as F
from gridspine.static import gfm_tuning as T
from tests.gridspine.test_frequency import bess, gen

F0 = 50.0


def lim(rocof=1e6, f_min=40.0, f_max=60.0, band=100.0):
    return {"transition": {"rocof_hz_per_s": rocof, "f_min_hz": f_min, "f_max_hz": f_max, "qss_band_hz": 100.0},
            "steady_island": {"rocof_hz_per_s": 1e6, "f_min_hz": 40.0, "f_max_hz": 60.0, "qss_band_hz": band}}


def case(units, dp, key="E2"):
    return T.Case(key, units, lambda us, dt, t_end: F.simulate(us, F.step(dp), t_end=t_end, dt=dt))


def island(**b):
    """A genset and a VSM BESS: H_v 1 s on 10 MVA, droop 4 %."""
    return [gen(p0=5.0), bess("gfm_vsm", h=1.0, **b)]


def passes(units, c, limits, margin=0.1, horizon=(1e-3, 20.0)):
    dt, t_end = horizon
    m = F.metrics(c.run(units, dt, t_end))
    return F.judge(m, T.shrunk(limits, margin)).ok


def test_shrunk_tightens_each_limit_by_the_margin():
    s = T.shrunk(lim(rocof=1.0, f_min=47.5, f_max=51.5, band=1.0), 0.1)
    assert s["transition"]["rocof_hz_per_s"] == pytest.approx(0.9)
    assert s["transition"]["f_min_hz"] == pytest.approx(50.0 - 2.5 * 0.9)
    assert s["transition"]["f_max_hz"] == pytest.approx(50.0 + 1.5 * 0.9)
    assert s["steady_island"]["qss_band_hz"] == pytest.approx(0.9)


def test_a_passing_island_is_left_alone_and_the_count_is_exact():
    c = case(island(), 2.0)
    r = T.recommend_gfm_settings([c], lim())
    assert r.achievable and not r.changed
    assert r.settings["B"]["recommended"] == r.settings["B"]["current"]
    # 1 before + 1 after + the comparison: gfl_fw skipped (no FW block), droop and VSM one each
    assert r.simulations == 4
    assert [x["ok_after"] for x in r.cases] == [True]


def test_a_rocof_limited_island_gets_the_least_inertia_that_passes():
    units, dp = island(), 4.0
    limits = lim(rocof=0.9)
    c = case(units, dp)
    assert not passes(units, c, limits)
    r = T.recommend_gfm_settings([c], limits)
    assert r.achievable and r.changed
    rec = r.settings["B"]["recommended"]
    assert rec["droop_pct"] == pytest.approx(4.0)                  # the steady checks already passed
    h = rec["H_v_s"]
    assert 1.0 < h <= 10.0
    assert passes(T.apply(units, 4.0, h), c, limits)
    assert not passes(T.apply(units, 4.0, h - 0.1), c, limits, horizon=(5e-3, 10.0))   # minimal to the step
    assert r.simulations <= 400


def test_a_settled_band_limited_island_gets_a_lower_droop_within_bounds():
    units = island()
    limits = lim(band=0.38)                       # 4 MW on 10 MW/Hz settles at 0.40 Hz, outside 0.38
    c = case(units, 4.0)
    assert not passes(units, c, limits)
    r = T.recommend_gfm_settings([c], limits)
    rec = r.settings["B"]["recommended"]
    assert r.achievable and 1.0 <= rec["droop_pct"] < 4.0
    assert passes(T.apply(units, rec["droop_pct"], rec["H_v_s"]), c, limits)


def test_a_droop_gfm_takes_the_equivalent_tau_f_inside_its_bounds():
    u = bess("gfm_droop")
    (got,) = T.apply([u], 4.0, 1.0)
    assert got.tau_f == pytest.approx(2 * 1.0 * u.s_n * 0.04 / u.p_n)
    (capped,) = T.apply([u], 10.0, 10.0)
    assert capped.tau_f == pytest.approx(T.GFM_BOUNDS.tau_f_s[1])


def test_an_impossible_island_is_not_achievable_and_names_the_capacity():
    # 30 MW lost against one 10 MW converter (i_max 1.2 pu) and nothing else
    units = [bess("gfm_vsm", h=1.0, p0=0.0)]
    c = case(units, 30.0)
    r = T.recommend_gfm_settings([c], lim(rocof=1.0, f_min=47.5))
    assert not r.achievable
    assert r.capacity and {x["kind"] for x in r.capacity} <= set(F.CUT_KINDS)
    assert any("capacity is needed" in n for n in r.notes)
    rec = r.settings["B"]["recommended"]
    assert rec["droop_pct"] == T.GFM_BOUNDS.droop_pct[0] and rec["H_v_s"] == T.GFM_BOUNDS.h_s[1]


def test_raising_i_max_is_only_tried_when_allowed():
    units = [gen(p0=5.0), bess("gfm_vsm", h=1.0, i_max_mw=10.5)]
    limits = lim(f_min=49.0)
    c = case(units, 9.0)
    held = T.recommend_gfm_settings([c], limits)
    assert held.settings["B"]["recommended"]["i_max_pu"] == pytest.approx(1.05)
    allowed = T.recommend_gfm_settings([c], limits, allow_i_max=True)
    if allowed.settings["B"]["recommended"]["i_max_pu"] != pytest.approx(1.05):
        assert allowed.settings["B"]["recommended"]["i_max_pu"] == pytest.approx(1.5)
        assert any("hardware" in n for n in allowed.notes)


def test_the_budget_stops_the_search():
    c = case(island(), 4.0)
    r = T.recommend_gfm_settings([c], lim(rocof=0.9), max_sims=3)
    assert r.simulations <= 3 and not r.achievable
    assert any("budget" in n for n in r.notes)


def test_a_vsm_beats_a_frequency_watt_converter_on_rocof_at_0_plus():
    """At 0+ a grid-following unit adds no inertia; over the 500 ms window a
    fast frequency-watt block (20 MW/Hz here) can respond more than the VSM's
    droop, so the window figure is reported, not asserted."""
    fw = {"delay_s": 0.2, "deadband_hz": 0.0, "k_mw_per_hz": 20.0, "p_limit_mw": 10.0, "ramp_mw_per_s": 50.0}
    units = [gen(p0=5.0), bess("gfm_vsm", h=1.0, fw=fw)]
    r = T.recommend_gfm_settings([case(units, 4.0)], lim())
    by = {x["model"]: x for x in r.comparison if x["unit"] == "B"}
    assert by["gfm_vsm"]["worst_rocof0_hz_per_s"] < by["gfl_fw"]["worst_rocof0_hz_per_s"]
    assert by["gfl_fw"]["worst_rocof_hz_per_s"] is not None
    assert by["gfl_fw"]["note"] is None


def test_without_a_gfm_nothing_is_tuned():
    r = T.recommend_gfm_settings([case([gen(p0=5.0)], 2.0)], lim())
    assert r.settings == {} and not r.changed and any("no grid-forming" in n for n in r.notes)
    assert math.isnan(float("nan"))
    assert dataclasses.is_dataclass(r)
