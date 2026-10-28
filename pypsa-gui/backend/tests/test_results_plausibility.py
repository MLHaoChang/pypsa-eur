"""Native screening rules: independent small fixtures, not domain certification."""
import pandas as pd
import pypsa
import pytest


@pytest.fixture
def network():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2026-01-01', periods=2, freq='h'))
    n.add('Bus', 'B', carrier='AC')
    n.add('Generator', 'Solar', bus='B', carrier='solar', p_nom=100)
    n.generators['p_nom_opt'] = 100.0
    n.generators_t.p = pd.DataFrame({'Solar': [100.0, 100.0]}, index=n.snapshots)
    return n


def evaluate(n, **kwargs):
    from services.results.plausibility import evaluate as native
    return native(n, **kwargs)


def flagged(result, rule):
    return [f for f in result['findings'] if f['rule_id'] == rule and f['verdict'] == 'flagged']


def test_lost_load_without_an_admitted_cap_is_an_error(network):
    capture = {'lost_load_t': pd.DataFrame({'B': [0., 12.]}, index=network.snapshots)}
    result = evaluate(network, capture=capture)
    f, = flagged(result, 'lost_load')
    assert f['severity'] == 'error'
    assert f['facts'][0]['value'] == 12
    assert f['facts'][0]['unit'] == 'MWh'
    assert 'screening' in f['explanation']


def test_branch_screen_uses_dynamic_planning_limit_and_both_endpoints(network):
    network.add('Bus', 'C')
    network.add('Line', 'L', bus0='B', bus1='C', s_nom=100)
    network.lines_t.p0 = pd.DataFrame({'L': [10., 10.]}, index=network.snapshots)
    network.lines_t.p1 = pd.DataFrame({'L': [-10., -80.]}, index=network.snapshots)
    network.lines_t.s_max_pu = pd.DataFrame({'L': [1., 0.5]}, index=network.snapshots)
    f, = flagged(evaluate(network, capture=None), 'branch_overload')
    assert f['severity'] == 'error'
    assert [f['facts'][i]['value'] for i in range(3)] == [1.6, 100, 50]
    assert 'active-power' in f['explanation'] and 'LOPF' in f['explanation']


def test_curtailment_is_a_per_period_weighted_screen_not_a_design_failure(network):
    network.generators_t.p['Solar'] = [40., 40.]
    f, = flagged(evaluate(network, capture=None), 'curtailment_share')
    assert f['severity'] == 'warning'
    assert f['facts'][0]['value'] == 0.6
    assert 'least-cost design' in f['explanation']


def test_storage_screen_uses_both_directions_and_relative_capacity_tolerance(network):
    network.add('StorageUnit', 'Battery', bus='B', p_nom=1000, efficiency_store=0.9, efficiency_dispatch=0.9)
    network.storage_units_t.p_store = pd.DataFrame({'Battery': [0.0005, 2.]}, index=network.snapshots)
    network.storage_units_t.p_dispatch = pd.DataFrame({'Battery': [0.0005, 3.]}, index=network.snapshots)
    f, = flagged(evaluate(network, capture=None), 'storage_simultaneous')
    assert f['severity'] == 'warning'
    assert [f['facts'][i]['value'] for i in range(3)] == [2., 3., 0.001]
    assert 'StorageUnit' in f['explanation'] and 'surplus' in f['explanation']


@pytest.mark.parametrize('bound,opt,word', [('p_nom_max', 100., 'upper'), ('p_nom_min', 40., 'forced')])
def test_build_at_a_finite_bound_is_a_design_warning(network, bound, opt, word):
    network.generators.loc['Solar', 'p_nom_extendable'] = True
    network.generators.loc['Solar', bound] = opt
    network.generators.loc['Solar', 'p_nom_opt'] = opt
    f, = flagged(evaluate(network, capture=None), 'build_at_bound')
    assert f['severity'] == 'warning'
    assert f['facts'][0]['value'] == opt
    assert word in f['explanation']


@pytest.mark.parametrize('cap,severity', [(20., 'warning'), (2., 'error')])
def test_admitted_solve_time_ens_cap_changes_severity_not_the_fact(network, cap, severity):
    capture = {'lost_load_t': pd.DataFrame({'B': [0., 12.]}, index=network.snapshots)}
    f, = flagged(evaluate(network, capture=capture, ens_targets={None: {'cap_mwh': cap, 'source': 'native ENS report'}}), 'lost_load')
    assert f['severity'] == severity
    assert f['facts'][0]['value'] == 12.
    assert f['basis'] == 'native ENS report'


@pytest.mark.parametrize('cap', ['bad', True, float('nan'), -1.])
def test_invalid_cap_cannot_hide_observed_loss_or_authorise_an_allowance(network, cap):
    capture = {'lost_load_t': pd.DataFrame({'B': [0., 12.]}, index=network.snapshots)}
    f, = flagged(evaluate(network, capture=capture, ens_targets={None: {'cap_mwh': cap, 'source': 'malformed report'}}), 'lost_load')
    assert f['severity'] == 'error' and f['facts'][0]['value'] == 12.
    assert f['threshold']['cap_mwh'] is None


def test_raw_price_trigger_normalises_both_objective_weightings(network):
    network.set_snapshots(pd.MultiIndex.from_product([[2030], pd.date_range('2026-01-01', periods=2, freq='h')]))
    network.snapshot_weightings['objective'] = 2.
    network.investment_period_weightings['years'] = 5.
    capture = {'lost_load_t': pd.DataFrame({'B': [0., 0.]}, index=network.snapshots), 'voll_eur_per_mwh': 8000.}
    raw = pd.DataFrame({'B': [64000., 0.]}, index=network.snapshots)
    f, = flagged(evaluate(network, capture=capture, raw_prices=raw, period_objective_weights={2030: 4.}), 'lost_load')
    assert f['severity'] == 'warning' and f['facts'][0]['value'] == 8000.
    assert 'without shedding' in f['explanation']
    missing = evaluate(network, capture=capture, raw_prices=raw)
    assert not flagged(missing, 'lost_load')
    assert 'investment objective' in str(missing['coverage'])


def test_curtailment_preserves_investment_periods(network):
    network.set_snapshots(pd.MultiIndex.from_product([[2030, 2035], pd.date_range('2026-01-01', periods=2, freq='h')]))
    network.generators_t.p = pd.DataFrame({'Solar': [20., 20., 100., 100.]}, index=network.snapshots)
    network.snapshot_weightings['generators'] = [2., 1., 4., 1.]
    network.investment_period_weightings['years'] = [5., 10.]
    f, = flagged(evaluate(network, capture=None), 'curtailment_share')
    assert f['facts'][0]['period'] == '2030' and f['facts'][0]['value'] == 0.8
    assert f['facts'][1]['value'] == 1200.


@pytest.mark.parametrize('carrier,penalty,expected', [('hydrogen', 0., 0), ('unseen', 0., 0), ('unseen', 2., 1)])
def test_native_curtailment_helper_owns_carrier_admission(network, carrier, penalty, expected):
    network.generators.loc['Solar', 'carrier'] = carrier
    network.generators.loc['Solar', 'curtailment_cost'] = penalty
    network.generators_t.p['Solar'] = 40.
    assert len(flagged(evaluate(network, capture=None), 'curtailment_share')) == expected


@pytest.mark.parametrize('fault', ['capture_missing', 'capture_nan', 'dispatch_nan', 'zero_available', 'negative_weights', 'missing_weights', 'unknown_bus'])
def test_missing_invalid_or_zero_basis_is_explicitly_not_evaluated(network, fault):
    capture = {'lost_load_t': pd.DataFrame({'B': [0., 12.]}, index=network.snapshots)}
    rule = 'lost_load'
    if fault == 'capture_missing': capture = None
    if fault == 'capture_nan': capture['lost_load_t'].iloc[0, 0] = float('nan')
    if fault == 'dispatch_nan': network.generators_t.p.iloc[0, 0] = float('nan'); rule = 'curtailment_share'
    if fault == 'zero_available': network.generators_t.p[:] = 0.; network.generators['p_nom_opt'] = 0.; rule = 'curtailment_share'
    if fault == 'negative_weights': network.snapshot_weightings['generators'] = -1.
    if fault == 'missing_weights': network.snapshot_weightings.drop(columns=['generators'], inplace=True)
    if fault == 'unknown_bus': capture['lost_load_t'].columns = ['Unknown']
    got = evaluate(network, capture=capture)
    assert got['coverage']['rules'][rule]['verdict'] == 'not_evaluated'
    assert not flagged(got, rule)
    import json
    json.dumps(got, allow_nan=False)


def test_clean_fixture_has_no_findings_and_pins_the_shared_contract(network):
    got = evaluate(network, capture={'lost_load_t': pd.DataFrame({'B': [0., 0.]}, index=network.snapshots)})
    assert not got['findings']
    assert set(got['coverage']['rules']) == {'lost_load', 'branch_overload', 'curtailment_share', 'storage_simultaneous', 'build_at_bound'}
    assert got['coverage']['rules']['curtailment_share']['verdict'] == 'passed'
    assert got['coverage']['rules']['storage_simultaneous']['not_evaluated']


@pytest.mark.parametrize('kind,nominal', [('Line', 's_nom'), ('Transformer', 's_nom'), ('Link', 'p_nom'), ('Store', 'e_nom')])
def test_capacity_bound_screen_covers_additional_native_classes(network, kind, nominal):
    kwargs = {'bus': 'B'} if kind == 'Store' else {'bus0': 'B', 'bus1': 'B'}
    network.add(kind, 'Asset', **kwargs, **{nominal + '_extendable': True, nominal + '_max': 10.})
    network.components[kind].static.loc['Asset', nominal + '_opt'] = 10.
    f, = flagged(evaluate(network, capture=None), 'build_at_bound')
    assert f['facts'][0]['component_class'] == kind and f['facts'][0]['value'] == 10.
    assert set(f) == {'rule_id', 'severity', 'verdict', 'facts', 'threshold', 'basis', 'explanation'}


@pytest.mark.parametrize('cap,severity', [(None, 'error'), (20., 'warning')])
def test_native_load_identity_is_distinct_from_its_bus(network, cap, severity):
    network.add('Load', 'Demand', bus='B', p_set=20.)
    capture = {'lost_load_t': pd.DataFrame({'Demand': [0., 12.]}, index=network.snapshots)}
    targets = None if cap is None else {None: {'cap_mwh': cap, 'source': 'native ENS report'}}
    f, = flagged(evaluate(network, capture=capture, ens_targets=targets), 'lost_load')
    assert f['severity'] == severity
    assert f['facts'][1]['bus'] == 'B' and f['facts'][1]['load'] == 'Demand'
    assert f['facts'][1]['value'] == 12.


@pytest.mark.parametrize('fault', ['missing', 'nan'])
def test_missing_investment_energy_basis_is_not_silently_one_year(network, fault):
    network.set_snapshots(pd.MultiIndex.from_product([[2030], pd.date_range('2026-01-01', periods=2, freq='h')]))
    network.generators_t.p = pd.DataFrame({'Solar': [40., 40.]}, index=network.snapshots)
    if fault == 'missing': network.investment_period_weightings.drop(columns='years', inplace=True)
    else: network.investment_period_weightings.loc[2030, 'years'] = float('nan')
    capture = {'lost_load_t': pd.DataFrame({'B': [0., 12.]}, index=network.snapshots)}
    got = evaluate(network, capture=capture)
    assert not flagged(got, 'lost_load') and not flagged(got, 'curtailment_share')
    assert got['coverage']['rules']['lost_load']['verdict'] == 'not_evaluated'
    assert got['coverage']['rules']['curtailment_share']['verdict'] == 'not_evaluated'


@pytest.mark.parametrize('multiple,expected', [(0.5, 0), (1., 0), (2., 1)])
def test_branch_relative_tolerance_boundary(network, multiple, expected):
    from services.results.plausibility import REL_TOL
    network.add('Bus', 'C')
    network.add('Line', 'L', bus0='B', bus1='C', s_nom=100.)
    flow = 100 * (1 + multiple * REL_TOL)
    network.lines_t.p0 = pd.DataFrame({'L': [flow, flow]}, index=network.snapshots)
    network.lines_t.p1 = -network.lines_t.p0
    assert len(flagged(evaluate(network, capture=None), 'branch_overload')) == expected


@pytest.mark.parametrize('multiple,expected', [(0.5, 0), (1., 0), (2., 1)])
def test_storage_relative_tolerance_boundary(network, multiple, expected):
    from services.results.plausibility import REL_TOL, ABS_POWER_TOL_MW
    network.add('StorageUnit', 'Battery', bus='B', p_nom=1000.)
    flow = multiple * max(ABS_POWER_TOL_MW, 1000 * REL_TOL)
    network.storage_units_t.p_store = pd.DataFrame({'Battery': [flow, flow]}, index=network.snapshots)
    network.storage_units_t.p_dispatch = network.storage_units_t.p_store.copy()
    assert len(flagged(evaluate(network, capture=None), 'storage_simultaneous')) == expected


def test_actual_solver_capture_uses_load_ids_and_solve_time_energy(client, install_network, session_ctx, bound_ctx):
    import queue
    import threading
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2026-01-01', periods=2, freq='h'))
    n.add('Bus', 'Bus A', carrier='AC')
    n.add('Load', 'Demand', bus='Bus A', p_set=22.)
    n.add('Generator', 'Supply', bus='Bus A', p_nom=10., marginal_cost=10.)
    install_network(n)
    assert client.post('/api/projects/native-loss').status_code == 200
    ctx, sink = session_ctx(client), {}
    with bound_ctx(client):
        status, condition = run_simulation(SolverConfig(voll=8000.), ctx.network, PyPSAService.get_lock(),
            threading.Event(), queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw))
        assert condition == 'optimal'
        capture = sink['last_lost_load']
        assert list(capture['lost_load_t'].columns) == ['Demand']
        f, = flagged(evaluate(ctx.network, capture=capture), 'lost_load')
        assert f['facts'][0]['value'] == 24. and f['facts'][1]['bus'] == 'Bus A'
        assert f['facts'][1]['load'] == 'Demand'
        ctx.network.snapshot_weightings['generators'] = 99.
        f, = flagged(evaluate(ctx.network, capture=capture), 'lost_load')
        assert f['facts'][0]['value'] == 24.  # Captured solve-time MWh, not restored/draft weights.
