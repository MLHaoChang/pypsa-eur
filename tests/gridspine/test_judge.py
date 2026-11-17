"""GC-07 parts 2 and 3: dynamic envelopes in the grid-code profile and the automatic judge."""

import copy

import numpy as np
import pytest

from gridspine.readback.channels import ChannelFrame
from gridspine.readback.judge import case_status, curve_at, judge
from gridspine.schema.contracts import ContractError
from gridspine.templates.grid_codes import (
    DYNAMIC_ENVELOPE_KEYS,
    PROFILE_KEYS,
    list_grid_codes,
    load_grid_code,
    validate_profile,
)

CITE = {"clause": "synthetic test clause", "source": "assumed"}
LVRT = {
    **CITE,
    "points": [[0.0, 0.2], [0.15, 0.2], [0.15, 0.5], [0.6, 0.85], [0.6, 0.9]],
}
HVRT = {**CITE, "points": [[0.0, 1.3], [0.1, 1.3], [0.1, 1.2], [0.5, 1.15]]}
IQ = {
    **CITE,
    "k_gain": 2.0,
    "deadband_pu": 0.1,
    "target_pct": 90.0,
    "rise_time_s": 0.03,
}
PREC = {**CITE, "recovery_pct": 90.0, "time_s": 0.25}
STEP = {
    **CITE,
    "rise_level_pct": 90.0,
    "rise_time_s": 0.06,
    "settling_time_s": 0.2,
    "settling_band_pct": 2.0,
    "overshoot_max_pct": 10.0,
}
DROOP = {
    **CITE,
    "droop_pct": 4.0,
    "deadband_hz": 0.2,
    "nominal_hz": 50.0,
    "tolerance_pct": 0.5,
}
PROFILE = {
    "lvrt_curve": LVRT,
    "hvrt_curve": HVRT,
    "reactive_current_injection": IQ,
    "active_power_recovery": PREC,
    "setpoint_step_response": STEP,
    "frequency_droop": DROOP,
}


def _frame(t, **channels):
    units = {"V": "pu", "IQ": "pu", "P": "pu", "CONN": "status", "F": "Hz", "Y": "pu"}
    return ChannelFrame(t, channels, {n: units[n] for n in channels}, "synthetic")


def _rows(result):
    return {r["check"]: r for r in result["rows"]}


# --- the ride-through curve -----------------------------------------------------------------------------------


def test_curve_linear_step_and_held():
    pts = LVRT["points"]
    assert curve_at(pts, [0.0, 0.1, 0.149999])[0] == 0.2
    assert curve_at(pts, [0.149999])[0] == 0.2  # just before the step
    assert curve_at(pts, [0.15])[0] == 0.5  # on the step: the later point holds
    assert curve_at(pts, [0.375])[0] == pytest.approx(0.675)  # mid-ramp 0.15..0.6
    assert curve_at(pts, [0.6])[0] == 0.9  # second step, right-continuous
    assert curve_at(pts, [5.0])[0] == 0.9  # the last value holds
    assert (
        curve_at([[0.1, 0.3], [0.2, 0.5]], [0.0])[0] == 0.3
    )  # the first value before the first point


# --- LVRT / HVRT ----------------------------------------------------------------------------------------------

T = np.round(np.arange(0.0, 1.5 + 1e-9, 1e-4), 10)  # 10 kHz, so 1 ms is ten samples


def _lvrt_case(**channels):
    return {
        "id": "lvrt-1",
        "kind": "lvrt",
        "t_event": 0.2,
        "t_clear": 0.35,
        "channels": channels,
    }


def test_voltage_exactly_on_the_lvrt_curve_passes():
    v = np.where(T < 0.2, 1.0, curve_at(LVRT["points"], T - 0.2))
    rows = _rows(
        judge(
            _frame(T, V=v, CONN=np.ones_like(T)),
            _lvrt_case(v1="V", connected="CONN"),
            PROFILE,
        )
    )
    r = rows["voltage_within_curve"]
    assert r["status"] == "pass" and r["margin"] == 0.0 and r["role"] == "validity"
    assert r["quantity"] == "positive-sequence voltage at the PoC"
    assert rows["stays_connected"]["status"] == "pass"


def test_one_ms_below_the_lvrt_curve_fails_at_that_time():
    v = np.where(T < 0.2, 1.0, curve_at(LVRT["points"], T - 0.2))
    dip = (T >= 0.5) & (T < 0.501)
    v = np.where(dip, v - 0.01, v)
    r = _rows(judge(_frame(T, V=v), _lvrt_case(v1="V"), PROFILE))[
        "voltage_within_curve"
    ]
    assert r["status"] == "fail" and r["at_s"] == pytest.approx(0.5)
    assert r["margin"] == pytest.approx(-0.01)


def test_hvrt_is_the_mirror_of_lvrt():
    case = {
        "id": "hvrt-1",
        "kind": "hvrt",
        "t_event": 0.2,
        "t_clear": 0.3,
        "channels": {"v_ll_min": "V"},
    }
    v = np.where(T < 0.2, 1.0, curve_at(HVRT["points"], T - 0.2))
    r = _rows(judge(_frame(T, V=v), case, PROFILE))["voltage_within_curve"]
    assert (
        r["status"] == "pass"
        and r["quantity"] == "minimum phase-to-phase voltage at the PoC"
    )
    v_low = np.where(T < 0.2, 1.0, 1.0 + 0 * T)  # well below the HVRT curve: within it
    assert (
        _rows(judge(_frame(T, V=v_low), case, PROFILE))["voltage_within_curve"][
            "status"
        ]
        == "pass"
    )
    above = np.where((T >= 0.4) & (T < 0.401), v + 0.01, v)
    r = _rows(judge(_frame(T, V=above), case, PROFILE))["voltage_within_curve"]
    assert r["status"] == "fail" and r["at_s"] == pytest.approx(0.4)
    # an LVRT trace well above the curve passes LVRT but is outside HVRT at the start
    high = np.where(T < 0.2, 1.0, 1.35)
    assert (
        _rows(judge(_frame(T, V=high), case, PROFILE))["voltage_within_curve"]["status"]
        == "fail"
    )
    assert (
        _rows(judge(_frame(T, V=high), _lvrt_case(v1="V"), PROFILE))[
            "voltage_within_curve"
        ]["status"]
        == "pass"
    )


def test_stays_connected_is_the_obligation():
    v = np.where(T < 0.2, 1.0, curve_at(LVRT["points"], T - 0.2) + 0.05)
    case = _lvrt_case(v1="V", connected="CONN")
    trip = np.where(T >= 0.7, 0.0, 1.0)
    res = judge(_frame(T, V=v, CONN=trip), case, {"lvrt_curve": LVRT})
    r = _rows(res)["stays_connected"]
    assert r["status"] == "fail" and r["at_s"] == pytest.approx(0.7)
    assert res["status"] == "fail"
    # the voltage leaves the curve at 0.6; a trip after that is allowed
    v_out = np.where(T >= 0.6, 0.5, v)
    res = judge(_frame(T, V=v_out, CONN=trip), case, {"lvrt_curve": LVRT})
    rows = _rows(res)
    assert rows["voltage_within_curve"]["status"] == "fail"
    assert (
        rows["stays_connected"]["status"] == "pass"
        and "ended at 0.6 s" in rows["stays_connected"]["detail"]
    )
    # ... but a trip before it is not
    early = np.where(T >= 0.5, 0.0, 1.0)
    assert (
        _rows(judge(_frame(T, V=v_out, CONN=early), case, {"lvrt_curve": LVRT}))[
            "stays_connected"
        ]["status"]
        == "fail"
    )


def test_no_status_channel_is_unknown_never_pass_on_the_voltage_alone():
    v = np.where(T < 0.2, 1.0, curve_at(LVRT["points"], T - 0.2))
    res = judge(_frame(T, V=v), _lvrt_case(v1="V"), {"lvrt_curve": LVRT})
    rows = _rows(res)
    assert rows["voltage_within_curve"]["status"] == "pass"
    assert (
        rows["stays_connected"]["status"] == "unknown"
        and "role 'connected'" in rows["stays_connected"]["detail"]
    )
    assert res["status"] == "unknown"
    v_out = np.where(T >= 0.6, 0.5, v)
    r = _rows(judge(_frame(T, V=v_out), _lvrt_case(v1="V"), {"lvrt_curve": LVRT}))[
        "stays_connected"
    ]
    assert r["status"] == "unknown" and "ended at 0.6 s" in r["detail"]


def test_missing_channel_or_key_is_unknown():
    res = judge(_frame(T, P=np.ones_like(T)), _lvrt_case(v1="V"), PROFILE)
    assert res["status"] == "unknown"
    assert all(r["status"] == "unknown" for r in res["rows"])
    assert "has no channel 'V'" in _rows(res)["voltage_within_curve"]["detail"]
    res = judge(_frame(T, V=np.ones_like(T)), _lvrt_case(v1="V"), {})
    assert all(
        r["status"] == "unknown" and r["detail"].endswith("not in profile")
        for r in res["rows"]
    )
    assert case_status([]) == "unknown"
    assert case_status([{"role": "validity", "status": "pass"}]) == "unknown"


# --- reactive current -----------------------------------------------------------------------------------------


def _iq_case(t, v, iq, kind="lvrt"):
    return judge(
        _frame(t, V=v, IQ=iq),
        {
            "id": "iq",
            "kind": kind,
            "t_event": 0.2,
            "t_clear": 0.35,
            "channels": {"v1": "V", "iq": "IQ"},
        },
        {"reactive_current_injection": IQ},
    )


def test_reactive_current_rise_and_sign():
    v = np.where((T >= 0.2) & (T < 0.35), 0.5, 1.0)
    # need 0.9 x 2 x (0.5 - 0.1) = 0.72 pu; a 0.8 pu ramp over 30 ms reaches it at 27 ms
    iq = np.clip((T - 0.2) / 0.03 * 0.8, 0.0, 0.8) * ((T >= 0.2) & (T < 0.35))
    r = _rows(_iq_case(T, v, iq))["reactive_current_rise"]
    assert r["status"] == "pass" and r["measured"] == pytest.approx(0.027, abs=1e-4)
    assert r["margin"] == pytest.approx(0.003, abs=1e-4)
    r = _rows(_iq_case(T, v, -iq))[
        "reactive_current_rise"
    ]  # right size, inductive: wrong sign
    assert r["status"] == "fail" and r["measured"] is None
    slow = np.clip((T - 0.2) / 0.05 * 0.8, 0.0, 0.8) * ((T >= 0.2) & (T < 0.35))
    r = _rows(_iq_case(T, v, slow))["reactive_current_rise"]
    assert r["status"] == "fail" and r["measured"] == pytest.approx(
        0.045, abs=1.5e-4
    )  # within one 0.1 ms sample
    # an overvoltage needs inductive (negative) current
    v_high = np.where(
        (T >= 0.2) & (T < 0.35), 1.3, 1.0
    )  # 0.3 - 0.1 = 0.2 beyond: need 0.36
    r = _rows(_iq_case(T, v_high, -iq, kind="hvrt"))["reactive_current_rise"]
    assert r["status"] == "pass" and "inductive" in r["detail"]
    assert (
        _rows(_iq_case(T, v_high, iq, kind="hvrt"))["reactive_current_rise"]["status"]
        == "fail"
    )


def test_reactive_current_inside_the_deadband_is_unknown_and_units_refused():
    v = np.where((T >= 0.2) & (T < 0.35), 0.95, 1.0)
    r = _rows(_iq_case(T, v, 0 * T))["reactive_current_rise"]
    assert r["status"] == "unknown" and "inside the 0.1 pu deadband" in r["detail"]
    f = ChannelFrame(T, {"V": v, "IQ": 0 * T}, {"V": "pu", "IQ": "kA"}, "x")
    with pytest.raises(
        ContractError, match="role 'iq' needs 'pu'; channel 'IQ' is in 'kA'"
    ):
        judge(
            f,
            {
                "id": "c",
                "kind": "lvrt",
                "t_event": 0.2,
                "t_clear": 0.35,
                "channels": {"v1": "V", "iq": "IQ"},
            },
            PROFILE,
        )


# --- active power recovery ------------------------------------------------------------------------------------


def _p_case(p, prec=PREC):
    return _rows(
        judge(
            _frame(T, P=p),
            {
                "id": "p",
                "kind": "lvrt",
                "t_event": 0.2,
                "t_clear": 0.35,
                "channels": {"p": "P"},
            },
            {"active_power_recovery": prec},
        )
    )


P_REC = np.where(
    T < 0.2, 1.0, np.where(T < 0.35, 0.0, np.clip((T - 0.35) / 0.2, 0.0, 1.0))
)


def test_active_power_recovers_within_time():
    r = _p_case(P_REC)["active_power_recovery"]  # 0.9 at 0.35 + 0.18
    assert r["status"] == "pass" and r["measured"] == pytest.approx(0.18, abs=1e-4)
    assert r["at_s"] == pytest.approx(0.53, abs=1e-4)
    r = _p_case(P_REC, {**PREC, "time_s": 0.15})["active_power_recovery"]
    assert r["status"] == "fail"


def test_a_dip_back_after_recovery_fails():
    p = np.where((T >= 0.9) & (T < 1.0), 0.5, P_REC)
    r = _p_case(p)["active_power_recovery"]
    assert r["status"] == "fail" and r["at_s"] == pytest.approx(1.0)
    r = _p_case(np.where(T >= 1.4, 0.5, P_REC))["active_power_recovery"]
    assert r["status"] == "fail" and "end of the record" in r["detail"]


def test_active_power_ramp_row():
    rows = _p_case(
        P_REC, {**PREC, "ramp_min_pu_per_s": 4.0}
    )  # 0.9 pu over 0.18 s = 5 pu/s
    r = rows["active_power_ramp"]
    assert r["status"] == "pass" and r["measured"] == pytest.approx(5.0, rel=1e-3)
    assert (
        _p_case(P_REC, {**PREC, "ramp_min_pu_per_s": 6.0})["active_power_ramp"][
            "status"
        ]
        == "fail"
    )
    flat = np.where((T >= 0.2) & (T < 0.35), 0.0, 1.0)
    flat = np.where(T >= 0.35, 1.0, flat)
    r = _p_case(flat, {**PREC, "ramp_min_pu_per_s": 4.0})["active_power_ramp"]
    assert r["status"] == "unknown" and "already recovered" in r["detail"]
    assert "active_power_ramp" not in _p_case(P_REC)


# --- setpoint step --------------------------------------------------------------------------------------------

TS = np.round(np.arange(0.0, 1.0 + 1e-9, 1e-3), 10)  # 1 kHz


def _step_response(t0=0.2, y0=0.0, y1=1.0):
    """Rises to 90 % of the change at t0 + 50 ms, peaks 5 % past it, settles back."""
    up = np.clip((TS - t0) * 18.0, 0.0, 1.05)
    decay = np.where(TS > 0.3, 1.0 + 0.05 * np.exp(-(TS - 0.3) / 0.05), up)
    frac = np.where(TS < 0.3, up, decay)
    return y0 + (y1 - y0) * frac


def _step(y, target=1.0, env=STEP, **case):
    c = {
        "id": "s",
        "kind": "setpoint_step",
        "t_event": 0.2,
        "channels": {"y": "Y"},
        **case,
    }
    if target is not None:
        c["target"] = target
    return _rows(judge(_frame(TS, Y=y), c, {"setpoint_step_response": env}))


def test_step_rise_time_and_overshoot_within_one_sample():
    rows = _step(_step_response())
    assert abs(rows["rise_time"]["measured"] - 0.050) <= 1e-3
    assert rows["overshoot"]["measured"] == pytest.approx(5.0, abs=1e-9)
    assert all(r["status"] == "pass" for r in rows.values())
    assert rows["settling_time"]["measured"] == pytest.approx(0.146, abs=1e-3)


def test_downward_step_is_judged_in_its_direction():
    rows = _step(_step_response(y0=1.0, y1=0.0), target=0.0)
    assert abs(rows["rise_time"]["measured"] - 0.050) <= 1e-3
    assert rows["settling_time"]["status"] == "pass"
    assert rows["settling_time"]["measured"] == pytest.approx(0.146, abs=1e-3)
    assert rows["overshoot"]["measured"] == pytest.approx(5.0, abs=1e-9)
    assert rows["overshoot"]["status"] == "pass"
    assert (
        _step(
            _step_response(y0=1.0, y1=0.0),
            target=0.0,
            env={**STEP, "overshoot_max_pct": 4.0},
        )["overshoot"]["status"]
        == "fail"
    )


def test_settling_band_edge_is_inside():
    env = {**STEP, "settling_band_pct": 25.0}  # change 4 -> band exactly 1.0
    edge = np.where(
        TS < 0.2, 0.0, np.where(TS < 0.3, 6.0, 5.0)
    )  # |5 - 4| == 1.0, on the edge
    r = _step(edge, target=4.0, env=env)["settling_time"]
    assert r["status"] == "pass" and r["at_s"] == pytest.approx(0.3)
    measured = r["measured"]
    beyond = np.where(TS < 0.2, 0.0, np.where(TS < 0.3, 6.0, np.nextafter(5.0, 6.0)))
    r = _step(beyond, target=4.0, env=env)["settling_time"]
    assert r["status"] == "fail" and r["measured"] is None
    # settling exactly at the limit passes, one ulp under it fails
    assert (
        _step(edge, target=4.0, env={**env, "settling_time_s": measured})[
            "settling_time"
        ]["status"]
        == "pass"
    )
    under = float(np.nextafter(measured, 0.0))
    assert (
        _step(edge, target=4.0, env={**env, "settling_time_s": under})["settling_time"][
            "status"
        ]
        == "fail"
    )


def test_step_without_target_discloses_the_end_mean():
    rows = _step(_step_response(), target=None)
    assert all("no target given" in r["detail"] for r in rows.values())
    rows = _step(np.where(TS < 0.2, 1.0, 1.0), target=None)
    assert all(r["status"] == "unknown" for r in rows.values())


def test_step_target_centres_the_band_and_shows_steady_state_error():
    y = _step_response() * 0.95  # settles 5 % short of the target
    rows = _step(y, target=1.0)
    assert rows["settling_time"]["status"] == "fail"
    assert rows["overshoot"]["measured"] == 0.0  # never passes the target
    assert rows["overshoot"]["status"] == "pass"
    assert _step(y, target=None)["settling_time"]["status"] == "pass"


# --- frequency droop ------------------------------------------------------------------------------------------


def _droop(f1, p1, env=DROOP, f0=50.0):
    f = np.where(TS < 0.2, f0, f1)
    p = np.where(TS < 0.2, 0.5, p1)
    return _rows(
        judge(
            _frame(TS, F=f, P=p),
            {
                "id": "d",
                "kind": "frequency_step",
                "t_event": 0.2,
                "channels": {"f": "F", "p": "P"},
            },
            {"frequency_droop": env},
        )
    )["droop"]


def test_droop_measured_against_set():
    r = _droop(50.5, 0.35)  # (0.5 - 0.2) / 50 / 0.15 = 4 %
    assert r["status"] == "pass" and r["measured"] == pytest.approx(4.0)
    assert _droop(50.5, 0.40)["status"] == "fail"  # 6 %
    assert _droop(49.5, 0.65)["status"] == "pass"  # under-frequency: more power
    r = _droop(50.5, 0.30)  # (0.5 - 0.2) / 50 / 0.2 = 3 %: 1 pp below the set 4 %
    assert r["status"] == "fail" and r["measured"] == pytest.approx(3.0)
    assert r["margin"] == pytest.approx(-0.5)


def test_droop_on_a_60_hz_system():
    env = {**DROOP, "nominal_hz": 60.0}
    r = _droop(60.5, 0.375, env=env, f0=60.0)  # (0.5 - 0.2) / 60 / 0.125 = 4 %
    assert r["status"] == "pass" and r["measured"] == pytest.approx(4.0)
    # the same trace judged on 50 Hz reads 4.8 %, outside the 0.5 pp tolerance
    r = _droop(60.5, 0.375, f0=60.0)
    assert r["status"] == "fail" and r["measured"] == pytest.approx(4.8)


def test_end_window_must_lie_after_the_step():
    y = _step_response()
    last = float(TS[-1])
    with pytest.raises(ContractError, match="reaches back to t_event"):
        _step(y, target=None, end_window_s=last - 0.2)  # exactly back to t_event
    with pytest.raises(ContractError, match="reaches back to t_event"):
        _step(y, target=None, end_window_s=last)
    rows = _step(y, target=None, end_window_s=float(np.nextafter(last - 0.2, 0.0)))
    assert all("no target given" in r["detail"] for r in rows.values())


def test_overshoot_of_a_subnormal_change_is_finite_or_fails():
    y = np.where(TS < 0.2, 0.0, 1.0)
    r = _step(y, target=1e-308)["overshoot"]
    assert r["status"] == "fail" and r["measured"] is None
    assert "too small to give a finite overshoot" in r["detail"]


def test_droop_guards_never_inf_or_nan():
    r = _droop(50.1, 0.35)
    assert r["status"] == "unknown" and "inside the 0.2 Hz deadband" in r["detail"]
    r = _droop(50.5, 0.5)
    assert r["status"] == "fail" and r["measured"] is None
    r = _droop(50.5, 0.6)
    assert r["status"] == "fail" and "does not oppose" in r["detail"]


# --- case refusals --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change,match",
    [
        ({"kind": "emt"}, "kind 'emt'"),
        ({"id": ""}, "needs an id"),
        ({"t_event": 5.0}, "not inside the record"),
        ({"t_event": float("nan")}, "finite time"),
        ({"t_clear": None}, "t_clear is required"),
        ({"t_clear": 0.1}, "must be after t_event"),
        ({"channels": {"v1": "V", "v_ll_min": "V"}}, "one voltage role"),
        ({"channels": {"volts": "V"}}, "unknown channel role"),
        ({"channels": {"v1": 3}}, "must be a name"),
        ({"channels": ["V"]}, "mapping of role"),
        ({"target": float("inf")}, "target must be a finite number"),
        ({"pre_window_s": 0}, "pre_window_s must be above zero"),
        ({"end_window_s": -1}, "finite time from 0"),
    ],
)
def test_case_refusals(change, match):
    case = {**_lvrt_case(v1="V"), **change}
    with pytest.raises(ContractError, match=match):
        judge(_frame(T, V=np.ones_like(T)), case, PROFILE)


def test_frame_and_profile_types_refused():
    with pytest.raises(ContractError, match="needs a ChannelFrame"):
        judge({}, _lvrt_case(v1="V"), PROFILE)
    with pytest.raises(ContractError, match="profile must be a mapping"):
        judge(_frame(T, V=np.ones_like(T)), _lvrt_case(v1="V"), [])
    with pytest.raises(ContractError, match="a case must be a mapping"):
        judge(_frame(T, V=np.ones_like(T)), [], PROFILE)


# --- profile validation ---------------------------------------------------------------------------------------


def _base():
    p = load_grid_code("generic_assumed", raw=True)
    p.pop("name", None)
    return p


def test_envelopes_validate_and_are_profile_keys():
    assert set(DYNAMIC_ENVELOPE_KEYS) <= PROFILE_KEYS
    p = {**_base(), **copy.deepcopy(PROFILE)}
    p["active_power_recovery"]["ramp_min_pu_per_s"] = 2.0
    assert validate_profile("x", p)["lvrt_curve"]["points"] == LVRT["points"]


def test_shipped_profiles_validate_unchanged():
    for name in list_grid_codes():
        raw = load_grid_code(name, raw=True)
        before = copy.deepcopy(raw)
        validate_profile(name, raw)
        assert raw == before
        assert not set(raw) & set(DYNAMIC_ENVELOPE_KEYS)


@pytest.mark.parametrize(
    "key,change,match",
    [
        ("lvrt_curve", {"points": []}, "non-empty list"),
        ("lvrt_curve", {"points": [[0, 0.2], [0.1]]}, r"points\[1\] must be"),
        ("lvrt_curve", {"points": [[0, 0.2], [-0.1, 0.5]]}, r"points\[1\] must be"),
        (
            "hvrt_curve",
            {"points": [[0, 1.3], [0.2, 1.2], [0.1, 1.1]]},
            "times never decrease",
        ),
        ("hvrt_curve", {"points": [[0, float("nan")]]}, r"points\[0\] must be"),
        ("lvrt_curve", {"clause": ""}, "has no clause"),
        ("lvrt_curve", {"source": "guess"}, "unknown source"),
        (
            "reactive_current_injection",
            {"k_gain": 0},
            r"k_gain must be a finite number in \(0, inf\)",
        ),
        (
            "reactive_current_injection",
            {"deadband_pu": -0.1},
            r"deadband_pu must be .* \[0, inf\)",
        ),
        (
            "reactive_current_injection",
            {"target_pct": 0},
            r"target_pct must be .* \(0, 100\]",
        ),
        ("reactive_current_injection", {"target_pct": 100.5}, r"target_pct must be"),
        (
            "reactive_current_injection",
            {"rise_time_s": float("inf")},
            "rise_time_s must be",
        ),
        ("active_power_recovery", {"time_s": -1}, "time_s must be"),
        (
            "active_power_recovery",
            {"ramp_min_pu_per_s": 0},
            "ramp_min_pu_per_s must be",
        ),
        (
            "setpoint_step_response",
            {"overshoot_max_pct": -1},
            r"overshoot_max_pct must be .* \[0, 100\]",
        ),
        (
            "setpoint_step_response",
            {"settling_band_pct": True},
            "settling_band_pct must be",
        ),
        ("frequency_droop", {"droop_pct": 0}, "droop_pct must be"),
        ("frequency_droop", {"nominal_hz": 0}, "nominal_hz must be"),
        ("frequency_droop", {"tolerance_pct": "0.5"}, "tolerance_pct must be"),
    ],
)
def test_envelope_refusals(key, change, match):
    p = {**_base(), key: {**copy.deepcopy(PROFILE[key]), **change}}
    with pytest.raises(ContractError, match=match):
        validate_profile("x", p)


def test_envelope_missing_field_refused():
    env = {k: v for k, v in STEP.items() if k != "settling_time_s"}
    with pytest.raises(
        ContractError, match="setpoint_step_response has no settling_time_s"
    ):
        validate_profile("x", {**_base(), "setpoint_step_response": env})


def test_100_pct_and_zero_times_are_in_range():
    p = {
        **_base(),
        "setpoint_step_response": {
            **STEP,
            "rise_level_pct": 100.0,
            "overshoot_max_pct": 0.0,
            "rise_time_s": 0.0,
        },
    }
    validate_profile("x", p)


# --- one targeted test per guard and boundary (GK A2 B8) --------------------------------------------------------


def test_curve_interpolates_on_the_first_segment():
    assert curve_at([[0.0, 0.0], [1.0, 1.0]], [0.5])[0] == 0.5


FULL = {"v1": "V", "iq": "IQ", "p": "P", "connected": "CONN"}


def _full_frame(v=None, iq=None, p=None, conn=None):
    one = np.ones_like(T)
    return _frame(
        T,
        V=one if v is None else v,
        IQ=0 * one if iq is None else iq,
        P=one if p is None else p,
        CONN=one if conn is None else conn,
    )


def test_absent_envelopes_are_not_in_profile_even_with_every_channel():
    res = judge(_full_frame(), _lvrt_case(**FULL), {})
    assert {r["check"] for r in res["rows"]} >= {
        "reactive_current_rise",
        "active_power_recovery",
    }
    assert all(
        r["detail"].endswith("not in profile") and r["clause"] == ""
        for r in res["rows"]
    )
    step = judge(
        _frame(TS, Y=0 * TS),
        {"id": "s", "kind": "setpoint_step", "t_event": 0.2, "channels": {"y": "Y"}},
        {},
    )
    assert all(
        r["detail"] == "setpoint_step_response not in profile" for r in step["rows"]
    )
    droop = judge(
        _frame(TS, F=50 + 0 * TS, P=0 * TS),
        {
            "id": "d",
            "kind": "frequency_step",
            "t_event": 0.2,
            "channels": {"f": "F", "p": "P"},
        },
        {},
    )
    assert droop["rows"][0]["detail"] == "frequency_droop not in profile"


def test_missing_step_and_droop_channels_are_unknown():
    step = judge(
        _frame(TS, F=0 * TS),
        {"id": "s", "kind": "setpoint_step", "t_event": 0.2, "channels": {}},
        PROFILE,
    )
    assert all(
        r["status"] == "unknown" and "role 'y'" in r["detail"] for r in step["rows"]
    )
    droop = judge(
        _frame(TS, F=50 + 0 * TS),
        {"id": "d", "kind": "frequency_step", "t_event": 0.2, "channels": {"f": "F"}},
        PROFILE,
    )
    assert (
        droop["rows"][0]["status"] == "unknown"
        and "role 'p'" in droop["rows"][0]["detail"]
    )


def test_clause_and_source_come_from_the_envelope():
    r = _rows(judge(_full_frame(), _lvrt_case(**FULL), PROFILE))["voltage_within_curve"]
    assert (r["clause"], r["source"]) == ("synthetic test clause", "assumed")


def test_no_voltage_role_names_v1_and_v_ll_min_feeds_the_reactive_check():
    rows = _rows(judge(_full_frame(), _lvrt_case(iq="IQ"), PROFILE))
    assert "role 'v1'" in rows["reactive_current_rise"]["detail"]
    v = np.where((T >= 0.2) & (T < 0.35), 0.5, 1.0)
    iq = np.where((T >= 0.22) & (T < 0.35), 0.8, 0.0)
    case = {**_lvrt_case(), "channels": {"v_ll_min": "V", "iq": "IQ"}}
    assert (
        _rows(judge(_full_frame(v=v, iq=iq), case, PROFILE))["reactive_current_rise"][
            "status"
        ]
        == "pass"
    )


def test_pre_window_needs_a_sample_and_includes_its_start():
    with pytest.raises(ContractError, match="no sample in the 4e-05 s before t_event"):
        judge(
            _full_frame(),
            {**_lvrt_case(**FULL), "t_event": 0.20005, "pre_window_s": 0.00004},
            PROFILE,
        )
    p = np.where(
        T == 0.1, 0.0, P_REC
    )  # the window's first sample, exactly t_event - 0.1 s
    assert "pre-fault 0.999 pu" in _p_case(p)["active_power_recovery"]["detail"]


def test_end_window_includes_its_start():
    y = np.where(TS < 0.2, 0.0, 1.0)
    y = np.where(np.isclose(TS, 0.9), 1.1, y)  # exactly t[-1] - 0.1 s
    rows = _step(y, target=None)
    assert (
        "final value 1.00099 " in rows["rise_time"]["detail"]
    )  # 101 samples, one at 1.1


def test_fault_window_is_event_inclusive_and_clear_exclusive():
    v = np.where((T >= 0.2) & (T < 0.35), 0.5, 1.0)
    v = np.where((T == 0.2) | (T == 0.35), 0.0, v)  # distinct values on both edges
    r = _rows(_iq_case(T, v, 0 * T))["reactive_current_rise"]
    assert f"voltage deviation {1.0 - (0.0 + 1499 * 0.5) / 1500:g} pu" in r["detail"]


def test_deadband_edges_are_unknown():
    v = np.where((T >= 0.2) & (T < 0.35), 0.75, 1.0)
    r = _rows(
        judge(
            _full_frame(v=v),
            _lvrt_case(**FULL),
            {"reactive_current_injection": {**IQ, "deadband_pu": 0.25}},
        )
    )
    assert r["reactive_current_rise"]["status"] == "unknown"
    r = _droop(50.25, 0.35, {**DROOP, "deadband_hz": 0.25})
    assert r["status"] == "unknown"


def test_reactive_current_exactly_at_need_and_rise_at_limit():
    v = np.where((T >= 0.2) & (T < 0.35), 0.5, 1.0)
    need = IQ["target_pct"] / 100 * IQ["k_gain"] * (abs(1.0 - 0.5) - IQ["deadband_pu"])
    iq = np.where((T >= 0.227) & (T < 0.35), need, 0.0)
    r = _rows(_iq_case(T, v, iq))["reactive_current_rise"]
    assert r["at_s"] == pytest.approx(0.227)
    at_limit = {**IQ, "rise_time_s": r["measured"]}
    res = judge(
        _frame(T, V=v, IQ=iq),
        {**_lvrt_case(v1="V", iq="IQ")},
        {"reactive_current_injection": at_limit},
    )
    assert _rows(res)["reactive_current_rise"]["status"] == "pass"
    under = {**IQ, "rise_time_s": float(np.nextafter(r["measured"], 0))}
    res = judge(
        _frame(T, V=v, IQ=iq),
        {**_lvrt_case(v1="V", iq="IQ")},
        {"reactive_current_injection": under},
    )
    assert _rows(res)["reactive_current_rise"]["status"] == "fail"


def test_recovery_scales_with_pre_fault_power_and_exact_level_counts():
    p = 0.8 * P_REC
    r = _p_case(p)["active_power_recovery"]
    assert r["measured"] == pytest.approx(0.18, abs=1e-4)
    level = PREC["recovery_pct"] / 100 * 0.5  # an exact pre-fault mean
    held = np.where(
        T < 0.2, 0.5, np.where(T < 0.4, 0.0, level)
    )  # sits exactly on the level
    r = _p_case(held)["active_power_recovery"]
    assert r["status"] == "pass" and r["at_s"] == pytest.approx(0.4)


def test_recovery_and_ramp_at_their_limits():
    took = _p_case(P_REC)["active_power_recovery"]["measured"]
    assert (
        _p_case(P_REC, {**PREC, "time_s": took})["active_power_recovery"]["status"]
        == "pass"
    )
    under = float(np.nextafter(took, 0))
    assert (
        _p_case(P_REC, {**PREC, "time_s": under})["active_power_recovery"]["status"]
        == "fail"
    )
    ramp = _p_case(P_REC, {**PREC, "ramp_min_pu_per_s": 1.0})["active_power_ramp"][
        "measured"
    ]
    assert (
        _p_case(P_REC, {**PREC, "ramp_min_pu_per_s": ramp})["active_power_ramp"][
            "status"
        ]
        == "pass"
    )
    over = float(np.nextafter(ramp, 10))
    assert (
        _p_case(P_REC, {**PREC, "ramp_min_pu_per_s": over})["active_power_ramp"][
            "status"
        ]
        == "fail"
    )


def test_ramp_fails_when_power_never_recovers():
    r = _p_case(np.where(T >= 0.2, 0.0, 1.0), {**PREC, "ramp_min_pu_per_s": 1.0})[
        "active_power_ramp"
    ]
    assert r["status"] == "fail" and r["measured"] is None


def test_step_at_the_event_sample_and_exact_rise_level():
    jump = np.where(TS < 0.2, 0.0, 1.0)
    rows = _step(jump)
    assert (
        rows["rise_time"]["measured"] == 0.0
        and rows["settling_time"]["measured"] == 0.0
    )
    ramp = np.where(
        TS < 0.2, 0.0, np.where(TS < 0.23, 0.5, np.where(TS < 0.24, 0.9, 1.0))
    )
    assert _step(ramp)["rise_time"]["at_s"] == pytest.approx(0.23)


def test_step_limits_at_the_edge():
    rows = _step(_step_response())
    rise, over = rows["rise_time"]["measured"], rows["overshoot"]["measured"]
    assert (
        _step(_step_response(), env={**STEP, "rise_time_s": rise})["rise_time"][
            "status"
        ]
        == "pass"
    )
    assert (
        _step(
            _step_response(), env={**STEP, "rise_time_s": float(np.nextafter(rise, 0))}
        )["rise_time"]["status"]
        == "fail"
    )
    assert (
        _step(_step_response(), env={**STEP, "overshoot_max_pct": over})["overshoot"][
            "status"
        ]
        == "pass"
    )
    assert (
        _step(
            _step_response(),
            env={**STEP, "overshoot_max_pct": float(np.nextafter(over, 0))},
        )["overshoot"]["status"]
        == "fail"
    )


def test_droop_tolerance_edge():
    r = _droop(50.5, 0.35, {**DROOP, "tolerance_pct": 1.0})
    margin = r["margin"]
    tol = 1.0 - margin  # |measured - set|
    assert _droop(50.5, 0.35, {**DROOP, "tolerance_pct": tol})["status"] == "pass"
    assert (
        _droop(50.5, 0.35, {**DROOP, "tolerance_pct": float(np.nextafter(tol, 0))})[
            "status"
        ]
        == "fail"
    )


def test_trip_on_the_exit_sample_is_allowed_and_status_must_be_binary():
    v = np.where(T < 0.2, 1.0, curve_at(LVRT["points"], T - 0.2) + 0.05)
    v_out = np.where(T >= 0.6, 0.5, v)
    trip = np.where(T >= 0.6, 0.0, 1.0)
    r = _rows(
        judge(
            _frame(T, V=v_out, CONN=trip), _lvrt_case(v1="V", connected="CONN"), PROFILE
        )
    )
    assert r["stays_connected"]["status"] == "pass"
    with pytest.raises(ContractError, match="must be 0 or 1"):
        judge(
            _frame(T, V=v, CONN=0.5 + 0 * T),
            _lvrt_case(v1="V", connected="CONN"),
            PROFILE,
        )


@pytest.mark.parametrize(
    "change,match",
    [
        ({"id": 5}, "needs an id"),
        ({"t_event": "0.2"}, "finite time from 0"),
        ({"t_event": True}, "finite time from 0"),
        ({"pre_window_s": -0.05}, "finite time from 0"),
        ({"t_event": 0.0}, "not inside the record"),
        ({"t_event": 1.5}, "not inside the record"),
        ({"t_clear": 0.2}, "must be after t_event"),
        ({"t_clear": 1.6}, "must be after t_event"),
        ({"target": True}, "target must be a finite number"),
        ({"target": "1"}, "target must be a finite number"),
    ],
)
def test_more_case_refusals(change, match):
    with pytest.raises(ContractError, match=match):
        judge(_full_frame(), {**_lvrt_case(**FULL), **change}, PROFILE)


def test_clearing_at_the_last_sample_is_allowed():
    judge(_full_frame(), {**_lvrt_case(**FULL), "t_clear": 1.5}, PROFILE)


def test_profile_point_shapes_and_optional_ramp():
    validate_profile("x", {**_base(), "active_power_recovery": PREC})
    validate_profile(
        "x", {**_base(), "lvrt_curve": {**LVRT, "points": [[0.0, 0.0], [0.2, 0.9]]}}
    )
    for points, match in [
        ("ab", "non-empty list"),
        ([[0, 0.2, 1]], r"points\[0\]"),
        (["ab"], r"points\[0\]"),
        ([{"t": 0}], r"points\[0\]"),
        ([[0, -0.1]], r"points\[0\]"),
    ]:
        with pytest.raises(ContractError, match=match):
            validate_profile("x", {**_base(), "lvrt_curve": {**LVRT, "points": points}})


def test_ride_through_judges_the_event_sample():
    v = np.where(T < 0.2, 1.0, curve_at(LVRT["points"], T - 0.2))
    v = np.where(T == 0.2, 0.1, v)  # below the 0.2 pu curve only at fault inception
    r = _rows(judge(_frame(T, V=v), _lvrt_case(v1="V"), PROFILE))[
        "voltage_within_curve"
    ]
    assert r["status"] == "fail" and r["at_s"] == 0.2


def test_curve_point_must_be_a_list_not_a_two_key_mapping():
    with pytest.raises(ContractError, match=r"points\[0\]"):
        validate_profile(
            "x", {**_base(), "lvrt_curve": {**LVRT, "points": [{0: 0.0, 1: 0.2}]}}
        )


def test_voltage_outside_from_inception_leaves_connection_unknown():
    v = np.where(
        T < 0.2, 1.0, 0.05
    )  # below the 0.2 pu curve from the first fault sample
    rows = _rows(judge(_full_frame(v=v), _lvrt_case(**FULL), PROFILE))
    assert rows["stays_connected"]["status"] == "unknown"
    assert "outside the curve from fault inception" in rows["stays_connected"]["detail"]


def test_reactive_rows_name_the_voltage_quantity():
    v = np.where((T >= 0.2) & (T < 0.35), 0.5, 1.0)
    for iq in (0 * T, np.where((T >= 0.21) & (T < 0.35), 0.8, 0.0)):
        r = _rows(_iq_case(T, v, iq))["reactive_current_rise"]
        assert r["quantity"] == "positive-sequence voltage at the PoC"
    r = _rows(judge(_full_frame(v=0.95 + 0 * T), _lvrt_case(**FULL), PROFILE))[
        "reactive_current_rise"
    ]
    assert (
        r["quantity"] == "positive-sequence voltage at the PoC"
        and r["status"] == "unknown"
    )
    r = _rows(judge(_full_frame(), _lvrt_case(v1="V"), PROFILE))[
        "reactive_current_rise"
    ]
    assert (
        r["quantity"] == "positive-sequence voltage at the PoC"
        and "role 'iq'" in r["detail"]
    )


def test_pre_fault_power_not_above_zero_is_unknown():
    r = _p_case(np.where(T < 0.2, 0.0, P_REC))["active_power_recovery"]
    assert r["status"] == "unknown" and "not above 0" in r["detail"]
    assert "mean over 0.1 s" in _p_case(P_REC)["active_power_recovery"]["detail"]


def test_subnormal_power_change_gives_no_infinite_droop():
    f = np.where(TS < 0.2, 50.0, 50.5)
    p = np.where(TS < 0.2, 0.0, -1e-310)  # a subnormal change: df / dp overflows
    case = {
        "id": "d",
        "kind": "frequency_step",
        "t_event": 0.2,
        "channels": {"f": "F", "p": "P"},
    }
    r = _rows(judge(_frame(TS, F=f, P=p), case, {"frequency_droop": DROOP}))["droop"]
    assert (
        r["status"] == "fail"
        and r["measured"] is None
        and "finite droop" in r["detail"]
    )


def test_curve_must_start_at_inception():
    with pytest.raises(ContractError, match="must start at t = 0 s"):
        validate_profile(
            "x", {**_base(), "lvrt_curve": {**LVRT, "points": [[0.1, 0.2], [0.2, 0.5]]}}
        )
