# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-06 LC-D07: an independently solved static AC power flow, and P=60/Q=80 -> 100 MVA.

A meshed 3-bus 110 kV network is solved through the ordinary chat tools
(`run_simulation`, then `run_ac_pf_stage`). The same network is solved again
here, independently of PyPSA's Newton-Raphson: the nodal admittance matrix is
built by hand from the line ohms and siemens, and `scipy.optimize.root` solves
the power-balance equations. Voltages, both terminals of every line, KCL at the
100 MVA load bus (60 MW + j80 Mvar), losses, the slack's output and the
load-flow summary are checked against that solution.

The summary's apparent-power arithmetic is pinned exactly on the hand-set
LC-T07 weighted fixture: P=60, Q=80 at both terminals is 100.00 MVA, 100 % of a
100 MVA rating, one weighted hour at the limit.

Tolerances. The app's PF runs with `ac_pf_x_tol = 1e-10` (the NR stops when the
largest bus mismatch is below 1e-10 on a 1 MVA base). The independent solve is
driven to a residual below 1e-9 MW/Mvar, which the test asserts. Measured at
2026-10-28, the two agree to 2e-16 p.u. on voltages and 3e-13 MW/Mvar on flows.
Voltages are compared at 1e-9 p.u. and flows at 1e-6 MW/Mvar: far above either
solver's error, and far below any modelling difference (one per cent on one
resistance moves AB's loss by about 9 kW, and an NR stopped at x_tol 1e-3
already fails both). The summary rounds to 2 decimals, so its values are
compared at 0.005 + 1e-6.
"""
import math
import time

import numpy as np
import pandas as pd
import pypsa
import pytest
from scipy.optimize import root

from services import chat_service, chat_tools
from services import loadflow_summary as lf
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_loadflow_summary import SNAPSHOTS as LT07_SNAPSHOTS, _by_name, solved  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

V_NOM = 110.0                       # kV, every bus
S_BASE = 1.0                        # MVA, the per-unit base PyPSA's PF uses
SLACK = 'A'
LINES = {                           # name: (bus0, bus1, r ohm, x ohm, b siemens)
    'AB': ('A', 'B', 2.0, 10.0, 0.0),
    'BC': ('B', 'C', 3.0, 12.0, 0.0),
    'AC': ('A', 'C', 4.0, 16.0, 1e-4),
}
LOADS = {                           # bus: (p MW per snapshot, q Mvar per snapshot)
    'B': ([40.0, 20.0], [10.0, 5.0]),
    'C': ([60.0, 30.0], [80.0, 40.0]),
}
BUSES = ['A', 'B', 'C']
X_TOL = 1e-10
V_TOL, FLOW_TOL = 1e-9, 1e-6
SUMMARY_TOL = 0.005 + FLOW_TOL


def network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=2, freq='h'))
    for bus in BUSES:
        n.add('Bus', bus, v_nom=V_NOM)
    n.add('Carrier', 'grid')
    n.add('Generator', 'Grid', bus=SLACK, carrier='grid', p_nom=500.0, marginal_cost=10.0, control='Slack')
    for name, (b0, b1, r, x, b) in LINES.items():
        n.add('Line', name, bus0=b0, bus1=b1, r=r, x=x, b=b, s_nom=200.0)
    for bus, (p, q) in LOADS.items():
        n.add('Load', f'L{bus}', bus=bus, p_set=p, q_set=q)
    return n


# ── the independent solve ────────────────────────────────────────────────

def _admittances():
    z_base = V_NOM ** 2 / S_BASE
    out = {}
    for name, (b0, b1, r, x, b) in LINES.items():
        out[name] = (BUSES.index(b0), BUSES.index(b1), 1.0 / complex(r / z_base, x / z_base), 1j * b * z_base)
    return out


def independent_pf(t: int) -> dict:
    """Bus voltages and line terminal flows (MW, Mvar) for snapshot ``t``."""
    branches = _admittances()
    Y = np.zeros((3, 3), dtype=complex)
    for i, j, y, ysh in branches.values():
        Y[i, i] += y + ysh / 2
        Y[j, j] += y + ysh / 2
        Y[i, j] -= y
        Y[j, i] -= y
    s_spec = np.array([0j] + [-complex(LOADS[bus][0][t], LOADS[bus][1][t]) / S_BASE for bus in BUSES[1:]])

    def voltages(z):
        th_b, th_c, v_b, v_c = z
        return np.array([1.0 + 0j, v_b * np.exp(1j * th_b), v_c * np.exp(1j * th_c)])

    def mismatch(z):
        V = voltages(z)
        d = (V * np.conj(Y @ V) - s_spec)[1:]
        return np.concatenate([d.real, d.imag])

    # hybr may stop with "xtol too small" once it is at machine precision, so
    # the residual, not its success flag, is what certifies the solution.
    sol = root(mismatch, [0.0, 0.0, 1.0, 1.0], method='hybr', tol=1e-14)
    assert np.max(np.abs(mismatch(sol.x))) * S_BASE < 1e-9, sol.message
    V = voltages(sol.x)
    flows = {}
    for name, (i, j, y, ysh) in branches.items():
        s0 = V[i] * np.conj((V[i] - V[j]) * y + V[i] * ysh / 2) * S_BASE
        s1 = V[j] * np.conj((V[j] - V[i]) * y + V[j] * ysh / 2) * S_BASE
        flows[name] = (s0, s1)
    return {'v_mag': dict(zip(BUSES, np.abs(V))), 'flows': flows}


# ── the app's solve ──────────────────────────────────────────────────────

def _frame(payload) -> pd.DataFrame:
    return pd.DataFrame(payload['data'], index=payload['index'], columns=payload['columns'])


def _run_ac_pf(session) -> dict:
    frames = list(chat_service._dispatch_real_tool_call(
        session, {'id': f'lc06-d07-{time.monotonic_ns()}', 'name': 'run_ac_pf_stage', 'input': {}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = chat_tools.get_simulation_status()
        if not state.get('running') and str(state.get('condition', '')).startswith('ac_pf'):
            return state
        time.sleep(0.2)
    raise AssertionError(f'AC PF did not finish: {chat_tools.get_simulation_status()}')


@pytest.fixture(scope='module')
def oracle():
    return [independent_pf(t) for t in range(2)]


@pytest.fixture
def pf(seam):
    solve = seam(network(), ac_pf_x_tol=X_TOL)
    lopf = solve()
    assert lopf['status'] == 'completed' and lopf['condition'] == 'optimal'
    state = _run_ac_pf(chat_tools._CHAT_SESSION.get())
    assert state['status'] == 'completed' and state['condition'] == 'ac_pf_ok', state
    status = chat_tools.get_results('ac_pf_status', source='ac_pf')
    assert status['converged_count'] == status['total_snapshots'] == 2
    assert status['slack_bus_used'] == SLACK
    return {'p0': _frame(chat_tools.get_results('lines', source='ac_pf')),
            'q0': _frame(chat_tools.get_results('line_reactive', source='ac_pf')),
            'v_mag': _frame(chat_tools.get_results('voltages', source='ac_pf')),
            'slack': _frame(chat_tools.get_results('generators', source='ac_pf')),
            'losses': chat_tools.get_results('losses', source='ac_pf'),
            'summary': chat_tools.get_loadflow_summary(source='ac_pf', limit=25)}


def test_the_independent_solve_closes_kcl_and_meets_the_100_mva_load(oracle):
    """The oracle's own check: at the load bus C the lines deliver exactly the
    load, 60 MW + j80 Mvar = 100 MVA (snapshot 0) and 30 + j40 = 50 (snapshot 1)."""
    for t, sol in enumerate(oracle):
        into_c = -(sol['flows']['BC'][1] + sol['flows']['AC'][1])
        assert (into_c.real, into_c.imag) == pytest.approx((LOADS['C'][0][t], LOADS['C'][1][t]), abs=FLOW_TOL)
    assert abs(-(oracle[0]['flows']['BC'][1] + oracle[0]['flows']['AC'][1])) == pytest.approx(100.0, abs=FLOW_TOL)


def test_bus_voltages_equal_the_independent_solve(pf, oracle):
    for t, sol in enumerate(oracle):
        for bus in BUSES:
            assert pf['v_mag'].iloc[t][bus] == pytest.approx(sol['v_mag'][bus], abs=V_TOL)
    assert pf['v_mag']['C'].iloc[0] < 0.95       # a real voltage drop, not the flat 1.0 of an LP


def test_line_flows_at_bus0_equal_the_independent_solve(pf, oracle):
    for t, sol in enumerate(oracle):
        for name, (s0, _s1) in sol['flows'].items():
            assert pf['p0'].iloc[t][name] == pytest.approx(s0.real, abs=FLOW_TOL)
            assert pf['q0'].iloc[t][name] == pytest.approx(s0.imag, abs=FLOW_TOL)


def test_both_terminals_close_kcl_at_the_100_mva_load_bus(pf, oracle):
    """bus1 terminals are read from the solved network (no tool serves p1/q1):
    they equal the oracle, and at C they sum to the 100 MVA load."""
    from services.pypsa_service import PyPSAService
    n = PyPSAService.get_network()
    for t, sol in enumerate(oracle):
        for name, (_s0, s1) in sol['flows'].items():
            assert n.lines_t.p1[name].iloc[t] == pytest.approx(s1.real, abs=FLOW_TOL)
            assert n.lines_t.q1[name].iloc[t] == pytest.approx(s1.imag, abs=FLOW_TOL)
    p_c = -(n.lines_t.p1['BC'].iloc[0] + n.lines_t.p1['AC'].iloc[0])
    q_c = -(n.lines_t.q1['BC'].iloc[0] + n.lines_t.q1['AC'].iloc[0])
    assert (p_c, q_c) == pytest.approx((60.0, 80.0), abs=FLOW_TOL)
    assert math.hypot(p_c, q_c) == pytest.approx(100.0, abs=FLOW_TOL)


def test_losses_and_the_slack_equal_the_independent_solve(pf, oracle):
    """Per-line loss p0 + p1 (MW x 1 h weights = MWh), and the slack generator
    supplies load plus losses in every snapshot."""
    loss = {name: [(sol['flows'][name][0] + sol['flows'][name][1]).real for sol in oracle] for name in LINES}
    by_branch = {r['name']: r for r in pf['losses']['by_branch']}
    for name, per_t in loss.items():
        assert by_branch[name]['loss_mwh'] == pytest.approx(sum(per_t), abs=FLOW_TOL)
        assert by_branch[name]['peak_mw'] == pytest.approx(max(per_t), abs=FLOW_TOL)
    assert pf['losses']['total_mwh'] == pytest.approx(sum(map(sum, loss.values())), abs=FLOW_TOL)
    for t in range(2):
        demand = sum(p[t] for p, _q in LOADS.values())
        assert pf['slack'].iloc[t]['Grid'] == pytest.approx(demand + sum(v[t] for v in loss.values()), abs=FLOW_TOL)


def test_the_summary_ranks_on_the_independent_apparent_power(pf, oracle):
    out = pf['summary']
    assert out['status'] == 'ok' and out['source'] == 'ac_pf'
    assert out['claims']['full_ac_thermal_compliance'] is False
    rows = _by_name(out)
    for name in LINES:
        peak = max(max(abs(s0), abs(s1)) for s0, s1 in (sol['flows'][name] for sol in oracle))
        assert rows[name]['basis'] == 'ac_apparent_power'
        assert rows[name]['peak_flow'] == pytest.approx(peak, abs=SUMMARY_TOL)
        assert rows[name]['util_pct'] == pytest.approx(100 * peak / 200.0, abs=SUMMARY_TOL)


# ── the exact arithmetic on the LC-T07 weighted fixture ──────────────────

def test_p60_q80_at_both_terminals_is_exactly_100_mva(solved):
    """L_hot (100 MVA): snapshot 0 carries 60 MW + j80 Mvar at both terminals;
    the other snapshots keep their active-only flows (|p| <= 30)."""
    from services.pypsa_service import PyPSAService
    n = solved['network']
    n.lines_t.p0.loc[LT07_SNAPSHOTS[0], 'L_hot'] = 60.0
    n.lines_t.p1.loc[LT07_SNAPSHOTS[0], 'L_hot'] = -60.0
    q0 = pd.DataFrame(0.0, index=LT07_SNAPSHOTS, columns=n.lines.index)
    q0.loc[LT07_SNAPSHOTS[0], 'L_hot'] = 80.0
    q1 = -q0
    PyPSAService.get_solver_state().update(
        ac_pf_results={'lines_t.p0': n.lines_t.p0.copy(), 'lines_t.p1': n.lines_t.p1.copy(),
                       'lines_t.q0': q0, 'lines_t.q1': q1,
                       'transformers_t.p0': n.transformers_t.p0.copy(),
                       'transformers_t.p1': n.transformers_t.p1.copy()},
        ac_pf_convergence={str(s): True for s in LT07_SNAPSHOTS},
        ac_pf_convergence_list=[{'snapshot': str(s), 'ok': True} for s in LT07_SNAPSHOTS],
        ac_pf_converged_count=4, ac_pf_total_snapshots=4)
    row = _by_name(lf.get_loadflow_summary(source='ac_pf', limit=25))['L_hot']
    assert row['basis'] == 'ac_apparent_power'
    assert row['peak_flow'] == 100.0
    assert row['util_pct'] == 100.0
    assert row['peak_at'] == str(LT07_SNAPSHOTS[0])
    assert (row['h_at_limit'], row['h_over_limit']) == (1.0, 0.0)
