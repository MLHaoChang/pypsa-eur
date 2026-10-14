"""The island frequency events of I6b (plan 2026-10-07-campus-island-operation).

Oracles:
* a UPS that transfers on islanding is the import step less its IT load at
  0+, its load walking back in linearly: the same trace as that ramped
  event, its battery energy exactly L_it * walk_in / 2;
* a UPS on line changes nothing until |Δf| leaves its window: it drops at
  the first sample outside it, and stays on its battery while the settled
  frequency is outside;
* E4: the first block from rest is the one-governor step response; each
  next block waits for the governors' recovery; the settled frequency is
  -Σblocks / ΣK; the bridge needs max(bridge_s, ready + the last pickup),
  checked on both sides; N+k drops the largest genset;
* E6: the ramp time by hand (a converter at once, a cold genset from its
  join at its ramp); the shortfall; the opening on the residual;
* Q8: a higher current limit never deepens the nadir;
* the EMT flags at their thresholds, both sides;
* the sweeps: E1 at every hour on I3a's lossless steps equals the engine
  run by hand; the study's E1 runs on I4's ΔP_AC and settles at I4's Δf, its
  losses ΔP_AC − the lossless import.
"""
import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import frequency as F
from gridspine.static import island_flow as IF
from tests.gridspine.test_frequency import bess, gen
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_island_schema import requirements, t, ups

F0 = 50.0


def a_ups(it=6.0, walk_in=2.0, window=5.0, transfers=True, e=10.0, name="U"):
    return F.Unit(name, "ups", 0.0, 10.0, 10.0, ups={"it_mw": it, "walk_in_s": walk_in, "f_window_hz": window,
                                                      "transfers": transfers, "e_mwh": e})


# ── the UPS ──────────────────────────────────────────────────────────────────

def test_a_transferring_ups_relieves_its_it_load_at_0_plus_and_walks_it_back_in():
    g, u = gen(t_g=1e12), a_ups(it=6.0, walk_in=2.0, window=1e9)       # no governor, no drop-out
    tr = F.e1_islanding([g, u], 10.0, t_end=4.0)
    m = 2 * 1.5 * 10.0 / F0
    assert tr.rocof0 == pytest.approx(-(10.0 - 6.0) / m, rel=1e-12)
    ramped = F.simulate([g], lambda tt: 10.0 - 6.0 * max(0.0, 1.0 - tt / 2.0), t_end=4.0)
    # the relief is held over each 1 ms step: it lags the ramp by rate * dt / 2 over the walk-in
    assert np.max(np.abs(tr.df - ramped.df)) <= (6.0 / 2.0) * 1e-3 / 2 * 2.0 / m * 1.01
    finer = F.e1_islanding([g, u], 10.0, t_end=4.0, dt=1e-4)
    fine_ramp = F.simulate([g], lambda tt: 10.0 - 6.0 * max(0.0, 1.0 - tt / 2.0), t_end=4.0, dt=1e-4)
    assert np.max(np.abs(finer.df - fine_ramp.df)) <= (6.0 / 2.0) * 1e-4 / 2 * 2.0 / m * 1.01
    rec = tr.ups["U"]
    assert rec.energy_mwh == pytest.approx(6.0 * 2.0 / 2 / 3600.0, rel=1e-9)
    assert rec.t_online_s == pytest.approx(2.0, abs=2e-3) and rec.drops == [] and rec.autonomy_ok
    assert tr.dp["U"][0] == 6.0 and tr.dp["U"][-1] == 0.0


def test_a_ups_that_stays_on_line_changes_nothing_inside_its_window():
    g = gen()
    alone = F.e1_islanding([g], 4.0, t_end=3.0)
    with_ups = F.e1_islanding([g, a_ups(transfers=False)], 4.0, t_end=3.0)
    assert np.array_equal(alone.df, with_ups.df)
    assert with_ups.ups["U"].energy_mwh == 0.0 and with_ups.ups["U"].t_online_s == 0.0


def test_a_transferring_ups_stays_on_line_in_an_event_inside_the_island():
    g = gen()
    alone = F.e2_load_step([g], 2.0, t_end=2.0)
    tr = F.e2_load_step([g, a_ups(transfers=True)], 2.0, t_end=2.0)
    assert np.array_equal(alone.df, tr.df) and tr.ups["U"].energy_mwh == 0.0


def test_a_ups_drops_off_line_at_the_first_sample_outside_its_window():
    g = gen()
    alone = F.simulate([g], F.step(4.0), t_end=3.0)
    tr = F.simulate([g, a_ups(it=1.0, walk_in=1.0, window=0.3, transfers=False)], F.step(4.0), t_end=3.0)
    i = int(np.flatnonzero(np.abs(alone.df) > 0.3)[0])
    assert tr.ups["U"].drops[0] == pytest.approx(alone.t[i])
    assert np.array_equal(tr.df[:i + 1], alone.df[:i + 1])
    assert F.metrics(tr).nadir_hz > F.metrics(alone).nadir_hz              # its load left the island


def test_a_ups_drops_again_each_time_the_frequency_leaves_its_window():
    # inertia only: Δf = -t for 1 s, then rises at 1 Hz/s; a 0.5 Hz window is left at 0.5 s and at 2.5 s
    g = gen(t_g=1e12)
    m = 2 * 1.5 * 10.0 / F0
    tr = F.simulate([g, a_ups(it=1e-9, walk_in=1e-3, window=0.5, transfers=False)],
                    lambda tt: m if tt < 1.0 else -m, t_end=3.0)
    assert tr.ups["U"].drops == pytest.approx([0.5, 2.5], abs=2e-3)


def test_a_dropped_ups_gives_its_it_load_from_the_drop_to_the_end():
    m = 2 * 1.5 * 10.0 / F0
    tr = F.simulate([gen(t_g=1e12), a_ups(it=1.0, window=0.5, transfers=False)], F.step(2 * m), t_end=3.0)
    (t_drop,) = tr.ups["U"].drops
    assert t_drop == pytest.approx(0.25, abs=2e-3)
    assert tr.ups["U"].energy_mwh * 3600.0 == pytest.approx(1.0 * (3.0 - t_drop), rel=1e-9)


def test_a_ups_whose_window_the_settled_frequency_leaves_stays_on_its_battery():
    # the genset settles at -(4 - 1) / 5 = -0.6 Hz even with the UPS's 1 MW off: outside a 0.5 Hz window
    tr = F.simulate([gen(), a_ups(it=1.0, window=0.5, transfers=False, e=1.0)], F.step(4.0), t_end=15.0)
    rec = tr.ups["U"]
    assert tr.df[-1] == pytest.approx(-0.6, abs=1e-3)
    assert rec.t_online_s is None and not rec.autonomy_ok and rec.drops and tr.dp["U"][-1] == 1.0


@pytest.mark.parametrize("e_mwh, ok", [(2.0 * 1.0 / 3600 * 1.001, True), (2.0 * 1.0 / 3600 * 0.999, False)])
def test_the_ups_autonomy_is_its_battery_energy_against_what_it_gave(e_mwh, ok):
    tr = F.e1_islanding([gen(), a_ups(it=2.0, walk_in=2.0, e=e_mwh)], 3.0, t_end=3.0)
    assert tr.ups["U"].energy_mwh == pytest.approx(2.0 * 2.0 / 2 / 3600, rel=1e-9) and tr.ups["U"].drops == []
    assert tr.ups["U"].autonomy_ok is ok


# ── E4, the ups_bridged start ───────────────────────────────────────────────

def cold_gen(name="G", p_n=10.0, start=10.0, sync=5.0, **kw):
    return gen(name=name, p0=0.0, p_n=p_n, s_n=p_n, p_max=p_n, start_s=start, sync_s=sync, **kw)


def test_e4_takes_the_first_block_from_rest_like_a_one_governor_step():
    res = F.e4_ups_bridged([cold_gen()], [2.0, 2.0, 2.0], dt=1e-3, t_end=30.0)
    first = F.metrics(F.simulate([dataclasses.replace(cold_gen(), join_s=0.0)], F.step(2.0), t_end=1.5))
    assert res.ready_s == 15.0 and res.complete
    assert res.blocks[0].nadir_hz == pytest.approx(first.nadir_hz, abs=1e-9)
    assert res.blocks[0].t_nadir_s == pytest.approx(first.t_nadir_s, abs=1e-9)


def test_e4_restores_nominal_before_each_block_so_equal_blocks_respond_alike():
    # owner, 2026-10-14 (Q2 = A): secondary control restores f0 once a block settles; the next block starts
    # from nominal at the accumulated loading. Unsaturated, equal blocks then give equal responses.
    res = F.e4_ups_bridged([cold_gen()], [2.0, 2.0, 2.0], dt=1e-3, t_end=30.0)
    assert res.complete
    for m in res.blocks:
        assert m.nadir_hz == pytest.approx(res.blocks[0].nadir_hz, abs=1e-9)
        assert m.df_qss_hz == pytest.approx(-2.0 / 5.0, abs=1e-3)            # one block over K, not the sum
    gaps = np.diff(res.block_times_s)
    assert gaps == pytest.approx([gaps[0]] * len(gaps), abs=1e-9)
    # the joined trace's time strictly increases: no repeated instant at a block boundary (gatekeeper, #226)
    assert (np.diff(res.trace.t) > 0).all() and len(res.trace.t) == len(res.trace.df)


def test_each_block_waits_for_the_governors_to_recover_from_it():
    band, settle = 0.1, 0.05
    res = F.e4_ups_bridged([cold_gen()], [2.0, 2.0], band_hz=band, settle_hz_per_s=settle, dt=1e-3, t_end=30.0)
    one = F.simulate([dataclasses.replace(cold_gen(), join_s=0.0)], F.step(2.0), t_end=30.0, dt=1e-3)
    rocof = np.gradient(one.df, one.t)
    ok = (np.abs(one.df - (-2.0 / 5.0)) <= band) & (np.abs(rocof) <= settle)
    first = one.t[int(np.flatnonzero(ok & (one.t > 0))[0])]
    assert res.block_times_s == pytest.approx([0.0, first], abs=2e-3)


def test_each_block_starts_at_the_loading_the_earlier_blocks_left():
    # 10 MW genset with a 5 MW load-acceptance step: after 4 + 4 MW the third 4 MW block has 2 MW of room
    res = F.e4_ups_bridged([cold_gen()], [4.0, 4.0, 4.0], dt=1e-3, t_end=30.0)
    assert not res.complete and len(res.block_times_s) == 3
    assert res.blocks[2].nadir_hz < res.blocks[0].nadir_hz - 1.0


def test_an_isochronous_genset_settles_every_block_back_to_nominal():
    res = F.e4_ups_bridged([cold_gen(isochronous=True)], [2.0, 2.0], dt=1e-3, t_end=60.0)
    assert res.complete and all(abs(m.df_qss_hz) <= 0.1 for m in res.blocks)


def test_e4_on_an_isochronous_ramp_limited_genset_has_a_bounded_nadir(monkeypatch):
    # regression (gatekeeper, #256 review): the isochronous integral wound up while the genset was held at its
    # ramp, and the 2 MW block read a +61.8 Hz "nadir". Held, the extreme is the excursion while ramping
    gens, blocks = [cold_gen(isochronous=True, step_mw=0.5, ramp_mw_per_s=0.08)], [2.0, 2.0]
    res = F.e4_ups_bridged(gens, blocks)
    assert res.complete
    for m in res.blocks:
        assert m.nadir_hz == pytest.approx(-23.541, abs=1e-3) and abs(m.df_qss_hz) < 1e-4
    monkeypatch.setattr(F, "_hold_integral", lambda u, p, df, t: df)
    wound = F.e4_ups_bridged(gens, blocks)
    assert wound.blocks[0].nadir_hz == pytest.approx(61.80, abs=0.01)


# ── the trip floor ──────────────────────────────────────────────────────────

LIMITS_47 = {"transition": {"rocof_hz_per_s": 1.0, "f_min_hz": 47.5, "f_max_hz": 51.5, "qss_band_hz": 1.0},
             "steady_island": {"rocof_hz_per_s": 1.0, "f_min_hz": 49.0, "f_max_hz": 51.0, "qss_band_hz": 1.0}}


def test_the_trip_floor_is_the_lowest_limit_and_names_it():
    fl = F.trip_floor(LIMITS_47)
    assert (fl.lo_hz, fl.lo_what, fl.hi_hz, fl.hi_what) == (47.5, "transition f_min", 51.5, "transition f_max")
    # a UPS window below it sets it; a load's under-frequency trip has no high side (NaN), which is skipped
    eq = [("UPS window U", 47.0, 53.0), ("CH trip", 48.0, math.nan), ("FAN trip", math.nan, 52.0)]
    fl = F.trip_floor(LIMITS_47, eq)
    assert (fl.lo_hz, fl.lo_what, fl.hi_hz, fl.hi_what) == (47.0, "UPS window U", 53.0, "UPS window U")
    fl = F.trip_floor(LIMITS_47, eq[1:])
    assert (fl.lo_hz, fl.hi_hz, fl.hi_what) == (47.5, 52.0, "FAN trip")


def test_a_run_that_stays_inside_the_floor_is_the_run_without_it():
    g = gen(p0=0.0, p_max=1e6)
    free = F.simulate([g], F.step(4.0), t_end=10.0, dt=5e-3)
    held = F.simulate([g], F.step(4.0), t_end=10.0, dt=5e-3, floor=F.trip_floor(LIMITS_47))
    assert held.trip is None and np.array_equal(free.t, held.t) and np.array_equal(free.df, held.df)
    assert F.metrics(free) == F.metrics(held)


def test_a_run_that_crosses_the_floor_stops_there_as_a_trip():
    # the #260 case: an isochronous, ramp-limited genset falls to -23.5 Hz unfloored; at 47.5 Hz it trips
    g = gen(isochronous=True, p0=0.0, step_mw=0.5, ramp_mw_per_s=0.08)
    floor = F.trip_floor(LIMITS_47)
    free = F.simulate([g], F.step(2.0), t_end=60.0, dt=5e-3)
    tr = F.simulate([g], F.step(2.0), t_end=60.0, dt=5e-3, floor=floor)
    first = int(np.flatnonzero(50.0 + free.df < 47.5)[0])
    assert tr.trip == (pytest.approx(free.t[first]), "Δf below the 47.5 Hz floor (transition f_min)")
    assert tr.t[-1] == pytest.approx(free.t[first]) and np.array_equal(tr.df, free.df[:first + 1])
    m = F.metrics(tr)
    assert m.trip == tr.trip and m.collapse is None and math.isnan(m.nadir_hz) and math.isnan(m.df_qss_hz)
    v = F.judge(m, LIMITS_47)
    assert not v.ok and v.worst == math.inf
    assert v.binding == f"trip: Δf below the 47.5 Hz floor (transition f_min) at t = {free.t[first]:.2f} s"
    # and the ceiling: a 2 MW load lost from 8 MW
    hot = F.simulate([gen(isochronous=True, p0=8.0, step_mw=0.5, ramp_mw_per_s=0.08)], F.step(-2.0),
                     t_end=60.0, dt=5e-3, floor=floor)
    assert hot.trip[1] == "Δf above the 51.5 Hz ceiling (transition f_max)"


def test_e4_reads_a_tripped_block_as_not_completing():
    gens, blocks = [cold_gen(isochronous=True, step_mw=0.5, ramp_mw_per_s=0.08)], [2.0, 2.0]
    res = F.e4_ups_bridged(gens, blocks, [("U", 6.0, 100.0)], floor=F.trip_floor(LIMITS_47))
    assert not res.complete and len(res.blocks) == 1 and res.blocks[0].trip is not None
    assert F.judge(res.blocks[0], LIMITS_47).binding.startswith("trip: Δf below the 47.5 Hz floor")
    assert res.bridge[0].needed_s == math.inf and not res.bridge[0].ok
    # the floor is in the pickup's cache key: the unfloored run is not served the tripped one
    cache = {}
    F.e4_ups_bridged(gens, blocks, floor=F.trip_floor(LIMITS_47), cache=cache)
    assert F.e4_ups_bridged(gens, blocks, cache=cache).complete


def test_the_sweep_reports_a_trip_and_its_cuts_are_those_of_the_run_without_the_floor(monkeypatch):
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    h = hourly()
    h.loc[h["unit_id"] == "BESS1", "status"] = 0                 # the genset alone runs away
    ih, iu = island_tables(online=(1.0, 1.0))
    floored = F.e1_every_hour(camp, h, ih, iu)
    assert floored["trip_s"].notna().any() and floored["binding"].str.startswith("trip:").any()
    monkeypatch.setattr(F, "trip_floor", lambda *a, **k: F.TripFloor())    # NaN: no floor, no ceiling
    free = F.e1_every_hour(camp, h, ih, iu)
    assert free["trip_s"].isna().all()
    monkeypatch.undo()
    a = F.propose_cuts(camp, h, None, floored, island_hourly=ih, island_unit_hourly=iu)
    b = F.propose_cuts(camp, h, None, free, island_hourly=ih, island_unit_hourly=iu)
    pd.testing.assert_frame_equal(a, b)


def test_the_study_runs_every_event_against_the_campus_floor():
    from tests.gridspine.test_island_schema import t
    # E1: without the BESS the genset alone runs away, past the UPS window's 52 Hz
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    h = hourly()
    h.loc[h["unit_id"] == "BESS1", "status"] = 0
    e1 = F.campus_frequency_study(camp, h, [(2030, 0)], t_end=5.0).events.iloc[0]
    assert e1["event"] == "E1" and e1["trip_s"] == pytest.approx(0.34)
    assert e1["binding"] == "trip: Δf above the 52 Hz ceiling (UPS window UPS1) at t = 0.34 s"
    # E4: a 30 MW block on the 10 MW genset falls through the 47.5 Hz floor; the pickup does not complete
    s = ups_campus_spec(scenarios=("ups_bridged",))
    s["campus"]["island"]["pickup_blocks"] = t([30.0])
    ih, iu = island_tables(online=(1.0, 1.0))
    st = F.campus_frequency_study(build_campus(s), hourly(), [(2030, 0)], island_unit_hourly=iu)
    e4 = st.events.iloc[0]
    assert e4["event"] == "E4" and e4["trip_s"] == pytest.approx(0.065)
    assert e4["binding"].startswith("trip: Δf below the 47.5 Hz floor (transition f_min)")
    assert not st.bridge.iloc[0]["pickup_complete"] and st.bridge.iloc[0]["needed_s"] == math.inf


def test_a_pickup_beyond_the_gensets_does_not_complete_and_fails_the_bridge():
    res = F.e4_ups_bridged([cold_gen()], [6.0, 6.0], [("U", 6.0, 100.0)], bridge_s=60.0, dt=1e-3, t_end=30.0)
    assert not res.complete
    assert res.bridge[0].needed_s == math.inf and not res.bridge[0].ok


@pytest.mark.parametrize("factor, ok", [(1.001, True), (0.999, False)])
def test_the_bridge_needs_the_ready_time_plus_the_last_pickup_on_both_sides(factor, ok):
    probe = F.e4_ups_bridged([cold_gen()], [2.0, 2.0], dt=1e-3, t_end=30.0)
    needed = 15.0 + probe.block_times_s[-1]
    e = 6.0 * needed / 3600.0 * factor
    res = F.e4_ups_bridged([cold_gen()], [2.0, 2.0], [("U", 6.0, e)], bridge_s=1.0, dt=1e-3, t_end=30.0)
    b = res.bridge[0]
    assert b.needed_s == pytest.approx(needed) and b.autonomy_s == pytest.approx(needed * factor)
    assert b.ok is ok


def test_the_bridge_needs_bridge_s_when_that_is_longer():
    res = F.e4_ups_bridged([cold_gen()], [2.0], [("U", 6.0, 6.0 * 600.0 / 3600.0 * 0.999)], bridge_s=600.0,
                           dt=1e-3, t_end=30.0)
    assert res.bridge[0].needed_s == 600.0 and not res.bridge[0].ok


def test_n_plus_k_leaves_out_the_largest_genset_and_needs_one_left():
    small, large = cold_gen("S", p_n=6.0, start=30.0), cold_gen("L", p_n=10.0, start=10.0)
    res = F.e4_ups_bridged([small, large], [2.0], redundancy_n=1, dt=1e-3, t_end=30.0)
    assert res.dropped == ["L"] and res.ready_s == 35.0                  # the small one's 30 + 5 s
    assert F.e4_ups_bridged([small, large], [2.0], dt=1e-3, t_end=30.0).ready_s == 35.0
    with pytest.raises(ContractError, match="N\\+k"):
        F.e4_ups_bridged([large], [2.0], redundancy_n=1)


# ── E4 runs each block until its tail is steady, and once per fleet ────────

def eh_datacenter_fleet():
    """eh_datacenter's gensets (template ISLAND_CONFIG): 4 x 10 MW and genset_new
    36.67 MW in 10 MW units; H 1.5 s on MVA = MW / 0.8, R 4 %, T_g 0.5 s, 50 %
    load step, 0.2 pu/s ramp, 10 s start + 5 s sync."""
    def g(name, p_n, **kw):
        return F.Unit(name, "genset", 0.0, p_n, p_n / 0.8, 0.0, p_n, r=0.04, h=1.5, t_g=0.5, step_mw=0.5 * p_n,
                      ramp_mw_per_s=0.2 * p_n, start_s=10.0, sync_s=5.0, join_s=15.0, **kw)
    return [g(f"genset_{i}", 10.0) for i in range(1, 5)] + [g("genset_new", 36.67, unit_mw=10.0)]


EH_LIMITS = {"transition": {"rocof_hz_per_s": 1.0, "f_min_hz": 47.5, "f_max_hz": 51.5, "qss_band_hz": 1.0},
             "steady_island": {"rocof_hz_per_s": 1.0, "f_min_hz": 47.5, "f_max_hz": 51.5, "qss_band_hz": 1.0}}


def assert_same_e4(a, b):
    """The steady-tail run against the full horizon: every block's figures and
    verdict, the block times, completion and the bridge."""
    assert a.complete == b.complete and a.ready_s == b.ready_s and a.dropped == b.dropped
    assert a.block_times_s == pytest.approx(b.block_times_s, abs=1e-9)
    assert len(a.blocks) == len(b.blocks)
    for x, y in zip(a.blocks, b.blocks):
        for f in ("nadir_hz", "df_qss_hz", "rocof0_hz_per_s", "rocof_window_hz_per_s"):
            assert getattr(x, f) == pytest.approx(getattr(y, f), abs=1e-6), f
        assert x.t_return_s == y.t_return_s and x.saturated == y.saturated
        assert F.judge(x, EH_LIMITS).failures == F.judge(y, EH_LIMITS).failures
    assert [(u.ups, u.needed_s, u.ok) for u in a.bridge] == [(u.ups, u.needed_s, u.ok) for u in b.bridge]


def test_e4_on_eh_datacenter_stops_each_block_once_settled_and_matches_the_600_s_run():
    # gatekeeper's reference (hour 2030/0, last block): nadir -0.3118, qss -0.150, t_return 0.795, ends at 13.7 s
    feeds = [("UPS", 36.0, 10.0)]
    kw = dict(redundancy_n=1, bridge_s=600.0, t_end=600.0, dt=5e-3)
    calls, real = [], F.simulate

    def counting(*a, **k):
        calls.append(k["t_end"])
        return real(*a, **k)
    F.simulate = counting
    try:
        fast = F.e4_ups_bridged(eh_datacenter_fleet(), [5.0] * 11, feeds, **kw)
    finally:
        F.simulate = real
    full = F.e4_ups_bridged(eh_datacenter_fleet(), [5.0] * 11, feeds, steady_stop=False, **kw)
    assert_same_e4(fast, full)
    assert calls == [F.E4_FIRST_S] * 11                     # each block steady within its first 20 s
    m = fast.blocks[-1]
    assert fast.complete and fast.dropped == ["genset_new: 1 x 10 MW"]
    assert m.nadir_hz == pytest.approx(-0.3118, abs=1e-4) and m.df_qss_hz == pytest.approx(-0.15, abs=1e-4)
    assert m.t_return_s == pytest.approx(0.795, abs=1e-9) and fast.block_times_s[-1] == pytest.approx(13.7)


@pytest.mark.parametrize("kw", [dict(step_mw=0.5, ramp_mw_per_s=0.08), dict(isochronous=True)],
                         ids=["ramp-limited", "isochronous"])
def test_a_slow_island_is_run_on_until_steady_not_read_at_a_fixed_short_horizon(kw):
    # the ramp-limited genset takes about 39 s to take a 2 MW block: at a fixed 20 s it has not, and E4 would
    # read it as not completing; the steady-tail stop runs it on (20, 40, 80 s) and matches the 600 s run
    gens, blocks = [cold_gen(**kw)], [2.0, 2.0]
    fast = F.e4_ups_bridged(gens, blocks)
    assert_same_e4(fast, F.e4_ups_bridged(gens, blocks, steady_stop=False))
    assert fast.complete
    if "ramp_mw_per_s" in kw:
        assert not F.e4_ups_bridged(gens, blocks, steady_stop=False, t_end=20.0).complete
        assert fast.block_times_s[-1] > 20.0


def test_the_steady_tail_holds_over_its_whole_window_not_one_flat_moment():
    t = np.arange(0.0, 20.0 + 1e-9, 5e-3)
    moving = np.where(t < 19.0, -0.01 * (19.0 - t), 0.0)          # still falling until the last second
    trace = F.Trace(t, moving, {}, {}, None, 0.0)
    assert not F._steady_tail(trace)
    flat = F.Trace(t, np.where(t < 14.0, moving, 0.0), {}, {}, None, 0.0)   # flat for the last 6 s
    assert F._steady_tail(flat)
    assert F._steady_tail(F.Trace(t[:2], moving[:2], {}, {}, 0.005, 0.0))     # a collapse ends the run
    assert not F._steady_tail(F.Trace(t[t <= 4.0], 0.0 * t[t <= 4.0], {}, {}, None, 0.0))  # shorter than the hold


def test_a_block_that_never_steadies_runs_to_t_end_and_does_not_complete():
    # 6 MW on a 10 MW genset with a 5 MW load step: the first block never settles, so it runs the whole horizon
    calls, real = [], F.simulate

    def counting(*a, **k):
        calls.append(k["t_end"])
        return real(*a, **k)
    F.simulate = counting
    try:
        res = F.e4_ups_bridged([cold_gen(step_mw=5.0, ramp_mw_per_s=0.0)], [6.0, 6.0], t_end=100.0)
    finally:
        F.simulate = real
    assert not res.complete and calls == [20.0, 40.0, 80.0, 100.0]
    # a horizon shorter than the first run is the run
    calls.clear()
    F.simulate = counting
    try:
        F.e4_ups_bridged([cold_gen()], [2.0], t_end=10.0)
    finally:
        F.simulate = real
    assert calls == [10.0]


def test_hours_with_the_same_fleet_share_one_e4_pickup_and_keep_their_own_bridge():
    gens, blocks = [cold_gen()], [2.0, 2.0]
    calls, real = [], F.simulate

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    cache = {}
    F.simulate = counting
    try:
        one = F.e4_ups_bridged(gens, blocks, [("U", 6.0, 1.0)], cache=cache)
        n = len(calls)
        # another hour: the same gensets at another dispatch and join, another UPS energy
        moved = [dataclasses.replace(gens[0], p0=4.0, join_s=0.0)]
        two = F.e4_ups_bridged(moved, blocks, [("U", 6.0, 0.001)], cache=cache)
        assert len(calls) == n and two.blocks is one.blocks
        assert one.bridge[0].ok and not two.bridge[0].ok              # the bridge is each hour's own
        # anything that reaches the simulation is in the key
        for other in (dict(blocks=[2.0, 3.0]), dict(dt=1e-3), dict(gensets=[cold_gen(h=2.0)]), dict(f0=60.0)):
            args = dict(gensets=gens, blocks=blocks, cache=cache)
            args.update(other)
            F.e4_ups_bridged(**args)
            assert len(calls) > n
            n = len(calls)
    finally:
        F.simulate = real


def test_the_study_runs_e4_once_for_identical_hours():
    camp = ups_campus(scenarios=("ups_bridged",))
    ih, iu = island_tables(online=(1.0, 1.0))
    counts = []
    real = F.simulate
    for hours in ([(2030, 0)], [(2030, 0), (2030, 1), (2030, 0), (2030, 1)]):
        calls = []
        F.simulate = lambda *a, **k: calls.append(1) or real(*a, **k)            # noqa: E731
        try:
            st = F.campus_frequency_study(camp, hourly(), hours, island_unit_hourly=iu)
        finally:
            F.simulate = real
        counts.append(len(calls))
        assert len(st.bridge[st.bridge["kind"] == "ups"]) == len(hours)           # one bridge check per hour
    assert counts[0] == counts[1] > 0


def test_n_plus_k_removes_one_unit_of_a_modular_genset():
    # owner, 2026-10-14 (Q1 = A), as I2b: N+1 takes one 5 MW unit from the 20 MW genset, not all of it
    big = cold_gen("BIG", p_n=20.0, unit_mw=5.0, step_mw=10.0, ramp_mw_per_s=2.0)
    small = cold_gen("S", p_n=1.0)                                         # a smaller unit than BIG's 5 MW
    res = F.e4_ups_bridged([big, small], [2.0], redundancy_n=1, dt=1e-3, t_end=30.0)
    assert res.dropped == ["BIG: 1 x 5 MW"] and res.complete
    left = [dataclasses.replace(big, p_n=15.0, s_n=15.0, p_max=15.0, step_mw=big.step_mw * 0.75,
                                ramp_mw_per_s=big.ramp_mw_per_s * 0.75, join_s=0.0),
            dataclasses.replace(small, join_s=0.0)]
    by_hand = F.metrics(F.simulate(left, F.step(2.0), t_end=30.0, dt=1e-3))
    assert res.blocks[0].nadir_hz == pytest.approx(by_hand.nadir_hz, abs=1e-9)
    # the load step scales with the unit gone: BIG's 8.4 MW share of 9 MW is beyond the 7.5 MW left, within 10 MW
    nine = F.e4_ups_bridged([big, small], [9.0], redundancy_n=1, dt=1e-3, t_end=30.0)
    hand9 = F.metrics(F.simulate(left, F.step(9.0), t_end=30.0, dt=1e-3))
    assert nine.blocks[0].nadir_hz == pytest.approx(hand9.nadir_hz, abs=1e-9)
    unscaled = [dataclasses.replace(left[0], step_mw=big.step_mw, ramp_mw_per_s=big.ramp_mw_per_s), left[1]]
    assert F.metrics(F.simulate(unscaled, F.step(9.0), t_end=30.0, dt=1e-3)).nadir_hz > hand9.nadir_hz + 1e-3
    # two units out: BIG's 5 MW units stay the largest, so both come from it
    two = F.e4_ups_bridged([big, small], [2.0], redundancy_n=2, dt=1e-3, t_end=30.0)
    assert two.dropped == ["BIG: 1 x 5 MW", "BIG: 1 x 5 MW"]
    # a whole genset with a larger unit goes first
    six = F.e4_ups_bridged([big, cold_gen("S6", p_n=6.0)], [2.0], redundancy_n=1, dt=1e-3, t_end=30.0)
    assert six.dropped == ["S6"]


def test_a_whole_unit_genset_is_removed_whole():
    one = cold_gen("ONE", p_n=5.0, unit_mw=5.0)
    res = F.e4_ups_bridged([one, cold_gen("S", p_n=6.0)], [2.0], redundancy_n=1, dt=1e-3, t_end=30.0)
    assert res.dropped == ["S"]                                             # the largest unit is S's 6 MW
    res = F.e4_ups_bridged([one, cold_gen("T", p_n=4.0)], [2.0], redundancy_n=1, dt=1e-3, t_end=30.0)
    assert res.dropped == ["ONE"]


# ── E6, planned islanding ──────────────────────────────────────────────────

def test_e6_ramp_time_by_hand_the_bess_at_once_the_cold_genset_from_its_join():
    g = cold_gen(join_s=15.0, ramp_mw_per_s=1.0)
    b = bess(p0=0.0, p_max=3.0)
    res = F.e6_planned([g, b], 8.0, t_end=1.0)
    assert res.feasible and res.ramp_s == pytest.approx(15.0 + 5.0, abs=1e-6)      # 3 at once, 5 at 1 MW/s
    assert res.p0 == pytest.approx({"G": 5.0, "B": 3.0}, abs=1e-6)
    assert np.max(np.abs(res.trace.df)) < 1e-9                                     # ΔP ≈ 0 at the opening


def test_e6_without_the_room_reports_the_shortfall():
    res = F.e6_planned([cold_gen(join_s=15.0, ramp_mw_per_s=1.0), bess(p0=0.0, p_max=3.0)], 20.0)
    assert not res.feasible and res.ramp_s == math.inf and res.shortfall_mw == pytest.approx(7.0)
    assert res.trace is None


def test_e6_takes_an_export_back_on_the_storage_at_once():
    res = F.e6_planned([gen(p0=5.0, ramp_mw_per_s=0.5), bess(p0=0.0)], -2.0, t_end=1.0)
    assert res.ramp_s == 0.0 and res.p0["B"] == pytest.approx(-2.0) and res.p0["G"] == 5.0


def test_e6_takes_an_export_back_on_a_genset_by_its_room_down():
    # 8 MW running on 10: 2 MW of room up, 8 down; 6 MW of export back at 1 MW/s
    res = F.e6_planned([gen(p0=8.0, ramp_mw_per_s=1.0)], -6.0, t_end=0.5)
    assert res.feasible and res.ramp_s == pytest.approx(6.0, abs=1e-6) and res.p0["G"] == pytest.approx(2.0)


def test_e6_opens_on_the_residual_with_the_shifted_dispatch():
    # shifted to 9 of 10 MW, the genset has 1 MW left for a 2 MW residual: the shift matters
    res = F.e6_planned([gen(p0=5.0, ramp_mw_per_s=1.0)], 4.0, residual_mw=2.0, t_end=2.0)
    assert res.ramp_s == pytest.approx(4.0, abs=1e-6) and res.p0["G"] == pytest.approx(9.0)
    by_hand = F.simulate([gen(p0=9.0, ramp_mw_per_s=1.0)], F.step(2.0), t_end=2.0)
    unshifted = F.simulate([gen(p0=5.0, ramp_mw_per_s=1.0)], F.step(2.0), t_end=2.0)
    assert np.allclose(res.trace.df, by_hand.df, atol=1e-12)
    assert not np.allclose(by_hand.df, unshifted.df, atol=1e-3)


def test_e6_counts_a_frequency_watt_unit_at_its_ramp():
    pv = F.Unit("PV", "gfl_fw", 0.0, 10.0, 10.0, -10.0, 10.0,
                fw={"k_mw_per_hz": 1.0, "deadband_hz": 0.0, "delay_s": 9.0, "ramp_mw_per_s": 2.0, "p_limit_mw": 10.0})
    res = F.e6_planned([pv], 4.0, t_end=0.5)
    assert res.ramp_s == pytest.approx(2.0, abs=1e-6)                   # the setpoint ramps; no fw delay


# ── Q8 and the EMT flags ────────────────────────────────────────────────────

def test_q8_reruns_with_each_current_limit_and_a_higher_limit_never_deepens_the_nadir():
    units = [gen(), bess(p_max=10.0, i_max_mw=1e9, s_n=2.0)]
    runs = F.i_max_sensitivity(units, lambda us: (us, F.simulate(us, F.step(6.0), t_end=3.0)), [1.0, 1.5, 2.0])
    assert [v for v, _ in runs] == [1.0, 1.5, 2.0]
    for v, (us, _) in runs:
        assert next(u for u in us if u.name == "B").i_max_mw == pytest.approx(v * 2.0)
        assert next(u for u in us if u.name == "G").i_max_mw == math.inf
    nadirs = [F.metrics(tr).nadir_hz for _, (_, tr) in runs]
    assert nadirs[0] < nadirs[1] < nadirs[2]


def lim(f_min=47.5, f_max=51.5):
    one = {"rocof_hz_per_s": 1.0, "f_min_hz": f_min, "f_max_hz": f_max, "qss_band_hz": 1.0}
    return {"transition": one, "steady_island": one}


def m_at(nadir, saturated=None, collapse=None):
    return F.Metrics(0.0, 0.0, 500.0, nadir, 1.0, nadir, 0.0, saturated or {}, collapse)


@pytest.mark.parametrize("nadir, flagged", [(-2.0, False), (-2.001, True), (1.2, False), (1.201, True)])
def test_the_nadir_margin_flag_at_its_threshold(nadir, flagged):
    # 20 % of 2.5 Hz below and of 1.5 Hz above is left at -2.0 and +1.2 Hz
    flags = F.emt_flags(m_at(nadir), lim())
    assert any("nadir margin" in f for f in flags) is flagged


def test_saturation_and_collapse_are_flagged():
    assert F.emt_flags(m_at(-0.1, {"B": 0.2}), lim()) == ["grid-forming units saturate in the event: B"]
    assert F.emt_flags(m_at(math.nan, collapse=0.3), lim())[0].startswith(F.COLLAPSE)


@pytest.mark.parametrize("kw, flagged", [
    ({"scr_pcc": 2.9, "gfl_share": 0.31}, True), ({"scr_pcc": 3.0, "gfl_share": 0.9}, False),
    ({"scr_pcc": 1.0, "gfl_share": 0.3}, False), ({"island_scr": 2.9, "gfm_share": 0.51}, True),
    ({"island_scr": 3.0, "gfm_share": 0.9}, False), ({"island_scr": 1.0, "gfm_share": 0.5}, False),
    ({"scr_pcc": math.nan, "gfl_share": 0.9}, False)])
def test_the_weak_grid_flags_need_both_conditions(kw, flagged):
    assert bool(F.emt_flags(m_at(-0.1), lim(), **kw)) is flagged


# ── the campus ──────────────────────────────────────────────────────────────

def ups_campus(transfers=True, scenarios=("seamless", "ups_bridged", "planned"), walk_in=15.0, f_trip=None):
    return build_campus(ups_campus_spec(transfers, scenarios, walk_in, f_trip))


def ups_campus_spec(transfers=True, scenarios=("seamless", "ups_bridged", "planned"), walk_in=15.0, f_trip=None):
    s = spec()
    s["campus"]["units"]["UPS1"] = {"kind": "ups", "bus": "MV2", "p_mw": t(8.0), "e_mwh": t(2.0), "s_mva": t(9.0),
                                    "island": {**ups(), "walk_in_s": t(walk_in), "it_load": ["DC_LOAD"],
                                               "transfers_on_islanding": transfers}}
    if f_trip:
        s["campus"]["units"]["DC_LOAD"]["island"] = {"f_trip_hz": t(f_trip)}
    req = requirements()
    req.update(scenarios=list(scenarios), genset_redundancy_n=t(0), pickup_blocks=t([2.0, 3.0, 1.0]))
    s["campus"]["island"] = req
    return s


def ups_rows(**kw):
    return pd.concat([rows(**kw), pd.DataFrame({"unit_id": ["UPS1"], "p_mw": [0.0], "status": [1]})],
                     ignore_index=True)


def test_the_campus_ups_carries_its_it_loads_draw_and_its_stored_energy():
    camp = ups_campus()
    u = next(x for x in F.units_from_campus(camp, ups_rows(load=30.0)) if x.name == "UPS1")
    assert u.ups == {"it_mw": 30.0, "walk_in_s": 15.0, "f_window_hz": 2.0, "transfers": True, "e_mwh": 2.0}
    u = next(x for x in F.units_from_campus(camp, ups_rows(), soc={"UPS1": 0.7}) if x.name == "UPS1")
    assert u.ups["e_mwh"] == 0.7
    g = next(x for x in F.units_from_campus(camp, ups_rows()) if x.name == "GEN1")
    assert (g.start_s, g.sync_s, g.ready_s) == (10.0, 5.0, 15.0) and math.isnan(g.unit_mw)
    s = ups_campus_spec()
    s["campus"]["units"]["GEN1"]["island"]["unit_mw"] = t(2.5, "datasheet")
    g = next(x for x in F.units_from_campus(build_campus(s), ups_rows()) if x.name == "GEN1")
    assert g.unit_mw == 2.5


@pytest.mark.parametrize("f_trip, expect", [(48.0, ("DC_LOAD trip", 48.0, None)), (51.0, ("DC_LOAD trip", None, 51.0))])
def test_the_equipment_is_each_ups_window_and_each_load_trip(f_trip, expect):
    eq = dict((label, (lo, hi)) for label, lo, hi in F.equipment(ups_campus(f_trip=f_trip)))
    assert eq["UPS window UPS1"] == (48.0, 52.0)
    lo, hi = eq[expect[0]]
    assert (lo if expect[1] else hi) == (expect[1] or expect[2])
    assert math.isnan(hi if expect[1] else lo)


def test_the_emt_inputs_by_hand():
    camp = ups_campus()
    e = F.emt_inputs(camp, ups_rows())
    # PV1 16.5 MVA grid-following; BESS1 22 MVA grid-forming; GEN1 12.5 MVA
    assert e["scr_pcc"] == pytest.approx(2000.0 / 16.5)
    assert e["gfl_share"] == pytest.approx(16.5 / 51.0) and e["gfm_share"] == pytest.approx(22.0 / 51.0)
    assert 0 < e["island_scr"] < math.inf
    # a genset built but not running counts: it starts on islanding; one not built in the period does not
    idle = ups_rows()
    idle.loc[idle["unit_id"] == "GEN1", "p_mw"] = 0.0
    assert F.emt_inputs(camp, idle)["gfl_share"] == pytest.approx(16.5 / 51.0)
    absent = ups_rows()
    absent.loc[absent["unit_id"] == "GEN1", ["p_mw", "status"]] = [0.0, 0]
    assert F.emt_inputs(camp, absent)["gfl_share"] == pytest.approx(16.5 / 38.5)


def test_a_unit_not_built_in_the_period_is_not_in_the_island():
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    h = hourly()
    h.loc[h["unit_id"] == "BESS1", "status"] = 0
    ih, iu = island_tables(online=(1.0, 1.0))
    sweep = F.e1_every_hour(camp, h, ih, iu)
    rows0 = h[h["hour"] == 0][["unit_id", "p_mw", "status"]].reset_index(drop=True)
    units = F.units_from_campus(camp, rows0, online=F.built(rows0))          # the UPS stays on line: kept
    assert "BESS1" not in {u.name for u in units} and "UPS1" in {u.name for u in units}
    limits, req = F._limits(camp)
    floor = F.trip_floor(limits, F.equipment(camp))
    by_hand = F.metrics(F.simulate(units, F.step(10.0), t_end=10.0, dt=5e-3, floor=floor))
    # the genset alone runs away (+131 Hz unfloored): the sweep reads the same trip, not a number
    assert by_hand.trip is not None and sweep.iloc[0]["trip_s"] == pytest.approx(by_hand.trip[0], abs=1e-12)
    assert sweep.iloc[0]["binding"] == F.trip_label(by_hand.trip) and math.isnan(sweep.iloc[0]["nadir_hz"])
    # without the BESS the genset cannot form the island; counting the unbuilt BESS would make it viable
    st = F.campus_frequency_study(camp, h, [(2030, 0)], t_end=5.0)
    assert not st.losses.iloc[0]["i4_viable"] and IF.island_flow(camp, rows0).viable


def island_tables(dp_up=(10.0, 0.0), dp_down=(-10.0, 3.0), online=(1.0, 0.0)):
    ih = pd.DataFrame({"period": [2030, 2030], "hour": [0, 1], "scenario": ["seamless"] * 2,
                       "dp_up_mw": list(dp_up), "dp_down_mw": list(dp_down), "inertia_mws": [1.0, 1.0],
                       "k_mw_per_hz": [1.0, 1.0], "critical_load_mw": [1.0, 1.0]})
    iu = pd.DataFrame({"period": [2030] * 4, "hour": [0, 0, 1, 1], "unit_id": ["GEN1", "UPS1"] * 2,
                       "soc_start_mwh": [math.nan, 1.5, math.nan, 0.5], "online": [online[0], math.nan, online[1], math.nan],
                       "headroom_up_mw": [5.0, math.nan, 5.0, math.nan],
                       "headroom_down_mw": [5.0, math.nan, 5.0, math.nan], "p_ups_it_mw": [math.nan, 20.0, math.nan, 20.0]})
    return ih, iu


def hourly():
    return pd.concat([ups_rows().assign(period=2030, hour=h) for h in (0, 1)], ignore_index=True)


def test_e1_at_every_hour_runs_i3a_s_lossless_steps_each_way_on_the_hours_units():
    camp = ups_campus()
    ih, iu = island_tables()
    out = F.e1_every_hour(camp, hourly(), ih, iu)
    assert list(zip(out["hour"], out["direction"])) == [(0, "import"), (1, "export")]
    assert set(out["dp_source"]) == {"I3a lossless"}
    # hour 0: the genset runs; hour 1: I3a says it is off, so it starts cold; no UPS in either
    for hour, dp, cold in ((0, 10.0, set()), (1, -3.0, {"GEN1"})):
        units = [u for u in F.units_from_campus(camp, ups_rows(), cold=cold) if u.model != "ups"]
        m = F.metrics(F.simulate(units, F.step(dp), t_end=10.0, dt=5e-3))
        row = out[out["hour"] == hour].iloc[0]
        assert row["dp_mw"] == dp and row["nadir_hz"] == pytest.approx(m.nadir_hz, abs=1e-12)


def test_e1_at_every_hour_runs_identical_hours_once(monkeypatch):
    calls = []
    real = F.simulate
    monkeypatch.setattr(F, "simulate", lambda *a, **k: calls.append(1) or real(*a, **k))
    ih, iu = island_tables(dp_up=(10.0, 10.0), dp_down=(-10.0, -10.0), online=(1.0, 1.0))
    out = F.e1_every_hour(ups_campus(), hourly(), ih, iu)
    assert len(out) == 2 and len(calls) == 1


def test_e1_at_every_hour_runs_hours_with_the_same_step_but_other_units_apart(monkeypatch):
    calls = []
    real = F.simulate
    monkeypatch.setattr(F, "simulate", lambda *a, **k: calls.append(1) or real(*a, **k))
    ih, iu = island_tables(dp_up=(10.0, 10.0), dp_down=(-10.0, -10.0), online=(1.0, 1.0))
    h = pd.concat([ups_rows(gen=5.0).assign(period=2030, hour=0), ups_rows(gen=9.0).assign(period=2030, hour=1)],
                  ignore_index=True)
    out = F.e1_every_hour(ups_campus(), h, ih, iu)
    assert len(calls) == 2 and out["nadir_hz"].iloc[0] != out["nadir_hz"].iloc[1]


def test_e1_at_every_hour_keeps_a_ups_on_line_to_see_its_drop_out_but_not_one_that_transfers():
    ih, iu = island_tables(dp_up=(40.0, 40.0), online=(1.0, 1.0))
    on_line = F.e1_every_hour(ups_campus(transfers=False), hourly(), ih, iu)
    imports = on_line[on_line["direction"] == "import"]
    assert list(imports["ups_drops"]) == ["UPS1", "UPS1"]                # 40 MW beyond 20 + 5 MW: below 48 Hz
    transfers = F.e1_every_hour(ups_campus(transfers=True), hourly(), ih, iu)
    assert set(transfers["ups_drops"]) == {""}


def test_e1_at_every_hour_refuses_a_table_off_its_contract():
    ih, iu = island_tables()
    with pytest.raises(ContractError, match="island hourly table"):
        F.e1_every_hour(ups_campus(), hourly(), ih.drop(columns="inertia_mws"), iu)


def test_the_study_runs_e1_on_i4s_dp_ac_and_reports_the_losses():
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    st = F.campus_frequency_study(camp, hourly(), [(2030, 0)], t_end=20.0)
    i4 = IF.island_flow(camp, ups_rows())
    loss = st.losses.iloc[0]
    assert loss["dp_ac_mw"] == pytest.approx(i4.delta_p_ac_mw) and loss["import_lossless_mw"] == 10.0
    assert loss["losses_mw"] == pytest.approx(i4.delta_p_ac_mw - 10.0)
    e1 = st.events[(st.events["event"] == "E1") & st.events["i_max_pu"].isna()].iloc[0]
    assert e1["dp_source"] == "I4 ΔP_AC" and e1["dp_mw"] == pytest.approx(i4.delta_p_ac_mw)
    assert e1["df_qss_hz"] == pytest.approx(i4.df_hz, abs=1e-3)


def test_the_study_runs_each_scenario_s_events():
    camp = ups_campus()
    _, iu = island_tables()
    st = F.campus_frequency_study(camp, hourly(), [(2030, 0)], island_unit_hourly=iu, t_end=20.0)
    ev = st.events
    seamless = ev[ev["scenario"] == "seamless"]
    assert sorted(seamless[seamless["i_max_pu"].isna()]["event"]) == ["E1", "E2", "E3", "E5"]
    assert sorted(seamless["i_max_pu"].dropna()) == [1.2, 1.2, 1.5, 1.5, 2.0, 2.0]
    assert seamless[seamless["event"] == "E2"]["dp_mw"].iloc[0] == 3.0           # the largest pickup block
    i4 = IF.island_flow(camp, ups_rows()).units.set_index("unit_id")["p_mw"]
    e3 = seamless[seamless["event"] == "E3"].iloc[0]
    assert e3["unit"] == "GEN1" and e3["dp_mw"] == pytest.approx(i4["GEN1"])
    assert seamless[seamless["event"] == "E5"]["dp_mw"].iloc[0] == pytest.approx(i4["PV1"])
    # ups_bridged: three blocks, the bridge on I3a's IT load and e(t-1)
    e4 = ev[ev["event"] == "E4"]
    assert list(e4["block"]) == [1, 2, 3] and e4["t_block_s"].iloc[0] == 15.0
    b = st.bridge.iloc[0]
    assert (b["unit"], b["kind"], b["it_mw"], b["e_mwh"]) == ("UPS1", "ups", 20.0, 1.5)                # I3a's, not the 25 MW draw
    assert b["autonomy_s"] == pytest.approx(1.5 * 3600 / 20.0) and b["needed_s"] == 600.0 and not b["ok"]
    # planned: the BESS takes the import at once
    p = st.planned.iloc[0]
    assert p["feasible"] and p["ramp_s"] == 0.0
    # the UPS's walk-in after E1, from I3a's e(t-1)
    u = st.ups[st.ups["event"] == "E1"].iloc[0]
    assert u["e_mwh"] == 1.5 and u["energy_mwh"] == pytest.approx(20.0 * 15.0 / 2 / 3600, rel=1e-9)


def test_the_study_reports_the_gfm_energy_until_the_cold_genset_joins():
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    _, iu = island_tables(online=(0.0, 0.0))
    iu = pd.concat([iu, pd.DataFrame({"period": [2030], "hour": [0], "unit_id": ["BESS1"], "soc_start_mwh": [0.01],
                                      "online": [1.0], "headroom_up_mw": [20.0], "headroom_down_mw": [20.0],
                                      "p_ups_it_mw": [math.nan]})], ignore_index=True)
    st = F.campus_frequency_study(camp, hourly(), [(2030, 0)], island_unit_hourly=iu, t_end=20.0)
    b = st.bridge.set_index("unit").loc["BESS1"]
    units = F.units_from_campus(camp, ups_rows(), cold={"GEN1"})
    tr = F.e1_islanding(units, st.losses["dp_ac_mw"].iloc[0], t_end=20.0)
    sel = tr.t <= 15.0 + 1e-12
    p = 0.0 + tr.dp["BESS1"][sel]
    assert b["join_s"] == 15.0 and b["until_s"] == 15.0
    assert b["energy_mwh"] == pytest.approx(np.trapezoid(p, tr.t[sel]) / 3600.0, rel=1e-12)
    assert b["energy_mwh"] > 10.0 * 15.0 / 3600 * 0.9 and b["e_mwh"] == 0.01 and not b["ok"]


def test_the_study_needs_the_island_requirements():
    camp = build_campus(spec())
    with pytest.raises(ContractError, match="island requirements"):
        F.campus_frequency_study(camp, rows().assign(period=2030, hour=0), [(2030, 0)])
