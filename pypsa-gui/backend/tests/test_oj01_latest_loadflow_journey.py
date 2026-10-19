"""OJ-01 (VS-1): "Show me the results of the latest calculation, the load flow results."

Tier A: the `oj-grid` fixture solved twice by real HiGHS through the ordinary
run_simulation path (so each run publishes its manifest), then a scripted model
over each real provider wire: newest completed run -> its load flow -> open the
Load flow tab. Values are checked against the hand calculation in
tests/oj_grid.py, never against the adapter. Only provider HTTP is mocked.
"""
import json
import re
import time

import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import NAME, ORACLE, build_oj_grid
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

MESSAGE = 'Show me the results of the latest calculation, the load flow results.'


def _solve_once(session):
    """One ordinary run_simulation call (execution tier auto-approved for the
    fixture only), then wait for the real solve and its published manifest."""
    before = chat_tools.list_project_runs(status='completed')['total_count']
    frames = list(chat_service._dispatch_real_tool_call(
        session, {'id': f'fixture-solve-{before}', 'name': 'run_simulation', 'input': {}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        runs = chat_tools.list_project_runs(status='completed')
        if runs['total_count'] == before + 1 and PyPSAService.get_solver_state().get('status') == 'completed':
            return runs['items'][0]
        time.sleep(0.2)
    raise AssertionError(f'solve did not publish: {PyPSAService.get_solver_state().get("status")}')


@pytest.fixture
def oj_grid(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm

    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            older = _solve_once(session)
            latest = _solve_once(session)
        chat_tools.set_chat_session(None)
        yield {'older': older, 'latest': latest}
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _steps(run_of=lambda r: r[0]['items'][0]['run_id']):
    return [lambda r: ('list_project_runs', {'limit': 1, 'status': 'completed'}),
            lambda r: ('get_loadflow_summary', {'run_id': run_of(r)}),
            lambda r: ('ui_open_panel', {'panel_id': 'results', 'results_tab': 'loadflow'}),
            lambda r: (None, {})]


def _numbers(text):
    return {float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', text)}


def _answer(results):
    """A narration built only from the turn's facts, as the prompt demands."""
    run, lf = results[0]['items'][0], results[1]
    top = ', '.join(f"{row['name']} {row['util_pct']} %" for row in lf['top'][:3])
    return (f"Latest run {run['run_id']} ({run['finished_at']}) is the current result. "
            f"Highest loading: {top}. Active-power proxy, not an AC thermal check.")


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_latest_load_flow_is_read_then_shown(oj_grid, client, kind, model, base_url, auth):
    acks = []

    def browser(event, data):
        # The browser's half: the tab rendered the bound source, so it ACKs.
        if event == 'ui_event' and data.get('acknowledgement'):
            ack = data['acknowledgement']
            body = {k: ack[k] for k in ('version', 'action_id', 'turn_id', 'binding_generation', 'target')}
            body['source_binding'] = ack['source_binding']
            response = client.post('/api/chat/ui-ack', json=body)
            acks.append(response.json())

    steps = _steps()
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, steps, MESSAGE,
                                            on_frame=browser, ui_ack=True)
    assert calls == ['list_project_runs', 'get_loadflow_summary', 'ui_open_panel', None]

    # Latest = the newest completed run, and it is the current solve.
    runs, lf, shown = results
    assert runs['items'][0]['run_id'] == oj_grid['latest']['run_id']
    assert lf['status'] == 'ok'
    assert lf['run'] == {'run_id': oj_grid['latest']['run_id'],
                         'finished_at': oj_grid['latest']['finished_at'], 'is_current': True}
    # The hand-calculated oracle, not the adapter.
    assert [row['name'] for row in lf['top']] == ORACLE['ranking']
    for row in lf['top']:
        assert row['util_pct'] == pytest.approx(ORACLE['util_pct'][row['name']], abs=0.01)
        assert row['peak_flow'] == pytest.approx(ORACLE['peak_flow_mw'][row['name']], abs=0.01)
        assert row['rating']['value'] == ORACLE['rating_mva'][row['name']]
        assert row['h_over_limit'] == ORACLE['over_limit_hours']
    assert lf['claims']['full_ac_thermal_compliance'] is False

    # Shown on the same source and window, and the browser proved it.
    events = [d for e, d in frames if e == 'ui_event']
    assert len(events) == 1 and events[0]['results_tab'] == 'loadflow'
    binding = events[0]['acknowledgement']['source_binding']
    assert (binding['source'], binding['window_from'], binding['window_to']) == (
        lf['ui_reference']['source'], lf['ui_reference']['window_from'], lf['ui_reference']['window_to'])
    assert acks == [{'version': 2, 'action_id': events[0]['acknowledgement']['action_id'],
                     'outcome': 'completed', 'verification': 'browser_displayed_data'}]
    assert shown['display'] == {'verification': 'browser_displayed_data', 'say': 'showing'}
    assert frames[-1][0] == 'turn_done'
    assert not [d for e, d in frames if e in {'error', 'tool_error'}]

    # Every number in the narration is a fact of this turn.
    text = _answer(results)
    facts = _numbers(json.dumps(results))
    assert _numbers(text) <= facts, _numbers(text) - facts


def test_without_a_browser_ack_the_tab_is_only_opened(oj_grid):
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _steps(), MESSAGE, ui_ack=True)
    assert results[2]['display'] == {'verification': 'submitted', 'say': 'opened'}


def test_a_client_that_cannot_ack_is_not_waited_for(oj_grid):
    started = time.monotonic()
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _steps(), MESSAGE)
    assert results[2]['display'] == {'verification': 'submitted', 'say': 'opened'}
    assert time.monotonic() - started < 5


def test_an_older_run_never_gets_the_current_values(oj_grid):
    older = oj_grid['older']['run_id']
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _steps(lambda r: older), MESSAGE)
    lf = results[1]
    assert lf['status'] == 'unavailable' and 'top' not in lf
    assert lf['run_id'] == older and lf['run_is_current'] is False


def test_latest_run_after_an_input_edit_is_not_the_current_result(oj_grid, client):
    response = client.put('/api/network/loads/Demand', json={'name': 'Demand', 'bus': 'B3', 'p_set': 70})
    assert response.status_code == 200, response.text
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _steps(), MESSAGE)
    lf = results[1]
    assert lf['status'] == 'unavailable' and 'top' not in lf
    assert lf['run_id'] == oj_grid['latest']['run_id'] and lf['run_is_current'] is False
    assert lf['inputs_changed_since_run'] is True
