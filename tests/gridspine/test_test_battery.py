"""
GC-04 part 1: the tool-neutral DMAT / compliance test battery (TRGC 04/05).

The expected case ids below are listed by hand, not derived from the builder,
so a builder that drops or renames a case fails here. Each refusal test
asserts its own guard's reason, so a mutant of one guard is killed by its own
test and not by a neighbour's.
"""

import cmath
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
    cases_from_frame,
    cases_from_json,
    fault_impedance_ohm,
    thevenin_impedances_ohm,
    validate_battery,
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
