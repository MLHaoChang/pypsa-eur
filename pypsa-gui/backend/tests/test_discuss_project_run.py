"""Issue 06 — conversational run refs + open_run_result UI selection.

discuss_project_run validates a recorded run, stores discussed refs on the
chat session (and durable parent when an episode is bound), and emits a typed
open_run_result ui_event so the Results chrome selects that run without
restoring historical inputs.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness import catalogue, toolsets
from services import chat_service, chat_tools
from services import durable_conversation as dc
from services import run_evidence as re_

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture
def roots(tmp_path, monkeypatch):
    state = {'project': PROJECT, 'revoked': False}

    def root_for(project_id):
        root = tmp_path / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def authority():
        if state['revoked']:
            raise HTTPException(404, 'Project not found')
        return root_for(state['project']), state['project'], OWNER

    monkeypatch.setattr(re_, '_authority', authority)
    monkeypatch.setattr(dc, '_authority', authority)
    state['root_for'] = root_for
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    chat_tools.set_chat_session(None)
    yield state
    chat_tools.set_chat_session(None)
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()


def _publish(root, run_id, *, status='completed', started='2026-10-01T10:00:00+00:00',
             mode='lopf', fingerprint='fp-a'):
    kwargs = {}
    if status == 'completed':
        kwargs['metrics'] = {
            'objective': {'value': 1000.0, 'unit': 'EUR'},
            'solve_seconds': {'value': 40.0, 'unit': 's'},
        }
        kwargs['sections'] = {'summary': {'peak_load': {'value': 88.0, 'unit': 'MW'}}}
    elif status == 'failed':
        kwargs['error'] = {'error_kind': 'solver_infeasible', 'message': 'Infeasible.'}
    return re_.publish_run(
        root, PROJECT, run_id=run_id, kind='simulation', status=status,
        started_at=started, finished_at=started.replace('10:00:00', '10:05:00'),
        mode=mode, fingerprint=fingerprint, **kwargs,
    )


def test_discuss_project_run_stores_session_refs_and_emits_ui_event(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)

    out = chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-a', results_tab='dispatch', section='summary',
    )
    assert out['_ui_event'] is True
    assert out['kind'] == 'open_run_result'
    assert out['run_id'] == 'run-a'
    assert out['results_tab'] == 'dispatch'
    assert out['section'] == 'summary'
    assert out['status'] == 'completed'
    assert session.discussed_refs['run_id'] == 'run-a'
    assert session.discussed_refs['project_id'] == PROJECT
    assert session.discussed_refs['section'] == 'summary'
    assert session.discussed_refs['source'] == 'dispatch'


def test_discuss_project_run_can_skip_ui_and_set_baseline(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    _publish(root, 'new', started='2026-10-02T10:00:00+00:00')
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)

    out = chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='new', baseline_run_id='base', open_ui=False,
    )
    assert '_ui_event' not in out
    assert out['ok'] is True
    assert out['discussed']['run_id'] == 'new'
    assert out['discussed']['baseline_run_id'] == 'base'
    assert session.discussed_refs['baseline_run_id'] == 'base'


def test_discuss_unknown_run_refuses(roots):
    roots['root_for'](PROJECT)
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    with pytest.raises(HTTPException) as failure:
        chat_tools.DISPATCHERS['discuss_project_run'](run_id='missing')
    assert failure.value.status_code == 404
    assert failure.value.detail['error_kind'] == 'run_not_found'
    assert session.discussed_refs is None or session.discussed_refs == {}


def test_discuss_requires_chat_session(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    chat_tools.set_chat_session(None)
    with pytest.raises(HTTPException) as failure:
        chat_tools.DISPATCHERS['discuss_project_run'](run_id='run-a')
    assert failure.value.detail['error_kind'] == 'no_chat_session'


def test_discuss_syncs_durable_parent_when_episode_bound(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    thread = dc.create_thread()
    updated, episode = dc.resume_episode(thread['thread_id'], generation=1)
    chat_tools.set_chat_session(episode)

    chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-a', results_tab='economics', open_ui=True,
    )
    after = dc.get_thread(updated['thread_id'])
    assert after['refs']['discussed']['run_id'] == 'run-a'
    assert after['refs']['discussed']['project_id'] == PROJECT
    assert after['refs']['discussed']['source'] == 'economics'
    assert after['checkpoint_rev'] >= 1


def test_set_discussed_run_generation_cas(roots):
    roots['root_for'](PROJECT)
    thread = dc.create_thread()
    tid = thread['thread_id']
    dc.set_discussed_run(
        tid, generation=1, run_id='R0', project_id=PROJECT, source='dispatch',
    )
    # Same generation may checkpoint again (like checkpoint_thread); rebind
    # advances generation and rejects the stale token.
    dc.set_discussed_run(
        tid, generation=1, run_id='R1', project_id=PROJECT, source='economics',
    )
    assert dc.get_thread(tid)['refs']['discussed']['run_id'] == 'R1'
    dc.rebind_scope(tid, generation=1)
    with pytest.raises(HTTPException) as failure:
        dc.set_discussed_run(
            tid, generation=1, run_id='R2', project_id=PROJECT,
        )
    assert failure.value.detail['error_kind'] == 'stale_generation'
    after = dc.get_thread(tid)
    assert after['refs']['discussed']['run_id'] == 'R1'
    assert after['generation'] == 2


def test_acl_revocation_blocks_discuss(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    roots['revoked'] = True
    with pytest.raises(HTTPException):
        chat_tools.DISPATCHERS['discuss_project_run'](run_id='run-a')


def test_catalogue_and_dispatcher_parity():
    tool = next(t for t in catalogue.TOOLS if t['name'] == 'discuss_project_run')
    schema = tool['input_schema']
    assert 'run_id' in schema['required']
    assert 'discuss_project_run' in chat_tools.DISPATCHERS
    assert catalogue.TOOL_ROUTES['discuss_project_run'] == ['_ui_event_']
    assert 'discuss_project_run' in toolsets.CONTROL_TOOLS
    assert 'Safety: write' in tool['description']


def test_invalid_results_tab_refused(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'run-a')
    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    with pytest.raises(HTTPException) as failure:
        chat_tools.DISPATCHERS['discuss_project_run'](
            run_id='run-a', results_tab='not-a-tab',
        )
    assert failure.value.detail['error_kind'] == 'invalid_navigation'
