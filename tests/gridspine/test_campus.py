"""Campus electrical network, C1 (plan 2026-10-05-gridspine-campus-electrical).

A campus is described in one YAML file and built into a pandapower net:
* a PCC bus with an ext_grid carrying the grid operator's Sk''max/min and R/X;
* transformers, MV/LV buses and cables;
* typed units: load, bess, pv, wind, genset.

Every number is tagged ``measured | datasheet | assumed``, like the unit
templates. The oracle for the build is the same network made directly with
pandapower calls in this file, so a field mapped to the wrong pandapower
argument shows up as a different load flow. The oracle for the PCC fault
level is arithmetic: Ik'' = Sk'' / (sqrt(3) * Vn).
"""
import copy
import math

import pandapower as pp
import pandapower.shortcircuit as sc
import pytest
import yaml

from gridspine.ingest.campus import UNIT_KINDS, build_campus, load_campus
from gridspine.schema.contracts import ContractError


def t(value, source="assumed"):
    return {"value": value, "source": source}


def campus_spec():
    """PCC 110 kV -> two 40 MVA 110/20 kV transformers -> MV1 -> 2 km cable -> MV2."""
    return {
        "campus": {
            "name": "TEST_HUB",
            "f_hz": 50,
            "pcc": {
                "bus": "PCC", "vn_kv": 110.0,
                "vm_pu": t(1.0), "sk_max_mva": t(3000.0, "measured"), "sk_min_mva": t(2000.0, "measured"),
                "rx_max": t(0.1), "rx_min": t(0.12),
            },
            "buses": {"MV1": {"vn_kv": 20.0}, "MV2": {"vn_kv": 20.0}},
            "transformers": {
                name: {
                    "hv_bus": "PCC", "lv_bus": "MV1",
                    "sn_mva": t(40.0, "datasheet"), "vn_hv_kv": t(110.0, "datasheet"),
                    "vn_lv_kv": t(21.0, "datasheet"), "vk_percent": t(12.0, "datasheet"),
                    "vkr_percent": t(0.4, "datasheet"), "pfe_kw": t(25.0), "i0_percent": t(0.05),
                }
                for name in ("TR1", "TR2")
            },
            "cables": {
                "CB1": {
                    "from_bus": "MV1", "to_bus": "MV2", "length_km": t(2.0),
                    "r_ohm_per_km": t(0.0754), "x_ohm_per_km": t(0.11),
                    "c_nf_per_km": t(330.0), "max_i_ka": t(1.2),
                },
            },
            "units": {
                "DC_LOAD": {"kind": "load", "bus": "MV2", "p_mw": t(50.0), "pf": t(0.98)},
                "BESS1": {"kind": "bess", "bus": "MV1", "p_mw": t(20.0), "e_mwh": t(40.0),
                          "s_mva": t(22.0), "k_sc": t(1.2), "rx_sc": t(0.1)},
                "PV1": {"kind": "pv", "bus": "MV1", "p_mw": t(15.0), "s_mva": t(16.5),
                        "k_sc": t(1.1), "rx_sc": t(0.1)},
                "GEN1": {"kind": "genset", "bus": "MV2", "p_mw": t(10.0), "s_mva": t(12.5),
                         "xd_pp": t(0.15), "rx_sc": t(0.07)},
            },
        }
    }


def direct_net():
    """The same campus, written straight in pandapower: the oracle."""
    net = pp.create_empty_network(sn_mva=1.0, f_hz=50)
    pcc = pp.create_bus(net, vn_kv=110.0, name="PCC")
    mv1 = pp.create_bus(net, vn_kv=20.0, name="MV1")
    mv2 = pp.create_bus(net, vn_kv=20.0, name="MV2")
    pp.create_ext_grid(net, pcc, vm_pu=1.0, s_sc_max_mva=3000.0, s_sc_min_mva=2000.0,
                       rx_max=0.1, rx_min=0.12, name="GRID")
    for name in ("TR1", "TR2"):
        pp.create_transformer_from_parameters(
            net, pcc, mv1, sn_mva=40.0, vn_hv_kv=110.0, vn_lv_kv=21.0, vkr_percent=0.4,
            vk_percent=12.0, pfe_kw=25.0, i0_percent=0.05, name=name)
    pp.create_line_from_parameters(net, mv1, mv2, length_km=2.0, r_ohm_per_km=0.0754,
                                   x_ohm_per_km=0.11, c_nf_per_km=330.0, max_i_ka=1.2, name="CB1")
    pp.create_load(net, mv2, p_mw=50.0, q_mvar=50.0 * math.tan(math.acos(0.98)), name="DC_LOAD")
    pp.create_sgen(net, mv1, p_mw=0.0, sn_mva=22.0, name="BESS1")
    pp.create_sgen(net, mv1, p_mw=15.0, sn_mva=16.5, name="PV1")
    pp.create_sgen(net, mv2, p_mw=10.0, sn_mva=12.5, name="GEN1")
    return net


def _res(net):
    pp.runpp(net)
    return net


# --------------------------------------------------------------------------
# the build reproduces the network written directly in pandapower
# --------------------------------------------------------------------------

def test_the_built_campus_solves_exactly_as_the_same_network_written_in_pandapower():
    camp = build_campus(campus_spec())
    got, ref = _res(camp.net), _res(direct_net())
    by_name = lambda n, tbl, col: dict(zip(n.bus["name"], n.res_bus[col])) if tbl == "bus" else None
    for col in ("vm_pu", "va_degree"):
        a, b = by_name(got, "bus", col), by_name(ref, "bus", col)
        assert a.keys() == b.keys()
        for k in a:
            assert a[k] == pytest.approx(b[k], abs=1e-9), (col, k)
    assert float(got.res_ext_grid["p_mw"].iloc[0]) == pytest.approx(float(ref.res_ext_grid["p_mw"].iloc[0]), abs=1e-9)
    assert float(got.res_ext_grid["q_mvar"].iloc[0]) == pytest.approx(float(ref.res_ext_grid["q_mvar"].iloc[0]), abs=1e-9)
    assert list(got.res_trafo["loading_percent"]) == pytest.approx(list(ref.res_trafo["loading_percent"]), abs=1e-9)
    assert list(got.res_line["loading_percent"]) == pytest.approx(list(ref.res_line["loading_percent"]), abs=1e-9)


def test_the_load_takes_its_power_factor_and_the_battery_starts_idle():
    net = build_campus(campus_spec()).net
    load = net.load.set_index("name").loc["DC_LOAD"]
    assert load["p_mw"] == 50.0
    assert load["q_mvar"] == pytest.approx(50.0 * math.tan(math.acos(0.98)))
    sgen = net.sgen.set_index("name")
    assert sgen.at["BESS1", "p_mw"] == 0.0           # hourly dispatch sets it (C2)
    assert sgen.at["BESS1", "sn_mva"] == 22.0        # the inverter rating, not the MW
    assert sgen.at["PV1", "p_mw"] == 15.0


# --------------------------------------------------------------------------
# the PCC fault level is the grid operator's figure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("case, sk", [("max", 3000.0), ("min", 2000.0)])
def test_the_pcc_fault_level_with_every_unit_off_is_the_grid_operators_sk(case, sk):
    net = build_campus(campus_spec()).net
    net.sgen["in_service"] = False
    sc.calc_sc(net, case=case, bus=net.bus.index[net.bus["name"] == "PCC"][0])
    ikss = float(net.res_bus_sc["ikss_ka"].iloc[0])
    assert ikss == pytest.approx(sk / (math.sqrt(3) * 110.0), rel=1e-9)


@pytest.mark.parametrize("case, sk, rx", [("max", 3000.0, 0.1), ("min", 2000.0, 0.12)])
def test_the_pcc_peak_current_follows_the_grid_operators_r_over_x(case, sk, rx):
    """IEC 60909 peak current at a bus fed by the grid alone:
    ip = kappa * sqrt(2) * Ik'', kappa = 1.02 + 0.98 * exp(-3 R/X)."""
    net = build_campus(campus_spec()).net
    net.sgen["in_service"] = False
    sc.calc_sc(net, case=case, ip=True, bus=net.bus.index[net.bus["name"] == "PCC"][0])
    ikss = sk / (math.sqrt(3) * 110.0)
    kappa = 1.02 + 0.98 * math.exp(-3 * rx)
    assert float(net.res_bus_sc["ip_ka"].iloc[0]) == pytest.approx(kappa * math.sqrt(2) * ikss, rel=1e-6)


def test_inverters_feed_fault_current_up_to_their_k_and_raise_the_mv_level():
    camp = build_campus(campus_spec())
    net = copy.deepcopy(camp.net)
    sc.calc_sc(net, case="max")
    with_units = dict(zip(net.bus["name"], net.res_bus_sc["ikss_ka"]))
    net = copy.deepcopy(camp.net)
    net.sgen["in_service"] = False
    sc.calc_sc(net, case="max")
    without = dict(zip(net.bus["name"], net.res_bus_sc["ikss_ka"]))
    assert with_units["MV1"] > without["MV1"]
    sgen = camp.net.sgen.set_index("name")
    assert sgen.at["BESS1", "k"] == 1.2 and sgen.at["PV1", "k"] == 1.1
    # a genset is screened as a source of 1/x''d times its rated current (ledgered)
    assert sgen.at["GEN1", "k"] == pytest.approx(1 / 0.15)


# --------------------------------------------------------------------------
# what the build hands downstream
# --------------------------------------------------------------------------

def test_the_units_table_carries_kind_bus_ratings_and_every_value_keeps_its_tag():
    camp = build_campus(campus_spec())
    u = camp.units
    assert set(u.index) == {"DC_LOAD", "BESS1", "PV1", "GEN1"}
    assert u.at["BESS1", "kind"] == "bess" and u.at["BESS1", "e_mwh"] == 40.0
    assert u.at["PV1", "s_mva"] == 16.5 and u.at["DC_LOAD", "bus"] == "MV2"
    p = camp.params.set_index(["element", "param"])
    assert p.at[("PCC", "sk_max_mva"), "source"] == "measured"
    assert p.at[("TR1", "vk_percent"), "source"] == "datasheet"
    assert p.at[("BESS1", "k_sc"), "source"] == "assumed"
    assert set(UNIT_KINDS) == {"load", "bess", "pv", "wind", "genset"}


def test_the_registry_names_the_grid_and_every_generating_unit_by_kind():
    reg = build_campus(campus_spec()).registry
    assert reg.at["GRID", "kind"] == "ext_grid" and reg.at["GRID", "bus"] == "PCC"
    assert reg.at["BESS1", "kind"] == "storage"
    assert reg.at["PV1", "kind"] == "res"
    assert reg.at["GEN1", "kind"] == "genset"
    assert "DC_LOAD" not in reg.index          # loads travel in loads.csv, by bus


def test_a_campus_loads_from_a_yaml_file(tmp_path):
    path = tmp_path / "campus.yaml"
    path.write_text(yaml.safe_dump(campus_spec()))
    a, b = _res(load_campus(path).net), _res(build_campus(campus_spec()).net)
    assert list(a.res_bus["vm_pu"]) == pytest.approx(list(b.res_bus["vm_pu"]), abs=1e-12)


def test_a_missing_file_is_an_error_not_an_empty_campus(tmp_path):
    with pytest.raises(ContractError, match="not found"):
        load_campus(tmp_path / "nope.yaml")


# --------------------------------------------------------------------------
# refusals: a campus that cannot be what it says is refused at load
# --------------------------------------------------------------------------

def _broken(mutate):
    spec = campus_spec()
    mutate(spec["campus"])
    return spec


@pytest.mark.parametrize("mutate, match", [
    (lambda c: c["units"]["PV1"]["p_mw"].pop("source"), "source"),
    (lambda c: c["units"]["PV1"]["p_mw"].update(source="guess"), "unknown source"),
    (lambda c: c["units"]["PV1"].update(p_mw=15.0), "value.*source"),
    (lambda c: c["units"]["PV1"].update(kind="nuclear"), "unknown kind"),
    (lambda c: c["units"]["PV1"].update(bus="MV9"), "unknown bus"),
    (lambda c: c["units"]["PV1"].pop("s_mva"), "s_mva"),
    (lambda c: c["units"]["PV1"].update(s_mva=t(10.0)), "s_mva.*below"),
    (lambda c: c["units"]["DC_LOAD"].update(pf=t(1.2)), "pf"),
    (lambda c: c["units"]["BESS1"].pop("e_mwh"), "e_mwh"),
    (lambda c: c["units"]["GEN1"].update(xd_pp=t(0.0)), "xd_pp"),
    (lambda c: c["units"]["PV1"].update(colour="red"), "unknown field"),
    (lambda c: c["pcc"].update(sk_min_mva=t(4000.0)), "sk_min_mva"),
    (lambda c: c["transformers"]["TR1"].update(lv_bus="PCC"), "same bus"),
    (lambda c: c["transformers"]["TR1"].update(vn_lv_kv=t(33.0)), "vn_lv_kv"),
    (lambda c: c["transformers"]["TR1"].update(vkr_percent=t(13.0)), "vkr_percent"),
    (lambda c: c["cables"]["CB1"].update(to_bus="PCC"), "different nominal voltages"),
    (lambda c: c["buses"].update(MV3={"vn_kv": 20.0}), "not connected"),
    (lambda c: c["units"].update(TR1={"kind": "load", "bus": "MV2", "p_mw": t(1.0), "pf": t(1.0)}), "duplicate"),
    (lambda c: c["units"].update(A_VERY_LONG_NAME={"kind": "load", "bus": "MV2", "p_mw": t(1.0), "pf": t(1.0)}), "12"),
    (lambda c: c["pcc"].update(p_connection_mw=50.0), "value.*source"),
    (lambda c: c["pcc"].update(p_connection_mw=t(0.0)), "positive"),
    (lambda c: c.update(pcc_extra=1), "unknown key"),
    (lambda c: c.pop("pcc"), "pcc"),
])
def test_an_impossible_or_untagged_campus_is_refused(mutate, match):
    with pytest.raises(ContractError, match=match):
        build_campus(_broken(mutate))
