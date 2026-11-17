"""The plant's voltage-control strategy in the load flow and its PoC P-Q
capability across voltage (plan GC-02, ``static/voltage_control.py``).

The fixture is one PV unit behind one transformer at a 110 kV PoC: the
transformer has no iron losses, so its reactive loss is the series
impedance's alone, by hand. Every hand value below is computed from literal
numbers, never from the code under test.
"""

import copy
import dataclasses
import math

import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import voltage_control as vc
from gridspine.static.campus_flow import apply_hour, solve_cases
from gridspine.static.campus_reactive import ibr_q_curve
from gridspine.templates.grid_codes import load_grid_code, validate_profile
from gridspine.static.voltage_control import (
    PlantControl,
    control_case,
    droop_q,
    grid_equivalent,
    poc_pq_chart,
    scorecard_rows,
)


def t(value, source="assumed"):
    return {"value": value, "source": source}


SK_MIN, SK_MAX, RX = 500.0, 3000.0, 0.1


def _spec(vm=1.0, units=None):
    """PoC 110 kV -> one 40 MVA 110/20 kV transformer (vk 10 %, vkr 0.3 %, no
    iron losses) -> MV1 with one 20 MW / 25 MVA PV unit."""
    return {
        "name": "ONE", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 110.0, "vm_pu": t(vm), "sk_max_mva": t(SK_MAX), "sk_min_mva": t(SK_MIN),
                "rx_max": t(RX), "rx_min": t(RX)},
        "buses": {"MV1": {"vn_kv": 20.0}},
        "transformers": {"TR1": {"hv_bus": "PCC", "lv_bus": "MV1", "sn_mva": t(40.0), "vn_hv_kv": t(110.0),
                                 "vn_lv_kv": t(20.0), "vk_percent": t(10.0), "vkr_percent": t(0.3),
                                 "pfe_kw": t(0.0), "i0_percent": t(0.0)}},
        "cables": {},
        "units": units or {"PV1": {"kind": "pv", "bus": "MV1", "p_mw": t(20.0), "s_mva": t(25.0),
                                   "k_sc": t(1.1), "rx_sc": t(0.1)}},
    }


def one_unit(vm=1.0, units=None):
    return build_campus({"campus": _spec(vm, units)})


def hour(**p):
    p = p or {"PV1": 20.0}
    return pd.DataFrame({"unit_id": list(p), "p_mw": list(p.values()), "status": [1] * len(p)})


def intact(campus, control, rows=None):
    return solve_cases(campus, hour() if rows is None else rows, control=control)["intact"]


# ---- the droop by hand: d = 1 %, s = 4 %, Q_max = 10 Mvar --------------------------------------

D_PCT, S_PCT, Q_MAX = 1.0, 4.0, 10.0


def test_the_droop_outside_the_deadband_is_minus_delta_over_s_times_q_max_on_both_sides():
    # V = 1 + d + D with D = 0.02: Q = -0.02 / 0.04 x 10 = -5 (a mutant without "- d" gives -7.5,
    # one reading s in pu, not %, gives -0.05)
    assert droop_q(1.03, 1.0, D_PCT, S_PCT, Q_MAX) == pytest.approx(-5.0, abs=1e-12)
    assert droop_q(0.97, 1.0, D_PCT, S_PCT, Q_MAX) == pytest.approx(5.0, abs=1e-12)


def test_the_droop_is_zero_inside_the_deadband_and_at_both_of_its_edges():
    # d = 1.5625 % = 2^-6 and V on its edges are exact in binary, so "at the edge" is exactly at it
    for v in (1.0, 1.0078125, 0.9921875, 1.015625, 0.984375):
        assert droop_q(v, 1.0, 1.5625, S_PCT, Q_MAX) == 0.0
    # just past each edge Q has left zero, with the sign that pulls V back
    assert droop_q(1.015625 + 1e-6, 1.0, 1.5625, S_PCT, Q_MAX) == pytest.approx(-1e-6 / 0.04 * 10, rel=1e-6)
    assert droop_q(0.984375 - 1e-6, 1.0, 1.5625, S_PCT, Q_MAX) == pytest.approx(1e-6 / 0.04 * 10, rel=1e-6)


def test_the_droop_stops_at_q_max_beyond_the_slope():
    # D = 0.05 > s = 0.04: the line would ask -12.5 Mvar; the plant gives its -10
    assert droop_q(1.06, 1.0, D_PCT, S_PCT, Q_MAX) == -10.0
    assert droop_q(0.94, 1.0, D_PCT, S_PCT, Q_MAX) == 10.0


def test_the_droop_follows_its_reference_voltage():
    assert droop_q(1.05, 1.02, D_PCT, S_PCT, Q_MAX) == pytest.approx(-5.0, abs=1e-12)


@pytest.mark.parametrize("over, match", [
    ({"mode": "volts"}, "unknown control mode"),
    ({"mode": "pf", "setpoint": 0.0}, "pf setpoint"),
    ({"mode": "pf", "setpoint": 1.2}, "pf setpoint"),
    ({"mode": "v_control", "setpoint": 0.0}, "v_control setpoint"),
    ({"mode": "q_fixed", "setpoint": math.inf}, "finite"),
    ({"mode": "q_of_v", "slope_pct": 0.0, "q_max_mvar": 10.0}, "slope_pct"),
    ({"mode": "q_of_v", "slope_pct": 4.0, "q_max_mvar": math.nan}, "q_max_mvar"),
    ({"mode": "q_of_v", "slope_pct": 4.0, "q_max_mvar": 10.0, "deadband_pct": -1.0}, "deadband_pct"),
    ({"mode": "q_fixed", "grid": "mid"}, "'min' or 'max'"),
])
def test_an_impossible_control_is_refused(over, match):
    with pytest.raises(ContractError, match=match):
        PlantControl(**over)


# ---- the grid equivalent ----------------------------------------------------------------------

@pytest.mark.parametrize("case, z_ohm, c", [
    ("min", 1.1 * 110.0 ** 2 / 500.0, 1.1),      # 26.62 ohm: the weak grid, c_max on S_k''min
    ("max", 1.0 * 110.0 ** 2 / 3000.0, 1.0),     # 4.033 ohm: the strong grid, c_min on S_k''max
])
def test_the_grid_equivalent_is_c_un2_over_sk_behind_the_poc(case, z_ohm, c):
    net = copy.deepcopy(one_unit().net)
    imp, z, cc = grid_equivalent(net, case)
    assert (z, cc) == (pytest.approx(z_ohm, rel=1e-12), c)
    z_pu = z_ohm / (110.0 ** 2 / 100.0)            # on the equivalent's 100 MVA base
    x_pu = z_pu / math.sqrt(1 + RX ** 2)
    assert net.impedance.at[imp, "xft_pu"] == pytest.approx(x_pu, rel=1e-12)
    assert net.impedance.at[imp, "rft_pu"] == pytest.approx(RX * x_pu, rel=1e-12)
    assert net.bus.at[int(net.ext_grid["bus"].iloc[0]), "name"] == vc.SOURCE_BUS
    assert net.bus.at[int(net.impedance.at[imp, "to_bus"]), "name"] == "PCC"


def test_a_pcc_without_its_grid_strength_is_refused_never_solved_stiff():
    net = copy.deepcopy(one_unit().net)
    net.ext_grid["s_sc_min_mva"] = math.nan
    with pytest.raises(ContractError, match="S_k''min"):
        grid_equivalent(net, "min")


# ---- the closed loop --------------------------------------------------------------------------

def _hand_droop(v):
    """The droop of d = 1 %, s = 4 %, Q_max = 10 Mvar, from the formula, not the code."""
    dv = v - 1.0
    if abs(dv) <= 0.01:
        return 0.0
    return max(-10.0, min(10.0, -(dv - math.copysign(0.01, dv)) / 0.04 * 10.0))


@pytest.mark.parametrize("grid", ["min", "max"])
@pytest.mark.parametrize("vm, side", [(1.025, 1), (0.975, -1), (1.004, 0)])
def test_q_of_v_puts_the_poc_on_its_droop_on_both_grid_equivalents(grid, vm, side):
    ctl = PlantControl("q_of_v", deadband_pct=D_PCT, slope_pct=S_PCT, q_max_mvar=Q_MAX, grid=grid)
    f = intact(one_unit(vm=vm), ctl)
    o = f.control
    assert f.converged and o.converged and o.at_limit == ""
    dv = o.v_poc_pu - 1.0
    if side:
        assert side * dv > 0.01                   # outside the deadband, on the side asked
    else:
        assert abs(dv) < 0.01
    assert o.q_poc_mvar == pytest.approx(_hand_droop(o.v_poc_pu), abs=1e-3 * Q_MAX)
    assert f.pcc_q_mvar == pytest.approx(-o.q_poc_mvar)      # the PCC row keeps the grid's convention
    assert vc.SOURCE_BUS not in set(f.bus["bus"])


def test_the_weak_grid_moves_the_poc_more_than_the_strong_one_for_the_same_droop():
    ctl = PlantControl("q_of_v", deadband_pct=D_PCT, slope_pct=S_PCT, q_max_mvar=Q_MAX)
    weak = intact(one_unit(vm=1.025), ctl).control
    strong = intact(one_unit(vm=1.025), dataclasses.replace(ctl, grid="max")).control
    assert weak.z_ohm > strong.z_ohm
    # the absorbing plant pulls the PoC further below the source on the weak grid
    assert weak.v_poc_pu < strong.v_poc_pu


@pytest.mark.parametrize("grid", ["min", "max"])
def test_v_control_holds_the_poc_at_its_setpoint_when_q_is_available(grid):
    o = intact(one_unit(), PlantControl("v_control", setpoint=1.004, grid=grid)).control
    assert o.converged and o.at_limit == ""
    assert abs(o.v_poc_pu - 1.004) < 1e-4
    assert -1 < o.x < 1


@pytest.mark.parametrize("grid", ["min", "max"])
@pytest.mark.parametrize("setpoint, bound, x", [(1.2, "upper", 1.0), (0.8, "lower", -1.0)])
def test_v_control_out_of_reach_reports_the_bound_and_the_shortfall(grid, setpoint, bound, x):
    o = intact(one_unit(), PlantControl("v_control", setpoint=setpoint, grid=grid)).control
    assert o.converged and o.at_limit == bound and o.x == x
    assert o.shortfall == pytest.approx(abs(setpoint - o.v_poc_pu), abs=1e-12)
    assert o.shortfall > 0.05
    assert o.units_at_limit == ["PV1"]
    assert o.unit_q["PV1"] == pytest.approx(x * 15.0)        # sqrt(25^2 - 20^2) = 15 Mvar


def test_pf_mode_delivers_q_with_exported_p_for_a_positive_setpoint_and_absorbs_for_a_negative():
    tan = math.tan(math.acos(0.95))                             # 0.328684...
    assert tan == pytest.approx(0.3286841, abs=1e-7)
    lag = intact(one_unit(), PlantControl("pf", setpoint=0.95)).control
    assert lag.p_poc_mw > 19 and lag.q_poc_mvar > 0
    assert lag.q_poc_mvar == pytest.approx(lag.p_poc_mw * 0.3286841, abs=1e-5)
    lead = intact(one_unit(), PlantControl("pf", setpoint=-0.95)).control
    assert lead.q_poc_mvar == pytest.approx(-lead.p_poc_mw * 0.3286841, abs=1e-5)


TWO = {
    "PV1": {"kind": "pv", "bus": "MV1", "p_mw": t(20.0), "s_mva": t(25.0), "k_sc": t(1.1), "rx_sc": t(0.1)},
    "BESS1": {"kind": "bess", "bus": "MV1", "p_mw": t(10.0), "e_mwh": t(20.0), "s_mva": t(12.0),
              "k_sc": t(1.2), "rx_sc": t(0.1)},
    "PV2": {"kind": "pv", "bus": "MV1", "p_mw": t(10.0), "s_mva": t(10.0), "k_sc": t(1.1), "rx_sc": t(0.1)},
}


def test_the_ppc_shares_q_pro_rata_to_each_units_available_q():
    o = intact(one_unit(units=TWO), PlantControl("q_fixed", setpoint=5.0),
               hour(PV1=20.0, BESS1=10.0, PV2=10.0)).control
    assert o.converged and o.at_limit == ""
    assert o.q_poc_mvar == pytest.approx(5.0, abs=1e-6)
    # PV1 has sqrt(25^2 - 20^2) = 15 Mvar free, BESS1 sqrt(12^2 - 10^2) = 6.6332 Mvar
    assert o.unit_q["PV1"] / 15.0 == pytest.approx(o.unit_q["BESS1"] / math.sqrt(44.0), rel=1e-9)
    assert o.unit_q["PV1"] != pytest.approx(o.unit_q["BESS1"], rel=1e-3)     # not an equal split
    # PV2 runs at P = S: no headroom, Q 0, and it is not a unit without a rating
    assert o.unit_q["PV2"] == 0.0
    assert o.no_headroom == ["PV2"] and o.no_rating == []


def test_a_unit_without_a_rating_is_listed_apart_and_gets_no_q():
    campus = one_unit(units=TWO)
    work = copy.copy(campus)
    work.net = copy.deepcopy(campus.net)
    apply_hour(work, hour(PV1=20.0, BESS1=10.0, PV2=10.0))
    work.net.sgen.loc[work.net.sgen["name"] == "BESS1", "sn_mva"] = math.nan
    imp = grid_equivalent(work.net, "min")[0]
    o = control_case(work.net, work, imp, PlantControl("q_fixed", setpoint=5.0))
    assert o.no_rating == ["BESS1"] and o.unit_q["BESS1"] == 0.0
    assert o.no_headroom == ["PV2"]


def _prepared(control=None):
    campus = one_unit()
    work = copy.copy(campus)
    work.net = copy.deepcopy(campus.net)
    apply_hour(work, hour())
    return work, grid_equivalent(work.net, "min")[0]


def test_a_load_flow_that_fails_inside_the_loop_makes_the_case_non_converged_never_at_a_limit():
    for fail_on in (1, 2, 3, 5):                  # at either bound, or inside the root search
        calls = []

        def flaky(net, fail_on=fail_on):
            calls.append(1)
            if len(calls) == fail_on:
                raise pp.LoadflowNotConverged("test")
            pp.runpp(net)

        work, imp = _prepared()
        o = control_case(work.net, work, imp, PlantControl("v_control", setpoint=1.01), run=flaky)
        assert not o.converged and o.at_limit == "" and math.isnan(o.v_poc_pu), fail_on
        assert "did not converge" in o.note


def test_a_failed_loop_makes_the_hourflow_non_converged(monkeypatch):
    monkeypatch.setattr(vc, "control_case", lambda *a, **k: vc.ControlOutcome("q_fixed", False, note="x"))
    f = intact(one_unit(), PlantControl("q_fixed"))
    assert not f.converged and not f.control.converged


def test_a_loop_whose_q_works_against_the_poc_is_refused_not_root_found():
    def reversed_q(net):
        pp.runpp(net)
        net.res_impedance["q_to_mvar"] *= -1.0       # a reverse-wired measurement

    work, imp = _prepared()
    with pytest.raises(ContractError, match=r"g\(-1\).*> g\(\+1\)"):
        control_case(work.net, work, imp, PlantControl("q_fixed", setpoint=1.0), run=reversed_q)


def test_a_dispatch_on_the_units_and_a_controller_cannot_both_set_q():
    with pytest.raises(ContractError, match="cannot both"):
        solve_cases(one_unit(), hour(), setpoints={"sgen_q": {"PV1": 1.0}}, control=PlantControl("q_fixed"))


def test_without_a_controller_solve_cases_gives_masters_numbers():
    # pinned from master's campus_flow (755cbe3b) on this fixture, before GC-02
    f = solve_cases(one_unit(), hour())["intact"]
    assert f.control is None
    assert (f.pcc_p_mw, f.pcc_q_mvar, f.losses_mw) == (
        pytest.approx(PIN_P, abs=1e-9), pytest.approx(PIN_Q, abs=1e-9), pytest.approx(PIN_LOSS, abs=1e-9))
    assert list(f.bus["bus"]) == ["PCC", "MV1"]


PIN_P, PIN_Q, PIN_LOSS = -19.970015030041274, 0.9990491228289216, 0.02998496995872557


# ---- the PoC P-Q chart ------------------------------------------------------------------------

def _hand_q_poc(q_unit):
    """The Q reaching the 1.0 pu PoC from S = 20 + j q_unit MVA at the LV bus,
    through z = 0.003 + j sqrt(0.1^2 - 0.003^2) pu on 40 MVA: the two-bus
    equation V_lv = 1 + z conj(S / V_lv), by fixed point, minus I^2 x."""
    z = complex(0.003, math.sqrt(0.1 ** 2 - 0.003 ** 2))
    s = complex(20.0, q_unit) / 40.0
    v = 1 + 0j
    for _ in range(200):
        v = 1 + z * (s / v).conjugate()
    i = (s / v).conjugate()
    return 40.0 * (s.imag - abs(i) ** 2 * z.imag)


CURVE = {"q_capability_unit": {"points": [[0.0, -0.33, 0.33], [1.0, -0.33, 0.33]], "clause": "test curve",
                               "source": "assumed"}}


def test_the_chart_at_1_pu_is_the_inverter_curve_less_the_transformers_q_loss():
    campus = one_unit()
    curve = ibr_q_curve(campus, CURVE)
    avail = float(curve.loc[curve["p_pu"] == 1.0, "available_mvar"].iloc[0])
    assert avail == pytest.approx(15.0)
    chart = poc_pq_chart(campus, {}, 20.0, v_points=(1.0,), p_points=(1.0,))
    r = chart.iloc[0]
    assert r["q_max_mvar"] == pytest.approx(_hand_q_poc(avail), abs=1e-4)
    assert r["q_min_mvar"] == pytest.approx(_hand_q_poc(-avail), abs=1e-4)
    # the loss is real: the PoC sees less than the inverter gives, both ways
    assert 15.0 - r["q_max_mvar"] > 0.5 and -15.0 - r["q_min_mvar"] > 0.5
    assert r["status"] == "reported" and "no q_capability_pq" in r["detail"]


BAND = {"q_capability_pq": {"points": [[-1.0, -0.5, 0.5], [0.0, -0.5, 0.5]], "clause": "test band",
                            "source": "assumed"}}


def test_the_chart_judges_every_voltage_against_the_band_in_the_export_convention():
    chart = poc_pq_chart(one_unit(), BAND, 20.0, p_points=(0.0, 1.0))
    assert list(chart["v_poc_pu"].unique()) == [0.90, 0.95, 1.00, 1.05, 1.10]
    full = chart[chart["p_frac"] == 1.0]
    # +-0.5 x 20 = +-10 Mvar required; at full P the plant reaches about +12 / -17 Mvar
    assert set(full["status"]) == {"pass"}
    assert (full["req_q_max_mvar"] == 10.0).all() and (full["req_q_min_mvar"] == -10.0).all()
    # P = 0 is a small import (the losses), outside the band's P range: reported, not passed
    zero = chart[chart["p_frac"] == 0.0]
    assert set(zero["status"]) == {"reported"} and zero["detail"].str.contains("does not state this P").all()


def test_a_band_the_plant_cannot_reach_fails_and_names_the_voltage():
    tight = {"q_capability_pq": {**BAND["q_capability_pq"], "points": [[-1.0, -0.9, 0.9], [0.0, -0.9, 0.9]]}}
    chart = poc_pq_chart(one_unit(), tight, 20.0, p_points=(1.0,))
    assert set(chart["status"]) == {"fail"}
    assert (chart["shortfall_mvar"] > 1.0).all()
    assert chart["detail"].str.contains("V 0.90 pu").any()
    card = scorecard_rows(tight, chart, None, "no droop").set_index("check")
    assert card.at["poc_pq_band", "status_as_is"] == "fail"


def test_a_stated_voltage_range_narrows_the_judged_points_and_the_rest_read_reported():
    ranged = {**BAND, "q_band_v_range_pu": {"min": 0.95, "max": 1.05, "clause": "test range", "source": "assumed"}}
    chart = poc_pq_chart(one_unit(), ranged, 20.0, p_points=(1.0,)).set_index("v_poc_pu")
    assert chart.loc[[0.90, 1.10], "status"].tolist() == ["reported", "reported"]
    assert chart.loc[[0.95, 1.00, 1.05], "status"].tolist() == ["pass"] * 3
    assert chart.loc[0.90, "detail"].endswith("reported, not judged")


def test_statcom_counts_in_the_chart_and_switched_banks_are_listed_not_judged():
    with_comp = build_campus({"campus": {**_spec(), "compensation": [
        {"name": "ST1", "kind": "statcom", "bus": "MV1", "q_mvar": t(5.0)},
        {"name": "CAP1", "kind": "capacitor_bank", "bus": "MV1", "q_mvar": t(4.0), "steps": 2},
        {"name": "REA1", "kind": "shunt_reactor", "bus": "MV1", "q_mvar": t(3.0)}]}})
    a = poc_pq_chart(one_unit(), {}, 20.0, v_points=(1.0,), p_points=(1.0,)).iloc[0]
    b = poc_pq_chart(with_comp, {}, 20.0, v_points=(1.0,), p_points=(1.0,)).iloc[0]
    assert b["q_max_mvar"] - a["q_max_mvar"] > 4.5          # the 5 Mvar STATCOM, less its share of the loss
    assert (b["banks_cap_mvar"], b["banks_ind_mvar"]) == (4.0, 3.0)
    assert (a["banks_cap_mvar"], a["banks_ind_mvar"]) == (0.0, 0.0)



# ---- the profile keys and the scorecard -------------------------------------------------------

def _profile(**extra):
    p = load_grid_code("generic_assumed", raw=True)
    p.update(extra)
    return p


SLOPE = {"value": 4.0, "clause": "test slope", "source": "assumed"}
MODES = {"value": ["q_of_v", "pf"], "clause": "test modes", "source": "assumed"}
RANGE = {"min": 0.95, "max": 1.05, "clause": "test range", "source": "assumed"}


def test_the_voltage_control_keys_load():
    p = validate_profile("vc", _profile(q_v_slope_pct=SLOPE, voltage_control_modes=MODES, q_band_v_range_pu=RANGE))
    assert p["q_v_slope_pct"]["value"] == 4.0 and p["voltage_control_modes"]["value"] == ["q_of_v", "pf"]


@pytest.mark.parametrize("extra, match", [
    ({"q_v_slope_pct": {**SLOPE, "value": math.inf}}, "q_v_slope_pct value must be a finite number > 0"),
    ({"q_v_slope_pct": {**SLOPE, "value": 0.0}}, "q_v_slope_pct value must be a finite number > 0"),
    ({"q_v_slope_pct": {"value": 4.0, "source": "assumed"}}, "q_v_slope_pct has no clause"),
    ({"voltage_control_modes": {**MODES, "value": ["q_of_v", "droop"]}}, r"unknown mode\(s\) \['droop'\]"),
    ({"voltage_control_modes": {**MODES, "value": []}}, "non-empty list"),
    ({"voltage_control_modes": {**MODES, "value": "pf"}}, "non-empty list"),
    ({"voltage_control_modes": {**MODES, "value": ["pf", "pf"]}}, "repeats a mode"),
    ({"q_band_v_range_pu": {**RANGE, "min": 1.05, "max": 0.95}}, "0 < min < max"),
    ({"q_band_v_range_pu": {**RANGE, "max": math.inf}}, "0 < min < max"),
    ({"q_band_v_range_pu": {**RANGE, "min": 0.0}}, "0 < min < max"),
])
def test_an_impossible_voltage_control_key_is_refused_with_its_reason(extra, match):
    with pytest.raises(ContractError, match=match):
        validate_profile("vc", _profile(**extra))


def test_without_the_keys_every_voltage_control_row_is_unknown_never_pass():
    p = {k: v for k, v in _profile().items() if k not in ("q_capability_pq",)}
    chart = poc_pq_chart(one_unit(), p, 20.0, v_points=(1.0,), p_points=(1.0,))
    control, why = vc.droop_from(p, one_unit())
    assert control is None and "q_v_slope_pct" in why
    card = scorecard_rows(p, chart, None, why).set_index("check")
    assert set(card["status_as_is"]) == {"unknown"} and set(card["status_with_measures"]) == {"unknown"}
    assert list(card.index) == ["voltage_control_modes", "q_v_droop", "poc_pq_band"]


def test_the_droop_from_a_profile_takes_q_max_from_q_range_ibr_times_the_ibr_rating():
    p = _profile(q_v_slope_pct=SLOPE, voltage_deadband_pct={"value": 1.0, "clause": "db", "source": "assumed"},
                 q_range_ibr={"value": 0.33, "clause": "ibr", "source": "assumed"})
    control, why = vc.droop_from(p, one_unit())
    assert why == "" and control.mode == "q_of_v"
    assert (control.q_max_mvar, control.slope_pct, control.deadband_pct) == (pytest.approx(0.33 * 20.0), 4.0, 1.0)


def test_the_droop_row_fails_when_the_plant_cannot_follow_and_passes_when_it_does():
    ok = pd.DataFrame([{"period": 2030, "hour": 1, "grid": "min", "case": "intact", "mode": "q_of_v",
                        "converged": True, "v_poc_pu": 1.02, "q_poc_mvar": -2.5, "at_limit": "", "shortfall": 0.0}])
    short = pd.concat([ok, ok.assign(grid="max", at_limit="lower", shortfall=1.5)], ignore_index=True)
    lost = pd.concat([ok, ok.assign(converged=False)], ignore_index=True)
    chart = poc_pq_chart(one_unit(), BAND, 20.0, v_points=(1.0,), p_points=(1.0,))
    p = _profile(q_v_slope_pct=SLOPE, voltage_control_modes=MODES)
    rows = {name: scorecard_rows(p, chart, o, "").set_index("check").loc["q_v_droop"]
            for name, o in (("ok", ok), ("short", short), ("lost", lost))}
    assert rows["ok"]["status_as_is"] == "pass"
    assert rows["short"]["status_as_is"] == "fail" and rows["short"]["value"] == 1.5
    assert "S_k''max" in rows["short"]["detail"]
    assert rows["lost"]["status_as_is"] == "fail" and "did not converge" in rows["lost"]["detail"]
    modes = scorecard_rows(p, chart, ok, "").set_index("check").loc["voltage_control_modes"]
    assert modes["status_as_is"] == "reported" and "q_of_v, pf" in modes["detail"]



def test_the_hand_loss_is_sane():
    # a guard on the hand formula itself: with no Q at the unit the transformer only absorbs
    assert _hand_q_poc(0.0) < 0


# ---- the driver step --------------------------------------------------------------------------

def test_the_step_writes_the_chart_the_droop_on_both_grids_and_the_scorecard(tmp_path):
    import yaml

    from gridspine.drivers.campus_study import (
        POC_PQ_CSV,
        VOLTAGE_CONTROL_CSV,
        VOLTAGE_CONTROL_LF_CSV,
        draft_from_project,
        prepare_campus,
        rank_campus,
        voltage_control_campus,
    )
    from tests.gridspine.test_campus_hourly import solved_hub

    path = tmp_path / "network.nc"
    solved_hub(periods=[2030, 2040]).export_to_netcdf(str(path))
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(path).spec, path)
    rank_campus(run, k=1)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    db = {"value": 1.0, "clause": "test deadband", "source": "assumed"}
    ibr = {"value": 0.33, "clause": "test ibr", "source": "assumed"}
    (profiles / "vc_test.yaml").write_text(yaml.safe_dump(_profile(
        q_v_slope_pct=SLOPE, voltage_control_modes=MODES, voltage_deadband_pct=db, q_range_ibr=ibr)))
    out = voltage_control_campus(run, "vc_test", profile_dirs=[profiles])
    for name in (POC_PQ_CSV, VOLTAGE_CONTROL_CSV, VOLTAGE_CONTROL_LF_CSV):
        assert (run / name).is_file()
    lf = out["outcomes"]
    assert set(lf["grid"]) == {"min", "max"} and "intact" in set(lf["case"])
    assert lf["converged"].all()
    card = out["scorecard"].set_index("check")
    assert card.at["q_v_droop", "status_as_is"] in ("pass", "fail")
    assert card.at["voltage_control_modes", "status_as_is"] == "reported"
    bare = voltage_control_campus(run, "generic_assumed")["scorecard"].set_index("check")
    assert bare.at["q_v_droop", "status_as_is"] == "unknown"
    assert bare.loc["voltage_control_modes", "status_as_is"] == "unknown"
