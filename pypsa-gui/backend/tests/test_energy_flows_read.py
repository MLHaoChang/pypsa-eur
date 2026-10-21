"""LC-04 hand-off from LC-03: the copilot's `get_energy_flows` read of the
Energy flow tab (services/results/energy_flows.py, #423). Values come only from
the module; unserved energy is reported as NOT delivered, never as supply.

Tier A on `oj-grid` solved by real HiGHS through run_simulation, checked
against the hand calculation: the 90 MW load over 24 h is 2160 MWh; Solar
delivers 80 MW × Σ pu, Gas the rest; DC flow is lossless and storage is idle.
"""
import json

import pytest

from services import chat_service, chat_tools
from services.energy_flows_read import MAX_CHARS, shape
from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import HOURS, LOAD_MW, NAME, SOLAR_MW, SOLAR_PU, build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def solved(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm

    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            _solve_once(session)
        chat_tools.set_chat_session(None)
        yield
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_energy_flows_read_matches_the_hand_calculation(solved, kind, model, base_url, auth):
    steps = [lambda r: ('get_energy_flows', {}), lambda r: (None, {})]
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, steps, 'Where did the energy go?')
    flows = results[0]
    assert flows['status'] == 'ok' and flows['unit'] == 'MWh'
    solar = SOLAR_MW * sum(SOLAR_PU)
    by_source = {}
    for row in flows['rows']:
        by_source[row['source']] = by_source.get(row['source'], 0.0) + row['mwh']
    assert flows['rows_total'] == len(flows['rows'])
    assert sum(by_source.values()) == pytest.approx(LOAD_MW * HOURS, abs=1e-3)
    # Sources are the carriers; all of the load is served, Solar 80 MW × Σ pu, Gas the rest.
    assert by_source == {'solar': pytest.approx(solar, abs=1e-3), 'gas': pytest.approx(LOAD_MW * HOURS - solar, abs=1e-3)}
    assert flows['conservation']['status'] == 'ok'
    assert flows['not_delivered']['meaning'].startswith('Demand that was NOT delivered')
    assert flows['ui_reference']['results_tab'] == 'flows'
    assert len(json.dumps(flows)) <= MAX_CHARS
    assert not [d for e, d in frames if e in {'error', 'tool_error'}]


def test_before_a_solve_it_is_unavailable_never_zero(client, install_network):
    install_network(build_oj_grid())
    out = chat_tools.DISPATCHERS['get_energy_flows']()
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'not_solved'
    assert 'rows' not in out


def test_unserved_is_not_delivered_never_a_source():
    payload = {'status': 'ok', 'unit': 'MWh', 'period': None, 'periods': [None],
               'window': {'from': 0, 'to': 23, 'snapshots': 24},
               'rows': [{'source': 'Grid import', 'via': 'Direct', 'destination': 'Ammonia', 'mwh': 100.0}],
               'conservation': {'status': 'ok', 'checks': [], 'generation_mwh': 100.0, 'destinations_mwh': 100.0,
                                'storage_delta_mwh': 0.0, 'stock_mwh': 0.0, 'residual_mwh': 0.0, 'tolerance_mwh': 0.001},
               'unserved_product_mwh': {'Ammonia': 12.5}, 'unserved_mwh': 3.0, 'flags': [], 'rule': 'r',
               'series': {'big': list(range(10_000))}}
    out = shape(payload, limit=10, inputs_changed=False)
    assert [row['source'] for row in out['rows']] == ['Grid import']
    assert out['not_delivered'] == {'product_mwh': {'Ammonia': 12.5}, 'demand_mwh': 3.0,
                                    'meaning': out['not_delivered']['meaning']}
    assert 'supply' in out['not_delivered']['meaning']
    assert 'series' not in out


def test_unproven_flows_carry_no_rows():
    payload = {'status': 'not_established', 'unit': 'MWh', 'period': None, 'periods': [None],
               'window': {'from': 0, 'to': 23, 'snapshots': 24}, 'rows': [],
               'conservation': {'status': 'failed', 'checks': ['energy_closure']},
               'unserved_product_mwh': {}, 'unserved_mwh': None, 'flags': ['bus balance does not close at B1'],
               'rule': 'r'}
    out = shape(payload, limit=10, inputs_changed=False)
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'not_established'
    assert 'rows' not in out and out['flags'] == ['bus balance does not close at B1']
