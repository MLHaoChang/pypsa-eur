"""AC load flow at the campus's critical hours, and transformer sizing (C4a).

One selected hour puts the hourly table on the campus net:
* each generating unit's P, out of service when its status is 0;
* each load's P, with Q from its power factor.

The hour is then solved intact and, for every group of parallel
transformers, once with each unit out (N-1). The sizing recommends, per
transformer group, the unit rating that carries the worst hour:
    max(total intact S / units, S on a survivor under N-1) * (1 + margin),
rounded up to the next standard MVA.

The oracle is the same network written directly in pandapower, with the
same hour set by hand.
"""
import math

import pandas as pd
import pandapower as pp
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.campus import STANDARD_MVA
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_flow import (
    SizingCriteria,
    apply_hour,
    size_transformers,
    solve_cases,
    trafo_groups,
)
from tests.gridspine.test_campus import campus_spec, direct_net

HOUR = {"DC_LOAD": -45.0, "BESS1": -10.0, "PV1": 12.0, "GEN1": 0.0}


def rows(hour=HOUR, status=None, period=2030, h=7):
    status = status or {}
    return pd.DataFrame([{"unit_id": u, "period": period, "hour": h, "p_mw": p,
                          "status": status.get(u, 1)} for u, p in hour.items()])


def oracle(hour=HOUR, out=(), run=True):
    net = direct_net()
    load = net.load.index[net.load["name"] == "DC_LOAD"][0]
    net.load.at[load, "p_mw"] = -hour["DC_LOAD"]
    net.load.at[load, "q_mvar"] = -hour["DC_LOAD"] * math.tan(math.acos(0.98))
    for name in ("BESS1", "PV1", "GEN1"):
        i = net.sgen.index[net.sgen["name"] == name][0]
        net.sgen.at[i, "p_mw"] = hour[name]
    for name in out:
        net.trafo.loc[net.trafo["name"] == name, "in_service"] = False
    if run:
        pp.runpp(net)
    return net


def test_an_hour_on_the_campus_solves_as_the_same_hour_written_by_hand():
    camp = build_campus(campus_spec())
    cases = solve_cases(camp, rows())
    intact = cases["intact"]
    ref = oracle()
    assert intact.converged
    assert intact.pcc_p_mw == pytest.approx(float(ref.res_ext_grid["p_mw"].iloc[0]), abs=1e-7)
    assert intact.pcc_q_mvar == pytest.approx(float(ref.res_ext_grid["q_mvar"].iloc[0]), abs=1e-7)
    vm = dict(zip(ref.bus["name"], ref.res_bus["vm_pu"]))
    assert intact.bus.set_index("bus")["vm_pu"].to_dict() == pytest.approx(vm, abs=1e-9)
    tr = intact.trafo.set_index("trafo")
    for name in ("TR1", "TR2"):
        i = ref.trafo.index[ref.trafo["name"] == name][0]
        s_ref = math.hypot(ref.res_trafo.at[i, "p_hv_mw"], ref.res_trafo.at[i, "q_hv_mvar"])
        assert tr.at[name, "s_mva"] == pytest.approx(s_ref, abs=1e-7)
        assert tr.at[name, "loading_pct"] == pytest.approx(ref.res_trafo.at[i, "loading_percent"], abs=1e-7)
    p_in = float(ref.res_ext_grid["p_mw"].iloc[0])
    p_out = 45.0 + 10.0 - 12.0
    assert intact.losses_mw == pytest.approx(p_in - p_out, abs=1e-7)


def test_with_one_of_two_parallel_transformers_out_the_survivor_carries_the_hour():
    cases = solve_cases(build_campus(campus_spec()), rows())
    assert set(cases) == {"intact", "N-1:TR1", "N-1:TR2"}
    for out, survivor in (("TR1", "TR2"), ("TR2", "TR1")):
        ref = oracle(out=(out,))
        tr = cases[f"N-1:{out}"].trafo.set_index("trafo")
        i = ref.trafo.index[ref.trafo["name"] == survivor][0]
        assert cases[f"N-1:{out}"].converged
        assert tr.at[survivor, "loading_pct"] == pytest.approx(ref.res_trafo.at[i, "loading_percent"], abs=1e-7)
        assert out not in tr.index


def test_a_unit_with_status_zero_is_out_of_service():
    camp = build_campus(campus_spec())
    apply_hour(camp, rows(status={"PV1": 0}, hour=dict(HOUR, PV1=0.0)))
    sgen = camp.net.sgen.set_index("name")
    assert not bool(sgen.at["PV1", "in_service"]) and bool(sgen.at["BESS1", "in_service"])
    assert sgen.at["BESS1", "p_mw"] == -10.0


def test_the_load_takes_its_power_factor():
    camp = build_campus(campus_spec())
    apply_hour(camp, rows())
    load = camp.net.load.set_index("name").loc["DC_LOAD"]
    assert load["p_mw"] == 45.0 and load["q_mvar"] == pytest.approx(45.0 * math.tan(math.acos(0.98)))


def test_an_hour_missing_a_unit_is_refused():
    with pytest.raises(ContractError, match="GEN1"):
        apply_hour(build_campus(campus_spec()), rows(hour={k: v for k, v in HOUR.items() if k != "GEN1"}))


def test_parallel_transformers_form_one_group():
    assert trafo_groups(build_campus(campus_spec())) == {"TR1+TR2": ["TR1", "TR2"]}


def test_the_recommended_unit_rating_covers_the_worst_hour_with_margin_and_n_minus_1():
    camp = build_campus(campus_spec())
    hours = [rows(h=7), rows(hour=dict(HOUR, DC_LOAD=-50.0, PV1=0.0, BESS1=-12.0), h=19)]
    flows = {(2030, int(r["hour"].iloc[0])): solve_cases(camp, r) for r in hours}
    sizing = size_transformers(flows, camp, SizingCriteria(margin=0.2)).set_index("group").loc["TR1+TR2"]
    worst = flows[(2030, 19)]
    s_total = worst["intact"].trafo["s_mva"].sum()
    s_n1 = max(worst[c].trafo["s_mva"].max() for c in ("N-1:TR1", "N-1:TR2"))
    need = max(s_total / 2, s_n1) * 1.2
    assert sizing["units"] == 2 and sizing["unit_rating_mva"] == 40.0
    assert sizing["max_s_intact_mva"] == pytest.approx(s_total)
    assert sizing["max_s_n1_mva"] == pytest.approx(s_n1)
    assert sizing["required_unit_mva"] == pytest.approx(need)
    assert sizing["worst_period"] == 2030 and sizing["worst_hour"] == 19
    # N-1 binds: 68.6 MVA on the survivor, 82.3 MVA with margin -> 100 MVA units
    assert s_n1 > s_total / 2
    assert sizing["recommended_unit_mva"] == next(s for s in STANDARD_MVA if s >= need - 1e-9) == 100.0
    assert bool(sizing["adequate"]) == (40.0 >= need)


def test_a_single_transformer_needs_no_n_minus_1_case():
    spec = campus_spec()
    del spec["campus"]["transformers"]["TR2"]
    camp = build_campus(spec)
    cases = solve_cases(camp, rows())
    assert set(cases) == {"intact"}
    sizing = size_transformers({(2030, 7): cases}, camp, SizingCriteria(margin=0.0)).iloc[0]
    assert sizing["max_s_n1_mva"] == 0.0
    assert sizing["required_unit_mva"] == pytest.approx(cases["intact"].trafo["s_mva"].iloc[0])


def test_a_hour_that_does_not_converge_is_flagged_not_sized_from():
    camp = build_campus(campus_spec())
    cases = solve_cases(camp, rows(hour=dict(HOUR, DC_LOAD=-5000.0)))
    assert not cases["intact"].converged
    sizing = size_transformers({(2030, 7): cases}, camp, SizingCriteria()).iloc[0]
    assert sizing["unconverged_hours"] == 1


def test_without_n_minus_1_each_unit_is_sized_for_its_share_of_the_intact_flow():
    camp = build_campus(campus_spec())
    cases = solve_cases(camp, rows())
    sizing = size_transformers({(2030, 7): cases}, camp, SizingCriteria(margin=0.1, n_minus_1=False)).iloc[0]
    total = cases["intact"].trafo["s_mva"].sum()
    assert sizing["max_s_n1_mva"] == 0.0 and not sizing["n_minus_1"]
    assert sizing["required_unit_mva"] == pytest.approx(total / 2 * 1.1)


def test_the_hour_reports_each_cable_loading():
    cases = solve_cases(build_campus(campus_spec()), rows())
    ref = oracle()
    line = cases["intact"].line.set_index("cable")
    assert line.at["CB1", "loading_pct"] == pytest.approx(float(ref.res_line.at[0, "loading_percent"]), abs=1e-7)
    assert line.at["CB1", "i_ka"] == pytest.approx(float(ref.res_line.at[0, "i_ka"]), abs=1e-9)


def test_an_hour_can_be_solved_with_a_reactive_dispatch_in_place():
    """The setpoints put Q on named sgens and steps on named shunts before
    every case is solved; the oracle is the hand-built net with the same."""
    spec = campus_spec()
    spec["campus"]["compensation"] = [
        {"name": "CAP1", "bus": "MV1", "kind": "capacitor_bank", "q_mvar": {"value": 9.0, "source": "assumed"}, "steps": 3},
        {"name": "ST1", "bus": "MV2", "kind": "statcom", "q_mvar": {"value": 5.0, "source": "assumed"}}]
    cases = solve_cases(build_campus(spec), rows(),
                        setpoints={"sgen_q": {"BESS1": 4.0, "ST1": -2.0}, "shunt_step": {"CAP1": 2}})
    for case, out in (("intact", ()), ("N-1:TR1", ("TR1",))):
        ref = oracle(out=out, run=False)
        ref.sgen.at[ref.sgen.index[ref.sgen["name"] == "BESS1"][0], "q_mvar"] = 4.0
        pp.create_sgen(ref, ref.bus.index[ref.bus["name"] == "MV2"][0], p_mw=0.0, q_mvar=-2.0)
        pp.create_shunt(ref, ref.bus.index[ref.bus["name"] == "MV1"][0], q_mvar=-3.0, step=2, max_step=3)
        pp.runpp(ref)
        assert cases[case].pcc_q_mvar == pytest.approx(float(ref.res_ext_grid["q_mvar"].iloc[0]), abs=1e-7)
