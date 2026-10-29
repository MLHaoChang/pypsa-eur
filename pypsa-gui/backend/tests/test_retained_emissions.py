"""LC-03: the Emissions family is kept with its run (retained_results).

The tab's one read (get_results emissions) is kept as the route serves it, in
section `results_emissions`, like capacity.

oj-grid with gas at 0.2 tCO2/MWh of primary energy is solved, then Gas's
efficiency is halved and it is solved again. Efficiency enters only the
emissions (tCO2 = sum_t gas_t x 0.2 / efficiency; no CO2 constraint binds),
so both runs dispatch gas_t = 90 - 80 pu_t (tests/oj_grid.py) and the older
run emits half of the newer.
"""
import pytest

from services import chat_service, chat_tools, result_views as rv, retained_results, run_evidence
from services.pypsa_service import PyPSAService
from tests.oj_grid import LOAD_MW, NAME, SOLAR_MW, SOLAR_PU, build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_tools import helpers_state  # noqa: F401

GAS_MWH = sum(LOAD_MW - SOLAR_MW * pu for pu in SOLAR_PU)
CO2 = 0.2


@pytest.fixture
def emitting(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm

    n = build_oj_grid()
    n.add('Carrier', 'gas', co2_emissions=CO2)
    n.add('Carrier', 'solar', co2_emissions=0.0)
    install_network(n)
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            older = _solve_once(session)
            response = client.put('/api/network/generators/Gas',
                                  json={'name': 'Gas', 'bus': 'B1', 'efficiency': 0.5})
            assert response.status_code == 200, response.text
            newest = _solve_once(session)
        chat_tools.set_chat_session(None)
        yield {'older': older, 'newest': newest}
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def test_an_older_run_answers_its_own_emissions(emitting):
    current = chat_tools.get_results(result_kind='emissions')
    assert current['total_tCO2'] == pytest.approx(CO2 * GAS_MWH / 0.5, rel=1e-6)
    old = chat_tools.get_results(result_kind='emissions', run_id=emitting['older']['run_id'])
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['section'] == 'results_emissions' and old['run']['is_current'] is False
    assert old['data']['total_tCO2'] == pytest.approx(CO2 * GAS_MWH, rel=1e-6)


def test_the_current_run_answers_the_current_read(emitting):
    run_id = emitting['newest']['run_id']
    latest = chat_tools.get_results(result_kind='emissions', run_id=run_id)
    assert latest['scope'] == 'current'
    assert latest['data']['total_tCO2'] == pytest.approx(CO2 * GAS_MWH / 0.5, rel=1e-6)
    kept = run_evidence.get_run_evidence(run_id, 'results_emissions')['data']['emissions']
    assert kept['total_tCO2'] == pytest.approx(latest['data']['total_tCO2'])


def test_an_emissions_run_without_its_section_refuses_with_its_id(emitting):
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-legacy06', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'summary': {'solver_kind': 'lopf'}})
    out = chat_tools.get_results(result_kind='emissions', run_id='fg-legacy06')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-legacy06'
    assert out['unavailable_kind'] == 'historical_results_unavailable'
    assert 'emissions' in out['message'] and 'data' not in out


def test_the_emissions_view_names_its_section():
    assert rv.view('emissions').historical_section == 'results_emissions'
    assert retained_results.KEPT_KINDS['emissions'] == 'results_emissions'


@pytest.fixture
def capped(client, session_ctx, install_network, monkeypatch):
    """oj-grid under a slack CO2 cap, solved once: the cap's dual is a real 0."""
    import harness.confirm as harness_confirm

    n = build_oj_grid()
    n.add('Carrier', 'gas', co2_emissions=CO2)
    n.add('Carrier', 'solar', co2_emissions=0.0)
    n.add('GlobalConstraint', 'CO2Limit', type='primary_energy',
          carrier_attribute='co2_emissions', sense='<=', constant=1e9)
    install_network(n)
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            run = _solve_once(session)
        chat_tools.set_chat_session(None)
        yield run
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def test_a_kept_run_keeps_a_real_zero_shadow_price(capped):
    kept = run_evidence.get_run_evidence(capped['run_id'], 'results_emissions')['data']['emissions']
    assert [c['shadow_price_eur_per_tCO2'] for c in kept['caps']] == [0.0]
    assert kept['cap']['shadow_price_eur_per_tCO2'] == 0.0


def test_a_null_shadow_price_stays_null_in_a_kept_run(capped):
    """A1.6: an unknown dual is kept as null, and the kept run reads back the same."""
    PyPSAService.get_network().global_constraints.at['CO2Limit', 'mu'] = float('nan')
    section = retained_results.adapter('results_emissions').capture({})
    assert section is not None, 'results stopped being current'
    emissions = section['emissions']
    assert [c['shadow_price_eur_per_tCO2'] for c in emissions['caps']] == [None]
    assert emissions['cap']['shadow_price_eur_per_tCO2'] is None
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-nullmu01', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'summary': {'solver_kind': 'lopf'},
                                       'results_emissions': section})
    old = chat_tools.get_results(result_kind='emissions', run_id='fg-nullmu01')
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['data'] == emissions
