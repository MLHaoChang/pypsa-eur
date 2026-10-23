"""DC issue 13: emergency ratings, the two-stage check, bus-section loss (PR a);
N-M-1 / N-1-1 with the user cap, and the angle-limit check (PR b).

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
from gridspine.static.contingency import branch_loading_pct, screen_n1, screen_n2
from gridspine.static.contingency_set import (
    BUS_SECTION_LEDGER,
    branch_contingencies,
    bus_section_contingencies,
    n2_candidates,
    n_1_1_contingencies,
    n_m_1_contingencies,
    unit_contingencies,
)
from gridspine.static.loadflow import apply_snapshot
from gridspine.static.planning_criteria import (
    NO_CONTROLS,
    NO_CONTROLS_ON,
    NO_REDISPATCH,
    PLANNING_LEDGER,
    AngleLimit,
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
# N-M-1 and N-1-1
# --------------------------------------------------------------------------

AC, CB, T1_T2 = "BUS_A-BUS_C-1", "BUS_C-BUS_B-1", "BUS_D1-BUS_D2-1"
SPUR = "BUS_B-BUS_S-1"
BOTH = Controls(oltc=(T1,), switched_shunts=("SH1",))


def test_compound_ids_are_ordered_pairs_sorted_and_stable():
    net = _net()
    n1 = branch_contingencies(net)
    n11 = n_1_1_contingencies(n1)
    assert len(n11) == 8 * 7
    assert list(n11["contingency_id"]) == sorted(n11["contingency_id"])
    assert f"{AC}>{CB}" in set(n11["contingency_id"]) and f"{CB}>{AC}" in set(n11["contingency_id"])
    assert (n11["kind"] == "n_1_1").all() and (n11["order"] == 2).all()
    row = n11.set_index("contingency_id").loc[f"{AC}>{CB}"]
    assert list(row["element_ids"]) == [AC, CB]
    pd.testing.assert_frame_equal(n11, n_1_1_contingencies(n1.iloc[::-1]))
    nm1 = n_m_1_contingencies(n1, maintenance=[CB, AC])
    assert len(nm1) == 2 * 7
    assert list(nm1["contingency_id"]) == sorted(f"{m}|{f}" for m in (AC, CB) for f in n1["contingency_id"] if f != m)
    assert (nm1["kind"] == "n_m_1").all()
    assert len(n_m_1_contingencies(n1)) == 8 * 7


def test_compound_enumeration_refuses_bad_input():
    net = _net()
    n1 = branch_contingencies(net)
    with pytest.raises(ContractError, match="maintenance outage.*not in the N-1 branch set.*BUS_X"):
        n_m_1_contingencies(n1, maintenance=["BUS_X"])
    for build in (n_1_1_contingencies, n_m_1_contingencies):
        with pytest.raises(ContractError, match="non-branch rows"):
            build(_set(net, ("branch", "unit")))
        with pytest.raises(ContractError, match="order != 1"):
            build(n2_candidates(n1))


def test_contingency_set_contract_ties_a_compound_id_to_its_elements():
    net = _net()
    nm1 = n_m_1_contingencies(branch_contingencies(net), maintenance=[AC])
    bad = nm1.head(1).copy()
    bad.at[0, "contingency_id"] = f"{CB}|{AC}"
    with pytest.raises(ContractError, match="joined on '\\|'"):
        validate_contingency_set(bad)
    bad = nm1.head(1).copy()
    bad.at[0, "kind"] = "branch"
    with pytest.raises(ContractError, match="compound separator on kind 'branch'"):
        validate_contingency_set(bad)
    bad = nm1.head(1).copy()
    bad.at[0, "contingency_id"] = f"{AC}|{CB}>{T1}"
    with pytest.raises(ContractError, match="more than one compound separator"):
        validate_contingency_set(bad)
    bad = nm1.head(1).copy()
    bad.at[0, "contingency_id"], bad.at[0, "element_ids"], bad.at[0, "order"] = AC, [AC], 1
    with pytest.raises(ContractError, match="n_m_1 contingency is order 2"):
        validate_contingency_set(bad)


def test_n_1_1_re_adjusts_after_the_first_outage():
    net = _rated(_net())
    one, other = f"{CB}>{T2}", f"{T2}>{CB}"
    cset = n_1_1_contingencies(branch_contingencies(net))
    cset = cset[cset["contingency_id"].isin([one, other])].reset_index(drop=True)
    res, _ = _screen(net, cset, controls=BOTH)
    a, b = _one(res, one), _one(res, other)
    # losing T2 first: SH1 re-adjusts before the second fault, so it has nothing left to do after it
    assert f"re-adjusted after the first outage ({T2}): SH1 +3" in b["note"]
    assert "SH1" in a["remedial"] and "SH1" not in b["remedial"]
    assert a["stage2_verdict"] == b["stage2_verdict"] == "pass"
    assert NO_REDISPATCH in a["note"]


def test_n_m_1_judges_the_maintenance_outage_then_the_fault():
    net = _rated(_net())
    nm1 = n_m_1_contingencies(branch_contingencies(net), maintenance=[PARALLEL[0]])
    res, report = _screen(net, nm1)
    row = _one(res, f"{PARALLEL[0]}|{PARALLEL[1]}")
    assert row["stage1_verdict"] == "fail"          # both parallel lines gone: A-B rides on A-C and C-B
    assert report["counts"] == {"n_m_1": 7}
    assert report["summary"]["n_m_1"]["verdict"] == "fail"


def test_first_outage_islanding_is_not_solved_at_the_first_outage():
    net = _net()
    cset = n_1_1_contingencies(branch_contingencies(net))
    cset = cset[cset["contingency_id"] == f"{SPUR}>{AC}"].reset_index(drop=True)
    res, _ = _screen(net, cset, angle_limit=AngleLimit(30.0, "assumed"))
    row = _one(res, f"{SPUR}>{AC}")
    assert bool(row["islanded"]) and not bool(row["converged"])
    assert row["stage1_verdict"] == row["stage2_verdict"] == row["angle_verdict"] == "not_solved"
    assert f"islanded at the first outage ({SPUR}): disconnects BUS_S" in row["note"]
    assert row["load_lost_mw"] == pytest.approx(10.0)       # the spur's load goes with the first outage


def test_first_outage_divergence_is_not_solved_at_the_first_outage(monkeypatch):
    net = _net()
    cset = n_1_1_contingencies(branch_contingencies(net))
    cset = cset[cset["contingency_id"] == f"{AC}>{CB}"].reset_index(drop=True)
    monkeypatch.setattr(planning_criteria, "_solve", lambda work, judged: None)
    res, _ = _screen(net, cset, angle_limit=AngleLimit(30.0, "assumed"))
    row = _one(res, f"{AC}>{CB}")
    assert not bool(row["islanded"]) and not bool(row["converged"])
    assert row["stage1_verdict"] == row["angle_verdict"] == "not_solved"
    assert row["note"] == f"load flow does not converge at the first outage ({AC})"


def test_the_cap_keeps_the_first_rows_by_id_and_counts_the_rest():
    net = _net()
    n1 = branch_contingencies(net)
    cset = pd.concat([n1, n_1_1_contingencies(n1), n_m_1_contingencies(n1, maintenance=[AC])], ignore_index=True)
    res, report = _screen(net, cset, max_compound=3)
    assert report["counts"] == {"branch": 8, "n_1_1": 3, "n_m_1": 3}
    assert report["skipped"] == {"n_1_1": 56 - 3, "n_m_1": 7 - 3}
    kept = res.loc[res["kind"] == "n_1_1", "contingency_id"]
    assert list(kept) == sorted(n_1_1_contingencies(n1)["contingency_id"])[:3]
    assert "53 of 56 n_1_1 rows skipped" in report["summary"]["n_1_1"]["note"]
    _, uncapped = _screen(net, n1)
    assert uncapped["skipped"] == {"n_1_1": 0, "n_m_1": 0}
    for bad in (-1, 1.5, True, "3"):
        with pytest.raises(ContractError, match="max_compound"):
            _screen(net, n1, max_compound=bad)


def test_a_capped_kind_whose_judged_rows_all_pass_is_not_a_pass():
    net = _rated(_net())
    nm1 = n_m_1_contingencies(branch_contingencies(net), maintenance=[CB])
    nm1 = nm1[nm1["contingency_id"].isin([f"{CB}|{AC}", f"{CB}|{T1_T2}"])].reset_index(drop=True)
    res, report = _screen(net, nm1, max_compound=1, angle_limit=AngleLimit(30.0, "assumed"))
    assert len(res) == 1
    assert (res["stage1_verdict"] == "pass").all() and (res["stage2_verdict"] == "pass").all()
    assert (res["angle_verdict"] == "pass").all()
    summary = report["summary"]["n_m_1"]
    assert summary["verdict"] == summary["angle_verdict"] == "incomplete"
    assert summary["note"] == "1 of 2 n_m_1 rows skipped by max_compound"
    _, full = _screen(net, nm1, angle_limit=AngleLimit(30.0, "assumed"))
    assert full["summary"]["n_m_1"]["verdict"] == "pass"


def test_screen_n1_capacity_and_n2_refuse_compound_rows_instead_of_dropping_them():
    net = _net()
    n1 = branch_contingencies(net)
    dispatch, loads, reg = _tables(net)
    for compound in (n_1_1_contingencies(n1).head(2), n_m_1_contingencies(n1, maintenance=[AC]).head(2)):
        cset = pd.concat([n1, compound], ignore_index=True)
        with pytest.raises(ContractError, match="screen_n1 takes an N-1 set"):
            screen_n1(net, cset, dispatch, loads, 0, reg)
        with pytest.raises(ContractError, match="order != 1"):
            capacity_ac(net, "BUS_B", "load", cset)
        with pytest.raises(ContractError, match="n2_candidates takes an N-1 set"):
            n2_candidates(cset)
        with pytest.raises(ContractError, match="kind != branch"):
            screen_n2(net, compound, dispatch, loads, 0, reg, 50.0)


def test_capacity_evaluate_refuses_compound_rows():
    from gridspine.static.capacity import _evaluate
    net = _net()
    compound = n_1_1_contingencies(branch_contingencies(net)).head(1)
    with pytest.raises(ContractError, match="kind not judged here.*n_1_1"):
        _evaluate(copy.deepcopy(net), compound)


# --------------------------------------------------------------------------
# the angle limit
# --------------------------------------------------------------------------

@pytest.mark.parametrize("deg, match", [
    (float("nan"), "finite"), (float("inf"), "finite"), (0.0, "> 0"), (-5.0, "> 0"),
    (True, "finite"), ("30", "finite"), (None, "finite"),
])
def test_angle_limit_refuses_a_bad_figure(deg, match):
    with pytest.raises(ContractError, match=match):
        AngleLimit(deg, "assumed")


def test_angle_limit_needs_a_basis_and_a_reference_for_sagc():
    with pytest.raises(ContractError, match="basis"):
        AngleLimit(30.0, "grid code")
    with pytest.raises(ContractError, match="SAGC clause"):
        AngleLimit(30.0, "sagc")
    with pytest.raises(ContractError, match="angle pair"):
        AngleLimit(30.0, "assumed", pairs=(("BUS_A",),))
    lim = AngleLimit(30, "sagc", reference="SAGC (May 2026), planning criteria")
    assert lim.ledger() == {"deg": 30.0, "basis": "sagc", "reference": "SAGC (May 2026), planning criteria"}


def test_angle_unset_is_unknown_on_every_row_never_pass():
    net = _net()
    res, report = _screen(net, _set(net, ("branch", "unit", "bus_section")))
    solved = res["stage1_verdict"] != "not_solved"
    assert (res.loc[solved, "angle_verdict"] == "unknown").all()
    assert (res.loc[~solved, "angle_verdict"] == "not_solved").all()
    assert report["angle_limit"] == "unset"
    assert all(s["angle_verdict"] != "pass" for s in report["summary"].values())


def test_angle_limit_pass_and_fail_across_a_branch_and_a_bus_pair():
    net = _net()
    cset = _set(net)
    res, _ = _screen(net, cset, angle_limit=AngleLimit(60.0, "assumed", pairs=(AC, ("BUS_B", "BUS_D2"))))
    solved = res[res["stage1_verdict"] != "not_solved"]
    assert (solved["angle_verdict"] == "pass").all()
    assert set(solved["angle_pair"]) <= {AC, "BUS_B:BUS_D2"}
    worst = float(solved["angle_deg"].max())
    res, report = _screen(net, cset, angle_limit=AngleLimit(worst - 1e-6, "assumed",
                                                                pairs=(AC, ("BUS_B", "BUS_D2"))))
    assert (res["angle_verdict"] == "fail").sum() >= 1
    assert report["angle_limit"] == {"deg": worst - 1e-6, "basis": "assumed", "reference": ""}
    with pytest.raises(ContractError, match="not a branch on the net"):
        _screen(net, cset, angle_limit=AngleLimit(30.0, "assumed", pairs=("BUS_X-BUS_Y-1",)))
    with pytest.raises(ContractError, match="not on the net"):
        _screen(net, cset, angle_limit=AngleLimit(30.0, "assumed", pairs=(("BUS_A", "BUS_X"),)))


def test_angle_pair_on_a_lost_bus_is_skipped_and_named():
    net = _net()
    cset = bus_section_contingencies(net, ["BUS_D2"])
    lim = AngleLimit(60.0, "assumed", pairs=(AC, T2))
    res, _ = _screen(net, cset, angle_limit=lim)
    row = _one(res, "BUS_D2")
    assert row["angle_verdict"] == "pass" and row["angle_pair"] == AC
    assert f"angle pairs skipped, a bus out of service: {T2}" in row["note"]
    res, _ = _screen(net, cset, angle_limit=AngleLimit(60.0, "assumed", pairs=(T2,)))
    row = _one(res, "BUS_D2")
    assert row["angle_verdict"] == "unknown" and math.isnan(row["angle_deg"])
    assert "no angle pair measurable" in row["note"]


def test_angle_matches_a_hand_computed_two_bus_case():
    """Lossless, uncharged A-B link, both ends held at 110 kV: P = V^2 sin(d) / X."""
    n = pp.create_empty_network(sn_mva=100)
    a, b = pp.create_bus(n, 110, name="BUS_A"), pp.create_bus(n, 110, name="BUS_B")
    pp.create_ext_grid(n, a, vm_pu=1.0, name="SLK")
    lt = dict(r_ohm_per_km=0.0, x_ohm_per_km=0.4, c_nf_per_km=0.0, max_i_ka=2.0)
    pp.create_line_from_parameters(n, a, b, 50, **lt)
    pp.create_line_from_parameters(n, a, b, 50, **lt)
    pp.create_gen(n, b, p_mw=80.0, vm_pu=1.0, name="G1")
    pp.create_load(n, a, p_mw=1.0, q_mvar=0.0)
    res, _ = _screen(n, branch_contingencies(n).head(1), angle_limit=AngleLimit(30.0, "assumed"))
    expected = math.degrees(math.asin(80.0 * 0.4 * 50 / 110.0**2))
    assert res["angle_deg"].iloc[0] == pytest.approx(expected, rel=1e-9)
    assert res["angle_verdict"].iloc[0] == "pass"


def test_nan_or_inf_shunt_step_is_refused_naming_the_shunt():
    for col, value in (("max_step", np.nan), ("max_step", np.inf), ("step", np.nan), ("step", np.inf)):
        net = _net()
        net.shunt[col] = net.shunt[col].astype(float)
        net.shunt.at[0, col] = value
        with pytest.raises(ContractError, match=f"switched shunt 'SH1' has no finite {col}"):
            _screen(net, _set(net), controls=Controls(switched_shunts=("SH1",)))


def test_results_contract_ties_the_angle_verdict_to_stage_1():
    net = _net()
    res, _ = _screen(net, _set(net), angle_limit=AngleLimit(60.0, "assumed"))
    i = res.index[res["stage1_verdict"] == "pass"][0]
    bad = res.copy()
    bad.at[i, "angle_verdict"] = "not_solved"
    with pytest.raises(ContractError, match="angle_verdict is not_solved exactly where stage 1 is"):
        validate_planning_results(bad)
    bad = res.copy()
    bad.at[i, "angle_deg"] = np.nan
    with pytest.raises(ContractError, match="angle_deg"):
        validate_planning_results(bad)
    bad = res.copy()
    bad.at[i, "angle_verdict"] = "ok"
    with pytest.raises(ContractError, match="angle_verdict must be one of"):
        validate_planning_results(bad)


def test_ledger_states_the_compound_cap_and_angle_assumptions():
    assert any("share mechanics" in s for s in PLANNING_LEDGER)
    assert any("max_compound" in s and "never pass" in s for s in PLANNING_LEDGER)
    assert any("stage-1 state" in s for s in PLANNING_LEDGER)


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
    n1 = branch_contingencies(net)
    compound = pd.concat([n_m_1_contingencies(n1), n_1_1_contingencies(n1)], ignore_index=True)
    cres, creport = screen_planning(net, compound, dispatch, loads, HOUR, reg, max_compound=10,
                                    angle_limit=AngleLimit(30.0, "assumed"))
    assert creport["counts"] == {"n_1_1": 10, "n_m_1": 10}
    assert creport["skipped"] == {"n_1_1": 46 * 45 - 10, "n_m_1": 46 * 45 - 10}
    assert creport["summary"]["n_1_1"]["verdict"] in ("fail", "incomplete")
    assert creport["seconds"] > 0.0
    print(f"case39 compound check, 20 of {len(compound)} contingencies: {creport['seconds']} s")
    # the same islanding outages screen_n1 finds
    branch = res[res["kind"] == "branch"]
    assert set(branch.loc[branch["islanded"], "contingency_id"]) == ISLANDING
    # the one non-islanded collapse is screen_n1's too (G_BUS_39, ruling 16)
    assert list(res.loc[~res["islanded"] & ~res["converged"], "contingency_id"]) == ["G_BUS_39"]
    assert res["note"].str.contains(NO_CONTROLS).sum() == (res["stage2_verdict"] != "not_solved").sum()
    assert pp.nets_equal(net, before, check_only_results=False)
