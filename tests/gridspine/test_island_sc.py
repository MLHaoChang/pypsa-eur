"""Fault levels by control mode, grid-connected and islanded, I5.

Oracles worked by hand:
* a genset is a synchronous machine behind x''d with IEC 60909's K_G
  = c_max / (1 + x''d sin φ_rG) (spike 2: 1.589 kA, which also explains the
  first review's 1.563: it used K_G = 1.0261, which is not the IEC value);
* behind its own unit transformer it takes K_S instead;
* an inverter-only island: Σ k·In referred to the fault bus;
* the converter-aware minimum: the gensets' IEC minimum plus every counted
  converter at its minimum current, in phase;
* with the largest source out, the minimum without it.
"""
import copy
import math

import pandapower as pp
import pandapower.shortcircuit as sc
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static import island_sc as ISC
from gridspine.static.campus_sc import campus_fault_levels
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_schema import genset, gfm_bess


def a_genset_island(rx=0.07, with_unit_trafo=False, oltc=False):
    """A 33 kV PCC bus, a 12.5 MVA genset (x''d 0.15, cos φ 0.8) on it, or
    on an 11 kV bus behind its own 15 MVA 33/11 kV transformer."""
    g = genset()
    g["unit_transformer"] = with_unit_trafo
    g["unit_transformer_oltc"] = oltc
    c = {"name": "GEN_ISLAND", "f_hz": 50,
         "pcc": {"bus": "PCC", "vn_kv": 33.0, "vm_pu": t(1.0), "sk_max_mva": t(500.0), "sk_min_mva": t(400.0),
                 "rx_max": t(0.1), "rx_min": t(0.1)},
         "buses": {}, "units": {}}
    gbus = "PCC"
    if with_unit_trafo:
        c["buses"]["GLV"] = {"vn_kv": 11.0}
        c["transformers"] = {"UT": {"hv_bus": "PCC", "lv_bus": "GLV", "sn_mva": t(15.0), "vn_hv_kv": t(33.0),
                                    "vn_lv_kv": t(11.0), "vk_percent": t(10.0), "vkr_percent": t(0.5),
                                    "pfe_kw": t(0.0), "i0_percent": t(0.0)}}
        gbus = "GLV"
    c["units"]["G"] = {"kind": "genset", "bus": gbus, "p_mw": t(10.0), "s_mva": t(12.5), "xd_pp": t(0.15),
                       "rx_sc": t(rx), "island": g}
    return {"campus": c}


def ik_genset_by_hand(c, un_kv=33.0, sn=12.5, xd=0.15, rx=0.07, cos_phi=0.8):
    kg = 1.1 / (1.0 + xd * math.sqrt(1.0 - cos_phi ** 2))
    x_ohm = xd * un_kv ** 2 / sn
    z = kg * x_ohm * math.hypot(1.0, rx)
    return c * un_kv / (math.sqrt(3) * z)


def bus_row(df, bus):
    return df.set_index("bus").loc[bus]


def test_a_genset_island_follows_iec_with_k_g_and_explains_the_review_residual():
    fl = ISC.island_fault_levels(build_campus(a_genset_island()))
    row = bus_row(fl, "PCC")
    assert row.ikss_max_ka == pytest.approx(ik_genset_by_hand(1.1), rel=1e-6)
    assert row.ikss_min_ka == pytest.approx(ik_genset_by_hand(1.0), rel=1e-6)
    # spike 2 (R = 0.01 ohm, so rx = 0.01 / 13.068): 1.589 kA, with K_G = 1.0092
    spike = ISC.island_fault_levels(build_campus(a_genset_island(rx=0.01 / (0.15 * 33 ** 2 / 12.5))))
    assert bus_row(spike, "PCC").ikss_max_ka == pytest.approx(1.589, abs=5e-4)


def _ps_unit_by_hand(k):
    """Z = K (t_r^2 Z_G + Z_THV) for the 12.5 MVA genset at 11 kV behind 15 MVA
    33/11 kV, vk 10 %, vkr 0.5 %, and Ik'' = c_max U_nQ / (sqrt 3 |Z|)."""
    xt = math.sqrt(0.10 ** 2 - 0.005 ** 2)
    zg = complex(0.07, 1.0) * 0.15 * 11.0 ** 2 / 12.5            # ohm at 11 kV
    zt = complex(0.005, xt) * 33.0 ** 2 / 15.0                    # ohm at 33 kV
    return 1.1 * 33.0 / (math.sqrt(3) * abs(k * ((33.0 / 11.0) ** 2 * zg + zt)))


def test_behind_its_unit_transformer_without_an_oltc_a_genset_takes_k_so():
    """IEC 60909-0 power station unit without an on-load tap changer:
    K_SO = (U_nQ/U_rG)(U_rTLV/U_rTHV) c_max / (1 + x''d sin phi) = 1.0092."""
    fl = ISC.island_fault_levels(build_campus(a_genset_island(with_unit_trafo=True)))
    kso = (33.0 / 11.0) * (11.0 / 33.0) * 1.1 / (1.0 + 0.15 * 0.6)
    assert bus_row(fl, "PCC").ikss_max_ka == pytest.approx(_ps_unit_by_hand(kso), rel=1e-4)


def test_behind_a_unit_transformer_with_an_oltc_a_genset_takes_k_s():
    """K_S = (U_nQ/U_rG)^2 (U_rTLV/U_rTHV)^2 c_max / (1 + |x''d - x_T| sin phi)."""
    fl = ISC.island_fault_levels(build_campus(a_genset_island(with_unit_trafo=True, oltc=True)))
    xt = math.sqrt(0.10 ** 2 - 0.005 ** 2)
    ks = (33.0 / 11.0) ** 2 * (11.0 / 33.0) ** 2 * 1.1 / (1.0 + abs(0.15 - xt) * 0.6)
    assert bus_row(fl, "PCC").ikss_max_ka == pytest.approx(_ps_unit_by_hand(ks), rel=1e-4)


def two_converters(control2="gfm_droop"):
    """Two 22 MVA converters on a 20 kV island bus: B1 GFM, B2 GFM or GFL."""
    b1 = gfm_bess()
    b1["i_max_pu"], b1["k_gfm_min"] = t(1.5), t(1.1)
    b2 = gfm_bess() if control2 == "gfm_droop" else {"control": "gfl_pq", "pf_rated": t(0.95)}
    return {"campus": {
        "name": "INV_ISLAND", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 20.0, "vm_pu": t(1.0), "sk_max_mva": t(300.0), "sk_min_mva": t(200.0),
                "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {"A": {"vn_kv": 20.0}},
        "cables": {"F": {"from_bus": "PCC", "to_bus": "A", "length_km": t(2.0), "r_ohm_per_km": t(0.1),
                         "x_ohm_per_km": t(0.1), "c_nf_per_km": t(0.0), "max_i_ka": t(1.0)}},
        "units": {
            "B1": {"kind": "bess", "bus": "PCC", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                   "k_sc": t(1.1), "rx_sc": t(0.1), "island": b1},
            "B2": {"kind": "pv", "bus": "A", "p_mw": t(20.0), "s_mva": t(22.0), "k_sc": t(1.1), "rx_sc": t(0.1),
                   "island": b2},
        }}}


IN_20 = 22.0 / (math.sqrt(3) * 20.0)          # kA rated current of a 22 MVA unit at 20 kV


def test_an_inverter_only_island_is_the_sum_of_the_converter_currents():
    fl = ISC.island_fault_levels(build_campus(two_converters()))
    for bus in ("PCC", "A"):
        row = bus_row(fl, bus)
        assert row.ikss_max_ka == pytest.approx((1.5 + 1.2) * IN_20, rel=1e-9)     # i_max of each GFM
        assert row.ikss_min_ka == pytest.approx((1.1 + 1.0) * IN_20, rel=1e-9)     # k_gfm_min of each
        assert row.method_min == "converter-aware (not IEC 60909)"


def test_a_gfl_converter_is_dropped_from_the_minimum_unless_kept():
    camp = build_campus(two_converters(control2="gfl_pq"))
    dropped = bus_row(ISC.island_fault_levels(camp), "A")
    assert dropped.ikss_max_ka == pytest.approx((1.5 + 1.1) * IN_20, rel=1e-9)     # GFL at k_sc in the max
    assert dropped.ikss_min_ka == pytest.approx(1.1 * IN_20, rel=1e-9)
    kept = bus_row(ISC.island_fault_levels(camp, gfl_min_case="keep"), "A")
    assert kept.ikss_min_ka == pytest.approx((1.1 + 1.1) * IN_20, rel=1e-9)


def test_with_the_largest_source_out_the_minimum_loses_it():
    camp = build_campus(two_converters())
    row = bus_row(ISC.island_fault_levels(camp), "A")
    assert row.largest_out in ("B1", "B2")
    rest = {"B1": 1.0, "B2": 1.1}[row.largest_out]                 # the other one's k_gfm_min
    assert row.ikss_min_n1_ka == pytest.approx(rest * IN_20, rel=1e-9)


def test_a_mixed_island_minimum_is_the_genset_minimum_plus_the_converters_in_phase():
    s = a_genset_island()
    b = gfm_bess()
    s["campus"]["units"]["B"] = {"kind": "bess", "bus": "PCC", "p_mw": t(5.0), "e_mwh": t(5.0), "s_mva": t(6.0),
                                 "k_sc": t(1.1), "rx_sc": t(0.1), "island": b}
    row = bus_row(ISC.island_fault_levels(build_campus(s)), "PCC")
    in_b = 6.0 / (math.sqrt(3) * 33.0)
    assert row.ikss_min_ka == pytest.approx(ik_genset_by_hand(1.0) + 1.0 * in_b, rel=1e-6)
    assert row.ikss_max_ka > ik_genset_by_hand(1.1)                 # the converter adds to the max


def test_pandapower_still_leaves_converters_out_of_its_minimum():
    """IEC 60909-0:2016 lets full-converter sources be neglected in the min
    case, and pandapower does (spike 3). The converter-aware minimum exists
    because of it; if pandapower ever changes, this fails and the module's
    minimum must be revisited."""
    net = pp.create_empty_network(sn_mva=1.0)
    a = pp.create_bus(net, 33.0)
    pp.create_gen(net, a, p_mw=0.0, vm_pu=1.0, sn_mva=12.5, vn_kv=33.0, xdss_pu=0.15, rdss_ohm=0.01, cos_phi=0.8)
    pp.create_sgen(net, a, p_mw=0.0, sn_mva=10.0, k=1.2, rx=0.1, generator_type="current_source")
    sc.calc_sc(net, case="min")
    with_sgen = float(net.res_bus_sc.at[a, "ikss_ka"])
    net.sgen["in_service"] = False
    sc.calc_sc(net, case="min")
    assert with_sgen == pytest.approx(float(net.res_bus_sc.at[a, "ikss_ka"]), rel=1e-12)


def test_a_ups_feeds_no_fault_in_the_island_either():
    s = two_converters()
    s["campus"]["units"]["L"] = {"kind": "load", "bus": "A", "p_mw": t(10.0), "pf": t(0.95)}
    s["campus"]["units"]["U"] = {"kind": "ups", "bus": "A", "p_mw": t(10.0), "e_mwh": t(2.5), "s_mva": t(11.0),
                                 "island": {"it_load": ["L"], "walk_in_s": t(15.0), "f_window_hz": t(2.0)}}
    with_ups = bus_row(ISC.island_fault_levels(build_campus(s)), "A")
    assert with_ups.ikss_max_ka == pytest.approx((1.5 + 1.2) * IN_20, rel=1e-9)


# ── grid-connected (campus_sc) ─────────────────────────────────────────────

def test_grid_connected_converters_are_unchanged_by_the_genset_model():
    """Without a genset installed, campus_fault_levels is pandapower on the
    campus net exactly as before I5."""
    camp = build_campus(campus_spec())
    fl = campus_fault_levels(camp, installed={"BESS1", "PV1"}).set_index("bus")
    net = copy.deepcopy(camp.net)
    net.sgen["in_service"] = net.sgen["name"].isin({"BESS1", "PV1"})
    sc.calc_sc(net, case="max")
    for name, ik in zip(net.bus["name"], net.res_bus_sc["ikss_ka"]):
        assert fl.at[name, "ikss_max_ka"] == pytest.approx(ik, rel=1e-9)


def test_grid_connected_a_genset_is_a_synchronous_machine_not_a_current_source():
    """The C1 ledger line retired: GEN1 (10 MW, 12.5 MVA, x''d 0.15, R/X 0.07)
    is a pandapower gen with K_G, built here independently of the module."""
    camp = build_campus(campus_spec())
    fl = campus_fault_levels(camp, installed={"GEN1"}).set_index("bus")
    net = copy.deepcopy(camp.net)
    i = net.sgen.index[net.sgen["name"] == "GEN1"][0]
    bus = int(net.sgen.at[i, "bus"])
    net.sgen["in_service"] = False
    x_ohm = 0.15 * 20.0 ** 2 / 12.5
    pp.create_gen(net, bus, p_mw=0.0, vm_pu=1.0, sn_mva=12.5, vn_kv=20.0, xdss_pu=0.15,
                  rdss_ohm=0.07 * x_ohm, cos_phi=0.8, name="GEN1")
    sc.calc_sc(net, case="max")
    for name, ik in zip(net.bus["name"], net.res_bus_sc["ikss_ka"]):
        assert fl.at[name, "ikss_max_ka"] == pytest.approx(ik, rel=1e-9)
    # and it differs from the old 1/x''d current source, which ignored K_G and the network
    old = copy.deepcopy(camp.net)
    old.sgen["in_service"] = old.sgen["name"] == "GEN1"
    sc.calc_sc(old, case="max")
    mv2 = list(net.bus["name"]).index("MV2")
    assert float(old.res_bus_sc["ikss_ka"].iloc[mv2]) != pytest.approx(fl.at["MV2", "ikss_max_ka"], rel=1e-3)


def test_grid_connected_a_gfm_converter_feeds_its_current_limit():
    s = campus_spec()
    b = gfm_bess()
    b["i_max_pu"] = t(1.5)
    s["campus"]["units"]["BESS1"]["island"] = b
    camp = build_campus(s)
    fl = campus_fault_levels(camp, installed={"BESS1"}).set_index("bus")
    net = copy.deepcopy(camp.net)
    net.sgen["in_service"] = net.sgen["name"] == "BESS1"
    net.sgen.loc[net.sgen["name"] == "BESS1", "k"] = 1.5
    sc.calc_sc(net, case="max")
    for name, ik in zip(net.bus["name"], net.res_bus_sc["ikss_ka"]):
        assert fl.at[name, "ikss_max_ka"] == pytest.approx(ik, rel=1e-9)


def test_grid_connected_a_ups_feeds_no_fault():
    s = campus_spec()
    s["campus"]["units"]["UPS1"] = {"kind": "ups", "bus": "MV2", "p_mw": t(20.0), "e_mwh": t(5.0), "s_mva": t(22.0),
                                    "island": {"it_load": ["DC_LOAD"], "walk_in_s": t(15.0), "f_window_hz": t(2.0)}}
    camp = build_campus(s)
    with_ups = campus_fault_levels(camp, installed={"BESS1", "UPS1"}).set_index("bus")
    without = campus_fault_levels(camp, installed={"BESS1"}).set_index("bus")
    for bus in with_ups.index:
        assert with_ups.at[bus, "ikss_max_ka"] == pytest.approx(without.at[bus, "ikss_max_ka"], rel=1e-12)
