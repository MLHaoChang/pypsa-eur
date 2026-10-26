"""A campus description generated from a solved hub project (C1, stage B).

The capacity-expansion model knows the campus's buses, its connection and
transformer Links, and the assets with their optimised capacities. It does
not know impedances, inverter ratings or the grid operator's fault level.
``draft_campus`` writes the campus YAML from what the model knows and fills
the rest with typical values, every one tagged ``assumed``. The user edits
the draft, which is the same file a user-supplied single line would be.

The network here mirrors the pypsa-gui *Data Center Energy Hub* template.
It is built in this file and "solved" by setting ``p_nom_opt`` directly, so
the test does not depend on a solver.
"""
import math

import numpy as np
import pandas as pd
import pandapower as pp
import pypsa
import pytest
import yaml

from gridspine.ingest.campus import build_campus
from gridspine.producers.campus import STANDARD_MVA, draft_campus, short_names
from gridspine.schema.contracts import ContractError

HOURS = 24


def hub(sk=250.0):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=HOURS, freq="h"))
    n.add("Bus", "grid", v_nom=132.0, carrier="AC")
    n.add("Bus", "dc_mv", v_nom=33.0, carrier="AC")
    n.add("Bus", "it_bus", v_nom=11.0, carrier="AC")
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Link", "grid_import", bus0="grid", bus1="dc_mv", p_nom=40.0, efficiency=0.995)
    n.add("Link", "site_transformer", bus0="dc_mv", bus1="it_bus", p_nom=80.0, efficiency=0.99)
    n.add("Load", "it_load", bus="it_bus")
    n.add("Load", "cooling", bus="dc_mv")
    n.add("Load", "offices", bus="dc_mv")
    h = np.arange(HOURS)
    n.loads_t.p_set = pd.DataFrame({
        "it_load": 36.0 + 0.5 * np.sin(h),
        "cooling": 8.0 + 6.0 * (h == 15),
        "offices": np.full(HOURS, 1.0),
    }, index=n.snapshots)
    for i in range(1, 5):
        n.add("Generator", f"genset_{i}", bus="dc_mv", carrier="gas", p_nom=10.0)
    n.add("Generator", "rooftop_pv", bus="dc_mv", carrier="solar", p_nom=4.0)
    n.add("StorageUnit", "ups_battery", bus="it_bus", carrier="battery", p_nom=15.0, max_hours=1.0)
    n.add("Generator", "genset_new", bus="dc_mv", carrier="gas", p_nom=0.0, p_nom_extendable=True)
    n.add("StorageUnit", "bess_new", bus="it_bus", carrier="battery", p_nom=0.0,
          p_nom_extendable=True, max_hours=4.0)
    # "solved": the expansion built 12 MW of new genset and no new battery
    n.generators["p_nom_opt"] = n.generators["p_nom"]
    n.generators.loc["genset_new", "p_nom_opt"] = 12.0
    n.storage_units["p_nom_opt"] = n.storage_units["p_nom"]
    n.links["p_nom_opt"] = n.links["p_nom"]
    n.buses["eh_poc"] = False
    n.buses.loc["grid", "eh_poc"] = True
    n.buses["eh_sk_mva"] = float("nan")
    if sk is not None:
        n.buses.loc["grid", "eh_sk_mva"] = sk
    return n


def _by_pypsa(section):
    return {spec["pypsa_name"]: (name, spec) for name, spec in section.items()}


# --------------------------------------------------------------------------
# the draft is a buildable campus with the model's own topology and ratings
# --------------------------------------------------------------------------

def test_the_draft_builds_and_its_load_flow_converges():
    draft = draft_campus(hub())
    camp = build_campus(draft.spec)
    pp.runpp(camp.net)
    assert camp.net.converged


def test_buses_keep_their_voltages_and_the_poc_bus_is_the_pcc():
    c = draft_campus(hub()).spec["campus"]
    assert c["pcc"]["vn_kv"] == 132.0 and c["pcc"]["pypsa_name"] == "grid"
    buses = {spec["pypsa_name"]: spec["vn_kv"] for spec in c["buses"].values()}
    assert buses == {"dc_mv": 33.0, "it_bus": 11.0}


def test_a_link_between_voltage_levels_becomes_a_transformer_sized_to_the_next_standard_mva():
    trafos = _by_pypsa(draft_campus(hub()).spec["campus"]["transformers"])
    _, imp = trafos["grid_import"]
    _, site = trafos["site_transformer"]
    # 40 MW at the 0.95 sizing power factor is 42.1 MVA -> next standard size 50 MVA
    assert imp["sn_mva"]["value"] == 50.0 and imp["sn_mva"]["source"] == "assumed"
    # 80 MW / 0.95 = 84.2 MVA -> 100 MVA
    assert site["sn_mva"]["value"] == 100.0
    assert imp["vn_hv_kv"]["value"] == 132.0 and imp["vn_lv_kv"]["value"] == 33.0
    # typical short-circuit impedance for the size class (IEC 60076-5 minimums)
    assert imp["vk_percent"]["value"] == 11.0 and site["vk_percent"]["value"] == 12.5


def test_standard_sizes_are_ascending_and_a_rating_rounds_up_never_down():
    assert list(STANDARD_MVA) == sorted(STANDARD_MVA)
    n = hub()
    n.links.loc["grid_import", ["p_nom", "p_nom_opt"]] = 0.95 * 40.0      # exactly 40 MVA
    _, imp = _by_pypsa(draft_campus(n).spec["campus"]["transformers"])["grid_import"]
    assert imp["sn_mva"]["value"] == 40.0


def test_assets_are_typed_by_carrier_and_rated_at_their_optimised_capacity():
    draft = draft_campus(hub())
    units = _by_pypsa(draft.spec["campus"]["units"])
    assert units["genset_1"][1]["kind"] == "genset"
    assert units["genset_new"][1]["p_mw"]["value"] == 12.0          # p_nom_opt, not p_nom
    assert units["rooftop_pv"][1]["kind"] == "pv"
    assert units["rooftop_pv"][1]["s_mva"]["value"] == pytest.approx(4.0 / 0.95)
    assert units["genset_1"][1]["s_mva"]["value"] == pytest.approx(10.0 / 0.8)
    ups = units["ups_battery"][1]
    assert ups["kind"] == "bess" and ups["p_mw"]["value"] == 15.0 and ups["e_mwh"]["value"] == 15.0
    # an inverter is rated to deliver its MW at power factor 0.95
    assert ups["s_mva"]["value"] == pytest.approx(15.0 / 0.95)
    assert "grid_supply" not in units                                 # that is the grid itself
    assert "bess_new" not in units                                    # built 0 MW
    assert any("bess_new" in s for s in draft.skipped)


def test_a_generator_the_expansion_did_not_build_is_skipped_and_listed():
    n = hub()
    n.generators.loc["genset_new", "p_nom_opt"] = 0.0
    draft = draft_campus(n)
    assert "genset_new" not in _by_pypsa(draft.spec["campus"]["units"])
    assert any("genset_new" in s and "0 MW" in s for s in draft.skipped)
    build_campus(draft.spec)


def test_a_battery_holds_its_power_times_its_hours():
    n = hub()
    n.storage_units.loc["bess_new", "p_nom_opt"] = 10.0                 # built: 10 MW, 4 h
    units = _by_pypsa(draft_campus(n).spec["campus"]["units"])
    assert units["bess_new"][1]["e_mwh"]["value"] == 40.0


@pytest.mark.parametrize("mw, sn, vk", [
    (0.95 * 40.0, 40.0, 10.0),      # the top of the 25-40 MVA class
    (0.95 * 25.0, 25.0, 8.0),       # the top of the 6.3-25 MVA class
    (0.95 * 63.0, 63.0, 11.0),
])
def test_a_size_class_boundary_belongs_to_the_class_below(mw, sn, vk):
    n = hub()
    n.links.loc["grid_import", ["p_nom", "p_nom_opt"]] = mw
    _, imp = _by_pypsa(draft_campus(n).spec["campus"]["transformers"])["grid_import"]
    assert imp["sn_mva"]["value"] == sn and imp["vk_percent"]["value"] == vk


def test_a_load_is_rated_at_its_peak_hour():
    units = _by_pypsa(draft_campus(hub()).spec["campus"]["units"])
    assert units["cooling"][1]["p_mw"]["value"] == pytest.approx(14.0)
    assert units["it_load"][1]["p_mw"]["value"] == pytest.approx(36.0 + 0.5 * max(np.sin(np.arange(HOURS))))
    assert units["it_load"][1]["kind"] == "load"


def test_the_pcc_fault_level_comes_from_the_project_or_a_typical_value_for_the_voltage():
    pcc = draft_campus(hub(sk=250.0)).spec["campus"]["pcc"]
    assert pcc["sk_max_mva"] == {"value": 250.0, "source": "assumed"}
    assert pcc["sk_min_mva"]["value"] == 250.0
    pcc = draft_campus(hub(sk=None)).spec["campus"]["pcc"]
    assert pcc["sk_max_mva"]["value"] == 3000.0                       # 132 kV class default
    assert pcc["sk_max_mva"]["source"] == "assumed"


def test_every_number_in_the_draft_is_tagged_assumed():
    def walk(x):
        if isinstance(x, dict):
            if set(x) == {"value", "source"}:
                yield x
            else:
                for v in x.values():
                    yield from walk(v)
    tags = list(walk(draft_campus(hub()).spec))
    assert tags and all(t["source"] == "assumed" for t in tags)


def test_names_are_canonical_unique_and_keep_the_project_name():
    c = draft_campus(hub()).spec["campus"]
    names = [*c["transformers"], *c["units"], *c.get("cables", {})]
    assert len(names) == len(set(names))
    assert all(len(nm) <= 12 for nm in names)
    assert {s["pypsa_name"] for s in c["transformers"].values()} == {"grid_import", "site_transformer"}
    assert short_names(["site_transformer", "site_transformer_2"]) == ["SITE_TRANSFO", "SITE_TRANS_2"]
    # a cut that ends on a separator does not keep it
    assert short_names(["Data Center Energy Hub"]) == ["DATA_CENTER"]
    assert short_names(["Data Center Energy Hub", "Data Center Energy Hub"]) == ["DATA_CENTER", "DATA_CENTE_2"]


def test_the_draft_round_trips_through_yaml(tmp_path):
    draft = draft_campus(hub())
    path = tmp_path / "campus.yaml"
    path.write_text(yaml.safe_dump(draft.spec, sort_keys=False))
    a = build_campus(yaml.safe_load(path.read_text())).net
    b = build_campus(draft.spec).net
    pp.runpp(a), pp.runpp(b)
    assert list(a.res_bus["vm_pu"]) == pytest.approx(list(b.res_bus["vm_pu"]), abs=1e-12)


def test_a_link_between_buses_of_the_same_voltage_becomes_a_short_bus_tie():
    n = hub()
    n.add("Bus", "dc_mv_b", v_nom=33.0, carrier="AC")
    n.add("Link", "tie", bus0="dc_mv", bus1="dc_mv_b", p_nom=20.0, p_nom_opt=20.0)
    n.add("Load", "hall_b", bus="dc_mv_b", p_set=5.0)
    c = draft_campus(n).spec["campus"]
    cables = _by_pypsa(c.get("cables", {}))
    assert "tie" in cables
    build_campus({"campus": c})


# --------------------------------------------------------------------------
# what the draft cannot honestly translate is refused, with the fix named
# --------------------------------------------------------------------------

def test_an_unknown_carrier_is_refused_with_the_carrier_named():
    n = hub()
    n.add("Generator", "mystery", bus="dc_mv", carrier="fusion", p_nom=5.0, p_nom_opt=5.0)
    with pytest.raises(ContractError, match="fusion"):
        draft_campus(n)


def test_a_bus_added_after_the_poc_tag_carries_nan_and_is_not_a_pcc():
    n = hub()
    n.add("Bus", "spare", v_nom=33.0, carrier="AC")
    n.add("Link", "spare_tie", bus0="dc_mv", bus1="spare", p_nom=5.0)
    assert pd.isna(n.buses.at["spare", "eh_poc"]) or not n.buses.at["spare", "eh_poc"]
    assert draft_campus(n).spec["campus"]["pcc"]["pypsa_name"] == "grid"


def test_a_project_without_a_marked_pcc_is_refused():
    n = hub()
    n.buses["eh_poc"] = False
    with pytest.raises(ContractError, match="PCC"):
        draft_campus(n)


def test_non_electrical_parts_are_left_out_and_said_so():
    n = hub()
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "electrolyser", bus0="dc_mv", bus1="h2", p_nom=5.0, p_nom_opt=5.0)
    draft = draft_campus(n)
    units = _by_pypsa(draft.spec["campus"]["units"])
    # power drawn from an AC bus into another carrier is a load on that bus
    assert units["electrolyser"][1]["kind"] == "load"
    assert units["electrolyser"][1]["p_mw"]["value"] == 5.0
    assert "h2" not in {s.get("pypsa_name") for s in draft.spec["campus"]["buses"].values()}


def test_a_fuel_cell_feeding_an_ac_bus_becomes_an_inverter_source_at_its_ac_rating():
    # the H2 hub: a fuel cell Link from the H2 bus into the AC hub
    n = hub()
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "fuel_cell", bus0="h2", bus1="dc_mv", carrier="fuel cell", p_nom=10.0,
          p_nom_extendable=True, p_nom_opt=14.0, efficiency=0.5)
    draft = draft_campus(n)
    eid, fc = _by_pypsa(draft.spec["campus"]["units"])["fuel_cell"]
    assert fc["kind"] == "fuel_cell"
    assert fc["bus"] == _by_pypsa(draft.spec["campus"]["buses"])["dc_mv"][0]
    assert fc["p_mw"]["value"] == pytest.approx(7.0)               # 14 MW of H2 in, 7 MW AC out
    assert fc["s_mva"]["value"] >= fc["p_mw"]["value"]
    assert {fc[f]["source"] for f in ("p_mw", "s_mva", "k_sc", "rx_sc")} == {"assumed"}
    campus = build_campus(draft.spec)
    assert campus.units.at[eid, "kind"] == "fuel_cell"


def test_an_engine_link_feeding_an_ac_bus_becomes_a_genset():
    n = hub()
    n.add("Bus", "gas", carrier="gas")
    n.add("Link", "gas_engine", bus0="gas", bus1="dc_mv", carrier="gas", p_nom=10.0, efficiency=0.4)
    _, g = _by_pypsa(draft_campus(n).spec["campus"]["units"])["gas_engine"]
    assert g["kind"] == "genset" and g["p_mw"]["value"] == pytest.approx(4.0)


def test_a_link_source_of_an_unknown_carrier_is_refused_with_the_carrier_named():
    n = hub()
    n.add("Bus", "heat", carrier="heat")
    n.add("Link", "orc", bus0="heat", bus1="dc_mv", carrier="organic rankine", p_nom=2.0)
    with pytest.raises(ContractError, match="organic rankine"):
        draft_campus(n)


def test_a_normally_open_tie_at_the_pcc_gives_its_rating_as_the_connection_capacity():
    # the microgrid: its subsea tie never imports, so the peak import is no P_ref
    n = hub()
    n.links.loc["grid_import", "p_max_pu"] = 0.0
    pcc = draft_campus(n).spec["campus"]["pcc"]
    assert pcc["p_connection_mw"] == {"value": 40.0, "source": "assumed"} and pcc["tie_normally_open"] is True
    assert build_campus(draft_campus(n).spec).tie_normally_open


def test_a_tie_that_conducts_leaves_the_connection_capacity_to_the_peak_import():
    pcc = draft_campus(hub()).spec["campus"]["pcc"]
    assert "p_connection_mw" not in pcc and "tie_normally_open" not in pcc
    assert not build_campus(draft_campus(hub()).spec).tie_normally_open


def test_an_open_backup_link_beside_a_conducting_line_at_the_pcc_is_no_open_tie():
    n = hub()
    n.links.loc["grid_import", "p_max_pu"] = 0.0                     # the backup link is open...
    n.add("Line", "feeder", bus0="grid", bus1="dc_mv", s_nom=60.0, x=0.5, r=0.1, length=1.0)
    pcc = draft_campus(n).spec["campus"]["pcc"]                       # ...but the line conducts
    assert "p_connection_mw" not in pcc and "tie_normally_open" not in pcc


@pytest.mark.parametrize("extra", [{"carrier": "H2"}, {"carrier": "", "eh_role": "fuel_cell"}])
def test_a_gui_drawn_fuel_cell_is_typed_by_its_role_or_its_h2_input(extra):
    # the GUI palette draws a fuel cell as a Link with carrier 'H2'
    n = hub()
    n.add("Bus", "h2", carrier="H2")
    if "eh_role" in extra:
        n.links["eh_role"] = None
    n.add("Link", "fc", bus0="h2", bus1="dc_mv", p_nom=4.0, efficiency=0.5, carrier=extra["carrier"])
    if "eh_role" in extra:
        n.links.loc["fc", "eh_role"] = extra["eh_role"]
    _, fc = _by_pypsa(draft_campus(n).spec["campus"]["units"])["fc"]
    assert fc["kind"] == "fuel_cell" and fc["p_mw"]["value"] == pytest.approx(2.0)


C_NF_PER_KM = 250.0


def _feeder(f_hz):
    """``hub()`` with a 2 km cable whose susceptance is ``2π f_hz · C``."""
    n = hub()
    n.add("Line", "feeder", bus0="grid", bus1="dc_mv", s_nom=60.0, x=0.5, r=0.1, length=2.0,
          b=2 * math.pi * f_hz * C_NF_PER_KM * 1e-9 * 2.0)
    return n


@pytest.mark.parametrize("f_hz", [50, 60])
def test_a_line_keeps_its_capacitance_at_the_networks_frequency(f_hz):
    n = _feeder(f_hz)
    if f_hz != 50:
        n.meta["f_hz"] = f_hz
    spec = draft_campus(n).spec["campus"]
    _, cable = _by_pypsa(spec["cables"])["feeder"]
    assert spec["f_hz"] == f_hz
    assert cable["c_nf_per_km"]["value"] == pytest.approx(C_NF_PER_KM, rel=1e-12)


def test_a_stated_frequency_wins_over_the_networks():
    n = _feeder(60)
    n.meta["f_hz"] = 50
    spec = draft_campus(n, f_hz=60).spec["campus"]
    _, cable = _by_pypsa(spec["cables"])["feeder"]
    assert spec["f_hz"] == 60
    assert cable["c_nf_per_km"]["value"] == pytest.approx(C_NF_PER_KM, rel=1e-12)


@pytest.mark.parametrize("bad", [7, "60", True, 60.5])
def test_a_frequency_other_than_50_or_60_is_refused(bad):
    n = _feeder(50)
    n.meta["f_hz"] = bad
    with pytest.raises(ContractError, match="f_hz"):
        draft_campus(n)
