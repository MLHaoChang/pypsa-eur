"""OJ-09, load-flow half: "Open project oj-grid-copy and show me its load flow."

LC-01B closed the first half (exact resolve -> activation -> that project's own
values). This is the second: after activation the load flow read and the tab
are about the named project, never the one open before. The copy differs from
oj-grid only in its line ratings (L1 and L2 at 150 MVA), so by the hand
calculation in tests/oj_grid.py its lines carry the same 45 MW at 30 %, while
oj-grid's are at 15 %. Only provider HTTP is mocked.
"""
import pytest

from services import chat_service, chat_tools
from tests.journey_wire import run_journey
from tests.oj_grid import NAME
from tests.test_oj01_latest_loadflow_journey import _solve_once, oj_grid  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

COPY = 'oj-grid-copy'
COPY_UTIL = {'T1': 45.0, 'L1': 30.0, 'L2': 30.0}


@pytest.fixture
def named_copy(oj_grid, client, monkeypatch):
    import harness.confirm as harness_confirm

    base_id = next(p['id'] for p in client.get('/api/projects/').json() if p['name'] == NAME)
    response = client.post(f'/api/projects/{base_id}/scenarios', json={'name': COPY})
    assert response.status_code == 201, response.text
    copy_id = next(p['id'] for p in client.get('/api/projects/').json() if p['name'] == COPY)
    assert client.post(f'/api/projects/{copy_id}/activate').status_code == 200
    for line, buses in (('L1', ('B1', 'B2')), ('L2', ('B2', 'B3'))):
        response = client.put(f'/api/network/lines/{line}', json={
            'name': line, 'bus0': buses[0], 'bus1': buses[1], 's_nom': 150})
        assert response.status_code == 200, response.text
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        _solve_once(session)
    chat_tools.set_chat_session(None)
    assert client.post(f'/api/projects/{COPY}').status_code == 200
    assert client.post(f'/api/projects/{base_id}/activate').status_code == 200
    return copy_id


def _steps():
    return [lambda r: ('resolve_project_target', {'project_name': COPY}),
            lambda r: ('activate_project', {'project_id': r[0]['project_id']}),
            lambda r: ('get_loadflow_summary', {}),
            lambda r: ('ui_open_panel', {'panel_id': 'results', 'results_tab': 'loadflow'}),
            lambda r: (None, {})]


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_the_named_projects_load_flow_is_read_and_shown(named_copy, client, kind, model, base_url, auth):
    def approve(event, data):
        if event == 'tool_pending_confirmation':
            session.record_decision(data['confirmation_token'], 'approve')

    session = chat_service.ChatSession(model=model)
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, _steps(),
                                            f'Open project {COPY} and show me its load flow.',
                                            mode='guided', on_frame=approve, session=session)
    assert calls == ['resolve_project_target', 'activate_project', 'get_loadflow_summary',
                     'ui_open_panel', None]
    assert results[0]['project_id'] == named_copy
    assert any(event == 'project_rebound' for event, _ in frames)
    lf = results[2]
    assert lf['status'] == 'ok' and lf['project_id'] == named_copy
    assert {row['name']: row['util_pct'] for row in lf['top']} == pytest.approx(COPY_UTIL, abs=0.01)
    assert [d['results_tab'] for e, d in frames if e == 'ui_event'] == ['loadflow']
    assert client.get('/api/network/meta').json()['loaded_project'] == COPY
