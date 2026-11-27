"""
GC-04 part 1: the tool-neutral DMAT / compliance test battery (TRGC 04/05).

The expected case ids below are listed by hand, not derived from the builder,
so a builder that drops or renames a case fails here. Each refusal test
asserts its own guard's reason, so a mutant of one guard is killed by its own
test and not by a neighbour's.
"""

import cmath
import dataclasses
import json
import math

import pytest

from gridspine.handoff.test_battery import (
    CHANNELS,
    CONTRACT_VERSION,
    KIND_PARAMS,
    Case,
    Thevenin,
    battery_frame,
    battery_json,
    build_battery,
    c_min,
    cases_from_frame,
    cases_from_json,
    fault_impedance_ohm,
    thevenin_impedances_ohm,
    validate_battery,
    voltage_factor,
)
from gridspine.schema.contracts import ContractError

BAD_NUMBERS = [math.inf, -math.inf, math.nan]
GRID = Thevenin("g", 1000.0, 5.0, 2.0, 5.0)
Z0 = {"z0_over_z1": 1.5, "x0_over_r0": 4.0}


def _bb(*args, **kw):
    return build_battery(*args, **{**Z0, **kw})


def _case(**kw):
    base = dict(
        case_id="c",
        kind="fault_3ph",
        trgc="04/05",
        grid=GRID,
        t_event_s=1.0,
        t_end_s=10.0,
        channels=("v_poc_pu",),
        params={"v_retained_pu": 0.2, "duration_s": 0.15},
    )
    base.update(kw)
    return Case(**base)


@pytest.fixture(scope="module")
def battery():
    return _bb(1000, 3000, 3, 10)


EXPECTED_IDS = {
    "flat_skmin_xrmin",
    "flat_skmin_xrmax",
    "flat_skmax_xrmin",
    "flat_skmax_xrmax",
    "fault_3ph_skmin_xrmin_v000",
    "fault_3ph_skmin_xrmin_v020",
    "fault_3ph_skmin_xrmin_v050",
    "fault_3ph_skmin_xrmax_v000",
    "fault_3ph_skmin_xrmax_v020",
    "fault_3ph_skmin_xrmax_v050",
    "fault_3ph_skmax_xrmin_v000",
    "fault_3ph_skmax_xrmin_v020",
    "fault_3ph_skmax_xrmin_v050",
    "fault_3ph_skmax_xrmax_v000",
    "fault_3ph_skmax_xrmax_v020",
    "fault_3ph_skmax_xrmax_v050",
    "fault_1ph_skmin_xrmin_v000",
    "fault_1ph_skmin_xrmin_v020",
    "fault_1ph_skmin_xrmin_v050",
    "fault_1ph_skmin_xrmax_v000",
    "fault_1ph_skmin_xrmax_v020",
    "fault_1ph_skmin_xrmax_v050",
    "fault_1ph_skmax_xrmin_v000",
    "fault_1ph_skmax_xrmin_v020",
    "fault_1ph_skmax_xrmin_v050",
    "fault_1ph_skmax_xrmax_v000",
    "fault_1ph_skmax_xrmax_v020",
    "fault_1ph_skmax_xrmax_v050",
    "v_step_skmin_xrmin_up",
    "v_step_skmin_xrmin_down",
    "v_step_skmin_xrmax_up",
    "v_step_skmin_xrmax_down",
    "v_step_skmax_xrmin_up",
    "v_step_skmax_xrmin_down",
    "v_step_skmax_xrmax_up",
    "v_step_skmax_xrmax_down",
    "q_step_skmin_xrmin_up",
    "q_step_skmin_xrmin_down",
    "q_step_skmin_xrmax_up",
    "q_step_skmin_xrmax_down",
    "q_step_skmax_xrmin_up",
    "q_step_skmax_xrmin_down",
    "q_step_skmax_xrmax_up",
    "q_step_skmax_xrmax_down",
    "p_step_skmin_xrmin_up",
    "p_step_skmin_xrmin_down",
    "p_step_skmin_xrmax_up",
    "p_step_skmin_xrmax_down",
    "p_step_skmax_xrmin_up",
    "p_step_skmax_xrmin_down",
    "p_step_skmax_xrmax_up",
    "p_step_skmax_xrmax_down",
    "f_step_skmax_xrmax_up",
    "f_step_skmax_xrmax_down",
    "f_ramp_skmax_xrmax_up",
    "f_ramp_skmax_xrmax_down",
    "phase_jump_skmin_xrmin_up",
    "phase_jump_skmin_xrmin_down",
}


# --- the built battery -------------------------------------------------------


def test_pinned_ids_and_count(battery):
    ids = [c.case_id for c in battery]
    assert len(ids) == 58
    assert set(ids) == EXPECTED_IDS


def test_every_case_well_formed(battery):
    for c in battery:
        assert c.channels and all(ch in CHANNELS for ch in c.channels)
        assert set(c.params) == set(KIND_PARAMS[c.kind])
        assert all(math.isfinite(v) for v in c.params.values())
        assert math.isfinite(c.grid.sk_mva) and math.isfinite(c.grid.x_over_r)
        assert c.source == "assumed" and c.trgc == "04/05"


def test_grids_carry_the_inputs(battery):
    by_id = {c.case_id: c for c in battery}
    assert (
        by_id["flat_skmin_xrmax"].grid.sk_mva,
        by_id["flat_skmin_xrmax"].grid.x_over_r,
    ) == (1000.0, 10.0)
    assert (
        by_id["flat_skmax_xrmin"].grid.sk_mva,
        by_id["flat_skmax_xrmin"].grid.x_over_r,
    ) == (3000.0, 3.0)
    f = by_id["fault_1ph_skmin_xrmin_v020"]
    assert (f.grid.sk_mva, f.grid.x_over_r, f.params["v_retained_pu"]) == (
        1000.0,
        3.0,
        0.2,
    )


def test_fault_cases_name_current_channels(battery):
    f = next(c for c in battery if c.kind == "fault_3ph")
    assert {"iq_pu", "id_pu", "v_poc_pu"} <= set(f.channels)


def test_channel_contract_states_unit_base_sign_point():
    for name, ch in CHANNELS.items():
        assert ch.unit and ch.base and ch.sign and ch.point, name
    assert "rated current" in CHANNELS["iq_pu"].base
    assert "injection" in CHANNELS["q_poc_mvar"].sign


# --- overrides ---------------------------------------------------------------


def test_override_marks_supplied_and_applies():
    b = _bb(
        1000,
        3000,
        3,
        10,
        overrides={"fault_3ph_skmin_xrmin_v000": {"duration_s": 0.2, "t_end_s": 5.0}},
    )
    c = next(c for c in b if c.case_id == "fault_3ph_skmin_xrmin_v000")
    assert (c.params["duration_s"], c.t_end_s, c.source) == (0.2, 5.0, "supplied")
    assert all(o.source == "assumed" for o in b if o is not c)


def test_override_unknown_case_refused():
    with pytest.raises(ContractError, match="unknown case id"):
        _bb(1000, 3000, 3, 10, overrides={"nope": {"t_end_s": 5.0}})


def test_override_unknown_name_refused():
    with pytest.raises(ContractError, match="case takes no"):
        _bb(1000, 3000, 3, 10, overrides={"flat_skmin_xrmin": {"duration_s": 0.2}})


# --- builder input guards ----------------------------------------------------


def test_sk_min_above_max_refused():
    with pytest.raises(ContractError, match="exceeds sk_max_mva"):
        _bb(3000.5, 3000, 3, 10)


def test_sk_min_equal_max_allowed():
    assert _bb(3000, 3000, 3, 10)


def test_xr_min_above_max_refused():
    with pytest.raises(ContractError, match="exceeds xr_max"):
        _bb(1000, 3000, 10.5, 10)


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize("pos", range(4))
def test_builder_nonfinite_refused(bad, pos):
    args = [1000, 3000, 3, 10]
    args[pos] = bad
    with pytest.raises(ContractError, match="not a finite number"):
        _bb(*args)


# --- Thevenin guards ---------------------------------------------------------


def test_sk_zero_refused():
    with pytest.raises(ContractError, match="sk_mva=0.0 must be > 0"):
        Thevenin("g", 0, 5.0)


def test_xr_zero_refused():
    with pytest.raises(ContractError, match="x_over_r=0.0 must be > 0"):
        Thevenin("g", 1000, 0)


def test_sk_negative_refused_by_builder():
    with pytest.raises(ContractError, match="sk_mva=-1.0 must be > 0"):
        _bb(-1, 3000, 3, 10)


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize("name", ["sk_mva", "x_over_r"])
def test_thevenin_nonfinite_refused(bad, name):
    kw = {"label": "g", "sk_mva": 1000.0, "x_over_r": 5.0, name: bad}
    with pytest.raises(ContractError, match=f"{name}=.* is not a finite number"):
        Thevenin(**kw)


def test_thevenin_bool_refused():
    with pytest.raises(ContractError, match="not a finite number"):
        Thevenin("g", True, 5.0)


# --- Case guards -------------------------------------------------------------


def test_minimal_case_ok():
    assert _case().params == {"duration_s": 0.15, "v_retained_pu": 0.2}


def test_duration_zero_refused():
    # duration > 0 is required, so 0 itself is refused (guard is `<= 0`)
    with pytest.raises(ContractError, match="duration_s=0.0 must be > 0"):
        _case(params={"v_retained_pu": 0.2, "duration_s": 0.0})


def test_duration_negative_refused():
    with pytest.raises(ContractError, match="duration_s=-0.1 must be > 0"):
        _case(params={"v_retained_pu": 0.2, "duration_s": -0.1})


def test_event_at_end_refused():
    with pytest.raises(ContractError, match="is not before t_end_s"):
        _case(kind="flat", params={}, t_event_s=10.0, t_end_s=10.0)


def test_event_negative_refused():
    with pytest.raises(ContractError, match="t_event_s=-0.5 must be >= 0"):
        _case(kind="flat", params={}, t_event_s=-0.5)


def test_event_at_zero_allowed():
    assert _case(kind="flat", params={}, t_event_s=0.0).t_event_s == 0.0


def test_fault_clearing_at_end_refused():
    with pytest.raises(ContractError, match="not before t_end_s=10.0"):
        _case(params={"v_retained_pu": 0.2, "duration_s": 9.0})


def test_ramp_ending_after_end_refused():
    with pytest.raises(ContractError, match="event ends at"):
        _case(kind="f_ramp", params={"ramp_hz_per_s": 0.5, "duration_s": 9.5})


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize("name", ["t_event_s", "t_end_s"])
def test_time_nonfinite_refused(bad, name):
    with pytest.raises(ContractError, match=f"{name}=.* is not a finite number"):
        _case(**{name: bad})


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize(
    "kind,param", [(k, p) for k, ps in KIND_PARAMS.items() for p in ps]
)
def test_param_nonfinite_refused(bad, kind, param):
    params = {
        "v_retained_pu": 0.2,
        "duration_s": 0.15,
        "step_pu": 0.1,
        "step_hz": 0.5,
        "ramp_hz_per_s": 0.5,
        "angle_deg": 20.0,
    }
    params = {p: params[p] for p in KIND_PARAMS[kind]}
    params[param] = bad
    with pytest.raises(ContractError, match=f"{param}=.* is not a finite number"):
        _case(kind=kind, params=params)


def test_unknown_channel_refused():
    with pytest.raises(ContractError, match="unknown channel"):
        _case(channels=("v_poc_pu", "volts"))


def test_empty_channels_refused():
    with pytest.raises(ContractError, match="names no output channels"):
        _case(channels=())


def test_repeated_channel_refused():
    with pytest.raises(ContractError, match="repeats a channel"):
        _case(channels=("v_poc_pu", "v_poc_pu"))


def test_missing_kind_param_refused():
    with pytest.raises(ContractError, match=r"needs parameter\(s\) \['duration_s'\]"):
        _case(params={"v_retained_pu": 0.2})


def test_extra_param_refused():
    with pytest.raises(ContractError, match="takes no parameter"):
        _case(kind="flat", params={"step_pu": 0.1})


def test_unknown_kind_refused():
    with pytest.raises(ContractError, match="unknown kind"):
        _case(kind="islanding", params={})


@pytest.mark.parametrize("v", [1.0, -0.01])
def test_v_retained_out_of_range_refused(v):
    with pytest.raises(ContractError, match=r"v_retained_pu=.* must be in \[0, 1\)"):
        _case(params={"v_retained_pu": v, "duration_s": 0.15})


def test_v_retained_zero_allowed():
    assert (
        _case(params={"v_retained_pu": 0.0, "duration_s": 0.15}).params["v_retained_pu"]
        == 0.0
    )


@pytest.mark.parametrize(
    "kind,param",
    [
        ("v_step", "step_pu"),
        ("q_step", "step_pu"),
        ("p_step", "step_pu"),
        ("f_step", "step_hz"),
    ],
)
def test_zero_step_refused(kind, param):
    with pytest.raises(ContractError, match=f"{param} must be nonzero"):
        _case(kind=kind, params={param: 0.0})


def test_zero_ramp_refused():
    with pytest.raises(ContractError, match="ramp_hz_per_s must be nonzero"):
        _case(kind="f_ramp", params={"ramp_hz_per_s": 0.0, "duration_s": 1.0})


@pytest.mark.parametrize("a", [180.0, -180.0, 0.0])
def test_phase_jump_bounds_refused(a):
    with pytest.raises(ContractError, match=r"0 < \|angle\| < 180"):
        _case(kind="phase_jump", params={"angle_deg": a})


def test_phase_jump_inside_bounds_allowed():
    assert (
        _case(kind="phase_jump", params={"angle_deg": -179.9}).params["angle_deg"]
        == -179.9
    )


def test_bad_source_refused():
    with pytest.raises(ContractError, match="source"):
        _case(source="guessed")


def test_duplicate_id_refused():
    with pytest.raises(ContractError, match="duplicate case id 'c'"):
        validate_battery([_case(), _case()])


def test_empty_battery_refused():
    with pytest.raises(ContractError, match="empty"):
        validate_battery([])


# --- serialisation -----------------------------------------------------------


def _edited():
    return _bb(
        800.5,
        2500,
        2.5,
        12,
        overrides={
            "fault_1ph_skmax_xrmax_v050": {
                "v_retained_pu": 0.35,
                "duration_s": 0.25,
                "t_event_s": 0.5,
            },
            "phase_jump_skmin_xrmin_up": {"angle_deg": 1 / 3},
        },
    )


@pytest.mark.parametrize("make", [lambda: _bb(1000, 3000, 3, 10), _edited])
def test_frame_round_trip_field_by_field(make):
    cases = make()
    back = cases_from_frame(battery_frame(cases))
    assert len(back) == len(cases)
    for a, b in zip(cases, back):
        assert (
            a == b
        )  # dataclass eq compares every field, params dict and grid included
        assert (
            a.params == b.params and a.channels == b.channels and a.source == b.source
        )


@pytest.mark.parametrize("make", [lambda: _bb(1000, 3000, 3, 10), _edited])
def test_json_round_trip_field_by_field(make):
    cases = make()
    back = cases_from_json(battery_json(cases))
    assert len(back) == len(cases)
    for a, b in zip(cases, back):
        assert a == b


def test_json_is_deterministic():
    a = battery_json(_bb(1000, 3000, 3, 10)).encode()
    b = battery_json(_bb(1000.0, 3000.0, 3.0, 10.0)).encode()
    assert a == b
    assert f'"contract_version": "{CONTRACT_VERSION}"' in a.decode()


def test_json_wrong_version_refused():
    text = battery_json(_bb(1000, 3000, 3, 10)).replace(
        f'"contract_version": "{CONTRACT_VERSION}"', '"contract_version": "0"'
    )
    with pytest.raises(ContractError, match="contract_version '0'"):
        cases_from_json(text)


def test_json_changed_contract_refused():
    text = battery_json(_bb(1000, 3000, 3, 10)).replace('"Hz"', '"kHz"', 1)
    with pytest.raises(ContractError, match="channel contract differs"):
        cases_from_json(text)


def test_json_malformed_case_refused():
    with pytest.raises(ContractError, match="malformed"):
        import json

        doc = json.loads(battery_json(_bb(1000, 3000, 3, 10)))
        del doc["cases"][0]["grid"]
        cases_from_json(json.dumps(doc))


def test_frame_missing_column_refused():
    with pytest.raises(ContractError, match="lacks column"):
        cases_from_frame(battery_frame(_bb(1000, 3000, 3, 10)).drop(columns=["source"]))


# --- identity and type guards; pinned default values -------------------------


def test_xr_min_equal_max_allowed():
    assert _bb(1000, 3000, 10, 10)


@pytest.mark.parametrize("label", ["Weak", "", 7])
def test_thevenin_bad_label_refused(label):
    with pytest.raises(ContractError, match="thevenin label .*not a lower-case id"):
        Thevenin(label, 1000.0, 5.0)


@pytest.mark.parametrize("cid", ["Fault-1", "", None])
def test_bad_case_id_refused(cid):
    with pytest.raises(ContractError, match="case id .*not a lower-case id"):
        _case(case_id=cid)


@pytest.mark.parametrize("trgc", ["", None, 5])
def test_empty_trgc_refused(trgc):
    with pytest.raises(ContractError, match="trgc reference is empty"):
        _case(trgc=trgc)


def test_grid_not_thevenin_refused():
    with pytest.raises(ContractError, match="grid is not a Thevenin source"):
        _case(grid=(1000.0, 5.0))


def test_battery_holding_non_case_refused():
    with pytest.raises(ContractError, match="holds a dict, not a Case"):
        validate_battery([_case(), {"case_id": "d"}])


def test_json_unparseable_refused():
    with pytest.raises(ContractError, match="does not parse"):
        cases_from_json("{not json")


def test_json_not_object_refused():
    with pytest.raises(ContractError, match="is not an object"):
        cases_from_json("[]")


# Hand-listed default event values: sizes and signs of every step, ramp and jump.
EXPECTED_PARAMS = {
    "v_step_skmin_xrmin_up": {"step_pu": 0.05},
    "v_step_skmax_xrmax_down": {"step_pu": -0.05},
    "q_step_skmin_xrmin_down": {"step_pu": -0.2},
    "p_step_skmax_xrmax_up": {"step_pu": 0.2},
    "f_step_skmax_xrmax_up": {"step_hz": 0.5},
    "f_step_skmax_xrmax_down": {"step_hz": -0.5},
    "f_ramp_skmax_xrmax_up": {"ramp_hz_per_s": 0.5, "duration_s": 2.0},
    "f_ramp_skmax_xrmax_down": {"ramp_hz_per_s": -0.5, "duration_s": 2.0},
    "phase_jump_skmin_xrmin_up": {"angle_deg": 20.0},
    "phase_jump_skmin_xrmin_down": {"angle_deg": -20.0},
    "fault_3ph_skmax_xrmax_v050": {"v_retained_pu": 0.5, "duration_s": 0.15},
}


def test_pinned_default_params(battery):
    by_id = {c.case_id: c for c in battery}
    for cid, params in EXPECTED_PARAMS.items():
        assert by_id[cid].params == params, cid


def test_numeric_string_refused():
    with pytest.raises(ContractError, match="sk_mva='1000' is not a finite number"):
        Thevenin("g", "1000", 5.0)


def test_params_read_only(battery):
    with pytest.raises(TypeError):
        battery[0].params["step_pu"] = 1.0


# Hand-listed channels per kind, in contract order.
_BASE = ("v_poc_pu", "f_hz", "p_poc_mw", "q_poc_mvar")
_CUR = ("id_pu", "iq_pu", "i_poc_pu")
EXPECTED_CHANNELS = {
    "flat": _BASE,
    "fault_3ph": _BASE + _CUR,
    "fault_1ph": _BASE + _CUR,
    "v_step": _BASE + ("iq_pu",),
    "q_step": _BASE + ("iq_pu",),
    "p_step": _BASE + ("id_pu",),
    "f_step": _BASE,
    "f_ramp": _BASE,
    "phase_jump": ("v_poc_pu", "theta_poc_deg", "f_hz", "p_poc_mw", "q_poc_mvar")
    + _CUR,
}


def test_pinned_channels_per_kind(battery):
    for c in battery:
        assert c.channels == EXPECTED_CHANNELS[c.kind], c.case_id


def test_json_golden_case_text():
    text = battery_json(_bb(1000, 3000, 3, 10))
    golden = """  {
   "case_id": "phase_jump_skmin_xrmin_down",
   "channels": [
    "v_poc_pu",
    "theta_poc_deg",
    "f_hz",
    "p_poc_mw",
    "q_poc_mvar",
    "id_pu",
    "iq_pu",
    "i_poc_pu"
   ],
   "grid": {
    "c_kind": "c_max",
    "label": "skmin_xrmin",
    "sk_mva": 1000.0,
    "x0_over_r0": 4.0,
    "x_over_r": 3.0,
    "z0_over_z1": 1.5
   },
   "kind": "phase_jump",
   "params": {
    "angle_deg": -20.0
   },
   "source": "assumed",
   "t_end_s": 10.0,
   "t_event_s": 1.0,
   "trgc": "04/05"
  }
 ]"""
    assert golden in text


# --- GK A2 review of 619e1d53: zero sequence, fault impedance, bool probes ----


def test_fault_1ph_without_zero_sequence_refused():
    with pytest.raises(
        ContractError, match="single-phase fault needs the source's zero sequence"
    ):
        _case(kind="fault_1ph", grid=Thevenin("g", 1000.0, 5.0))


def test_fault_3ph_without_zero_sequence_allowed():
    assert _case(grid=Thevenin("g", 1000.0, 5.0)).grid.z0_over_z1 is None


def test_half_zero_sequence_refused():
    with pytest.raises(ContractError, match="zero sequence needs both"):
        Thevenin("g", 1000.0, 5.0, z0_over_z1=1.0)


@pytest.mark.parametrize("name", ["z0_over_z1", "x0_over_r0"])
def test_zero_sequence_zero_refused(name):
    kw = {"z0_over_z1": 1.0, "x0_over_r0": 3.0, name: 0}
    with pytest.raises(ContractError, match=f"{name}=0.0 must be > 0"):
        Thevenin("g", 1000.0, 5.0, **kw)


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize("name", ["z0_over_z1", "x0_over_r0"])
def test_zero_sequence_nonfinite_refused(bad, name):
    kw = {"z0_over_z1": 1.0, "x0_over_r0": 3.0, name: bad}
    with pytest.raises(ContractError, match=f"{name}=.* is not a finite number"):
        Thevenin("g", 1000.0, 5.0, **kw)


@pytest.mark.parametrize("name", ["z0_over_z1", "x0_over_r0"])
def test_builder_requires_zero_sequence(name):
    kw = dict(Z0)
    del kw[name]
    with pytest.raises(TypeError, match=name):
        build_battery(1000, 3000, 3, 10, **kw)


def test_builder_zero_sequence_nonfinite_refused():
    with pytest.raises(ContractError, match="z0_over_z1=nan is not a finite number"):
        build_battery(1000, 3000, 3, 10, z0_over_z1=math.nan, x0_over_r0=4.0)


def test_built_grids_carry_zero_sequence(battery):
    assert {(c.grid.z0_over_z1, c.grid.x0_over_r0) for c in battery} == {(1.5, 4.0)}


# Hand values: 110 kV, Sk 1000 MVA, X/R 10 -> |Z1| = 12.1 ohm,
# R1 = 12.1/sqrt(101) = 1.2039950, X1 = 10 R1 = 12.039950.
# Z0 = 2 |Z1| at X0/R0 = 5 -> R0 = 24.2/sqrt(26) = 4.7460105, X0 = 23.730052.
_HAND = Thevenin("g", 1000.0, 10.0, 2.0, 5.0)


def test_thevenin_impedances_hand_value():
    z1, z0 = thevenin_impedances_ohm(_HAND, 110.0)
    assert z1 == pytest.approx(complex(1.2039950, 12.039950), rel=1e-7)
    assert z0 == pytest.approx(complex(4.7460105, 23.730052), rel=1e-7)


def test_fault_3ph_impedance_hand_value():
    # v = 0.2: Z_f = Z1 * 0.2 / 0.8 = Z1 / 4
    c = _case(grid=_HAND, params={"v_retained_pu": 0.2, "duration_s": 0.15})
    assert fault_impedance_ohm(c, 110.0) == pytest.approx(
        complex(0.30099875, 3.0099875), rel=1e-6
    )


def test_fault_1ph_impedance_hand_value():
    # v = 0.2: Z_f = 0.2 (2 Z1 + Z0) / 2.4 = (2 Z1 + Z0) / 12
    # 2 Z1 + Z0 = 7.1540005 + j 47.809952 -> Z_f = 0.5961667 + j 3.9841627
    c = _case(
        kind="fault_1ph", grid=_HAND, params={"v_retained_pu": 0.2, "duration_s": 0.15}
    )
    assert fault_impedance_ohm(c, 110.0) == pytest.approx(
        complex(0.5961667, 3.9841627), rel=1e-6
    )


@pytest.mark.parametrize("kind", ["fault_3ph", "fault_1ph"])
@pytest.mark.parametrize("v", [0.0, 0.2, 0.5, 0.9])
def test_fault_impedance_retains_v_with_plant_disconnected(kind, v):
    c = _case(kind=kind, grid=_HAND, params={"v_retained_pu": v, "duration_s": 0.15})
    zf = fault_impedance_ohm(c, 110.0)
    z1, z0 = thevenin_impedances_ohm(_HAND, 110.0)
    retained = (
        zf / (z1 + zf) if kind == "fault_3ph" else 3 * zf / (2 * z1 + z0 + 3 * zf)
    )
    assert abs(retained) == pytest.approx(v, abs=1e-12)
    assert abs(cmath.phase(retained)) < 1e-9 or v == 0.0


def test_fault_impedance_of_non_fault_refused():
    with pytest.raises(ContractError, match="is not a fault"):
        fault_impedance_ohm(_case(kind="flat", params={}), 110.0)


@pytest.mark.parametrize("vn", [0.0, -110.0, math.nan, True])
def test_fault_impedance_bad_voltage_refused(vn):
    with pytest.raises(ContractError, match="vn_kv=.* must be a finite number > 0"):
        fault_impedance_ohm(_case(), vn)


def test_json_params_not_mapping_refused():
    import json

    doc = json.loads(battery_json(_bb(1000, 3000, 3, 10)))
    doc["cases"][0]["params"] = [["x"]]
    with pytest.raises(ContractError, match="params must be a mapping"):
        cases_from_json(json.dumps(doc))


def test_json_channels_string_refused():
    import json

    doc = json.loads(battery_json(_bb(1000, 3000, 3, 10)))
    doc["cases"][0]["channels"] = "v_poc_pu"
    with pytest.raises(ContractError, match="channels must be a list"):
        cases_from_json(json.dumps(doc))


@pytest.mark.parametrize("name", ["t_event_s", "t_end_s"])
def test_bool_time_refused(name):
    with pytest.raises(ContractError, match=f"{name}=True is not a finite number"):
        _case(kind="flat", params={}, **{name: True})


def test_bool_param_refused():
    with pytest.raises(ContractError, match="duration_s=True is not a finite number"):
        _case(params={"v_retained_pu": 0.2, "duration_s": True})


@pytest.mark.parametrize("chans", [{"v_poc_pu"}, None])
def test_channels_not_a_list_refused(chans):
    with pytest.raises(ContractError, match="channels must be a list"):
        _case(channels=chans)


def test_thevenin_impedances_without_zero_sequence():
    z1, z0 = thevenin_impedances_ohm(Thevenin("g", 1000.0, 10.0), 110.0)
    assert z0 is None
    assert z1 == pytest.approx(complex(1.2039950, 12.039950), rel=1e-7)


# --- GC-14: IEC 60909 voltage factor c, conservative bracket ----------------
# Expectations are hand-written literals (GK A2 T1), never the module's own constants.


def test_bracket_names_c_max_on_sk_min_and_c_min_on_sk_max():
    by_level = {}
    for c in _bb(1000, 3000, 3, 10):
        by_level.setdefault(c.grid.label.split("_")[0], set()).add(c.grid.c_kind)
    assert by_level == {"skmin": {"c_max"}, "skmax": {"c_min"}}


def test_hv_ends_of_the_bracket_by_hand():
    cases = {c.case_id: c for c in _bb(1000, 3000, 3, 10)}
    weak = cases["fault_3ph_skmin_xrmin_v000"].grid
    strong = cases["fault_3ph_skmax_xrmax_v000"].grid
    assert (voltage_factor(weak, 110.0), voltage_factor(strong, 110.0)) == (1.1, 1.0)
    # Sk-min, c_max: |Z1| = 1.1 * 110^2 / 1000 = 13.31 ohm; Z0 = 1.5 |Z1| = 19.965 ohm
    z1, z0 = thevenin_impedances_ohm(weak, 110.0)
    assert abs(z1) == pytest.approx(13.31, rel=1e-12)
    assert abs(z0) == pytest.approx(19.965, rel=1e-12)
    # Sk-max, c_min 1.0 at 110 kV: |Z1| = 110^2 / 3000 = 4.0333... ohm
    assert abs(thevenin_impedances_ohm(strong, 110.0)[0]) == pytest.approx(
        12100 / 3000, rel=1e-12
    )


def test_fault_impedance_follows_the_corrected_source_by_hand():
    cases = {c.case_id: c for c in _bb(1000, 3000, 3, 10)}
    f = next(
        c
        for cid, c in cases.items()
        if cid.startswith("fault_3ph_skmin_xrmin_v") and c.params["v_retained_pu"] > 0
    )
    v = f.params["v_retained_pu"]
    assert abs(fault_impedance_ohm(f, 110.0)) == pytest.approx(
        13.31 * v / (1 - v), rel=1e-12
    )


def test_lv_end_takes_c_min_095_from_the_source_un():
    # 0.4 kV PoC, Sk 5 / 10 MVA: Sk-max |Z1| = 0.95 * 0.16 / 10 = 0.0152 ohm,
    # Sk-min |Z1| = 1.1 * 0.16 / 5 = 0.0352 ohm.
    cases = {c.case_id: c for c in _bb(5, 10, 3, 10)}
    strong = cases["fault_3ph_skmax_xrmax_v000"].grid
    weak = cases["fault_3ph_skmin_xrmin_v000"].grid
    assert (voltage_factor(strong, 0.4), voltage_factor(weak, 0.4)) == (0.95, 1.1)
    assert abs(thevenin_impedances_ohm(strong, 0.4)[0]) == pytest.approx(
        0.0152, rel=1e-12
    )
    assert abs(thevenin_impedances_ohm(weak, 0.4)[0]) == pytest.approx(
        0.0352, rel=1e-12
    )
    assert voltage_factor(strong, 1.0) == 0.95  # "at or below 1 kV"
    assert voltage_factor(Thevenin("g", 10.0, 3.0, c_kind="unity"), 0.4) == 1.0


@pytest.mark.parametrize("vn", [0.0, -1.0, math.nan, math.inf, True])
def test_c_min_refuses_a_bad_un(vn):
    with pytest.raises(ContractError, match="vn_kv"):
        c_min(vn)


def test_c_kind_none_is_no_c_and_is_never_relabelled():
    # A source without c (a battery written before GC-14) keeps |Z1| = Un^2 / Sk = 12.1 ohm.
    g = Thevenin("g", 1000.0, 10.0)
    assert voltage_factor(g, 110.0) is None
    assert abs(thevenin_impedances_ohm(g, 110.0)[0]) == pytest.approx(12.1)
    old = [
        dataclasses.replace(c, grid=dataclasses.replace(c.grid, c_kind=None))
        for c in _bb(1000, 3000, 3, 10)
    ]
    text = battery_json(old)
    assert '"c_kind"' not in text
    assert {c.grid.c_kind for c in cases_from_json(text)} == {None}
    frame = battery_frame(old).drop(
        columns=["c_kind"]
    )  # a frame from before the column
    assert {c.grid.c_kind for c in cases_from_frame(frame)} == {None}


def test_frame_and_json_name_each_cases_c():
    cases = _bb(1000, 3000, 3, 10)
    want = ["c_max" if "_skmin_" in c.case_id else "c_min" for c in cases]
    assert list(battery_frame(cases)["c_kind"]) == want
    doc = json.loads(battery_json(cases))
    assert [c["grid"]["c_kind"] for c in doc["cases"]] == want


@pytest.mark.parametrize("bad", [True, math.nan, 1.1, "c_mid", "C_MAX", ""])
def test_c_kind_not_a_known_name_refused(bad):
    with pytest.raises(ContractError, match="thevenin g: c_kind="):
        Thevenin("g", 1000.0, 10.0, c_kind=bad)


@pytest.mark.parametrize("vn", [0.4, 1.0, 110.0])
def test_c_agrees_with_the_short_circuit_engines_c_factor(vn):
    # GC-14 F3: test_battery keeps its own c table (it imports no engine); it must agree
    # with gridspine.static.island_sc_seq.c_factor, the pandapower-convention table.
    from gridspine.static.island_sc_seq import c_factor

    assert c_min(vn) == c_factor(vn, "min")
    g_max = Thevenin("g", 10.0, 3.0, c_kind="c_max")
    assert voltage_factor(g_max, vn) == c_factor(vn, "max")


def test_exported_c_note_cites_the_standard_not_a_review():
    # The note reaches the TSO through the EMT and PSS/E packs.
    from gridspine.handoff.test_battery import C_BRACKET_NOTE

    assert "IEC 60909-0 Table 1" in C_BRACKET_NOTE
    assert "GK" not in C_BRACKET_NOTE and "GC-02" not in C_BRACKET_NOTE


# --- GC-13: pack ratings ------------------------------------------------------------------------


def test_pack_ratings_round_trip_and_refusals():
    from gridspine.handoff.test_battery import (
        PackRatings,
        ratings_from_json,
        ratings_json,
    )

    good = {
        "rated_mw": 90,
        "rated_mva": 99.5,
        "rating_source": "nameplate",
        "campus_data_hash": "a" * 64,
    }
    r = PackRatings(**good)
    assert ratings_from_json(ratings_json(r)) == r
    assert ratings_json(r) == ratings_json(PackRatings(**good))  # byte-equal
    for name in ("rated_mw", "rated_mva"):
        for bad in (0, -1.0, float("nan"), float("inf"), True, "90", None):
            with pytest.raises(
                ContractError, match=f"{name} must be a finite number > 0"
            ):
                PackRatings(**{**good, name: bad})
    for bad in ("", "  ", None, 3):
        with pytest.raises(ContractError, match="rating_source must be stated"):
            PackRatings(**{**good, "rating_source": bad})
    for bad in ("A" * 64, "a" * 63, "g" * 64, None, 7):
        with pytest.raises(ContractError, match="64 hex digits"):
            PackRatings(**{**good, "campus_data_hash": bad})
    with pytest.raises(ContractError, match="must be a PackRatings"):
        ratings_json(good)
    for text, match in (
        ("{", "does not parse"),
        ("[1]", "must hold exactly"),
        ('{"rated_mw": 1}', "must hold exactly"),
    ):
        with pytest.raises(ContractError, match=match):
            ratings_from_json(text)
