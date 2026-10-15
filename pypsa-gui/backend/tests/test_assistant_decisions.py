"""Native HTTP questions/confirmations survive as inspection, never authority."""
import json
import concurrent.futures
import time
import uuid

import pytest

from harness import session as sessions
from harness.providers.fake import FakeProvider
from services import chat_service


@pytest.fixture
def origin(client, seeded_identity, session_ctx, tmp_path, monkeypatch):
    monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(tmp_path / 'app'))
    assert client.post('/api/projects/Decisions', params={'force': True, 'rebind': True}).status_code == 200
    sid = 'decision-http-' + uuid.uuid4().hex
    yield session_ctx(client), str(seeded_identity['user_id']), sid, tmp_path / 'app'
    sessions.drop_session(sid)


def install(monkeypatch, turns):
    provider = FakeProvider(turns)
    monkeypatch.setattr(chat_service, '_provider_for_profile', lambda *args, **kwargs: (provider, None))
    return provider


def ask(monkeypatch, client, sid):
    install(monkeypatch, [
        {'blocks': [{'type': 'tool_use', 'id': 'native-question', 'name': 'ask_user', 'input': {
            'title': 'Storage basis', 'question': 'Which input should the next draft use?',
            'options': [{'label': 'Battery'}, {'label': 'Thermal storage'}],
        }}]}, {'blocks': [{'type': 'text', 'text': 'Please choose.'}]},
    ])
    response = client.post('/api/chat/stream', json={'session_id': sid, 'message': 'Ask about storage.'})
    assert response.status_code == 200, response.text
    assert 'event: choice_request' in response.text, response.text
    return response


def inspect(client, row, **changes):
    params = dict(project_id=row['project_id'], revision=row['decision_revision'], digest=row['decision_digest'])
    params.update(changes)
    return client.get('/api/chat/decisions/' + row['decision_id'], params=params)


def test_question_survives_session_loss_and_inspects_exact_origin(client, origin, monkeypatch):
    ctx, _owner, sid, app = origin
    ask(monkeypatch, client, sid)
    first = client.get('/api/chat/activity', params={'filter': 'needs_you'})
    assert first.status_code == 200, first.text
    row = next(r for r in first.json()['items'] if r['family'] == 'question')
    assert row['session_id'] == sid and row['tool_use_id'] == 'native-question'
    assert row['status'] == 'waiting_for_input'
    sessions.drop_session(sid)
    from services import assistant_activity
    assistant_activity._SCOPES.clear()
    recovered = client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()
    row = next(r for r in recovered['items'] if r['family'] == 'question')
    detail = inspect(client, row)
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert data['session_id'] == sid and data['project_id'] == str(ctx.project_uuid)
    assert data['question']['question'] == 'Which input should the next draft use?'
    assert [o['label'] for o in data['question']['options']] == ['Battery', 'Thermal storage']
    assert data['inspection_only'] is True
    assert sessions.get_session(sid) is None
    assert data['evidence_available'] is False
    assert list(app.rglob('decisions.json'))


def test_later_native_turn_records_follow_up_and_refuses_stale_inspection(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    ask(monkeypatch, client, sid)
    row = next(r for r in client.get('/api/chat/activity').json()['items'] if r['family'] == 'question')
    install(monkeypatch, [{'blocks': [{'type': 'text', 'text': 'Acknowledged.'}]}])
    follow = client.post('/api/chat/stream', json={'session_id': sid, 'message': 'Battery; private-user-follow-up'})
    assert follow.status_code == 200, follow.text
    assert inspect(client, row).status_code == 409
    assert not any(r['family'] == 'question' for r in client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items'])
    latest = next(r for r in client.get('/api/chat/activity', params={'filter': 'completed'}).json()['items'] if r['family'] == 'question')
    detail = inspect(client, latest)
    assert detail.status_code == 200, detail.text
    assert detail.json()['native_status'] == 'followed_up'
    assert 'does not establish' in detail.json()['reason']
    assert 'private-user-follow-up' not in json.dumps(detail.json())


def test_decision_inspection_rechecks_acl_and_never_changes_active_project(client, other_org_client, origin, monkeypatch):
    ctx, _owner, sid, _app = origin
    ask(monkeypatch, client, sid)
    row = next(r for r in client.get('/api/chat/activity').json()['items'] if r['family'] == 'question')
    assert inspect(other_org_client, row).status_code == 404
    assert client.post('/api/projects/Elsewhere', params={'force': True, 'rebind': True}).status_code == 200
    assert client.get('/api/chat/activity').json()['items'] == []
    assert inspect(client, row).status_code == 200
    assert client.get('/api/chat/activity').json()['items'] == []
    wrong = dict(row, project_id=str(ctx.project_uuid))
    assert inspect(client, wrong, digest='0' * 64).status_code in (404, 409)


@pytest.mark.parametrize('decision,outcome', [('approve', 'approved'), ('deny', 'rejected'), ('approve', 'unavailable')])
def test_real_http_confirmation_is_inspection_only_and_native_decision_is_single_use(
    client, other_org_client, origin, monkeypatch, decision, outcome,
):
    ctx, _owner, sid, app = origin
    from services import chat_tools
    if outcome == 'unavailable':
        from harness import approvals
        monkeypatch.setattr(approvals, 'decided', lambda *args, **kwargs: False)
    calls = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'update_solver_config', lambda **kw: calls.append(kw) or {'status': 'saved'})
    install(monkeypatch, [
        {'blocks': [{'type': 'tool_use', 'id': 'native-confirm', 'name': 'update_solver_config',
                     'input': {'partial': {'voll': 4321}}}]},
        {'blocks': [{'type': 'text', 'text': 'Turn finished.'}]},
    ])
    token = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat/stream', json={
            'session_id': sid, 'message': 'Update configuration.', 'ui_context': {'ui_mode': 'guided'},
        })
        try:
            deadline = time.monotonic() + 8
            row = None
            while time.monotonic() < deadline:
                live = sessions.get_session(sid)
                if live:
                    with live._lock:
                        token = next(iter(live.pending_confirmations), None)
                rows = client.get('/api/chat/activity').json()['items']
                row = next((r for r in rows if r.get('decision_id') and r['family'] == 'confirmation'), None)
                if token and row:
                    break
                if future.done():
                    pytest.fail(future.result().text)
                time.sleep(0.01)
            assert token and row
            assert row['session_id'] == sid and row['project_id'] == str(ctx.project_uuid)
            detail = inspect(client, row)
            assert detail.status_code == 200, detail.text
            assert detail.json()['live_approval_available'] is True
            assert token not in detail.text and '"voll"' not in detail.text
            assert calls == [] and token in sessions.get_session(sid).pending_confirmations
            assert client.post(f'/api/chat/{sid}/confirm', json={'token': row['decision_id'], 'decision': 'approve'}).status_code == 404
            assert token in sessions.get_session(sid).pending_confirmations
            assert other_org_client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': 'approve'}).status_code == 404
            assert client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': decision}).status_code == 200
            assert client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': decision}).status_code == 404
            future.result(timeout=12)
        finally:
            if sessions.get_session(sid):
                sessions.get_session(sid).abort_event.set()
            response = future.result(timeout=12)
        assert response.status_code == 200
    assert len(calls) == (1 if outcome == 'approved' else 0)
    assert inspect(client, row).status_code == 409
    sessions.drop_session(sid)
    final = next(r for r in client.get('/api/chat/activity', params={'filter': 'needs_you' if outcome == 'unavailable' else 'completed'}).json()['items']
                 if r['family'] == 'confirmation')
    assert final['status'] == ('needs_review' if outcome == 'unavailable' else 'decision_recorded')
    assert final['native_status'] == outcome
    inspected = inspect(client, final)
    assert inspected.status_code == 200
    assert inspected.json()['live_approval_available'] is False
    assert inspected.json()['evidence_available'] is False
    stored = list(app.rglob('decisions.json'))[0].read_text()
    assert token not in stored and '"voll"' not in stored and '"partial"' not in stored


def test_lost_pending_confirmation_cannot_be_revived_by_inspection(client, origin):
    ctx, owner, sid, _ = origin
    from harness import attention
    from services.pypsa_service import PyPSAService
    native = sessions.get_or_create_session(sid, owner_user_id=owner)
    binding = PyPSAService._request_ctx.set(ctx)
    try:
        pc = native.issue_confirmation(tool_name='delete_component', args={'name': 'private'}, safety_tier='destructive')
        attention.confirmation(native, pc, 'lost-call')
    finally:
        PyPSAService._request_ctx.reset(binding)
    before = client.get('/api/chat/activity').json()['items'][0]
    sessions.drop_session(sid)
    assert inspect(client, before).status_code == 409  # liveness is part of inspected view
    after = client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items'][0]
    assert after['status'] == 'needs_review' and after['native_status'] == 'waiting_for_approval'
    assert inspect(client, after).json()['live_approval_available'] is False
    assert sessions.get_session(sid) is None
    assert client.post(f'/api/chat/{sid}/confirm', json={'token': pc.token, 'decision': 'approve'}).status_code == 404


@pytest.mark.parametrize('outcome', ['expired', 'aborted', 'revoked'])
def test_native_gate_stop_outcomes_are_observations_never_work_completion(client, origin, outcome):
    ctx, owner, sid, _app = origin
    from harness.confirm import _confirm_destructive_tool
    from services.pypsa_service import PyPSAService
    native = sessions.get_or_create_session(sid, owner_user_id=owner)
    binding = PyPSAService._request_ctx.set(ctx)
    try:
        collector = []
        gate = _confirm_destructive_tool(native, tool_use_id='stopped-native', tool_name='delete_component',
            args={'name': 'private'}, tier='destructive', tool_results_collector=collector)
        event, card = next(gate)
        assert event == 'tool_pending_confirmation'
        if outcome == 'expired':
            native.pending_confirmations[card['confirmation_token']].expires_at = time.monotonic() - 1
        elif outcome == 'aborted':
            native.abort_event.set()
        else:
            native.revoke_confirmations()
        assert list(gate)[0][0] == 'tool_error'
        assert collector[0]['is_error'] is True
    finally:
        PyPSAService._request_ctx.reset(binding)
    row = next(r for r in client.get('/api/chat/activity', params={'filter': 'completed'}).json()['items'] if r['family'] == 'confirmation')
    data = inspect(client, row).json()
    assert data['native_status'] == outcome and data['evidence_available'] is False
    assert data['live_approval_available'] is False
    assert 'does not establish' in data['reason']
