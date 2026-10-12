"""The campus data the sequence networks need, I5b (plan 2026-10-07-campus-island-operation).

* a transformer's ``vector_group`` (``Dyn11``, ``YNd5``, ``YNyn0``...): the
  winding connections, untagged like the earthing. Without it a transformer
  is taken as Dyn11 with its LV star solidly earthed, and the campus says so
  (``assumptions``). Earthing on a delta winding, or solid / resistance
  earthing on a star without its neutral brought out, is refused;
* a cable's zero sequence ``r0_ohm_per_km``, ``x0_ohm_per_km``,
  ``c0_nf_per_km`` (tagged, optional);
* the grid's zero sequence at the PCC, ``x0x_max`` / ``r0x0_max`` (and
  ``_min``), tagged, optional;
* earthing transformers, ``{name: {bus, x0_ohm, r_n_ohm?}}``;
* a relay's earth-fault pickup ``i_pickup_e_ka``; a relay needs at least one
  of the two pickups;
* a genset's ``x2_pu`` and ``x0_pu`` in its island block.
"""
import copy

import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import FIELD_USERS
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_schema import genset


def spec():
    return copy.deepcopy(campus_spec())


def test_a_vector_group_gives_each_winding_its_connection_and_neutral():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(vector_group="YNd5")
    s["campus"]["transformers"]["TR2"].update(vector_group="Dyn11", earthing_lv="resistance", r_n_lv_ohm=t(20.0))
    c = build_campus(s)
    assert c.windings["TR1"] == {"hv": "Y", "hv_earth": "solid", "lv": "D", "lv_earth": None, "clock": 5}
    assert c.windings["TR2"] == {"hv": "D", "hv_earth": None, "lv": "Y", "lv_earth": "resistance", "clock": 11}
    assert not [a for a in c.assumptions if a.startswith("TR2")]
    # the neutral resistance travels with the other tagged values
    p = c.params.set_index(["element", "param"])
    assert p.at[("TR2", "r_n_lv_ohm"), "value"] == 20.0


def test_a_neutral_brought_out_without_its_earthing_is_taken_as_solid_and_said():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(vector_group="YNd5")
    c = build_campus(s)
    assert c.windings["TR1"]["hv_earth"] == "solid"
    assert any(a.startswith("TR1") and "solid" in a for a in c.assumptions)


def test_a_star_without_its_neutral_is_unearthed():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(vector_group="Yd1")
    assert build_campus(s).windings["TR1"]["hv_earth"] == "isolated"


def test_without_a_vector_group_a_transformer_is_taken_as_dyn11_and_said():
    c = build_campus(spec())
    assert c.windings["TR1"] == {"hv": "D", "hv_earth": None, "lv": "Y", "lv_earth": "solid", "clock": 11}
    assert any(a.startswith("TR1") and "Dyn11" in a for a in c.assumptions)


def test_without_a_vector_group_the_given_earthing_decides_the_windings():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(earthing_hv="solid", earthing_lv="resistance", r_n_lv_ohm=t(12.0))
    w = build_campus(s).windings["TR1"]
    assert (w["hv"], w["hv_earth"], w["lv"], w["lv_earth"]) == ("Y", "solid", "Y", "resistance")


@pytest.mark.parametrize("group", ["Dyn", "XYn11", "YNyn12", "dYN11", "Dyn-1", 11])
def test_a_malformed_vector_group_is_refused(group):
    s = spec()
    s["campus"]["transformers"]["TR1"]["vector_group"] = group
    with pytest.raises(ContractError, match="vector_group"):
        build_campus(s)


def test_earthing_on_a_delta_winding_is_refused():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(vector_group="Dyn11", earthing_hv="solid")
    with pytest.raises(ContractError, match="delta"):
        build_campus(s)


def test_earthing_a_star_whose_neutral_is_not_brought_out_is_refused():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(vector_group="Dy11", earthing_lv="solid")
    with pytest.raises(ContractError, match="neutral"):
        build_campus(s)


def test_an_isolated_neutral_on_a_star_without_n_is_accepted():
    s = spec()
    s["campus"]["transformers"]["TR1"].update(vector_group="Dy11", earthing_lv="isolated")
    assert build_campus(s).windings["TR1"]["lv_earth"] == "isolated"


def test_cable_zero_sequence_is_optional_and_tagged():
    s = spec()
    s["campus"]["cables"]["CB1"].update(r0_ohm_per_km=t(1.2, "datasheet"), x0_ohm_per_km=t(0.4, "datasheet"),
                                        c0_nf_per_km=t(0.0))
    p = build_campus(s).params.set_index(["element", "param"])
    assert p.at[("CB1", "r0_ohm_per_km"), "value"] == 1.2
    assert p.at[("CB1", "r0_ohm_per_km"), "source"] == "datasheet"
    assert p.at[("CB1", "c0_nf_per_km"), "value"] == 0.0
    s["campus"]["cables"]["CB1"]["x0_ohm_per_km"] = 0.4
    with pytest.raises(ContractError, match="x0_ohm_per_km"):
        build_campus(s)


def test_the_grid_zero_sequence_at_the_pcc_is_optional_and_tagged():
    s = spec()
    s["campus"]["pcc"].update(x0x_max=t(1.5), r0x0_max=t(0.0), x0x_min=t(2.0), r0x0_min=t(0.2))
    p = build_campus(s).params.set_index(["element", "param"])
    assert p.at[("PCC", "x0x_max"), "value"] == 1.5
    assert p.at[("PCC", "r0x0_max"), "value"] == 0.0
    s["campus"]["pcc"]["x0x_min"] = t(0.0)
    with pytest.raises(ContractError, match="x0x_min"):
        build_campus(s)


def test_earthing_transformers_are_read():
    s = spec()
    s["campus"]["earthing_transformers"] = {"ET1": {"bus": "MV2", "x0_ohm": t(15.0, "datasheet"),
                                                    "r_n_ohm": t(25.0)}}
    et = build_campus(s).earthing_transformers
    assert et.loc["ET1"].to_dict() == {"bus": "MV2", "x0_ohm": 15.0, "r_n_ohm": 25.0}


@pytest.mark.parametrize("bad, match", [
    ({"bus": "NOPE", "x0_ohm": t(15.0)}, "bus"),
    ({"bus": "MV2"}, "x0_ohm"),
    ({"bus": "MV2", "x0_ohm": t(15.0), "colour": "red"}, "unknown"),
])
def test_a_bad_earthing_transformer_is_refused(bad, match):
    s = spec()
    s["campus"]["earthing_transformers"] = {"ET1": bad}
    with pytest.raises(ContractError, match=match):
        build_campus(s)


def test_without_an_earthing_transformer_the_table_is_empty():
    et = build_campus(spec()).earthing_transformers
    assert et.empty and list(et.columns) == ["bus", "x0_ohm", "r_n_ohm"]


def _relay(**extra):
    return {"name": "R1", "branch": "CB1", "fault_bus": "MV2", "timing": "time_delayed", **extra}


def test_a_relay_may_carry_an_earth_fault_pickup_alone():
    s = spec()
    s["campus"]["relays"] = [_relay(i_pickup_e_ka=t(0.05, "datasheet"))]
    r = build_campus(s).relays.set_index("relay").loc["R1"]
    assert r["i_pickup_e_ka"] == 0.05
    assert r["i_pickup_ka"] != r["i_pickup_ka"]          # NaN: no phase pickup


def test_a_relay_needs_a_pickup():
    s = spec()
    s["campus"]["relays"] = [_relay()]
    with pytest.raises(ContractError, match="pickup"):
        build_campus(s)


def test_a_relay_with_both_pickups_keeps_both():
    s = spec()
    s["campus"]["relays"] = [_relay(i_pickup_ka=t(0.4), i_pickup_e_ka=t(0.05))]
    r = build_campus(s).relays.set_index("relay").loc["R1"]
    assert (r["i_pickup_ka"], r["i_pickup_e_ka"]) == (0.4, 0.05)


def test_a_genset_may_carry_its_negative_and_zero_sequence_reactance():
    s = spec()
    g = genset()
    g.update(x2_pu=t(0.17, "datasheet"), x0_pu=t(0.06, "datasheet"))
    s["campus"]["units"]["G1"] = {"kind": "genset", "bus": "MV1", "p_mw": t(10.0), "s_mva": t(12.5),
                                  "xd_pp": t(0.15), "rx_sc": t(0.07), "island": g}
    block = build_campus(s).island["units"]["G1"]
    assert (block["values"]["x2_pu"], block["values"]["x0_pu"]) == (0.17, 0.06)
    g["x0_pu"] = t(0.0)
    with pytest.raises(ContractError, match="x0_pu"):
        build_campus(s)


def test_the_new_genset_fields_have_their_reader():
    assert FIELD_USERS["x2_pu"] == ("I5b",)
    assert FIELD_USERS["x0_pu"] == ("I5b",)


def test_a_campus_without_sequence_data_still_builds_its_net_as_before():
    plain = build_campus(spec())
    s = spec()
    s["campus"]["transformers"]["TR1"]["vector_group"] = "YNd5"
    s["campus"]["cables"]["CB1"].update(r0_ohm_per_km=t(1.2), x0_ohm_per_km=t(0.4), c0_nf_per_km=t(300.0))
    s["campus"]["pcc"].update(x0x_max=t(1.5), r0x0_max=t(0.1))
    s["campus"]["earthing_transformers"] = {"ET1": {"bus": "MV2", "x0_ohm": t(15.0)}}
    rich = build_campus(s)
    for table in ("bus", "line", "trafo", "ext_grid", "sgen", "load"):
        assert rich.net[table].equals(plain.net[table]), table
