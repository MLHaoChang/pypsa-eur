"""On-load tap changers set per critical hour and case (plan C15).

Where a campus voltage is out of its band, the study moves the on-load taps
of the campus transformers, parallel units together, top-down from the PCC,
to bring it back; among equal excursions it ends nearest neutral. The Q
dispatch comes first and is re-settled after a tap move. An hour that
passes keeps its file taps and today's numbers.

The oracles do not run the code under test:
* the tap sign by hand, V_lv = V_hv / (1 + t s);
* a brute force over every tap position, each solved on a pandapower net
  written directly in this file, with the voltage bands restated by hand
  (0.90-1.10 pu below 110 kV in the EU profile);
* the order of the dispatch rounds by hand, on stand-in steps;
* the taps written by the study, put on a directly written net, give the
  voltages the study reports.
"""
import itertools
import math

import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static.campus_flow import apply_setpoints, solve_cases
from gridspine.static.campus_invest import select_assets
from gridspine.static.campus_milp import select_assets_milp
from gridspine.static.campus_reactive import ReactiveRequirement
from gridspine.static.campus_taps import TAP_ROUNDS, Taps, tap_rounds
from gridspine.schema.contracts import ContractError
from gridspine.templates.grid_codes import load_grid_code
from tests.gridspine.test_campus import campus_spec, direct_net, t
from tests.gridspine.test_campus_flow import rows
from tests.gridspine.test_campus_invest import H7, comp, hourly, library, row, solve_direct, tr

PROFILE = load_grid_code("eu_rfg_dcc_ce")
WIDE = ReactiveRequirement(100.0, "test band", "assumed")
#: The EU profile's band below 110 kV, restated by hand.
LO, HI = 0.90, 1.10
OFF = {"BESS1": 0, "PV1": 0, "GEN1": 0}


def oltc(pos=0, changer="on_load", lo=-9, hi=9, side="hv", step=1.5, neutral=0):
    return {"changer": changer, "side": side, "neutral": neutral, "min": lo, "max": hi,
            "step_percent": t(step, "datasheet"), "pos": pos}


def lib_oltc(**kw):
    block = oltc(**kw)
    del block["pos"]
    return block


def dark(load_mw):
    """One dark hour (every inverter off), the load drawing ``load_mw``."""
    return rows(hour={"DC_LOAD": -load_mw, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}, status=OFF)


# --------------------------------------------------------------------------
# one group: the test campus's pair, wound 110/20 kV, with on-load taps
# --------------------------------------------------------------------------

def pair_spec(pos=0, pf=0.85, **tap):
    spec = campus_spec()
    c = spec["campus"]
    c["units"]["DC_LOAD"]["pf"] = t(pf)
    c["cables"]["CB1"]["max_i_ka"] = t(3.0)
    for name in ("TR1", "TR2"):
        c["transformers"][name]["vn_lv_kv"] = t(20.0)
        if pos is not None:
            c["transformers"][name]["tap"] = oltc(pos=pos, **tap)
    return spec


def pair_direct(load_mw, pf, pos, out=None):
    """The pair campus at one dark hour, written directly, both units at
    tap ``pos`` (HV side, 1.5 % a step), unit ``out`` (0 or 1) out of service."""
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    for name in ("TR1", "TR2"):
        pp.create_transformer_from_parameters(
            net, 0, 1, sn_mva=40.0, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4, vk_percent=12.0, pfe_kw=25.0,
            i0_percent=0.05, name=name, tap_side="hv", tap_neutral=0, tap_min=-9, tap_max=9, tap_step_percent=1.5,
            tap_pos=pos, tap_changer_type="Ratio")
    if out is not None:
        net.trafo.at[out, "in_service"] = False
    net.line.at[0, "max_i_ka"] = 3.0
    net.load.at[0, "p_mw"] = load_mw
    net.load.at[0, "q_mvar"] = load_mw * math.tan(math.acos(pf))
    net.sgen["in_service"] = False
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return None                                    # a failed position
    return net


def worst_by_hand(net, buses=("MV1", "MV2")):
    if net is None:
        return math.inf
    vm = dict(zip(net.bus["name"], net.res_bus["vm_pu"]))
    return max(max(vm[b] - HI, LO - vm[b], 0.0) for b in buses)


def brute_one(load_mw, pf, out=None):
    """``(excursion, position)``: every position solved, the smallest
    excursion, then the fewest steps from neutral."""
    return min(((worst_by_hand(pair_direct(load_mw, pf, p, out)), abs(p), p) for p in range(-9, 10)),
               key=lambda x: (round(x[0], 12), x[1]))


def test_a_tap_set_point_has_the_sign_worked_by_hand():
    """One unit, no load, no magnetising branch, taps on the HV side: at t =
    +2 and s = 1.5 % the LV voltage is 1/1.03 pu."""
    spec = pair_spec()
    c = spec["campus"]
    del c["transformers"]["TR2"]
    c["transformers"]["TR1"].update(pfe_kw=t(0.0), i0_percent=t(0.0))
    net = build_campus(spec).net
    for tbl in ("load", "sgen", "line"):
        net[tbl]["in_service"] = False
    apply_setpoints(net, {"trafo_tap": {"TR1": 2}})
    pp.runpp(net)
    assert float(net.res_bus.at[1, "vm_pu"]) == pytest.approx(1 / 1.03, abs=1e-9)
    with pytest.raises(ContractError, match="TRX"):
        apply_setpoints(net, {"trafo_tap": {"TRX": 1}})


@pytest.mark.parametrize("load_mw, pos", [(60.0, 0), (66.0, 0), (45.0, 4), (20.0, -8), (60.0, 5), (80.0, 0)])
def test_one_group_lands_on_the_brute_force_minimum_in_every_case(load_mw, pos):
    """The pair moves together. Intact and with either unit out, the
    search's position and excursion are the brute-force minimum over the 19
    positions (smallest excursion, then nearest neutral), and the solved
    voltages are the direct net's at that position. The file positions 4,
    -8 and 5 start the walk away from neutral: it still ends nearest
    neutral among the in-band positions."""
    camp = build_campus(pair_spec(pos=pos))
    log = {}
    cases = solve_cases(camp, dark(load_mw), retap=Taps(camp, PROFILE).hook(True, log))
    for case, out in (("intact", None), ("N-1:TR1", 0), ("N-1:TR2", 1)):
        exc, _, best = brute_one(load_mw, 0.85, out)
        got = log[case]
        on = [n for i, n in enumerate(("TR1", "TR2")) if i != out]
        assert set(got) == set(on) and set(got.values()) == {best}, (case, got, best)
        want = pair_direct(load_mw, 0.85, best, out)
        vm = cases[case].bus.set_index("bus")["vm_pu"]
        for b in ("MV1", "MV2"):
            assert vm[b] == pytest.approx(float(want.res_bus.at[int(want.bus.index[want.bus["name"] == b][0]),
                                                                  "vm_pu"]), abs=1e-9)
        assert worst_by_hand(want) == pytest.approx(exc, abs=1e-12)


def test_an_hour_in_band_keeps_its_file_taps_and_solves_as_without_taps():
    camp = build_campus(pair_spec(pos=2))
    log = {}
    with_taps = solve_cases(camp, dark(20.0), retap=Taps(camp, PROFILE).hook(True, log))
    for out in (None, 0, 1):
        assert worst_by_hand(pair_direct(20.0, 0.85, 2, out)) == 0.0             # in band at the file's taps
    assert log == {"intact": {"TR1": 2, "TR2": 2}, "N-1:TR1": {"TR2": 2}, "N-1:TR2": {"TR1": 2}}
    plain = solve_cases(camp, dark(20.0))
    for case in plain:
        pd.testing.assert_frame_equal(with_taps[case].bus, plain[case].bus)
        pd.testing.assert_frame_equal(with_taps[case].trafo, plain[case].trafo)


def test_an_off_circuit_tap_never_moves_and_a_group_with_one_untapped_unit_has_no_on_load_data():
    spec = pair_spec(pos=1, changer="off_circuit")
    camp = build_campus(spec)
    taps = Taps(camp, PROFILE)
    assert not taps.active and [g.oltc for g in taps.groups] == [False]
    log = {}
    solve_cases(camp, dark(66.0), retap=taps.hook(True, log))
    assert log["intact"] == {"TR1": 1, "TR2": 1}
    assert worst_by_hand(pair_direct(66.0, 0.85, 1)) > 0                        # it would have to move
    spec = pair_spec()
    del spec["campus"]["transformers"]["TR2"]["tap"]
    assert not Taps(build_campus(spec), PROFILE).active


def test_the_pcc_transformer_stays_at_its_file_tap_when_the_grid_operator_moves_it():
    camp = build_campus(pair_spec(pos=0))
    taps = Taps(camp, PROFILE, "grid_operator")
    assert not taps.active and taps.groups[0].pcc and taps.groups[0].oltc
    log = {}
    solve_cases(camp, dark(66.0), retap=taps.hook(True, log))
    assert log["intact"] == {"TR1": 0, "TR2": 0}
    with pytest.raises(ContractError, match="pcc_tap_control"):
        Taps(camp, PROFILE, "operator")


def test_a_failing_n_minus_1_case_moves_its_taps_while_the_intact_case_keeps_the_file_taps():
    """Intact, 40 MW is in band at neutral; with a unit out, MV2 falls below
    0.90 pu (both solved here). The intact case keeps its taps, the N-1 case
    gets the tap changer's answer, from the intact taps (plan C15, the
    owner's question 11)."""
    assert worst_by_hand(pair_direct(48.0, 0.85, 0)) == 0.0
    assert worst_by_hand(pair_direct(48.0, 0.85, 0, out=0)) > 0.0
    camp = build_campus(pair_spec(pos=0))
    log = {}
    solve_cases(camp, dark(48.0), retap=Taps(camp, PROFILE).hook(False, log))
    assert log["intact"] == {"TR1": 0, "TR2": 0}
    assert log["N-1:TR1"] == {"TR2": brute_one(48.0, 0.85, out=0)[2]} != {"TR2": 0}


# --------------------------------------------------------------------------
# two groups in series: 110/20 kV at the PCC, 20/10 kV below
# --------------------------------------------------------------------------

def two_spec(pos1=0, pos3=0):
    spec = campus_spec()
    c = spec["campus"]
    del c["transformers"]["TR2"]
    c["transformers"]["TR1"].update(sn_mva=t(80.0), vn_lv_kv=t(20.0), tap=oltc(pos=pos1))
    c["buses"]["LV1"] = {"vn_kv": 10.0}
    c["transformers"]["TR3"] = {
        "hv_bus": "MV2", "lv_bus": "LV1", "sn_mva": t(40.0), "vn_hv_kv": t(20.0), "vn_lv_kv": t(10.0),
        "vk_percent": t(10.0), "vkr_percent": t(0.4), "pfe_kw": t(10.0), "i0_percent": t(0.05), "tap": oltc(pos=pos3)}
    c["cables"]["CB1"]["max_i_ka"] = t(3.0)
    c["units"]["DC_LOAD"].update(pf=t(0.85))
    c["units"]["LV_LOAD"] = {"kind": "load", "bus": "LV1", "p_mw": t(40.0), "pf": t(0.85)}
    return spec


def two_direct(mv_mw, lv_mw, t1, t3):
    net = pp.create_empty_network(sn_mva=1.0, f_hz=50)
    pcc = pp.create_bus(net, 110.0, name="PCC")
    mv1, mv2 = pp.create_bus(net, 20.0, name="MV1"), pp.create_bus(net, 20.0, name="MV2")
    lv1 = pp.create_bus(net, 10.0, name="LV1")
    pp.create_ext_grid(net, pcc, vm_pu=1.0, s_sc_max_mva=3000.0, s_sc_min_mva=2000.0, rx_max=0.1, rx_min=0.12)
    tap = dict(tap_side="hv", tap_neutral=0, tap_min=-9, tap_max=9, tap_step_percent=1.5, tap_changer_type="Ratio")
    pp.create_transformer_from_parameters(net, pcc, mv1, sn_mva=80.0, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4,
                                          vk_percent=12.0, pfe_kw=25.0, i0_percent=0.05, tap_pos=t1, **tap)
    pp.create_line_from_parameters(net, mv1, mv2, length_km=2.0, r_ohm_per_km=0.0754, x_ohm_per_km=0.11,
                                   c_nf_per_km=330.0, max_i_ka=3.0)
    pp.create_transformer_from_parameters(net, mv2, lv1, sn_mva=40.0, vn_hv_kv=20.0, vn_lv_kv=10.0, vkr_percent=0.4,
                                          vk_percent=10.0, pfe_kw=10.0, i0_percent=0.05, tap_pos=t3, **tap)
    q = math.tan(math.acos(0.85))
    pp.create_load(net, mv2, p_mw=mv_mw, q_mvar=mv_mw * q)
    pp.create_load(net, lv1, p_mw=lv_mw, q_mvar=lv_mw * q)
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return math.inf
    return worst_by_hand(net, ("MV1", "MV2", "LV1"))


def two_rows(mv_mw, lv_mw):
    return rows(hour={"DC_LOAD": -mv_mw, "LV_LOAD": -lv_mw, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}, status=OFF)


def test_two_groups_are_visited_top_down_each_holding_its_own_zone():
    taps = Taps(build_campus(two_spec()), PROFILE)
    assert [g.name for g in taps.searched] == ["TR1", "TR3"]
    top, low = taps.searched
    assert (top.fed, top.zone) == ({"MV1", "MV2", "LV1"}, {"MV1", "MV2"})
    assert (low.fed, low.zone) == ({"LV1"}, {"LV1"})
    assert top.pcc and not low.pcc


@pytest.mark.parametrize("mv_mw, lv_mw, pos1, pos3", [
    (30.0, 20.0, 0, 0), (50.0, 30.0, 0, 0), (10.0, 35.0, 0, 0), (60.0, 10.0, 0, -9), (0.0, 0.0, -9, -9),
    (70.0, 35.0, 0, 0), (20.0, 5.0, 4, -6),
])
def test_two_groups_find_an_in_band_setting_exactly_when_the_brute_force_does(mv_mw, lv_mw, pos1, pos3):
    """All 19 x 19 positions solved directly: where any is in band, the
    search ends in band; where none is, it reports the failure."""
    feasible = any(two_direct(mv_mw, lv_mw, a, b) == 0.0 for a, b in itertools.product(range(-9, 10), repeat=2))
    camp = build_campus(two_spec(pos1, pos3))
    log = {}
    cases = solve_cases(camp, two_rows(mv_mw, lv_mw), retap=Taps(camp, PROFILE).hook(True, log))
    got = log["intact"]
    exc = two_direct(mv_mw, lv_mw, got["TR1"], got["TR3"])                    # the search's taps, re-solved here
    vm = cases["intact"].bus.set_index("bus")["vm_pu"]
    assert max(max(vm[b] - HI, LO - vm[b], 0.0) for b in ("MV1", "MV2", "LV1")) == pytest.approx(exc, abs=1e-9)
    assert (exc == 0.0) == feasible, (got, exc, feasible)


# --------------------------------------------------------------------------
# the rounds of Q and taps, on stand-in steps (the order by hand)
# --------------------------------------------------------------------------

class _R:
    def __init__(self, name):
        self.name, self.converged = name, True


def _steps(excursions, moves):
    """Stand-ins: ``dispatch(taps)`` names its round, ``solve`` returns the
    next excursion, ``search`` the next taps; every call is logged."""
    calls, exc, mv = [], iter(excursions), iter(moves)

    def dispatch(taps):
        calls.append(("dispatch", dict(taps)))
        return _R(f"Q{len([c for c in calls if c[0] == 'dispatch'])}")

    def solve(r, taps):
        calls.append(("solve", r.name, dict(taps)))
        return "net", next(exc)

    def search(net):
        calls.append(("search",))
        return next(mv)

    return calls, dispatch, solve, search


def test_the_rounds_dispatch_q_first_search_with_it_held_then_resettle_until_the_taps_stay():
    calls, dispatch, solve, search = _steps([0.02, 0.004, 0.004], [{"T": -2}, {"T": -3}, {"T": -3}])
    r, taps, hunted = tap_rounds(dispatch, solve, search, {"T": 0})
    assert calls == [("dispatch", {"T": 0}), ("solve", "Q1", {"T": 0}), ("search",),
                     ("dispatch", {"T": -2}), ("solve", "Q2", {"T": -2}), ("search",),
                     ("dispatch", {"T": -3}), ("solve", "Q3", {"T": -3}), ("search",)]
    assert (r.name, taps, hunted) == ("Q3", {"T": -3}, False)


def test_an_hour_in_band_after_the_dispatch_is_never_searched():
    calls, dispatch, solve, search = _steps([0.0], [])
    r, taps, hunted = tap_rounds(dispatch, solve, search, {"T": 1})
    assert calls == [("dispatch", {"T": 1}), ("solve", "Q1", {"T": 1})]
    assert (r.name, taps, hunted) == ("Q1", {"T": 1}, False)


def test_taps_still_moving_at_the_cap_keep_the_round_with_the_smallest_excursion_and_are_flagged():
    exc = [0.03, 0.01, 0.02, 0.01, 0.05]
    moves = [{"T": 1}, {"T": 0}, {"T": 1}, {"T": 0}, {"T": 1}]
    calls, dispatch, solve, search = _steps(exc, moves)
    r, taps, hunted = tap_rounds(dispatch, solve, search, {"T": 0})
    assert TAP_ROUNDS == 3
    assert [c[0] for c in calls].count("dispatch") == TAP_ROUNDS + 1           # one per tap change, plus the first
    assert [c[0] for c in calls].count("search") == TAP_ROUNDS + 1
    assert (r.name, taps, hunted) == ("Q2", {"T": 1}, True)                    # 0.01 first, not the last 0.01


# --------------------------------------------------------------------------
# the investment re-check: taps act inside it
# --------------------------------------------------------------------------

def single_spec(pf=0.98):
    spec = pair_spec(pos=None, pf=pf)
    del spec["campus"]["transformers"]["TR2"]
    return spec


def t60(**tap):
    return tr("T60", 60.0, 1.0e6, vk=30.0, tap=lib_oltc(**tap) if tap is not None else None)


def test_a_voltage_compensation_cannot_lift_is_fixed_by_the_taps_when_the_unit_has_an_oltc(tmp_path):
    """The case of test_campus_invest's unresolved voltage (MV2 below 0.90 pu
    on a 30 % unit, solved here), with the unit given an on-load tap
    changer: the taps bring MV2 back, the STATCOM is not bought, and the
    reported voltages are the directly written net's at the reported taps."""
    assert solve_direct(H7, [(60.0, 30.0)]).res_bus["vm_pu"].min() < 0.9
    lib = library(tmp_path, transformers=[t60()],
                  statcoms=[comp("statcoms", "ST10", 10.0, 400e3), comp("statcoms", "ST20", 20.0, 700e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE)
    assert out["unresolved"] == [] and out["history"].empty
    assert row(out, "reactive")["kind"] == "none"
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["campus_voltage", "status_with_measures"] == "pass"
    detail = comp_.at["campus_voltage", "detail_with_measures"]
    assert "after the on-load tap changers act" in detail and "TR1 -" in detail
    taps = out["taps"]
    assert list(taps.columns) == ["period", "hour", "case", "transformer", "group", "tap_pos", "ratio_pct", "moved_by"]
    (_, r), = taps.iterrows()
    assert (r["case"], r["transformer"], r["group"]) == ("intact", "TR1", "TR1")
    assert r["moved_by"] == r["tap_pos"] < 0
    assert r["ratio_pct"] == pytest.approx(100.0 * (1 + r["tap_pos"] * 0.015))
    # the re-solve: the written tap on a direct net with the hour's P (Q 0: the band holds without it)
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    pp.create_transformer_from_parameters(net, 0, 1, sn_mva=60.0, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4,
                                          vk_percent=30.0, pfe_kw=25.0, i0_percent=0.05, tap_side="hv", tap_neutral=0,
                                          tap_min=-9, tap_max=9, tap_step_percent=1.5, tap_pos=int(r["tap_pos"]),
                                          tap_changer_type="Ratio")
    net.line.at[0, "max_i_ka"] = 3.0
    net.load.at[0, "p_mw"] = 45.0
    net.load.at[0, "q_mvar"] = 45.0 * math.tan(math.acos(0.98))
    for name in ("BESS1", "PV1", "GEN1"):
        net.sgen.loc[net.sgen["name"] == name, "p_mw"] = H7[name]
    assert set(out["dispatch"]["q_mvar"]) == {0.0}
    pp.runpp(net)
    bus = out["state"].study.bus
    for b in ("MV1", "MV2"):
        want = float(net.res_bus.loc[net.bus["name"] == b, "vm_pu"].iloc[0])
        assert float(bus.loc[bus["bus"] == b, "vm_pu"].iloc[0]) == pytest.approx(want, abs=1e-9)
    assert worst_by_hand(net) == 0.0
    assert out["scope"]["pcc_tap_control"] == "campus"


def test_a_tap_range_too_short_is_reported_exhausted(tmp_path):
    """A changer that can only lower the LV voltage (0..+2) cannot lift MV2:
    the walk does not move, and the failure names the range end."""
    lib = library(tmp_path, transformers=[t60(lo=0, hi=2)], statcoms=[comp("statcoms", "ST10", 10.0, 400e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE)
    got = row(out, "reactive")
    assert got["status"] == "unresolved"
    assert "range exhausted: TR1 at +0 (min)" in got["reason"] and "MV2 0.8" in got["reason"]
    assert set(out["taps"]["tap_pos"]) == {0}


def test_the_grid_operators_pcc_taps_never_move_and_the_reason_says_whose_they_are(tmp_path):
    lib = library(tmp_path, transformers=[t60()], statcoms=[comp("statcoms", "ST10", 10.0, 400e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, pcc_tap_control="grid_operator")
    assert set(out["taps"]["moved_by"]) == {0}
    got = row(out, "reactive")
    assert got["status"] == "unresolved" and "TR1's taps are the grid operator's (pcc_tap_control)" in got["reason"]
    assert out["scope"]["pcc_tap_control"] == "grid_operator"
    with pytest.raises(ContractError, match="pcc_tap_control"):
        select_assets(single_spec(), table, sel, lib, WIDE, PROFILE, pcc_tap_control="nobody")


def test_a_pcc_voltage_out_of_band_is_the_grid_set_point_and_never_moves_a_tap(tmp_path):
    """The grid's set-point at 1.13 pu is above the 110 kV band (1.118). The
    existing 80 MVA unit, at tap +5, keeps the 20 kV buses in band (solved
    here), so no tap moves, and the PCC failure is reported as the
    operator's voltage."""
    spec = single_spec()
    spec["campus"]["pcc"]["vm_pu"] = t(1.13)
    spec["campus"]["transformers"]["TR1"].update(sn_mva=t(80.0), existing=True, tap=oltc(pos=5))
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    pp.create_transformer_from_parameters(net, 0, 1, sn_mva=80.0, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4,
                                          vk_percent=12.0, pfe_kw=25.0, i0_percent=0.05, tap_side="hv", tap_neutral=0,
                                          tap_min=-9, tap_max=9, tap_step_percent=1.5, tap_pos=5,
                                          tap_changer_type="Ratio")
    net.ext_grid.at[0, "vm_pu"] = 1.13
    net.line.at[0, "max_i_ka"] = 3.0
    net.load.at[0, "p_mw"] = 45.0
    net.load.at[0, "q_mvar"] = 45.0 * math.tan(math.acos(0.98))
    for name in ("BESS1", "PV1", "GEN1"):
        net.sgen.loc[net.sgen["name"] == name, "p_mw"] = H7[name]
    pp.runpp(net)
    assert worst_by_hand(net) == 0.0 and float(net.res_bus.at[0, "vm_pu"]) > 1.118
    lib = library(tmp_path, transformers=[tr("T60", 60.0, 1.0e6, tap=lib_oltc())],
                  statcoms=[comp("statcoms", "ST10", 10.0, 400e3)])
    table, sel = hourly((2030, 7, H7, {}))
    out = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    assert row(out, "transformer TR1")["status"] == "kept"
    assert len(out["taps"]) and set(out["taps"]["tap_pos"]) == {5} and set(out["taps"]["moved_by"]) == {0}
    got = row(out, "reactive")
    assert got["status"] == "unresolved" and got["reason"].startswith("pcc_voltage")
    assert "the grid set-point is outside the band: the operator's voltage, not a campus measure" in got["reason"]


def test_an_investment_whose_hours_pass_is_exactly_todays_with_tap_data_or_without(tmp_path):
    """Every hour in band at neutral: the bought units' on-load taps change
    nothing, not the flows, the dispatch, the investment or the report."""
    hours = ((2030, 7, H7, {}), (2030, 19, {"DC_LOAD": -30.0, "BESS1": 5.0, "PV1": 0.0, "GEN1": 0.0}, {}))
    table, sel = hourly(*hours)
    got = {}
    for name, tap in (("plain", None), ("oltc", lib_oltc())):
        lib = library(tmp_path, transformers=[tr("T63", 63.0, 1.2e6, tap=tap)],
                      statcoms=[comp("statcoms", "ST10", 10.0, 400e3)])
        got[name] = select_assets(pair_spec(pos=None, pf=0.98), table, sel, lib, WIDE, PROFILE)
    a, b = got["plain"], got["oltc"]
    for k in ("investment", "cost", "compliance", "dispatch", "history", "pcc", "short_circuit"):
        pd.testing.assert_frame_equal(a[k], b[k], obj=k)
    for x, y in ((a["state"].study.bus, b["state"].study.bus), (a["state"].study.trafo, b["state"].study.trafo)):
        pd.testing.assert_frame_equal(x, y)
    assert a["taps"].empty
    assert len(b["taps"]) == 2 * (2 + 1 + 1) and set(b["taps"]["moved_by"]) == {0}   # the units in service
    assert b["spec"]["campus"]["transformers"]["TR1"]["tap"]["pos"] == 0


# --------------------------------------------------------------------------
# the MILP sets taps by the same rule inside every AC evaluation
# --------------------------------------------------------------------------

def test_the_milp_and_c8_both_take_the_tap_where_a_tap_or_a_statcom_would_fix_the_voltage(tmp_path):
    """The 30 % unit with an OLTC, and a STATCOM in the library. By brute
    force over the reactive options (none, ST10, ST20), each with the tap
    rule's own oracle (every position solved directly): 'none' is in band
    and costs nothing, so it is the minimum; C8 and the MILP agree."""
    def in_band_somewhere(q):
        for p in range(-9, 10):
            net = direct_net()
            net.trafo.drop(net.trafo.index, inplace=True)
            pp.create_transformer_from_parameters(
                net, 0, 1, sn_mva=60.0, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4, vk_percent=30.0,
                pfe_kw=25.0, i0_percent=0.05, tap_side="hv", tap_neutral=0, tap_min=-9, tap_max=9,
                tap_step_percent=1.5, tap_pos=p, tap_changer_type="Ratio")
            net.line.at[0, "max_i_ka"] = 3.0
            net.load.at[0, "p_mw"] = 45.0
            net.load.at[0, "q_mvar"] = 45.0 * math.tan(math.acos(0.98))
            for name in ("BESS1", "PV1", "GEN1"):
                net.sgen.loc[net.sgen["name"] == name, "p_mw"] = H7[name]
            if q:
                pp.create_sgen(net, 1, p_mw=0.0, q_mvar=q)
            pp.runpp(net)
            if worst_by_hand(net) == 0.0:
                return True
        return False

    costs = {"none": 0.0, "ST10": 400e3, "ST20": 700e3}
    feasible = [k for k, q in (("none", 0.0), ("ST10", 0.0), ("ST20", 0.0)) if in_band_somewhere(q)]
    assert min(feasible, key=costs.get) == "none"
    lib = library(tmp_path, transformers=[t60()],
                  statcoms=[comp("statcoms", "ST10", 10.0, 400e3), comp("statcoms", "ST20", 20.0, 700e3)])
    table, sel = hourly((2030, 7, H7, {}))
    c8 = select_assets(single_spec(), table, sel, lib, WIDE, PROFILE)
    out = select_assets_milp(single_spec(), table, sel, lib, WIDE, PROFILE, c8=c8)
    for res in (c8, out):
        assert row(res, "reactive")["kind"] == "none" and res["unresolved"] == []
        assert (res["taps"]["moved_by"] < 0).all()
    assert out["scope"]["pcc_tap_control"] == "campus"
