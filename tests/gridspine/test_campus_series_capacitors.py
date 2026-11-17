# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""GC-01b: series capacitors in the campus description.

A ``series_capacitors`` entry is ``{from_bus, to_bus, xc_ohm}`` or the
compensation degree ``k`` with the ``line`` (a cable in series with it) it
compensates, Xc = k * X_line. It enters the net as a pandapower
``impedance`` of -Xc, is listed in ``Campus.series_capacitors``, and is
handed to GC-08's sub-synchronous scan as ``Campus.series_caps``.

The oracles are arithmetic: the per-unit impedance is -Xc / (Vn^2 / Sbase);
a series capacitor cancels part of the feeder's reactance, so the far bus's
voltage rises.
"""

import copy

import pandapower as pp
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import data_hash
from tests.gridspine.test_campus import campus_spec, t

# X of CB1: 0.11 ohm/km * 2 km.
X_CB1 = 0.22


def cap_spec(**cap):
    """``campus_spec`` with MV1 -> SC1 -> MV1C -> CB1 -> MV2: the cable now
    starts at a dedicated bus, the capacitor in series with it."""
    spec = campus_spec()
    c = spec["campus"]
    c["buses"]["MV1C"] = {"vn_kv": 20.0}
    c["cables"]["CB1"]["from_bus"] = "MV1C"
    entry = {"from_bus": "MV1", "to_bus": "MV1C", "xc_ohm": t(0.1, "datasheet")}
    entry.update(cap)
    entry = {k: v for k, v in entry.items() if v is not None}
    c["series_capacitors"] = {"SC1": entry}
    return spec


def _refused(spec, match):
    with pytest.raises(ContractError, match=match):
        build_campus(spec)


# --- the build ---------------------------------------------------------------


def test_a_series_capacitor_is_a_negative_reactance_impedance_in_the_net():
    campus = build_campus(cap_spec())
    imp = campus.net.impedance
    assert list(imp["name"]) == ["SC1"]
    row = imp.iloc[0]
    # Zbase = 20 kV^2 / 1 MVA = 400 ohm; -0.1 / 400 = -0.00025 pu.
    assert row.xft_pu == pytest.approx(-0.00025, abs=1e-12)
    assert row.xtf_pu == pytest.approx(-0.00025, abs=1e-12)
    assert (row.rft_pu, row.rtf_pu, row.sn_mva) == (0.0, 0.0, 1.0)
    assert campus.net.bus.at[row.from_bus, "name"] == "MV1"
    assert campus.net.bus.at[row.to_bus, "name"] == "MV1C"


def test_the_table_lists_the_capacitor_with_its_tag_and_the_scan_form_uses_pandapower_buses():
    campus = build_campus(cap_spec())
    tab = campus.series_capacitors
    assert list(tab.index) == ["SC1"]
    assert tab.loc["SC1"].to_dict() == {"from_bus": "MV1", "to_bus": "MV1C", "xc_ohm": 0.1,
                                        "source": "datasheet", "protection": "bypassed"}
    bus = {n: i for i, n in campus.net.bus["name"].items()}
    assert campus.series_caps == [(bus["MV1"], bus["MV1C"], 0.1)]
    p = campus.params
    assert p[(p["element"] == "SC1") & (p["param"] == "xc_ohm")][["value", "source"]].values.tolist() == [
        [0.1, "datasheet"]]


def test_k_with_its_cable_gives_xc_as_k_times_the_cables_reactance_with_ks_tag():
    campus = build_campus(cap_spec(xc_ohm=None, k=t(0.4, "measured"), line="CB1"))
    row = campus.series_capacitors.loc["SC1"]
    assert row.xc_ohm == pytest.approx(0.4 * X_CB1, rel=1e-12)
    assert row.source == "measured"
    assert campus.net.impedance.iloc[0].xft_pu == pytest.approx(-0.088 / 400, rel=1e-12)


def test_k_reads_a_parallel_cables_combined_reactance():
    spec = cap_spec(xc_ohm=None, k=t(0.5), line="CB1")
    spec["campus"]["cables"]["CB1"]["parallel"] = 2
    assert build_campus(spec).series_capacitors.loc["SC1"].xc_ohm == pytest.approx(0.5 * X_CB1 / 2, rel=1e-12)


def test_the_capacitor_cancels_part_of_the_feeders_reactance_so_the_far_bus_sits_higher():
    def v_mv2(spec):
        campus = build_campus(spec)
        pp.runpp(campus.net)
        return float(campus.net.res_bus.loc[campus.net.bus["name"] == "MV2", "vm_pu"].iloc[0])

    plain = v_mv2(campus_spec())
    with_cap = v_mv2(cap_spec(xc_ohm=t(0.1)))
    stronger = v_mv2(cap_spec(xc_ohm=t(0.2)))
    assert plain < with_cap < stronger


def test_a_campus_without_the_key_builds_unchanged():
    campus = build_campus(campus_spec())
    assert campus.series_capacitors.empty
    assert list(campus.series_capacitors.columns) == ["from_bus", "to_bus", "xc_ohm", "source", "protection"]
    assert campus.series_caps == []
    assert campus.net.impedance.empty


def test_protection_none_is_kept_and_the_default_is_bypassed():
    assert build_campus(cap_spec(protection="none")).series_capacitors.loc["SC1"].protection == "none"
    assert build_campus(cap_spec(protection="bypassed")).series_capacitors.loc["SC1"].protection == "bypassed"


# --- the freeze's data hash (S4) ---------------------------------------------

#: data_hash(campus_spec(), {}) on master before GC-01b (b34f80c3): the key's code
#: must not change the hash of a campus that does not use it.
PINNED_HASH = "ab1046752ec5787004fa70665a76b4782797ffbc42acc7a3092df2c3fc9457fb"


def test_the_freeze_hash_of_a_campus_without_capacitors_is_unchanged_and_one_with_them_differs():
    assert data_hash(campus_spec(), {}) == PINNED_HASH
    with_cap = data_hash(cap_spec(), {})
    assert with_cap != PINNED_HASH
    other = data_hash(cap_spec(xc_ohm=t(0.2, "datasheet")), {})
    assert other not in (with_cap, PINNED_HASH)


# --- refusals, each with its own reason --------------------------------------


def test_an_unknown_bus_is_refused_naming_the_field():
    _refused(cap_spec(to_bus="NOWHERE"), r"series_capacitors\.SC1\.to_bus: unknown bus 'NOWHERE'")
    _refused(cap_spec(from_bus="NOWHERE"), r"series_capacitors\.SC1\.from_bus: unknown bus 'NOWHERE'")


def test_buses_at_different_nominal_voltages_are_refused():
    _refused(cap_spec(from_bus="PCC"), r"series_capacitors\.SC1: connects buses of different nominal voltages")


def test_the_same_bus_at_both_ends_is_refused():
    _refused(cap_spec(to_bus="MV1"), r"series_capacitors\.SC1: from_bus and to_bus are the same bus 'MV1'")


def test_both_xc_and_k_are_refused():
    _refused(cap_spec(k=t(0.5), line="CB1"), r"series_capacitors\.SC1: give exactly one of xc_ohm and k")


def test_neither_xc_nor_k_is_refused():
    _refused(cap_spec(xc_ohm=None), r"series_capacitors\.SC1: give exactly one of xc_ohm and k")


@pytest.mark.parametrize("k, match", [
    (1.0, r"series_capacitors\.SC1\.k must be in \(0, 1\), got 1\.0"),
    (1.5, r"series_capacitors\.SC1\.k must be in \(0, 1\), got 1\.5"),
    (0.0, r"series_capacitors\.SC1\.k must be positive, got 0"),
    (-0.2, r"series_capacitors\.SC1\.k must be positive, got -0\.2"),
])
def test_k_outside_zero_one_is_refused(k, match):
    _refused(cap_spec(xc_ohm=None, k=t(k), line="CB1"), match)


def test_k_without_a_line_is_refused():
    _refused(cap_spec(xc_ohm=None, k=t(0.5)), r"series_capacitors\.SC1\.line: a k entry needs the name of the cable")


def test_a_line_that_is_not_a_cable_is_refused():
    _refused(cap_spec(xc_ohm=None, k=t(0.5), line="TR1"),
             r"series_capacitors\.SC1\.line: 'TR1' is not a cable of the campus")


def test_a_cable_between_the_same_buses_would_sit_in_parallel_and_is_refused():
    spec = cap_spec(xc_ohm=None, k=t(0.5), line="CB2")
    spec["campus"]["cables"]["CB2"] = dict(spec["campus"]["cables"]["CB1"], from_bus="MV1", to_bus="MV1C")
    _refused(spec, r"series_capacitors\.SC1\.line: cable 'CB2' \(\['MV1', 'MV1C'\]\) is not in series")


def test_a_cable_sharing_no_bus_with_the_capacitor_is_refused():
    spec = cap_spec(xc_ohm=None, k=t(0.5), line="CB2")
    c = spec["campus"]
    c["buses"]["MV3"] = {"vn_kv": 20.0}
    c["cables"]["CB2"] = dict(c["cables"]["CB1"], from_bus="MV2", to_bus="MV3")
    _refused(spec, r"series_capacitors\.SC1\.line: cable 'CB2' \(\['MV2', 'MV3'\]\) is not in series")


def test_a_line_with_an_xc_entry_is_refused():
    _refused(cap_spec(line="CB1"), r"series_capacitors\.SC1\.line: only a k entry names a line")


@pytest.mark.parametrize("field", ["length_km", "x_ohm_per_km"])
def test_k_from_a_cable_missing_its_length_or_reactance_is_refused(field):
    spec = cap_spec(xc_ohm=None, k=t(0.5), line="CB1")
    del spec["campus"]["cables"]["CB1"][field]
    _refused(spec, rf"cables\.CB1: missing {field}")


@pytest.mark.parametrize("value, match", [
    (0.0, r"series_capacitors\.SC1\.xc_ohm must be positive, got 0"),
    (-0.1, r"series_capacitors\.SC1\.xc_ohm must be positive, got -0\.1"),
    (float("nan"), r"series_capacitors\.SC1\.xc_ohm value must be a finite number"),
    (float("inf"), r"series_capacitors\.SC1\.xc_ohm value must be a finite number"),
])
def test_a_non_positive_or_non_finite_xc_is_refused(value, match):
    _refused(cap_spec(xc_ohm=t(value)), match)


@pytest.mark.parametrize("cap, match", [
    ({"xc_ohm": 0.1}, r"series_capacitors\.SC1\.xc_ohm must be a mapping with 'value' and 'source'"),
    ({"xc_ohm": None, "k": 0.5, "line": "CB1"}, r"series_capacitors\.SC1\.k must be a mapping with 'value' and 'source'"),
])
def test_an_untagged_value_is_refused(cap, match):
    _refused(cap_spec(**cap), match)


def test_a_duplicate_name_is_refused():
    spec = cap_spec()
    spec["campus"]["series_capacitors"]["CB1"] = spec["campus"]["series_capacitors"].pop("SC1")
    _refused(spec, r"campus: duplicate element name\(s\) \['CB1'\]")


def test_an_unknown_protection_or_field_is_refused():
    _refused(cap_spec(protection="mov"), r"series_capacitors\.SC1\.protection must be one of \['bypassed', 'none'\]")
    _refused(cap_spec(r_ohm=t(0.01)), r"series_capacitors\.SC1: unknown field")


def test_a_block_that_is_not_a_mapping_is_refused():
    spec = cap_spec()
    spec["campus"]["series_capacitors"] = [spec["campus"]["series_capacitors"]["SC1"]]
    _refused(spec, r"campus\.series_capacitors must be a mapping, got list")


def test_a_bus_reachable_only_through_the_capacitor_is_connected():
    # MV1C and MV2 hang off the PCC only through SC1: without its edge the build refuses them as stranded.
    campus = build_campus(cap_spec())
    pp.runpp(campus.net)
    assert campus.net.converged


def test_the_input_spec_is_not_mutated():
    spec = cap_spec(xc_ohm=None, k=t(0.5), line="CB1")
    before = copy.deepcopy(spec)
    build_campus(spec)
    assert spec == before


# --- fault levels: a bypassed capacitor is neglected (S1, IEC 60909-0) -------

ALL_UNITS = ("BESS1", "PV1", "GEN1")


def _ik(campus, bus):
    from gridspine.static.campus_sc import campus_fault_levels

    fl = campus_fault_levels(campus, ALL_UNITS).set_index("bus")
    return float(fl.at[bus, "ikss_max_ka"]), float(fl.at[bus, "ikss_min_ka"])


def test_a_bypassed_capacitor_leaves_the_fault_levels_of_the_uncompensated_feeder():
    plain = build_campus(campus_spec())
    capped = build_campus(cap_spec(xc_ohm=t(0.2)))
    for bus in ("PCC", "MV1", "MV2"):
        assert _ik(capped, bus) == pytest.approx(_ik(plain, bus), abs=1e-6)
    # MV1C sits on MV1 once the capacitor is shorted.
    assert _ik(capped, "MV1C") == pytest.approx(_ik(plain, "MV1"), abs=1e-6)


def test_protection_none_keeps_the_capacitor_in_and_raises_the_far_fault_level():
    plain = build_campus(campus_spec())
    kept = build_campus(cap_spec(xc_ohm=t(0.2), protection="none"))
    ik_plain, ik_kept = _ik(plain, "MV2")[0], _ik(kept, "MV2")[0]
    # -jXc cancels part of the feeder's reactance: the fault current behind it rises.
    assert ik_kept > ik_plain + 1e-3


def test_bypass_for_faults_shorts_only_the_bypassed_capacitors_and_leaves_the_campus_net_alone():
    from gridspine.static import series_caps as SC

    spec = cap_spec()
    c = spec["campus"]
    c["buses"]["MV2C"] = {"vn_kv": 20.0}
    c["cables"]["CB2"] = dict(c["cables"]["CB1"], from_bus="MV2C", to_bus="MV2")
    c["series_capacitors"]["SC2"] = {"from_bus": "MV1", "to_bus": "MV2C", "xc_ohm": t(0.1), "protection": "none"}
    campus = build_campus(spec)
    work = SC.bypass_for_faults(copy.deepcopy(campus.net))
    imp = work.impedance.set_index("name")
    assert (bool(imp.at["SC1", "in_service"]), bool(imp.at["SC2", "in_service"])) == (False, True)
    sw = work.switch
    assert list(sw["name"]) == ["SC1~bypass"]
    row = sw.iloc[0]
    bus = {n: i for i, n in work.bus["name"].items()}
    assert (row.bus, row.element, row.et, bool(row.closed)) == (bus["MV1"], bus["MV1C"], "b", True)
    assert campus.net.impedance["in_service"].all() and campus.net.switch.empty


# --- walkers over lines and transformers refuse; the tap graph follows it (S3) -


def _refuses(call, what):
    with pytest.raises(ContractError, match=rf"{what}: series capacitor\(s\) \['SC1'\] not modelled here"):
        call()


def test_every_line_and_transformer_walker_refuses_a_campus_with_a_series_capacitor():
    from gridspine.handoff.raw_writer import write_raw
    from gridspine.producers.pypsa_nodal import to_pypsa
    from gridspine.static import island_sc as ISC
    from gridspine.static import island_sc_seq as SEQ
    from gridspine.static.loadflow import branch_keys

    campus = build_campus(cap_spec())
    _refuses(lambda: branch_keys(campus.net), r"branch keys \(contingency, sensitivity and planning screens\)")
    _refuses(lambda: ISC._branches(campus.net, 1.0), "island fault levels")
    # a bypassed capacitor is a bus-bus switch, which these walkers do not read either
    _refuses(lambda: ISC._branches(ISC.machine_net(campus, ALL_UNITS), 1.0), "island fault levels")
    _refuses(lambda: SEQ.sequence_net(campus, {}), r"sequence networks \(unbalanced faults, earthing\)")
    _refuses(lambda: write_raw(campus.net, "unused.raw"), "PSS/E RAW writer")
    _refuses(lambda: to_pypsa(campus.net), "PyPSA nodal export")


def test_the_walkers_still_take_a_campus_without_capacitors():
    from gridspine.static import island_sc as ISC
    from gridspine.static.loadflow import branch_keys

    campus = build_campus(campus_spec())
    assert len(branch_keys(campus.net)) == 3
    assert len(ISC._branches(campus.net, 1.0)) == 3


def test_the_tap_graph_reaches_the_buses_behind_the_capacitor():
    from gridspine.static import campus_taps as CT

    campus = build_campus(cap_spec())
    adj = CT._graph(campus.net)
    bus = {n: i for i, n in campus.net.bus["name"].items()}
    assert CT._reach(adj, bus["PCC"], set()) == set(bus.values())
    assert (bus["MV1C"], "impedance", 0) in adj[bus["MV1"]]


# --- GC-01's aggregation: refused inside a collector tree, kept elsewhere ------


def _plant_with_cap_in_collector():
    from tests.gridspine.test_aggregate import plant

    spec = plant()
    c = spec["campus"]
    # COL -> SC1 -> JC -> TRUNK -> J: every converter sits behind the capacitor.
    c["buses"]["JC"] = {"vn_kv": 33.0}
    c["cables"]["TRUNK"]["from_bus"] = "JC"
    c["series_capacitors"] = {"SC1": {"from_bus": "COL", "to_bus": "JC", "xc_ohm": t(0.5)}}
    return spec


def _plant_with_aux_feeder(cap_name):
    """``plant()`` plus a second main transformer feeding AUX -> cap -> AUXC
    with a load: a tree without converters, which aggregation leaves alone."""
    from tests.gridspine.test_aggregate import plant

    spec = plant()
    c = spec["campus"]
    c["transformers"]["TRA"] = dict(c["transformers"]["TRM"], lv_bus="AUX")
    c["buses"]["AUX"] = {"vn_kv": 33.0}
    c["buses"]["AUXC"] = {"vn_kv": 33.0}
    c["units"]["AUXLD"] = {"kind": "load", "bus": "AUXC", "p_mw": t(1.0), "pf": t(0.95)}
    c["series_capacitors"] = {cap_name: {"from_bus": "AUX", "to_bus": "AUXC", "xc_ohm": t(0.5)}}
    return spec


def test_a_series_capacitor_inside_a_collector_tree_is_refused_by_name_before_the_walk_loses_its_far_side():
    from gridspine.ingest.aggregate import AggregationError, aggregate

    spec = _plant_with_cap_in_collector()
    build_campus(spec)  # a valid campus: only aggregation refuses it
    # Without following the capacitor the walk would find no converter and say so instead.
    with pytest.raises(AggregationError, match=r"series capacitor SC1 sits inside the collector behind COL \(COL-JC\)"):
        aggregate(spec)


def test_a_series_capacitor_outside_every_collector_stays_in_the_aggregated_model():
    from gridspine.ingest.aggregate import aggregate

    agg = aggregate(_plant_with_aux_feeder("SCAUX"))
    assert agg.spec["campus"]["series_capacitors"] == _plant_with_aux_feeder("SCAUX")["campus"]["series_capacitors"]
    assert build_campus(agg.spec).series_capacitors.index.tolist() == ["SCAUX"]


def test_a_kept_series_capacitor_name_blocks_an_equivalent_of_the_same_name():
    from gridspine.ingest.aggregate import AggregationError, aggregate

    with pytest.raises(AggregationError, match=r"the equivalent name\(s\) \['EQ_MV'\] are already taken"):
        aggregate(_plant_with_aux_feeder("EQ_MV"))
