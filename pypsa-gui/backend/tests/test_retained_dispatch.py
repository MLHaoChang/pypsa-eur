"""LC-03: the Dispatch family is kept with its run as a bounded digest.

The tab's reads (`generators`, `storage_dispatch`) are per-snapshot tables;
a run keeps a digest of them (services/results/dispatch_digest.py), and a
run_id read answers that digest for the current and for a past run alike.

oj-grid is solved at Gas 50 then 60 EUR/MWh with the same dispatch
(gas_t = 90 - 80 pu_t, tests/oj_grid.py), so the hand figures below hold for
both runs.
"""
import numpy as np
import pandas as pd
import pypsa
import pytest

from services import chat_tools, result_views as rv, retained_results, run_evidence
from services.results import dispatch_digest as dd
from tests.oj_grid import HOURS, LOAD_MW, SOLAR_MW, SOLAR_PU
from tests.test_oj01_latest_loadflow_journey import oj_grid  # noqa: F401
from tests.test_retained_summary import repriced  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

GAS_T = [LOAD_MW - SOLAR_MW * pu for pu in SOLAR_PU]


def _network(p: dict, *, weight: float = 1.0, storage: dict | None = None) -> pypsa.Network:
    hours = len(next(iter(p.values())))
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=hours, freq='h'))
    n.snapshot_weightings.loc[:, :] = weight
    n.add('Bus', 'B')
    for name in p:
        n.add('Generator', name, bus='B', carrier=name.split('-')[0])
    n.generators_t.p = pd.DataFrame(p, index=n.snapshots, dtype=float)
    for name in storage or {}:
        n.add('StorageUnit', name, bus='B', carrier='battery')
    if storage:
        n.storage_units_t.p = pd.DataFrame(storage, index=n.snapshots, dtype=float)
    return n


def test_energy_hours_and_mean_are_weighted():
    """At a 2 h weighting an unweighted sum is half the energy and hours."""
    d = dd.dispatch_digest(_network({'gas-1': [10.0, 0.0, 30.0]}, weight=2.0))
    (row,) = d['generators']['units']
    assert row['energy_mwh'] == pytest.approx(2 * 40.0)
    assert row['hours'] == pytest.approx(2 * 2)
    assert row['mean_mw'] == pytest.approx(80.0 / 6)
    assert row['peak_mw'] == 30.0 and row['peak_snapshot'] == '2030-01-01 02:00:00'
    assert d['weighted_hours'] == 6.0


def test_storage_keeps_discharge_and_charge_apart():
    n = _network({'gas-1': [0.0, 0.0, 0.0]}, storage={'bat': [5.0, -3.0, 0.0]})
    (row,) = dd.dispatch_digest(n)['storage_dispatch']['units']
    assert row['energy_out_mwh'] == 5.0 and row['energy_in_mwh'] == 3.0
    assert row['peak_discharge_mw'] == 5.0 and row['peak_charge_mw'] == 3.0
    assert row['hours_discharging'] == 1.0 and row['hours_charging'] == 1.0
    assert row['peak_charge_snapshot'] == '2030-01-01 01:00:00'


def test_units_and_carriers_order_by_energy_then_name():
    d = dd.dispatch_digest(_network({'wind-b': [5.0], 'wind-a': [5.0], 'gas-1': [9.0]}))
    assert [u['name'] for u in d['generators']['units']] == ['gas-1', 'wind-a', 'wind-b']
    assert [(c['name'], c['energy_mwh']) for c in d['generators']['by_carrier']] == [
        ('wind', 10.0), ('gas', 9.0)]


def test_the_worst_case_is_bounded_by_construction():
    """500 units over 8760 h fit, trimmed smallest-first and counted."""
    rng = np.random.default_rng(0)
    p = {f'c{i % 60}-unit-{i:03d}': rng.random(8760) * (i + 1) for i in range(500)}
    d = dd.dispatch_digest(_network(p, storage={f's{i}': rng.random(8760) - 0.5 for i in range(50)}))
    assert len(run_evidence._json_bytes(d)) <= dd.BUDGET_BYTES < run_evidence.MAX_SECTION_BYTES
    gens = d['generators']
    assert gens['units_omitted'] > 0 and len(gens['units']) + gens['units_omitted'] == 500
    energies = [u['energy_mwh'] for u in gens['units']]
    assert energies == sorted(energies, reverse=True)
    assert gens['units'][0]['name'] == 'c19-unit-499'                  # the largest unit stays
    assert dd.dispatch_digest(_network(p)) == dd.dispatch_digest(_network(p))   # deterministic


def test_non_finite_values_are_null():
    d = dd.dispatch_digest(_network({'gas-1': [np.inf, 1.0]}))
    (row,) = d['generators']['units']
    assert row['energy_mwh'] is None and row['peak_mw'] is None and row['peak_snapshot'] is None


def test_multi_period_is_refused_by_name():
    n = _network({'gas-1': [1.0, 1.0]})
    n.snapshots = pd.MultiIndex.from_product([[2030], n.snapshots])
    assert dd.dispatch_digest(n) == {'status': 'unavailable', 'reason': 'multi_period_not_digested'}


def test_carrier_energy_matches_compare_dispatch(oj_grid):  # noqa: F811
    """One formula: the digest's carrier energy is Compare's dispatch by carrier."""
    from services.compare.dispatch import _compute_dispatch_summary
    from services.pypsa_service import PyPSAService

    n = PyPSAService.get_network()
    d = dd.dispatch_digest(n)
    energy = {c['name']: c['energy_mwh'] for c in d['generators']['by_carrier']}
    for c in d['storage_dispatch']['by_carrier']:
        energy[c['name']] = energy.get(c['name'], 0.0) + c['energy_out_mwh']
    compare = _compute_dispatch_summary(n, [], False, True).dispatch_gwh_by_carrier
    produced = {c: e for c, e in energy.items() if e}     # Compare leaves idle carriers out
    assert {c: pytest.approx(v.total * 1000) for c, v in compare.items() if v.total} == produced
    assert energy['gas'] == pytest.approx(sum(GAS_T), rel=1e-6)


def test_an_older_run_answers_its_dispatch_digest(repriced):  # noqa: F811
    old = chat_tools.get_results(result_kind='generators', run_id=repriced['older']['run_id'])
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['section'] == 'results_dispatch' and old['run']['is_current'] is False
    data = old['data']
    assert data['shape'] == 'digest' and data['snapshots'] == HOURS
    gas = next(c for c in data['by_carrier'] if c['name'] == 'gas')
    assert gas['energy_mwh'] == pytest.approx(sum(GAS_T), rel=1e-6)
    assert gas['peak_mw'] == pytest.approx(max(GAS_T), rel=1e-6)
    storage = chat_tools.get_results(result_kind='storage_dispatch', run_id=repriced['older']['run_id'])
    assert storage['data']['shape'] == 'digest' and 'energy_in_mwh' in storage['data']['units'][0]


def test_the_current_run_answers_the_same_digest(repriced):  # noqa: F811
    run_id = repriced['newest']['run_id']
    latest = chat_tools.get_results(result_kind='generators', run_id=run_id)
    assert latest['scope'] == 'current' and latest['data']['shape'] == 'digest'
    kept = run_evidence.get_run_evidence(run_id, 'results_dispatch')['data']
    assert latest['data'] == dd.for_kind(kept, 'generators')


def test_the_plain_read_is_unchanged(repriced):  # noqa: F811
    plain = chat_tools.get_results(result_kind='generators')
    assert {'index', 'columns', 'data'} <= set(plain) and 'shape' not in plain


@pytest.mark.parametrize('extra', [{'source': 'ac_pf'}, {'limit': 5}, {'offset': 3}])
def test_a_run_refuses_what_its_digest_does_not_keep(repriced, extra):  # noqa: F811
    for run in ('older', 'newest'):
        out = chat_tools.get_results(result_kind='generators', run_id=repriced[run]['run_id'], **extra)
        assert out['status'] == 'unavailable' and 'data' not in out
        assert out['unavailable_kind'] == 'historical_scope_mismatch'
        assert 'peak_mw' in out['digest_fields']


def test_a_dispatch_run_without_its_section_refuses_with_its_id(repriced):  # noqa: F811
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-legacy04', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'summary': {'solver_kind': 'lopf'}})
    out = chat_tools.get_results(result_kind='generators', run_id='fg-legacy04')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-legacy04'
    assert out['unavailable_kind'] == 'historical_results_unavailable'
    assert 'dispatch' in out['message'] and 'data' not in out


def test_the_dispatch_view_names_its_section():
    assert rv.view('dispatch').historical_section == 'results_dispatch'
    assert retained_results.KEPT_KINDS['generators'] == 'results_dispatch'
