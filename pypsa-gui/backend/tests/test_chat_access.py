"""Read-only current policy through authenticated HTTP; no provider needed."""
import uuid
from datetime import UTC, datetime

import pytest


@pytest.fixture
def peer(_auth_db, seeded_identity):
    from db.models import OrgMembership, User
    from fastapi.testclient import TestClient
    import main
    from tests.conftest import attach_session

    _, session_local = _auth_db
    uid = uuid.uuid4()
    with session_local() as db:
        db.add(User(id=uid, email=f'access-{uid.hex}@example.com', password_hash='unused',
                    status='active', is_super_admin=False, created_at=datetime.now(UTC)))
        db.flush()
        db.add(OrgMembership(user_id=uid, org_id=seeded_identity['org_id'], role='member'))
        db.commit()
    with TestClient(main.app) as browser:
        yield attach_session(browser, session_local, uid), str(uid)


def test_access_requires_authentication(anon_client):
    assert anon_client.get('/api/chat/access').status_code == 401


def test_access_reports_actual_confirmation_floors(client, monkeypatch):
    from harness import confirm
    monkeypatch.setattr(confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
    response = client.get('/api/chat/access', params={'ui_mode': 'expert'})
    assert response.status_code == 200, response.text
    assert response.headers['Cache-Control'] == 'no-store'
    data = response.json()
    assert data['version'] == 1 and data['read_only'] is True
    rules = {row['tier']: row for row in data['confirmation_rules']}
    assert rules['read'] == {'tier': 'read', 'native': 'none', 'external': 'none'}
    assert rules['write'] == {'tier': 'write', 'native': 'none', 'external': 'user'}
    assert rules['execution'] == {'tier': 'execution', 'native': 'policy', 'external': 'user'}
    assert rules['destructive']['native'] == 'user'
    guided = client.get('/api/chat/access', params={'ui_mode': 'guided'}).json()
    assert next(r for r in guided['confirmation_rules'] if r['tier'] == 'write')['native'] == 'user'
    assert data['limits']['output_tokens_per_turn'] == 8192
    assert data['limits']['tool_calls_per_turn'] == 25
    assert data['session'] is None


def test_access_is_bound_to_authorized_current_project(client, other_org_client, session_ctx):
    assert client.post('/api/projects/Access', params={'force': True, 'rebind': True}).status_code == 200
    ctx = session_ctx(client)
    original_id = ctx.project_uuid
    response = client.get('/api/chat/access', params={'project_id': ctx.project_uuid})
    assert response.status_code == 200, response.text
    assert response.json()['scope'] == {'project_id': ctx.project_uuid, 'project_name': 'Access'}
    assert other_org_client.get('/api/chat/access', params={'project_id': ctx.project_uuid}).status_code == 404
    assert client.post('/api/projects/Other', params={'force': True, 'rebind': True}).status_code == 200
    assert client.get('/api/chat/access', params={'project_id': original_id}).status_code == 409


def test_fresh_lock_and_revocation_cannot_be_replaced_by_an_old_snapshot(client, peer, session_ctx):
    browser, peer_id = peer
    assert client.post('/api/projects/Access', params={'force': True, 'rebind': True}).status_code == 200
    pid = session_ctx(client).project_uuid
    assert client.put('/api/projects/Access/members', json={'user_ids': [peer_id]}).status_code == 200
    assert browser.post(f'/api/projects/{pid}/activate').status_code == 200
    assert client.delete(f'/api/projects/{pid}/lock').status_code == 200
    assert browser.post(f'/api/projects/{pid}/lock').status_code == 200
    observed = client.get('/api/chat/access', params={'project_id': pid})
    assert observed.status_code == 200, observed.text
    assert observed.json()['project_lock'] == 'other'
    assert 'project_writes_locked' in observed.json()['blockers']
    assert peer_id not in observed.text and '@example.com' not in observed.text
    assert client.put('/api/projects/Access/members', json={'user_ids': []}).status_code == 409
    assert browser.delete(f'/api/projects/{pid}/lock').status_code == 200
    assert client.get('/api/chat/access').json()['project_lock'] == 'unlocked'
    assert client.put('/api/projects/Access/members', json={'user_ids': []}).status_code == 200
    assert browser.get('/api/chat/access', params={'project_id': pid}).status_code == 404


def test_owned_session_read_never_consumes_a_decision_or_creates_a_missing_session(client, other_org_client, seeded_identity):
    from harness import session as sessions

    sid = 'access-' + uuid.uuid4().hex
    assert client.get('/api/chat/access', params={'session_id': sid}).status_code == 404
    assert sessions.get_session(sid) is None
    sess = sessions.get_or_create_session(sid, owner_user_id=str(seeded_identity['user_id']))
    pending = sess.issue_confirmation(tool_name='delete_project', args={'name': 'private-marker'}, safety_tier='destructive')
    try:
        response = client.get('/api/chat/access', params={'session_id': sid})
        assert response.status_code == 200, response.text
        assert response.json()['session']['output_tokens_used'] is None
        assert pending.token not in response.text and 'private-marker' not in response.text
        assert pending.token in sess.pending_confirmations and not sess.confirmation_decisions
        foreign = other_org_client.get('/api/chat/access', params={'session_id': sid})
        missing = other_org_client.get('/api/chat/access', params={'session_id': 'not-present'})
        assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
        assert pending.token in sess.pending_confirmations
    finally:
        sessions.drop_session(sid)


@pytest.mark.parametrize(('mode', 'requested', 'effective'), [
    ('disabled', False, False), ('disabled', True, False), ('shadow', True, False),
    ('pilot', False, False), ('pilot', True, True), ('strict', False, True), ('strict', True, True),
])
def test_check_selection_reports_server_floor_without_changing_it(client, monkeypatch, mode, requested, effective):
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', mode)
    monkeypatch.setenv('OPENAI_API_KEY', 'private-secret-marker')
    response = client.get('/api/chat/access', params={'checked': requested, 'auto_approve': 'all'})
    assert response.status_code == 200, response.text
    assert response.json()['checking']['effective_for_selection'] is effective
    assert ('requested_checks_unavailable' in response.json()['blockers']) == (requested and mode not in {'pilot', 'strict'})
    assert next(r for r in response.json()['confirmation_rules'] if r['tier'] == 'destructive')['external'] == 'user'
    assert 'private-secret-marker' not in response.text


def test_owned_session_limits_refresh_without_a_provider(client, seeded_identity):
    from harness import budget, session as sessions

    sid = 'access-limits-' + uuid.uuid4().hex
    sess = sessions.get_or_create_session(sid, owner_user_id=str(seeded_identity['user_id']))
    try:
        sess.turns_started = budget.MAX_TURNS_PER_SESSION
        first = client.get('/api/chat/access', params={'session_id': sid}).json()
        assert 'session_unreported_usage_turn_limit' in first['blockers']
        sess.usage_reported = True
        sess.usage_acc['output_tokens'] = budget.MAX_OUTPUT_TOKENS_PER_SESSION
        second = client.get('/api/chat/access', params={'session_id': sid}).json()
        assert second['session']['output_tokens_used'] == 200000
        assert 'session_output_budget_exhausted' in second['blockers']
        assert 'session_unreported_usage_turn_limit' not in second['blockers']
    finally:
        sessions.drop_session(sid)


def test_invalid_server_policy_is_visible_even_without_check_opt_in(client, monkeypatch):
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'invalid-mode')
    response = client.get('/api/chat/access')
    assert response.status_code == 200, response.text
    assert response.json()['checking']['server_mode'] == 'unavailable'
    assert 'chat_policy_unavailable' in response.json()['blockers']


def test_busy_chat_state_refuses_access_read_without_waiting_for_the_turn(client, seeded_identity):
    from concurrent.futures import ThreadPoolExecutor
    from harness import session as sessions

    sid = 'access-busy-' + uuid.uuid4().hex
    sess = sessions.get_or_create_session(sid, owner_user_id=str(seeded_identity['user_id']))
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            sess._lock.acquire()
            response = worker.submit(client.get, '/api/chat/access', params={'session_id': sid})
            try:
                assert response.result(timeout=2).status_code == 409
            finally:
                sess._lock.release()
        assert client.get('/api/chat/access', params={'session_id': sid}).status_code == 200
    finally:
        sessions.drop_session(sid)


@pytest.mark.parametrize('params', [{'ui_mode': 'admin'}, {'checked': 'approve-all'}, {'session_id': ''}, {'project_id': 'x' * 121}])
def test_malformed_selectors_cannot_grant_access(client, params):
    assert client.get('/api/chat/access', params=params).status_code == 422
