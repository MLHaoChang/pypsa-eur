"""
DC 03 (owner decision RLR-527): the load-smoothing screen. One battery/UPS,
two duties: the backup duty is reserved first, smoothing gets what is left.
Closed form: a step S through a ramp limit R needs ≈ S MW and S²/(2R) MWh each
way; an oscillation A, T needs the excess over the limit and A·T/π MWh.
"""
from __future__ import annotations

import json
import math
import sys
import pathlib

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import smoothing as S

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402


def _phase(swing=100.0, amp=0.0, period=2.0, depth=None, duration=60.0, interval=1800.0, cod="2030-01-01"):
    return {"cod": cod, "it_mw": swing, "swing_mw": swing,
            "checkpoint": {"interval_s": interval, "duration_s": duration,
                           "depth_mw": swing if depth is None else depth},
            "oscillation": {"amplitude_mw": amp, "period_s": period}}


def _site(storage=(), loads=None):
    """A 48-hour site; ``storage`` is ``(name, p_nom, max_hours, eff, extendable)``."""
    snaps = pd.date_range("2030-01-01", periods=48, freq="h")
    n = pypsa.Network()
    n.set_snapshots(snaps)
    n.add("Bus", "site")
    for name, p, h, eff, ext in storage:
        n.add("StorageUnit", name, bus="site", p_nom=p, max_hours=h, efficiency_dispatch=eff,
              p_nom_extendable=ext)
    for name, mw in (loads or {}).items():
        n.add("Load", name, bus="site", p_set=mw)
    return n


def _screen(n, island, phases, ramp=20.0, osc=1.0, conn=200.0):
    return S.smoothing_screen(n, island, {"phases": phases}, ramp_limit_mw_per_min=ramp,
                              oscillation_limit_pct=osc, connection_mw=conn)


#: A slow walk-in (600 s), so the UPS shapes these tests' steps to the limit.
UPS = {"units": {"ups": {"kind": "ups", "it_load": ["it"], "walk_in_s": 600.0}},
       "requirements": {"bridge_s": {"value": 600.0}}}


def test_a_hundred_megawatt_step_at_twenty_per_minute_moves_about_four_megawatt_hours():
    """The #527 case, corrected: 100 MW through 20 MW/min is a 5 min ramp and
    100·5/2 MW·min ≈ 4.17 MWh, not 0.56 kWh."""
    assert S.step_mwh(100.0, 20.0) == pytest.approx(100.0 * 5.0 / 2.0 / 60.0)
    assert S.step_mwh(100.0, 20.0) == pytest.approx(4.1667, abs=1e-4)


def test_up_and_down_steps_each_need_the_triangle_so_the_band_is_twice_it():
    n = _site([("ups", 150.0, 0.5, 1.0, False)], {"it": 0.0})
    (ph,) = _screen(n, UPS, [_phase(100.0)])["phases"]
    assert ph["step_mwh"] == pytest.approx(S.step_mwh(100.0, 20.0))
    assert ph["need_mw"] == pytest.approx(100.0)
    assert ph["need_mwh"] == pytest.approx(2 * S.step_mwh(100.0, 20.0))     # S²/R
    assert ph["ramp_minutes"] == pytest.approx(5.0)


@pytest.mark.parametrize("duration, expect", [
    (60.0, 100.0 * 60.0 / 3600.0),         # short dip: hold the grid flat, S·d = 1.67 MWh
    (900.0, 100.0 * 100.0 / 2400.0),       # long dip: ramp it, S²/(2R) = 4.17 MWh
])
def test_a_checkpoint_dip_takes_the_cheaper_of_holding_and_ramping(duration, expect):
    assert S.checkpoint_mwh(100.0, duration, 20.0) == pytest.approx(expect)


def test_checkpoints_repeat_every_interval_inside_the_step_band():
    n = _site([("ups", 150.0, 0.5, 1.0, False)], {"it": 0.0})
    (ph,) = _screen(n, UPS, [_phase(100.0, interval=1800.0)])["phases"]
    assert ph["checkpoints_per_day"] == pytest.approx(48.0)
    assert ph["checkpoint_mwh"] <= ph["step_mwh"]
    (ph,) = _screen(n, UPS, [_phase(100.0, duration=0.0)])["phases"]
    assert ph["checkpoints_per_day"] == 0.0


@pytest.mark.parametrize("amp, period, conn, excess", [
    (10.0, 2.0, 200.0, 9.0),         # 0.5 Hz, in band: 1 % of 200 MW p-p is 1 MW peak
    (10.0, 20.0, 200.0, 0.0),        # 0.05 Hz, outside the band
    (0.5, 2.0, 200.0, 0.0),          # in band, below the limit
])
def test_an_oscillation_needs_its_excess_over_the_limit_inside_the_band(amp, period, conn, excess):
    n = _site([("ups", 150.0, 0.5, 1.0, False)], {"it": 0.0})
    (ph,) = _screen(n, UPS, [_phase(50.0, amp=amp, period=period)], conn=conn)["phases"]
    assert ph["oscillation_excess_mw"] == pytest.approx(excess)
    assert ph["oscillation_mwh"] == pytest.approx(excess * period / (math.pi * 3600.0))
    assert ph["need_mw"] == pytest.approx(50.0 + excess)


def test_no_battery_or_ups_is_unknown():
    n = _site([], {"it": 0.0})
    out = _screen(n, {"units": {}, "requirements": {}}, [_phase()])
    assert out["verdict"] == "unknown" and out["note"].startswith(S.NO_BATTERY)
    assert _screen(n, None, [_phase()])["note"] == S.NO_BATTERY


def test_an_extendable_battery_is_sized_by_the_run_and_not_counted():
    n = _site([("bess", 0.0, 4.0, 0.95, True)])
    out = _screen(n, {"units": {"bess": {"kind": "bess"}}, "requirements": {}}, [_phase()])
    assert out["verdict"] == "unknown" and "bess sized by the run" in out["note"]


def test_without_an_envelope_there_is_nothing_to_screen():
    assert S.smoothing_screen(_site(), UPS, None, ramp_limit_mw_per_min=20.0,
                              oscillation_limit_pct=1.0, connection_mw=200.0) is None


def test_up_and_down_steps_share_one_soc_band():
    """A battery with exactly S²/(2R) free MWh can give the up step or take
    the down step, not both from one state of charge: it is short."""
    e = S.step_mwh(100.0, 20.0)
    n = _site([("ups", 150.0, e / 150.0, 1.0, False)], {"it": 0.0})
    (ph,) = _screen(n, UPS, [_phase(100.0)])["phases"]
    assert ph["reserve_mwh"] == pytest.approx(e)
    assert ph["verdict"] == "short" and ph["short"] == ["MWh"]
    n = _site([("ups", 150.0, 2 * e / 150.0, 1.0, False)], {"it": 0.0})
    assert _screen(n, UPS, [_phase(100.0)])["verdict"] == "covered"


def _rt(p_nom, max_hours, critical, sustained_h):
    n = _site([("rt", p_nom, max_hours, 1.0, False)], {"critical": critical})
    island = {"units": {"rt": {"kind": "bess"}, "critical": {"kind": "load", "critical": True}},
              "requirements": {"sustained_h": {"value": sustained_h}}}
    return n, island


def test_the_ride_through_battery_keeps_the_outage_reserve_in_energy_only():
    """20 MW / 80 MWh, a 3 MW critical load with no gensets for 24 h: R =
    72 MWh is kept, 8 MWh and all 20 MW are left for smoothing."""
    n, island = _rt(20.0, 4.0, 3.0, 24.0)
    (ph,) = _screen(n, island, [_phase(10.0)])["phases"]
    assert ph["backup_mwh"] == pytest.approx(72.0)
    assert ph["reserve_mwh"] == pytest.approx(8.0)
    assert ph["reserve_mw"] == pytest.approx(20.0)
    assert "72 MWh outage reserve" in ph["note"]


def test_a_reserve_that_takes_all_the_energy_leaves_nothing_to_smooth():
    n, island = _rt(20.0, 4.0, 4.0, 24.0)                  # R = 96 MWh > 80 MWh
    (ph,) = _screen(n, island, [_phase(10.0)])["phases"]
    assert ph["reserve_mwh"] == 0.0
    assert ph["verdict"] == "short" and ph["short"] == ["MWh"]


def test_the_ups_keeps_its_bridge_energy_and_lends_its_power():
    n = _site([("ups", 40.0, 0.25, 0.95, False)], {"it": 30.0})
    (ph,) = _screen(n, UPS, [_phase(20.0)], ramp=4.0, conn=40.0)["phases"]
    assert ph["backup_mwh"] == pytest.approx(30.0 * 600.0 / 3600.0)          # 5 MWh
    assert ph["reserve_mwh"] == pytest.approx(40.0 * 0.25 * 0.95 - 5.0)      # 4.5 MWh
    assert ph["reserve_mw"] == pytest.approx(40.0)
    assert ph["verdict"] == "covered"                                          # 20 MW, 2·20²/480 = 1.67 MWh


def test_a_unit_not_yet_built_is_not_counted_in_an_earlier_phase():
    n = _site([("ups", 40.0, 0.25, 1.0, False)], {"it": 0.0})
    n.storage_units.at["ups", "build_year"] = 2035
    phases = [_phase(10.0, cod="2030-01-01"), _phase(10.0, cod="2035-01-01")]
    a, b = _screen(n, UPS, phases)["phases"]
    assert a["verdict"] == "unknown" and b["verdict"] == "covered"


def test_the_data_centre_campus_end_to_end():
    """The single-year campus with the AI profile: a 23.4 MW step through its
    4 MW/min limit is 1.14 MWh each way; its 40 MW / 10 MWh UPS keeps
    ≈ 5.75 MWh to bridge the IT load for 600 s and covers the rest."""
    tid = "eh_datacenter"
    spec = T.dc_load_spec(tid, {})
    n = T.BUILDERS[tid](load_spec=spec)
    env = T.dc_load_meta(tid, spec)["envelope"]
    out = S.smoothing_screen(n, json.loads(json.dumps(T.ISLAND_CONFIG[tid])), env,
                             ramp_limit_mw_per_min=4.0, oscillation_limit_pct=1.0, connection_mw=40.0)
    (ph,) = out["phases"]
    assert ph["step_mw"] == pytest.approx(23.4)
    assert ph["step_mwh"] == pytest.approx(23.4 ** 2 / 480.0)
    assert ph["oscillation_excess_mw"] == pytest.approx(3.6 - 0.2)
    assert ph["units"] == ["ups_battery"]
    assert ph["reserve_mw"] == pytest.approx(40.0)
    assert 0 < ph["reserve_mwh"] < 10.0 * 0.95
    # Its UPS walks the 23.4 MW onto the grid over 15 s: 93.6 MW/min, not 4.
    assert ph["covered_ups_mw"] == pytest.approx(23.4)
    assert ph["ups_ramp_mw_per_min"] == pytest.approx(23.4 * 60.0 / 15.0)
    assert out["verdict"] == "short" and ph["short"] == ["UPS walk-in"]
    assert np.isfinite(ph["need_mwh"])


def test_a_ride_through_shortfall_does_not_eat_the_ups_energy():
    """R = 96 MWh over an 80 MWh ride-through battery leaves it 0, and the
    UPS still has its own 10 MWh less its bridge."""
    n = _site([("rt", 20.0, 4.0, 1.0, False), ("ups", 40.0, 0.25, 1.0, False)],
              {"critical": 4.0, "it": 6.0})
    island = {"units": {"rt": {"kind": "bess"}, "critical": {"kind": "load", "critical": True},
                        "ups": {"kind": "ups", "it_load": ["it"]}},
              "requirements": {"sustained_h": {"value": 24.0}, "bridge_s": {"value": 600.0}}}
    (ph,) = _screen(n, island, [_phase(10.0)])["phases"]
    assert ph["reserve_mwh"] == pytest.approx(10.0 - 6.0 * 600.0 / 3600.0)
    assert ph["reserve_mw"] == pytest.approx(60.0)
    assert "blind to in-hour ramps" in ph["note"]


def test_after_battery_oscillation_is_energy_limited_after_the_step():
    """Power to spare, but only 0.5 kWh left above the step's S²/R band: the
    oscillation (10 MW at 2 s, A·T/π = 1.77 kWh) is covered for
    0.0005·π·3600/2 = 2.83 MW of its amplitude, not by the power term."""
    s, r = 10.0, 20.0
    e_one = S.step_mwh(s, r)
    reserve = {"battery": {"mw": 100.0, "mwh": 2 * e_one + 0.0005}, "ups": {"mw": 0.0, "mwh": 0.0}}
    c = S.coverage(s, e_one, 10.0, 2.0, r, reserve)
    assert c["covered_battery_mw"] == pytest.approx(s)
    assert c["oscillation_covered_mw"] == pytest.approx(0.0005 * math.pi * 3600.0 / 2.0)
    reserve["battery"] = {"mw": 14.0, "mwh": 2 * e_one + 1.0}        # energy no longer binds; power does
    assert S.coverage(s, e_one, 10.0, 2.0, r, reserve)["oscillation_covered_mw"] == pytest.approx(4.0)


def test_a_ups_also_named_for_ride_through_is_counted_once():
    """40 MW / 10 MWh, a UPS and in ride_through_storage, no critical load:
    its MW counts once (40, not 80) and it keeps its 5 MWh bridge."""
    n = _site([("ups", 40.0, 0.25, 1.0, False)], {"it": 30.0})
    island = {"units": {"ups": {"kind": "ups", "it_load": ["it"]}},
              "requirements": {"bridge_s": {"value": 600.0}, "sustained_h": {"value": 1.0},
                               "ride_through_storage": ["ups"]}}
    (ph,) = _screen(n, island, [_phase(5.0)])["phases"]
    assert ph["units"] == ["ups"]
    assert ph["reserve_mw"] == pytest.approx(40.0)
    assert ph["backup_mwh"] == pytest.approx(5.0) and ph["reserve_mwh"] == pytest.approx(5.0)


def test_the_ups_keeps_the_whole_it_load_even_above_its_rating():
    """Island row (b) holds P_it·bridge_s, not the rating: 50 MW for 600 s is
    8.33 MWh from a 40 MW UPS."""
    n = _site([("ups", 40.0, 0.25, 1.0, False)], {"it": 50.0})
    (ph,) = _screen(n, UPS, [_phase(5.0)])["phases"]
    assert ph["backup_mwh"] == pytest.approx(50.0 * 600.0 / 3600.0)
    assert ph["reserve_mwh"] == pytest.approx(10.0 - 50.0 / 6.0)


def test_a_dip_deeper_than_the_swing_sets_the_band():
    n = _site([("ups", 500.0, 1.0, 1.0, False)], {"it": 0.0})
    (ph,) = _screen(n, UPS, [_phase(10.0, depth=100.0, duration=600.0)])["phases"]
    assert ph["checkpoint_mwh"] > ph["step_mwh"]
    assert ph["need_mwh"] == pytest.approx(2.0 * ph["checkpoint_mwh"])


def test_a_ups_shapes_the_step_only_at_its_walk_in_rate():
    """23.4 MW on a 40 MW UPS that walks in over 10 s: the grid sees
    23.4/10·60 = 140 MW/min, above a 4 MW/min limit; only a walk-in of
    60·23.4/4 = 351 s holds it to the limit."""
    n = _site([("ups", 40.0, 0.25, 1.0, False)], {"it": 10.0})
    for walk_in, verdict in ((10.0, "short"), (351.0, "covered")):
        island = {"units": {"ups": {"kind": "ups", "it_load": ["it"], "walk_in_s": walk_in}},
                  "requirements": {"bridge_s": {"value": 600.0}}}
        (ph,) = _screen(n, island, [_phase(23.4)], ramp=4.0, conn=40.0)["phases"]
        assert ph["ups_ramp_mw_per_min"] == pytest.approx(23.4 * 60.0 / walk_in)
        assert ph["verdict"] == verdict


def test_a_converter_battery_shapes_to_the_limit_and_the_ups_takes_the_rest():
    """A 10 MW ride-through battery with energy to spare takes 10 of a
    23.4 MW step to the limit; the UPS carries 13.4 MW at its walk-in."""
    n = _site([("rt", 10.0, 4.0, 1.0, False), ("ups", 40.0, 0.25, 1.0, False)], {"it": 10.0})
    island = {"units": {"rt": {"kind": "bess"}, "ups": {"kind": "ups", "it_load": ["it"], "walk_in_s": 15.0}},
              "requirements": {"bridge_s": {"value": 600.0}, "sustained_h": {"value": 1.0}}}
    (ph,) = _screen(n, island, [_phase(23.4)], ramp=4.0, conn=40.0)["phases"]
    assert ph["covered_battery_mw"] == pytest.approx(10.0)
    assert ph["covered_ups_mw"] == pytest.approx(13.4)
    assert ph["ups_ramp_mw_per_min"] == pytest.approx(13.4 * 4.0)


def test_a_ups_without_a_walk_in_is_not_counted_as_shaping():
    n = _site([("ups", 40.0, 0.25, 1.0, False)], {"it": 10.0})
    island = {"units": {"ups": {"kind": "ups", "it_load": ["it"]}}, "requirements": {}}
    (ph,) = _screen(n, island, [_phase(5.0)])["phases"]
    assert ph["covered_ups_mw"] == 0.0
    assert ph["verdict"] == "unknown" and ph["missing"] == ["UPS walk-in not known"] and ph["short"] == []
    assert ph["note"].startswith("UPS walk-in not known")
    # A measured shortfall still wins over the missing input.
    (ph,) = _screen(_site([("ups", 1.0, 0.01, 1.0, False)], {"it": 0.1}), island, [_phase(5.0)])["phases"]
    assert ph["verdict"] == "short" and ph["missing"] == ["UPS walk-in not known"]
