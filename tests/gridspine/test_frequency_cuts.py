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
    inertia = cuts[cuts["kind"] == "inertia"].iloc[0]
    assert inertia["current"] == 1.0                     # island_tables' inertia_mws
    assert inertia["required"] == pytest.approx(inertia["current"] + inertia["delta"])
    assert "against I3a inertia_mws 1" in inertia["note"]  # the engine's M is not the fixture's 1 MW·s
    assert inertia["unit"] == "MW·s" and inertia["heuristic"] and inertia["feasible"]
    # one row per (period, hour, scenario, kind), the tightest over the hour's events
    assert not cuts.duplicated(["period", "hour", "scenario", "kind"]).any()
    per_event = []
    for _, r in st.events[(st.events["event"].isin(["E1", "E2", "E3", "E5"])) & st.events["i_max_pu"].isna()
                          & ~st.events["ok"].astype(bool)].iterrows():
        per_event.append(F.propose_cuts(camp, hourly(), pd.DataFrame([r]), island_hourly=ih, island_unit_hourly=iu))
    allc = pd.concat(per_event)
    tight = allc[allc["kind"] == "inertia"]["required"].max()
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
    droop = cuts[cuts["kind"] == "droop_gain"]
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
    for _, r in cuts.iterrows():
        assert r["delta"] == pytest.approx(by_hand[r["kind"]][1], rel=1e-9)


def test_the_every_hour_cut_leaves_a_transferring_ups_out_as_the_sweep_does():
    camp = ups_campus(transfers=True, scenarios=("seamless",))
    ih, iu = island_tables(dp_up=(40.0, 40.0), online=(1.0, 1.0))
    sweep = F.e1_every_hour(camp, hourly(), ih, iu)
    row = sweep[(sweep["hour"] == 0) & ~sweep["ok"].astype(bool)]
    cuts = F.propose_cuts(camp, hourly(), None, row, island_hourly=ih, island_unit_hourly=iu)
    _, _, units, _ = F._hour_units(camp, hourly(), F.validate_island_unit_hourly(iu) if False else iu, 2030, 0)
    kept = [u for u in units if u.model != "ups"]
    limits, req = F._limits(camp)
    by_hand = kinds(F.cuts_for_event(kept, step_run(40.0), limits, F0, F.equipment(camp),
                                     req["values"]["rocof_window_ms"]))
    assert set(cuts["kind"]) == set(by_hand)
    for _, r in cuts.iterrows():
        assert r["delta"] == pytest.approx(by_hand[r["kind"]][1], rel=1e-9)
