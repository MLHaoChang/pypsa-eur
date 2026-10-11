"""Issue 11 — GET /api/chat/{session_id}/progress drains detached ring as tool_progress."""
from __future__ import annotations

import json
import time

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


def test_progress_sse_emits_tool_progress_then_idle(
    client, owned_session, monkeypatch,
):
    owned_session.append_detached_progress({
        'tool_use_id': 'tu-sim',
        'tool_name': 'run_simulation',
        'kind': 'PHASE',
        'line': 'Optimising',
    })
    owned_session.append_detached_progress({
        'tool_use_id': 'tu-sim',
        'tool_name': 'run_simulation',
        'kind': 'VALIDATION',
        'line': 'ok',
    })
    # Keep the monitor inactive so the observer exits after one drain.
    monkeypatch.setattr(
        owned_session, 'detached_monitor_active', lambda *_a, **_k: False,
    )

    with client.stream(
        'GET', f'/api/chat/{owned_session.session_id}/progress',
    ) as resp:
        assert resp.status_code == 200
        assert 'text/event-stream' in resp.headers['content-type']
        body = ''.join(resp.iter_text())

    frames = _parse_sse(body)
    progress = [(ev, p) for ev, p in frames if ev == 'tool_progress']
    assert len(progress) == 2
    assert progress[0][1]['kind'] == 'PHASE'
    assert progress[1][1]['kind'] == 'VALIDATION'
    assert all(p['tool_use_id'] == 'tu-sim' for _, p in progress)
    # Ring drained — a second poll finds nothing.
    assert owned_session.drain_detached_progress() == []


def test_progress_sse_unknown_or_foreign_session_404(client, owned_session):
    owned_session.owner_user_id = 'someone-else'
    resp = client.get(f'/api/chat/{owned_session.session_id}/progress')
    assert resp.status_code == 404
    assert resp.json()['detail']['error_kind'] == 'unknown_session'

    resp2 = client.get('/api/chat/does-not-exist/progress')
    assert resp2.status_code == 404
    assert resp2.json()['detail']['error_kind'] == 'unknown_session'


def test_progress_sse_stays_open_while_monitor_active(
    client, owned_session, monkeypatch,
):
    state = {'active': True, 'n': 0}

    def _active(_tid=None):
        return state['active']

    def _drain():
        state['n'] += 1
        if state['n'] == 1:
            return [{
                'tool_use_id': 'tu-sim', 'tool_name': 'run_simulation',
                'kind': 'PHASE', 'line': 'still going',
            }]
        state['active'] = False
        return []

    monkeypatch.setattr(owned_session, 'detached_monitor_active', _active)
    monkeypatch.setattr(owned_session, 'drain_detached_progress', _drain)
    monkeypatch.setattr(
        'routers.chat.PROGRESS_POLL_SECONDS', 0.01, raising=False,
    )

    t0 = time.monotonic()
    with client.stream(
        'GET', f'/api/chat/{owned_session.session_id}/progress',
    ) as resp:
        assert resp.status_code == 200
        body = ''.join(resp.iter_text())
    assert time.monotonic() - t0 >= 0.01
    frames = _parse_sse(body)
    assert any(ev == 'tool_progress' for ev, _ in frames)
