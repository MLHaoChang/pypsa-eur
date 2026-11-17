"""GC-04 part 1: the tool-neutral DMAT / compliance test battery (TRGC 04/05).

The expected case ids below are listed by hand, not derived from the builder,
so a builder that drops or renames a case fails here. Each refusal test
asserts its own guard's reason, so a mutant of one guard is killed by its own
test and not by a neighbour's.
"""
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
    validate_battery,
)
from gridspine.schema.contracts import ContractError

BAD_NUMBERS = [math.inf, -math.inf, math.nan]
GRID = Thevenin("g", 1000.0, 5.0)


def _case(**kw):
    base = dict(case_id="c", kind="fault_3ph", trgc="04/05", grid=GRID, t_event_s=1.0, t_end_s=10.0,
                channels=("v_poc_pu",), params={"v_retained_pu": 0.2, "duration_s": 0.15})
    base.update(kw)
    return Case(**base)


@pytest.fixture(scope="module")
def battery():
    return build_battery(1000, 3000, 3, 10)


EXPECTED_IDS = {
    "flat_skmin_xrmin", "flat_skmin_xrmax", "flat_skmax_xrmin", "flat_skmax_xrmax",
    "fault_3ph_skmin_xrmin_v000", "fault_3ph_skmin_xrmin_v020", "fault_3ph_skmin_xrmin_v050",
    "fault_3ph_skmax_xrmax_v000", "fault_3ph_skmax_xrmax_v020", "fault_3ph_skmax_xrmax_v050",
    "fault_1ph_skmin_xrmin_v000", "fault_1ph_skmin_xrmin_v020", "fault_1ph_skmin_xrmin_v050",
    "fault_1ph_skmax_xrmax_v000", "fault_1ph_skmax_xrmax_v020", "fault_1ph_skmax_xrmax_v050",
    "v_step_skmin_xrmin_up", "v_step_skmin_xrmin_down", "v_step_skmax_xrmax_up", "v_step_skmax_xrmax_down",
    "q_step_skmin_xrmin_up", "q_step_skmin_xrmin_down", "q_step_skmax_xrmax_up", "q_step_skmax_xrmax_down",
    "p_step_skmin_xrmin_up", "p_step_skmin_xrmin_down", "p_step_skmax_xrmax_up", "p_step_skmax_xrmax_down",
    "f_step_skmax_xrmax_up", "f_step_skmax_xrmax_down", "f_ramp_skmax_xrmax_up", "f_ramp_skmax_xrmax_down",
    "phase_jump_skmin_xrmin_up", "phase_jump_skmin_xrmin_down",
}


# --- the built battery -------------------------------------------------------

def test_pinned_ids_and_count(battery):
    ids = [c.case_id for c in battery]
    assert len(ids) == 34
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
    assert (by_id["flat_skmin_xrmax"].grid.sk_mva, by_id["flat_skmin_xrmax"].grid.x_over_r) == (1000.0, 10.0)
    assert (by_id["flat_skmax_xrmin"].grid.sk_mva, by_id["flat_skmax_xrmin"].grid.x_over_r) == (3000.0, 3.0)
    f = by_id["fault_1ph_skmin_xrmin_v020"]
    assert (f.grid.sk_mva, f.grid.x_over_r, f.params["v_retained_pu"]) == (1000.0, 3.0, 0.2)


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
    b = build_battery(1000, 3000, 3, 10, overrides={"fault_3ph_skmin_xrmin_v000": {"duration_s": 0.2,
                                                                                    "t_end_s": 5.0}})
    c = next(c for c in b if c.case_id == "fault_3ph_skmin_xrmin_v000")
    assert (c.params["duration_s"], c.t_end_s, c.source) == (0.2, 5.0, "supplied")
    assert all(o.source == "assumed" for o in b if o is not c)


def test_override_unknown_case_refused():
    with pytest.raises(ContractError, match="unknown case id"):
        build_battery(1000, 3000, 3, 10, overrides={"nope": {"t_end_s": 5.0}})


def test_override_unknown_name_refused():
    with pytest.raises(ContractError, match="case takes no"):
        build_battery(1000, 3000, 3, 10, overrides={"flat_skmin_xrmin": {"duration_s": 0.2}})


# --- builder input guards ----------------------------------------------------

def test_sk_min_above_max_refused():
    with pytest.raises(ContractError, match="exceeds sk_max_mva"):
        build_battery(3000.5, 3000, 3, 10)


def test_sk_min_equal_max_allowed():
    assert build_battery(3000, 3000, 3, 10)


def test_xr_min_above_max_refused():
    with pytest.raises(ContractError, match="exceeds xr_max"):
        build_battery(1000, 3000, 10.5, 10)


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize("pos", range(4))
def test_builder_nonfinite_refused(bad, pos):
    args = [1000, 3000, 3, 10]
    args[pos] = bad
    with pytest.raises(ContractError, match="not a finite number"):
        build_battery(*args)


# --- Thevenin guards ---------------------------------------------------------

def test_sk_zero_refused():
    with pytest.raises(ContractError, match="sk_mva=0.0 must be > 0"):
        Thevenin("g", 0, 5.0)


def test_xr_zero_refused():
    with pytest.raises(ContractError, match="x_over_r=0.0 must be > 0"):
        Thevenin("g", 1000, 0)


def test_sk_negative_refused_by_builder():
    with pytest.raises(ContractError, match="sk_mva=-1.0 must be > 0"):
        build_battery(-1, 3000, 3, 10)


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
@pytest.mark.parametrize("kind,param", [(k, p) for k, ps in KIND_PARAMS.items() for p in ps])
def test_param_nonfinite_refused(bad, kind, param):
    params = {"v_retained_pu": 0.2, "duration_s": 0.15, "step_pu": 0.1, "step_hz": 0.5,
              "ramp_hz_per_s": 0.5, "angle_deg": 20.0}
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
    assert _case(params={"v_retained_pu": 0.0, "duration_s": 0.15}).params["v_retained_pu"] == 0.0


@pytest.mark.parametrize("kind,param", [("v_step", "step_pu"), ("q_step", "step_pu"), ("p_step", "step_pu"),
                                        ("f_step", "step_hz")])
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
    assert _case(kind="phase_jump", params={"angle_deg": -179.9}).params["angle_deg"] == -179.9


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
    return build_battery(800.5, 2500, 2.5, 12, overrides={
        "fault_1ph_skmax_xrmax_v050": {"v_retained_pu": 0.35, "duration_s": 0.25, "t_event_s": 0.5},
        "phase_jump_skmin_xrmin_up": {"angle_deg": 1 / 3}})


@pytest.mark.parametrize("make", [lambda: build_battery(1000, 3000, 3, 10), _edited])
def test_frame_round_trip_field_by_field(make):
    cases = make()
    back = cases_from_frame(battery_frame(cases))
    assert len(back) == len(cases)
    for a, b in zip(cases, back):
        assert a == b  # dataclass eq compares every field, params dict and grid included
        assert a.params == b.params and a.channels == b.channels and a.source == b.source


@pytest.mark.parametrize("make", [lambda: build_battery(1000, 3000, 3, 10), _edited])
def test_json_round_trip_field_by_field(make):
    cases = make()
    back = cases_from_json(battery_json(cases))
    assert len(back) == len(cases)
    for a, b in zip(cases, back):
        assert a == b


def test_json_is_deterministic():
    a = battery_json(build_battery(1000, 3000, 3, 10)).encode()
    b = battery_json(build_battery(1000.0, 3000.0, 3.0, 10.0)).encode()
    assert a == b
    assert f'"contract_version": "{CONTRACT_VERSION}"' in a.decode()


def test_json_wrong_version_refused():
    text = battery_json(build_battery(1000, 3000, 3, 10)).replace(
        f'"contract_version": "{CONTRACT_VERSION}"', '"contract_version": "0"')
    with pytest.raises(ContractError, match="contract_version '0'"):
        cases_from_json(text)


def test_json_changed_contract_refused():
    text = battery_json(build_battery(1000, 3000, 3, 10)).replace('"Hz"', '"kHz"', 1)
    with pytest.raises(ContractError, match="channel contract differs"):
        cases_from_json(text)


def test_json_malformed_case_refused():
    with pytest.raises(ContractError, match="malformed"):
        import json
        doc = json.loads(battery_json(build_battery(1000, 3000, 3, 10)))
        del doc["cases"][0]["grid"]
        cases_from_json(json.dumps(doc))


def test_frame_missing_column_refused():
    with pytest.raises(ContractError, match="lacks column"):
        cases_from_frame(battery_frame(build_battery(1000, 3000, 3, 10)).drop(columns=["source"]))
