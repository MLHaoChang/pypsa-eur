"""Issue 09 / B-I3 — one authoritative replica owns durable + chat controls."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services import chat_service
from services import durable_conversation as dc
from services import utterance_admission as ua


OWNER = 'owner-aaa'
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


def test_create_stamps_owner_replica(monkeypatch):
    import security

    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    thread = dc.create_thread()
    assert thread['owner_replica_id'] == 'replica-alpha'


def test_wrong_replica_refuses_checkpoint_resume_rebind(monkeypatch):
    import security

    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    tid = dc.create_thread()['thread_id']
    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-beta')

    with pytest.raises(HTTPException) as failure:
        dc.checkpoint_thread(tid, generation=1, goals=['x'])
    assert failure.value.status_code == 409
    assert failure.value.detail['error_kind'] == 'wrong_replica'
    assert failure.value.detail['owner_replica_id'] == 'replica-alpha'

    with pytest.raises(HTTPException) as failure2:
        dc.resume_episode(tid, generation=1)
    assert failure2.value.detail['error_kind'] == 'wrong_replica'

    with pytest.raises(HTTPException) as failure3:
        dc.rebind_scope(tid, generation=1)
    assert failure3.value.detail['error_kind'] == 'wrong_replica'


def test_same_replica_allows_controls(monkeypatch):
    import security

    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    tid = dc.create_thread()['thread_id']
    out = dc.checkpoint_thread(tid, generation=1, goals=['Limit battery'])
    assert out['goals'] == ['Limit battery']
    out = dc.rebind_scope(tid, generation=1)
    assert out['generation'] == 2
    assert out['owner_replica_id'] == 'replica-alpha'


def test_chat_session_pins_confirm_abort_to_creating_replica(
    client, seeded_identity, monkeypatch,
):
    import security

    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    session = chat_service.get_or_create_session(
        'pin-sess',
        owner_user_id=str(seeded_identity['user_id']),
    )
    assert session.owner_replica_id == 'replica-alpha'
    pc = session.issue_confirmation(
        tool_name='delete_component',
        args={'component_class': 'Bus', 'name': 'B1'},
        safety_tier='destructive',
    )

    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-beta')
    abort = client.post(f'/api/chat/{session.session_id}/abort')
    assert abort.status_code == 200
    assert abort.json() == {
        'ok': False,
        'reason': 'wrong_replica',
        'owner_replica_id': 'replica-alpha',
    }
    assert not session.abort_event.is_set()

    confirm = client.post(
        f'/api/chat/{session.session_id}/confirm',
        json={'token': pc.token, 'decision': 'deny'},
    )
    assert confirm.status_code == 409
    assert confirm.json()['detail']['error_kind'] == 'wrong_replica'
    assert pc.token in session.pending_confirmations


def test_http_rebind_wrong_replica_409(client, monkeypatch):
    import security

    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    created = client.post('/api/durable-conversations')
    assert created.status_code == 200
    tid = created.json()['thread_id']
    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-beta')
    resp = client.post(
        f'/api/durable-conversations/{tid}/rebind',
        json={'generation': 1},
    )
    assert resp.status_code == 409
    assert resp.json()['detail']['error_kind'] == 'wrong_replica'
