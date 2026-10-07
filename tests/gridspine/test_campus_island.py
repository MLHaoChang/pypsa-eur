"""Island data in the campus file, I1 (plan 2026-10-07-campus-island-operation).

A campus unit may carry an ``island`` block (its control mode and dynamics),
the campus may carry ``island`` requirements, a transformer may carry its
neutral earthing, and there is a new unit kind, ``ups``.

Oracles:
* a campus without island data builds exactly as before (the C1 tests, and
  the load-flow equality below);
* a UPS feeds no fault: the IEC 60909 currents with and without it are equal;
* the island schema's own refusals, raised with the campus element named.
"""
import copy

import pandapower as pp
import pandapower.shortcircuit as sc
import pytest

from gridspine.ingest.campus import UNIT_KINDS, build_campus
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_schema import genset, gfm_bess, limits, pv_qu, ups


def island_campus():
    s = campus_spec()
    c = s["campus"]
    c["units"]["BESS1"]["island"] = gfm_bess()
    c["units"]["PV1"]["island"] = pv_qu()
    c["units"]["GEN1"]["island"] = genset()
    c["units"]["GEN2"] = {"kind": "genset", "bus": "MV2", "p_mw": t(10.0), "s_mva": t(12.5),
                          "xd_pp": t(0.15), "rx_sc": t(0.07), "island": genset()}
    c["units"]["DC_LOAD"]["island"] = {"critical": True}
    c["units"]["UPS1"] = {"kind": "ups", "bus": "MV2", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                          "island": {**ups(), "it_load": ["DC_LOAD"]}}
    c["island"] = {"bridge_s": t(600.0), "sustained_h": t(24.0), "scenarios": ["ups_bridged", "seamless"],
                   "pickup_blocks": t([25.0, 25.0]), "frequency_limits": limits(),
                   "rocof_window_ms": t(500.0), "gfm_margin": t(0.1)}
    c["transformers"]["TR1"].update(earthing_hv="solid", earthing_lv="resistance", r_n_lv_ohm=t(12.0))
    return s


def test_a_campus_without_island_data_has_none_and_solves_as_before():
    plain = build_campus(campus_spec())
    assert plain.island == {"units": {}, "requirements": None}
    with_blocks = campus_spec()
    with_blocks["campus"]["units"]["BESS1"]["island"] = gfm_bess()
    built = build_campus(with_blocks)
    pp.runpp(plain.net)
    pp.runpp(built.net)
    assert (plain.net.res_bus.vm_pu.values == built.net.res_bus.vm_pu.values).all()


def test_island_blocks_and_requirements_are_validated_and_kept():
    c = build_campus(island_campus())
    assert c.island["units"]["BESS1"]["control"] == "gfm_droop"
    assert c.island["units"]["GEN1"]["governor"] == "droop"
    assert c.island["units"]["DC_LOAD"]["critical"] is True
    assert c.island["units"]["UPS1"]["it_load"] == ["DC_LOAD"]
    req = c.island["requirements"]
    assert req["values"]["sustained_h"] == 24.0
    assert req["ride_through_storage"] == ["BESS1"]      # the default: every bess, never the UPS


def test_island_numbers_join_the_tagged_parameter_table():
    p = build_campus(island_campus()).params
    row = p[(p["element"] == "BESS1") & (p["param"] == "droop_pct")]
    assert row["value"].tolist() == [4.0] and row["source"].tolist() == ["assumed"]
    row = p[(p["element"] == "GEN1") & (p["param"] == "load_step_max_pct")]
    assert row["source"].tolist() == ["datasheet"]


def test_a_ups_is_a_storage_unit_that_feeds_no_fault():
    assert "ups" in UNIT_KINDS
    c = build_campus(island_campus())
    assert c.registry.at["UPS1", "kind"] == "storage"
    assert c.units.at["UPS1", "kind"] == "ups"
    without = island_campus()
    del without["campus"]["units"]["UPS1"]
    del without["campus"]["units"]["DC_LOAD"]["island"]       # nothing left for the UPS to protect
    without["campus"]["island"]["pickup_blocks"] = t([25.0, 25.0])
    a, b = build_campus(island_campus()).net, build_campus(without).net
    for case in ("max", "min"):
        sc.calc_sc(a, case=case)
        sc.calc_sc(b, case=case)
        assert a.res_bus_sc["ikss_ka"].round(9).tolist() == b.res_bus_sc["ikss_ka"].round(9).tolist()


def test_a_ups_needs_its_island_block():
    s = island_campus()
    del s["campus"]["units"]["UPS1"]["island"]
    with pytest.raises(ContractError, match="UPS1.*island"):
        build_campus(s)


def test_a_block_with_fields_of_another_kind_is_refused_with_the_unit_named():
    s = island_campus()
    s["campus"]["units"]["BESS1"]["island"]["governor"] = "droop"
    with pytest.raises(ContractError, match=r"units\.BESS1\.island.*governor"):
        build_campus(s)


def test_a_ups_must_protect_a_load_of_the_campus():
    s = island_campus()
    s["campus"]["units"]["UPS1"]["island"]["it_load"] = ["PV1"]
    with pytest.raises(ContractError, match="PV1"):
        build_campus(s)


def test_ride_through_storage_cannot_be_the_ups():
    s = island_campus()
    s["campus"]["island"]["ride_through_storage"] = ["UPS1"]
    with pytest.raises(ContractError, match="ride_through_storage"):
        build_campus(s)


def test_pickup_blocks_must_cover_the_critical_load():
    s = island_campus()
    s["campus"]["island"]["pickup_blocks"] = t([25.0, 24.0])     # 49 MW against a 50 MW critical load
    with pytest.raises(ContractError, match="pickup_blocks"):
        build_campus(s)


def test_redundancy_must_leave_a_genset():
    s = island_campus()
    s["campus"]["island"]["genset_redundancy_n"] = t(2)
    with pytest.raises(ContractError, match="genset_redundancy_n"):
        build_campus(s)


def test_transformer_earthing_is_kept_and_checked():
    c = build_campus(island_campus())
    assert c.earthing["TR1"] == {"hv": "solid", "lv": "resistance"}
    p = c.params
    assert p[(p["element"] == "TR1") & (p["param"] == "r_n_lv_ohm")]["value"].tolist() == [12.0]
    s = island_campus()
    del s["campus"]["transformers"]["TR1"]["r_n_lv_ohm"]
    with pytest.raises(ContractError, match="r_n_lv_ohm"):
        build_campus(s)
    s = island_campus()
    s["campus"]["transformers"]["TR1"]["earthing_hv"] = "grounded"
    with pytest.raises(ContractError, match="earthing_hv"):
        build_campus(s)


def test_requirements_need_no_unit_blocks():
    s = campus_spec()
    s["campus"]["island"] = copy.deepcopy(island_campus()["campus"]["island"])
    s["campus"]["island"]["scenarios"] = ["seamless"]
    del s["campus"]["island"]["pickup_blocks"]
    with pytest.raises(ContractError, match="genset_redundancy_n"):
        build_campus(s)                      # the default N+1 leaves no genset of the one there is
    s["campus"]["island"]["genset_redundancy_n"] = t(0)
    assert build_campus(s).island["requirements"]["scenarios"] == ["seamless"]
