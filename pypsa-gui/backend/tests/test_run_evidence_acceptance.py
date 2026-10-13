"""Issue 06 acceptance — three-run conversational evidence journey.

One project holds a completed run, a newer failed run and a newest running
marker. The journey lists them, drills the completed summary, binds discuss
refs + open_run_result, refuses an incomparable compare against the failed
run, exports the discussed completed run (never the failed/running ones),
then compares two compatible completed runs. ACL revocation and stale
durable generation refuse further mutations.
"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi import HTTPException

from services import chat_service, chat_tools
from services import durable_conversation as dc
from services import run_evidence as re_

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'

T_COMPLETED = '2026-10-01T10:00:00+00:00'
T_FAILED = '2026-10-02T10:00:00+00:00'
T_RUNNING = '2026-10-03T10:00:00+00:00'
T_SIBLING = '2026-10-01T12:00:00+00:00'


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

    saved = []

    def fake_export(data, filename, mime, **_kwargs):
        saved.append({'data': data, 'filename': filename, 'mime': mime})
        return {
            'file_id': 'export-accept-1', 'filename': filename, 'mime': mime,
            'size': len(data), 'kind': 'agent_export', 'sha256': 'deadbeef',
        }

    monkeypatch.setattr(chat_tools, '_save_agent_export', fake_export)
    state['root_for'] = root_for
    state['saved'] = saved
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    chat_tools.set_chat_session(None)
    yield state
    chat_tools.set_chat_session(None)
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()


def _seed_three(root):
    re_.publish_run(
        root, PROJECT,
        run_id='run-completed', kind='simulation', status='completed',
        started_at=T_COMPLETED, finished_at='2026-10-01T10:05:00+00:00',
        mode='lopf', fingerprint='fp-shared',
        metrics={
            'objective': {'value': 1234.5, 'unit': 'EUR'},
            'solve_seconds': {'value': 42.0, 'unit': 's'},
        },
        sections={
            'summary': {
                'total_cost': {'value': 1234.5, 'unit': 'EUR'},
                'peak_load': {'value': 88.0, 'unit': 'MW'},
            },
        },
    )
    re_.publish_run(
        root, PROJECT,
        run_id='run-failed', kind='simulation', status='failed',
        started_at=T_FAILED, finished_at='2026-10-02T10:01:00+00:00',
        mode='lopf', fingerprint='fp-failed',
        error={'error_kind': 'solver_infeasible', 'message': 'Infeasible.'},
    )
    re_.start_run(
        root, PROJECT, run_id='run-running', kind='simulation',
        started_at=T_RUNNING, mode='lopf', fingerprint='fp-running',
    )


def test_three_run_list_discuss_export_compare_and_acl(roots):
    root = roots['root_for'](PROJECT)
    _seed_three(root)

    listed = re_.list_project_runs(limit=2)
    assert [r['run_id'] for r in listed['items']] == ['run-running', 'run-failed']
    assert listed['has_more'] is True and listed['total_count'] == 3
    page2 = re_.list_project_runs(limit=2, offset=2)
    assert [r['run_id'] for r in page2['items']] == ['run-completed']
    by_id = {r['run_id']: r for r in re_.list_project_runs(limit=50)['items']}
    assert by_id['run-completed']['status'] == 'completed'
    assert by_id['run-completed']['started_at'] == T_COMPLETED
    assert by_id['run-completed']['evidence_available'] is True
    assert by_id['run-failed']['evidence_available'] is False
    assert by_id['run-running']['status'] == 'running'
    assert by_id['run-running']['finished_at'] is None

    summary = re_.get_run_evidence('run-completed')
    assert summary['metrics']['objective'] == {'value': 1234.5, 'unit': 'EUR'}
    assert summary['metrics']['solve_seconds'] == {'value': 42.0, 'unit': 's'}
    assert {'name': 'summary', 'available': True} in summary['sections']
    drilled = re_.get_run_evidence('run-completed', section='summary')
    assert drilled['data']['peak_load'] == {'value': 88.0, 'unit': 'MW'}
    assert drilled['data']['total_cost']['unit'] == 'EUR'

    with pytest.raises(HTTPException) as missing_failed:
        re_.get_run_evidence('run-failed', section='summary')
    assert missing_failed.value.detail['error_kind'] == 'evidence_unavailable'
    assert missing_failed.value.detail['run_id'] == 'run-failed'

    session = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(session)
    ui = chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-completed', results_tab='dispatch', section='summary',
    )
    assert ui['_ui_event'] is True
    assert ui['kind'] == 'open_run_result'
    assert ui['run_id'] == 'run-completed'
    assert ui['results_tab'] == 'dispatch'
    assert ui['section'] == 'summary'
    assert session.discussed_refs['run_id'] == 'run-completed'
    assert session.discussed_refs['project_id'] == PROJECT
    assert session.discussed_refs['section'] == 'summary'
    assert session.discussed_refs['source'] == 'dispatch'
    assert session.discussed_refs['status'] == 'completed'
    assert session.discussed_refs['mode'] == 'lopf'
    assert session.discussed_refs['fingerprint'] == 'fp-shared'
    assert session.discussed_refs['evidence_available'] is True

    incomparable = re_.compare_project_runs('run-failed', 'run-completed')
    assert incomparable['comparable'] is False
    assert incomparable['deltas'] == {}
    assert any(
        reason.get('reason') == 'run_not_completed'
        for reason in incomparable['incomparable_reasons']
    )

    exported = chat_tools.DISPATCHERS['export_run_evidence']()
    assert exported['file_id'] == 'export-accept-1'
    assert exported['run_id'] == 'run-completed'
    payload = json.loads(roots['saved'][0]['data'])
    assert payload['run_id'] == 'run-completed'
    assert payload['metrics']['objective']['value'] == 1234.5
    assert payload['metrics']['objective']['unit'] == 'EUR'
    blob = roots['saved'][0]['data'].decode()
    assert 'run-failed' not in blob and 'run-running' not in blob

    re_.publish_run(
        root, PROJECT,
        run_id='run-completed-b', kind='simulation', status='completed',
        started_at=T_SIBLING, finished_at='2026-10-01T12:05:00+00:00',
        mode='lopf', fingerprint='fp-shared',
        metrics={
            'objective': {'value': 1500.0, 'unit': 'EUR'},
            'solve_seconds': {'value': 30.0, 'unit': 's'},
        },
        sections={'summary': {'peak_load': {'value': 90.0, 'unit': 'MW'}}},
    )
    chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-completed-b', baseline_run_id='run-completed', open_ui=False,
    )
    compared = re_.compare_project_runs('run-completed-b', 'run-completed')
    assert compared['comparable'] is True
    assert compared['deltas']['objective']['delta'] == {'value': 265.5, 'unit': 'EUR'}
    assert compared['deltas']['objective']['delta_pct'] == pytest.approx(21.506, rel=1e-3)
    assert compared['run']['started_at'] == T_SIBLING
    assert compared['baseline']['started_at'] == T_COMPLETED

    roots['revoked'] = True
    with pytest.raises(HTTPException):
        re_.list_project_runs()
    with pytest.raises(HTTPException):
        re_.get_run_evidence('run-completed')
    with pytest.raises(HTTPException):
        chat_tools.DISPATCHERS['discuss_project_run'](run_id='run-completed')
    with pytest.raises(HTTPException):
        chat_tools.DISPATCHERS['export_run_evidence']()


def test_restart_clears_session_refs_and_stale_generation_refuses(roots):
    root = roots['root_for'](PROJECT)
    _seed_three(root)

    thread = dc.create_thread()
    updated, episode = dc.resume_episode(thread['thread_id'], generation=1)
    chat_tools.set_chat_session(episode)
    chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-completed', results_tab='economics', open_ui=False,
    )
    parent = dc.get_thread(updated['thread_id'])
    assert parent['refs']['discussed']['run_id'] == 'run-completed'
    assert parent['refs']['discussed']['source'] == 'economics'
    generation = parent['generation']

    # Simulate restart: drop the live episode; a fresh session has no refs.
    chat_tools.set_chat_session(None)
    chat_service._reset_sessions_for_tests()
    fresh = chat_service.ChatSession(owner_user_id=OWNER)
    chat_tools.set_chat_session(fresh)
    assert not fresh.discussed_refs
    with pytest.raises(HTTPException) as missing:
        chat_tools.DISPATCHERS['export_run_evidence']()
    assert missing.value.detail['error_kind'] == 'discussed_run_required'

    # Resume the durable parent restores discussed refs on the new episode.
    view, resumed = dc.resume_episode(updated['thread_id'], generation=generation)
    chat_tools.set_chat_session(resumed)
    assert resumed.discussed_refs['run_id'] == 'run-completed'
    assert resumed.discussed_refs['source'] == 'economics'
    assert resumed.discussed_refs['status'] == 'completed'
    assert resumed.discussed_refs['evidence_available'] is True
    exported = chat_tools.DISPATCHERS['export_run_evidence'](section='summary')
    assert exported['run_id'] == 'run-completed'
    assert exported['section'] == 'summary'

    live_generation = view['generation']
    dc.rebind_scope(updated['thread_id'], generation=live_generation)
    with pytest.raises(HTTPException) as stale:
        dc.set_discussed_run(
            updated['thread_id'],
            generation=live_generation,
            run_id='run-failed',
            project_id=PROJECT,
        )
    assert stale.value.detail['error_kind'] == 'stale_generation'
    after = dc.get_thread(updated['thread_id'])
    assert after['refs']['discussed']['run_id'] == 'run-completed'
    assert after['generation'] == live_generation + 1


def test_resume_keeps_discussed_run_when_evidence_is_gone(roots):
    """A deleted run must not drop the binding or invent a live evidence status."""
    root = roots['root_for'](PROJECT)
    _seed_three(root)
    thread = dc.create_thread()
    updated, episode = dc.resume_episode(thread['thread_id'], generation=1)
    chat_tools.set_chat_session(episode)
    chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-completed',
        results_tab='economics',
        section='summary',
        open_ui=False,
    )
    generation = dc.get_thread(updated['thread_id'])['generation']
    shutil.rmtree(root / 'run_evidence' / 'run-completed')

    chat_service._reset_sessions_for_tests()
    _view, resumed = dc.resume_episode(updated['thread_id'], generation=generation)
    refs = resumed.discussed_refs
    assert refs['run_id'] == 'run-completed'
    assert refs['project_id'] == PROJECT
    assert refs['source'] == 'economics'
    assert refs['section'] == 'summary'
    for invented in ('status', 'mode', 'fingerprint', 'evidence_available'):
        assert invented not in refs

    chat_tools.set_chat_session(resumed)
    with pytest.raises(HTTPException) as missing:
        chat_tools.DISPATCHERS['export_run_evidence']()
    assert missing.value.status_code == 404
    assert missing.value.detail['error_kind'] == 'run_not_found'


def test_resume_does_not_hide_evidence_lookup_failures(roots, monkeypatch):
    root = roots['root_for'](PROJECT)
    _seed_three(root)
    thread = dc.create_thread()
    updated, episode = dc.resume_episode(thread['thread_id'], generation=1)
    chat_tools.set_chat_session(episode)
    chat_tools.DISPATCHERS['discuss_project_run'](
        run_id='run-completed', open_ui=False,
    )
    generation = dc.get_thread(updated['thread_id'])['generation']

    def boom(run_id, section=None):
        raise RuntimeError('evidence store offline')

    monkeypatch.setattr(re_, 'get_run_evidence', boom)
    chat_service._reset_sessions_for_tests()
    with pytest.raises(RuntimeError, match='evidence store offline'):
        dc.resume_episode(updated['thread_id'], generation=generation)
