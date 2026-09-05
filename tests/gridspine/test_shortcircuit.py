"""Task 6: IEC 60909 three-phase fault levels — with the fault state's OWN mapping.

THE TRAP, from `_apply_res`'s docstring and binding here: `apply_snapshot`
maps a curtailed RES unit to `in_service=False`. That is correct for load flow
(a zero-injection PQ element and an absent one are the same node equation) and
WRONG for short circuit: a curtailed inverter is still energised, still
synchronised, and still feeds fault current. `in_service=False` deletes it
from the fault calculation and understates the level at exactly the buses the
study is about. So this module has its own status -> element mapping,
`apply_fault_state`, and the test that matters compares the two mappings on a
curtailed hour and demands a NON-TRIVIAL difference — the mutation check
points `fault_levels` back at the load-flow mapping and that test must go red.

pandapower 3.1.2, probed: `calc_sc` needs `s_sc_max_mva`/`rx_max` on every
ext_grid, `vn_kv`/`xdss_pu`/`rdss_ohm`/`cos_phi`/`sn_mva` on every gen and
`k`/`rx`/`sn_mva` on every sgen; a missing COLUMN raises but a missing per-unit
VALUE is not its problem, so element coverage is asserted here before the
solve. Its result frame is `res_bus_sc` with `ikss_ka` and `skss_mw` (an MVA
figure despite the name), mapped onto the schema's `ikss_ka`/`sk_mva`.

The hand-checkable case: a single feeder of S_k'' = 1000 MVA at 110 kV gives
I_k'' = S/(sqrt(3) U) = 5.2486 kA at its own bus — the voltage factor c cancels
there — and 4.0149 kA one 1+j4 ohm line downstream (c = 1.1).
"""
import copy
import math

import numpy as np
import pandapower as pp
import pandas as pd
import pytest
import yaml

from gridspine.ingest.pandapower_source import RES_LEDGER, load_case39_res, registry_from_net
from gridspine.schema.contingency import validate_fault_levels
from gridspine.schema.contracts import ContractError
from gridspine.static.loadflow import apply_snapshot
from gridspine.static.shortcircuit import (
    FAULT_LEDGER,
    apply_fault_state,
    fault_levels,
    set_sc_params,
)
from gridspine.templates.unit_params import load_unit_templates

HOUR = 0
RES_CF = 0.3
N_BUSES = 39


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _hour_tables(net, registry, curtailed=()):
    cap = {e["name"]: e["p_mw"] for e in RES_LEDGER}
    rows = []
    for unit_id, rec in registry.iterrows():
        status = 1
        if rec["kind"] == "gen":
            i = net.gen.index[net.gen["name"] == unit_id][0]
            p = float(net.gen.at[i, "p_mw"])
        elif rec["kind"] == "res":
            p = RES_CF * cap[unit_id]
        else:
            p = 0.0
        if unit_id in curtailed:
            p, status = 0.0, 0
        rows.append({"unit_id": unit_id, "hour": HOUR, "p_mw": p, "q_mvar": 0.0, "status": status})
    name_of = net.bus["name"]
    by_bus = net.load.groupby("bus")[["p_mw", "q_mvar"]].sum()
    loads = pd.DataFrame({
        "bus": [name_of.at[b] for b in by_bus.index], "hour": HOUR,
        "p_mw": by_bus["p_mw"].values, "q_mvar": by_bus["q_mvar"].values,
    })
    return pd.DataFrame(rows), loads


@pytest.fixture(scope="module")
def case39():
    net = load_case39_res()
    reg = registry_from_net(net)
    return net, reg, load_unit_templates()


def _p(v, s="assumed"):
    return {"value": v, "source": s}


def _toy_feeder():
    """One feeder, one line, one load. S_sc = mbase/xd_pp = 100/0.1 = 1000 MVA."""
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=110.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=110.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b2, length_km=10.0,
        r_ohm_per_km=0.1, x_ohm_per_km=0.4, c_nf_per_km=0.0, max_i_ka=1.0,
    )
    pp.create_load(net, bus=b2, p_mw=20.0, q_mvar=5.0)
    return net


def _toy_templates(tmp_path):
    units = {"SLK_BUS_01": {
        "model": "GENROU", "mbase_mva": 100.0, "include_in_inertia": False,
        "params": {
            "h_s": _p(20.0, "datasheet"), "d": _p(0.0),
            "xd": _p(0.3, "datasheet"), "xq": _p(0.28, "datasheet"),
            "xd_p": _p(0.15, "datasheet"), "xq_p": _p(0.17, "datasheet"),
            "xd_pp": _p(0.1), "xl": _p(0.05, "datasheet"),
            "t_do_p": _p(6.5, "datasheet"), "t_qo_p": _p(1.5, "datasheet"),
            "t_do_pp": _p(0.05), "t_qo_pp": _p(0.05), "s1": _p(0.05), "s12": _p(0.3),
            "rx_sc": _p(0.1), "cos_phi": _p(0.85),
        },
    }}
    f = tmp_path / "toy.yaml"
    f.write_text(yaml.safe_dump({"units": units}, sort_keys=False))
    return load_unit_templates(f)


# --------------------------------------------------------------------------
# the hand-checkable case
# --------------------------------------------------------------------------

def test_feeder_bus_fault_level_matches_the_hand_calculation(tmp_path):
    net = _toy_feeder()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg)
    fl = fault_levels(net, dispatch, loads, HOUR, reg, _toy_templates(tmp_path), case="max")
    fl = fl.set_index("bus")
    assert fl.at["BUS_01", "ikss_ka"] == pytest.approx(1000.0 / (math.sqrt(3) * 110.0), rel=1e-4)
    assert fl.at["BUS_01", "sk_mva"] == pytest.approx(1000.0, rel=1e-4)
    # downstream: Z_Q = 1.1*110^2/1000 ohm at R/X 0.1, plus 1+j4 ohm of line.
    zq = 1.1 * 110.0 ** 2 / 1000.0
    xq, rq = zq / math.sqrt(1 + 0.1 ** 2), 0.1 * zq / math.sqrt(1 + 0.1 ** 2)
    zk = abs(complex(rq + 1.0, xq + 4.0))
    assert fl.at["BUS_02", "ikss_ka"] == pytest.approx(1.1 * 110.0 / (math.sqrt(3) * zk), rel=2e-3)


def test_ext_grid_strength_is_derived_from_its_template_machine(tmp_path):
    net = _toy_feeder()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg)
    work = copy.deepcopy(net)
    apply_fault_state(work, dispatch, loads, HOUR, reg, _toy_templates(tmp_path))
    e = work.ext_grid.iloc[0]
    assert e["s_sc_max_mva"] == pytest.approx(100.0 / 0.1)
    assert e["rx_max"] == pytest.approx(0.1)
    assert e["s_sc_min_mva"] == pytest.approx(e["s_sc_max_mva"])


# --------------------------------------------------------------------------
# case39_res
# --------------------------------------------------------------------------

def test_case39_fault_levels_validate_and_cover_every_bus(case39):
    net, reg, t = case39
    dispatch, loads = _hour_tables(net, reg)
    fl = fault_levels(net, dispatch, loads, HOUR, reg, t, case="max")
    validate_fault_levels(fl)
    assert len(fl) == N_BUSES
    assert set(fl["bus"]) == set(net.bus["name"])
    assert (fl["case"] == "max").all()
    assert (fl["ikss_ka"] > 0).all() and (fl["sk_mva"] > 0).all()


def test_case39_min_case_never_exceeds_max(case39):
    net, reg, t = case39
    dispatch, loads = _hour_tables(net, reg)
    mx = fault_levels(net, dispatch, loads, HOUR, reg, t, case="max").set_index("bus")
    mn = fault_levels(net, dispatch, loads, HOUR, reg, t, case="min").set_index("bus")
    assert (mn["case"] == "min").all()
    assert (mn["ikss_ka"] <= mx["ikss_ka"] + 1e-9).all()


def test_fault_levels_do_not_mutate_the_callers_net(case39):
    """The fault state flips curtailed sgens back in service; on the CALLER's
    net that would flip STAT in a later .raw. So the work happens on a copy."""
    net, reg, t = case39
    dispatch, loads = _hour_tables(net, reg, curtailed=("W_BUS_33",))
    work = copy.deepcopy(net)
    apply_snapshot(work, dispatch, loads, hour=HOUR, registry=reg)
    i = work.sgen.index[work.sgen["name"] == "W_BUS_33"][0]
    assert not work.sgen.at[i, "in_service"]
    before = (work.sgen["in_service"].copy(), work.gen["in_service"].copy(), list(work.gen.columns))
    fault_levels(work, dispatch, loads, HOUR, reg, t)
    pd.testing.assert_series_equal(work.sgen["in_service"], before[0])
    pd.testing.assert_series_equal(work.gen["in_service"], before[1])
    assert list(work.gen.columns) == before[2]


# --------------------------------------------------------------------------
# THE mapping test
# --------------------------------------------------------------------------

def test_a_curtailed_inverter_still_feeds_the_fault(case39):
    """Same hour, two mappings. The load-flow mapping (`apply_snapshot` ->
    `_apply_res`) takes a curtailed W_BUS_33 out of service; the fault-state
    mapping keeps it energised. The fault level at BUS_33 must be HIGHER under
    the fault-state mapping by a non-trivial margin — 600 MVA at k=1.2 is
    ~1.2 kA of contribution on a bus in the ~10 kA class."""
    net, reg, t = case39
    dispatch, loads = _hour_tables(net, reg, curtailed=("W_BUS_33",))

    right = fault_levels(net, dispatch, loads, HOUR, reg, t).set_index("bus")

    wrong_net = copy.deepcopy(net)
    apply_snapshot(wrong_net, dispatch, loads, hour=HOUR, registry=reg)  # _apply_res: out of service
    set_sc_params(wrong_net, reg, t)
    import pandapower.shortcircuit as sc
    sc.calc_sc(wrong_net, fault="3ph", case="max")
    wrong = wrong_net.res_bus_sc["ikss_ka"].values
    wrong_at_33 = float(wrong[list(wrong_net.bus["name"]).index("BUS_33")])

    assert right.at["BUS_33", "ikss_ka"] > wrong_at_33 * 1.05, (right.at["BUS_33", "ikss_ka"], wrong_at_33)


def test_a_decommitted_thermal_unit_is_out_of_the_fault_calculation(case39):
    """The other half of the mapping: a synchronous machine with status 0 is
    disconnected and contributes nothing — unlike a curtailed inverter."""
    net, reg, t = case39
    on_d, on_l = _hour_tables(net, reg)
    off_d, off_l = _hour_tables(net, reg, curtailed=("G_BUS_30",))
    on = fault_levels(net, on_d, on_l, HOUR, reg, t).set_index("bus")
    off = fault_levels(net, off_d, off_l, HOUR, reg, t).set_index("bus")
    assert off.at["BUS_30", "ikss_ka"] < on.at["BUS_30", "ikss_ka"] * 0.95


# --------------------------------------------------------------------------
# coverage: nothing is silently skipped
# --------------------------------------------------------------------------

def test_a_machine_without_sc_parameters_is_a_contract_error_not_a_skip(case39, tmp_path):
    net, reg, _t = case39
    raw = yaml.safe_load(open("gridspine/templates/data/case39_units.yaml"))
    del raw["units"]["G_BUS_34"]["params"]["cos_phi"]
    f = tmp_path / "gap.yaml"
    f.write_text(yaml.safe_dump(raw, sort_keys=False))
    dispatch, loads = _hour_tables(net, reg)
    with pytest.raises(ContractError, match="G_BUS_34.*cos_phi"):
        fault_levels(net, dispatch, loads, HOUR, reg, load_unit_templates(f))


def test_a_machine_with_no_template_row_is_a_contract_error(case39, tmp_path):
    net, reg, _t = case39
    raw = yaml.safe_load(open("gridspine/templates/data/case39_units.yaml"))
    del raw["units"]["S_BUS_36"]
    f = tmp_path / "gap.yaml"
    f.write_text(yaml.safe_dump(raw, sort_keys=False))
    dispatch, loads = _hour_tables(net, reg)
    with pytest.raises(ContractError, match="S_BUS_36"):
        fault_levels(net, dispatch, loads, HOUR, reg, load_unit_templates(f))


def test_sc_params_land_on_every_element(case39):
    net, reg, t = case39
    work = copy.deepcopy(net)
    set_sc_params(work, reg, t)
    assert work.gen[["vn_kv", "xdss_pu", "rdss_ohm", "cos_phi", "sn_mva"]].notna().all().all()
    assert work.sgen[["k", "rx", "sn_mva"]].notna().all().all()
    assert work.ext_grid[["s_sc_max_mva", "rx_max", "s_sc_min_mva", "rx_min"]].notna().all().all()
    # xdss on the machine base the template states; terminal voltage is the bus's
    g = work.gen.set_index("name")
    assert g.at["G_BUS_39", "xdss_pu"] == pytest.approx(0.0045)
    assert g.at["G_BUS_39", "sn_mva"] == pytest.approx(100.0)
    assert g.at["G_BUS_39", "vn_kv"] == pytest.approx(work.bus.at[int(g.at["G_BUS_39", "bus"]), "vn_kv"])
    s = work.sgen.set_index("name")
    assert s.at["W_BUS_33", "k"] == pytest.approx(1.2) and s.at["W_BUS_33", "sn_mva"] == pytest.approx(600.0)


def test_unknown_case_is_rejected(case39):
    net, reg, t = case39
    dispatch, loads = _hour_tables(net, reg)
    with pytest.raises(ContractError, match="case"):
        fault_levels(net, dispatch, loads, HOUR, reg, t, case="typical")


def test_fault_ledger_records_the_assumptions():
    text = " ".join(FAULT_LEDGER)
    for word in ("ext_grid", "curtail", "energised", "s_sc_min", "cos_phi"):
        assert word in text, word


def test_module_imports_pandapower_but_not_pypsa():
    import gridspine.static.shortcircuit as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "import pypsa" not in src and "gridspine.producers" not in src
