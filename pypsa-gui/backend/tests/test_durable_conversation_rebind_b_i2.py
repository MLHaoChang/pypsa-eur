"""Issue 09 / B-I2 — rebind bumps generation and refreshes bridge credentials."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services import chat_service
from services import durable_conversation as dc
from services import utterance_admission as ua


OWNER = 'owner-aaa'
OTHER = 'owner-bbb'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()

    def authority(project_id=PROJECT, owner_id=OWNER):
        root = tmp_path / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root, project_id, owner_id

    monkeypatch.setattr(dc, '_authority', lambda: authority())
    yield
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


def test_rebind_bumps_generation_and_preserves_parent_facts():
    thread = dc.create_thread()
    tid = thread['thread_id']
    dc.checkpoint_thread(
        tid, generation=1,
        goals=['Limit battery to 7.5 MW'],
        units={'power': 'MW'},
        refs={'current': {'run_id': 'R1'}, 'discussed': {'run_id': 'R0'}},
    )
    out = dc.rebind_scope(tid, generation=1)
    assert out['generation'] == 2
    assert out['goals'] == ['Limit battery to 7.5 MW']
    assert out['units'] == {'power': 'MW'}
    assert out['refs']['current']['run_id'] == 'R1'
    assert out['checkpoint_rev'] >= 2
    assert out['credentials']['token']
    assert out['credentials']['generation'] == 2


def test_rebind_mints_credentials_scoped_to_actor_project_generation():
    tid = dc.create_thread()['thread_id']
    first = dc.rebind_scope(tid, generation=1)
    token1 = first['credentials']['token']
    assert dc.authorize_bridge_credentials(tid, token1)['token'] == token1
    assert first['credentials']['owner_id'] == OWNER
    assert first['credentials']['project_id'] == PROJECT
    assert first['credentials']['thread_id'] == tid

    second = dc.rebind_scope(tid, generation=2)
    token2 = second['credentials']['token']
    assert token2 != token1
    with pytest.raises(HTTPException) as failure:
        dc.authorize_bridge_credentials(tid, token1)
    assert failure.value.status_code == 401
    assert failure.value.detail['error_kind'] in {
        'bridge_credentials_invalid', 'bridge_credentials_stale',
    }
    assert dc.authorize_bridge_credentials(tid, token2)['generation'] == 3


def test_rebind_stale_generation_409():
    tid = dc.create_thread()['thread_id']
    dc.rebind_scope(tid, generation=1)
    with pytest.raises(HTTPException) as failure:
        dc.rebind_scope(tid, generation=1)
    assert failure.value.status_code == 409
    assert failure.value.detail['error_kind'] == 'stale_generation'
    assert failure.value.detail['generation'] == 2


def test_rebind_invalidates_pending_approvals_on_episode():
    tid = dc.create_thread()['thread_id']
    old = chat_service.get_or_create_session('ep-rebind', owner_user_id=OWNER)
    pc = old.issue_confirmation(
        tool_name='apply_project_changes',
        args={'preview_id': 'p1'},
        safety_tier='destructive',
    )
    dc.checkpoint_thread(tid, generation=1, episode_id=old.session_id)
    # Seed admission generation 1 for the episode.
    ua._DEFAULT.admit_utterance(ua.StableUtterance(
        utterance_id='u0', text='hi', generation=1, session_id=old.session_id,
    ))
    out = dc.rebind_scope(tid, generation=1)
    assert out['generation'] == 2
    sess = chat_service.get_session(old.session_id)
    assert sess is not None
    assert pc.token not in sess.pending_confirmations
    stale = ua._DEFAULT.admit_utterance(ua.StableUtterance(
        utterance_id='u1', text='next', generation=1, session_id=old.session_id,
    ))
    assert stale.status == 'stale_generation'


def test_rebind_http_happy_and_wrong_actor_404(client, tmp_path, monkeypatch):
    tid = client.post('/api/durable-conversations').json()['thread_id']
    client.post(
        f'/api/durable-conversations/{tid}/checkpoint',
        json={'generation': 1, 'goals': ['Limit battery to 7.5 MW']},
    )
    ok = client.post(
        f'/api/durable-conversations/{tid}/rebind',
        json={'generation': 1},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()['generation'] == 2
    assert ok.json()['credentials']['token']
    assert ok.json()['goals'] == ['Limit battery to 7.5 MW']

    def other_authority():
        root = tmp_path / PROJECT
        root.mkdir(parents=True, exist_ok=True)
        return root, PROJECT, OTHER

    monkeypatch.setattr(dc, '_authority', other_authority)
    bad = client.post(
        f'/api/durable-conversations/{tid}/rebind',
        json={'generation': 2},
    )
    assert bad.status_code == 404
    assert bad.json()['detail']['error_kind'] == 'thread_not_found'
