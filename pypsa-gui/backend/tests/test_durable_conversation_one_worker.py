"""Issue 09 PR3 — one authoritative app worker for durable threads.

GK B condition (g): each worker-count source is tested (desktop launcher
declaration, uvicorn/gunicorn ``--workers``, ``WEB_CONCURRENCY``), and an
undetectable count is one only because one worker is uvicorn's documented
default when neither the flag nor the variable is given.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services import app_workers
from services import chat_service
from services import durable_conversation as dc


OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    app_workers._reset_for_tests()
    monkeypatch.delenv('WEB_CONCURRENCY', raising=False)
    monkeypatch.setattr(app_workers.sys, 'argv', ['uvicorn', 'main:app'])
    root = tmp_path / PROJECT
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dc, '_authority', lambda: (root, PROJECT, OWNER))
    yield
    app_workers._reset_for_tests()
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()


@pytest.mark.parametrize('argv, expected', [
    (['uvicorn', 'main:app'], (1, 'default')),
    (['uvicorn', 'main:app', '--workers', '4'], (4, 'argv')),
    (['uvicorn', 'main:app', '--workers=3'], (3, 'argv')),
    (['/usr/bin/gunicorn', '-w', '2', 'main:app'], (2, 'argv')),
    (['uvicorn', 'main:app', '--workers', '1'], (1, 'argv')),
    (['python', 'script.py', '-w', '5'], (1, 'default')),
])
def test_argv_source(monkeypatch, argv, expected):
    monkeypatch.setattr(app_workers.sys, 'argv', argv)
    assert app_workers.detected() == expected


def test_web_concurrency_source(monkeypatch):
    monkeypatch.setenv('WEB_CONCURRENCY', '3')
    assert app_workers.detected() == (3, 'WEB_CONCURRENCY')


def test_flag_beats_environment(monkeypatch):
    monkeypatch.setenv('WEB_CONCURRENCY', '3')
    monkeypatch.setattr(app_workers.sys, 'argv', ['uvicorn', 'main:app', '--workers', '1'])
    assert app_workers.detected() == (1, 'argv')


def test_unreadable_values_fall_through(monkeypatch):
    monkeypatch.setenv('WEB_CONCURRENCY', 'many')
    monkeypatch.setattr(app_workers.sys, 'argv', ['uvicorn', 'main:app', '--workers', 'x'])
    assert app_workers.detected() == (1, 'default')


def test_desktop_declaration_wins(monkeypatch):
    monkeypatch.setenv('WEB_CONCURRENCY', '8')
    app_workers.declare(1, source='desktop')
    assert app_workers.detected() == (1, 'desktop')


def test_desktop_launcher_declares_one_worker(monkeypatch):
    import socket

    from desktop import launcher

    monkeypatch.setenv('WEB_CONCURRENCY', '8')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    try:
        server = launcher.DesktopServer(app=None, sock=sock)
        assert server.config.workers == 1
    finally:
        sock.close()
    assert app_workers.detected() == (1, 'desktop')


def test_multi_worker_refuses_every_durable_control(monkeypatch):
    tid = dc.create_thread()['thread_id']
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    calls = [
        dc.create_thread,
        lambda: dc.get_thread(tid),
        lambda: dc.checkpoint_thread(tid, generation=1, goals=['x']),
        lambda: dc.resume_episode(tid, generation=1),
        lambda: dc.rebind_scope(tid, generation=1),
        lambda: dc.authorize_bridge_credentials(tid, 'token'),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as failure:
            call()
        assert failure.value.status_code == 503
        detail = failure.value.detail
        assert detail['error_kind'] == 'multi_worker_unsupported'
        assert detail['workers'] == 2 and detail['source'] == 'WEB_CONCURRENCY'
    monkeypatch.delenv('WEB_CONCURRENCY')
    assert dc.get_thread(tid)['goals'] == []


def test_multi_worker_refusal_over_http(client, monkeypatch):
    monkeypatch.setattr(app_workers.sys, 'argv', ['uvicorn', 'main:app', '--workers', '2'])
    response = client.post('/api/durable-conversations')
    assert response.status_code == 503
    assert response.json()['detail']['error_kind'] == 'multi_worker_unsupported'
