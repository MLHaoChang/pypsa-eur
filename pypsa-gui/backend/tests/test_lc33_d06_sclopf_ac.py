# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-33 LC-D06: an N-1 secure solve and the chained AC stage, through the chat tools.

SCLOPF. Two identical parallel lines join A and B (s_nom 60 MW each). A holds a
10 EUR/MWh unit, B a 50 EUR/MWh unit (both fixed at 200 MW) and a 100 MW Load,
over one hour at weight 1. Without N-1 the cheap unit serves all of it over
both lines (50/50 MW): objective 100 x 10 = 1000. With every line an outage
candidate, the line left in service after an outage carries the other's flow
too (BODF 1 for identical parallel lines), so f1 + f2 <= 60: the base flows
are 30/30, and 60 x 10 + 40 x 50 = 2600. Raising both s_nom to 80 gives
40/40 and 80 x 10 + 20 x 50 = 1800. The cases are set through
`update_solver_config` and solved through `run_simulation`; the flows come
back through `get_results('lines')` and the objective through
`get_simulation_status`.

AC stage. The LC-D07 3-bus network with a 5 EUR/MWh, 30 MW unit `GenB` at B
(PQ, q 0) beside the slack `Grid` at A. `run_ac_pf_after_lopf` chains the AC
power flow onto `run_simulation`. The LP serves the 100 MW (then 50 MW) of
load with GenB at 30 MW and Grid at the rest. The AC stage keeps GenB at its
LP setpoint exactly; the slack carries its LP dispatch plus the losses. The
losses and voltages come from the independent Ybus/Newton solve of
`test_lc06_d07_static_pf.py` with GenB's 30 MW injected at B, never from
pypsa's pf. The mutation doubles every line's resistance: the LP (which sees
x only) keeps its objective to 1e-9 relative, and the losses move to the
independent solve's new value.
"""
import math

import pandas as pd
import pypsa
import pytest

from services import chat_tools
from tests import test_lc06_d07_static_pf as d07
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

# ── SCLOPF ───────────────────────────────────────────────────────────────

LOAD_MW = 100.0
CHEAP, DEAR = 10.0, 50.0
OBJ_TOL = 1e-6
FLOW_TOL = 1e-6


def parallel_network(s_nom: float) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=1, freq='h'))
    n.snapshot_weightings.loc[:, :] = 1.0
    for bus in ('A', 'B'):
        n.add('Bus', bus, v_nom=110.0)
    n.add('Carrier', 'gas')
    n.add('Generator', 'cheap', bus='A', carrier='gas', p_nom=200.0, marginal_cost=CHEAP)
    n.add('Generator', 'dear', bus='B', carrier='gas', p_nom=200.0, marginal_cost=DEAR)
    for name in ('L1', 'L2'):
        n.add('Line', name, bus0='A', bus1='B', r=1.0, x=10.0, s_nom=s_nom)
    n.add('Load', 'load', bus='B', p_set=LOAD_MW)
    return n


def sclopf_oracle(s_nom: float, secure: bool) -> tuple[float, float]:
    """(objective, flow on each line): with N-1 the pair carries at most one s_nom."""
    corridor = min(LOAD_MW, s_nom if secure else 2 * s_nom)
    return corridor * CHEAP + (LOAD_MW - corridor) * DEAR, corridor / 2


def solve_lines(seam, s_nom: float, secure: bool) -> tuple[dict, pd.DataFrame]:
    config = {'sclopf': True, 'sclopf_include_all_lines': True} if secure else {'sclopf': False}
    state = seam(parallel_network(s_nom), **config)()
    assert state['status'] == 'completed', state
    return state, d07._frame(chat_tools.get_results('lines'))


@pytest.mark.parametrize('s_nom,secure', [(60.0, True), (60.0, False), (80.0, True)],
                         ids=['n1', 'n1_off', 'n1_s_nom_80'])
def test_n1_limits_the_corridor_to_one_lines_rating(seam, s_nom, secure):
    objective, flow = sclopf_oracle(s_nom, secure)
    assert (objective, flow) == {(60.0, True): (2600.0, 30.0), (60.0, False): (1000.0, 50.0),
                                 (80.0, True): (1800.0, 40.0)}[(s_nom, secure)]
    state, p0 = solve_lines(seam, s_nom, secure)
    assert state['objective'] == pytest.approx(objective, abs=OBJ_TOL)
    for line in ('L1', 'L2'):
        assert p0[line].iloc[0] == pytest.approx(flow, abs=FLOW_TOL)
    if secure:
        # Outage of either line: BODF 1, so the survivor carries both base flows.
        assert p0['L1'].iloc[0] + p0['L2'].iloc[0] == pytest.approx(s_nom, abs=FLOW_TOL)


# ── the chained AC stage ─────────────────────────────────────────────────

GEN_B_MW, GEN_B_COST = 30.0, 5.0
REL = 1e-9


def ac_network(r_scale: float) -> pypsa.Network:
    n = d07.network()
    n.lines['r'] *= r_scale
    n.add('Generator', 'GenB', bus='B', carrier='grid', p_nom=GEN_B_MW, marginal_cost=GEN_B_COST)
    return n


@pytest.fixture
def with_gen_b(monkeypatch):
    """The LC-D07 oracle with GenB's LP setpoint injected at B and resistances scaled."""
    lines, loads = dict(d07.LINES), dict(d07.LOADS)

    def use(r_scale: float):
        monkeypatch.setattr(d07, 'LOADS', {**loads, 'B': ([p - GEN_B_MW for p in loads['B'][0]], loads['B'][1])})
        monkeypatch.setattr(d07, 'LINES', {k: (b0, b1, r * r_scale, x, b)
                                           for k, (b0, b1, r, x, b) in lines.items()})
        try:
            return [d07.independent_pf(t) for t in range(2)]
        finally:
            monkeypatch.setattr(d07, 'LINES', lines)
            monkeypatch.setattr(d07, 'LOADS', loads)
    return use


LOAD_B, LOAD_C = d07.LOADS['B'][0], d07.LOADS['C'][0]


def load_mw(t: int) -> float:
    return LOAD_B[t] + LOAD_C[t]


def solve_chained(seam, r_scale: float) -> dict:
    state = seam(ac_network(r_scale), run_ac_pf_after_lopf=True, ac_pf_x_tol=d07.X_TOL)()
    assert state['status'] == 'completed' and state['condition'] == 'lopf+ac_pf_ok', state
    status = chat_tools.get_results('ac_pf_status', source='ac_pf')
    assert status['converged_count'] == status['total_snapshots'] == 2
    assert status['slack_bus_used'] == d07.SLACK
    return {'objective': state['objective'],
            'ac': d07._frame(chat_tools.get_results('generators', source='ac_pf')),
            'lp': d07._frame(chat_tools.get_results('generators', source='lopf')),
            'v_mag': d07._frame(chat_tools.get_results('voltages', source='ac_pf'))}


def losses_of(oracle: dict) -> float:
    return sum((s0 + s1).real for s0, s1 in oracle['flows'].values())


def lp_objective() -> float:
    return sum(GEN_B_MW * GEN_B_COST + (load_mw(t) - GEN_B_MW) * 10.0 for t in range(2))


def test_the_chained_ac_stage_keeps_the_lp_setpoints_and_the_slack_takes_the_losses(seam, with_gen_b):
    lp_obj = lp_objective()
    assert lp_obj == 30 * 5 * 2 + (70 + 20) * 10
    run = solve_chained(seam, 1.0)
    oracle = with_gen_b(1.0)
    assert run['objective'] == pytest.approx(lp_obj, rel=REL)
    for t, snap in enumerate(run['ac'].index):
        assert run['lp'].at[snap, 'GenB'] == pytest.approx(GEN_B_MW, abs=FLOW_TOL)
        assert run['lp'].at[snap, 'Grid'] == pytest.approx(load_mw(t) - GEN_B_MW, abs=FLOW_TOL)
        assert run['ac'].at[snap, 'GenB'] == run['lp'].at[snap, 'GenB']
        losses = losses_of(oracle[t])
        assert losses > 0
        assert run['ac'].at[snap, 'Grid'] == pytest.approx(load_mw(t) - GEN_B_MW + losses, abs=FLOW_TOL)
        for bus in d07.BUSES:
            assert run['v_mag'].at[snap, bus] == pytest.approx(oracle[t]['v_mag'][bus], abs=d07.V_TOL)


def test_doubling_the_resistance_moves_only_the_losses(seam, with_gen_b):
    """The mutation, against the same independent solve: losses rise, the LP does not move."""
    base, doubled = with_gen_b(1.0), with_gen_b(2.0)
    run = solve_chained(seam, 2.0)
    assert math.isclose(run['objective'], lp_objective(), rel_tol=REL)
    for t, snap in enumerate(run['ac'].index):
        delta = losses_of(doubled[t]) - losses_of(base[t])
        assert delta > 1e-3
        assert run['ac'].at[snap, 'Grid'] - (load_mw(t) - GEN_B_MW) == pytest.approx(
            losses_of(base[t]) + delta, abs=FLOW_TOL)
