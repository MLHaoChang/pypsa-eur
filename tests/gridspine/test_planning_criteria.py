"""DC issue 13 (PR a): emergency ratings, the two-stage check, bus-section loss.

The hand-built net is generic and meshed (not radial): a 110 kV ring A-B-C
with two parallel lines A-B, a spur B-S, and two 110/20 kV transformers T1
(B-D1) and T2 (C-D2) whose 20 kV sides are tied by a line, so losing T2
leaves D2 fed through T1 and the tie at a low voltage that T1's on-load
taps or D2's switched shunt can bring back.

The acceptance fixture: losing one parallel A-B line loads the other to
115 % of its normal rating and 96 % of its emergency rating. It passes
stage 1 and fails stage 2, because no named control relieves a thermal
overload and no redispatch is modelled.
"""
import copy
import math

import numpy as np
import pandapower as pp
import pandas as pd
import pytest

import gridspine.static.planning_criteria as planning_criteria
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.contingency import (
    validate_contingency_set,
    validate_planning_results,
)
from gridspine.schema.contracts import ContractError
from gridspine.static.capacity import capacity_ac
from gridspine.static.contingency import branch_loading_pct, screen_n1
from gridspine.static.contingency_set import (
    BUS_SECTION_LEDGER,
    branch_contingencies,
    bus_section_contingencies,
    unit_contingencies,
)
from gridspine.static.loadflow import apply_snapshot
from gridspine.static.planning_criteria import (
    NO_CONTROLS,
    NO_CONTROLS_ON,
    NO_REDISPATCH,
    PLANNING_LEDGER,
    Controls,
    emergency_loading_pct,
    screen_planning,
    validate_emergency_ratings,
)
from tests.gridspine.test_shortcircuit import HOUR, _hour_tables

PARALLEL = ["BUS_A-BUS_B-1", "BUS_A-BUS_B-2"]
T1, T2 = "BUS_B-BUS_D1-1", "BUS_C-BUS_D2-1"
SURVIVOR_LOADING = 1.15     # of normal, after one parallel line is lost
EMERGENCY_FACTOR = 1.20     # emergency rating over normal


def _net():
    n = pp.create_empty_network(sn_mva=100)
    b = {k: pp.create_bus(n, 110, name=k) for k in ("BUS_A", "BUS_B", "BUS_C", "BUS_S")}
    d = {k: pp.create_bus(n, 20, name=k) for k in ("BUS_D1", "BUS_D2")}
    pp.create_ext_grid(n, b["BUS_A"], vm_pu=1.0, name="SLK")
    lt = dict(r_ohm_per_km=0.06, x_ohm_per_km=0.4, c_nf_per_km=9, max_i_ka=1.0)
    for f, t, km in (("BUS_A", "BUS_B", 30), ("BUS_A", "BUS_B", 30), ("BUS_A", "BUS_C", 40),
                     ("BUS_C", "BUS_B", 60), ("BUS_B", "BUS_S", 10)):
        pp.create_line_from_parameters(n, b[f], b[t], km, **lt)
    tt = dict(sn_mva=40, vn_hv_kv=110, vn_lv_kv=20, vk_percent=12, vkr_percent=0.4, pfe_kw=0,
              i0_percent=0, tap_side="hv", tap_neutral=0, tap_min=-9, tap_max=9,
              tap_step_percent=1.5, tap_pos=0, tap_changer_type="Ratio")
    pp.create_transformer_from_parameters(n, b["BUS_B"], d["BUS_D1"], **tt)
    pp.create_transformer_from_parameters(n, b["BUS_C"], d["BUS_D2"], **tt)
    pp.create_line_from_parameters(n, d["BUS_D1"], d["BUS_D2"], 6, r_ohm_per_km=0.2,
                                   x_ohm_per_km=0.35, c_nf_per_km=10, max_i_ka=1.0)
    pp.create_gen(n, b["BUS_C"], p_mw=20, vm_pu=1.0, name="G1")
    pp.create_load(n, b["BUS_B"], p_mw=120, q_mvar=30)
    pp.create_load(n, b["BUS_S"], p_mw=10, q_mvar=2)
    pp.create_load(n, d["BUS_D1"], p_mw=14, q_mvar=5)
    pp.create_load(n, d["BUS_D2"], p_mw=16, q_mvar=6)
    pp.create_shunt(n, d["BUS_D2"], q_mvar=-3.5, p_mw=0, step=0, max_step=4, name="SH1")
    return n


def _tables(net):
    reg = registry_from_net(net)
    dispatch = pd.DataFrame([
        {"unit_id": u, "hour": 0, "p_mw": float(net.gen.loc[net.gen["name"] == u, "p_mw"].iloc[0])
         if rec["kind"] == "gen" else 0.0, "q_mvar": 0.0, "status": 1}
        for u, rec in reg.iterrows()
    ])
    by_bus = net.load.groupby("bus")[["p_mw", "q_mvar"]].sum()
    loads = pd.DataFrame({"bus": [net.bus.at[b, "name"] for b in by_bus.index], "hour": 0,
                          "p_mw": by_bus["p_mw"].values, "q_mvar": by_bus["q_mvar"].values})
    return dispatch, loads, reg


def _rated(net, emergency=True):
    """Rate the parallel A-B lines so that losing one loads the other to
    SURVIVOR_LOADING of normal (measured, not guessed)."""
    probe = copy.deepcopy(net)
    probe.line.at[1, "in_service"] = False
    pp.runpp(probe)
    normal = float(probe.res_line.at[0, "i_from_ka"]) / SURVIVOR_LOADING
    net.line.loc[[0, 1], "max_i_ka"] = normal
    if emergency:
        net.line["max_i_ka_emergency"] = np.nan
        net.line.loc[[0, 1], "max_i_ka_emergency"] = normal * EMERGENCY_FACTOR
    return net


def _screen(net, cset, **kw):
    dispatch, loads, reg = _tables(net)
    return screen_planning(net, cset, dispatch, loads, 0, reg, **kw)


def _one(results, cid):
    return results.set_index("contingency_id").loc[cid]


def _set(net, kinds=("branch",)):
    parts = {"branch": lambda: branch_contingencies(net),
             "unit": lambda: unit_contingencies(registry_from_net(net)),
             "bus_section": lambda: bus_section_contingencies(net)}
    return pd.concat([parts[k]() for k in kinds], ignore_index=True)


# --------------------------------------------------------------------------
# emergency ratings
# --------------------------------------------------------------------------

def test_emergency_loading_matches_a_hand_calculation_with_parallel_and_df():
    net = _net()
    net.line.at[2, "parallel"] = 2
    net.line.at[2, "df"] = 0.8
    net.line["max_i_ka_emergency"] = np.nan
    net.line.at[2, "max_i_ka_emergency"] = 1.25
    net.trafo["sn_mva_emergency"] = np.nan
    net.trafo.at[0, "sn_mva_emergency"] = 50.0
    i_from = np.full(len(net.line), 0.5)
    i_hv = np.full(len(net.trafo), 0.2)
    got = emergency_loading_pct(net, i_from, i_hv)
    assert got[2] == pytest.approx(0.5 / (1.25 * 0.8 * 2) * 100.0, rel=1e-12)
    nl = len(net.line)
    assert got[nl] == pytest.approx(0.2 * math.sqrt(3) * 110.0 / 50.0 * 100.0, rel=1e-12)
    # none given: the normal rating, exactly as branch_loading_pct
    normal = branch_loading_pct(net, i_from, i_hv)
    assert got[0] == pytest.approx(normal[0], rel=1e-12)
    assert got[nl + 1] == pytest.approx(normal[nl + 1], rel=1e-12)


@pytest.mark.parametrize("value, match", [
    (0.5, "below the normal rating"),
    (0.0, "must be > 0"),
    (-1.0, "must be > 0"),
    (float("inf"), "finite"),
    ("high", "finite"),
])
def test_bad_emergency_rating_is_refused_naming_the_element(value, match):
    net = _net()
    net.line["max_i_ka_emergency"] = pd.Series([np.nan] * len(net.line), dtype=object)
    net.line.at[3, "max_i_ka_emergency"] = value
    with pytest.raises(ContractError, match=match) as err:
        validate_emergency_ratings(net)
    assert "BUS_C-BUS_B-1" in str(err.value)


def test_emergency_equal_to_normal_passes():
    net = _net()
    net.line["max_i_ka_emergency"] = net.line["max_i_ka"]
    net.trafo["sn_mva_emergency"] = net.trafo["sn_mva"]
    validate_emergency_ratings(net)


def test_emergency_columns_survive_the_way_into_the_screen():
    net = load_case39_res()
    reg = registry_from_net(net)
    net.line["max_i_ka_emergency"] = net.line["max_i_ka"] * 1.2
    net.trafo["sn_mva_emergency"] = net.trafo["sn_mva"] * 1.3
    dispatch, loads = _hour_tables(net, reg)
    apply_snapshot(net, dispatch, loads, hour=HOUR, registry=reg)
    for n in (net, copy.deepcopy(net), pp.from_json_string(pp.to_json(net))):
        np.testing.assert_allclose(n.line["max_i_ka_emergency"], n.line["max_i_ka"] * 1.2)
        np.testing.assert_allclose(n.trafo["sn_mva_emergency"], n.trafo["sn_mva"] * 1.3)


# --------------------------------------------------------------------------
# the two stages
# --------------------------------------------------------------------------

def test_acceptance_passes_stage_1_on_emergency_and_fails_stage_2():
    net = _rated(_net())
    res, _ = _screen(net, _set(net))
    row = _one(res, PARALLEL[1])
    assert row["stage1_branch"] == PARALLEL[0]
    assert row["stage1_loading_pct"] == pytest.approx(100.0 * SURVIVOR_LOADING / EMERGENCY_FACTOR, rel=1e-6)
    assert row["stage1_verdict"] == "pass"
    assert row["stage2_branch"] == PARALLEL[0]
    assert row["stage2_loading_pct"] == pytest.approx(100.0 * SURVIVOR_LOADING, rel=1e-6)
    assert row["stage2_verdict"] == "fail"
    assert NO_REDISPATCH in row["note"]


def test_no_emergency_rating_judges_stage_1_on_normal_and_says_so():
    net = _rated(_net(), emergency=False)
    res, _ = _screen(net, _set(net))
    row = _one(res, PARALLEL[1])
    assert row["stage1_loading_pct"] == pytest.approx(100.0 * SURVIVOR_LOADING, rel=1e-6)
    assert row["stage1_verdict"] == "fail"
    n = len(net.line) + len(net.trafo) - 1          # per row: the outaged line is not counted
    assert f"no emergency rating given ({n} of {n} branches)" in row["note"]


def test_partial_emergency_ratings_are_counted():
    net = _rated(_net())
    res, _ = _screen(net, _set(net))
    n = len(net.line) + len(net.trafo) - 1          # per row: the outaged line is not counted
    assert f"no emergency rating given ({n - 1} of {n} branches)" in _one(res, PARALLEL[1])["note"]


def test_without_controls_stage_2_re_solves_unchanged_on_normal_ratings():
    net = _net()
    res, _ = _screen(net, _set(net))
    row = _one(res, T2)
    assert row["stage1_verdict"] == "pass"
    assert row["stage2_verdict"] == "fail"           # D2 low, nothing named to fix it
    assert row["stage2_min_vm_pu"] < 0.9
    assert row["remedial"] == "none"
    assert NO_CONTROLS in row["note"]
    # unchanged: stage 2 is the post-outage state, judged on normal ratings
    probe = copy.deepcopy(net)
    probe.trafo.at[1, "in_service"] = False
    pp.runpp(probe)
    assert row["stage2_min_vm_pu"] == pytest.approx(float(probe.res_bus["vm_pu"].min()), abs=1e-9)


def test_named_oltc_clears_the_low_voltage():
    net = _net()
    res, _ = _screen(net, _set(net), controls=Controls(oltc=(T1,)))
    row = _one(res, T2)
    assert row["stage2_verdict"] == "pass"
    assert row["stage2_min_vm_pu"] >= 0.9
    assert row["remedial"].startswith(f"{T1} -")     # taps toward the LV side's boost
    assert row["note"].endswith(NO_REDISPATCH) and NO_CONTROLS not in row["note"]


def test_named_switched_shunt_clears_the_low_voltage():
    net = _net()
    res, _ = _screen(net, _set(net), controls=Controls(switched_shunts=("SH1",)))
    row = _one(res, T2)
    assert row["stage2_verdict"] == "pass"
    assert row["remedial"].startswith("SH1 +")
    assert 0 < int(row["remedial"].split("+")[1]) <= 4


def test_walk_does_not_move_when_every_voltage_is_in_band():
    net = _net()
    res, _ = _screen(net, _set(net), controls=Controls(oltc=(T1,), switched_shunts=("SH1",)))
    assert _one(res, PARALLEL[1])["remedial"] == "none"


def test_with_both_named_losing_the_oltc_leaves_the_shunt_to_clear_it():
    # GK condition 1, with the OLTC-only test above: losing T1 takes its taps
    # out, so the named shunt alone clears that low voltage.
    net = _net()
    res, _ = _screen(net, _set(net), controls=Controls(oltc=(T1,), switched_shunts=("SH1",)))
    t2, t1 = _one(res, T2), _one(res, T1)
    assert t2["stage2_verdict"] == t1["stage2_verdict"] == "pass"
    assert t1["remedial"].startswith("SH1 +") and T1 not in t1["remedial"]
    assert t1["stage2_min_vm_pu"] >= 0.9


def test_losing_the_only_named_control_says_none_is_in_service():
    net = _net()
    res, _ = _screen(net, _set(net), controls=Controls(oltc=(T1,)))
    row = _one(res, T1)
    assert row["remedial"] == "none"
    assert NO_CONTROLS_ON in row["note"]


def test_stage_2_not_converging_at_the_chosen_controls_is_not_solved(monkeypatch):
    net = _net()
    walked = []
    real_solve, real_walk = planning_criteria._solve, planning_criteria.remedial_walk

    def walk(*a, **kw):
        out = real_walk(*a, **kw)
        walked.append(bool(out[1]))
        return out

    def solve(*a, **kw):
        return None if walked and walked[-1] else real_solve(*a, **kw)

    monkeypatch.setattr(planning_criteria, "remedial_walk", walk)
    monkeypatch.setattr(planning_criteria, "_solve", solve)
    res, _ = _screen(net, _set(net), controls=Controls(oltc=(T1,)))
    row = _one(res, T2)
    assert row["stage1_verdict"] == "pass"
    assert row["stage2_verdict"] == "not_solved"
    assert row["remedial"].startswith(f"{T1} -")
    assert "stage 2 does not converge at the chosen controls" in row["note"]


def test_unknown_or_inert_control_is_refused():
    net = _net()
    with pytest.raises(ContractError, match="not a transformer"):
        _screen(net, _set(net), controls=Controls(oltc=("BUS_A-BUS_B-1",)))
    with pytest.raises(ContractError, match="not on the net"):
        _screen(net, _set(net), controls=Controls(switched_shunts=("SH9",)))
    net.trafo.at[0, "tap_changer_type"] = None
    with pytest.raises(ContractError, match="tap_changer_type"):
        _screen(net, _set(net), controls=Controls(oltc=(T1,)))


def test_unit_outage_reports_the_generation_lost():
    net = _net()
    res, _ = _screen(net, _set(net, ("unit",)))
    row = _one(res, "G1")
    assert row["gen_lost_mw"] == pytest.approx(20.0)
    assert row["load_lost_mw"] == 0.0
    assert row["stage1_verdict"] in ("pass", "fail")


# --------------------------------------------------------------------------
# bus sections
# --------------------------------------------------------------------------

def test_bus_section_feeding_a_spur_is_islanded_and_names_it():
    net = _net()
    res, _ = _screen(net, _set(net, ("bus_section",)))
    row = _one(res, "BUS_B")
    assert bool(row["islanded"]) and not bool(row["converged"])
    assert row["stage1_verdict"] == row["stage2_verdict"] == "not_solved"
    assert math.isnan(row["stage2_loading_pct"])
    assert "BUS_S" in row["note"]
    assert row["load_lost_mw"] == pytest.approx(130.0)       # the section's 120 + the spur's 10


def test_bus_section_reports_load_and_generation_lost():
    net = _net()
    res, _ = _screen(net, _set(net, ("bus_section",)))
    row = _one(res, "BUS_C")
    assert not bool(row["islanded"]) and bool(row["converged"])
    assert row["gen_lost_mw"] == pytest.approx(20.0)
    assert row["load_lost_mw"] == 0.0
    d2 = _one(res, "BUS_D2")
    assert d2["load_lost_mw"] == pytest.approx(16.0)


def test_bus_section_set_skips_the_slack_and_dead_buses():
    net = _net()
    net.bus.at[3, "in_service"] = False                 # BUS_S
    cset = bus_section_contingencies(net)
    assert list(cset["contingency_id"]) == ["BUS_B", "BUS_C", "BUS_D1", "BUS_D2"]
    assert (cset["kind"] == "bus_section").all() and (cset["order"] == 1).all()
    assert list(bus_section_contingencies(net, ["BUS_C", "BUS_S"])["contingency_id"]) == ["BUS_C"]


def test_bus_section_set_refuses_unknown_and_slack_buses():
    net = _net()
    with pytest.raises(ContractError, match="not on the net"):
        bus_section_contingencies(net, ["BUS_X"])
    with pytest.raises(ContractError, match="slack"):
        bus_section_contingencies(net, ["BUS_A"])


def test_n1_screens_refuse_a_bus_section_row_instead_of_dropping_it():
    net = _net()
    cset = _set(net, kinds=("branch", "bus_section"))
    dispatch, loads, reg = _tables(net)
    with pytest.raises(ContractError, match="kind not judged here.*bus_section"):
        screen_n1(net, cset, dispatch, loads, 0, reg)
    with pytest.raises(ContractError, match="kind not judged here.*bus_section"):
        capacity_ac(net, "BUS_B", "load", cset)


def test_unit_and_bus_sharing_a_name_fail_closed_naming_both_kinds():
    net = _net()
    net.gen.at[0, "name"] = "BUS_C"
    both = pd.concat([unit_contingencies(registry_from_net(net)), bus_section_contingencies(net)],
                     ignore_index=True)
    with pytest.raises(ContractError, match=r"duplicate contingency_id: BUS_C \(kinds: bus_section, unit\)"):
        validate_contingency_set(both)


def test_ledgers_state_the_assumptions():
    assert any("split busbar" in s for s in BUS_SECTION_LEDGER)
    assert any("thermal loading only" in s for s in PLANNING_LEDGER)
    assert any("no redispatch" in s for s in PLANNING_LEDGER)


# --------------------------------------------------------------------------
# the results contract
# --------------------------------------------------------------------------

def test_results_contract_refuses_a_solved_stage_2_after_an_unsolved_stage_1():
    net = _rated(_net())
    res, _ = _screen(net, _set(net))
    bad = res.copy()
    i = bad.index[bad["contingency_id"] == PARALLEL[1]][0]
    bad.at[i, "converged"] = False
    bad.at[i, "stage1_verdict"] = "not_solved"
    bad.at[i, "stage1_loading_pct"] = np.nan
    with pytest.raises(ContractError, match="stage 2 solved where stage 1 is not"):
        validate_planning_results(bad)
    bad = res.copy()
    bad.at[i, "stage2_verdict"] = "ok"
    with pytest.raises(ContractError, match="stage2_verdict must be one of"):
        validate_planning_results(bad)


# --------------------------------------------------------------------------
# case39
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def case39_hour():
    net = load_case39_res()
    reg = registry_from_net(net)
    dispatch, loads = _hour_tables(net, reg)
    apply_snapshot(net, dispatch, loads, hour=HOUR, registry=reg)
    return net, dispatch, loads, reg


def test_case39_every_kind_is_judged_counted_and_timed(case39_hour):
    from tests.gridspine.test_contingency import ISLANDING
    net, dispatch, loads, reg = case39_hour
    before = copy.deepcopy(net)
    cset = pd.concat([branch_contingencies(net), unit_contingencies(reg), bus_section_contingencies(net)],
                     ignore_index=True)
    res, report = screen_planning(net, cset, dispatch, loads, HOUR, reg)
    assert list(res["contingency_id"]) == list(cset["contingency_id"])
    assert report["counts"] == {"branch": 46, "bus_section": 38, "unit": 14}
    assert report["seconds"] > 0.0
    print(f"case39 planning check, {len(cset)} contingencies: {report['seconds']} s")
    # the same islanding outages screen_n1 finds
    branch = res[res["kind"] == "branch"]
    assert set(branch.loc[branch["islanded"], "contingency_id"]) == ISLANDING
    # the one non-islanded collapse is screen_n1's too (G_BUS_39, ruling 16)
    assert list(res.loc[~res["islanded"] & ~res["converged"], "contingency_id"]) == ["G_BUS_39"]
    assert res["note"].str.contains(NO_CONTROLS).sum() == (res["stage2_verdict"] != "not_solved").sum()
    assert pp.nets_equal(net, before, check_only_results=False)
