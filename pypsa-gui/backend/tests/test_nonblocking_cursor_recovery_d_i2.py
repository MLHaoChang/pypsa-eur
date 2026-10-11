"""Issue 11 / D-I2 — versioned progress cursors and gap recovery."""
from __future__ import annotations

import json

import pytest

from services import chat_service


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    event = 'message'
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith('event:'):
            event = line[len('event:'):].strip()
        elif line.startswith('data:'):
            data_lines.append(line[len('data:'):].strip())
        elif line == '':
            if data_lines:
                frames.append((event, json.loads('\n'.join(data_lines))))
            event = 'message'
            data_lines = []
    if data_lines:
        frames.append((event, json.loads('\n'.join(data_lines))))
    return frames


@pytest.fixture
def owned_session(seeded_identity):
    chat_service._reset_sessions_for_tests()
    session = chat_service.get_or_create_session(
        owner_user_id=str(seeded_identity['user_id']),
    )
    yield session
    chat_service._reset_sessions_for_tests()


def test_append_stamps_monotonic_cursor(owned_session):
    owned_session.append_detached_progress({'line': 'a', 'kind': 'INFO'})
    owned_session.append_detached_progress({'line': 'b', 'kind': 'INFO'})
    items = owned_session.drain_detached_progress()
    assert [i['cursor'] for i in items] == [1, 2]
    assert [i['line'] for i in items] == ['a', 'b']
    assert owned_session.progress_cursor() == 2


def test_after_cursor_replays_log_and_trims_unread(owned_session):
    owned_session.append_detached_progress({'line': 'a'})
    owned_session.append_detached_progress({'line': 'b'})
    owned_session.append_detached_progress({'line': 'c'})
    # Client already saw cursor 1; recover 2 and 3 from the log.
    recovered = owned_session.drain_detached_progress(after_cursor=1)
    assert [i['line'] for i in recovered] == ['b', 'c']
    # Unread ring trimmed through the recovered high-water mark.
    unread = owned_session.drain_detached_progress()
    assert unread == []


def test_progress_sse_cursor_query_recovers_gap(
    client, owned_session, monkeypatch,
):
    owned_session.append_detached_progress({'line': 'one', 'kind': 'INFO'})
    owned_session.append_detached_progress({'line': 'two', 'kind': 'INFO'})
    owned_session.append_detached_progress({'line': 'three', 'kind': 'INFO'})
    # Ordinary drain consumes the unread ring (simulates a dropped stream
    # that already delivered cursor=1).
    first = owned_session.drain_detached_progress()
    assert first[0]['cursor'] == 1
    monkeypatch.setattr(
        owned_session, 'detached_monitor_active', lambda *_a, **_k: False,
    )

    with client.stream(
        'GET',
        f'/api/chat/{owned_session.session_id}/progress',
        params={'cursor': 1},
    ) as resp:
        assert resp.status_code == 200
        body = ''.join(resp.iter_text())

    frames = [(ev, p) for ev, p in _parse_sse(body) if ev == 'tool_progress']
    assert [p['line'] for _, p in frames] == ['two', 'three']
    assert [p['cursor'] for _, p in frames] == [2, 3]


def test_progress_sse_rejects_negative_cursor(client, owned_session):
    resp = client.get(
        f'/api/chat/{owned_session.session_id}/progress',
        params={'cursor': -1},
    )
    assert resp.status_code == 422
    assert resp.json()['detail']['error_kind'] == 'invalid_progress_cursor'


def test_duplicate_cursor_request_is_idempotent(
    client, owned_session, monkeypatch,
):
    owned_session.append_detached_progress({'line': 'only'})
    monkeypatch.setattr(
        owned_session, 'detached_monitor_active', lambda *_a, **_k: False,
    )
    # after_cursor equal to latest → empty recovery (no duplicates).
    with client.stream(
        'GET',
        f'/api/chat/{owned_session.session_id}/progress',
        params={'cursor': 1},
    ) as resp:
        body = ''.join(resp.iter_text())
    frames = [(ev, p) for ev, p in _parse_sse(body) if ev == 'tool_progress']
    assert frames == []
