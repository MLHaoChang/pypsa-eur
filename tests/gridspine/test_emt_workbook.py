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
