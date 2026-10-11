"""Issue 09 / B-I1 — durable thread checkpoint/resume HTTP + stale generation."""
from __future__ import annotations

import pytest

from services import chat_service
from services import durable_conversation as dc


OWNER = 'owner-aaa'
OTHER = 'owner-bbb'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()

    def authority(project_id=PROJECT, owner_id=OWNER):
        root = tmp_path / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root, project_id, owner_id

    monkeypatch.setattr(dc, '_authority', lambda: authority())
    yield
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()


def test_happy_checkpoint_via_http(client):
    created = client.post('/api/durable-conversations')
    assert created.status_code == 200, created.text
    tid = created.json()['thread_id']
    assert created.json()['generation'] == 1

    body = {
        'generation': 1,
        'goals': ['Limit battery to 7.5 MW'],
        'units': {'power': 'MW'},
        'refs': {
            'current': {'run_id': 'R1', 'source': 'dispatch'},
            'discussed': {'run_id': 'R0'},
        },
    }
    resp = client.post(f'/api/durable-conversations/{tid}/checkpoint', json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()['goals'] == ['Limit battery to 7.5 MW']
    assert resp.json()['checkpoint_rev'] >= 1

    got = client.get(f'/api/durable-conversations/{tid}')
    assert got.status_code == 200
    assert got.json()['units'] == {'power': 'MW'}
    assert got.json()['refs']['current']['run_id'] == 'R1'


def test_happy_resume_via_http(client):
    created = client.post('/api/durable-conversations').json()
    tid = created['thread_id']
    client.post(
        f'/api/durable-conversations/{tid}/checkpoint',
        json={'generation': 1, 'goals': ['Limit battery to 7.5 MW']},
    )
    old = chat_service.get_or_create_session('episode-http-old', owner_user_id=OWNER)
    pc = old.issue_confirmation(
        tool_name='apply_project_changes',
        args={'preview_id': 'p1'},
        safety_tier='destructive',
    )
    client.post(
        f'/api/durable-conversations/{tid}/checkpoint',
        json={'generation': 1, 'episode_id': old.session_id},
    )

    resumed = client.post(
        f'/api/durable-conversations/{tid}/resume',
        json={'generation': 1},
    )
    assert resumed.status_code == 200, resumed.text
    data = resumed.json()
    assert data['generation'] == 2
    assert data['episode_id'] != old.session_id
    assert data['goals'] == ['Limit battery to 7.5 MW']
    assert chat_service.get_session(old.session_id) is None
    new = chat_service.get_session(data['episode_id'])
    assert new is not None
    assert pc.token not in new.pending_confirmations


def test_wrong_actor_checkpoint_and_resume_404(client, tmp_path, monkeypatch):
    tid = client.post('/api/durable-conversations').json()['thread_id']

    def other_authority():
        root = tmp_path / PROJECT
        root.mkdir(parents=True, exist_ok=True)
        return root, PROJECT, OTHER

    monkeypatch.setattr(dc, '_authority', other_authority)
    bad_cp = client.post(
        f'/api/durable-conversations/{tid}/checkpoint',
        json={'generation': 1, 'goals': ['hijack']},
    )
    assert bad_cp.status_code == 404
    assert bad_cp.json()['detail']['error_kind'] == 'thread_not_found'
    bad_rs = client.post(
        f'/api/durable-conversations/{tid}/resume',
        json={'generation': 1},
    )
    assert bad_rs.status_code == 404

    monkeypatch.setattr(
        dc, '_authority',
        lambda: (tmp_path / PROJECT, PROJECT, OWNER),
    )
    got = client.get(f'/api/durable-conversations/{tid}')
    assert got.status_code == 200
    assert got.json()['goals'] == []


def test_stale_generation_rejected(client):
    tid = client.post('/api/durable-conversations').json()['thread_id']
    client.post(
        f'/api/durable-conversations/{tid}/checkpoint',
        json={'generation': 1, 'goals': ['Limit battery to 7.5 MW']},
    )
    assert client.post(
        f'/api/durable-conversations/{tid}/resume',
        json={'generation': 1},
    ).status_code == 200

    stale_cp = client.post(
        f'/api/durable-conversations/{tid}/checkpoint',
        json={'generation': 1, 'goals': ['stale']},
    )
    assert stale_cp.status_code == 409
    assert stale_cp.json()['detail']['error_kind'] == 'stale_generation'
    assert stale_cp.json()['detail']['generation'] == 2

    stale_rs = client.post(
        f'/api/durable-conversations/{tid}/resume',
        json={'generation': 1},
    )
    assert stale_rs.status_code == 409
    assert stale_rs.json()['detail']['error_kind'] == 'stale_generation'

    got = client.get(f'/api/durable-conversations/{tid}')
    assert got.json()['generation'] == 2
    assert got.json()['goals'] == ['Limit battery to 7.5 MW']
