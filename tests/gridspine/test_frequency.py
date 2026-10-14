"""First-pass island frequency check, I6 (plan 2026-10-07-campus-island-operation).

Oracles:
* inertia only: a constant ΔP gives a linear ramp, RoCoF = ΔP f0 / (2 H S);
* one governor: the linear second-order system solved exactly (matrix
  exponential), nadir and its time within 0.1 %;
* settled frequency: Δf_qss = ΔP / ΣK;
* D'Arco–Suul, two independently coded models: the native droop GFM
  (filtered P, imposed frequency) against the aggregate engine's droop and
  VSM forms; 3.636 Hz/s at 0+, 0.40 Hz settled, and the plan's simulated
  2.88 / 1.09 Hz/s, -0.555 Hz at 0.415 s;
* a droop GFM alone: the closed-form first order with time constant tau_f;
* saturation deepens the nadir monotonically; an isochronous genset returns
  to 0; with every GFM at a limit and no machine, collapse;
* I4: E1 at an island hour settles at I4's Δf within 1 mHz, with and
  without a unit at its limit.
"""
import math

import numpy as np
import pytest
from scipy.linalg import expm
from scipy.optimize import brentq

from gridspine.ingest.campus import build_campus
from gridspine.static import frequency as F
from gridspine.static import island_flow as IF
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_island_schema import genset

F0 = 50.0


def gen(**kw):
    """The plan's genset: H 1.5 s on 10 MVA, P_n 10 MW, R 4 %, T_g 0.5 s."""
    base = dict(name="G", model="genset", p0=5.0, p_n=10.0, s_n=10.0, r=0.04, h=1.5, t_g=0.5)
    base.update(kw)
    return F.Unit(**base)


def bess(model="gfm_droop", **kw):
    """The plan's BESS: P_n 10 MW, R 0.04, tau_f 0.1 s (or the VSM with
    H_v S_n = tau_f P_n / (2 R))."""
    base = dict(name="B", model=model, p0=0.0, p_n=10.0, s_n=10.0, p_min=-10.0, p_max=10.0, r=0.04, i_max_mw=12.0)
    if model == "gfm_droop":
        base["tau_f"] = 0.1
    else:
        base["h"] = 0.1 * 10.0 / (2 * 0.04) / 10.0
    base.update(kw)
    return F.Unit(**base)


def test_inertia_alone_ramps_linearly_at_dp_f0_over_2hs():
    g = gen(t_g=1e12)                                        # a governor that never moves
    tr = F.simulate([g], F.step(4.0), t_end=1.0)
    rocof = -4.0 * F0 / (2 * 1.5 * 10.0)
    assert tr.rocof0 == pytest.approx(rocof, rel=1e-12)
    assert np.allclose(tr.df, rocof * tr.t, rtol=0, atol=1e-9)


def test_one_governor_matches_the_exact_second_order_solution():
    g = gen()
    tr = F.simulate([g], F.step(4.0), t_end=8.0)
    m, k, tg = 2 * 1.5 * 10.0 / F0, 10.0 / (0.04 * F0), 0.5
    a = np.array([[0.0, 1.0 / m], [-k / tg, -1.0 / tg]])
    b = np.array([-4.0 / m, 0.0])
    xs = -np.linalg.solve(a, b)                              # the steady state

    def exact(t):
        return (xs + expm(a * t) @ (np.zeros(2) - xs))

    t_nadir = brentq(lambda t: (a @ exact(t) + b)[0], 0.05, 1.2)   # dΔf/dt = 0, the first extremum
    nadir = exact(t_nadir)[0]
    mt = F.metrics(tr)
    assert mt.nadir_hz == pytest.approx(nadir, rel=1e-3)
    assert mt.t_nadir_s == pytest.approx(t_nadir, rel=1e-3)
    assert mt.df_qss_hz == pytest.approx(-4.0 / k, rel=1e-3)


def test_the_settled_frequency_is_the_step_over_the_sum_of_the_droop_gains():
    units = [gen(), bess(), bess("gfm_vsm", name="V", p_n=20.0, s_n=20.0, p_min=-20.0, p_max=20.0, r=0.05,
                                 i_max_mw=24.0)]
    tr = F.simulate(units, F.step(6.0), t_end=20.0)
    k = 10 / (0.04 * F0) + 10 / (0.04 * F0) + 20 / (0.05 * F0)
    assert F.metrics(tr).df_qss_hz == pytest.approx(-6.0 / k, rel=1e-6)


@pytest.mark.parametrize("model", ["gfm_droop", "gfm_vsm"])
def test_darco_suul_the_native_droop_and_the_engine_agree(model):
    tr = F.simulate([gen(), bess(model)], F.step(4.0), t_end=5.0)
    t_ref, df_ref = F.native_droop_reference(gen(), bess(), 4.0, t_end=5.0)
    assert np.allclose(tr.df, df_ref, rtol=0, atol=1e-7)
    # closed form at 0+ and settled
    assert tr.rocof0 == pytest.approx(-4.0 * F0 / (30.0 + 25.0), rel=1e-12)
    assert tr.rocof0 == pytest.approx(-3.636, abs=5e-4)
    m100, m500 = F.metrics(tr, window_ms=100.0), F.metrics(tr, window_ms=500.0)
    assert m500.df_qss_hz == pytest.approx(-0.40, abs=1e-4)
    # the plan's simulated values (re-review, I1 genset defaults)
    assert m100.rocof_window_hz_per_s == pytest.approx(-2.88, abs=5e-3)
    assert m500.rocof_window_hz_per_s == pytest.approx(-1.09, abs=5e-3)
    assert m500.nadir_hz == pytest.approx(-0.555, abs=5e-4)
    assert m500.t_nadir_s == pytest.approx(0.415, abs=2e-3)


def test_the_rejected_lag_model_would_double_the_initial_rocof():
    """A droop GFM taken as a first-order lag adds no inertia: 4 x 50 / 30 =
    6.667 Hz/s at 0+, against the native droop's 3.636: the tests above tell
    the two apart."""
    tr = F.simulate([gen(), bess()], F.step(4.0), t_end=0.01)
    assert abs(tr.rocof0) == pytest.approx(3.636, abs=5e-4)
    assert abs(abs(tr.rocof0) - 4.0 * F0 / 30.0) > 3.0


def test_a_droop_gfm_alone_is_a_first_order_response_with_its_filter_time():
    tr = F.simulate([bess()], F.step(4.0), t_end=1.0)
    k = 10.0 / (0.04 * F0)
    assert np.allclose(tr.df, -4.0 / k * (1 - np.exp(-tr.t / 0.1)), rtol=0, atol=1e-8)


def test_a_tighter_gfm_limit_deepens_the_nadir_monotonically():
    nadirs = []
    for p_max in (10.0, 3.0, 2.0, 1.5):
        tr = F.simulate([gen(), bess(p_max=p_max)], F.step(4.0), t_end=5.0)
        nadirs.append(F.metrics(tr).nadir_hz)
        assert ("B" in tr.saturated) == (p_max < 3.0)
    assert all(b <= a for a, b in zip(nadirs, nadirs[1:]))
    assert nadirs[-1] < nadirs[0] - 0.05


def test_the_current_limit_saturates_a_gfm_like_its_p_limit():
    a = F.simulate([gen(), bess(p_max=2.0)], F.step(4.0), t_end=3.0)
    b = F.simulate([gen(), bess(i_max_mw=2.0)], F.step(4.0), t_end=3.0)
    assert np.allclose(a.df, b.df, atol=1e-12) and "B" in b.saturated


def test_an_isochronous_genset_returns_the_frequency_to_nominal():
    tr = F.simulate([gen(isochronous=True)], F.step(4.0), t_end=40.0)
    assert abs(F.metrics(tr).df_qss_hz) < 1e-3
    assert F.metrics(tr).nadir_hz < -0.1


def test_with_every_gfm_at_its_limit_and_no_machine_the_frequency_collapses():
    tr = F.simulate([bess(p_max=2.0)], F.step(4.0), t_end=2.0)
    assert tr.collapse is not None
    m = F.metrics(tr)
    assert F.judge(m, LIMITS).binding == F.COLLAPSE
    assert F.COLLAPSE == "frequency collapse: no grid-forming headroom"


def test_a_cold_genset_joins_on_its_clock_and_only_then_responds():
    g = gen(p0=0.0, join_s=1.0, step_mw=5.0, ramp_mw_per_s=2.0)
    tr = F.simulate([g, bess()], F.step(4.0), t_end=4.0)
    before, after = tr.t < 1.0, tr.t > 1.5
    assert np.all(tr.dp["G"][before] == 0.0) and tr.dp["G"][after].max() > 0.5
    assert tr.rocof0 == pytest.approx(-4.0 / (10.0 * 0.1 / (0.04 * F0)), rel=1e-12)   # the BESS alone at 0+
    assert F.metrics(tr).df_qss_hz == pytest.approx(-0.40, abs=2e-3)


def test_the_load_acceptance_step_and_ramp_hold_the_governor():
    g = gen(t_g=0.01, step_mw=1.0, ramp_mw_per_s=0.5)
    tr = F.simulate([g], F.step(4.0), t_end=3.0)                     # alone: its governor asks for more
    assert np.all(tr.dp["G"] <= 1.0 + 0.5 * tr.t + 1e-9)
    assert tr.dp["G"][-1] == pytest.approx(1.0 + 0.5 * 3.0, abs=1e-3)


def test_a_genset_at_its_limit_holds_it_inside_the_rk4_step():
    # G at 12 of 15 MW gives 3 MW; B (5 MW/Hz, no limit) the other 17: settled exactly at -3.4 Hz. Without
    # anti-windup the RK4 stages count G's governor beyond its limit, (ref - x) dt / (2 T_g): 0.11 MW at 5 ms
    g = gen(p0=12.0, p_n=15.0, s_n=15.0, p_max=15.0)
    b = bess(p_min=-math.inf, p_max=math.inf, i_max_mw=math.inf)
    for dt in (5e-3, 1e-3):
        tr = F.simulate([g, b], F.step(20.0), t_end=40.0, dt=dt)
        assert tr.dp["G"][-1] == 3.0 and F.metrics(tr).df_qss_hz == pytest.approx(-17.0 / 5.0, abs=1e-9)
    # and at the lower bound: a 30 MW export lost backs G off to 0 MW (its governor asks for -27), B absorbs 18
    tr = F.simulate([g, b], F.step(-30.0), t_end=40.0, dt=5e-3)
    assert tr.dp["G"][-1] == -12.0 and F.metrics(tr).df_qss_hz == pytest.approx(18.0 / 5.0, abs=1e-9)


def test_a_genset_on_its_ramp_beside_a_former_is_counted_on_the_moving_bound():
    # G steps 1 MW and ramps 0.5 MW/s to its 15 MW, B (no limit) carries the rest. On the ramp the bound moves:
    # the 5 ms run matches a 0.5 ms one (held still at the bound it would lag, 8e-3 Hz at 2 s); at its fixed
    # limit the ramp no longer applies, so the island settles at the balance's -17 / 5 Hz exactly
    g = gen(p0=12.0, p_n=15.0, s_n=15.0, p_max=15.0, step_mw=1.0, ramp_mw_per_s=0.5)
    b = bess(p_min=-math.inf, p_max=math.inf, i_max_mw=math.inf)
    coarse, fine = (F.simulate([g, b], F.step(20.0), t_end=40.0, dt=dt) for dt in (5e-3, 5e-4))
    at = lambda tr: tr.df[int(np.searchsorted(tr.t, 2.0))]                          # noqa: E731
    assert at(coarse) == pytest.approx(at(fine), abs=1e-6)
    assert coarse.dp["G"][-1] == 3.0 and F.metrics(coarse).df_qss_hz == pytest.approx(-17.0 / 5.0, abs=1e-9)


def test_a_genset_on_its_ramp_rides_it_without_lagging():
    # the bound moves at the ramp, so held at it the governor moves with it, not a step behind
    g = gen(t_g=0.01, step_mw=1.0, ramp_mw_per_s=0.5)                # alone: its governor asks for more
    tr = F.simulate([g], F.step(4.0), t_end=3.0, dt=5e-3)
    on = tr.t > 0.05
    assert np.allclose(tr.dp["G"][on], 1.0 + 0.5 * tr.t[on], atol=1e-9)


def test_a_genset_with_no_ramp_limit_starts_at_its_join_without_a_nan():
    # the bounds at the join itself: an unlimited ramp times no time elapsed is 0, not inf x 0
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        tr = F.simulate([gen()], F.step(4.0), t_end=1.0, dt=5e-3)
    assert np.isfinite(tr.df).all()


def iso_windup():
    """The isochronous integral without anti-windup: it integrates Δf at every
    state, as before the fix."""
    return lambda u, p, df, t: df


@pytest.mark.parametrize("p0, dp", [(0.0, 2.0), (8.0, -2.0)], ids=["upper bound", "lower bound"])
def test_an_isochronous_genset_held_at_a_bound_does_not_wind_up_its_integral(monkeypatch, p0, dp):
    # 2 MW on a genset with a 0.5 MW load step and a 0.08 MW/s ramp: held at its bound for about 19 s while the
    # frequency runs away. Integrating that error, the integral overshoots by about 61.8 Hz once the ramp
    # catches up; frozen at the bound, the frequency comes back with 0.05 Hz of overshoot
    g = gen(isochronous=True, p0=p0, step_mw=0.5, ramp_mw_per_s=0.08)
    tr = F.simulate([g], F.step(dp), t_end=200.0, dt=5e-3)
    away = np.sign(dp)                                                   # +1: the frequency falls first
    assert abs(tr.df[-1]) < 1e-4
    assert np.max(-away * tr.df) == pytest.approx(23.541, abs=1e-3)     # the excursion while held
    assert np.max(away * tr.df) < 0.1                                    # no overshoot back past f0
    monkeypatch.setattr(F, "_hold_integral", iso_windup())
    wound = F.simulate([g], F.step(dp), t_end=200.0, dt=5e-3)
    assert np.max(away * wound.df) > 30.0                                # what the windup did


@pytest.mark.parametrize("p0, dp", [(5.0, 2.0), (10.0, -2.0)], ids=["unclamped", "at its bound, pulled back in"])
def test_the_integral_hold_changes_nothing_unless_the_error_pushes_outward(monkeypatch, p0, dp):
    # unclamped, or at full output when load drops (the error pulls the genset back inside its range): the
    # integral integrates as it did without the hold, to the bit
    g = gen(isochronous=True, p0=p0)
    held = F.simulate([g], F.step(dp), t_end=30.0, dt=5e-3)
    monkeypatch.setattr(F, "_hold_integral", iso_windup())
    assert np.array_equal(held.df, F.simulate([g], F.step(dp), t_end=30.0, dt=5e-3).df)


def fw_pv(**kw):
    base = dict(name="PV", model="gfl_fw", p0=8.0, p_n=10.0, s_n=10.0, p_min=0.0, p_max=8.0,
                fw={"k_mw_per_hz": 10.0, "deadband_hz": 0.2, "delay_s": 0.2, "ramp_mw_per_s": 20.0,
                    "p_limit_mw": 6.0})
    base.update(kw)
    return F.Unit(**base)


def test_frequency_watt_pv_curtails_an_export_beyond_its_deadband():
    """The site exports 3 MW when the PCC opens: over-frequency. Settled, the
    BESS's K Δf and the PV's K_fw (Δf - db) share it: Δf = (3 + 10 * 0.2) / (5 + 10)."""
    tr = F.simulate([bess(), fw_pv()], F.step(-3.0), t_end=10.0)
    df = (3.0 + 10.0 * 0.2) / (10.0 / (0.04 * F0) + 10.0)
    assert F.metrics(tr).df_qss_hz == pytest.approx(df, rel=1e-6)
    assert tr.dp["PV"][-1] == pytest.approx(-10.0 * (df - 0.2), rel=1e-6)
    assert np.all(tr.dp["PV"][tr.t < 0.2] == 0.0)                                   # its delay


def test_pv_frequency_watt_has_no_upward_reserve_and_stays_inside_its_deadband():
    tr = F.simulate([bess(), fw_pv()], F.step(4.0), t_end=5.0)                      # under-frequency
    assert np.all(tr.dp["PV"] == 0.0)
    small = F.simulate([bess(), fw_pv()], F.step(-0.5), t_end=5.0)                  # 0.1 Hz: inside 0.2
    assert np.all(small.dp["PV"] == 0.0)


def test_the_frequency_watt_ramp_and_cap_hold():
    tr = F.simulate([bess(), fw_pv(fw={"k_mw_per_hz": 100.0, "deadband_hz": 0.0, "delay_s": 0.0,
                                       "ramp_mw_per_s": 2.0, "p_limit_mw": 1.5})], F.step(-3.0), t_end=5.0)
    assert np.all(np.diff(tr.dp["PV"]) >= -2.0 * 1e-3 - 1e-12)
    assert tr.dp["PV"].min() == pytest.approx(-1.5, abs=1e-9)


def test_load_damping_softens_the_settled_deviation():
    tr = F.simulate([bess()], F.step(4.0), t_end=2.0, damping=50.0)
    k = 10.0 / (0.04 * F0)
    assert F.metrics(tr).df_qss_hz == pytest.approx(-4.0 / (k + 50.0 / F0), rel=1e-6)


# ── metrics and limits ──────────────────────────────────────────────────────

def _lim(rocof=1.0, f_min=47.5, f_max=51.5, band=1.0):
    return {"rocof_hz_per_s": rocof, "f_min_hz": f_min, "f_max_hz": f_max, "qss_band_hz": band}


LIMITS = {"transition": _lim(), "steady_island": _lim()}


def test_the_time_to_return_is_the_last_exit_from_the_band_around_the_settled_value():
    tr = F.simulate([gen(), bess()], F.step(4.0), t_end=5.0)
    m = F.metrics(tr, band_hz=0.05)
    outside = np.abs(tr.df - m.df_qss_hz) > 0.05
    assert m.t_return_s == pytest.approx(tr.t[np.flatnonzero(outside)[-1]])
    assert 0.415 < m.t_return_s < 5.0


def test_judge_names_the_most_sensitive_broken_limit():
    m = F.Metrics(rocof0_hz_per_s=-2.0, rocof_window_hz_per_s=-0.8, window_ms=500.0, nadir_hz=-3.2,
                  t_nadir_s=0.4, df_qss_hz=-0.5, t_return_s=2.0, saturated={}, collapse=None)
    v = F.judge(m, {"transition": _lim(f_min=47.0), "steady_island": _lim()},
                equipment=[("UPS window, 18:00", 47.5, 52.5), ("chiller CH1 f_trip", 47.0, math.nan)])
    assert not v.ok
    assert v.binding == "UPS window, 18:00, nadir 46.80 Hz against 47.5 Hz"
    assert v.failures == ["UPS window, 18:00, nadir 46.80 Hz against 47.5 Hz",
                          "transition nadir 46.80 Hz against 47 Hz",
                          "chiller CH1 f_trip, nadir 46.80 Hz against 47 Hz"]
    shallow = F.judge(F.Metrics(-2.0, -0.8, 500.0, -2.9, 0.4, -0.5, 2.0, {}, None),
                      {"transition": _lim(f_min=47.0), "steady_island": _lim()},
                      equipment=[("UPS window, 18:00", 47.5, 52.5)])
    assert shallow.failures == ["UPS window, 18:00, nadir 47.10 Hz against 47.5 Hz"]   # 47.1 clears 47.0


def test_judge_passes_inside_every_limit_and_checks_rocof_and_the_steady_band():
    tr = F.simulate([gen(), bess()], F.step(4.0), t_end=5.0)
    m = F.metrics(tr, window_ms=500.0)
    assert F.judge(m, {"transition": _lim(rocof=2.0), "steady_island": _lim()}).ok
    tight = F.judge(m, {"transition": _lim(rocof=1.0), "steady_island": _lim(band=0.3)})
    # 0.40 against 0.3 Hz is 33 % over; 1.09 against 1 Hz/s is 9 % over
    assert tight.failures == ["steady island deviation 0.40 Hz against 0.3 Hz",
                              "transition RoCoF 1.09 Hz/s over 500 ms against 1 Hz/s"]
    assert tight.binding.startswith("steady island deviation")


def test_an_over_frequency_is_judged_against_the_upper_limits():
    m = F.Metrics(1.0, 0.5, 500.0, 2.0, 0.3, 1.2, 1.0, {}, None)
    v = F.judge(m, LIMITS)
    assert v.failures == ["transition peak 52.00 Hz against 51.5 Hz",
                          "steady island deviation 1.20 Hz against 1 Hz"]       # 51.2 Hz is inside 51.5
    high = F.judge(F.Metrics(1.0, 0.5, 500.0, 2.0, 0.3, 1.8, 1.0, {}, None), LIMITS)
    assert "steady island 51.80 Hz against 47.5-51.5 Hz" in high.failures


# ── events and the campus ───────────────────────────────────────────────────

def test_a_unit_trip_loses_its_output_and_its_response():
    units = [gen(), bess(), bess("gfm_droop", name="B2", p0=3.0)]
    tr = F.e3_unit_trip(units, "B2", t_end=5.0)
    assert "B2" not in tr.dp
    assert F.metrics(tr).df_qss_hz == pytest.approx(-3.0 / (2 * 10.0 / (0.04 * F0)), rel=1e-6)


def test_the_campus_units_carry_i4s_p0_and_their_control_data():
    camp = build_campus(spec())
    units = {u.name: u for u in F.units_from_campus(camp, rows(load=25.0))}
    assert set(units) == {"BESS1", "GEN1"}                    # PV1 has no island data: no response
    b, g = units["BESS1"], units["GEN1"]
    assert (b.model, b.r, b.tau_f, b.p_min, b.p_max) == ("gfm_droop", 0.04, 0.1, -20.0, 20.0)
    assert b.i_max_mw == pytest.approx(1.2 * 22.0)
    gs = genset()
    assert (g.p0, g.h, g.t_g, g.step_mw) == (5.0, gs["H_s"]["value"], gs["T_g_s"]["value"],
                                             gs["load_step_max_pct"]["value"] / 100 * 10.0)
    cold = {u.name: u for u in F.units_from_campus(camp, rows(load=25.0), cold={"GEN1"})}["GEN1"]
    assert cold.p0 == 0.0 and cold.join_s == gs["start_s"]["value"] + gs["sync_s"]["value"]


@pytest.mark.parametrize("load, saturates", [(25.0, False), (35.0, True)])
def test_e1_settles_at_i4s_island_frequency(load, saturates):
    """Two code paths fed the same ΔP and the same K = P_n / R: I4's
    pandapower slack with its outer loops, and this ODE's steady state."""
    camp = build_campus(spec())
    r = rows(load=load)
    i4 = IF.island_flow(camp, r)
    assert i4.viable
    assert (i4.units.set_index("unit_id").at["GEN1", "saturated"] == "p_limit") == saturates
    tr = F.e1_islanding(F.units_from_campus(camp, r), i4.delta_p_ac_mw, t_end=40.0)
    assert F.metrics(tr).df_qss_hz == pytest.approx(i4.df_hz, abs=1e-3)
    if saturates:                                              # held at P_n by its governor clamp
        assert tr.dp["GEN1"][-1] == pytest.approx(10.0 - 5.0, abs=1e-9)


# ── closing tests (mutation round 1) ─────────────────────────────────────────

def test_a_gfm_feeds_its_inertial_power_at_0_plus_and_saturates_on_it():
    free = F.simulate([gen(), bess()], F.step(4.0), t_end=0.01)
    m_b = 10.0 * 0.1 / (0.04 * F0)
    assert free.dp["B"][0] == pytest.approx(-m_b * free.rocof0, rel=1e-12)      # 1.818 MW, before Δf moves
    held = F.simulate([gen(), bess(p_max=1.0)], F.step(4.0), t_end=1.0)
    assert held.saturated["B"] == 0.0


@pytest.mark.parametrize("kw", [{"p_min": -1.0}, {"i_max_mw": 1.0}])
def test_an_absorbing_gfm_saturates_at_its_lower_limit(kw):
    """An export lost on islanding: the BESS must absorb 4 MW, but holds at
    -1 MW (its P limit or its current limit), so the genset takes the rest."""
    tr = F.simulate([gen(), bess(**kw)], F.step(-4.0), t_end=30.0)
    assert "B" in tr.saturated and tr.dp["B"][-1] == pytest.approx(-1.0)
    assert F.metrics(tr).df_qss_hz == pytest.approx(3.0 / (10.0 / (0.04 * F0)), rel=1e-6)
    assert F.metrics(tr).nadir_hz > 0                                             # the extreme is a peak


def test_a_cold_genset_ramps_from_the_moment_it_joins():
    g = gen(p0=0.0, join_s=1.0, t_g=0.01, step_mw=0.5, ramp_mw_per_s=1.0)
    tr = F.simulate([g, bess()], F.step(4.0), t_end=3.0)
    joined = tr.t >= 1.0
    assert np.all(tr.dp["G"][joined] <= 0.5 + 1.0 * (tr.t[joined] - 1.0) + 1e-9)
    assert tr.dp["G"][tr.t >= 2.0][0] == pytest.approx(1.5, abs=2e-3)


def test_the_binding_limit_ranks_by_relative_not_raw_exceedance():
    """Nadir 47.0 Hz against a 47.5 Hz UPS window: 0.5 Hz over of 2.5 Hz
    allowed (20 %); 0.40 Hz settled against a 0.3 Hz band: 0.1 Hz over, 33 %."""
    m = F.Metrics(-1.0, -0.5, 500.0, -3.0, 0.4, -0.4, 2.0, {}, None)
    v = F.judge(m, {"transition": _lim(f_min=46.0), "steady_island": _lim(band=0.3)},
                equipment=[("UPS window", 47.5, 52.5)])
    assert v.binding == "steady island deviation 0.40 Hz against 0.3 Hz"
    assert v.failures[1] == "UPS window, nadir 47.00 Hz against 47.5 Hz"


def test_equipment_with_an_upper_bound_is_judged_on_the_peak():
    m = F.Metrics(1.0, 0.5, 500.0, 2.8, 0.3, 0.5, 1.0, {}, None)
    v = F.judge(m, {"transition": _lim(f_max=53.0), "steady_island": _lim()},
                equipment=[("UPS window", 47.5, 52.5)])
    assert v.failures == ["UPS window, peak 52.80 Hz against 52.5 Hz"]


def test_a_frequency_watt_pv_on_the_campus_has_no_headroom_above_its_dispatch():
    fw = {"control": "gfl_fw", "pf_rated": {"value": 0.95, "source": "assumed"},
          "fw_k_mw_per_hz": {"value": 10.0, "source": "assumed"}, "fw_deadband_hz": {"value": 0.2, "source": "assumed"},
          "fw_delay_s": {"value": 0.2, "source": "assumed"}, "fw_ramp_mw_per_s": {"value": 20.0, "source": "assumed"},
          "fw_p_limit_mw": {"value": 6.0, "source": "assumed"}}
    camp = build_campus(spec(pv_island=fw))
    pv = {u.name: u for u in F.units_from_campus(camp, rows(load=25.0, pv=10.0))}["PV1"]
    assert (pv.model, pv.p0, pv.p_min, pv.p_max) == ("gfl_fw", 10.0, 0.0, 10.0)
    assert pv.fw == {"k_mw_per_hz": 10.0, "deadband_hz": 0.2, "delay_s": 0.2, "ramp_mw_per_s": 20.0,
                     "p_limit_mw": 6.0}
