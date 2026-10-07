"""Island data schema, I1 (plan 2026-10-07-campus-island-operation).

One schema serves both homes of the island data: the hub sidecar
``island_config.json`` (keyed by PyPSA name) and the ``island`` blocks of a
campus file. Every number is ``{value, source}``, as in the campus file.

The oracle is the plan's field table: each test names the field it pins and
the refusal it expects, so a dropped check or a renamed field fails here.
"""
import copy

import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.island import (
    ALL_FIELDS,
    CONTROLS,
    FIELD_USERS,
    SCENARIOS,
    check_horizon,
    check_island,
    validate_island_config,
    validate_requirements,
    validate_unit_island,
)


def t(value, source="assumed"):
    return {"value": value, "source": source}


def gfm_bess():
    return {"control": "gfm_droop", "pf_rated": t(0.95), "droop_pct": t(4.0), "q_droop_pct": t(4.0),
            "tau_f_s": t(0.1), "i_max_pu": t(1.2), "k_gfm_min": t(1.0)}


def vsm_bess():
    s = gfm_bess()
    del s["tau_f_s"]
    s.update(control="gfm_vsm", H_v_s=t(4.0))
    return s


def pv_qu():
    return {"control": "gfl_qu", "pf_rated": t(0.95),
            "qu_points": t([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])}


def genset():
    return {"governor": "droop", "droop_pct": t(4.0), "q_droop_pct": t(4.0), "H_s": t(1.5),
            "T_g_s": t(0.5), "ramp_pu_per_s": t(0.2), "start_s": t(10.0), "sync_s": t(5.0),
            "load_step_max_pct": t(50.0, "datasheet"), "cos_phi_r": t(0.8), "xd_sat": t(2.0),
            "excitation": "avr_pmg", "neutral_earthing": "solid", "p_min_pu": t(0.3)}


def ups():
    return {"it_load": ["IT"], "walk_in_s": t(15.0), "f_window_hz": t(2.0)}


def limits():
    one = {"rocof_hz_per_s": t(1.0), "f_min_hz": t(47.5), "f_max_hz": t(51.5), "qss_band_hz": t(1.0)}
    return {"transition": copy.deepcopy(one), "steady_island": copy.deepcopy(one)}


def requirements():
    return {"bridge_s": t(600.0), "sustained_h": t(24.0), "scenarios": ["ups_bridged", "seamless"],
            "pickup_blocks": t([10.0, 10.0, 10.0, 10.0]), "frequency_limits": limits(),
            "rocof_window_ms": t(500.0), "gfm_margin": t(0.1)}


def sidecar():
    return {
        "units": {
            "bess_new": {"kind": "bess", **gfm_bess()},
            "pv": {"kind": "pv", **pv_qu()},
            "genset_1": {"kind": "genset", **genset()},
            "genset_2": {"kind": "genset", **genset()},
            "ups_battery": {"kind": "ups", **ups(), "it_load": ["it_load"]},
            "it_load": {"kind": "load", "critical": True},
            "cooling": {"kind": "load", "critical": True, "f_trip_hz": t(47.0, "datasheet")},
            "office": {"kind": "load"},
        },
        "requirements": requirements(),
    }


# ── per-unit blocks ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,spec", [("bess", gfm_bess()), ("bess", vsm_bess()), ("pv", pv_qu()),
                                       ("genset", genset()), ("ups", ups()), ("load", {"critical": True})])
def test_a_complete_block_of_each_kind_is_accepted(kind, spec):
    out = validate_unit_island("u", kind, spec)
    assert out["kind"] == kind


def test_values_and_their_tags_come_back_separately():
    out = validate_unit_island("u", "bess", gfm_bess())
    assert out["values"]["droop_pct"] == 4.0
    assert out["sources"]["droop_pct"] == "assumed"
    assert out["control"] == "gfm_droop"


@pytest.mark.parametrize("control,needs", [
    ("gfl_qu", "qu_points"),
    ("gfm_droop", "tau_f_s"),
    ("gfm_vsm", "H_v_s"),
    ("gfl_fw", "fw_k_mw_per_hz"),
])
def test_each_control_mode_needs_its_own_parameters(control, needs):
    spec = {"control": control, "pf_rated": t(0.95)}
    with pytest.raises(ContractError, match=needs):
        validate_unit_island("u", "bess", spec)


def test_an_unknown_control_is_refused_with_the_allowed_list():
    with pytest.raises(ContractError, match="gfm_droop"):
        validate_unit_island("u", "bess", {"control": "gfm_magic", "pf_rated": t(0.95)})
    assert set(CONTROLS) == {"gfl_pq", "gfl_qu", "gfl_pf", "gfl_fw", "gfm_droop", "gfm_vsm"}


def test_a_field_of_another_control_is_refused():
    spec = gfm_bess()
    spec["H_v_s"] = t(4.0)                      # a VSM field on a droop unit
    with pytest.raises(ContractError, match="H_v_s"):
        validate_unit_island("u", "bess", spec)


def test_a_fixed_power_factor_or_a_curve_but_not_both():
    base = {"control": "gfl_pf", "pf_rated": t(0.95)}
    validate_unit_island("u", "pv", {**base, "cos_phi": t(0.95)})
    validate_unit_island("u", "pv", {**base, "cosphi_p_points": t([[0.0, 1.0], [1.0, -0.9]])})
    with pytest.raises(ContractError, match="exactly one"):
        validate_unit_island("u", "pv", base)
    with pytest.raises(ContractError, match="exactly one"):
        validate_unit_island("u", "pv", {**base, "cos_phi": t(0.95), "cosphi_p_points": t([[0.0, 1.0], [1.0, 0.9]])})


def test_an_untagged_number_is_refused():
    spec = gfm_bess()
    spec["droop_pct"] = 4.0
    with pytest.raises(ContractError, match="droop_pct"):
        validate_unit_island("u", "bess", spec)


@pytest.mark.parametrize("field,value,match", [
    ("droop_pct", 0.0, "droop_pct"),
    ("i_max_pu", 0.9, "i_max_pu"),
    ("k_gfm_min", 1.3, "k_gfm_min"),          # above i_max_pu 1.2
    ("pf_rated", 1.2, "pf_rated"),
    ("tau_f_s", -0.1, "tau_f_s"),
])
def test_converter_physics_checks(field, value, match):
    spec = gfm_bess()
    spec[field] = t(value)
    with pytest.raises(ContractError, match=match):
        validate_unit_island("u", "bess", spec)


def test_a_q_u_curve_must_rise_in_voltage_and_stay_within_one_pu():
    spec = pv_qu()
    spec["qu_points"] = t([[1.1, -0.3], [0.9, 0.3]])
    with pytest.raises(ContractError, match="increasing"):
        validate_unit_island("u", "pv", spec)
    spec["qu_points"] = t([[0.9, 1.5], [1.1, -0.3]])
    with pytest.raises(ContractError, match="qu_points"):
        validate_unit_island("u", "pv", spec)


def test_a_genset_droop_is_needed_only_with_a_droop_governor():
    spec = genset()
    del spec["droop_pct"]
    with pytest.raises(ContractError, match="droop_pct"):
        validate_unit_island("g", "genset", spec)
    spec["governor"] = "isochronous"
    validate_unit_island("g", "genset", spec)


def test_spinning_needs_a_minimum_load_above_zero():
    spec = genset()
    spec["spinning"] = True
    spec["p_min_pu"] = t(0.0)
    with pytest.raises(ContractError, match="p_min_pu"):
        validate_unit_island("g", "genset", spec)
    del spec["p_min_pu"]
    with pytest.raises(ContractError, match="p_min_pu"):
        validate_unit_island("g", "genset", spec)


def test_resistance_earthing_needs_its_resistance_and_only_then():
    spec = genset()
    spec["neutral_earthing"] = "resistance"
    with pytest.raises(ContractError, match="r_n_ohm"):
        validate_unit_island("g", "genset", spec)
    spec["r_n_ohm"] = t(20.0)
    validate_unit_island("g", "genset", spec)
    spec["neutral_earthing"] = "solid"
    with pytest.raises(ContractError, match="r_n_ohm"):
        validate_unit_island("g", "genset", spec)


@pytest.mark.parametrize("field,value", [("governor", "fast"), ("excitation", "magic"),
                                         ("neutral_earthing", "maybe")])
def test_genset_enumerations_are_checked(field, value):
    spec = genset()
    spec[field] = value
    with pytest.raises(ContractError, match=field):
        validate_unit_island("g", "genset", spec)


def test_a_ups_needs_the_loads_it_protects_and_flags_are_booleans():
    spec = ups()
    spec["it_load"] = []
    with pytest.raises(ContractError, match="it_load"):
        validate_unit_island("u", "ups", spec)
    spec = ups()
    spec["transfers_on_islanding"] = "yes"
    with pytest.raises(ContractError, match="transfers_on_islanding"):
        validate_unit_island("u", "ups", spec)
    assert validate_unit_island("u", "ups", ups())["transfers_on_islanding"] is False


def test_sidecar_only_fields_are_refused_in_a_campus_block():
    spec = genset()
    spec["unit_mw"] = t(10.0)
    validate_unit_island("g", "genset", spec, sidecar=True)
    with pytest.raises(ContractError, match="unit_mw"):
        validate_unit_island("g", "genset", spec)


# ── requirements ───────────────────────────────────────────────────────────

def test_requirement_defaults_are_filled_and_tagged_assumed():
    out = validate_requirements(requirements())
    assert out["values"]["genset_redundancy_n"] == 1
    assert out["sources"]["genset_redundancy_n"] == "assumed"
    assert out["gfl_min_case"] == "drop"
    assert out["linearised_uc"] is False
    assert out["values"]["i_max_sensitivity_pu"] == [1.2, 1.5, 2.0]


def test_scenarios_are_a_non_empty_subset_of_the_three():
    assert set(SCENARIOS) == {"ups_bridged", "seamless", "planned"}
    for bad in ([], ["seamless", "magic"], "seamless"):
        r = requirements()
        r["scenarios"] = bad
        with pytest.raises(ContractError, match="scenarios"):
            validate_requirements(r)


def test_pickup_blocks_are_needed_when_ups_bridged_is_studied():
    r = requirements()
    del r["pickup_blocks"]
    with pytest.raises(ContractError, match="pickup_blocks"):
        validate_requirements(r)
    r["scenarios"] = ["seamless"]
    validate_requirements(r)


def test_both_frequency_limit_sets_are_needed_and_ordered():
    r = requirements()
    del r["frequency_limits"]["steady_island"]
    with pytest.raises(ContractError, match="steady_island"):
        validate_requirements(r)
    r = requirements()
    r["frequency_limits"]["transition"]["f_min_hz"] = t(52.0)
    with pytest.raises(ContractError, match="f_min_hz"):
        validate_requirements(r)


def test_redundancy_is_a_whole_number():
    r = requirements()
    r["genset_redundancy_n"] = t(1.5)
    with pytest.raises(ContractError, match="genset_redundancy_n"):
        validate_requirements(r)


def test_sustained_ride_through_cannot_exceed_the_period():
    req = validate_requirements(requirements())
    check_horizon(req, period_hours=168)
    with pytest.raises(ContractError, match="sustained_h"):
        check_horizon(req, period_hours=12)


# ── the whole sidecar and cross checks ─────────────────────────────────────

def test_the_sidecar_validates_and_keeps_pypsa_names():
    cfg = validate_island_config(sidecar())
    assert set(cfg["units"]) == set(sidecar()["units"])
    assert cfg["units"]["it_load"]["critical"] is True
    assert cfg["units"]["office"]["critical"] is False
    assert cfg["requirements"]["values"]["sustained_h"] == 24.0


def test_a_ups_must_protect_loads_that_exist():
    s = sidecar()
    s["units"]["ups_battery"]["it_load"] = ["nowhere"]
    with pytest.raises(ContractError, match="nowhere"):
        validate_island_config(s)


def test_ride_through_storage_must_be_batteries_not_a_ups():
    s = sidecar()
    s["requirements"]["ride_through_storage"] = ["ups_battery"]
    with pytest.raises(ContractError, match="ride_through_storage"):
        validate_island_config(s)


def test_ride_through_storage_defaults_to_every_bess():
    cfg = validate_island_config(sidecar())
    assert cfg["requirements"]["ride_through_storage"] == ["bess_new"]


def test_redundancy_must_leave_at_least_one_genset():
    s = sidecar()
    s["requirements"]["genset_redundancy_n"] = t(2)
    with pytest.raises(ContractError, match="genset_redundancy_n"):
        validate_island_config(s)


def test_pickup_blocks_must_cover_the_peak_critical_load():
    cfg = validate_island_config(sidecar())
    check_island(cfg, critical_peak_mw=40.0)
    with pytest.raises(ContractError, match="pickup_blocks"):
        check_island(cfg, critical_peak_mw=41.0)


def test_an_unknown_kind_or_top_level_key_is_refused():
    s = sidecar()
    s["units"]["pv"]["kind"] = "fusion"
    with pytest.raises(ContractError, match="fusion"):
        validate_island_config(s)
    s = sidecar()
    s["extra"] = {}
    with pytest.raises(ContractError, match="extra"):
        validate_island_config(s)


# ── no orphan fields ───────────────────────────────────────────────────────

def test_every_field_names_the_increment_that_reads_it():
    """FIELD_USERS maps each schema field to the later increments that read
    it (plan I1, "no orphan fields"). A field nobody reads, or a reader that
    is not an increment of the plan, fails here."""
    increments = {"I2a", "I2b", "I2c", "I3a", "I3b", "I4", "I5", "I5b", "I6", "I7", "I8", "I9"}
    assert FIELD_USERS, "the field table is empty"
    assert set(FIELD_USERS) == set(ALL_FIELDS), (
        f"in the schema but not the table: {sorted(set(ALL_FIELDS) - set(FIELD_USERS))}; "
        f"in the table but not the schema: {sorted(set(FIELD_USERS) - set(ALL_FIELDS))}")
    for field, users in FIELD_USERS.items():
        assert users, f"{field} has no reader"
        assert set(users) <= increments, f"{field}: unknown readers {set(users) - increments}"
