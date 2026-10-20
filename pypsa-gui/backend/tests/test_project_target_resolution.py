"""Exact project labels resolve through ordinary dispatch under native identity."""
import uuid

import pytest

from services import chat_service, chat_tools
from tests.assurance_acceptance.support import approval, parse_sse, text_turn, tool_turn
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_chat_access import peer  # noqa: F401
from tests.test_chat_restrictions import save_rule
from tests.test_chat_stream_session_ownership import fake_provider  # noqa: F401
from tests.test_assistant_capabilities import dispatch, output
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def test_exact_project_name_returns_the_authorized_canonical_id(baseline, client):
    expected = next(p for p in client.get('/api/projects/').json() if p['name'] == 'Tool Baseline')
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    frames = dispatch(session, 'resolve_project_target', {'project_name': 'Tool Baseline'})
    assert any(event == 'tool_result' for event, _ in frames), frames
    result = next(data['result'] for event, data in frames if event == 'tool_result')
    assert result['version'] == 1
    assert result['project_id'] == expected['id'] == baseline[1]
    assert result['project_name'] == 'Tool Baseline'
    assert not any(event in {'project_rebound', 'tool_pending_confirmation', 'ui_action'} for event, _ in frames)


def test_an_id_is_not_silently_accepted_as_an_exact_project_name(baseline, client):
    label = baseline[1]
    assert all(p['name'] != label for p in client.get('/api/projects/').json())
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    frames = dispatch(session, 'resolve_project_target', {'project_name': label})
    assert not any(event == 'tool_result' for event, _ in frames), frames
    assert any(event == 'tool_error' and 'Project not found' in data.get('message', '')
               for event, data in frames), frames
    # The existing native ID input remains an ID; no route compatibility change.
    activated = client.post(f'/api/projects/{baseline[1]}/activate')
    assert activated.status_code == 200, activated.text
    assert activated.json()['activated'] == 'Tool Baseline'


def test_a_real_uuid_shaped_name_resolves_without_rebinding(baseline, client):
    label = str(uuid.uuid4())
    response = client.post(f'/api/projects/{baseline[1]}/scenarios', json={'name': label})
    assert response.status_code == 201, response.text
    named = next(p for p in client.get('/api/projects/').json() if p['name'] == label)
    assert client.post(f'/api/projects/{baseline[1]}/activate').status_code == 200
    before = client.get('/api/network/meta').json()
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    result = output(dispatch(session, 'resolve_project_target', {'project_name': label}))
    assert result['project_id'] == named['id'] and result['project_name'] == label
    assert client.get('/api/network/meta').json() == before
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10


@pytest.mark.parametrize('name', ['Tool', 'tool baseline', ' Tool Baseline', 'Tool Baseline ', '../../settings'])
def test_unknown_partial_and_case_variant_names_never_fall_back_to_context(baseline, client, name):
    before = client.get('/api/network/meta').json()
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    frames = dispatch(session, 'resolve_project_target', {'project_name': name})
    assert not any(event == 'tool_result' for event, _ in frames), frames
    assert any(event == 'tool_error' and 'Project not found' in data.get('message', '')
               for event, data in frames), frames
    assert client.get('/api/network/meta').json() == before


@pytest.mark.parametrize('name', ['', '   ', 'x' * 257, None, True, 1, {}])
def test_invalid_name_inputs_refuse_without_changing_context(baseline, client, name):
    before = client.get('/api/network/meta').json()
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    frames = dispatch(session, 'resolve_project_target', {'project_name': name})
    assert not any(event == 'tool_result' for event, _ in frames), frames
    assert any(event == 'tool_error' for event, _ in frames), frames
    assert client.get('/api/network/meta').json() == before


def test_existing_registry_uuid_name_collision_is_resolved_by_name(baseline, client):
    from services import project_registry

    # Valid persisted namespace state (e.g. an imported registry). The HTTP
    # creation route deliberately refuses to create this collision directly.
    with chat_tools._acting() as (db, user):
        named = project_registry.create_root(db, user, baseline[1])
        expected = str(named.id)
    native = next(p for p in client.get('/api/projects/').json() if p['name'] == baseline[1])
    assert native['id'] == expected and expected != baseline[1]
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    result = output(dispatch(session, 'resolve_project_target', {'project_name': baseline[1]}))
    assert result['project_id'] == expected and result['project_name'] == baseline[1]
    activated = client.post(f'/api/projects/{baseline[1]}/activate')
    assert activated.status_code == 200 and activated.json()['activated'] == 'Tool Baseline'


@pytest.fixture
def per_request_contexts(baseline):
    """Let each HTTP request bind its own session context, as under uvicorn.

    `baseline` binds a request context and turn cell in the test thread, and
    TestClient copies that thread's context into every request, so two
    tenants' requests would otherwise share one cell.
    """
    from services.pypsa_service import PyPSAService
    cell = PyPSAService._turn_cell.set(None)
    binding = PyPSAService.bind_request_context(None)
    try:
        yield
    finally:
        PyPSAService.reset_request_context(binding)
        PyPSAService._turn_cell.reset(cell)


def test_authenticated_tenant_streams_resolve_their_own_same_name(
        baseline, per_request_contexts, client, other_org_client, fake_provider):
    response = other_org_client.post('/api/projects/Tool Baseline', params={'force': True, 'rebind': True})
    assert response.status_code == 200, response.text
    foreign = next(p for p in other_org_client.get('/api/projects/').json() if p['name'] == 'Tool Baseline')
    assert foreign['id'] != baseline[1]
    for browser, expected in [(client, baseline[1]), (other_org_client, foreign['id']), (client, baseline[1])]:
        fake_provider([tool_turn('resolve_project_target', {'project_name': 'Tool Baseline'}), text_turn('Resolved.')])
        session_id = 'tenant-target-' + uuid.uuid4().hex
        try:
            response = browser.post('/api/chat/stream', json={
                'session_id': session_id, 'message': 'Resolve exactly Tool Baseline.'})
            assert response.status_code == 200, response.text
            frames = parse_sse(response.text)
            assert not any(event in {'error', 'tool_error', 'project_rebound'} for event, _ in frames), frames
            result = next(data['result'] for event, data in frames if event == 'tool_result')
            assert result['project_id'] == expected and result['project_name'] == 'Tool Baseline'
            assert any(event == 'turn_done' for event, _ in frames)
            assert browser.get('/api/network/meta').json()['loaded_project'] == 'Tool Baseline'
        finally:
            chat_service.drop_session(session_id)
    response = other_org_client.post(f"/api/projects/{foreign['id']}/rename", json={'new_name': 'Foreign target'})
    assert response.status_code == 200, response.text
    failures = []
    for name in ['Foreign target', 'Never created project']:
        fake_provider([tool_turn('resolve_project_target', {'project_name': name}), text_turn('No target resolved.')])
        session_id = 'missing-target-' + uuid.uuid4().hex
        try:
            response = client.post('/api/chat/stream', json={'session_id': session_id, 'message': f'Resolve {name}.'})
            assert response.status_code == 200, response.text
            frames = parse_sse(response.text)
            assert not any(event in {'tool_result', 'project_rebound'} for event, _ in frames), frames
            data = next(data for event, data in frames if event == 'tool_error')
            failures.append((data['error_kind'], data['message']))
            assert foreign['id'] not in response.text
            assert client.get('/api/network/meta').json()['loaded_project'] == 'Tool Baseline'
        finally:
            chat_service.drop_session(session_id)
    assert failures[0] == failures[1]


def test_unassigned_and_revoked_names_match_missing_then_native_use_rechecks(baseline, client, peer):
    browser, peer_id = peer
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    actor = chat_tools.acting_user_id()

    def as_peer(name):
        try:
            chat_tools.set_acting_user(peer_id)
            return dispatch(session, 'resolve_project_target', {'project_name': name})
        finally:
            chat_tools.set_acting_user(actor)

    def failure(frames):
        assert not any(event == 'tool_result' for event, _ in frames), frames
        data = next(data for event, data in frames if event == 'tool_error')
        return data['error_kind'], data['message']

    missing = failure(as_peer('Never created project'))
    assert failure(as_peer('Tool Baseline')) == missing
    response = client.put(f'/api/projects/{baseline[1]}/members', json={'user_ids': [peer_id]})
    assert response.status_code == 200, response.text
    resolved = output(as_peer('Tool Baseline'))
    assert resolved['project_id'] == baseline[1]
    assert any(p['id'] == baseline[1] for p in browser.get('/api/projects/').json())
    response = client.put(f'/api/projects/{baseline[1]}/members', json={'user_ids': []})
    assert response.status_code == 200, response.text
    assert failure(as_peer('Tool Baseline')) == missing
    assert browser.post(f"/api/projects/{resolved['project_id']}/activate").status_code == 404
    assert client.get('/api/network/meta').json()['loaded_project'] == 'Tool Baseline'


def test_rename_changes_the_label_but_never_the_canonical_id(baseline, client):
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    original = output(dispatch(session, 'resolve_project_target', {'project_name': 'Tool Baseline'}))
    response = client.post(f'/api/projects/{baseline[1]}/rename', json={'new_name': 'Renamed target'})
    assert response.status_code == 200, response.text
    result = output(dispatch(session, 'resolve_project_target', {'project_name': 'Renamed target'}))
    assert result['project_id'] == original['project_id'] == baseline[1]
    assert result['project_name'] == 'Renamed target'
    frames = dispatch(session, 'resolve_project_target', {'project_name': 'Tool Baseline'})
    assert not any(event == 'tool_result' for event, _ in frames), frames
    assert client.get('/api/network/meta').json()['loaded_project'] == 'Renamed target'


def test_personal_read_only_allows_resolution_but_never_grants_a_write(baseline, client):
    save_rule(client, baseline[1], 'read_only')
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    result = output(dispatch(session, 'resolve_project_target', {'project_name': 'Tool Baseline'}))
    assert result['project_id'] == baseline[1]
    frames = dispatch(session, 'update_component', {
        'component_class': 'Generator', 'name': 'G1', 'attrs': {'marginal_cost': 99}})
    assert any(data.get('error_kind') == 'user_restriction' for _, data in frames), frames
    assert not any(event == 'tool_result' for event, _ in frames)
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10


def test_strict_checked_policy_refuses_the_unmapped_project_resolver(checked):
    frames = checked.run([tool_turn('resolve_project_target', {'project_name': 'Tool Baseline'}),
                          text_turn('No checked adapter.')])
    assert any(data.get('error_kind') == 'capability_unsupported' for _, data in frames)
    assert not any(event == 'tool_result' and data.get('tool_name') == 'resolve_project_target'
                   for event, data in frames)
    assert approval(frames)['verdict'] == 'limited'


def test_authenticated_stream_resolves_native_id_without_switching_context(baseline, client, fake_provider):
    before = client.get('/api/network/meta').json()
    provider = fake_provider([tool_turn('resolve_project_target', {'project_name': 'Tool Baseline'}),
                              text_turn('Exact target resolved.')])
    session_id = 'target-http-fixture'
    try:
        response = client.post('/api/chat/stream', json={
            'session_id': session_id, 'message': 'Resolve project Tool Baseline exactly.'})
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        result = next(data['result'] for event, data in frames if event == 'tool_result')
        assert result['project_id'] == baseline[1] and result['project_name'] == 'Tool Baseline'
        assert not any(event in {'error', 'tool_error', 'tool_pending_confirmation', 'project_rebound'}
                       for event, _ in frames), frames
        assert any(event == 'turn_done' for event, _ in frames)
        assert len(provider.requests) == 2
        assert client.get('/api/network/meta').json() == before
    finally:
        chat_service.drop_session(session_id)
