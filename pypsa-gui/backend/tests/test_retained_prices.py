"""LC-03: the Prices family is kept with its run as a bounded digest.

The tab's reads are `prices` (a per-snapshot, per-bus table) and
`price_drivers`; a run keeps a digest of the first and the route's payload of
the second (services/results/prices_digest.py), and a run_id read answers
them for the current and for a past run alike.

oj-grid's older run is solved at Gas 50 EUR/MWh: gas is the marginal unit in
every hour (gas_t = 90 - 80 pu_t lies inside 10..90 MW of its 100 MW), the
grid is uncongested and lossless, so every bus pays 50 in every hour. The
only load is at B3.
"""
import numpy as np
import pandas as pd
import pypsa
import pytest

from services import chat_tools, result_views as rv, retained_results, run_evidence
from services.results import prices_digest as pd_
from tests.oj_grid import HOURS
from tests.test_oj01_latest_loadflow_journey import oj_grid  # noqa: F401
from tests.test_retained_summary import repriced  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _network(prices: dict, load: dict | None = None, *, objective=1.0, generators=1.0,
             carriers: dict | None = None) -> pypsa.Network:
    hours = len(next(iter(prices.values())))
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=hours, freq='h'))
    n.snapshot_weightings['objective'] = objective
    n.snapshot_weightings['generators'] = generators
    for bus in prices:
        n.add('Bus', bus, carrier=(carriers or {}).get(bus, 'AC'))
    n.buses_t.marginal_price = pd.DataFrame(prices, index=n.snapshots, dtype=float)
    for bus in load or {}:
        n.add('Load', f'load-{bus}', bus=bus)
    if load:
        n.loads_t.p = pd.DataFrame({f'load-{b}': v for b, v in load.items()},
                                   index=n.snapshots, dtype=float)
    return n


def _prices(n, **kw):
    return pd_.prices_digest(n, **kw)['prices']


def test_the_load_weighted_mean_uses_load_times_the_objective_weighting():
    """Objective weights 1 and 3: Σ p·L·w / Σ L·w = (10·1·1 + 30·1·3) / 4 = 25.
    The generators column (5, 1) would give (50 + 90) / 6 instead."""
    n = _network({'B': [10.0, 30.0]}, {'B': [1.0, 1.0]}, objective=[1.0, 3.0], generators=[5.0, 1.0])
    overall = _prices(n)['overall']
    assert overall['load_weighted_mean'] == pytest.approx(25.0)
    assert overall['mean_price'] == pytest.approx(25.0)      # objective-weighted too
    assert pd_.prices_digest(n)['weighting'] == 'snapshot_weightings.objective'


def test_load_shape_moves_the_load_weighted_mean_only():
    n = _network({'B': [10.0, 30.0]}, {'B': [3.0, 1.0]})
    overall = _prices(n)['overall']
    assert overall['mean_price'] == pytest.approx(20.0)
    assert overall['load_weighted_mean'] == pytest.approx((30.0 + 30.0) / 4)


def test_no_load_gives_a_null_load_weighted_mean_with_its_reason():
    overall = _prices(_network({'B': [10.0, 30.0]}))['overall']
    assert overall['load_weighted_mean'] is None
    assert overall['load_weighted_mean_reason'] == 'no_load'
    assert overall['mean_price'] == pytest.approx(20.0)
    zero = _prices(_network({'B': [10.0, 30.0]}, {'B': [0.0, 0.0]}))['overall']
    assert zero['load_weighted_mean'] is None and zero['load_weighted_mean_reason'] == 'no_load'


def test_a_bus_without_load_is_out_of_the_load_weighting_but_in_the_stats():
    d = _prices(_network({'A': [100.0, 100.0], 'B': [10.0, 30.0]}, {'B': [1.0, 1.0]}))
    assert d['overall']['load_weighted_mean'] == pytest.approx(20.0)
    assert d['overall']['mean_price'] == pytest.approx(60.0)
    assert d['overall']['max_price'] == 100.0
    buses = {b['name']: b for b in d['buses']}
    assert buses['A']['load_weighted_mean'] is None and buses['A']['mean_price'] == 100.0
    assert buses['B']['load_weighted_mean'] == pytest.approx(20.0)
    assert 'load_weighted_mean_reason' not in d['overall']


def test_buses_order_by_max_price_then_name_with_the_peak_snapshot():
    d = _prices(_network({'b': [5.0, 9.0], 'a': [9.0, 1.0], 'c': [1.0, 2.0]}))
    assert [b['name'] for b in d['buses']] == ['a', 'b', 'c']
    assert d['buses'][1]['max_snapshot'] == '2030-01-01 01:00:00'
    assert d['buses'][0]['max_snapshot'] == '2030-01-01 00:00:00'


def test_non_finite_values_are_null():
    """inf is not a price. (A NaN dual is 0 on every price surface: the shared
    merit-order correction fills it.)"""
    d = _prices(_network({'A': [np.inf, -np.inf], 'B': [10.0, 20.0]}, {'A': [1.0, 1.0]}))
    a = next(b for b in d['buses'] if b['name'] == 'A')
    assert a['mean_price'] is None and a['max_price'] is None and a['max_snapshot'] is None
    assert a['load_weighted_mean'] is None
    assert d['overall']['load_weighted_mean'] is None     # A's load meets no finite price
    assert d['overall']['max_price'] == 20.0


def test_multi_period_is_refused_by_name():
    n = _network({'B': [1.0, 1.0]})
    n.snapshots = pd.MultiIndex.from_product([[2030], n.snapshots])
    assert pd_.prices_digest(n) == {'status': 'unavailable', 'reason': 'multi_period_not_digested'}


def _drivers(rows: int) -> dict:
    return {'threshold': 2000.0, 'total_above_threshold': rows, 'truncated': False,
            'rows': [{'snapshot': f'2030-01-01T{i % 24:02d}:00:00', 'bus': f'bus-{i:04d}',
                      'price': 9000.0 - i, 'marginal_gen': f'gen-{i:04d}', 'marginal_cost': 3000.0,
                      'carrier': 'load_shedding', 'dispatch': 12.5, 'voll_slack_active': True,
                      'voll_dispatch': 12.5, 'diagnosis': 'load_shedding'} for i in range(rows)]}


def test_the_worst_case_is_bounded_by_construction():
    """2,000 buses over 8,760 h and 200 price_drivers rows fit, trimmed and counted."""
    rng = np.random.default_rng(0)
    buses = {f'bus-{i:04d}': rng.random(8760) * 100 + i for i in range(2000)}
    carriers = {b: f'c{i % 30}' for i, b in enumerate(buses)}
    n = _network(buses, {b: rng.random(8760) for b in list(buses)[:500]}, carriers=carriers)
    d = pd_.prices_digest(n, price_drivers=_drivers(200))
    assert len(run_evidence._json_bytes(d)) <= pd_.BUDGET_BYTES < run_evidence.MAX_SECTION_BYTES
    p = d['prices']
    assert p['buses_omitted'] > 0 and len(p['buses']) + p['buses_omitted'] == 2000
    assert p['buses'][0]['name'] == 'bus-1999'             # the highest-priced bus stays
    assert len(p['by_carrier']) == 30 and p['carriers_omitted'] == 0
    drivers = d['price_drivers']
    assert len(drivers['rows']) + drivers['rows_omitted'] == 200
    assert pd_.prices_digest(n, price_drivers=_drivers(200)) == d    # deterministic


def test_a_small_price_drivers_payload_is_kept_as_it_is():
    payload = _drivers(1)
    d = pd_.prices_digest(_network({'B': [1.0]}), price_drivers=payload)
    assert d['price_drivers'] == {**payload, 'rows_omitted': 0}
    assert pd_.for_kind(d, 'price_drivers') is d['price_drivers']


def test_statistics_match_compare_prices():
    """One formula: overall and per-carrier statistics are Compare's."""
    from services.compare.prices import _compute_prices_summary

    rng = np.random.default_rng(2)
    n = _network({f'b{i}': rng.random(48) * 80 - 10 for i in range(6)},
                 objective=list(rng.random(48) + 0.5),
                 carriers={f'b{i}': ('AC', 'H2')[i % 2] for i in range(6)})
    d = _prices(n)
    cmp = _compute_prices_summary(n, [], False, True)
    for key in ('mean_price', 'median_price', 'p90_price'):
        assert d['overall'][key] == pytest.approx(getattr(cmp, key).total)
    assert d['overall']['max_price'] == cmp.max_price and d['overall']['min_price'] == cmp.min_price
    for row in d['by_carrier']:
        stats = cmp.by_carrier_stats[row['name']]
        assert row['bus_count'] == stats.bus_count
        for key in ('mean_price', 'median_price', 'p90_price'):
            assert row[key] == pytest.approx(getattr(stats, key).total)


def test_an_older_run_answers_its_prices_digest(repriced):  # noqa: F811
    """Hand oracle: every bus pays 50 in every hour of the Gas 50 run; the load is at B3."""
    old = chat_tools.get_results(result_kind='prices', run_id=repriced['older']['run_id'])
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['section'] == 'results_prices' and old['run']['is_current'] is False
    data = old['data']
    assert data['shape'] == 'digest' and data['snapshots'] == HOURS
    assert data['weighted_hours'] == pytest.approx(HOURS)
    overall = data['overall']
    for key in ('mean_price', 'median_price', 'p90_price', 'max_price', 'min_price',
                'load_weighted_mean'):
        assert overall[key] == pytest.approx(50.0, abs=1e-6), key
    buses = {b['name']: b for b in data['buses']}
    assert set(buses) == {'B1', 'B2', 'B3'} and data['bus_count'] == 3
    assert buses['B1']['load_weighted_mean'] is None and buses['B2']['load_weighted_mean'] is None
    assert buses['B3']['load_weighted_mean'] == pytest.approx(50.0, abs=1e-6)
    drivers = chat_tools.get_results(result_kind='price_drivers', run_id=repriced['older']['run_id'])
    assert drivers['status'] == 'ok' and drivers['scope'] == 'historical'
    assert drivers['data']['rows'] == [] and drivers['data']['threshold'] == 2000.0


def test_the_current_run_answers_the_same_digest(repriced):  # noqa: F811
    run_id = repriced['newest']['run_id']
    kept = run_evidence.get_run_evidence(run_id, 'results_prices')['data']
    for kind in ('prices', 'price_drivers'):
        latest = chat_tools.get_results(result_kind=kind, run_id=run_id)
        assert latest['scope'] == 'current'
        assert latest['data'] == pd_.for_kind(kept, kind)
    assert kept['prices']['overall']['load_weighted_mean'] == pytest.approx(60.0, abs=1e-6)


def test_the_plain_read_is_unchanged(repriced):  # noqa: F811
    plain = chat_tools.get_results(result_kind='prices')
    assert {'index', 'columns', 'data', 'data_adjusted'} <= set(plain) and 'shape' not in plain


@pytest.mark.parametrize('extra', [{'source': 'ac_pf'}, {'limit': 5}, {'offset': 3}])
def test_a_run_refuses_what_its_digest_does_not_keep(repriced, extra):  # noqa: F811
    for run in ('older', 'newest'):
        out = chat_tools.get_results(result_kind='prices', run_id=repriced[run]['run_id'], **extra)
        assert out['status'] == 'unavailable' and 'data' not in out
        assert out['unavailable_kind'] == 'historical_scope_mismatch'
        assert 'load_weighted_mean' in out['digest_fields']


def test_a_prices_run_without_its_section_refuses_with_its_id(repriced):  # noqa: F811
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-legacy05', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'summary': {'solver_kind': 'lopf'}})
    out = chat_tools.get_results(result_kind='prices', run_id='fg-legacy05')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-legacy05'
    assert out['unavailable_kind'] == 'historical_results_unavailable'
    assert 'price' in out['message'] and 'data' not in out


def test_the_prices_view_names_its_section():
    assert rv.view('prices').historical_section == 'results_prices'
    assert retained_results.KEPT_KINDS['prices'] == 'results_prices'
    assert retained_results.KEPT_KINDS['price_drivers'] == 'results_prices'
