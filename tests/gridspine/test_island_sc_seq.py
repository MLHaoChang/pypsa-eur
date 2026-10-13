"""Unbalanced faults by sequence networks, I5b (plan 2026-10-07-campus-island-operation).

Oracles:
* by hand, the symmetrical-component formulas: Ik1 = sqrt 3 c Un / |Z1 + Z2 + Z0|
  and Ik2 = c Un / |Z1 + Z2|, with IEC's K_G and K_T;
* pandapower's ``fault="1ph"`` and ``"2ph"`` where they are valid: solidly
  earthed, no converters, no gensets;
* I5 (pandapower's IEC 60909 and I5's own solve) for the 3-phase fault, which
  the same solver computes;
* a converter as IEC 60909-0:2016 counts it: in phase, through its
  distribution factor; with no zero-sequence path it adds nothing to an earth
  fault (pandapower adds it there, spike 6, pinned).
"""
import copy
import math

import pandapower as pp
import pandapower.shortcircuit as sc
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static import island_sc as ISC
from gridspine.static import island_sc_seq as SEQ
from gridspine.static.campus_sc import campus_fault_levels
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_island_relays import feeder
from tests.gridspine.test_island_sc import a_genset_island, two_converters
from tests.gridspine.test_island_schema import gfm_bess

SQ3 = math.sqrt(3.0)


def row(df, bus):
    return df.set_index("bus").loc[bus]


def earthed_campus(load=True, cable_zero=True):
    """110 kV grid -> 40 MVA 110/20 kV Dyn11 (LV solid) -> MV1 -> 2 km -> MV2."""
    cable = {"from_bus": "MV1", "to_bus": "MV2", "length_km": t(2.0), "r_ohm_per_km": t(0.1),
             "x_ohm_per_km": t(0.11), "c_nf_per_km": t(0.0), "max_i_ka": t(1.0)}
    if cable_zero:
        cable.update(r0_ohm_per_km=t(0.8), x0_ohm_per_km=t(0.35), c0_nf_per_km=t(0.0))
    return {"campus": {
        "name": "EARTHED", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 110.0, "vm_pu": t(1.0), "sk_max_mva": t(3000.0), "sk_min_mva": t(2000.0),
                "rx_max": t(0.1), "rx_min": t(0.12), "x0x_max": t(1.2), "r0x0_max": t(0.1), "x0x_min": t(1.4),
                "r0x0_min": t(0.12)},
        "buses": {"MV1": {"vn_kv": 20.0}, "MV2": {"vn_kv": 20.0}},
        "transformers": {"T1": {"hv_bus": "PCC", "lv_bus": "MV1", "sn_mva": t(40.0), "vn_hv_kv": t(110.0),
                                "vn_lv_kv": t(20.0), "vk_percent": t(12.0), "vkr_percent": t(0.4), "pfe_kw": t(0.0),
                                "i0_percent": t(0.0), "vector_group": "Dyn11", "earthing_lv": "solid"}},
        "cables": {"CB1": cable},
        "units": {"L": {"kind": "load", "bus": "MV2", "p_mw": t(10.0), "pf": t(0.95)}} if load else {},
    }}


# ── by hand ──────────────────────────────────────────────────────────────────

def _grid_and_t1(case):
    """Z1 and Z0 at MV1 in ohm at 20 kV: the grid behind the Dyn11 (only in
    Z1: the delta blocks its zero sequence) and K_T Zk."""
    c = 1.1 if case == "max" else 1.0
    s, rx = (3000.0, 0.1) if case == "max" else (2000.0, 0.12)
    zq = c * 20.0 ** 2 / s                       # referred to 20 kV at the rated ratio
    xq = zq / math.hypot(1.0, rx)
    xt_own = math.sqrt(0.12 ** 2 - 0.004 ** 2)
    kt = 0.95 * 1.1 / (1.0 + 0.6 * xt_own)
    zt = kt * complex(0.004, xt_own) * 20.0 ** 2 / 40.0
    return complex(rx * xq, xq) + zt, zt, c


@pytest.mark.parametrize("case", ["max", "min"])
def test_an_earth_fault_behind_a_dyn11_follows_the_sequence_formula(case):
    z1, z0, c = _grid_and_t1(case)
    fl = row(SEQ.unbalanced_fault_levels(build_campus(earthed_campus()), island=False), "MV1")
    assert fl[f"ik1_{case}_ka"] == pytest.approx(SQ3 * c * 20.0 / abs(2 * z1 + z0), rel=1e-9)
    assert fl[f"ik2_{case}_ka"] == pytest.approx(c * 20.0 / abs(2 * z1), rel=1e-9)
    assert bool(fl["earth_path"]) and fl["note"] == ""


def test_the_residual_current_flows_in_the_transformer_star_and_the_faulted_cable():
    camp = build_campus(earthed_campus())
    sn = SEQ.sequence_net(camp, ISC._sources(camp, None), island=False, case="max")
    res = SEQ.solve_fault(sn, "MV2", "1ph")
    br = res.branch.set_index("branch")["i_e_ka"]
    assert res.earth_ka == pytest.approx(res.ik_ka, rel=1e-12)            # 3 I0 is the fault current
    assert br["T1"] == pytest.approx(res.ik_ka, rel=1e-9)                  # back through the LV star
    assert br["CB1"] == pytest.approx(res.ik_ka, rel=1e-9)


# ── pandapower, where it is valid ─────────────────────────────────────────────

def _pandapower_twin(camp):
    net = copy.deepcopy(camp.net)
    net.ext_grid["x0x_max"], net.ext_grid["r0x0_max"] = 1.2, 0.1
    net.ext_grid["x0x_min"], net.ext_grid["r0x0_min"] = 1.4, 0.12
    net.trafo["vector_group"], net.trafo["vk0_percent"], net.trafo["vkr0_percent"] = "Dyn", 12.0, 0.4
    net.trafo["mag0_percent"], net.trafo["mag0_rx"], net.trafo["si0_hv_partial"] = 1e9, 0.0, 0.9
    net.line["r0_ohm_per_km"], net.line["x0_ohm_per_km"], net.line["c0_nf_per_km"] = 0.8, 0.35, 0.0
    return net


@pytest.mark.parametrize("fault", ["1ph", "2ph"])
@pytest.mark.parametrize("case", ["max", "min"])
def test_solidly_earthed_without_converters_pandapower_agrees(fault, case):
    camp = build_campus(earthed_campus())
    net = _pandapower_twin(camp)
    sc.calc_sc(net, fault=fault, case=case)
    ours = SEQ.unbalanced_fault_levels(camp, island=False).set_index("bus")[f"ik{fault[0]}_{case}_ka"]
    for i, bus in zip(net.bus.index, net.bus["name"]):
        assert ours[bus] == pytest.approx(float(net.res_bus_sc.at[i, "ikss_ka"]), rel=1e-6), bus


def test_pandapower_adds_converter_current_where_there_is_no_earth_path():
    """Spike 6, pinned: a 1-phase fault on the unearthed delta of a YNd
    transformer. pandapower gives the converter's k In; there is no path, and
    the sequence networks give 0. An upgrade that fixes pandapower fails here."""
    def pandapower_ik1(k):
        n = pp.create_empty_network(sn_mva=1.0)
        hv, lv = pp.create_bus(n, 110.0), pp.create_bus(n, 20.0)
        pp.create_ext_grid(n, hv, s_sc_max_mva=3000, rx_max=0.1, x0x_max=1.0, r0x0_max=0.1)
        pp.create_transformer_from_parameters(n, hv, lv, sn_mva=40, vn_hv_kv=110, vn_lv_kv=20, vk_percent=12,
                                              vkr_percent=0.4, pfe_kw=0, i0_percent=0, vector_group="YNd",
                                              vk0_percent=12, vkr0_percent=0.4, mag0_percent=1e9, mag0_rx=0,
                                              si0_hv_partial=0.9)
        if k:
            pp.create_sgen(n, lv, p_mw=0, sn_mva=22, k=k, rx=0.1, generator_type="current_source")
        sc.calc_sc(n, fault="1ph", case="max")
        return float(n.res_bus_sc.at[lv, "ikss_ka"])

    assert pandapower_ik1(0.0) == pytest.approx(0.0, abs=1e-9)
    assert pandapower_ik1(1.2) == pytest.approx(1.2 * 22.0 / (SQ3 * 20.0), rel=1e-6)
    s = earthed_campus()
    s["campus"]["transformers"]["T1"].update(vector_group="YNd11", earthing_hv="solid")
    del s["campus"]["transformers"]["T1"]["earthing_lv"]
    s["campus"]["units"]["PV"] = {"kind": "pv", "bus": "MV1", "p_mw": t(20.0), "s_mva": t(22.0), "k_sc": t(1.2),
                                  "rx_sc": t(0.1)}
    fl = row(SEQ.unbalanced_fault_levels(build_campus(s), island=False), "MV1")
    assert fl["ik1_max_ka"] == 0.0 and not fl["earth_path"] and fl["note"] == SEQ.NO_EARTH


# ── the 3-phase fault, against I5 ────────────────────────────────────────────

@pytest.mark.parametrize("spec", [
    a_genset_island(), a_genset_island(with_unit_trafo=True), a_genset_island(with_unit_trafo=True, oltc=True),
    two_converters(), two_converters("gfl"), feeder([], genset_at_a=True),
], ids=["genset", "unit_kso", "unit_ks", "two_gfm", "gfm_gfl", "genset_and_gfm"])
def test_the_three_phase_fault_is_i5s_maximum(spec):
    camp = build_campus(spec)
    i5 = ISC.island_fault_levels(camp).set_index("bus")
    ours = SEQ.unbalanced_fault_levels(camp).set_index("bus")
    for bus in i5.index:
        assert ours.at[bus, "ik3_max_ka"] == pytest.approx(i5.at[bus, "ikss_max_ka"], rel=1e-9), bus


@pytest.mark.parametrize("spec", [a_genset_island(), a_genset_island(with_unit_trafo=True, oltc=True),
                                  two_converters(), two_converters("gfl")], ids=["genset", "unit_ks", "gfm", "gfl"])
def test_the_three_phase_minimum_is_i5s_where_no_converter_meets_a_machine(spec):
    camp = build_campus(spec)
    i5 = ISC.island_fault_levels(camp).set_index("bus")
    ours = SEQ.unbalanced_fault_levels(camp).set_index("bus")
    for bus in i5.index:
        assert ours.at[bus, "ik3_min_ka"] == pytest.approx(i5.at[bus, "ikss_min_ka"], rel=1e-9), bus


def test_beside_a_genset_a_converter_feeds_the_fault_through_its_distribution_factor():
    """I5's own minimum sends every converter ampere into the fault; IEC's
    distribution factor sends some into the genset when the converter is not
    between them. At the feeder's end that is the 0.5 % difference."""
    camp = build_campus(feeder([], genset_at_a=True))
    i5 = row(ISC.island_fault_levels(camp), "C")
    ours = row(SEQ.unbalanced_fault_levels(camp), "C")
    assert ours["ik3_min_ka"] < i5["ikss_min_ka"]
    assert ours["ik3_min_ka"] == pytest.approx(i5["ikss_min_ka"], rel=0.01)


def test_grid_connected_the_three_phase_fault_is_pandapowers_with_off_nominal_transformers():
    camp = build_campus(campus_spec())                    # 110/21 kV on a 20 kV bus, gensets and converters
    pp_levels = campus_fault_levels(camp, set(camp.units.index)).set_index("bus")
    ours = SEQ.unbalanced_fault_levels(camp, island=False).set_index("bus")
    for bus in pp_levels.index:
        assert ours.at[bus, "ik3_max_ka"] == pytest.approx(pp_levels.at[bus, "ikss_max_ka"], rel=1e-9), bus
        assert ours.at[bus, "ik3_min_ka"] == pytest.approx(pp_levels.at[bus, "ikss_min_ka"], rel=1e-9), bus


# ── gensets ──────────────────────────────────────────────────────────────────

def genset_island(earthing="solid", r_n=None, x2=0.17, x0=0.06, cable=False):
    s = a_genset_island()
    g = s["campus"]["units"]["G"]["island"]
    g["neutral_earthing"] = earthing
    if r_n is not None:
        g["r_n_ohm"] = t(r_n)
    if x2 is not None:
        g["x2_pu"] = t(x2)
    if x0 is not None:
        g["x0_pu"] = t(x0)
    if cable:
        s["campus"]["buses"]["FAR"] = {"vn_kv": 33.0}
        s["campus"]["cables"] = {"CF": {"from_bus": "PCC", "to_bus": "FAR", "length_km": t(5.0),
                                        "r_ohm_per_km": t(0.1), "x_ohm_per_km": t(0.1), "c_nf_per_km": t(250.0),
                                        "max_i_ka": t(1.0), "c0_nf_per_km": t(200.0)}}
    return s


def _genset_z(x, kg=1.1 / (1.0 + 0.15 * 0.6), rx=0.07):
    return kg * complex(rx * x, x) * 33.0 ** 2 / 12.5


@pytest.mark.parametrize("case, c", [("max", 1.1), ("min", 1.0)])
def test_a_solidly_earthed_genset_feeds_an_earth_fault_by_hand(case, c):
    fl = row(SEQ.unbalanced_fault_levels(build_campus(genset_island())), "PCC")
    z1, z2, z0 = _genset_z(0.15), _genset_z(0.17), _genset_z(0.06)          # K_G on all three (IEC 60909-0)
    assert fl[f"ik1_{case}_ka"] == pytest.approx(SQ3 * c * 33.0 / abs(z1 + z2 + z0), rel=1e-9)
    assert fl[f"ik2_{case}_ka"] == pytest.approx(c * 33.0 / abs(z1 + z2), rel=1e-9)


def test_a_neutral_resistor_adds_three_times_itself_without_correction():
    fl = row(SEQ.unbalanced_fault_levels(build_campus(genset_island("resistance", r_n=20.0))), "PCC")
    z = _genset_z(0.15) + _genset_z(0.17) + _genset_z(0.06) + 3 * 20.0
    assert fl["ik1_max_ka"] == pytest.approx(SQ3 * 1.1 * 33.0 / abs(z), rel=1e-9)


def test_a_genset_without_its_sequence_reactances_takes_x2_xd_and_x0_04_xd():
    fl = row(SEQ.unbalanced_fault_levels(build_campus(genset_island(x2=None, x0=None))), "PCC")
    z = _genset_z(0.15) * 2 + _genset_z(0.4 * 0.15)
    assert fl["ik1_max_ka"] == pytest.approx(SQ3 * 1.1 * 33.0 / abs(z), rel=1e-9)


def test_an_isolated_neutral_gives_no_earth_fault_current_only_the_capacitive_one():
    fl = SEQ.unbalanced_fault_levels(build_campus(genset_island("isolated", cable=True))).set_index("bus")
    for bus in ("PCC", "FAR"):
        assert fl.at[bus, "ik1_max_ka"] == 0.0 and fl.at[bus, "ik1_min_ka"] == 0.0
        assert not fl.at[bus, "earth_path"] and fl.at[bus, "note"] == SEQ.NO_EARTH
        # sqrt 3 omega C0 Un, C0 = 200 nF/km x 5 km
        assert fl.at[bus, "ic_e_ka"] == pytest.approx(SQ3 * 2 * math.pi * 50 * 200e-9 * 5.0 * 33.0, rel=1e-12)
    # the two-phase fault does not need the earth
    assert fl.at["PCC", "ik2_max_ka"] == pytest.approx(1.1 * 33.0 / abs(_genset_z(0.15) + _genset_z(0.17)), rel=1e-9)


def test_a_cable_without_zero_sequence_data_is_drafted():
    s = genset_island(cable=True)
    camp = build_campus(s)
    fl = row(SEQ.unbalanced_fault_levels(camp), "FAR")
    zc1 = complex(0.1, 0.1) * 5.0
    zc0 = complex(0.1 + 3 * SEQ.R_SCREEN_OHM_PER_KM, 0.1) * 5.0
    z = _genset_z(0.15) + zc1 + _genset_z(0.17) + zc1 + _genset_z(0.06) + zc0
    assert fl["ik1_max_ka"] == pytest.approx(SQ3 * 1.1 * 33.0 / abs(z), rel=1e-9)
    # the min case takes the cable at 80 degC, R x 1.24, on both
    f_t = 1.0 + 0.004 * 60.0
    zc1, zc0 = complex(0.1 * f_t, 0.1) * 5.0, complex((0.1 + 3 * 0.727) * f_t, 0.1) * 5.0
    z = _genset_z(0.15) + zc1 + _genset_z(0.17) + zc1 + _genset_z(0.06) + zc0
    assert fl["ik1_min_ka"] == pytest.approx(SQ3 * 1.0 * 33.0 / abs(z), rel=1e-9)


# ── converters ───────────────────────────────────────────────────────────────

IN_20 = 22.0 / (SQ3 * 20.0)


def converter_island(control="gfm", neg_seq=None, earthing_x0=10.0):
    """One 22 MVA converter (GFM i_max 1.2, k_gfm_min 1.0, or GFL k_sc 1.1) on
    a 20 kV bus earthed by an earthing transformer (x0 10 ohm)."""
    block = gfm_bess() if control == "gfm" else {"control": "gfl_pq", "pf_rated": t(0.95)}
    if neg_seq is not None:
        block["neg_seq"] = t(neg_seq)
    s = {"campus": {
        "name": "INV", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 20.0, "vm_pu": t(1.0), "sk_max_mva": t(300.0), "sk_min_mva": t(200.0),
                "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {},
        "units": {"B": {"kind": "bess", "bus": "PCC", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                        "k_sc": t(1.1), "rx_sc": t(0.1), "island": block}}}}
    if earthing_x0:
        s["campus"]["earthing_transformers"] = {"ET": {"bus": "PCC", "x0_ohm": t(earthing_x0)}}
    return s


def test_positive_sequence_converters_alone_cannot_carry_an_unbalanced_fault():
    fl = row(SEQ.unbalanced_fault_levels(build_campus(converter_island("gfl"))), "PCC")
    assert math.isnan(fl["ik1_max_ka"]) and math.isnan(fl["ik2_max_ka"])
    assert SEQ.NO_NEG in fl["note"]
    assert fl["ik3_max_ka"] == pytest.approx(1.1 * IN_20, rel=1e-9)      # the balanced fault they can


@pytest.mark.parametrize("k2", [0.5, 2.0, 10.0])
def test_with_neg_seq_a_converter_keeps_its_peak_current_at_its_limit(k2):
    """All of I1 and I2 pass through the converter: |I1| + |I2| = i_max In with
    I1 = I2 (earth fault) or I1 = -I2 (two-phase), so I1 = i_max In / 2 for
    any k2. Ik1 = 3 I1, Ik2 = sqrt 3 I1."""
    fl = row(SEQ.unbalanced_fault_levels(build_campus(converter_island(neg_seq=k2))), "PCC")
    assert fl["ik1_max_ka"] == pytest.approx(3 * 0.6 * IN_20, rel=1e-9)
    assert fl["ik2_max_ka"] == pytest.approx(SQ3 * 0.6 * IN_20, rel=1e-9)
    assert fl["ik1_min_ka"] == pytest.approx(3 * 0.5 * IN_20, rel=1e-9)        # k_gfm_min 1.0


def test_beside_a_genset_a_converter_adds_to_an_earth_fault_as_iec_counts_it():
    """IEC 60909-0:2016: Ik1 = sqrt 3 c Un / |Z| + 3 |Z1| / |Z| * I_conv, with
    |Z| = |Z1 + Z2 + Z0| and the converter at the faulted bus."""
    s = genset_island()
    s["campus"]["units"]["PV"] = {"kind": "pv", "bus": "PCC", "p_mw": t(10.0), "s_mva": t(11.0), "k_sc": t(1.2),
                                  "rx_sc": t(0.1)}
    fl = row(SEQ.unbalanced_fault_levels(build_campus(s)), "PCC")
    z1, z2, z0 = _genset_z(0.15), _genset_z(0.17), _genset_z(0.06)
    z = abs(z1 + z2 + z0)
    i_conv = 1.2 * 11.0 / (SQ3 * 33.0)
    assert fl["ik1_max_ka"] == pytest.approx(SQ3 * 1.1 * 33.0 / z + 3 * abs(z1) / z * i_conv, rel=1e-9)
    assert fl["ik2_max_ka"] == pytest.approx(1.1 * 33.0 / abs(z1 + z2) + SQ3 * abs(z1) / abs(z1 + z2) * i_conv,
                                             rel=1e-9)


def test_without_an_earth_path_a_converter_adds_nothing_to_an_earth_fault():
    s = genset_island("isolated")
    s["campus"]["units"]["PV"] = {"kind": "pv", "bus": "PCC", "p_mw": t(10.0), "s_mva": t(11.0), "k_sc": t(1.2),
                                  "rx_sc": t(0.1)}
    fl = row(SEQ.unbalanced_fault_levels(build_campus(s)), "PCC")
    assert fl["ik1_max_ka"] == 0.0 and fl["ik1_min_ka"] == 0.0


def test_an_earthing_transformer_is_an_earth_path_in_the_zero_sequence_only():
    s = genset_island("isolated")
    s["campus"]["earthing_transformers"] = {"ET": {"bus": "PCC", "x0_ohm": t(30.0), "r_n_ohm": t(10.0)}}
    camp = build_campus(s)
    fl = row(SEQ.unbalanced_fault_levels(camp), "PCC")
    z1, z2 = _genset_z(0.15), _genset_z(0.17)
    assert fl["ik1_max_ka"] == pytest.approx(SQ3 * 1.1 * 33.0 / abs(z1 + z2 + complex(30.0, 30.0)), rel=1e-9)
    assert fl["ik3_max_ka"] == pytest.approx(row(ISC.island_fault_levels(camp), "PCC")["ikss_max_ka"], rel=1e-9)


def test_an_unknown_fault_kind_is_refused():
    camp = build_campus(genset_island())
    with pytest.raises(ValueError, match="fault"):
        SEQ.solve_fault(SEQ.sequence_net(camp, ISC._sources(camp, None)), "PCC", "3ph-e")


# ── the closing tests (mutation round 1) ─────────────────────────────────────

def test_the_voltage_factor_follows_iec_and_pandapower():
    assert SEQ.c_factor(20.0, "max") == SEQ.c_factor(0.4, "max") == 1.1
    assert SEQ.c_factor(20.0, "min") == 1.0
    assert SEQ.c_factor(0.4, "min") == 0.95


@pytest.mark.parametrize("oltc", [False, True])
def test_grid_connected_a_unit_genset_is_pandapowers_inside_and_outside_its_unit(oltc):
    camp = build_campus(a_genset_island(with_unit_trafo=True, oltc=oltc))
    pp_levels = campus_fault_levels(camp, set(camp.units.index)).set_index("bus")
    ours = SEQ.unbalanced_fault_levels(camp, island=False).set_index("bus")
    for bus in ("PCC", "GLV"):                    # GLV: inside the unit, K_G and the transformer uncorrected
        for case in ("max", "min"):
            assert ours.at[bus, f"ik3_{case}_ka"] == pytest.approx(pp_levels.at[bus, f"ikss_{case}_ka"], rel=1e-9)


def transformer_campus(vector_group, earthing_hv=None, r_n_hv=None, earthing_lv=None):
    """110 kV grid (Sk'' 3000 MVA, R/X 0.1, zero sequence by default) -> 40 MVA
    110/21 kV on a 20 kV bus (an off-nominal ratio t = 20/21) -> MV."""
    tr = {"hv_bus": "PCC", "lv_bus": "MV", "sn_mva": t(40.0), "vn_hv_kv": t(110.0), "vn_lv_kv": t(21.0),
          "vk_percent": t(12.0), "vkr_percent": t(0.4), "pfe_kw": t(0.0), "i0_percent": t(0.0),
          "vector_group": vector_group}
    if earthing_hv:
        tr["earthing_hv"] = earthing_hv
    if r_n_hv is not None:
        tr["r_n_hv_ohm"] = t(r_n_hv)
    if earthing_lv:
        tr["earthing_lv"] = earthing_lv
    return {"campus": {
        "name": "TR", "f_hz": 50,
        "pcc": {"bus": "PCC", "vn_kv": 110.0, "vm_pu": t(1.0), "sk_max_mva": t(3000.0), "sk_min_mva": t(2000.0),
                "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {"MV": {"vn_kv": 20.0}},
        "transformers": {"T1": tr},
        "units": {"L": {"kind": "load", "bus": "MV", "p_mw": t(10.0), "pf": t(0.95)}}}}


def _pu_grid_and_transformer():
    """In pu on 1 MVA: the grid at 110 kV (its zero sequence by default the
    same as its positive), the ratio t and K_T Zk on the 20 kV bus base."""
    zq = 1.1 / 3000.0
    xq = zq / math.hypot(1.0, 0.1)
    z1g = complex(0.1 * xq, xq)
    t_ratio = 20.0 / 21.0
    zk, rk = 0.12 / 40.0 * (21.0 / 20.0) ** 2, 0.004 / 40.0 * (21.0 / 20.0) ** 2
    kt = 0.95 * 1.1 / (1.0 + 0.6 * math.sqrt(0.12 ** 2 - 0.004 ** 2))
    return z1g, t_ratio, kt * complex(rk, math.sqrt(zk ** 2 - rk ** 2))


def test_an_off_nominal_ynyn_passes_the_grids_zero_sequence_and_its_hv_neutral_resistor():
    camp = build_campus(transformer_campus("YNyn0", earthing_hv="resistance", r_n_hv=10.0, earthing_lv="solid"))
    z1g, tr, z = _pu_grid_and_transformer()
    z1 = z + z1g / tr ** 2
    z0 = z + (z1g + 3 * 10.0 / 110.0 ** 2) / tr ** 2
    ik1 = 3 * 1.1 / abs(2 * z1 + z0) / (SQ3 * 20.0)
    assert row(SEQ.unbalanced_fault_levels(camp, island=False), "MV")["ik1_max_ka"] == pytest.approx(ik1, rel=1e-9)


def test_an_off_nominal_ynd_is_a_zero_sequence_shunt_on_its_hv_side():
    camp = build_campus(transformer_campus("YNd5", earthing_hv="solid"))
    z1g, tr, z = _pu_grid_and_transformer()
    z0 = 1.0 / (1.0 / z1g + 1.0 / (tr ** 2 * z))
    ik1 = 3 * 1.1 / abs(2 * z1g + z0) / (SQ3 * 110.0)
    assert row(SEQ.unbalanced_fault_levels(camp, island=False), "PCC")["ik1_max_ka"] == pytest.approx(ik1, rel=1e-9)


def test_a_star_without_its_earth_passes_no_zero_sequence():
    z1g, _, _ = _pu_grid_and_transformer()
    yny = row(SEQ.unbalanced_fault_levels(build_campus(transformer_campus("YNy0", earthing_hv="solid")),
                                          island=False), "PCC")
    assert yny["ik1_max_ka"] == pytest.approx(3 * 1.1 / abs(3 * z1g) / (SQ3 * 110.0), rel=1e-9)   # the grid alone
    yyn = row(SEQ.unbalanced_fault_levels(build_campus(transformer_campus("Yyn0", earthing_lv="solid")),
                                          island=False), "MV")
    assert yyn["ik1_max_ka"] == 0.0 and yyn["note"] == SEQ.NO_EARTH


def test_a_transformer_neutral_resistor_adds_three_times_itself():
    s = earthed_campus()
    s["campus"]["transformers"]["T1"].update(earthing_lv="resistance", r_n_lv_ohm=t(12.0))
    z1, z0, c = _grid_and_t1("max")
    fl = row(SEQ.unbalanced_fault_levels(build_campus(s), island=False), "MV1")
    assert fl["ik1_max_ka"] == pytest.approx(SQ3 * c * 20.0 / abs(2 * z1 + z0 + 36.0), rel=1e-9)


def test_a_converters_negative_sequence_admittance_is_inductive():
    camp = build_campus(converter_island(neg_seq=2.0))
    (uid, _, i_pu, y2), = SEQ.sequence_net(camp, ISC._sources(camp, None)).conv
    assert uid == "B" and i_pu == pytest.approx(1.2 * 22.0)
    assert y2 == pytest.approx(-2j * 22.0)


def test_a_statcom_feeds_the_maximum_and_not_the_minimum_as_in_i5():
    s = a_genset_island()
    s["campus"]["compensation"] = [{"name": "SC1", "bus": "PCC", "kind": "statcom", "q_mvar": t(5.0)}]
    camp = build_campus(s)
    i5, ours = row(ISC.island_fault_levels(camp), "PCC"), row(SEQ.unbalanced_fault_levels(camp), "PCC")
    assert ours["ik3_max_ka"] == pytest.approx(i5["ikss_max_ka"], rel=1e-9)
    assert ours["ik3_min_ka"] == pytest.approx(i5["ikss_min_ka"], rel=1e-9)
    assert ours["ik3_max_ka"] > row(SEQ.unbalanced_fault_levels(build_campus(a_genset_island())), "PCC")["ik3_max_ka"]


def test_a_cable_without_c0_takes_its_c_for_the_capacitive_current():
    s = genset_island("isolated", cable=True)
    del s["campus"]["cables"]["CF"]["c0_nf_per_km"]
    fl = row(SEQ.unbalanced_fault_levels(build_campus(s)), "FAR")
    assert fl["ic_e_ka"] == pytest.approx(SQ3 * 2 * math.pi * 50 * 250e-9 * 5.0 * 33.0, rel=1e-12)
