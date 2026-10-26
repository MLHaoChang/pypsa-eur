"""P–V and Q–V voltage-stability margins at the PoC (screening).

The two-bus grid is the one with a closed form: a stiff source E behind a pure
reactance X feeding a load at power-factor angle phi (lagging). Its P–V nose is

    P_max = E^2 cos(phi) / (2 X (1 + sin(phi)))

and its Q–V curve, with a fictitious PV source at the load bus holding V, is

    Q_src(V) = Q_load - (V sqrt(E^2 - (P X / V)^2) - V^2) / X

(E, V in kV line-to-line, X in ohm, P and Q in MW and Mvar). The tests hold the
module to both within 1 %. The meshed grid checks which N-1 cases get studied.
"""
import copy
import math

import numpy as np
import pandapower as pp
import pytest
from scipy.optimize import brentq

from gridspine.schema.contracts import ContractError
from gridspine.static import voltage_stability as vs
from gridspine.static.contingency_set import branch_contingencies
from gridspine.static.voltage_stability import (
    SCREENING_BASIS,
    VoltageStabilitySettings,
    voltage_stability,
)

E_KV = 110.0
X_OHM = 10.0
PF = 0.98
PLANNED_MW = 200.0


def _line(net, a, b, x_ohm, name):
    return pp.create_line_from_parameters(
        net, a, b, length_km=1.0, r_ohm_per_km=0.0, x_ohm_per_km=x_ohm,
        c_nf_per_km=0.0, max_i_ka=10.0, name=name,
    )


def two_bus(x_ohm=X_OHM):
    net = pp.create_empty_network(f_hz=60.0)
    src = pp.create_bus(net, vn_kv=E_KV, name="SRC")
    poc = pp.create_bus(net, vn_kv=E_KV, name="POC")
    pp.create_ext_grid(net, src, vm_pu=1.0, name="GRID")
    _line(net, src, poc, x_ohm, "L1")
    return net


def meshed():
    """SRC feeds POC directly (strongest path) and through A; POC feeds a radial D."""
    net = pp.create_empty_network(f_hz=60.0)
    src = pp.create_bus(net, vn_kv=E_KV, name="SRC")
    a = pp.create_bus(net, vn_kv=E_KV, name="A")
    poc = pp.create_bus(net, vn_kv=E_KV, name="POC")
    d = pp.create_bus(net, vn_kv=E_KV, name="D")
    pp.create_ext_grid(net, src, vm_pu=1.0, name="GRID")
    _line(net, src, poc, 5.0, "SRC_POC")
    _line(net, src, a, 5.0, "SRC_A")
    _line(net, a, poc, 10.0, "A_POC")
    _line(net, poc, d, 2.0, "POC_D")
    pp.create_load(net, d, p_mw=10.0, q_mvar=2.0, name="LD_D")
    return net


def analytic_nose(e_kv=E_KV, x_ohm=X_OHM, pf=PF):
    phi = math.acos(pf)
    return e_kv**2 * math.cos(phi) / (2 * x_ohm * (1 + math.sin(phi)))


def q_src(v_kv, p_mw, q_mw, e_kv=E_KV, x_ohm=X_OHM):
    return q_mw - (v_kv * math.sqrt(e_kv**2 - (p_mw * x_ohm / v_kv) ** 2) - v_kv**2) / x_ohm


def _intact(rows):
    return rows[rows["case"] == "intact"].iloc[0]


# ---------------------------------------------------------------------------
# P–V
# ---------------------------------------------------------------------------

def test_two_bus_nose_matches_analytic_within_one_percent():
    rows, _ = voltage_stability(two_bus(), "POC", PLANNED_MW, None,
                                VoltageStabilitySettings(pf=PF))
    r = _intact(rows)
    p_max = analytic_nose()
    assert r["nose_mw"] == pytest.approx(p_max, rel=0.01)
    assert r["margin_mw"] == pytest.approx(r["nose_mw"] - PLANNED_MW)
    assert r["margin_pct"] == pytest.approx(100.0 * r["margin_mw"] / PLANNED_MW)
    assert r["verdict"] == "solved"
    assert not r["nose_is_lower_bound"] and not r["nose_below_planned"]
    # The nose voltage of this grid, V = E / sqrt(2 (1 + sin phi)): the upper
    # branch, never a low-voltage solution.
    v_nose = 1.0 / math.sqrt(2 * (1 + math.sin(math.acos(PF))))
    assert r["v_nose_pu"] == pytest.approx(v_nose, rel=0.01)


def test_nose_does_not_depend_on_the_step_size():
    noses = [
        _intact(voltage_stability(two_bus(), "POC", PLANNED_MW, None,
                                  VoltageStabilitySettings(pf=PF, step_pct=step))[0])["nose_mw"]
        for step in (10.0, 37.0)
    ]
    tol = VoltageStabilitySettings().tol_mw
    assert abs(noses[0] - noses[1]) <= 2 * tol


def test_a_solver_failure_is_retried_from_a_flat_start_and_recorded(monkeypatch):
    """A point that fails only because of the solver is not the nose: the flat
    start rescues it and the row says so."""
    real = pp.runpp
    p_max = analytic_nose()

    def flaky(net, **kw):
        probe = net.load.loc[net.load["name"] == vs.PROBE_NAME, "p_mw"]
        if kw.get("init") != "flat" and len(probe) and 0.5 * p_max < float(probe.iloc[0]) < 0.7 * p_max:
            raise pp.LoadflowNotConverged("solver trouble, not collapse")
        return real(net, **kw)

    monkeypatch.setattr(vs.pp, "runpp", flaky)
    rows, _ = voltage_stability(two_bus(), "POC", PLANNED_MW, None, VoltageStabilitySettings(pf=PF))
    r = _intact(rows)
    assert r["nose_mw"] == pytest.approx(p_max, rel=0.01)
    assert r["flat_start_retries"] >= 1
    assert r["flat_start_rescues"] >= 1
    v_nose = 1.0 / math.sqrt(2 * (1 + math.sin(math.acos(PF))))
    assert r["v_nose_pu"] == pytest.approx(v_nose, rel=0.01)    # rescued on the upper branch


def test_nose_past_the_cap_is_flagged_not_invented():
    rows, _ = voltage_stability(two_bus(), "POC", PLANNED_MW, None,
                                VoltageStabilitySettings(pf=PF, cap_mw=300.0))
    r = _intact(rows)
    assert r["nose_is_lower_bound"]
    assert r["nose_mw"] == pytest.approx(300.0)
    assert "lower bound" in r["note"]


def test_planned_load_past_the_nose_gives_a_negative_margin():
    planned = 1.2 * analytic_nose()
    rows, _ = voltage_stability(two_bus(), "POC", planned, None, VoltageStabilitySettings(pf=PF))
    r = _intact(rows)
    assert r["nose_mw"] == pytest.approx(analytic_nose(), rel=0.01)
    assert r["margin_mw"] < 0 and r["margin_pct"] < 0
    assert r["nose_below_planned"] and r["verdict"] == "solved"
    # no operating point to sweep Q–V around
    assert math.isnan(r["q_margin_mvar"]) and math.isnan(r["dqdv_mvar_per_pu"])


# ---------------------------------------------------------------------------
# Q–V
# ---------------------------------------------------------------------------

def test_two_bus_qv_margin_and_slope_match_analytic():
    rows, _ = voltage_stability(two_bus(), "POC", PLANNED_MW, None, VoltageStabilitySettings(pf=PF))
    r = _intact(rows)
    q_load = PLANNED_MW * math.tan(math.acos(PF))
    v_floor = PLANNED_MW * X_OHM / E_KV + 1e-6          # below it sin(delta) > 1: no solution
    grid = np.linspace(v_floor, E_KV, 20001)
    q = np.array([q_src(v, PLANNED_MW, q_load) for v in grid])
    assert r["q_margin_mvar"] == pytest.approx(-q.min(), rel=0.01)
    assert r["v_at_qmin_pu"] == pytest.approx(grid[q.argmin()] / E_KV, abs=0.02)
    assert not r["qmin_at_sweep_edge"]

    v_op = brentq(lambda v: q_src(v, PLANNED_MW, q_load), grid[q.argmin()], E_KV)
    assert r["v_poc_pu"] == pytest.approx(v_op / E_KV, abs=1e-4)
    h = 1e-3
    slope = (q_src(v_op + h, PLANNED_MW, q_load) - q_src(v_op - h, PLANNED_MW, q_load)) / (2 * h) * E_KV
    assert r["dqdv_mvar_per_pu"] == pytest.approx(slope, rel=0.01)
    assert r["dqdv_mvar_per_pu"] > 0          # the operating point is on the stable side


# ---------------------------------------------------------------------------
# Cases, labels, contract
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def meshed_run():
    """One default-settings run on the meshed grid, with copies of its inputs."""
    net = meshed()
    cset = branch_contingencies(net)
    before = (copy.deepcopy(net), cset.copy(deep=True))
    rows, summary = voltage_stability(net, "POC", 50.0, cset)
    return net, cset, before, rows, summary


def test_every_row_is_labelled_screening(meshed_run):
    rows = meshed_run[3]
    assert SCREENING_BASIS == "screening; binding answer is the RMS study"
    assert (rows["basis"] == SCREENING_BASIS).all()


def test_worst_n1_is_the_lowest_poc_voltage_and_the_cap_holds():
    net = meshed()
    cset = branch_contingencies(net)
    rows, summary = voltage_stability(net, "POC", 50.0, cset, VoltageStabilitySettings(pf=PF, max_cases=1))
    # POC-D islands D: not ranked, but always listed, and outside the cap
    assert list(rows["case"]) == ["intact", "POC-D-1", "SRC-POC-1"]
    assert summary["n1_ranked"] == 3
    assert summary["n1_not_solved"] == ["POC-D-1"]
    assert summary["n1_studied"] == 1
    isl = rows.iloc[1]
    assert isl["verdict"] == "not_solved" and isl["note"] == "islanded"
    assert isl[["nose_mw", "margin_mw", "margin_pct", "q_margin_mvar"]].isna().all()
    n1 = rows.iloc[2]
    # losing the strongest path lowers the nose
    assert n1["nose_mw"] < _intact(rows)["nose_mw"]
    assert n1["contingency_id"] == "SRC-POC-1"


def test_default_cap_is_five_and_ranking_order_is_by_poc_voltage(meshed_run):
    rows, summary = meshed_run[3], meshed_run[4]
    assert VoltageStabilitySettings().max_cases == 5
    assert summary["n1_studied"] == 3
    solved = rows[rows["verdict"] == "solved"]
    assert len(solved) == 4 and list(rows["case"])[:2] == ["intact", "POC-D-1"]
    # ties (two outages that leave the same path) may differ by solver noise
    assert (np.diff(solved["v_poc_pu"].iloc[1:].to_numpy()) >= -1e-9).all()
    # every solved case reports the nose, both margins and the Q–V margin
    cols = ["nose_mw", "margin_mw", "margin_pct", "q_margin_mvar", "dqdv_mvar_per_pu"]
    assert solved[cols].notna().all().all()


def test_caller_net_and_contingency_set_are_not_mutated(meshed_run):
    net, cset, (before_net, before_cset), _rows, _summary = meshed_run
    assert len(net.load) == len(before_net.load) and len(net.gen) == len(before_net.gen)
    assert net.line["in_service"].equals(before_net.line["in_service"])
    assert net.load["p_mw"].equals(before_net.load["p_mw"])
    assert cset.equals(before_cset)


@pytest.mark.parametrize("kw", [
    {"planned_mw": 0.0},
    {"planned_mw": float("nan")},
    {"planned_mw": float("inf")},
    {"planned_mw": True},
    {"poc_bus": "NOWHERE"},
])
def test_bad_inputs_refuse(kw):
    args = {"net": two_bus(), "poc_bus": "POC", "planned_mw": PLANNED_MW, "contingencies": None}
    args.update(kw)
    with pytest.raises(ContractError):
        voltage_stability(**args)


@pytest.mark.parametrize("kw", [
    {"max_cases": -1}, {"max_cases": True}, {"max_cases": 2.0}, {"step_pct": 0.0},
    {"step_pct": float("inf")}, {"cap_mw": 0.0}, {"cap_mw": float("nan")}, {"pf": 0.0}, {"tol_mw": 0.0}, {"pf": 1.2},
    {"qv_v_min_pu": 1.2, "qv_v_max_pu": 0.8}, {"qv_step_pu": 0.0},
])
def test_bad_settings_refuse(kw):
    with pytest.raises(ContractError):
        VoltageStabilitySettings(**kw)


def test_a_set_that_is_not_n1_refuses():
    net = meshed()
    cset = branch_contingencies(net)
    cset.loc[cset.index[0], "order"] = 2
    with pytest.raises(ContractError):
        voltage_stability(net, "POC", 50.0, cset)


# ---------------------------------------------------------------------------
# Severe cases, Q limits
# ---------------------------------------------------------------------------

def test_intact_case_that_does_not_converge_is_not_solved():
    net = two_bus()
    pp.create_load(net, 1, p_mw=1.5 * analytic_nose(), q_mvar=0.0, name="EXISTING")
    rows, _ = voltage_stability(net, "POC", PLANNED_MW, None, VoltageStabilitySettings(pf=PF))
    r = _intact(rows)
    assert r["verdict"] == "not_solved"
    assert r[["nose_mw", "margin_mw", "margin_pct", "q_margin_mvar"]].isna().all()


def test_n1_collapse_at_the_planned_load_is_listed_not_dropped():
    """Without SRC-POC the PoC hangs on X = 15 ohm, nose ~330 MW: a 400 MW
    planned load collapses that case. It is listed even with max_cases = 0."""
    net = meshed()
    rows, summary = voltage_stability(net, "POC", 400.0, branch_contingencies(net),
                                      VoltageStabilitySettings(pf=PF, max_cases=0))
    assert list(rows["case"]) == ["intact", "POC-D-1", "SRC-POC-1"]
    notes = dict(zip(rows["case"], rows["note"]))
    assert notes["SRC-POC-1"] == "collapses at or below the planned load"
    assert notes["POC-D-1"] == "islanded"
    assert (rows["verdict"].iloc[1:] == "not_solved").all()
    assert summary["n1_studied"] == 0 and summary["n1_ranked"] == 2


def _chain(q_limit_mvar):
    """SRC -- M -- POC with a P = 0 voltage-holding generator at M."""
    net = pp.create_empty_network(f_hz=60.0)
    src = pp.create_bus(net, vn_kv=E_KV, name="SRC")
    m = pp.create_bus(net, vn_kv=E_KV, name="M")
    poc = pp.create_bus(net, vn_kv=E_KV, name="POC")
    pp.create_ext_grid(net, src, vm_pu=1.0, name="GRID")
    _line(net, src, m, X_OHM, "L1")
    _line(net, m, poc, X_OHM, "L2")
    pp.create_gen(net, m, p_mw=0.0, vm_pu=1.0, min_q_mvar=-q_limit_mvar, max_q_mvar=q_limit_mvar, name="SVC")
    return net


def test_generator_q_limits_are_enforced():
    st = VoltageStabilitySettings(pf=PF)
    limited = _intact(voltage_stability(_chain(20.0), "POC", 100.0, None, st)[0])
    unlimited = _intact(voltage_stability(_chain(1e9), "POC", 100.0, None, st)[0])
    # unlimited, M holds 1 pu and the PoC sees the two-bus nose over one line
    assert unlimited["nose_mw"] == pytest.approx(analytic_nose(), rel=0.01)
    assert limited["nose_mw"] < 0.9 * unlimited["nose_mw"]
