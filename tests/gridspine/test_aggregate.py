"""
The aggregated plant model and the freeze (GC-01, ``ingest/aggregate.py``).

The fixture is a PV + BESS plant: PCC 132 kV -> one 100 MVA main
transformer -> the 33 kV collector bus COL -> a shared trunk (two parallel
runs) to J -> run A (2 units) and run B (3 units). Each unit sits behind
its own 33/0.69 kV unit transformer. The branches carry different P, so the
P_i**2 weighting is exercised (GK A2 condition A1).
"""

import copy
import datetime
import math

import pandas as pd
import pytest

from gridspine.ingest.aggregate import (
    AggregationError,
    aggregate,
    aggregate_campus,
    aggregate_hour,
    freeze,
)
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import data_hash, manifest
from gridspine.static.campus_sc import campus_fault_levels


def t(value, source="datasheet"):
    return {"value": value, "source": source}


#: (bus, parent bus, cable length km) of each 33 kV node; every node holds one unit.
NODES = (
    ("A1", "J", 1.0),
    ("A2", "A1", 1.0),
    ("B1", "J", 1.0),
    ("B2", "B1", 1.0),
    ("B3", "B2", 1.0),
)
#: Units: PV at A1, A2, B1, B2; BESS at B3. 5 MW each.
KIND = {"A1": "pv", "A2": "pv", "B1": "pv", "B2": "pv", "B3": "bess"}
T0 = datetime.datetime(2026, 11, 17, 8, 0, tzinfo=datetime.UTC)


def cable(a, b, length, parallel=1, zero=True):
    cb = {
        "from_bus": a,
        "to_bus": b,
        "length_km": t(length),
        "r_ohm_per_km": t(0.1),
        "x_ohm_per_km": t(0.12),
        "c_nf_per_km": t(200.0),
        "max_i_ka": t(0.4),
    }
    if parallel != 1:
        cb["parallel"] = parallel
    if zero:
        cb.update(r0_ohm_per_km=t(0.3), x0_ohm_per_km=t(0.36), c0_nf_per_km=t(150.0))
    return cb


def unit_trafo(hv, lv, sn=6.0):
    return {
        "hv_bus": hv,
        "lv_bus": lv,
        "sn_mva": t(sn),
        "vn_hv_kv": t(33.0),
        "vn_lv_kv": t(0.69),
        "vk_percent": t(6.0),
        "vkr_percent": t(0.8),
        "pfe_kw": t(5.0),
        "i0_percent": t(0.2),
    }


def unit(kind, bus, p=5.0):
    u = {
        "kind": kind,
        "bus": bus,
        "p_mw": t(p),
        "s_mva": t(p * 1.1),
        "k_sc": t(1.1),
        "rx_sc": t(0.1),
    }
    if kind == "bess":
        u["e_mwh"] = t(2 * p)
    return u


def plant():
    buses = {"COL": {"vn_kv": 33.0}, "J": {"vn_kv": 33.0}}
    cables = {"TRUNK": cable("COL", "J", 2.0, parallel=2)}
    trafos = {
        "TRM": {
            "hv_bus": "PCC",
            "lv_bus": "COL",
            "sn_mva": t(100.0),
            "vn_hv_kv": t(132.0),
            "vn_lv_kv": t(33.0),
            "vk_percent": t(12.0),
            "vkr_percent": t(0.3),
            "pfe_kw": t(40.0),
            "i0_percent": t(0.05),
        }
    }
    units = {}
    for bus, par, length in NODES:
        buses[bus] = {"vn_kv": 33.0}
        buses[bus + "L"] = {"vn_kv": 0.69}
        cables["C" + bus] = cable(par, bus, length)
        trafos["T" + bus] = unit_trafo(bus, bus + "L")
        units["U" + bus] = unit(KIND[bus], bus + "L")
    return {
        "campus": {
            "name": "PLANT",
            "f_hz": 50,
            "pcc": {
                "bus": "PCC",
                "vn_kv": 132.0,
                "vm_pu": t(1.0),
                "sk_max_mva": t(4000.0),
                "sk_min_mva": t(2500.0),
                "rx_max": t(0.1),
                "rx_min": t(0.12),
            },
            "buses": buses,
            "transformers": trafos,
            "cables": cables,
            "units": units,
        }
    }


# --- A1 collector equal-loss, hand calculation -----------------------------------------------


def test_collector_impedance_is_the_equal_loss_hand_calculation():
    eq = aggregate(plant()).spec["campus"]["cables"]["EQ_COL"]
    # P downstream of each branch (MW of 25): trunk 25, A1 10, A2 5, B1 15, B2 10, B3 5.
    # Trunk: 0.1 ohm/km * 2 km / 2 runs = 0.1 ohm; every other branch 0.1 ohm * 1 km.
    r_hand = 0.1 * 2 / 2 * (25 / 25) ** 2 + 0.1 * 1 * (
        (10 / 25) ** 2 + (5 / 25) ** 2 + (15 / 25) ** 2 + (10 / 25) ** 2 + (5 / 25) ** 2
    )
    x_hand = 0.12 * 2 / 2 * (25 / 25) ** 2 + 0.12 * 1 * (
        (10 / 25) ** 2 + (5 / 25) ** 2 + (15 / 25) ** 2 + (10 / 25) ** 2 + (5 / 25) ** 2
    )
    assert r_hand == pytest.approx(0.176) and x_hand == pytest.approx(0.2112)
    assert eq["length_km"]["value"] == 1.0
    assert eq["r_ohm_per_km"]["value"] == pytest.approx(r_hand, abs=1e-6)
    assert eq["x_ohm_per_km"]["value"] == pytest.approx(x_hand, abs=1e-6)
    # Charging summed: 200 nF/km * (2 km * 2 runs + 5 * 1 km).
    assert eq["c_nf_per_km"]["value"] == pytest.approx(200.0 * (2 * 2 + 5), abs=1e-6)
    # Rating: the root-adjacent trunk, two runs of 0.4 kA.
    assert eq["max_i_ka"]["value"] == pytest.approx(0.8)
    assert (eq["from_bus"], eq["to_bus"]) == ("COL", "EQ_MV")


def test_lv_cable_is_referred_to_the_root_voltage():
    spec = plant()
    c = spec["campus"]
    # Move unit UB3 behind a 0.2 km LV cable at the 0.69 kV side.
    c["buses"]["B3X"] = {"vn_kv": 0.69}
    c["cables"]["LB3"] = {
        "from_bus": "B3L",
        "to_bus": "B3X",
        "length_km": t(0.2),
        "r_ohm_per_km": t(0.05),
        "x_ohm_per_km": t(0.08),
        "c_nf_per_km": t(500.0),
        "max_i_ka": t(5.0),
        "r0_ohm_per_km": t(0.1),
        "x0_ohm_per_km": t(0.2),
        "c0_nf_per_km": t(300.0),
    }
    c["units"]["UB3"]["bus"] = "B3X"
    eq = aggregate(spec).spec["campus"]["cables"]["EQ_COL"]
    refer = (33.0 / 0.69) ** 2
    r_hand = 0.176 + 0.05 * 0.2 * refer * (5 / 25) ** 2
    assert eq["r_ohm_per_km"]["value"] == pytest.approx(r_hand, abs=1e-6)
    assert eq["c_nf_per_km"]["value"] == pytest.approx(
        200.0 * 9 + 500.0 * 0.2 / refer, abs=1e-6
    )


# --- A5 unit transformer equivalent -----------------------------------------------------------


def test_equal_unit_transformers_give_z_over_n():
    tr = aggregate(plant()).spec["campus"]["transformers"]["EQ_TR"]
    assert tr["sn_mva"]["value"] == pytest.approx(5 * 6.0)
    assert tr["vk_percent"]["value"] == pytest.approx(6.0, abs=1e-9)
    assert tr["vkr_percent"]["value"] == pytest.approx(0.8, abs=1e-9)
    z_one = 0.06 * 33.0**2 / 6.0
    z_eq = tr["vk_percent"]["value"] / 100 * 33.0**2 / tr["sn_mva"]["value"]
    assert z_eq == pytest.approx(z_one / 5, rel=1e-9)
    assert tr["pfe_kw"]["value"] == pytest.approx(25.0)
    assert tr["i0_percent"]["value"] == pytest.approx(0.2)
    assert (tr["vn_hv_kv"]["value"], tr["vn_lv_kv"]["value"]) == (33.0, 0.69)


def test_unequal_unit_transformers_follow_equal_loss():
    spec = plant()
    c = spec["campus"]
    c["transformers"]["TA1"] = unit_trafo("A1", "A1L", sn=12.0)
    c["units"]["UA1"] = unit("pv", "A1L", p=10.0)
    tr = aggregate(spec).spec["campus"]["transformers"]["EQ_TR"]
    # P: A1 10, others 5 each, total 30. Z (ohm, 33 kV): 0.06 * 33^2 / sn.
    zbase = 33.0**2
    r = 0.008 * zbase / 12 * (10 / 30) ** 2 + 4 * 0.008 * zbase / 6 * (5 / 30) ** 2
    xk = lambda sn: math.sqrt((0.06 * zbase / sn) ** 2 - (0.008 * zbase / sn) ** 2)  # noqa: E731
    x = xk(12.0) * (10 / 30) ** 2 + 4 * xk(6.0) * (5 / 30) ** 2
    sn = 12.0 + 4 * 6.0
    assert tr["sn_mva"]["value"] == pytest.approx(sn)
    assert tr["vkr_percent"]["value"] == pytest.approx(r / (zbase / sn) * 100, rel=1e-9)
    assert tr["vk_percent"]["value"] == pytest.approx(
        math.hypot(r, x) / (zbase / sn) * 100, rel=1e-9
    )
    assert tr["i0_percent"]["value"] == pytest.approx(0.2)


# --- equivalent units and the unit map -------------------------------------------------------


def test_one_equivalent_unit_per_kind_with_summed_ratings():
    agg = aggregate(plant())
    units = agg.spec["campus"]["units"]
    assert set(units) == {"EQ_PV", "EQ_BESS"}
    pv, bess = units["EQ_PV"], units["EQ_BESS"]
    assert (pv["bus"], pv["kind"]) == ("EQ_LV", "pv")
    assert pv["p_mw"]["value"] == pytest.approx(20.0) and pv["s_mva"][
        "value"
    ] == pytest.approx(22.0)
    assert bess["e_mwh"]["value"] == pytest.approx(10.0)
    assert agg.unit_map == {"EQ_BESS": ["UB3"], "EQ_PV": ["UA1", "UA2", "UB1", "UB2"]}


def test_k_sc_is_rating_weighted():
    spec = plant()
    spec["campus"]["units"]["UA1"]["k_sc"] = t(1.5)
    spec["campus"]["units"]["UA1"]["s_mva"] = t(11.0)
    pv = aggregate(spec).spec["campus"]["units"]["EQ_PV"]
    assert pv["k_sc"]["value"] == pytest.approx(
        (1.5 * 11.0 + 1.1 * 5.5 * 3) / (11.0 + 5.5 * 3)
    )


def test_root_elements_stay_and_map_to_themselves():
    spec = plant()
    spec["campus"]["units"]["AUX"] = {
        "kind": "load",
        "bus": "COL",
        "p_mw": t(0.5),
        "pf": t(0.95),
    }
    agg = aggregate(spec)
    assert agg.spec["campus"]["units"]["AUX"] == spec["campus"]["units"]["AUX"]
    assert agg.unit_map["AUX"] == ["AUX"]


def test_input_is_not_changed():
    spec = plant()
    before = copy.deepcopy(spec)
    aggregate(spec)
    assert spec == before


def test_aggregated_spec_builds_with_only_the_equivalent_buses():
    campus, _ = aggregate_campus(plant())
    assert set(campus.net.bus["name"]) == {"PCC", "COL", "EQ_MV", "EQ_LV"}
    assert set(campus.units.index) == {"EQ_PV", "EQ_BESS"}


# --- A2 load flow match ------------------------------------------------------------------------


def _dispatch(campus, frac_p, frac_q, member_of):
    """Every unit at ``frac_p`` of its rated P and ``frac_q`` of it as Q."""
    import pandapower as pp

    net = campus.net
    for i in net.sgen.index:
        uid = net.sgen.at[i, "name"]
        rated = float(campus.units.at[uid, "p_mw"])
        net.sgen.at[i, "p_mw"] = frac_p * rated
        net.sgen.at[i, "q_mvar"] = frac_q * rated
        net.sgen.at[i, "in_service"] = True
    pp.runpp(net, calculate_voltage_angles=True)
    grid = net.res_ext_grid.iloc[0]
    vm = dict(zip(net.bus["name"], net.res_bus["vm_pu"]))
    return float(grid["p_mw"]), float(grid["q_mvar"]), vm["PCC"], vm["COL"]


#: (P fraction, Q fraction of rated P): full rated P, partial P absorbing Q,
#: partial P delivering Q, and near zero (GK A2 A2: both Q signs over the set).
POINTS = [(1.0, 0.0), (0.6, -0.3), (0.5, 0.25), (0.02, 0.0)]


@pytest.mark.parametrize("frac_p, frac_q", POINTS)
def test_poc_load_flow_matches_the_detailed_model(frac_p, frac_q):
    """
    P and Q within 0.5 % of the plant rating (25 MW), not of the value, so
    the near-zero point is well defined; |V| within 0.005 pu absolute at the
    PoC and at the collector bus.
    """
    rating = 25.0
    detailed = build_campus(plant())
    aggregated, _ = aggregate_campus(plant())
    d = _dispatch(detailed, frac_p, frac_q, None)
    a = _dispatch(aggregated, frac_p, frac_q, None)
    assert abs(a[0] - d[0]) <= 0.005 * rating
    assert abs(a[1] - d[1]) <= 0.005 * rating
    assert abs(a[2] - d[2]) <= 0.005 and abs(a[3] - d[3]) <= 0.005


# --- A3 short circuit ----------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_poc_fault_level_matches_the_detailed_model():
    detailed = build_campus(plant())
    aggregated, _ = aggregate_campus(plant())
    d = campus_fault_levels(detailed, set(detailed.units.index)).set_index("bus")
    a = campus_fault_levels(aggregated, set(aggregated.units.index)).set_index("bus")
    for col in ("ikss_max_ka", "ikss_min_ka"):
        assert a.at["PCC", col] == pytest.approx(d.at["PCC", col], rel=0.02)
        assert a.at["COL", col] == pytest.approx(d.at["COL", col], rel=0.02)


# --- A6 zero sequence ---------------------------------------------------------------------------


def test_zero_sequence_is_aggregated_when_every_cable_has_it():
    agg = aggregate(plant())
    eq = agg.spec["campus"]["cables"]["EQ_COL"]
    assert eq["r0_ohm_per_km"]["value"] == pytest.approx(0.176 * 3, abs=1e-6)
    assert eq["x0_ohm_per_km"]["value"] == pytest.approx(0.2112 * 3, abs=1e-6)
    assert eq["c0_nf_per_km"]["value"] == pytest.approx(150.0 * 9, abs=1e-6)
    assert not any("zero sequence" in a for a in agg.assumptions)


def test_zero_sequence_is_omitted_and_named_when_a_cable_lacks_it():
    spec = plant()
    for f in ("r0_ohm_per_km", "x0_ohm_per_km", "c0_nf_per_km"):
        del spec["campus"]["cables"]["CB2"][f]
    agg = aggregate(spec)
    eq = agg.spec["campus"]["cables"]["EQ_COL"]
    assert not {"r0_ohm_per_km", "x0_ohm_per_km", "c0_nf_per_km"} & set(eq)
    assert any("zero sequence" in a and "CB2" in a for a in agg.assumptions)
    campus, _ = aggregate_campus(spec)
    assert any("CB2" in a for a in campus.assumptions)


# --- A7 source tags ---------------------------------------------------------------------------------


def test_derived_values_take_the_least_sure_source():
    spec = plant()
    assert (
        aggregate(spec).spec["campus"]["cables"]["EQ_COL"]["r_ohm_per_km"]["source"]
        == "datasheet"
    )
    spec["campus"]["cables"]["TRUNK"]["r_ohm_per_km"] = t(0.1, "measured")
    assert (
        aggregate(spec).spec["campus"]["cables"]["EQ_COL"]["r_ohm_per_km"]["source"]
        == "datasheet"
    )
    spec["campus"]["cables"]["CB3"]["x_ohm_per_km"] = t(0.12, "assumed")
    assert (
        aggregate(spec).spec["campus"]["cables"]["EQ_COL"]["r_ohm_per_km"]["source"]
        == "assumed"
    )


def test_all_measured_inputs_stay_measured():
    spec = plant()
    for u in spec["campus"]["units"].values():
        for f in ("p_mw", "s_mva"):
            u[f]["source"] = "measured"
    assert (
        aggregate(spec).spec["campus"]["units"]["EQ_PV"]["p_mw"]["source"] == "measured"
    )


# --- A4 refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["p_mw", "s_mva"])
def test_zero_rating_is_refused(field):
    spec = plant()
    spec["campus"]["units"]["UA2"][field] = t(0.0)
    with pytest.raises(AggregationError, match=rf"unit UA2 has zero {field}"):
        aggregate(spec)


def test_a_ring_is_refused():
    spec = plant()
    spec["campus"]["cables"]["TIE"] = cable("A2", "B3", 1.0)
    with pytest.raises(AggregationError, match=r"not radial: cable \w+ closes a ring"):
        aggregate(spec)


def test_a_ring_with_a_detached_part_is_refused():
    """
    Branches = buses - 1 overall (a ring plus a detached bus), but the
    collector is still not a tree.
    """
    spec = plant()
    spec["campus"]["cables"]["TIE"] = cable("A2", "B3", 1.0)
    spec["campus"]["buses"]["ISO"] = {"vn_kv": 33.0}
    with pytest.raises(AggregationError, match="closes a ring"):
        aggregate(spec)


def test_a_second_main_transformer_into_the_tree_is_refused():
    spec = plant()
    spec["campus"]["transformers"]["TRM2"] = copy.deepcopy(
        spec["campus"]["transformers"]["TRM"]
    )
    spec["campus"]["transformers"]["TRM2"]["lv_bus"] = "B2"
    with pytest.raises(AggregationError, match="second path to the PCC"):
        aggregate(spec)


def test_a_branch_back_to_the_pcc_is_refused():
    spec = plant()
    spec["campus"]["cables"]["BACK"] = cable("B2", "PCC", 1.0)
    with pytest.raises(
        AggregationError, match=r"reaches the PCC again through cable BACK"
    ):
        aggregate(spec)


def test_parallel_main_transformers_to_one_root_are_accepted():
    spec = plant()
    spec["campus"]["transformers"]["TRM2"] = copy.deepcopy(
        spec["campus"]["transformers"]["TRM"]
    )
    agg = aggregate(spec)
    assert {"TRM", "TRM2"} <= set(agg.spec["campus"]["transformers"])


@pytest.mark.parametrize(
    "kind, entry",
    [
        ("load", {"kind": "load", "bus": "B2", "p_mw": t(1.0), "pf": t(0.95)}),
        (
            "genset",
            {
                "kind": "genset",
                "bus": "B2",
                "p_mw": t(1.0),
                "s_mva": t(1.25),
                "xd_pp": t(0.15),
                "rx_sc": t(0.07),
            },
        ),
        (
            "ups",
            {
                "kind": "ups",
                "bus": "B2",
                "p_mw": t(1.0),
                "e_mwh": t(0.5),
                "s_mva": t(1.1),
            },
        ),
    ],
)
def test_non_converter_unit_inside_the_tree_is_refused(kind, entry):
    spec = plant()
    spec["campus"]["units"]["X1"] = entry
    with pytest.raises(
        AggregationError,
        match=rf"{kind} X1 sits inside the collector behind COL \(at B2\)",
    ):
        aggregate(spec)


def test_compensation_inside_the_tree_is_refused():
    spec = plant()
    spec["campus"]["compensation"] = [
        {
            "name": "CAP1",
            "bus": "B2",
            "kind": "capacitor_bank",
            "q_mvar": t(2.0),
            "steps": 2,
        }
    ]
    with pytest.raises(
        AggregationError, match=r"compensation CAP1 sits inside the collector"
    ):
        aggregate(spec)


def test_earthing_transformer_inside_the_tree_is_refused():
    spec = plant()
    spec["campus"]["earthing_transformers"] = {"ET1": {"bus": "J", "x0_ohm": t(30.0)}}
    with pytest.raises(
        AggregationError, match=r"earthing transformer ET1 sits inside the collector"
    ):
        aggregate(spec)


def test_differing_rated_ratio_is_refused():
    spec = plant()
    spec["campus"]["transformers"]["TB1"]["vn_hv_kv"] = t(34.0)
    with pytest.raises(AggregationError, match=r"differ in rated voltage vn_hv_kv"):
        aggregate(spec)


def test_differing_vector_group_is_refused():
    spec = plant()
    for n in ("TA1", "TA2", "TB1", "TB2", "TB3"):
        spec["campus"]["transformers"][n]["vector_group"] = "Dyn11"
    spec["campus"]["transformers"]["TB2"]["vector_group"] = "Dyn1"
    with pytest.raises(
        AggregationError, match=r"differ in vector_group \('Dyn11' vs 'Dyn1'\)"
    ):
        aggregate(spec)


def test_two_lv_levels_are_refused():
    spec = plant()
    spec["campus"]["buses"]["B3L"]["vn_kv"] = 0.66
    with pytest.raises(AggregationError, match=r"feed two LV levels \[0.66, 0.69\] kV"):
        aggregate(spec)


def test_units_on_and_behind_unit_transformers_are_refused():
    spec = plant()
    spec["campus"]["units"]["UB3"]["bus"] = "B3"
    with pytest.raises(AggregationError, match="units at two voltage levels"):
        aggregate(spec)


def test_cascaded_transformers_are_refused():
    spec = plant()
    c = spec["campus"]
    c["buses"]["B3M"] = {"vn_kv": 0.69}
    c["transformers"]["TB3B"] = {
        **unit_trafo("B3L", "B3M"),
        "vn_hv_kv": t(0.69),
        "vn_lv_kv": t(0.69),
    }
    c["units"]["UB3"]["bus"] = "B3M"
    with pytest.raises(
        AggregationError, match=r"transformers TB3 and TB3B are cascaded"
    ):
        aggregate(spec)


def test_transformer_fed_from_its_lv_side_is_refused():
    spec = plant()
    tr = spec["campus"]["transformers"]["TB3"]
    tr["hv_bus"], tr["lv_bus"] = "B3L", "B3"
    tr["vn_hv_kv"], tr["vn_lv_kv"] = t(0.69), t(33.0)
    with pytest.raises(AggregationError, match="TB3 is fed from its LV side"):
        aggregate(spec)


def test_tree_without_converter_units_is_refused():
    spec = plant()
    spec["campus"]["units"] = {
        "AUX": {"kind": "load", "bus": "COL", "p_mw": t(0.5), "pf": t(0.95)}
    }
    with pytest.raises(AggregationError, match="holds no converter unit"):
        aggregate(spec)


def test_equivalent_name_already_taken_is_refused():
    spec = plant()
    spec["campus"]["units"]["EQ_PV"] = {
        "kind": "load",
        "bus": "COL",
        "p_mw": t(0.5),
        "pf": t(0.95),
    }
    with pytest.raises(AggregationError, match=r"\['EQ_PV'\] are already taken"):
        aggregate(spec)


# --- what the aggregate leaves out --------------------------------------------------------------


def test_unequal_taps_are_dropped_with_an_assumption():
    spec = plant()
    tap = {
        "changer": "off_circuit",
        "side": "hv",
        "neutral": 0,
        "min": -2,
        "max": 2,
        "step_percent": 2.5,
    }
    spec["campus"]["transformers"]["TA1"]["tap"] = dict(tap, pos=1)
    agg = aggregate(spec)
    assert "tap" not in agg.spec["campus"]["transformers"]["EQ_TR"]
    assert any("unequal taps" in a for a in agg.assumptions)


# --- A9 aggregate_hour ------------------------------------------------------------------------------


def test_aggregate_hour_sums_members_and_status_is_any():
    agg = aggregate(plant())
    rows = pd.DataFrame(
        {
            "unit_id": ["UA1", "UA2", "UB1", "UB2", "UB3"],
            "period": 2030,
            "hour": 7,
            "p_mw": [5.0, 4.0, 0.0, 3.0, -2.0],
            "status": [1, 1, 0, 1, 1],
        }
    )
    out = aggregate_hour(rows, agg.unit_map).set_index("unit_id")
    assert (
        out.at["EQ_PV", "p_mw"] == pytest.approx(12.0)
        and out.at["EQ_PV", "status"] == 1
    )
    assert out.at["EQ_BESS", "p_mw"] == pytest.approx(-2.0)
    assert list(out.columns) == ["period", "hour", "p_mw", "status"]


def test_aggregate_hour_status_off_when_every_member_is_off():
    agg = aggregate(plant())
    rows = pd.DataFrame(
        {
            "unit_id": ["UA1", "UA2", "UB1", "UB2", "UB3"],
            "p_mw": 0.0,
            "status": [0, 0, 0, 0, 1],
        }
    )
    out = aggregate_hour(rows, agg.unit_map).set_index("unit_id")
    assert out.at["EQ_PV", "status"] == 0 and out.at["EQ_BESS", "status"] == 1


def test_aggregate_hour_refuses_an_unmapped_unit():
    agg = aggregate(plant())
    rows = pd.DataFrame(
        {"unit_id": ["UA1", "GHOST"], "p_mw": [1.0, 2.0], "status": [1, 1]}
    )
    with pytest.raises(AggregationError, match=r"unit\(s\) \['GHOST'\]"):
        aggregate_hour(rows, agg.unit_map)


# --- A8 freeze ------------------------------------------------------------------------------------


def test_freeze_holds_both_variants_and_is_deterministic():
    a = freeze(plant(), {"case": "max"}, now=T0)
    b = freeze(plant(), {"case": "max"}, now=T0 + datetime.timedelta(hours=1))
    assert a.data_hash == b.data_hash == data_hash(plant(), {"case": "max"})
    assert a.spec("aggregated") == aggregate(plant()).spec
    assert a.unit_map["EQ_PV"] == ["UA1", "UA2", "UB1", "UB2"]
    assert manifest(a, "aggregated")["variant"] == "aggregated"
    assert freeze(plant(), {"case": "min"}, now=T0).data_hash != a.data_hash


def test_freeze_carries_the_aggregation_assumptions():
    spec = plant()
    del spec["campus"]["cables"]["CB2"]["r0_ohm_per_km"]
    ms = freeze(spec, {}, now=T0)
    assert any("CB2" in line for line in ms.assumptions)


def test_freeze_refuses_when_the_aggregation_fails():
    spec = plant()
    spec["campus"]["units"]["X1"] = {
        "kind": "load",
        "bus": "B2",
        "p_mw": t(1.0),
        "pf": t(0.95),
    }
    with pytest.raises(AggregationError, match="sits inside the collector"):
        freeze(spec, {}, now=T0)


def test_freeze_refuses_when_the_detailed_model_fails():
    spec = plant()
    spec["campus"]["cables"]["CB2"]["to_bus"] = "NOWHERE"
    with pytest.raises(ContractError):
        freeze(spec, {}, now=T0)


def test_freeze_detailed_only_when_asked():
    spec = plant()
    spec["campus"]["units"]["X1"] = {
        "kind": "load",
        "bus": "B2",
        "p_mw": t(1.0),
        "pf": t(0.95),
    }
    ms = freeze(spec, {}, aggregated=False, now=T0)
    assert ms.specs["aggregated"] is None and ms.unit_map == {}
    with pytest.raises(ContractError, match="aggregated variant was not frozen"):
        manifest(ms, "aggregated")
