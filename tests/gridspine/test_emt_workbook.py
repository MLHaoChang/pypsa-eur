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
    EMT_ONLY,
    PARAM_UNITS,
    RSCAD_NOTES,
    SHEETS,
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
        "given",
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
    for f in p.manifest["files"]:
        assert (
            f in rscad_notes()
            or not f.endswith(".csv")
            or f.split(".")[0] not in SHEETS
        )


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
    assert (b.path / "h18.raw").exists() or any(f.endswith(".raw") for f in m["files"])
