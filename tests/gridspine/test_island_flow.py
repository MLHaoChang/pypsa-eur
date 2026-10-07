"""Island steady state by control mode, I4 (plan 2026-10-07-campus-island-operation).

The campus opens its PCC at a chosen hour. Grid-forming units (GFM droop,
VSM) and gensets become voltage sources behind their virtual droop
reactance and share the lost import by droop; grid-following units keep
their control law. The result is a steady-state screening.

Oracles, each worked by hand:
* droop sharing: each reference takes ΔP_AC * K_i / ΣK, with
  K = P_n / (R * f0) and Δf = -ΔP_AC / ΣK;
* a reference at its P limit is held there and the rest is shared by the
  others (the per-unit gate: every unsaturated reference sits at the one Δf);
* an isochronous genset is the only slack, so Δf = 0 exactly;
* frequency-watt has no response inside its deadband and K*(|Δf| - db)
  outside it;
* droop and VSM with the same droop give the same steady state;
* Q laws land on their curves; a fixed cos phi gives Q = P * tan phi;
* Q-V droop through the virtual reactance shares Q in proportion to
  1 / k_q at P ~ 0, and converges on very short ties.
"""
import copy
import math

import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static import island_flow as IF
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_schema import genset, gfm_bess, vsm_bess

F0 = 50.0


def spec(load=25.0, gen_island=None, bess_island=None, pv_island=None):
    """test_campus's campus: 110 kV PCC, 2 x 40 MVA to MV1, a cable to MV2;
    DC_LOAD on MV2, BESS1 (20 MW) and PV1 (15 MW) on MV1, GEN1 (10 MW) on MV2."""
    s = campus_spec()
    u = s["campus"]["units"]
    u["DC_LOAD"]["p_mw"] = t(max(load, 50.0))
    u["BESS1"]["island"] = bess_island or gfm_bess()
    u["GEN1"]["island"] = gen_island or genset()
    if pv_island:
        u["PV1"]["island"] = pv_island
    return s


def rows(load=25.0, pv=10.0, gen=5.0, bess=0.0):
    return pd.DataFrame({"unit_id": ["DC_LOAD", "BESS1", "PV1", "GEN1"],
                         "p_mw": [-load, bess, pv, gen], "status": [1, 1, 1, 1]})


def K(p_mw, droop_pct=4.0):
    return p_mw / (droop_pct / 100.0 * F0)


def unit(res, uid):
    return res.units.set_index("unit_id").loc[uid]


# ── droop sharing ───────────────────────────────────────────────────────────

def test_two_references_share_the_lost_import_by_their_droop_gains():
    camp = build_campus(spec())
    res = IF.island_flow(camp, rows(load=25.0))
    assert res.viable, res.reason
    b, g = unit(res, "BESS1"), unit(res, "GEN1")
    kb, kg = K(20.0), K(10.0)
    dp = res.delta_p_ac_mw
    assert dp == pytest.approx((b.p_mw - b.p0_mw) + (g.p_mw - g.p0_mw), abs=1e-6)
    assert b.p_mw - b.p0_mw == pytest.approx(dp * kb / (kb + kg), rel=1e-4)
    assert g.p_mw - g.p0_mw == pytest.approx(dp * kg / (kb + kg), rel=1e-4)
    assert res.df_hz == pytest.approx(-dp / (kb + kg), rel=1e-4)
    # the lost import is about 10 MW (25 - 10 PV - 5 genset) plus losses
    assert 10.0 < dp < 10.5
    assert res.label == "steady-state screening"


def test_droop_and_vsm_with_the_same_droop_give_the_same_steady_state():
    a = IF.island_flow(build_campus(spec(bess_island=gfm_bess())), rows())
    b = IF.island_flow(build_campus(spec(bess_island=vsm_bess())), rows())
    assert a.df_hz == pytest.approx(b.df_hz, abs=1e-9)
    pd.testing.assert_series_equal(a.units.set_index("unit_id")["p_mw"],
                                   b.units.set_index("unit_id")["p_mw"])


def test_a_reference_at_its_p_limit_is_held_and_the_other_takes_the_rest():
    # lost import ~20 MW: by droop the genset would reach 5 + 6.7 > 10 MW
    res = IF.island_flow(build_campus(spec()), rows(load=35.0))
    assert res.viable, res.reason
    g, b = unit(res, "GEN1"), unit(res, "BESS1")
    assert g.saturated == "p_limit" and g.p_mw == pytest.approx(10.0, abs=1e-6)
    assert b.saturated == "none"
    assert res.df_hz == pytest.approx(-(b.p_mw - b.p0_mw) / K(20.0), rel=1e-4)


def test_the_per_unit_gate_holds_on_a_saturating_case():
    res = IF.island_flow(build_campus(spec()), rows(load=35.0))
    gate = IF.check_gate(res)
    assert gate["ok"], gate


def test_when_every_grid_forming_source_is_at_its_limit_the_island_is_refused():
    res = IF.island_flow(build_campus(spec(load=60.0)), rows(load=60.0))
    assert not res.viable
    assert "every grid-forming source" in res.reason


def test_an_isochronous_genset_takes_the_whole_deficit_and_frequency_holds():
    iso = genset()
    iso["governor"] = "isochronous"
    del iso["droop_pct"]
    res = IF.island_flow(build_campus(spec(gen_island=iso)), rows(load=18.0))
    assert res.viable, res.reason
    assert res.df_hz == 0.0
    b, g = unit(res, "BESS1"), unit(res, "GEN1")
    assert b.p_mw == pytest.approx(b.p0_mw, abs=1e-6)
    assert g.p_mw - g.p0_mw == pytest.approx(res.delta_p_ac_mw, abs=1e-6)


def test_when_the_isochronous_genset_saturates_the_droop_units_take_over():
    iso = genset()
    iso["governor"] = "isochronous"
    del iso["droop_pct"]
    res = IF.island_flow(build_campus(spec(gen_island=iso)), rows(load=30.0))
    g, b = unit(res, "GEN1"), unit(res, "BESS1")
    assert g.saturated == "p_limit"
    assert res.df_hz == pytest.approx(-(b.p_mw - b.p0_mw) / K(20.0), rel=1e-4)
    assert res.df_hz < 0


def test_an_island_with_no_grid_forming_source_is_refused():
    s = spec()
    del s["campus"]["units"]["GEN1"]["island"]         # a genset without island data stays PQ
    s["campus"]["units"]["BESS1"]["island"] = {"control": "gfl_pq", "pf_rated": t(0.95)}
    res = IF.island_flow(build_campus(s), rows())
    assert not res.viable and "no reference" in res.reason


def test_a_unit_that_is_not_online_does_not_form_the_island():
    res = IF.island_flow(build_campus(spec()), rows(), online={"BESS1", "PV1", "DC_LOAD"})
    assert res.viable, res.reason
    assert unit(res, "GEN1").role == "offline"
    # the offline genset's 5 MW is lost too: about 25 - 10 PV = 15 MW plus losses
    assert 15.0 < res.delta_p_ac_mw < 15.6
    b = unit(res, "BESS1")
    assert res.df_hz == pytest.approx(-(b.p_mw - b.p0_mw) / K(20.0), rel=1e-4)


# ── grid-following laws ─────────────────────────────────────────────────────

def fw(db=0.2, k=2.0, limit=5.0):
    return {"control": "gfl_fw", "pf_rated": t(0.95), "fw_k_mw_per_hz": t(k), "fw_deadband_hz": t(db),
            "fw_delay_s": t(0.5), "fw_ramp_mw_per_s": t(1.0), "fw_p_limit_mw": t(limit)}


def test_frequency_watt_does_nothing_inside_its_deadband():
    res = IF.island_flow(build_campus(spec(pv_island=fw(db=5.0))), rows())
    pv = unit(res, "PV1")
    assert pv.p_mw == pytest.approx(pv.p0_mw, abs=1e-9)


def test_frequency_watt_responds_beyond_its_deadband_at_the_common_frequency():
    res = IF.island_flow(build_campus(spec(pv_island=fw(db=0.2, k=2.0))), rows())
    pv = unit(res, "PV1")
    expected = -2.0 * math.copysign(1.0, res.df_hz) * max(0.0, abs(res.df_hz) - 0.2)
    assert pv.p_mw - pv.p0_mw == pytest.approx(expected, abs=1e-4)
    assert pv.p_mw > pv.p0_mw                          # under-frequency: it raises output
    assert IF.check_gate(res)["ok"]


def test_a_fixed_power_factor_gives_q_equal_p_tan_phi():
    res = IF.island_flow(build_campus(spec(pv_island={"control": "gfl_pf", "pf_rated": t(0.95),
                                                       "cos_phi": t(0.9)})), rows())
    pv = unit(res, "PV1")
    assert pv.q_mvar == pytest.approx(pv.p_mw * math.tan(math.acos(0.9)), rel=1e-6)


def test_a_q_u_unit_lands_on_its_curve():
    curve = [[0.95, 0.5], [1.05, -0.5]]                 # sloped everywhere: no dead band to hide in
    res = IF.island_flow(build_campus(spec(pv_island={"control": "gfl_qu", "pf_rated": t(0.95),
                                                       "qu_points": t(curve)})), rows())
    pv = unit(res, "PV1")
    v = float(res.bus.set_index("bus").at["MV1", "vm_pu"])
    xs, ys = [p[0] for p in curve], [p[1] for p in curve]
    expected = 16.5 * float(pd.Series(ys, index=xs).reindex(sorted(set(xs) | {v})).interpolate("index").at[v])
    assert pv.q_mvar == pytest.approx(expected, abs=1e-3)
    assert abs(pv.q_mvar) > 0.05


# ── Q-V droop through the virtual reactance ────────────────────────────────

def two_gfm(tie_x_ohm_per_km, q1=4.0, q2=8.0):
    """Two equal GFM batteries on two MV buses joined by a short tie, sharing a
    mostly reactive load. P is near zero, where x_v sharing is exact."""
    b1, b2 = gfm_bess(), gfm_bess()
    b1["q_droop_pct"], b2["q_droop_pct"] = t(q1), t(q2)
    return {"campus": {
        "name": "TWO_GFM", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 20.0, "vm_pu": t(1.0), "sk_max_mva": t(300.0),
                "sk_min_mva": t(200.0), "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {"A": {"vn_kv": 20.0}, "B": {"vn_kv": 20.0}},
        "cables": {
            "FEED": {"from_bus": "PCC", "to_bus": "A", "length_km": t(1.0), "r_ohm_per_km": t(0.1),
                     "x_ohm_per_km": t(0.1), "c_nf_per_km": t(0.0), "max_i_ka": t(2.0)},
            "TIE": {"from_bus": "A", "to_bus": "B", "length_km": t(1.0), "r_ohm_per_km": t(0.0001),
                    "x_ohm_per_km": t(tie_x_ohm_per_km), "c_nf_per_km": t(0.0), "max_i_ka": t(2.0)},
        },
        "units": {
            "LOAD": {"kind": "load", "bus": "A", "p_mw": t(1.0), "pf": t(0.1)},
            "B1": {"kind": "bess", "bus": "A", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                   "k_sc": t(1.2), "rx_sc": t(0.1), "island": b1},
            "B2": {"kind": "bess", "bus": "B", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                   "k_sc": t(1.2), "rx_sc": t(0.1), "island": b2},
        }}}


def two_rows():
    return pd.DataFrame({"unit_id": ["LOAD", "B1", "B2"], "p_mw": [-1.0, 0.0, 0.0], "status": [1, 1, 1]})


@pytest.mark.parametrize("tie_x", [0.4, 0.04])      # ohm: 0.001 and 0.0001 pu on a 1 MVA base
def test_q_v_droop_shares_reactive_power_through_x_v_and_converges_on_short_ties(tie_x):
    """At P ~ 0 each unit's Q goes as 1 / (its x_v + the tie between it and
    the load), on the unit base S_n = 22 MVA: x_v1 = 0.04, x_v2 = 0.08, and the
    tie is tie_x / (20 kV^2 / 22 MVA). With no tie the ratio is 1/k_q, 2.0."""
    res = IF.island_flow(build_campus(two_gfm(tie_x)), two_rows())
    assert res.viable, res.reason
    q1, q2 = unit(res, "B1").q_mvar, unit(res, "B2").q_mvar
    x_tie = tie_x / (20.0 ** 2 / 22.0)
    assert q1 / q2 == pytest.approx((0.08 + x_tie) / 0.04, rel=0.01)


# ── verdicts and structure ─────────────────────────────────────────────────

def test_a_voltage_outside_the_band_is_not_viable():
    res = IF.island_flow(build_campus(spec()), rows(),
                         settings=IF.IslandSettings(v_band=(0.9999, 1.0001)))
    assert not res.viable and "voltage" in res.reason


def test_an_overloaded_cable_is_not_viable():
    s = spec()
    s["campus"]["cables"]["CB1"]["max_i_ka"] = t(0.2)
    res = IF.island_flow(build_campus(s), rows())
    assert not res.viable and "overload" in res.reason and "CB1" in res.reason


def test_non_convergence_is_returned_not_raised(monkeypatch):
    def boom(*a, **k):
        raise pp.LoadflowNotConverged("no")
    monkeypatch.setattr(IF.pp, "runpp", boom)
    res = IF.island_flow(build_campus(spec()), rows())
    assert not res.viable and "converge" in res.reason


def test_the_campus_net_is_not_changed():
    camp = build_campus(spec())
    before = copy.deepcopy(camp.net)
    IF.island_flow(camp, rows())
    for table in ("bus", "line", "trafo", "sgen", "load", "ext_grid", "gen"):
        pd.testing.assert_frame_equal(getattr(camp.net, table), getattr(before, table))


def test_the_island_net_keeps_every_campus_branch_and_adds_only_droop_reactances():
    camp = build_campus(spec())
    net = IF.island_net(camp, rows())
    assert not net.ext_grid["in_service"].any()
    pd.testing.assert_frame_equal(net.line, camp.net.line)
    pd.testing.assert_frame_equal(net.trafo, camp.net.trafo)
    assert len(net.impedance) == 2                      # BESS1 and GEN1
    assert set(net.gen["name"]) == {"BESS1", "GEN1"}


def test_the_sync_check_inputs_are_reported():
    res = IF.island_flow(build_campus(spec()), rows())
    assert res.sync["df_hz"] == res.df_hz
    assert res.sync["dv_pu"] == pytest.approx(
        float(res.bus.set_index("bus").at["PCC", "vm_pu"]) - 1.0, abs=1e-12)


def test_a_gfm_beyond_its_current_limit_becomes_a_pq_source_at_that_current():
    """A reactive load (pf 0.45) drives BESS1 (22 MVA, i_max 1.2) past
    1.2 * 22 * V_terminal (25.2 MVA at 0.953 pu), though not past 1.2 * 22 at
    1 pu (26.4): the limit is judged at the terminal voltage, and the source
    then carries exactly that current at the voltage it ends up at. (At pf 0.5
    it runs at about 104 %: an overload, below its limit.)"""
    s = spec()
    s["campus"]["units"]["DC_LOAD"]["pf"] = t(0.45)
    res = IF.island_flow(build_campus(s), rows(load=22.0))
    b = unit(res, "BESS1")
    assert b.saturated == "current_limit"
    v = float(res.bus.set_index("bus").at["MV1", "vm_pu"])
    assert math.hypot(b.p_mw, b.q_mvar) == pytest.approx(1.2 * 22.0 * v, rel=1e-3)


@pytest.mark.parametrize("curve,sign", [([[0.0, 1.0], [0.5, 1.0], [1.0, 0.9]], 1.0),
                                        ([[0.0, -0.95], [1.0, -0.95]], -1.0)])
def test_a_cos_phi_of_p_curve_sets_q_and_a_negative_cos_phi_absorbs(curve, sign):
    res = IF.island_flow(build_campus(spec(pv_island={"control": "gfl_pf", "pf_rated": t(0.95),
                                                       "cosphi_p_points": t(curve)})), rows())
    pv = unit(res, "PV1")
    xs, ys = [p[0] for p in curve], [p[1] for p in curve]
    c = float(pd.Series(ys, index=xs).reindex(sorted(set(xs) | {pv.p_mw / 15.0})).interpolate("index").at[pv.p_mw / 15.0])
    assert pv.q_mvar == pytest.approx(sign * pv.p_mw * math.tan(math.acos(abs(c))), rel=1e-6)


def test_a_ups_keeps_its_battery_dispatch_and_never_forms_the_island():
    s = spec()
    s["campus"]["units"]["UPS1"] = {"kind": "ups", "bus": "MV2", "p_mw": t(20.0), "e_mwh": t(5.0),
                                    "s_mva": t(22.0), "island": {"it_load": ["DC_LOAD"],
                                                                 "walk_in_s": t(15.0), "f_window_hz": t(2.0)}}
    r = pd.concat([rows(), pd.DataFrame({"unit_id": ["UPS1"], "p_mw": [-2.0], "status": [1]})])
    res = IF.island_flow(build_campus(s), r)
    ups = unit(res, "UPS1")
    assert ups.role == "pq" and ups.p_mw == pytest.approx(-2.0)


def test_in_an_exporting_hour_the_battery_absorbs_and_frequency_rises():
    res = IF.island_flow(build_campus(spec()), rows(load=8.0, pv=12.0, gen=2.0))
    assert res.viable, res.reason
    b, g = unit(res, "BESS1"), unit(res, "GEN1")
    assert b.p_mw < 0 and res.df_hz > 0
    assert g.p_mw < g.p0_mw                             # the genset backs off too
    assert b.saturated == "none"
    assert res.df_hz == pytest.approx(-(b.p_mw - b.p0_mw) / K(20.0), rel=1e-4)


def test_frequency_watt_is_capped_at_its_limit():
    res = IF.island_flow(build_campus(spec(pv_island=fw(db=0.0, k=50.0, limit=1.0))), rows())
    pv = unit(res, "PV1")
    assert pv.p_mw - pv.p0_mw == pytest.approx(1.0, abs=1e-6)
    assert IF.check_gate(res)["ok"]


def test_the_gate_catches_a_reference_off_the_common_frequency_and_fw_off_its_law():
    res = IF.island_flow(build_campus(spec(pv_island=fw(db=0.2, k=2.0))), rows())
    assert IF.check_gate(res)["ok"]
    bad = copy.deepcopy(res)
    i = bad.units.index[bad.units["unit_id"] == "BESS1"][0]
    bad.units.at[i, "p_mw"] += 1.0
    assert not IF.check_gate(bad)["ok"]
    bad = copy.deepcopy(res)
    i = bad.units.index[bad.units["unit_id"] == "PV1"][0]
    bad.units.at[i, "p_mw"] += 0.5
    assert not IF.check_gate(bad)["ok"]


# ── I4b: the grid-connected control-law check ─────────────────────────────

def test_without_island_data_the_control_law_flow_is_todays_campus_flow():
    from gridspine.static.campus_flow import solve_cases
    camp = build_campus(campus_spec())
    today = solve_cases(camp, rows())["intact"]
    law = IF.control_law_flow(camp, rows())
    assert law.converged
    assert law.pcc_p_mw == pytest.approx(today.pcc_p_mw, abs=1e-6)
    assert law.pcc_q_mvar == pytest.approx(today.pcc_q_mvar, abs=1e-6)


def test_grid_connected_a_q_u_unit_follows_its_curve_and_moves_the_pcc_q():
    curve = [[0.95, 0.5], [1.05, -0.5]]
    camp = build_campus(spec(pv_island={"control": "gfl_qu", "pf_rated": t(0.95), "qu_points": t(curve)}))
    law = IF.control_law_flow(camp, rows())
    pv = law.units.set_index("unit_id").loc["PV1"]
    v = float(law.bus.set_index("bus").at["MV1", "vm_pu"])
    assert pv.q_mvar == pytest.approx(16.5 * (0.5 - (v - 0.95) * 10.0), abs=1e-3)


def test_grid_connected_a_gfm_holds_its_dispatched_p_and_its_q_follows_its_droop():
    camp = build_campus(spec())
    law = IF.control_law_flow(camp, rows(bess=3.0))
    b = law.units.set_index("unit_id").loc["BESS1"]
    assert b.p_mw == pytest.approx(3.0, abs=1e-6)
    # Q through x_v = 0.04 pu on 22 MVA from V0 = 1 pu to the terminal: to first order
    # Q = S_n * (V0 - V_t) / x_v
    v = float(law.bus.set_index("bus").at["MV1", "vm_pu"])
    assert b.q_mvar == pytest.approx(22.0 * (1.0 - v) / 0.04, rel=0.05)
    # a genset with island data still runs in power-factor control on the grid
    g = law.units.set_index("unit_id").loc["GEN1"]
    assert g.p_mw == pytest.approx(5.0) and g.q_mvar == pytest.approx(0.0, abs=1e-9)
    assert law.label == "steady-state screening"
