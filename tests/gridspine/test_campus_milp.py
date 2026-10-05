"""The electrical asset choice as a MILP, solved in a loop of successive
linearisations with an adaptive convergence rate (plan C11).

The oracles do not run the code under test:
* sensitivities are checked against a central finite difference of an AC
  load flow on a network written directly in pandapower here;
* the joint optimum is a brute force over every combination of the tiny
  library, each built and solved directly in pandapower here;
* the polygon is checked against the circle numerically;
* the returned compliance is checked against a direct pandapower solve of
  the returned campus file with the returned dispatch.
"""
import math

import pandapower as pp
import pytest

from gridspine.static.campus_flow import SizingCriteria
from gridspine.static.campus_invest import open_candidates, select_assets
from gridspine.static.campus_reactive import ReactiveRequirement
from tests.gridspine.test_campus_invest import (
    PROFILE, WIDE, H7, comp, dark_hours, hourly, library, lowpf_spec, single_spec, tr,
)


# --------------------------------------------------------------------------
# the open candidate sets: C8's generators without C8's adequacy filters
# --------------------------------------------------------------------------

def test_open_candidates_are_c8s_generators_without_the_adequacy_filter(tmp_path):
    lib = library(tmp_path, transformers=[tr("T40", 40.0, 0.8e6), tr("T63", 63.0, 1.2e6)],
                  capacitor_banks=[comp("capacitor_banks", "CAP3", 3.0, 120e3)])
    table, sel = dark_hours((2030, 25.0))
    out = select_assets(lowpf_spec(existing=False), table, sel, lib, ReactiveRequirement(15.0, "c", "code"), PROFILE)
    st = out["state"]
    by = {n.key: n for n in st.needs}
    lib_by_id = st.lib_by_id
    trafo = by["transformer TR1+TR2"]
    opened = open_candidates(trafo, lib, lib_by_id, SizingCriteria(), 50)
    # a redundant pair stays redundant: n = 2 or 3, every size, cheapest first
    assert sorted(c.items for c in opened) == sorted(
        (("transformer", i, n),) for i in ("T40", "T63") for n in (2, 3))
    assert {c.items for c in trafo.candidates} <= {c.items for c in opened}
    reactive = open_candidates(by["reactive"], lib, lib_by_id, SizingCriteria(), 50)
    assert reactive[0].label == "none" and reactive[0].items == ()
    assert sorted(c.label for c in reactive[1:]) == ["1 x CAP3", "2 x CAP3", "3 x CAP3"]
    # C8's own reactive need had a gap, so "none" was not among its candidates
    assert "none" not in {c.label for c in by["reactive"].candidates}


# --------------------------------------------------------------------------
# sensitivities: the load-flow Jacobian against a finite-difference re-solve
# --------------------------------------------------------------------------

def _quantities(net):
    """Every monitored quantity of a solved net, read from pandapower's own
    result tables."""
    out = {("pcc_q",): float(net.res_ext_grid["q_mvar"].sum())}
    for i, name in zip(net.bus.index, net.bus["name"]):
        out[("vm", name)] = float(net.res_bus.at[i, "vm_pu"])
    for i, name in zip(net.trafo.index, net.trafo["name"]):
        if net.trafo.at[i, "in_service"]:
            out[("trafo_s", name)] = math.hypot(net.res_trafo.at[i, "p_hv_mw"], net.res_trafo.at[i, "q_hv_mvar"])
            out[("trafo_load", name)] = net.res_trafo.at[i, "loading_percent"] / 100 * net.trafo.at[i, "sn_mva"]
    for i, name in zip(net.line.index, net.line["name"]):
        out[("line_i", name)] = float(net.res_line.at[i, "i_ka"])
    return out


def _fd(net, bus, h=0.02):
    """Central difference of every quantity for +-h Mvar injected at ``bus``."""
    import copy
    vals = []
    for dq in (h, -h):
        n = copy.deepcopy(net)
        pp.create_sgen(n, int(n.bus.index[n.bus["name"] == bus][0]), p_mw=0.0, q_mvar=dq)
        pp.runpp(n)
        vals.append(_quantities(n))
    return {k: (vals[0][k] - vals[1][k]) / (2 * h) for k in vals[0]}


def weak_net(out=None):
    """The test campus on a weak grid (Sk 150 MVA) with a 15 km cable, two
    parallel cable runs and a capacitor step in: a strongly loaded,
    strongly non-linear point."""
    from tests.gridspine.test_campus import direct_net
    net = direct_net()
    net.ext_grid.at[0, "s_sc_max_mva"] = 150.0
    net.line.at[0, "length_km"] = 15.0
    net.line.at[0, "parallel"] = 2
    net.load.at[0, "p_mw"], net.load.at[0, "q_mvar"] = 45.0, 20.0
    net.sgen.loc[net.sgen["name"] == "BESS1", ["p_mw", "q_mvar"]] = (5.0, 3.0)
    pp.create_shunt(net, 1, q_mvar=-2.0, p_mw=0.0, step=1, max_step=2, name="CAP_1")
    if out is not None:
        net.trafo.at[out, "in_service"] = False
    pp.runpp(net)
    return net


@pytest.mark.parametrize("out", [None, 1])
def test_jacobian_sensitivities_match_a_finite_difference_ac_re_solve(out):
    from gridspine.static.campus_milp import sensitivities
    net = weak_net(out)
    got = sensitivities(net, ["MV1", "MV2", "PCC"])
    checked = 0
    for bus in ("MV1", "MV2", "PCC"):
        want = _fd(net, bus)
        for key, d in want.items():
            assert got[key][bus] == pytest.approx(d, rel=1e-4, abs=1e-7), (bus, key)
            checked += 1
    assert checked == 3 * (1 + 3 + (2 if out is None else 1) * 2 + 1)
    # the signs a planner expects: Q injected at MV2 lifts its voltage, and
    # reduces what the grid supplies by more than itself (the losses fall)
    assert got[("vm", "MV2")]["MV2"] > 0
    assert got[("pcc_q",)]["MV2"] < -1.0
    assert got[("pcc_q",)]["PCC"] == -1.0 and got[("vm", "MV1")]["PCC"] == 0.0


# --------------------------------------------------------------------------
# the inverter capability polygon is inscribed in its circle
# --------------------------------------------------------------------------

def test_the_capability_polygon_is_inscribed_in_the_circle_and_touches_it_at_its_vertices():
    from gridspine.static.campus_milp import POLYGON_SIDES, polygon, q_range
    import numpy as np
    normals, apothem = polygon()
    assert POLYGON_SIDES == 8 and normals.shape == (8, 2)
    s = 22.0
    # every point of the polygon is inside the circle: sample its boundary
    # by walking each side between its two vertices (intersections, solved here)
    for i in range(8):
        a1, a2 = normals[i], normals[(i + 1) % 8]
        vertex = np.linalg.solve(np.array([a1, a2]), np.array([apothem * s, apothem * s]))
        assert np.hypot(*vertex) == pytest.approx(s, rel=1e-12)           # the vertices lie on the circle
    rng = np.random.default_rng(0)
    pts = rng.uniform(-s, s, size=(20000, 2))
    inside = (pts @ normals.T <= apothem * s + 1e-12).all(axis=1)
    assert inside.mean() == pytest.approx(2 * math.sqrt(2) / 4, abs=0.01)     # the octagon's area, 2 sqrt(2) S^2
    assert (np.hypot(pts[inside, 0], pts[inside, 1]) <= s + 1e-9).all()
    # it is not trivially small: it contains the circle of radius S cos(pi/8)
    near = np.hypot(pts[:, 0], pts[:, 1]) <= s * math.cos(math.pi / 8) - 1e-9
    assert inside[near].all()
    # the Q range at a fixed P: inside the circle's, equal at P = 0 and at a vertex
    for p in np.linspace(-s, s, 41):
        lo, hi = q_range(p, s)
        circle = math.sqrt(max(s * s - p * p, 0.0))
        assert -circle - 1e-9 <= lo <= 0.0 <= hi <= circle + 1e-9
        assert lo == pytest.approx(-hi)
    assert q_range(0.0, s) == pytest.approx((-s, s))
    p45 = s * math.cos(math.pi / 4)
    assert q_range(p45, s)[1] == pytest.approx(s * math.sin(math.pi / 4))
    # and at its worst it gives up 1 - cos(pi/8) of S (midway between vertices)
    p = s * math.cos(math.pi / 8) * math.cos(3 * math.pi / 8)
    assert math.hypot(p, q_range(p, s)[1]) == pytest.approx(s * math.cos(math.pi / 8))


# --------------------------------------------------------------------------
# the joint optimum: a slightly larger transformer removes the compensation
# --------------------------------------------------------------------------

RATE = 0.05
TA = ("T_A", 40.0, 14.0, 1.00e6)            # cheap, 14 % impedance: more reactive loss
TB = ("T_B", 50.0, 10.0, 1.10e6)            # a little dearer, 10 %: less
CAP = ("CAP4", 4.0, 2, 0.30e6)              # 4 Mvar in two 2 Mvar steps, 25 years
DARK25 = {"DC_LOAD": -25.0, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}


def crf(r, n):
    return r / (1 - (1 + r) ** -n)


def annual(capex, life, opex):
    return capex * (crf(RATE, life) + opex)


def joint_net(n, s, vk, banks=0, steps=0, out=None):
    """The single-transformer test campus in pandapower: n units of (s, vk)
    at 110/20 kV, ``banks`` 4 Mvar banks with ``steps`` 2 Mvar steps in, every
    inverter off, the load at 25 MW and pf 0.85."""
    from tests.gridspine.test_campus import direct_net
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    for i in range(n):
        pp.create_transformer_from_parameters(net, 0, 1, sn_mva=s, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4,
                                              vk_percent=vk, pfe_kw=25.0, i0_percent=0.05, name=f"T{i}")
    if out is not None:
        net.trafo.at[out, "in_service"] = False
    net.line.at[0, "max_i_ka"] = 3.0
    net.load.at[0, "p_mw"] = 25.0
    net.load.at[0, "q_mvar"] = 25.0 * math.tan(math.acos(0.85))
    net.sgen["in_service"] = False
    if banks:
        pp.create_shunt(net, 1, q_mvar=-2.0, p_mw=0.0, step=steps, max_step=2 * banks)
    pp.runpp(net)
    return net


def joint_feasible(n, s, vk, banks, q_limit, margin=0.2):
    """The independent AC check of n units with ``banks`` banks: some step
    count passes the PCC band (intact), and every case passes the sizing
    rule with the margin, the loading and the 0.90-1.10 pu bands."""
    for steps in range(2 * banks + 1):
        cases = [joint_net(n, s, vk, banks, steps)] + \
            ([joint_net(n, s, vk, banks, steps, out=k) for k in range(n)] if n >= 2 else [])
        if abs(float(cases[0].res_ext_grid["q_mvar"].sum())) > q_limit + 0.011:
            continue
        ok = True
        for c in cases:
            on = c.trafo["in_service"].to_numpy()
            s_unit = ((c.res_trafo["p_hv_mw"] ** 2 + c.res_trafo["q_hv_mvar"] ** 2) ** 0.5).to_numpy()[on]
            ok &= bool((s_unit * (1 + margin) <= s + 1e-9).all())
            ok &= bool((c.res_trafo["loading_percent"].to_numpy()[on] <= 100.0).all())
            ok &= bool(((c.res_bus["vm_pu"] >= 0.9) & (c.res_bus["vm_pu"] <= 1.1)).all())
        if ok:
            return True
    return False


def joint_case(tmp_path):
    lib = library(tmp_path, transformers=[tr(i, s, capex, vk=vk) for i, s, vk, capex in (TA, TB)],
                  capacitor_banks=[comp("capacitor_banks", CAP[0], CAP[1], CAP[3], steps=CAP[2])])
    spec = single_spec()
    spec["campus"]["units"]["DC_LOAD"]["pf"] = {"value": 0.85, "source": "assumed"}
    # the PCC band midway between what T_A and T_B draw (solved here)
    q_a = float(joint_net(1, TA[1], TA[2]).res_ext_grid["q_mvar"].sum())
    q_b = float(joint_net(1, TB[1], TB[2]).res_ext_grid["q_mvar"].sum())
    assert q_b + 0.5 < q_a
    req = ReactiveRequirement((q_a + q_b) / 2, "test band", "code")
    table, sel = dark_hours((2030, 25.0))
    return spec, table, sel, lib, req


def test_the_milp_finds_the_joint_optimum_that_c8s_greedy_choice_misses(tmp_path):
    """T_A is the cheapest transformer that carries the load, so C8 takes it;
    its reactive loss puts the PCC outside the band, so C8 adds a bank. T_B
    costs a little more but draws less, and needs no bank at all. The
    brute force here, over every transformer count and bank count of the
    MILP's candidate set, finds T_B alone; so must the MILP."""
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib, req = joint_case(tmp_path)
    combos = []
    for tid, s, vk, capex in (TA, TB):
        for n in (1, 2, 3):
            for banks in (0, 1, 2, 3):
                cost = n * annual(capex, 40, 0.01) + banks * annual(CAP[3], 25, 0.02)
                combos.append((cost, tid, n, banks, s, vk))
    best = None
    for cost, tid, n, banks, s, vk in sorted(combos):
        if joint_feasible(n, s, vk, banks, req.q_limit_mvar):
            best = (cost, tid, n, banks)
            break
    assert best[1:] == ("T_B", 1, 0)
    out = select_assets_milp(spec, table, sel, lib, req, PROFILE)
    cmp_ = out["comparison"].set_index("need")
    # C8: the cheapest transformer, then a bank
    assert cmp_.at["transformer TR1", "c8_choice"] == "1 x T_A"
    assert cmp_.at["reactive", "c8_choice"] == "1 x CAP4"
    c8_cost = annual(TA[3], 40, 0.01) + annual(CAP[3], 25, 0.02)
    assert out["summary"]["c8_cost"] == pytest.approx(c8_cost)
    # the MILP: the joint optimum, the brute-force minimum
    assert cmp_.at["transformer TR1", "milp_choice"] == "1 x T_B"
    assert cmp_.at["reactive", "milp_choice"] == "none"
    assert out["summary"]["milp_cost"] == pytest.approx(best[0]) and best[0] < c8_cost
    assert out["fallback"] is None and out["summary"]["fallback"] is False
    inv = out["investment"].set_index("need")
    assert inv.at["transformer TR1", "library_id"] == "T_B" and inv.at["reactive", "status"] == "not_needed"
    assert out["cost"]["annualised_eur_per_a"].sum() == pytest.approx(best[0])
    comp_ = out["compliance"].set_index("check")
    assert set(comp_["status_with_measures"]) <= {"pass", "not_rated"}
    h = out["milp_history"]
    assert h.iloc[0]["note"].startswith("C8") and h.iloc[0]["cost"] == pytest.approx(c8_cost)
    assert bool(h["feasible"].iloc[-1]) and h["cost"].iloc[-1] == pytest.approx(best[0])
    # one discrete change (the transformer) and one switched step out: the
    # linear model predicted the AC result to within a scale unit
    assert h.iloc[1]["worst_lin_error"] < 1.0


# --------------------------------------------------------------------------
# the adaptive rate
# --------------------------------------------------------------------------

def test_the_back_off_and_trust_region_rules_are_the_documented_numbers():
    from gridspine.static.campus_milp import update_beta, update_delta
    # beta: x1.5 while violated, capped at 8; x0.7 with ample slack, floored at 0.25; else kept
    assert update_beta(1.0, True, False) == pytest.approx(1.5)
    assert update_beta(1.0, True, True) == pytest.approx(1.5)              # a violation wins
    assert update_beta(6.0, True, False) == pytest.approx(8.0)
    assert update_beta(1.0, False, True) == pytest.approx(0.7)
    assert update_beta(0.3, False, True) == pytest.approx(0.25)
    assert update_beta(1.3, False, False) == pytest.approx(1.3)
    # delta: rho > 0.75 doubles (capped) and accepts; rho < 0.25 halves and rejects; between keeps and accepts
    assert update_delta(0.76, 10.0, 100.0) == (pytest.approx(20.0), True)
    assert update_delta(0.75, 10.0, 100.0) == (pytest.approx(10.0), True)
    assert update_delta(0.9, 60.0, 100.0) == (pytest.approx(100.0), True)
    assert update_delta(0.25, 10.0, 100.0) == (pytest.approx(10.0), True)
    assert update_delta(0.24, 10.0, 100.0) == (pytest.approx(5.0), False)
    assert update_delta(-50.0, 10.0, 100.0) == (pytest.approx(5.0), False)


def transformer_case(tmp_path, load=38.0):
    """One transformer, the load at pf 0.85 and the battery idle but
    connected. C8 sizes from the as-described campus, with no reactive
    support from the battery: 63 MVA. A 50 MVA unit carries the hour with
    the margin only if the battery supplies about 15 Mvar locally (solved
    here): S = sqrt(P^2 + Q^2) falls ever more slowly as Q falls, so a
    linearisation at C8's point always asks for too little."""
    lib = library(tmp_path, transformers=[tr("T50", 50.0, 1.0e6), tr("T63", 63.0, 1.3e6)])
    spec = single_spec()
    spec["campus"]["units"]["DC_LOAD"]["pf"] = {"value": 0.85, "source": "assumed"}
    table, sel = hourly((2030, 1, {"DC_LOAD": -load, "BESS1": 0.0, "PV1": 0.0, "GEN1": 0.0}, {"PV1": 0, "GEN1": 0}))
    return spec, table, sel, lib


def t50_net(load, q_bess):
    from tests.gridspine.test_campus import direct_net
    net = direct_net()
    net.trafo.drop(net.trafo.index, inplace=True)
    pp.create_transformer_from_parameters(net, 0, 1, sn_mva=50.0, vn_hv_kv=110.0, vn_lv_kv=20.0, vkr_percent=0.4,
                                          vk_percent=12.0, pfe_kw=25.0, i0_percent=0.05)
    net.line.at[0, "max_i_ka"] = 3.0
    net.load.at[0, "p_mw"], net.load.at[0, "q_mvar"] = load, load * math.tan(math.acos(0.85))
    net.sgen["in_service"] = False
    net.sgen.loc[net.sgen["name"] == "BESS1", ["in_service", "p_mw", "q_mvar"]] = (True, 0.0, q_bess)
    pp.runpp(net)
    return net


def s_t50(net):
    return math.hypot(net.res_trafo.at[0, "p_hv_mw"], net.res_trafo.at[0, "q_hv_mvar"])


def test_adaptation_reaches_the_joint_optimum_in_fewer_iterations_than_a_fixed_rate(tmp_path):
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib = transformer_case(tmp_path)
    # the oracle: T50 fails the margin without the battery, passes with 15 Mvar
    assert s_t50(t50_net(38.0, 0.0)) * 1.2 > 50.0 > s_t50(t50_net(38.0, 15.0)) * 1.2
    c8 = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    assert c8["investment"].set_index("need").at["transformer TR1", "library_id"] == "T63"
    fast = select_assets_milp(spec, table, sel, lib, WIDE, PROFILE, c8=c8)
    slow = select_assets_milp(spec, table, sel, lib, WIDE, PROFILE, adaptive=False, c8=c8)
    t50 = 1.0e6 * (crf(RATE, 40) + 0.01)
    for out in (fast, slow):
        assert out["fallback"] is None
        assert out["summary"]["milp_cost"] == pytest.approx(t50)
        assert out["investment"].set_index("need").at["transformer TR1", "library_id"] == "T50"
    hf, hs = fast["milp_history"], slow["milp_history"]
    n_fast, n_slow = fast["summary"]["iterations"], slow["summary"]["iterations"]
    assert (n_fast, n_slow) == (5, 9)                              # reported; measured, not tuned
    assert n_fast < n_slow
    # adaptation: beta grows on the transformer's margin, violated in AC ...
    first = hf.iloc[1]
    assert not first["feasible"] and not first["accepted"] and first["rho"] < 0.25
    assert first["beta_max"] == pytest.approx(1.5) and "ts+ PCC/MV1 intact 2030/1" in first["beta_up"]
    assert hf.iloc[2]["beta_max"] == pytest.approx(2.25)
    # ... and fixed: beta stays at 1 and delta where it started
    assert set(hs["beta_max"]) == {1.0} and hs["delta"].nunique() == 1 and (hs["beta_up"] == "").all()


def test_a_rejected_step_halves_delta_and_keeps_the_previous_point(tmp_path):
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib = transformer_case(tmp_path)
    h = select_assets_milp(spec, table, sel, lib, WIDE, PROFILE)["milp_history"]
    rejected = h[(h["iteration"] > 0) & (h["rho"] < 0.25) & ~h["accepted"]]
    assert len(rejected) >= 2
    for i in rejected.index:
        assert h.at[i + 1, "delta"] == pytest.approx(h.at[i, "delta"] / 2)
        assert h.at[i, "point_cost"] == pytest.approx(h.at[i - 1, "point_cost"])      # the previous point kept
        assert h.at[i, "cost"] != pytest.approx(h.at[i, "point_cost"])               # not the trial's
    good = h[(h["iteration"] > 0) & (h["rho"] > 0.75) & h["accepted"]]
    assert len(good)
    for i in good.index:
        assert h.at[i + 1, "delta"] == pytest.approx(min(2 * h.at[i, "delta"], 4 * 22.0))
        assert h.at[i, "point_cost"] == pytest.approx(h.at[i, "cost"])


def test_the_returned_compliance_is_an_ac_re_solve_not_the_linear_prediction(tmp_path):
    """The winning point was predicted with a linearisation error (history),
    yet every value reported is the AC one: solved here, in pandapower,
    with the returned battery dispatch on the returned transformer."""
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib = transformer_case(tmp_path)
    out = select_assets_milp(spec, table, sel, lib, WIDE, PROFILE)
    h = out["milp_history"]
    win = h[h["feasible"] & (h["cost"] == out["summary"]["milp_cost"])].iloc[0]
    assert win["worst_lin_error"] > 0.1                             # the linear model was off there
    d = out["dispatch"]
    q_bess = float(d.loc[d["element"] == "inverters", "q_mvar"].iloc[0])
    assert 10.0 < q_bess <= 22.0
    net = t50_net(38.0, q_bess)
    comp_ = out["compliance"].set_index("check")
    assert comp_.at["transformer_loading", "value_with_measures"] == pytest.approx(
        float(net.res_trafo.at[0, "loading_percent"]), abs=1e-6)
    assert comp_.at["campus_voltage", "value_with_measures"] == pytest.approx(
        float(net.res_bus["vm_pu"].iloc[1:].min()), abs=1e-6)
    assert out["pcc"].set_index("case").at["intact", "q_mvar"] == pytest.approx(
        float(net.res_ext_grid["q_mvar"].sum()), abs=1e-6)
    assert s_t50(net) * 1.2 <= 50.0                                 # the sizing rule holds in AC
    assert set(comp_["status_with_measures"]) <= {"pass", "not_rated"}


# --------------------------------------------------------------------------
# the fallback, switchgear per bay and the PCC's switchgear scope
# --------------------------------------------------------------------------

def test_when_the_milp_cannot_beat_c8_c8s_choice_is_returned_and_flagged(tmp_path):
    """Without T_B, the bank is the only way into the band (T_A alone draws
    too much, solved in the joint case), so C8's choice is already the
    cheapest feasible one."""
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib, req = joint_case(tmp_path)
    lib["transformers"] = [e for e in lib["transformers"] if e["id"] == "T_A"]
    c8 = select_assets(spec, table, sel, lib, req, PROFILE)
    out = select_assets_milp(spec, table, sel, lib, req, PROFILE, c8=c8)
    assert out["fallback"].startswith("no AC-feasible point is cheaper than C8's")
    assert out["summary"]["fallback"] is True
    pd_testing = pytest.importorskip("pandas").testing
    pd_testing.assert_frame_equal(out["investment"], c8["investment"])
    pd_testing.assert_frame_equal(out["compliance"], c8["compliance"])
    assert out["spec"] == c8["spec"]
    cmp_ = out["comparison"]
    assert list(cmp_["c8_choice"]) == list(cmp_["milp_choice"]) == ["1 x T_A", "1 x CAP4"]
    assert "state" not in out


def sg(i, kv, ik, capex):
    t = lambda v: {"value": v, "source": "assumed"}
    return {"id": i, "vn_kv": t(kv), "ik_rated_ka": t(ik), "ip_rated_ka": t(2.5 * ik), "capex_eur": t(capex),
            "opex_frac": t(0.01), "lifetime_a": t(40)}


@pytest.mark.parametrize("pcc_switchgear", [True, False])
def test_switchgear_is_costed_per_bay_of_the_joint_choice_and_the_pcc_is_a_study_setting(tmp_path, pcc_switchgear):
    """The joint case with switchgear. MV1's bays follow the choice: T_A +
    a bank is the transformer, the cable, the battery, the PV and the bank
    (5); T_B alone is 4. MV2 has the cable, the load and the genset (3)
    either way. The PCC has the transformer and the grid (2), and no
    switchgear at all when the grid operator owns it."""
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib, req = joint_case(tmp_path)
    lib["switchgear"] = [sg("SG20", 20.0, 31.5, 20e3), sg("SG110", 110.0, 31.5, 150e3)]
    out = select_assets_milp(spec, table, sel, lib, req, PROFILE, pcc_switchgear=pcc_switchgear)
    a20, a110 = annual(20e3, 40, 0.01), annual(150e3, 40, 0.01)
    pcc = 2 * a110 if pcc_switchgear else 0.0
    c8_cost = annual(TA[3], 40, 0.01) + annual(CAP[3], 25, 0.02) + (5 + 3) * a20 + pcc
    assert out["summary"]["c8_cost"] == pytest.approx(c8_cost)
    assert out["summary"]["milp_cost"] == pytest.approx(annual(TB[3], 40, 0.01) + (4 + 3) * a20 + pcc)
    inv = out["investment"].set_index("need")
    assert (inv.at["switchgear MV1", "units"], inv.at["switchgear MV2", "units"]) == (4, 3)
    assert ("switchgear PCC" in inv.index) is pcc_switchgear
    assert ("switchgear PCC" in set(out["comparison"]["need"])) is pcc_switchgear
    assert out["scope"] == {"pcc_switchgear": "campus" if pcc_switchgear else "grid_operator"}


def test_bays_a_choice_adds_are_priced_in_the_milp(tmp_path):
    """Every inverter off and 50 MW at pf 0.85 behind a 100 MVA, 18 % unit: MV2
    sits below 0.90 pu, and about 6 Mvar at MV1 lifts it (solved here). C8
    escalates to one small bank, which its dispatch (for the PCC band, wide
    here) leaves off, so C8 is unresolved. The MILP may take two small
    banks (cheaper as banks) or one big one (cheaper with MV1's dear
    switchgear, one bay fewer)."""
    from gridspine.static.campus_milp import select_assets_milp

    def v_min(q):
        net = t50_net(50.0, 0.0)
        net.trafo.at[0, "sn_mva"], net.trafo.at[0, "vk_percent"] = 100.0, 18.0
        net.sgen["in_service"] = False
        pp.create_shunt(net, 1, q_mvar=-q, p_mw=0.0)
        pp.runpp(net)
        return float(net.res_bus["vm_pu"].min())

    assert v_min(0.0) < v_min(4.0) < 0.9 < v_min(8.0)                         # the oracle
    lib = library(tmp_path, transformers=[tr("T100", 100.0, 1.5e6, vk=18.0)],
                  capacitor_banks=[comp("capacitor_banks", "CAP_S", 4.0, 70e3), comp("capacitor_banks", "CAP_B", 8.0, 160e3)],
                  switchgear=[sg("SG20", 20.0, 31.5, 200e3)])
    spec = single_spec()
    spec["campus"]["units"]["DC_LOAD"]["pf"] = {"value": 0.85, "source": "assumed"}
    table, sel = dark_hours((2030, 50.0))
    c8 = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    assert c8["investment"].set_index("need").at["reactive", "library_id"] == "CAP_S" and c8["unresolved"]
    out = select_assets_milp(spec, table, sel, lib, WIDE, PROFILE, c8=c8)
    bank = lambda capex: capex * (crf(RATE, 25) + 0.02)
    bay = 200e3 * (crf(RATE, 40) + 0.01)
    assert 2 * bank(70e3) < bank(160e3) < 2 * bank(70e3) + bay
    cmp_ = out["comparison"].set_index("need")
    assert cmp_.at["reactive", "milp_choice"] == "1 x CAP_B"
    assert out["investment"].set_index("need").at["switchgear MV1", "units"] == 5    # T100, cable, battery, PV, bank
    want = 1.5e6 * (crf(RATE, 40) + 0.01) + bank(160e3) + (5 + 3) * bay
    assert out["summary"]["milp_cost"] == pytest.approx(want)
    assert set(out["compliance"]["status_with_measures"]) <= {"pass", "not_rated"}
    # priced right, the MILP never proposes the two small banks: no rejected
    # step, and the loop converges instead of shrinking Delta to its floor
    h = out["milp_history"]
    assert out["summary"]["stop"] == "converged" and not h["choice"].str.contains("2 x CAP_S").any()


def test_a_rated_pcc_left_to_the_grid_operator_constrains_nothing(tmp_path):
    """The PCC rated far below its fault level (Ik'' about 15.7 kA against
    5 kA): with the switchgear the grid operator's, C8 ignores it and so
    does the MILP, which still finds the joint optimum."""
    from gridspine.static.campus_milp import select_assets_milp
    spec, table, sel, lib, req = joint_case(tmp_path)
    spec["campus"]["pcc"]["ik_rated_ka"] = {"value": 5.0, "source": "datasheet"}
    out = select_assets_milp(spec, table, sel, lib, req, PROFILE, pcc_switchgear=False)
    assert out["fallback"] is None
    h = out["milp_history"]
    assert (h["slack"] == 0).all() and (h.loc[h["feasible"], "worst_violation"] == 0).all()
    assert out["comparison"].set_index("need").at["transformer TR1", "milp_choice"] == "1 x T_B"
    assert out["scope"] == {"pcc_switchgear": "grid_operator"}


def test_the_battery_is_held_to_the_polygon_not_the_circle(tmp_path):
    """The battery discharges at P = S cos^2(pi/8), midway between two
    vertices, where the octagon allows 7.8 Mvar and the circle 11.5. The
    grid holds the PCC at 1.05 pu, so the voltages hold. T_R is rated so
    that it carries the hour with the margin only with Q beyond the
    octagon, and passes every check with the circle's Q (solved here). The
    MILP must not buy it."""
    from gridspine.static.campus_milp import q_range, select_assets_milp
    p_bess = 22.0 * math.cos(math.pi / 8) ** 2
    q_poly, q_circle = q_range(p_bess, 22.0)[1], math.sqrt(22.0 ** 2 - p_bess ** 2)
    assert q_circle - q_poly > 3.0

    def at(q, rating):
        net = t50_net(55.0, q)
        net.trafo.at[0, "sn_mva"], net.ext_grid.at[0, "vm_pu"] = rating, 1.05
        net.sgen.loc[net.sgen["name"] == "BESS1", "p_mw"] = p_bess
        pp.runpp(net)
        return net

    rating = round(1.2 * (s_t50(at(q_poly, 60.0)) + s_t50(at(q_circle, 60.0))) / 2, 2)
    inside, outside = at(q_poly, rating), at(q_circle, rating)
    assert s_t50(inside) * 1.2 > rating > s_t50(outside) * 1.2                     # the oracle
    assert outside.res_bus["vm_pu"].between(0.9, 1.1).all() and outside.res_trafo.at[0, "loading_percent"] < 100
    spec, table, sel, lib = transformer_case(tmp_path, load=55.0)
    table.loc[table["unit_id"] == "BESS1", "p_mw"] = p_bess
    lib["transformers"] = [tr("T_R", rating, 1.1e6), tr("T80", 80.0, 1.5e6)]
    spec["campus"]["pcc"]["vm_pu"] = {"value": 1.05, "source": "assumed"}
    c8 = select_assets(spec, table, sel, lib, WIDE, PROFILE)
    assert c8["investment"].set_index("need").at["transformer TR1", "library_id"] == "T80"
    out = select_assets_milp(spec, table, sel, lib, WIDE, PROFILE, c8=c8)
    assert out["investment"].set_index("need").at["transformer TR1", "library_id"] == "T80"
    h = out["milp_history"]
    assert not h.loc[h["choice"].str.contains("T_R"), "feasible"].any()
    q = out["dispatch"].loc[out["dispatch"]["element"] == "inverters", "q_mvar"].abs()
    assert (q <= q_poly + 1e-6).all()
