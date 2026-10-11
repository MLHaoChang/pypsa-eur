"""Issue 06 — export the discussed (or named) run evidence, never another run."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness import catalogue, toolsets
from services import chat_service, chat_tools
from services import run_evidence as re_

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture
def roots(tmp_path, monkeypatch):
    def root_for(project_id):
        root = tmp_path / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def authority():
        return root_for(PROJECT), PROJECT, OWNER

    monkeypatch.setattr(re_, '_authority', authority)
    chat_service._reset_sessions_for_tests()
    chat_tools.set_chat_session(None)

    saved = []

    def fake_export(data, filename, mime, **_kwargs):
        saved.append({'data': data, 'filename': filename, 'mime': mime})
        return {
            'file_id': 'export-1', 'filename': filename, 'mime': mime,
            'size': len(data), 'kind': 'agent_export', 'sha256': 'abc',
        }

    monkeypatch.setattr(chat_tools, '_save_agent_export', fake_export)
    state = {'root_for': root_for, 'saved': saved}
    yield state
    chat_tools.set_chat_session(None)
    chat_service._reset_sessions_for_tests()


def _publish(root, run_id, *, started='2026-10-01T10:00:00+00:00'):
    return re_.publish_run(
        root, PROJECT, run_id=run_id, kind='simulation', status='completed',
        started_at=started, finished_at=started.replace('10:00:00', '10:05:00'),
        mode='lopf', fingerprint='fp-a',
        metrics={'objective': {'value': 1000.0, 'unit': 'EUR'}},
        sections={'summary': {'peak_load': {'value': 88.0, 'unit': 'MW'}}},
    )


def test_export_uses_discussed_run_when_run_id_omitted(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    _publish(root, 'run-b', started='2026-10-02T10:00:00+00:00')
    session = chat_service.ChatSession(owner_user_id=OWNER)
    session.discussed_refs = {'run_id': 'run-a', 'project_id': PROJECT}
    chat_tools.set_chat_session(session)

    out = chat_tools.DISPATCHERS['export_run_evidence']()
    assert out['file_id'] == 'export-1'
    assert out['run_id'] == 'run-a'
    payload = json.loads(roots['saved'][0]['data'])
    assert payload['run_id'] == 'run-a'
    assert payload['metrics']['objective']['value'] == 1000.0
    assert 'run-b' not in roots['saved'][0]['data'].decode()


def test_export_named_run_and_section(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)

    out = chat_tools.DISPATCHERS['export_run_evidence'](
        run_id='run-a', section='summary',
    )
    assert out['run_id'] == 'run-a'
    assert out['section'] == 'summary'
    payload = json.loads(roots['saved'][0]['data'])
    assert payload['section'] == 'summary'
    assert payload['data']['peak_load']['value'] == 88.0


def test_export_refuses_without_discussed_or_named_run(roots):
    roots['root_for'](PROJECT)
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    with pytest.raises(HTTPException) as failure:
        chat_tools.DISPATCHERS['export_run_evidence']()
    assert failure.value.detail['error_kind'] == 'discussed_run_required'


def test_export_unknown_run_refuses(roots):
    roots['root_for'](PROJECT)
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    with pytest.raises(HTTPException) as failure:
        chat_tools.DISPATCHERS['export_run_evidence'](run_id='missing')
    assert failure.value.detail['error_kind'] == 'run_not_found'


def test_catalogue_parity():
    tool = next(t for t in catalogue.TOOLS if t['name'] == 'export_run_evidence')
    assert 'export_run_evidence' in chat_tools.DISPATCHERS
    assert catalogue.TOOL_ROUTES['export_run_evidence'] == ['_service_call_']
    assert 'export_run_evidence' in toolsets.CONTROL_TOOLS
    assert 'Safety: write' in tool['description']
