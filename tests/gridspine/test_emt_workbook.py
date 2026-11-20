"""
The EMT parameter workbook (GC-06 part 3) and the EMT pack in the campus
bundle: one CSV per sheet, units in every header, source tags kept, every
missing field listed as to be supplied and never written as zero.
"""

import dataclasses
import io
import json

import pandas as pd
import pytest

from gridspine.handoff import campus_bundle as CB
from gridspine.handoff.emt_pack import write_emt_pack
from gridspine.handoff.emt_workbook import (
    CONTROL_DYNAMIC,
    EMT_ONLY,
    PARAM_UNITS,
    RSCAD_NOTES,
    SHEET_FIELDS,
    SHEETS,
    UNTAGGED,
    header,
    rscad_notes,
    workbook,
)
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_flow import rows as island_rows
from tests.gridspine.test_island_flow import spec as island_spec
from tests.gridspine.test_island_hours import campus_spec as relay_spec
from tests.gridspine.test_island_sc import two_converters


def gaps_of(g, sheet):
    return set(
        zip(g.loc[g["sheet"] == sheet, "element"], g.loc[g["sheet"] == sheet, "param"])
    )


def without(campus, element, param):
    """The campus with one tagged value removed (an edited, in-memory campus)."""
    p = campus.params
    keep = ~((p["element"] == element) & (p["param"] == param))
    assert (~keep).sum() == 1
    return dataclasses.replace(campus, params=p[keep].reset_index(drop=True))


def test_a_unit_missing_a_required_parameter_is_to_be_supplied_not_zero(tmp_path):
    camp = without(build_campus(campus_spec()), "BESS1", "s_mva")
    p = write_emt_pack(tmp_path, camp)
    g = p.to_be_supplied
    assert ("BESS1", "s_mva") in gaps_of(g, "plant_units")
    assert ("BESS1", "s_mva") in gaps_of(g, "converters")
    for sheet in ("plant_units", "converters"):
        text = (p.path / f"{sheet}.csv").read_text()
        frame = pd.read_csv(io.StringIO(text), keep_default_na=False).set_index(
            "element"
        )
        assert frame.at["BESS1", "s_mva [MVA]"] == ""  # empty, never 0
        assert frame.at["BESS1", "s_mva source"] == ""
        assert frame.at["PV1", "s_mva [MVA]"] == "16.5"


def test_a_converter_without_island_data_lists_its_current_limit(tmp_path):
    frames, g = workbook(build_campus(two_converters("gfl")))
    conv = frames["converters"].set_index("element")
    assert (
        conv.at["B1", "i_max_pu [pu]"] == 1.5
        and conv.at["B1", "i_max_pu source"] == "assumed"
    )
    assert pd.isna(conv.at["B2", "i_max_pu [pu]"])
    assert ("B2", "i_max_pu") in gaps_of(g, "converters")
    assert ("B1", "i_max_pu") not in gaps_of(g, "converters")
    assert (
        conv.at["B1", "control"] == "gfm_droop" and conv.at["B2", "control"] == "gfl_pq"
    )


def test_every_value_column_carries_its_unit_and_is_followed_by_its_source():
    frames, _ = workbook(build_campus(relay_spec()))
    assert tuple(frames) == SHEETS
    for name, f in frames.items():
        values = [c for c in f.columns if "[" in c]
        assert values, name
        for c in values:
            param = c.split(" [")[0]
            assert c == f"{param} [{PARAM_UNITS[param]}]"
            assert list(f.columns)[list(f.columns).index(c) + 1] == f"{param} source"


def test_units_stay_as_the_schema_names_them_with_no_conversion():
    # kW beside MVA on one sheet, kA on the relay sheet: each in its own unit
    frames, _ = workbook(build_campus(relay_spec()))
    tr = frames["transformers"].set_index("element")
    assert tr.at["TR1", "pfe_kw [kW]"] == 25.0
    assert tr.at["TR1", "sn_mva [MVA]"] == 40.0
    assert (tr.at["TR1", "sn_mva source"], tr.at["TR1", "pfe_kw source"]) == (
        "datasheet",
        "assumed",
    )
    r = frames["protection_relays"].set_index("element")
    assert r.at["R1", "i_pickup_ka [kA]"] == 0.5
    assert r.at["R1", "i_pickup_e_ka [kA]"] == 0.05


def test_a_field_without_a_unit_is_refused():
    assert header("pfe_kw") == "pfe_kw [kW]"
    with pytest.raises(
        ContractError,
        match="'pfe_w' has no unit in PARAM_UNITS; a unit is never guessed",
    ):
        header("pfe_w")


def test_emt_only_fields_are_always_to_be_supplied():
    frames, g = workbook(build_campus(campus_spec()))
    for name in ("TR1", "TR2"):
        assert {
            (name, f)
            for f in ("saturation_curve", "knee_voltage_pu", "air_core_reactance_pu")
        } <= gaps_of(g, "transformers")
    for bus in ("PCC", "MV1", "MV2"):
        assert {
            (bus, f)
            for f in (
                "bil_kv",
                "arrester_ur_kv",
                "arrester_uc_kv",
                "arrester_energy_kj_per_kv",
            )
        } <= gaps_of(g, "insulation")
    assert ("CB1", "cable_geometry") in gaps_of(g, "cables")
    assert {("BESS1", "emt_model"), ("PV1", "emt_model")} <= gaps_of(g, "converters")
    why = g.set_index(["sheet", "element", "param"])["why"]
    assert (
        why[("transformers", "TR1", "saturation_curve")] == EMT_ONLY["saturation_curve"]
    )
    ins = frames["insulation"].set_index("element")
    assert ins.at["PCC", "vn_kv [kV]"] == 110.0 and pd.isna(
        ins.at["PCC", "bil_kv [kV]"]
    )


def test_held_cable_zero_sequence_is_not_a_gap():
    s = campus_spec()
    s["campus"]["cables"]["CB1"].update(
        r0_ohm_per_km=t(0.3), x0_ohm_per_km=t(0.4), c0_nf_per_km=t(300.0)
    )
    frames, g = workbook(build_campus(s))
    assert gaps_of(g, "cables") == {("CB1", "cable_geometry")}
    assert (
        frames["cables"].set_index("element").at["CB1", "x0_ohm_per_km [ohm/km]"] == 0.4
    )
    _, g0 = workbook(build_campus(campus_spec()))
    assert {
        ("CB1", "r0_ohm_per_km"),
        ("CB1", "x0_ohm_per_km"),
        ("CB1", "c0_nf_per_km"),
    } <= gaps_of(g0, "cables")


def test_vector_group_is_tagged_assumed_when_the_campus_defaulted_it():
    tr = workbook(build_campus(campus_spec()))[0]["transformers"].set_index("element")
    assert (tr.at["TR1", "vector_group [-]"], tr.at["TR1", "vector_group source"]) == (
        "Dyn11",
        "assumed",
    )
    s = campus_spec()
    s["campus"]["transformers"]["TR1"]["vector_group"] = "YNd5"
    tr = workbook(build_campus(s))[0]["transformers"].set_index("element")
    assert (tr.at["TR1", "vector_group [-]"], tr.at["TR1", "vector_group source"]) == (
        "YNd5",
        "campus (untagged)",
    )


def test_only_a_time_delayed_relay_needs_its_curve():
    s = relay_spec()
    s["campus"]["relays"].append(
        {
            "name": "R2",
            "branch": "TR1",
            "fault_bus": "MV1",
            "timing": "instantaneous",
            "i_pickup_ka": t(2.0, "datasheet"),
        }
    )
    frames, g = workbook(build_campus(s))
    assert gaps_of(g, "protection_relays") == {("R1", "curve")}
    r = frames["protection_relays"].set_index("element")
    assert (r.at["R2", "timing"], r.at["R2", "branch"]) == ("instantaneous", "TR1")
    assert pd.isna(r.at["R2", "i_pickup_e_ka [kA]"])  # optional, not held, not a gap


def test_compensation_splits_into_filters_and_statcom_converters():
    s = campus_spec()
    s["campus"]["compensation"] = [
        {
            "name": "CAP1",
            "bus": "MV1",
            "kind": "capacitor_bank",
            "q_mvar": t(2.0),
            "steps": 4,
        },
        {"name": "REA1", "bus": "MV1", "kind": "shunt_reactor", "q_mvar": t(1.0)},
        {"name": "STC1", "bus": "MV2", "kind": "statcom", "q_mvar": t(5.0)},
    ]
    frames, g = workbook(build_campus(s))
    f = frames["filters"].set_index("element")
    assert list(f.index) == ["CAP1", "REA1"]
    assert (f.at["CAP1", "steps [-]"], f.at["CAP1", "q_mvar [Mvar]"]) == (4.0, 2.0)
    assert pd.isna(f.at["REA1", "steps [-]"]) and gaps_of(g, "filters") == set()
    conv = frames["converters"].set_index("element")
    assert conv.at["STC1", "kind"] == "statcom"
    assert ("STC1", "emt_model") in gaps_of(g, "converters")
    assert ("STC1", "i_max_pu") not in gaps_of(g, "converters")


def test_a_ups_needs_no_current_limit():
    from tests.gridspine.test_island_schema import ups

    s = island_spec()
    s["campus"]["units"]["UPS1"] = {
        "kind": "ups",
        "bus": "MV2",
        "p_mw": t(5.0),
        "e_mwh": t(2.0),
        "s_mva": t(6.0),
        "island": {**ups(), "it_load": ["DC_LOAD"]},
    }
    _, g = workbook(build_campus(s))
    assert ("UPS1", "i_max_pu") not in gaps_of(g, "converters")
    assert ("UPS1", "emt_model") in gaps_of(g, "converters")
    assert not {e for e, _ in gaps_of(g, "plant_units")}


def test_the_pack_carries_every_sheet_and_the_rscad_note(tmp_path):
    p = write_emt_pack(tmp_path, build_campus(campus_spec()))
    for name in SHEETS:
        assert f"{name}.csv" in p.manifest["files"]
        assert f"`{name}.csv`" in rscad_notes()
    assert RSCAD_NOTES in p.manifest["files"]
    assert (p.path / RSCAD_NOTES).read_text() == rscad_notes()
    assert set(p.to_be_supplied["sheet"]) >= {
        "transformers",
        "insulation",
        "cables",
        "converters",
    }


def test_the_campus_bundle_writes_the_emt_pack(tmp_path):
    camp = build_campus(island_spec())
    b = CB.export_campus_bundle(tmp_path, camp, island_rows(), "h18")
    m = json.loads((b.path / "manifest.json").read_text())
    emt = json.loads((b.path / "emt" / "manifest.json").read_text())
    assert m["emt_pack"] == {
        "manifest": "emt/manifest.json",
        "data_hash": emt["data_hash"],
    }
    assert "transformers.csv" in emt["files"]


def test_a_refused_pack_is_recorded_and_the_psse_bundle_still_written(tmp_path):
    s = island_spec()
    s["campus"]["pcc"]["rx_min"] = t(0.0)
    b = CB.export_campus_bundle(tmp_path, build_campus(s), island_rows(), "h18")
    m = json.loads((b.path / "manifest.json").read_text())
    assert "X/R not given / infinite" in m["emt_pack"]["not_written"]
    assert (b.path / f"{b.path.name}.raw").exists()
    assert not (b.path / "emt").exists()


# --- W2 (GK A2): unit coverage, mechanically ------------------------------------

#: A schema field-name suffix and the unit it fixes, longest first.
SUFFIX_UNITS = (
    ("_kj_per_kv", "kJ/kV"),
    ("_mw_per_hz", "MW/Hz"),
    ("_mw_per_s", "MW/s"),
    ("_ohm_per_km", "ohm/km"),
    ("_nf_per_km", "nF/km"),
    ("_percent", "%"),
    ("_pct", "%"),
    ("_mwh", "MWh"),
    ("_mvar", "Mvar"),
    ("_mva", "MVA"),
    ("_mw", "MW"),
    ("_kw", "kW"),
    ("_kv", "kV"),
    ("_ka", "kA"),
    ("_km", "km"),
    ("_pu", "pu"),
    ("_hz", "Hz"),
    ("_ohm", "ohm"),
    ("_s", "s"),
)


def test_every_field_any_sheet_writes_has_a_unit():
    for sheet, fields in SHEET_FIELDS.items():
        for f in fields:
            assert f in PARAM_UNITS, (sheet, f)
            assert header(f) == f"{f} [{PARAM_UNITS[f]}]"


def test_each_unit_matches_its_field_name_suffix():
    checked = 0
    for f, unit in PARAM_UNITS.items():
        for suffix, expected in SUFFIX_UNITS:
            if f.endswith(suffix):
                assert unit == expected, (f, unit, expected)
                checked += 1
                break
    assert checked >= 40


def test_values_the_campus_holds_untagged_say_so():
    s = campus_spec()
    s["campus"]["compensation"] = [
        {
            "name": "CAP1",
            "bus": "MV1",
            "kind": "capacitor_bank",
            "q_mvar": t(2.0),
            "steps": 4,
        }
    ]
    frames, _ = workbook(build_campus(s))
    assert (
        frames["insulation"].set_index("element").at["MV1", "vn_kv source"] == UNTAGGED
    )
    assert frames["filters"].set_index("element").at["CAP1", "steps source"] == UNTAGGED
    assert UNTAGGED in rscad_notes()


# --- gensets and converters: island data ------------------------------------------


def test_a_genset_with_island_data_carries_it():
    frames, g = workbook(build_campus(island_spec()))
    gen = frames["gensets"].set_index("element")
    assert list(gen.index) == ["GEN1"]
    assert gen.at["GEN1", "governor"] in ("droop", "isochronous")
    assert gen.at["GEN1", "H_s [s]"] > 0 and gen.at["GEN1", "xd_pp [pu]"] > 0
    assert not {p for e, p in gaps_of(g, "gensets")}


def test_a_genset_without_island_data_lists_its_dynamics():
    frames, g = workbook(build_campus(campus_spec()))
    assert gaps_of(g, "gensets") == {
        ("GEN1", f) for f in ("xd_sat", "H_s", "T_g_s", "q_droop_pct", "cos_phi_r")
    }
    gen = frames["gensets"].set_index("element")
    assert gen.at["GEN1", "xd_pp [pu]"] == 0.15 and gen.at["GEN1", "rx_sc [-]"] == 0.07
    assert pd.isna(gen.at["GEN1", "H_s [s]"])


def test_converter_control_data_follows_its_mode():
    frames, g = workbook(build_campus(island_spec()))
    conv = frames["converters"].set_index("element")
    assert conv.at["BESS1", "control"] == "gfm_droop"
    assert (
        conv.at["BESS1", "droop_pct [%]"] > 0 and conv.at["BESS1", "k_gfm_min [pu]"] > 0
    )
    gaps = gaps_of(g, "converters")
    assert not {p for e, p in gaps if e == "BESS1"} - {"emt_model"}
    # PV1 has no island data: its control mode and current limit are to be supplied
    assert {("PV1", "control"), ("PV1", "i_max_pu")} <= gaps
    why = g.set_index(["sheet", "element", "param"])["why"]
    assert why[("converters", "PV1", "control")].startswith(
        "converter control mode not held"
    )


def test_a_gfm_converter_missing_a_control_parameter_is_a_gap():
    camp = without(build_campus(island_spec()), "BESS1", "tau_f_s")
    _, g = workbook(camp)
    assert ("BESS1", "tau_f_s") in gaps_of(g, "converters")


def test_a_statcom_keeps_its_rating():
    s = campus_spec()
    s["campus"]["compensation"] = [
        {"name": "STC1", "bus": "MV2", "kind": "statcom", "q_mvar": t(5.0, "datasheet")}
    ]
    frames, g = workbook(build_campus(s))
    conv = frames["converters"].set_index("element")
    assert (conv.at["STC1", "q_mvar [Mvar]"], conv.at["STC1", "q_mvar source"]) == (
        5.0,
        "datasheet",
    )
    assert ("STC1", "q_mvar") not in gaps_of(g, "converters")
    assert ("STC1", "control") not in gaps_of(g, "converters")


# --- the bundle's hour stage --------------------------------------------------------


def _stage(b):
    th = json.loads((b.path / "emt" / "thevenin_set.json").read_text())
    return {r["stage"]: r for r in th["stages"]}


def test_the_bundle_stage_is_the_hour_with_its_running_units(tmp_path):
    camp = build_campus(island_spec())
    on = island_rows()
    off = on.assign(status=[1, 1, 1, 0])  # GEN1 off in this hour
    a = CB.export_campus_bundle(tmp_path / "on", camp, on, "h18")
    b = CB.export_campus_bundle(tmp_path / "off", camp, off, "h18")
    assert set(_stage(a)) == {"hour"}
    assert _stage(a)["hour"]["sk_max_mva"] > _stage(b)["hour"]["sk_max_mva"]


def test_every_control_mode_maps_to_converter_columns():
    from gridspine.schema.island import CONTROLS

    assert set(CONTROL_DYNAMIC) == set(CONTROLS)
    assert CONTROL_DYNAMIC["gfm_vsm"] == (
        "droop_pct",
        "q_droop_pct",
        "H_v_s",
        "i_max_pu",
        "k_gfm_min",
    )
    assert CONTROL_DYNAMIC["gfl_qu"] == ()
    for fields in CONTROL_DYNAMIC.values():
        assert set(fields) <= set(SHEET_FIELDS["converters"])


@pytest.mark.parametrize(
    "sheet, element, param",
    [("gensets", "GEN1", "xd_pp"), ("cables", "CB1", "length_km")],
)
def test_a_missing_schema_field_is_a_gap_on_every_sheet(sheet, element, param):
    _, g = workbook(without(build_campus(campus_spec()), element, param))
    assert (element, param) in gaps_of(g, sheet)


def test_a_statcom_missing_its_rating_is_a_gap():
    s = campus_spec()
    s["campus"]["compensation"] = [
        {"name": "STC1", "bus": "MV2", "kind": "statcom", "q_mvar": t(5.0)}
    ]
    _, g = workbook(without(build_campus(s), "STC1", "q_mvar"))
    assert ("STC1", "q_mvar") in gaps_of(g, "converters")


# --- GK A2 asks on 6c81fec1 -----------------------------------------------------

#: Fields whose name carries no unit suffix, with their unit, listed by hand.
UNSUFFIXED_UNITS = {
    "xd_pp": "pu",
    "k_gfm_min": "pu",
    "k_sc": "pu",
    "xd_sat": "pu",
    "pf": "-",
    "pf_rated": "-",
    "rx_sc": "-",
    "cos_phi": "-",
    "cos_phi_r": "-",
    "emt_model": "-",
    "vector_group": "-",
    "saturation_curve": "pu flux vs pu current",
    "cable_geometry": "-",
    "steps": "-",
    "curve": "-",
    "tap_changer": "-",
    "tap_side": "-",
    "tap_neutral": "-",
    "tap_min": "-",
    "tap_max": "-",
    "tap_pos": "-",
}


@pytest.mark.parametrize("field, unit", sorted(UNSUFFIXED_UNITS.items()))
def test_an_unsuffixed_field_has_its_hand_listed_unit(field, unit):
    assert PARAM_UNITS[field] == unit
    assert header(field) == f"{field} [{unit}]"


def test_every_field_without_a_unit_suffix_is_hand_listed():
    unsuffixed = {
        f for f in PARAM_UNITS if not any(f.endswith(s) for s, _ in SUFFIX_UNITS)
    }
    assert unsuffixed == set(UNSUFFIXED_UNITS)


#: Every required field the test campus holds, by sheet, listed by hand.
REQUIRED_HELD = [
    ("plant_units", "BESS1", "p_mw"),
    ("plant_units", "BESS1", "e_mwh"),
    ("plant_units", "BESS1", "s_mva"),
    ("plant_units", "PV1", "p_mw"),
    ("plant_units", "PV1", "s_mva"),
    ("plant_units", "GEN1", "p_mw"),
    ("plant_units", "GEN1", "s_mva"),
    ("plant_units", "DC_LOAD", "p_mw"),
    ("plant_units", "DC_LOAD", "pf"),
    ("gensets", "GEN1", "s_mva"),
    ("gensets", "GEN1", "xd_pp"),
    ("gensets", "GEN1", "rx_sc"),
    ("converters", "BESS1", "s_mva"),
    ("converters", "PV1", "s_mva"),
    *(
        ("transformers", "TR1", f)
        for f in (
            "sn_mva",
            "vn_hv_kv",
            "vn_lv_kv",
            "vk_percent",
            "vkr_percent",
            "pfe_kw",
            "i0_percent",
        )
    ),
    *(
        ("cables", "CB1", f)
        for f in (
            "length_km",
            "r_ohm_per_km",
            "x_ohm_per_km",
            "c_nf_per_km",
            "max_i_ka",
        )
    ),
]


@pytest.mark.parametrize("sheet, element, param", REQUIRED_HELD)
def test_each_required_field_once_missing_is_a_gap_with_an_empty_cell(
    sheet, element, param
):
    frames, g = workbook(without(build_campus(campus_spec()), element, param))
    assert (element, param) in gaps_of(g, sheet)
    row = frames[sheet].set_index("element").loc[element]
    assert pd.isna(row[header(param)])


def test_every_pack_csv_has_lf_line_endings(tmp_path):
    p = write_emt_pack(tmp_path, build_campus(campus_spec()))
    csvs = sorted(p.path.glob("*.csv"))
    assert {f"{s}.csv" for s in SHEET_FIELDS} <= {c.name for c in csvs}
    for c in csvs:
        data = c.read_bytes()
        assert b"\n" in data and b"\r" not in data, c.name


def test_a_stale_emt_pack_is_cleared_on_each_export(tmp_path):
    rows = island_rows()
    b = CB.export_campus_bundle(tmp_path, build_campus(island_spec()), rows, "h18")
    stale = b.path / "emt" / "stale.csv"
    stale.write_text("old\n")
    CB.export_campus_bundle(tmp_path, build_campus(island_spec()), rows, "h18")
    assert (b.path / "emt" / "manifest.json").exists()
    assert not stale.exists()
    s = island_spec()
    s["campus"]["pcc"]["rx_min"] = t(0.0)
    CB.export_campus_bundle(tmp_path, build_campus(s), rows, "h18")
    assert not (b.path / "emt").exists()


def test_a_workbook_contract_refusal_is_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.delitem(PARAM_UNITS, "pfe_kw")
    b = CB.export_campus_bundle(
        tmp_path, build_campus(island_spec()), island_rows(), "h18"
    )
    m = json.loads((b.path / "manifest.json").read_text())
    assert "'pfe_kw' has no unit" in m["emt_pack"]["not_written"]
    assert (b.path / f"{b.path.name}.raw").exists()
    assert not (b.path / "emt").exists()


def test_a_non_contract_error_in_the_pack_propagates(tmp_path, monkeypatch):
    from gridspine.handoff import emt_pack

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(emt_pack, "write_emt_pack", boom)
    with pytest.raises(RuntimeError, match="bug"):
        CB.export_campus_bundle(
            tmp_path, build_campus(island_spec()), island_rows(), "h18"
        )


# --- earthing transformers and tap changers (GC-06c) -----------------------


def _tap(**over):
    block = {
        "changer": "on_load",
        "side": "hv",
        "neutral": 1,
        "min": -9,
        "max": 9,
        "step_percent": t(1.5, "datasheet"),
        "pos": 2,
    }
    return {**block, **over}


def _frames(camp):
    return workbook(camp)


def test_a_held_tap_changer_fills_its_columns_with_its_own_tag():
    s = campus_spec()
    s["campus"]["transformers"]["TR1"]["tap"] = _tap(side="lv", min=-7, max=11)
    frames, g = _frames(build_campus(s))
    tr = frames["transformers"].set_index("element")
    assert tr.at["TR1", "tap_changer [-]"] == "on_load"
    assert tr.at["TR1", "tap_side [-]"] == "lv"
    assert tr.at["TR1", "tap_neutral [-]"] == 1
    assert (tr.at["TR1", "tap_min [-]"], tr.at["TR1", "tap_max [-]"]) == (-7, 11)
    assert tr.at["TR1", "tap_step_percent [%]"] == 1.5
    assert tr.at["TR1", "tap_step_percent source"] == "datasheet"
    assert tr.at["TR1", "tap_changer source"] == "campus (untagged)"
    assert ("TR1", "tap_changer") not in gaps_of(g, "transformers")


def test_a_transformer_without_a_tap_changer_is_to_be_supplied_not_zero():
    s = campus_spec()
    s["campus"]["transformers"]["TR1"]["tap"] = _tap()
    frames, g = _frames(build_campus(s))
    assert ("TR2", "tap_changer") in gaps_of(g, "transformers")
    assert ("TR1", "tap_changer") not in gaps_of(g, "transformers")
    tr = frames["transformers"].set_index("element")
    for col in ("tap_neutral [-]", "tap_min [-]", "tap_step_percent [%]"):
        assert pd.isna(tr.at["TR2", col])  # empty, never 0
    why = g[(g["element"] == "TR2") & (g["param"] == "tap_changer")]["why"].item()
    assert why == "no tap changer entered; modelled at nominal ratio"


def test_an_earthing_transformer_is_a_sheet_with_x0_and_its_neutral():
    s = campus_spec()
    s["campus"]["earthing_transformers"] = {
        "ET1": {"bus": "MV1", "x0_ohm": t(4.0, "datasheet")},
        "ET2": {
            "bus": "MV2",
            "x0_ohm": t(6.0, "measured"),
            "r_n_ohm": t(12.0, "datasheet"),
        },
    }
    frames, g = _frames(build_campus(s))
    et = frames["earthing_transformers"].set_index("element")
    assert et.at["ET1", "x0_ohm [ohm]"] == 4.0
    assert et.at["ET1", "x0_ohm source"] == "datasheet"
    assert et.at["ET1", "neutral"] == "solid"
    assert pd.isna(et.at["ET1", "r_n_ohm [ohm]"])  # solid: no resistor, not a gap
    assert et.at["ET2", "neutral"] == "resistance"
    assert et.at["ET2", "r_n_ohm [ohm]"] == 12.0
    assert et.at["ET2", "r_n_ohm source"] == "datasheet"
    assert not (g["sheet"] == "earthing_transformers").any()


def test_an_earthing_transformer_missing_x0_is_a_gap():
    s = campus_spec()
    s["campus"]["earthing_transformers"] = {
        "ET1": {"bus": "MV1", "x0_ohm": t(4.0, "datasheet")}
    }
    camp = without(build_campus(s), "ET1", "x0_ohm")
    _, g = workbook(camp)
    assert ("ET1", "x0_ohm") in gaps_of(g, "earthing_transformers")


def test_a_campus_without_earthing_transformers_has_an_empty_sheet(tmp_path):
    p = write_emt_pack(tmp_path, build_campus(campus_spec()))
    text = (p.path / "earthing_transformers.csv").read_text()
    assert text.splitlines() == [
        "element,bus,referred_to_kv,neutral,x0_ohm [ohm],x0_ohm source,r_n_ohm [ohm],r_n_ohm source"
    ]


def test_a_resistance_earthed_winding_needs_its_neutral_resistor():
    s = campus_spec()
    tr = s["campus"]["transformers"]["TR1"]
    tr["vector_group"] = "YNyn0"
    tr["earthing_hv"] = "resistance"
    tr["r_n_hv_ohm"] = t(20.0, "datasheet")
    tr["earthing_lv"] = "solid"
    camp = build_campus(s)
    frames, g = workbook(camp)
    row = frames["transformers"].set_index("element").loc["TR1"]
    assert row["r_n_hv_ohm [ohm]"] == 20.0
    assert row["r_n_hv_ohm source"] == "datasheet"
    assert ("TR1", "r_n_hv_ohm") not in gaps_of(g, "transformers")
    assert ("TR1", "r_n_lv_ohm") not in gaps_of(g, "transformers")  # solid: none
    _, g2 = workbook(without(camp, "TR1", "r_n_hv_ohm"))
    assert ("TR1", "r_n_hv_ohm") in gaps_of(g2, "transformers")


def test_the_new_sheet_is_written_and_listed_for_rscad(tmp_path):
    p = write_emt_pack(tmp_path, build_campus(campus_spec()))
    assert "earthing_transformers.csv" in p.manifest["files"]
    notes = (p.path / "RSCAD_NOTES.md").read_text()
    assert "`earthing_transformers.csv`" in notes


def test_a_held_tap_position_is_written_and_the_default_says_it_is_neutral():
    s = campus_spec()
    s["campus"]["transformers"]["TR1"]["tap"] = _tap(pos=-3)
    t2 = _tap()
    del t2["pos"]  # the campus defaults the position to neutral
    s["campus"]["transformers"]["TR2"]["tap"] = t2
    frames, _ = workbook(build_campus(s))
    tr = frames["transformers"].set_index("element")
    assert tr.at["TR1", "tap_pos [-]"] == -3
    assert tr.at["TR2", "tap_pos [-]"] == 1  # the fixture neutral, not 0
    for n in ("TR1", "TR2"):
        assert "neutral when none was entered" in tr.at[n, "tap_note"]
        assert "campus_invest_taps.csv" in tr.at[n, "tap_note"]


def test_no_tap_means_no_tap_note_and_no_position():
    frames, _ = workbook(build_campus(campus_spec()))
    tr = frames["transformers"].set_index("element")
    assert pd.isna(tr.at["TR1", "tap_note"]) and pd.isna(tr.at["TR1", "tap_pos [-]"])


def _earthing(frames, name):
    r = frames["transformers"].set_index("element").loc[name]
    return {
        k: r[k]
        for k in (
            "earthing_hv",
            "earthing_hv source",
            "earthing_lv",
            "earthing_lv source",
        )
    }


def test_each_winding_earthing_mode_is_written_with_its_own_source():
    s = campus_spec()
    tr = s["campus"]["transformers"]
    tr["TR1"].update(
        vector_group="YNyn0",
        earthing_hv="resistance",
        r_n_hv_ohm=t(20.0, "datasheet"),
        earthing_lv="solid",
    )
    tr["TR2"].update(
        vector_group="Dyn11", earthing_lv="resistance", r_n_lv_ohm=t(8.0, "measured")
    )
    frames, _ = workbook(build_campus(s))
    assert _earthing(frames, "TR1") == {
        "earthing_hv": "resistance",
        "earthing_hv source": "campus (untagged)",
        "earthing_lv": "solid",
        "earthing_lv source": "campus (untagged)",
    }
    assert _earthing(frames, "TR2") == {
        "earthing_hv": "delta",
        "earthing_hv source": "campus (untagged)",
        "earthing_lv": "resistance",
        "earthing_lv source": "campus (untagged)",
    }
    r = frames["transformers"].set_index("element")
    assert r.at["TR2", "r_n_lv_ohm [ohm]"] == 8.0
    assert pd.isna(
        r.at["TR1", "r_n_lv_ohm [ohm]"]
    )  # solid: blank, and the mode says so


def test_an_isolated_star_neutral_reads_isolated_not_solid():
    s = campus_spec()
    s["campus"]["transformers"]["TR1"].update(
        vector_group="Yyn0", earthing_hv="isolated"
    )
    frames, _ = workbook(build_campus(s))
    assert _earthing(frames, "TR1")["earthing_hv"] == "isolated"
    assert _earthing(frames, "TR1")["earthing_lv"] == "solid"


def test_an_assumed_neutral_earthing_is_tagged_assumed_with_the_campus_reason():
    s = campus_spec()
    s["campus"]["transformers"]["TR1"].update(
        vector_group="YNyn0", earthing_hv="resistance", r_n_hv_ohm=t(20.0, "datasheet")
    )
    camp = build_campus(s)
    frames, _ = workbook(camp)
    e = _earthing(frames, "TR1")
    assert (e["earthing_hv source"], e["earthing_lv source"]) == (
        "campus (untagged)",
        "assumed",
    )
    r = frames["transformers"].set_index("element")
    assert (
        r.at["TR1", "assumed"]
        == "TR1: LV neutral earthing not given; taken as solid (assumed)"
    )
    assert r.at["TR1", "assumed"] in camp.assumptions


def test_a_defaulted_vector_group_is_assumed_with_its_reason_and_a_given_one_is_not():
    s = campus_spec()
    s["campus"]["transformers"]["TR2"]["vector_group"] = "Dyn5"
    camp = build_campus(s)
    frames, _ = workbook(camp)
    r = frames["transformers"].set_index("element")
    assert r.at["TR1", "vector_group source"] == "assumed"
    assert r.at["TR1", "assumed"] == (
        "TR1: no vector_group; taken as Dyn11 (assumed); "
        "TR1: LV neutral earthing not given; taken as solid (assumed)"
    )
    assert r.at["TR2", "vector_group source"] == "campus (untagged)"
    # a given vector group is not assumed; the unearthed LV star neutral still is
    assert (
        r.at["TR2", "assumed"]
        == "TR2: LV neutral earthing not given; taken as solid (assumed)"
    )
    assert (
        r.at["TR1", "earthing_hv"] == "delta" and r.at["TR1", "earthing_lv"] == "solid"
    )
    assert r.at["TR1", "earthing_lv source"] == "assumed"


def test_the_earthing_transformer_names_the_kv_its_ohms_are_referred_to():
    s = campus_spec()
    s["campus"]["earthing_transformers"] = {
        "ET1": {"bus": "MV1", "x0_ohm": t(4.0, "datasheet")}
    }
    frames, _ = workbook(build_campus(s))
    et = frames["earthing_transformers"].set_index("element")
    assert et.at["ET1", "referred_to_kv"] == 20.0
    assert et.at["ET1", "x0_ohm [ohm]"] == 4.0


def test_the_workbook_csvs_hold_no_nan_text(tmp_path):
    s = campus_spec()
    s["campus"]["earthing_transformers"] = {
        "ET1": {"bus": "MV1", "x0_ohm": t(4.0, "datasheet")}
    }
    s["campus"]["transformers"]["TR1"]["tap"] = _tap()
    # TR2 states everything the campus would otherwise assume, so it has no
    # assumption: its `assumed` cell must be empty, not "None" or "nan".
    s["campus"]["transformers"]["TR2"].update(vector_group="Dyn11", earthing_lv="solid")
    camp = build_campus(s)
    assert not [a for a in camp.assumptions if a.startswith("TR2:")]
    p = write_emt_pack(tmp_path, camp)
    tr = pd.read_csv(
        p.path / "transformers.csv", keep_default_na=False, dtype=str
    ).set_index("element")
    assert tr.at["TR2", "assumed"] == ""
    assert tr.at["TR1", "assumed"] != ""
    for sheet in ("transformers", "earthing_transformers"):
        frame = pd.read_csv(p.path / f"{sheet}.csv", keep_default_na=False, dtype=str)
        cells = {c.strip().lower() for c in frame.to_numpy().ravel()}
        assert not cells & {"nan", "none", "inf"}, sheet
        assert (frame["element"] != "").all()


def test_the_rscad_note_says_when_no_earthing_transformer_is_entered():
    assert "header only when none entered" in rscad_notes()


def test_resistance_earthing_without_its_resistor_is_refused_not_read_as_solid():
    s = campus_spec()
    tr = s["campus"]["transformers"]["TR1"]
    tr.update(vector_group="YNyn0", earthing_hv="resistance", earthing_lv="solid")
    with pytest.raises(ContractError, match="r_n_hv_ohm is needed with resistance"):
        build_campus(s)


def test_a_neutral_resistor_on_a_solid_winding_is_refused():
    s = campus_spec()
    tr = s["campus"]["transformers"]["TR1"]
    tr.update(
        vector_group="YNyn0",
        earthing_hv="solid",
        r_n_hv_ohm=t(20.0, "datasheet"),
        earthing_lv="solid",
    )
    with pytest.raises(ContractError, match="r_n_hv_ohm is needed with resistance"):
        build_campus(s)
