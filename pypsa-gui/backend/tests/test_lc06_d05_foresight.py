"""LC-06 LC-D05 / LC-D06: foresight strategies and solver outcomes, by hand.

Every solve goes through the ordinary `run_simulation` chat tool with the
strategy set by `update_solver_config`, the outcome is read through
`get_simulation_status`, and every number is computed here from the inputs:

* the three foresight strategies on the `test_myopic_horizon_cost.py` network
  (hand horizon cost 33,000; the per-period LP sum 13,000 is the wrong basis);
* a two-period build/retirement fixture, discounted by hand the way the app's
  auto-discount defines it (objective weight = years x (1+r)^-(period - first));
* a partial-year horizon, where capex is periodised by nyears = 0.5;
* a rolling-horizon dispatch, whose cost is the sum over every window;
* the refusals and stops: an unbounded LP and an unavailable solver never
  reach the solver (the real validator, not a patched probe); a MIP gap
  reaches the solver and bounds the objective; a real HiGHS time limit with
  no incumbent is a failed run with no objective. The other limit stops
  (no incumbent on the SCLOPF and myopic call sites, a finite incumbent)
  cannot be produced deterministically by a real solver, so there only the
  solver's return is stubbed at the PyPSA optimize boundary and the app's
  own solve path runs as is.
"""
import dataclasses
import itertools
import math
import time

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.test_myopic_horizon_cost import OLD_BROKEN_SUM, PERIODS, TRUE_HORIZON_COST, _analytic_network
from tests.test_workflow_tools import helpers_state  # noqa: F401

NAME = 'lc06-d05-foresight'
HOURS_PER_YEAR = 8760.0


def _run(session) -> dict:
    """One run_simulation call, then wait for a terminal solver state (and,
    for a completed run, its published run record)."""
    before = chat_tools.list_project_runs(status='completed')['total_count']
    frames = list(chat_service._dispatch_real_tool_call(
        session, {'id': f'lc06-d05-{time.monotonic_ns()}', 'name': 'run_simulation',
                  'input': {'rerun_current': True}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = chat_tools.get_simulation_status()
        published = chat_tools.list_project_runs(status='completed')['total_count'] > before
        if state.get('status') in ('failed', 'aborted') or (state.get('status') == 'completed' and published):
            return state
        time.sleep(0.2)
    raise AssertionError(f'solve did not finish: {chat_tools.get_simulation_status().get("status")}')


@pytest.fixture
def seam(client, session_ctx, install_network, monkeypatch):
    """`seam(network, **solver_config)` installs and opens the network, applies
    the config through `update_solver_config`, and returns the solve."""
    import harness.confirm as harness_confirm

    def start(network, **config):
        install_network(network)
        assert client.post(f'/api/projects/{NAME}').status_code == 200
        if config:
            chat_tools.update_solver_config(config)
        return lambda: _run(session)

    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            yield start
        chat_tools.set_chat_session(None)
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def decomposition() -> dict:
    return chat_tools.get_results('objective_decomposition')


def run_record() -> dict:
    """The latest completed run's evidence summary (what the run record keeps)."""
    run_id = chat_tools.list_project_runs(status='completed')['items'][0]['run_id']
    evidence = chat_tools.get_run_evidence(run_id, section='summary')
    return {'objective': evidence['metrics']['objective']['value'], 'condition': evidence['data']['condition']}


def log_lines() -> list[str]:
    return chat_tools.get_simulation_log_history()['lines']


def one_bus(weights, loads, gens) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=len(weights), freq='h'))
    n.snapshot_weightings.loc[:, :] = pd.DataFrame({c: list(weights) for c in n.snapshot_weightings.columns},
                                                   index=n.snapshots)
    n.add('Bus', 'B', v_nom=20)
    for name, kw in gens.items():
        n.add('Carrier', name.lower())
        n.add('Generator', name, bus='B', carrier=name.lower(), **kw)
    n.add('Load', 'Demand', bus='B', p_set=pd.Series(list(loads), index=n.snapshots, dtype=float))
    return n


# ── LC-D05: the three foresight strategies price the same horizon ────────────

MULTI = {'multi_investment_periods': True, 'investment_periods': PERIODS, 'voll': 0.0}


@pytest.mark.parametrize('strategy', [
    {'solve_strategy': 'full'},
    {'solve_strategy': 'myopic'},
    {'solve_strategy': 'myopic', 'lf_aggregate_future': True},
], ids=['perfect', 'myopic', 'limited'])
def test_every_foresight_reports_the_hand_horizon_cost(seam, strategy):
    state = seam(_analytic_network(), **MULTI, **strategy)()
    assert (state['status'], state['condition']) == ('completed', 'optimal'), state
    # 30,000 capex (100 MW x 100 EUR/MW in each of three periods) + 3,000 opex.
    assert state['objective'] == pytest.approx(TRUE_HORIZON_COST)
    assert TRUE_HORIZON_COST != pytest.approx(OLD_BROKEN_SUM)  # the oracle tells the bases apart
    d = decomposition()
    assert d['cost_breakdown_total'] == pytest.approx(TRUE_HORIZON_COST)
    # The per-period LP sum is never offered as the horizon total.
    assert 'myopic_horizon_total' not in d
    if strategy['solve_strategy'] == 'myopic':
        assert d['lp_total_scope'] == 'last_period'
        if not strategy.get('lf_aggregate_future'):
            assert d['myopic_lp_sum'] == pytest.approx(OLD_BROKEN_SUM)
    else:
        assert d['lp_total_scope'] == 'horizon'
        assert d['lp_total'] == pytest.approx(TRUE_HORIZON_COST)


# ── LC-D05 / LC-D04: two periods, a retirement and per-period vintages ───────

RATE, YEARS = 0.05, 10.0
G = {'capital_cost': 1000.0, 'marginal_cost': 1.0}
OLD = {'p_nom': 4.0, 'marginal_cost': 0.5}


def two_periods() -> pypsa.Network:
    """Two periods (2030, 2040) of ten years, each one year of two 4380 h
    snapshots. Old (4 MW, built 2020, 15-year life) serves 2030 only; G is
    extendable with one vintage per period."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product([[2030, 2040], pd.date_range('2030-01-01', periods=2, freq='h')],
                                     names=['period', 'timestep'])
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.snapshot_weightings.loc[:, :] = HOURS_PER_YEAR / 2
    n.investment_period_weightings['years'] = YEARS
    n.add('Bus', 'B', v_nom=20)
    n.add('Carrier', 'wind')
    n.add('Carrier', 'old')
    n.add('Load', 'Demand', bus='B', p_set=10.0)
    n.add('Generator', 'G', bus='B', carrier='wind', p_nom_extendable=True, p_nom_max=100.0,
          build_year=2030, lifetime=30, **G)
    n.add('Generator', 'Old', bus='B', carrier='old', build_year=2020, lifetime=15, **OLD)
    return n


def test_two_periods_discount_and_build_vintages_by_hand(seam):
    solve = seam(two_periods(), multi_investment_periods=True, investment_periods=[2030, 2040],
                 discount_rate=RATE, auto_discount_periods=True, voll=0.0)
    bounds = {'2030': {'p_nom_max': 100.0}, '2040': {'p_nom_max': 100.0}}
    assert chat_tools.set_vintage_bounds('Generator', 'G', bounds)['bounds'] == bounds
    state = solve()
    assert (state['status'], state['condition']) == ('completed', 'optimal'), state

    w = {p: YEARS * (1 + RATE) ** -(p - 2030) for p in (2030, 2040)}
    # 2030: Old serves 4 MW, G 6. 2040: Old retired (2020 + 15), G serves 10.
    # A 2030 vintage pays capex in both periods, a 2040 one in 2040 only, so
    # the extra 4 MW waits for 2040: G@2030 = 6, G@2040 = 4.
    capex = G['capital_cost'] * (6 * (w[2030] + w[2040]) + 4 * w[2040])
    opex = HOURS_PER_YEAR * (w[2030] * (OLD['p_nom'] * OLD['marginal_cost'] + 6 * G['marginal_cost'])
                             + w[2040] * 10 * G['marginal_cost'])
    assert state['objective'] == pytest.approx(capex + opex)
    # Undiscounted (both weights = years) the same plan costs more: the discount is in the answer.
    undiscounted = (G['capital_cost'] * (6 * 2 + 4) * YEARS
                    + HOURS_PER_YEAR * YEARS * (OLD['p_nom'] * OLD['marginal_cost'] + 16 * G['marginal_cost']))
    assert undiscounted != pytest.approx(state['objective'])

    vintages = chat_tools.get_vintage_results()['results']['Generator']['G']['periods']
    assert [(v['build_year'], v['p_nom_opt']) for v in vintages] == [(2030, pytest.approx(6.0)),
                                                                      (2040, pytest.approx(4.0))]


# ── LC-D04: a partial-year horizon periodises capex by nyears ────────────────

def test_a_half_year_horizon_charges_half_the_annual_capex(seam):
    weights = (HOURS_PER_YEAR / 4, HOURS_PER_YEAR / 4)
    nyears = sum(weights) / HOURS_PER_YEAR
    cheap = {'capital_cost': 60_000.0, 'marginal_cost': 10.0, 'p_nom_max': 6.0}
    backup = {'capital_cost': 20_000.0, 'marginal_cost': 50.0, 'p_nom_max': 100.0}
    state = seam(one_bus(weights, (10, 10), {'Cheap': dict(p_nom_extendable=True, **cheap),
                                             'Backup': dict(p_nom_extendable=True, **backup)}))()
    assert (state['status'], state['condition']) == ('completed', 'optimal'), state

    def per_mw(gen, years):
        return gen['capital_cost'] * years + sum(weights) * gen['marginal_cost']

    assert state['objective'] == pytest.approx(6 * per_mw(cheap, nyears) + 4 * per_mw(backup, nyears))
    assert nyears == 0.5
    # The annual (unperiodised) figure is a different number.
    assert 6 * per_mw(cheap, 1) + 4 * per_mw(backup, 1) != pytest.approx(state['objective'])


# ── LC-D06: rolling horizon sums every window ────────────────────────────────

def test_a_rolling_solve_reports_the_cost_of_every_window(seam):
    from routers.simulation import _compute_run_objective, _state
    loads, cost = (10, 20, 30, 40), 2.0
    # Windows of 2 stepping by 1: every inner snapshot is solved twice and must be counted once.
    state = seam(one_bus((1,) * 4, loads, {'G': dict(p_nom=50, marginal_cost=cost)}),
                 solve_strategy='rolling', rolling_horizon=2, rolling_overlap=1)()
    assert (state['status'], state['condition']) == ('completed', 'optimal'), state
    assert any('Rolling-horizon solve: 4 snapshots in windows of 2 (overlap 1)' in line for line in log_lines())
    hand = cost * sum(loads)
    assert state['objective'] == pytest.approx(hand)
    # The last window alone is not the run's cost.
    assert cost * sum(loads[2:]) != pytest.approx(hand)
    # The run record, and the solve queue's reader (same function, the project's own config).
    assert run_record()['objective'] == pytest.approx(hand)
    queue_cfg = dataclasses.replace(_state['solver_config'])
    assert _compute_run_objective(PyPSAService.get_network(), queue_cfg) == pytest.approx(hand)
    d = decomposition()
    assert d['cost_breakdown_total'] == pytest.approx(hand)
    assert d['lp_total_scope'] == 'last_window'
    assert d['residual_gap_eur'] is None  # no single LP total to reconcile against


def test_a_rolling_run_without_statistics_reports_no_cost(monkeypatch):
    # The fallback must not be the last window's LP (which is all `n.objective` holds).
    import services.cost_totals as cost_totals
    from routers.simulation import _compute_run_objective
    from services.solver_service import SolverConfig
    n = one_bus((1,) * 2, (10, 20), {'G': dict(p_nom=50, marginal_cost=2.0)})
    n._objective = 40.0
    monkeypatch.setattr(cost_totals, 'horizon_system_cost', lambda *_: None)
    assert _compute_run_objective(n, SolverConfig(solve_strategy='rolling')) is None
    assert _compute_run_objective(n, SolverConfig(solve_strategy='full')) == pytest.approx(40.0)


# ── LC-D06: refusals and stops ───────────────────────────────────────────────

def refused_before_solving(state, code, where):
    assert (state['status'], state['condition']) == ('failed', 'validation_failed'), state
    assert state['objective'] is None
    assert state['failure']['category'] == 'validation'
    lines = log_lines()
    assert any(f'[{code}' in line and where in line for line in lines), [l for l in lines if 'VALIDATION' in l]
    assert not any('Solve complete' in line for line in lines)


def test_an_unbounded_lp_is_refused_before_the_solver(seam):
    state = seam(one_bus((4380, 4380), (10, 10), {'Neg': dict(p_nom_extendable=True, marginal_cost=-5.0,
                                                              capital_cost=1.0)}))()
    refused_before_solving(state, 'generator_unbounded_neg_cost', "Generator 'Neg'")


def test_an_unavailable_solver_is_refused_by_the_real_dispatcher(seam):
    from linopy.solvers import available_solvers
    missing = next(s for s in ('gurobi', 'cplex', 'xpress', 'mosek', 'copt') if s not in available_solvers)
    state = seam(one_bus((1, 1), (10, 10), {'G': dict(p_nom=20, marginal_cost=1.0)}), solver_name=missing)()
    refused_before_solving(state, 'solver_unavailable', f"Solver '{missing}'")


UC = {'A': {'p_nom': 10.0, 'p_min_pu': 0.5, 'marginal_cost': 10.0, 'start_up_cost': 100.0},
      'Peaker': {'p_nom': 10.0, 'marginal_cost': 30.0}}


def uc_optimum(loads) -> float:
    """Every on/off pattern of the committable unit A (off before hour one):
    A serves the load when on (it must lie in A's 5..10 MW range), the peaker otherwise."""
    a, peak = UC['A'], UC['Peaker']
    best = float('inf')
    for on in itertools.product((False, True), repeat=len(loads)):
        if any(o and not (a['p_min_pu'] * a['p_nom'] <= load <= a['p_nom']) for o, load in zip(on, loads)):
            continue
        starts = sum(1 for prev, o in zip((False,) + on, on) if o and not prev)
        cost = starts * a['start_up_cost'] + sum(
            load * (a['marginal_cost'] if o else peak['marginal_cost']) for o, load in zip(on, loads))
        best = min(best, cost)
    return best


def test_a_mip_gap_reaches_the_solver_and_bounds_the_objective(seam):
    loads, gap = (10, 4, 10, 4), 0.25
    gens = {'A': dict(committable=True, up_time_before=0, **UC['A']), 'Peaker': dict(UC['Peaker'])}
    state = seam(one_bus((1,) * 4, loads, gens), mip_gap=gap)()
    assert (state['status'], state['condition']) == ('completed', 'optimal'), state
    assert any(f'MIP gap {gap:.3g}' in line for line in log_lines())
    optimum = uc_optimum(loads)
    assert optimum == 2 * 100 + 20 * 10 + 8 * 30  # two starts, A at 10 MW twice, the peaker at 4 MW twice
    assert optimum <= state['objective'] <= optimum * (1 + gap)


UC_HOURS = 168


def hard_uc() -> pypsa.Network:
    rng = np.random.default_rng(0)
    # Feasible without a limit: every unit starts off (up_time_before=0) and
    # an uncommitted peaker covers any load the units cannot.
    gens = {f'U{i}': dict(p_nom=20, committable=True, p_min_pu=0.4, min_up_time=3, up_time_before=0,
                          marginal_cost=10.0 + i, start_up_cost=50.0 * (i + 1)) for i in range(8)}
    gens['Peaker'] = dict(p_nom=200, marginal_cost=200.0)
    return one_bus((1,) * UC_HOURS, rng.uniform(20, 140, UC_HOURS), gens)


def failed_on_the_limit(state):
    assert (state['status'], state['condition']) == ('failed', 'time_limit'), state
    assert state['objective'] is None
    assert state['failure']['category'] == 'time_limit'
    assert any('without a feasible solution' in line for line in log_lines())


def test_the_hard_uc_solves_without_a_limit(seam):
    # The control for the next test: the same MIP is feasible, so only the limit stops it.
    state = seam(hard_uc(), mip_gap=0.05)()
    assert (state['status'], state['condition']) == ('completed', 'optimal'), state
    assert math.isfinite(state['objective'])


def test_a_real_time_limit_without_an_incumbent_is_a_failed_run(seam):
    # 1 ms stops HiGHS in presolve, before any feasible point.
    failed_on_the_limit(seam(hard_uc(), mip_gap=0.0, mip_time_limit_s=0.001)())


@pytest.fixture
def solver_stops(monkeypatch):
    """`solver_stops(method, incumbent)`: the real PyPSA solve runs, then its
    return becomes ("ok", "time_limit"), and without an incumbent the
    objective is NaN, which is what linopy reports for a limit stop with no
    feasible point."""
    from pypsa.optimization.optimize import OptimizationAccessor

    def stop(method, incumbent):
        real = getattr(OptimizationAccessor, method)

        def stopped(self, *args, **kwargs):
            real(self, *args, **kwargs)
            if not incumbent:
                self._n._objective = math.nan
            return 'ok', 'time_limit'
        monkeypatch.setattr(OptimizationAccessor, method, stopped)
    return stop


def two_bus_corridor() -> pypsa.Network:
    n = one_bus((1, 1), (10, 10), {'G': dict(p_nom=50, marginal_cost=1.0)})
    n.add('Bus', 'C', v_nom=20)
    n.loads.loc['Demand', 'bus'] = 'C'
    for name in ('L1', 'L2'):
        n.add('Line', name, bus0='B', bus1='C', x=0.1, r=0.01, s_nom=50)
    return n


@pytest.mark.parametrize('call_site', ['full', 'sclopf', 'myopic'])
def test_a_limit_stop_without_an_incumbent_fails_at_every_call_site(seam, solver_stops, call_site):
    if call_site == 'full':
        solver_stops('__call__', incumbent=False)
        solve = seam(one_bus((1, 1), (10, 10), {'G': dict(p_nom=20, marginal_cost=1.0)}))
    elif call_site == 'sclopf':
        solver_stops('optimize_security_constrained', incumbent=False)
        solve = seam(two_bus_corridor(), sclopf=True, sclopf_include_all_lines=True)
    else:
        solver_stops('__call__', incumbent=False)
        solve = seam(_analytic_network(), **MULTI, solve_strategy='myopic')
    failed_on_the_limit(solve())
    if call_site == 'sclopf':
        assert any('SCLOPF' in line for line in log_lines())
    if call_site == 'myopic':
        assert any('Myopic iteration for period 2030 failed' in line for line in log_lines())


def test_a_limit_stop_with_an_incumbent_completes_but_never_reads_as_optimal(seam, solver_stops):
    solver_stops('__call__', incumbent=True)
    loads, cost = (10, 20), 2.0
    state = seam(one_bus((1, 1), loads, {'G': dict(p_nom=50, marginal_cost=cost)}))()
    assert (state['status'], state['condition']) == ('completed', 'time_limit'), state
    assert state['objective'] == pytest.approx(cost * sum(loads))
    assert run_record() == {'objective': pytest.approx(cost * sum(loads)), 'condition': 'time_limit'}
