"""I7's frequency cuts (plan 2026-10-07-campus-island-operation, owner Q6).

Oracles:
* inertia: on one governor without limits, the engine's bisection matches
  the closed-form inversion of the second-order nadir (matrix exponential)
  within 0.5 %; on inertia alone, the RoCoF cut is f0 ΔP / (2 RoCoF_max)
  exactly; the cut applied, the transient limits pass;
* headroom: the saturated GFM's shortfall, applied as range, removes the
  saturation;
* droop gain: ΣK_req = ΔP / Δf_allowed exactly for a droop island; applied,
  the steady band passes;
* collapse and an island without droop are not fixable;
* on the campus: current from I3a, the mismatch note, the scenario rule,
  the tightest per hour and kind, no E4 or Q8 cuts, fixed columns;
* the driver writes the file and refuses a run without a frequency check.
"""
import dataclasses
import math

import numpy as np
import pandas as pd
import pytest
from scipy.linalg import expm
from scipy.optimize import brentq, minimize_scalar

from gridspine.static import frequency as F
from tests.gridspine.test_frequency import bess, gen

F0 = 50.0


def lim(rocof=1e6, f_min=40.0, f_max=60.0, band=100.0, st_min=40.0, st_max=60.0):
    return {"transition": {"rocof_hz_per_s": rocof, "f_min_hz": f_min, "f_max_hz": f_max, "qss_band_hz": 100.0},
            "steady_island": {"rocof_hz_per_s": 1e6, "f_min_hz": st_min, "f_max_hz": st_max, "qss_band_hz": band}}


def step_run(dp):
    return lambda us, t_end=10.0: F.simulate(us, F.step(dp), t_end=t_end, dt=5e-3)


def kinds(cuts):
    return {c[0]: c for c in cuts}


def exact_nadir(m_mws, k, tg, dp):
    """The one-governor nadir (Hz) by the matrix exponential, M in MW·s."""
    m = 2.0 * m_mws / F0
    a = np.array([[0.0, 1.0 / m], [-k / tg, -1.0 / tg]])
    b = np.array([-dp / m, 0.0])
    xs = -np.linalg.solve(a, b)

    def df(t):
        return (xs + expm(a * t) @ (np.zeros(2) - xs))[0]

    # the extreme over time: a dense grid, refined; an overdamped response has none before it settles
    grid = np.linspace(0.0, 400.0 * max(m / (k + 1e-12), tg), 4001)
    vals = [df(t) for t in grid]
    i = int(np.argmin(vals))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    return minimize_scalar(df, bounds=(lo, hi), method="bounded", options={"xatol": 1e-9}).fun


def test_the_inertia_cut_matches_the_closed_form_nadir_inversion():
    g = gen(p0=0.0, p_max=1e6)                          # H 1.5 s on 10 MVA, R 4 %, T_g 0.5 s: no limit binds
    dp, f_min = 4.0, 49.0                               # settles at -0.8 Hz, inside the -1.0 Hz limit
    cuts = kinds(F.cuts_for_event([g], step_run(dp), lim(f_min=f_min)))
    _, dm, binding, *_ = cuts["inertia"]
    k = 10.0 / (0.04 * F0)
    m_req = brentq(lambda mm: exact_nadir(mm, k, 0.5, dp) - (f_min - F0), 15.0, 1e4)
    assert dm == pytest.approx(m_req - 1.5 * 10.0, rel=5e-3)
    assert binding.startswith("transition nadir")


def test_the_rocof_cut_on_inertia_alone_is_f0_dp_over_2_rocof():
    g = gen(t_g=1e12)                                   # no governor: a linear ramp
    cuts = kinds(F.cuts_for_event([g], step_run(4.0), lim(rocof=1.0)))
    dm = cuts["inertia"][1]
    assert dm + 15.0 == pytest.approx(F0 * 4.0 / (2 * 1.0), rel=2e-3)
    assert cuts["droop_gain"][4]                        # no governor: it never settles, which a droop cut addresses


def test_the_inertia_cut_applied_passes_the_transient_limits():
    g = gen(p0=0.0, p_max=1e6)
    limits = lim(f_min=49.0, rocof=0.8)
    run = step_run(4.0)
    dm = kinds(F.cuts_for_event([g], run, limits))["inertia"][1]
    for extra, ok in ((dm, True), (dm * 0.97, False)):
        added = F.Unit("~i", "inertia", 0.0, 0.0, 1.0, h=extra)
        v = F.judge(F.metrics(run([g, added])), limits)
        assert (not any(F._transient(f) for f in v.failures)) is ok


def test_the_headroom_cut_is_the_saturated_gfms_shortfall_and_removes_it():
    g, b = gen(), bess(p_max=0.5, i_max_mw=1e9)
    run = step_run(4.0)
    cuts = kinds(F.cuts_for_event([g, b], run, lim()))
    _, d, binding, *_ = cuts["headroom_up"]
    assert d > 0 and binding == "GFM at a limit: B" and "headroom_down" not in cuts
    widened = dataclasses.replace(b, p_max=0.5 + d * 1.001)
    assert not F.metrics(run([g, widened])).saturated
    assert F.metrics(run([g, dataclasses.replace(b, p_max=0.5 + d * 0.98)])).saturated


def test_a_saturated_event_takes_its_droop_and_inertia_cuts_from_the_run_with_the_headroom():
    # B at 0.5 MW saturates: the runaway trace settles at -(4 - 0.5) / 5 = -0.7 Hz, and ΣK counts B's 5 MW/Hz it
    # does not give. With B's range lifted the island settles at -4 / 10 = -0.4 Hz: against 0.2 Hz, K_req 20 MW/Hz
    g, b = gen(p0=0.0, p_max=1e6), bess(p_max=0.5, i_max_mw=1e9)
    cuts = kinds(F.cuts_for_event([g, b], step_run(4.0), lim(band=0.2)))
    assert {"headroom_up", "droop_gain"} <= set(cuts)
    _, dk, _, derivation, ok, _ = cuts["droop_gain"]
    assert ok and 10.0 + dk == pytest.approx(4.0 / 0.2, rel=2e-3) and "B's range lifted" in derivation
    # a nadir only the runaway trace breaks asks for no inertia: with the headroom the island stays above 49.2 Hz
    cuts = kinds(F.cuts_for_event([g, b], step_run(4.0), lim(f_min=49.2)))
    assert "headroom_up" in cuts and "inertia" not in cuts and "droop_gain" not in cuts


def test_the_headroom_is_read_from_the_settled_lifted_run_on_a_slow_island():
    # a heavy island: with B's range lifted it settles at -4 / 10 = -0.4 Hz, B giving 5 x 0.4 = 2 MW, but only
    # after 10 s (about 1.45 MW at 10 s). The headroom is the settled 2 - 0.5 MW, not what 10 s shows
    g, b = gen(p0=0.0, p_max=1e6, h=200.0), bess(p_max=0.5, i_max_mw=1e9)
    up = kinds(F.cuts_for_event([g, b], step_run(4.0), lim()))["headroom_up"][1]
    assert up == pytest.approx(1.5, rel=1e-3)


def test_an_absorbing_gfm_asks_for_headroom_down():
    g, b = gen(), bess(p_min=-0.5, i_max_mw=1e9)
    cuts = kinds(F.cuts_for_event([g, b], step_run(-4.0), lim()))
    assert "headroom_down" in cuts and "headroom_up" not in cuts


def test_the_droop_cut_is_dp_over_the_allowed_deviation():
    g = gen(p0=0.0, p_max=1e6)
    k = 10.0 / (0.04 * F0)                              # 5 MW/Hz: 4 MW settles at -0.8 Hz
    cuts = kinds(F.cuts_for_event([g], step_run(4.0), lim(band=0.5)))
    _, dk, binding, *_ = cuts["droop_gain"]
    assert k + dk == pytest.approx(4.0 / 0.5, rel=2e-3)
    assert binding.startswith("steady island deviation")
    # the tighter of the band and the steady limit: 49.8 Hz leaves 0.2 Hz
    dk2 = kinds(F.cuts_for_event([g], step_run(4.0), lim(band=0.5, st_min=49.8)))["droop_gain"][1]
    assert k + dk2 == pytest.approx(4.0 / 0.2, rel=2e-3)
    applied = dataclasses.replace(g, r=10.0 / ((k + dk) * F0) * 0.999)
    assert F.metrics(step_run(4.0)([applied])).df_qss_hz >= -0.5


def test_an_over_frequency_droop_cut_is_dp_over_the_allowed_deviation_too():
    # 4 MW of export lost: the genset at 5 MW backs off and the island settles at +0.8 Hz
    g = gen(p_max=1e6)
    _, dk, binding, *_ = kinds(F.cuts_for_event([g], step_run(-4.0), lim(band=0.5)))["droop_gain"]
    assert 5.0 + dk == pytest.approx(4.0 / 0.5, rel=2e-3) and binding.startswith("steady island deviation")


def test_collapse_and_an_island_without_droop_are_not_fixable_by_a_cut():
    b = bess(p_max=1.0, i_max_mw=1e9)                   # the only former, at its limit at once
    (cut,) = F.cuts_for_event([b], step_run(4.0), lim())
    assert cut[0] == "inertia" and cut[1] == math.inf and cut[4] is False and "former" in cut[5]
    iso = gen(p0=0.0, p_max=1e6, isochronous=True, t_g=1e12)
    cuts = kinds(F.cuts_for_event([iso], step_run(4.0), lim(band=0.1)))
    assert cuts["droop_gain"][1] == math.inf and not cuts["droop_gain"][4]


def test_a_settled_value_beyond_the_nadir_limit_asks_for_droop_not_inertia():
    # 4 MW on 5 MW/Hz settles at -0.8 Hz, below the -0.5 Hz transition limit: no inertia fixes that
    g = gen(p0=0.0, p_max=1e6)
    cuts = kinds(F.cuts_for_event([g], step_run(4.0), lim(f_min=49.5)))
    assert "inertia" not in cuts and cuts["droop_gain"][1] + 5.0 == pytest.approx(4.0 / 0.5, rel=2e-3)


def test_a_heavy_island_is_run_until_it_settles():
    # a nadir 0.01 Hz inside the settled value: the bisection must not stop at an unsettled trace
    calls = []
    run = step_run(4.0)

    def counting(us, t_end=10.0):
        calls.append(t_end)
        return run(us, t_end)
    g = gen(p0=0.0, p_max=1e6)
    dm = kinds(F.cuts_for_event([g], counting, lim(f_min=49.18)))["inertia"][1]
    assert max(calls) > 10.0
    m_req = brentq(lambda mm: exact_nadir(mm, 5.0, 0.5, 4.0) - (49.18 - F0), 15.0, 1e6)
    assert dm == pytest.approx(m_req - 15.0, rel=1e-2)


def test_an_equipment_nadir_alone_asks_for_inertia():
    # the transition limits are wide; a chiller tripping at 49.0 Hz is what fails
    g = gen(p0=0.0, p_max=1e6)
    eq = [("CH trip", 49.0, math.nan)]
    cuts = kinds(F.cuts_for_event([g], step_run(4.0), lim(), equipment_=eq))
    _, dm, binding, *_ = cuts["inertia"]
    assert binding.startswith("CH trip, nadir")
    m_req = brentq(lambda mm: exact_nadir(mm, 5.0, 0.5, 4.0) - (49.0 - F0), 15.0, 1e4)
    assert dm == pytest.approx(m_req - 15.0, rel=5e-3)


def test_the_response_counts_m_and_k_as_i2b():
    # M = H S for a joined genset and a VSM, tau_f P_n / (2 R) for a droop GFM; an isochronous genset adds no K
    units = [gen(), bess(), bess("gfm_vsm", name="V"), gen(name="ISO", isochronous=True),
             gen(name="COLD", join_s=15.0)]
    m, k = F._response(units)
    assert m == pytest.approx(1.5 * 10 + 0.1 * 10 / (2 * 0.04) + 1.25 * 10 + 1.5 * 10)
    assert k == pytest.approx(3 * 10.0 / (0.04 * F0))


def test_a_passing_event_asks_for_nothing():
    assert F.cuts_for_event([gen(p0=0.0, p_max=1e6)], step_run(0.5), lim()) == []


# ── on the campus ───────────────────────────────────────────────────────────

from gridspine.ingest.campus import build_campus  # noqa: E402
from tests.gridspine.test_frequency_events import hourly, island_tables, ups_campus  # noqa: E402


def study_for(scenarios=("seamless",), **kw):
    camp = ups_campus(transfers=False, scenarios=scenarios)
    ih, iu = island_tables(**kw)
    st = F.campus_frequency_study(camp, hourly(), [(2030, 0)], island_unit_hourly=iu, t_end=10.0)
    return camp, ih, iu, st


def test_the_campus_cuts_take_current_from_i3a_and_note_a_mismatch():
    camp, ih, iu, st = study_for()
    assert not st.events["ok"].all()                     # the fixture fails RoCoF at 1 Hz/s
    cuts = F.propose_cuts(camp, hourly(), st.events, island_hourly=ih, island_unit_hourly=iu)
    assert tuple(cuts.columns) == F.CUT_COLUMNS and len(cuts)
    inertia = cuts[(cuts["kind"] == "inertia") & cuts["feasible"]].iloc[0]
    assert inertia["current"] == 1.0                     # island_tables' inertia_mws
    assert inertia["required"] == pytest.approx(inertia["current"] + inertia["delta"])
    assert "against I3a inertia_mws 1" in inertia["note"]  # the engine's M is not the fixture's 1 MW·s
    assert inertia["unit"] == "MW·s" and inertia["heuristic"] and inertia["feasible"]
    # per (period, hour, scenario, kind): the tightest feasible row, and the E3 trip's infeasible one beside it
    assert not cuts.duplicated(["period", "hour", "scenario", "kind", "feasible"]).any()
    both = cuts[cuts["kind"] == "inertia"]
    assert list(both["feasible"]) == [True, False] and both.iloc[1]["event"] == "E3"
    per_event = []
    for _, r in st.events[(st.events["event"].isin(["E1", "E2", "E3", "E5"])) & st.events["i_max_pu"].isna()
                          & ~st.events["ok"].astype(bool)].iterrows():
        per_event.append(F.propose_cuts(camp, hourly(), pd.DataFrame([r]), island_hourly=ih, island_unit_hourly=iu))
    allc = pd.concat(per_event)
    tight = allc[(allc["kind"] == "inertia") & allc["feasible"]]["required"].max()
    assert inertia["required"] == tight


def test_without_i3a_the_engine_value_is_current_and_said_so():
    camp, ih, iu, st = study_for()
    cuts = F.propose_cuts(camp, hourly(), st.events)
    r = cuts[cuts["kind"] == "inertia"].iloc[0]
    assert "no I3a row" in r["note"]
    units = [u for u in F.units_from_campus(camp, hourly()[hourly()["hour"] == 0][["unit_id", "p_mw", "status"]]
                                            .reset_index(drop=True))]
    assert r["current"] > 0


def test_no_cuts_for_q8_reruns_or_e4_and_a_scenario_without_a_hub_row_is_not_feasible():
    camp, ih, iu, st = study_for()
    ev = st.events.copy()
    only_q8 = ev[ev["i_max_pu"].notna()]
    assert F.propose_cuts(camp, hourly(), only_q8, island_hourly=ih).empty
    e4 = ev.iloc[:1].assign(event="E4")
    assert F.propose_cuts(camp, hourly(), e4, island_hourly=ih).empty
    other = ev[ev["i_max_pu"].isna() & ~ev["ok"].astype(bool)].iloc[:1].assign(scenario="ups_bridged")
    cuts = F.propose_cuts(camp, hourly(), other)
    assert len(cuts) and not cuts["feasible"].any() and (cuts["required"] == math.inf).all()
    assert cuts["note"].str.contains("ups_bridged").all()


def test_the_every_hour_e1_failures_get_cuts_too():
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    ih, iu = island_tables(dp_up=(10.0, 10.0), online=(1.0, 1.0))
    sweep = F.e1_every_hour(camp, hourly(), ih, iu)
    assert not sweep["ok"].all()
    cuts = F.propose_cuts(camp, hourly(), None, sweep, island_hourly=ih, island_unit_hourly=iu)
    assert set(cuts["hour"]) == set(sweep[~sweep["ok"].astype(bool)]["hour"]) and set(cuts["event"]) == {"E1"}


def test_a_study_that_passes_gives_an_empty_table_with_its_columns():
    camp, ih, iu, st = study_for()
    passing = st.events.assign(ok=True)
    cuts = F.propose_cuts(camp, hourly(), passing, island_hourly=ih)
    assert cuts.empty and tuple(cuts.columns) == F.CUT_COLUMNS


# ── the driver ──────────────────────────────────────────────────────────────

from gridspine.drivers import campus_study as D  # noqa: E402
from gridspine.schema.contracts import ContractError  # noqa: E402
from tests.gridspine.test_campus_island_frequency_driver import campus_spec, write_run  # noqa: E402


def test_the_driver_writes_the_cuts_after_the_frequency_check(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    with pytest.raises(ContractError, match="island_frequency_campus first"):
        D.cuts_campus(run)
    D.island_frequency_campus(run)
    cuts = D.cuts_campus(run)
    written = pd.read_csv(run / D.FREQ_CUTS_CSV)
    assert tuple(written.columns) == F.CUT_COLUMNS and len(written) == len(cuts) > 0
    assert set(written["kind"]) <= set(F.CUT_KINDS)


def test_the_driver_takes_current_from_the_runs_i3a_tables(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    D.island_frequency_campus(run)
    cuts = D.cuts_campus(run)
    lp = cuts[cuts["kind"].isin(["inertia", "droop_gain"])]
    assert len(lp) and (lp["current"] == 1.0).all()                         # write_run's inertia_mws, k_mw_per_hz
    assert lp["note"].str.contains("against I3a").all()


def steady_campus(fw_pv=False):
    from tests.gridspine.test_island_flow import fw
    from tests.gridspine.test_island_schema import t as tag
    s = campus_spec()
    s["campus"]["island"]["frequency_limits"]["steady_island"]["qss_band_hz"] = tag(0.01)
    if fw_pv:
        s["campus"]["units"]["PV1"]["island"] = fw(db=0.0, k=0.5, limit=1.0)
    return build_campus(s)


@pytest.mark.parametrize("fw_pv, heuristic", [(False, False), (True, True)])
def test_a_droop_cut_is_exact_unless_a_frequency_watt_unit_is_in_the_island(fw_pv, heuristic):
    camp = steady_campus(fw_pv)
    h = pd.concat([hourly_rows(h) for h in (0,)], ignore_index=True)
    st = F.campus_frequency_study(camp, h, [(2030, 0)], t_end=10.0)
    cuts = F.propose_cuts(camp, h, st.events)
    droop = cuts[(cuts["kind"] == "droop_gain") & (cuts["event"] != "E3")]     # E3's island has lost PV1
    assert len(droop) and (droop["heuristic"] == heuristic).all()
    assert (cuts[cuts["kind"] != "droop_gain"]["heuristic"]).all()


def hourly_rows(hour):
    from tests.gridspine.test_campus_island_frequency_driver import hour_rows
    return hour_rows().assign(period=2030, hour=hour)


def test_an_e3_cut_is_inverted_without_the_tripped_unit():
    camp, ih, iu, st = study_for()
    e3 = st.events[(st.events["event"] == "E3") & ~st.events["ok"].astype(bool)]
    assert len(e3)
    cuts = F.propose_cuts(camp, hourly(), e3)
    from gridspine.static.island_flow import island_flow
    rows0 = hourly()[hourly()["hour"] == 0][["unit_id", "p_mw", "status"]].reset_index(drop=True)
    _, _, units, _ = F._hour_units(camp, hourly(), None, 2030, 0)
    isl = F._island_units(units, island_flow(camp, rows0, online=F.built(rows0)))
    name = e3.iloc[0]["unit"]
    lost = next(u for u in isl if u.name == name)
    rest = [u for u in isl if u.name != name]
    limits, req = F._limits(camp)
    by_hand = kinds(F.cuts_for_event(rest, step_run(lost.p0), limits, F0, F.equipment(camp),
                                     req["values"]["rocof_window_ms"]))
    # a trip needs N-1 cover from the units left, which a cut on I3a's total (the tripped unit in it) cannot give
    assert len(cuts) and not cuts["feasible"].any() and (cuts["required"] == math.inf).all()
    for _, r in cuts.iterrows():
        assert "N-1 cover" in r["note"]
        need = float(r["note"].split("the units left need ")[1].split()[0])
        assert need == pytest.approx(by_hand[r["kind"]][1], rel=1e-3)


def test_the_every_hour_cut_leaves_a_transferring_ups_out_as_the_sweep_does():
    camp = ups_campus(transfers=True, scenarios=("seamless",))
    ih, iu = island_tables(dp_up=(40.0, 40.0), online=(1.0, 1.0))
    sweep = F.e1_every_hour(camp, hourly(), ih, iu)
    row = sweep[(sweep["hour"] == 0) & ~sweep["ok"].astype(bool)]
    cuts = F.propose_cuts(camp, hourly(), None, row, island_hourly=ih, island_unit_hourly=iu)
    rows0 = hourly()[hourly()["hour"] == 0][["unit_id", "p_mw", "status"]].reset_index(drop=True)
    kept = [u for u in F.units_from_campus(camp, rows0, online=F.built(rows0)) if u.model != "ups"]
    assert repr(kept) == repr(F._sweep_units(camp, rows0, iu[iu["hour"] == 0]))
    limits, req = F._limits(camp)
    by_hand = kinds(F.cuts_for_event(kept, step_run(40.0), limits, F0, F.equipment(camp),
                                     req["values"]["rocof_window_ms"]))
    assert set(cuts["kind"]) == set(by_hand)
    for _, r in cuts.iterrows():
        assert r["delta"] == pytest.approx(by_hand[r["kind"]][1], rel=1e-9)


# ── the re-run is the table's run ───────────────────────────────────────────

from gridspine.schema.island import validate_island_unit_hourly  # noqa: E402

def test_a_failure_that_shows_only_after_10_s_still_gets_its_cut():
    # a heavy island on one governor falls slowly (slow pole about 15 s) toward -0.8 Hz: inside the 0.5 Hz band at
    # 10 s, outside it at the study's 20 s. The baseline starts at the study's horizon and runs until it settles
    g = gen(p0=0.0, p_max=1e6, h=200.0)
    limits = lim(band=0.5)
    assert F.judge(F.metrics(step_run(4.0)([g], 10.0)), limits).ok
    assert not F.judge(F.metrics(step_run(4.0)([g], 20.0)), limits).ok
    calls = []

    def counting(us, t_end):
        calls.append(t_end)
        return step_run(4.0)(us, t_end)
    _, dk, binding, *_ = kinds(F.cuts_for_event([g], counting, limits, t_end=20.0))["droop_gain"]
    assert calls[0] == 20.0 and binding.startswith("steady island deviation")
    assert 5.0 + dk == pytest.approx(4.0 / 0.5, rel=2e-3)              # ΔP / Δf_allowed on the settled -0.8 Hz


def test_the_cut_defaults_are_the_studys_and_the_sweeps_runs():
    import inspect

    def run_of(fn):
        p = inspect.signature(fn).parameters
        return p["dt"].default, p["t_end"].default
    p = inspect.signature(F.propose_cuts).parameters
    assert p["study"].default == run_of(F.campus_frequency_study)
    assert p["sweep_run"].default == run_of(F.e1_every_hour)


def test_the_rebuilt_study_events_reproduce_the_studys_rows():
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    ih, iu = island_tables()
    st = F.campus_frequency_study(camp, hourly(), [(2030, 0)], island_unit_hourly=iu)   # its own 1 ms, 20 s
    p = inspect_defaults(F.propose_cuts)
    rows = st.events[st.events["event"].isin(["E1", "E2", "E3", "E5"]) & st.events["i_max_pu"].isna()]
    assert set(rows["event"]) == {"E1", "E2", "E3", "E5"}
    ctx = {}
    for _, r in rows.iterrows():
        units, run, (dt, t_end) = F._event_run(camp, hourly(), validate_island_unit_hourly(iu), r, ctx,
                                               p["study"], p["sweep_run"])
        assert (dt, t_end) == (1e-3, 20.0)
        m = F.metrics(run(units, t_end))
        for col, got in (("nadir_hz", m.nadir_hz), ("df_qss_hz", m.df_qss_hz),
                         ("rocof_window_hz_per_s", m.rocof_window_hz_per_s)):
            assert got == pytest.approx(r[col], abs=1e-12), (r["event"], col)


def inspect_defaults(fn):
    import inspect
    return {k: v.default for k, v in inspect.signature(fn).parameters.items()}


def test_the_rebuilt_sweep_event_is_the_sweeps_own_with_a_ups_that_stays_on_line():
    # 40 MW drops UPS1 out of its window; the sweep runs it on the plan's 25 MW IT load, not I3a's 20 MW
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    ih, iu = island_tables(dp_up=(40.0, 40.0), online=(1.0, 1.0))
    sweep = F.e1_every_hour(camp, hourly(), ih, iu)
    r = sweep[(sweep["hour"] == 0) & (sweep["direction"] == "import")].iloc[0]
    assert r["ups_drops"] == "UPS1" and not r["ok"]
    p = inspect_defaults(F.propose_cuts)
    units, run, (dt, t_end) = F._event_run(camp, hourly(), validate_island_unit_hourly(iu), r, {}, p["study"],
                                           p["sweep_run"])
    assert (dt, t_end) == (5e-3, 10.0)
    assert next(u for u in units if u.model == "ups").ups["it_mw"] == 25.0
    tr = run(units, t_end)
    m = F.metrics(tr)
    assert tr.ups["UPS1"].drops
    for col, got in (("nadir_hz", m.nadir_hz), ("df_qss_hz", m.df_qss_hz),
                     ("rocof_window_hz_per_s", m.rocof_window_hz_per_s)):
        assert got == pytest.approx(r[col], abs=1e-12), col
    # the study's own build (I3a's IT load) gives another trace: the rebuild must not take it
    _, _, other, _ = F._hour_units(camp, hourly(), validate_island_unit_hourly(iu), 2030, 0)
    other = [u for u in other if not (u.model == "ups" and u.ups["transfers"])]
    assert F.metrics(run(other, t_end)).df_qss_hz != pytest.approx(r["df_qss_hz"], abs=1e-6)


def test_a_failure_the_re_run_does_not_reproduce_gets_an_infeasible_row_not_nothing():
    camp = ups_campus(transfers=False, scenarios=("seamless",))
    ih, iu = island_tables(dp_up=(10.0, 10.0), online=(1.0, 1.0))
    sweep = F.e1_every_hour(camp, hourly(), ih, iu)
    passing = sweep[sweep["ok"].astype(bool)]
    assert len(passing) == 1
    marked = passing.assign(ok=False, binding="transition RoCoF 9 Hz/s over 500 ms against 1 Hz/s")
    cuts = F.propose_cuts(camp, hourly(), None, marked, island_hourly=ih, island_unit_hourly=iu)
    (r,) = [r for _, r in cuts.iterrows()]
    assert r["kind"] == "inertia" and not r["feasible"] and r["required"] == math.inf
    assert r["binding"].startswith("transition RoCoF 9") and "not reproduced at dt 0.005 s, t_end 10 s" in r["note"]
    steady = passing.assign(ok=False, binding="steady island deviation 0.9 Hz against 0.5 Hz")
    cuts = F.propose_cuts(camp, hourly(), None, steady, island_hourly=ih, island_unit_hourly=iu)
    assert list(cuts["kind"]) == ["droop_gain"] and not cuts["feasible"].any()


# ── the two-former hub (pypsa-gui I7's end-to-end case) ─────────────────────

def two_former_hub():
    """20 MW critical load less 8 MW PV on a 15 MW droop genset at 12 MW and
    a 10 MW droop BESS at 0: a 20 MW step saturates the BESS, and the genset
    reaches its 15 MW."""
    g = gen(name="G", p0=12.0, p_n=15.0, s_n=15.0, p_max=15.0)
    b = bess(name="B", p0=0.0, p_n=10.0, s_n=10.0, p_min=-10.0, p_max=10.0, i_max_mw=12.0)
    return [g, b], lim(rocof=1.0, f_min=49.0, f_max=51.0, band=1.0, st_min=49.0, st_max=51.0)


def test_the_two_former_hub_gets_bounded_cuts_that_fix_it_not_a_runaway():
    units, limits = two_former_hub()
    run, dp = step_run(20.0), 20.0
    assert F.metrics(run(units, 20.0)).df_qss_hz < -50.0                 # the runaway trace the old cuts came from
    cuts = kinds(F.cuts_for_event(units, run, limits, t_end=20.0))
    assert set(cuts) == {"headroom_up", "droop_gain", "inertia"} and all(c[4] for c in cuts.values())
    m_now, k_now = F._response(units)
    # bounds: no more headroom than the step, no more gain than ΔP / Δf_allowed, no more inertia than the step needs
    # with no governor at all, f0 ΔP / (2 RoCoF_max)
    up, dk, dm = cuts["headroom_up"][1], cuts["droop_gain"][1], cuts["inertia"][1]
    assert 0 < up <= dp and dk <= dp / 1.0 and m_now + dm <= F0 * dp / (2 * 1.0)
    # the droop cut is exact with G at its limit: G gives its 3 MW, so B's gain must carry 17 MW at 1 Hz (to the
    # settling tolerance); ΣK (|Δf_qss| / Δf_allowed − 1) would ask for 12.5 x 2.38, G's gain counted
    assert 5.0 + dk == pytest.approx(17.0, rel=1e-2) and "at a limit: G" in cuts["droop_gain"][5]
    # all three applied, as pypsa-gui's loop applies them, the hub passes every limit
    k_b = 5.0 + dk * 1.01
    fixed = [units[0], dataclasses.replace(units[1], p_max=10.0 + up * 1.01, i_max_mw=1e9, r=10.0 / (k_b * F0)),
             F.Unit("~i", "inertia", 0.0, 0.0, 1.0, h=dm * 1.01)]
    m = F.metrics(run(fixed, 40.0))
    assert not m.saturated and F.judge(m, limits).ok


def test_a_cut_beyond_the_cap_is_not_offered(monkeypatch):
    camp, ih, iu, st = study_for()
    huge = [("droop_gain", 1e6, "steady island deviation", "test", True, "")]
    monkeypatch.setattr(F, "cuts_for_event", lambda *a, **k: huge)
    cuts = F.propose_cuts(camp, hourly(), st.events, island_hourly=ih, island_unit_hourly=iu)
    assert len(cuts) and not cuts["feasible"].any() and (cuts["required"] == math.inf).all()
    capped = cuts[cuts["event"] != "E3"]                 # an E3 row says it needs N-1 cover first
    assert len(capped) and capped["note"].str.contains("not offered as a cut").all()
    assert cuts[cuts["event"] == "E3"]["note"].str.contains("N-1 cover").all()


@pytest.mark.parametrize("note, heuristic", [("", False), ("at a limit: G (their gain in the total does not help)", True)])
def test_a_droop_cut_with_a_unit_at_its_limit_is_heuristic(monkeypatch, note, heuristic):
    # the hub's ΣK still counts the clipped unit's gain, so the cut on the total is no longer exact
    camp, ih, iu, st = study_for()
    rows = [("droop_gain", 1.0, "steady island deviation", "test", True, note)]
    monkeypatch.setattr(F, "cuts_for_event", lambda *a, **k: rows)
    cuts = F.propose_cuts(camp, hourly(), st.events, island_hourly=ih, island_unit_hourly=iu)
    feasible = cuts[cuts["feasible"]]
    assert len(feasible) and (feasible["heuristic"] == heuristic).all()


def test_a_study_event_is_re_run_from_the_studys_horizon(monkeypatch):
    camp, ih, iu, st = study_for()
    e2 = st.events[(st.events["event"] == "E2") & st.events["i_max_pu"].isna()].assign(ok=False)
    seen, real = [], F.simulate
    monkeypatch.setattr(F, "simulate", lambda *a, **k: seen.append((k["t_end"], k["dt"])) or real(*a, **k))
    F.propose_cuts(camp, hourly(), e2, island_hourly=ih, island_unit_hourly=iu, study=(5e-3, 20.0))
    assert seen and seen[0] == (20.0, 5e-3) and min(t for t, _ in seen) >= 20.0


def test_a_headroom_cut_takes_current_from_i3a_s_unit_rows(monkeypatch):
    camp, ih, iu, st = study_for()
    rows = [("headroom_up", 1.0, "GFM at a limit: B", "test", True, "")]
    monkeypatch.setattr(F, "cuts_for_event", lambda *a, **k: rows)
    cuts = F.propose_cuts(camp, hourly(), st.events, island_hourly=ih, island_unit_hourly=iu)
    r = cuts[cuts["feasible"]].iloc[0]
    assert r["current"] == 5.0 and r["required"] == 6.0 and "no I3a unit rows" not in r["note"]   # GEN1's 5 MW


def test_settle_run_reports_the_horizon_it_ran_when_it_never_settles():
    # a ramp never settles: five runs at 10, 20, 40, 80 and 160 s, and the trace and the horizon are the last run's
    calls = []
    run = step_run(4.0)

    def counting(us, t_end):
        calls.append(t_end)
        return run(us, t_end)
    tr, t = F._settle_run(counting, [gen(t_g=1e12)], 10.0)
    assert calls == [10.0, 20.0, 40.0, 80.0, 160.0] and t == 160.0 and tr.t[-1] == pytest.approx(160.0)
