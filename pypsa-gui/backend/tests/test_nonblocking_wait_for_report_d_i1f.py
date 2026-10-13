"""Issue 11 — wait_for_job accepts report-job handles (rpt:…)."""
from __future__ import annotations

import threading
import time

import pytest

from services import chat_service, chat_tools, utterance_admission as ua


@pytest.fixture(autouse=True)
def _reset():
    ua._reset_for_tests()
    from harness import job_adapters as ja
    ja._reset_for_tests()
    yield
    ua._reset_for_tests()
    ja._reset_for_tests()


def _publish_running_report(install_network, *, report_id: str = 'rep-1'):
    import pypsa
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)
    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        ctx.solver_state['report_job'] = {
            'status': 'running',
            'report_id': report_id,
            'mode': 'generate',
            'section': 'executive_summary',
            'error': None,
            'started_at': time.time(),
        }
    return ctx


def test_wait_for_job_observes_report_completion(
    tmp_projects_dir, install_network, seeded_identity,
):
    from harness import job_adapters as ja

    ctx = _publish_running_report(install_network)
    owner = str(seeded_identity['user_id'])
    job_id = ja.register_report_job(
        tool_name='generate_report',
        tool_use_id='tu-rpt',
        report_id='rep-1',
        owner_user_id=owner,
        project_uuid=getattr(ctx, 'project_uuid', None),
    )
    assert job_id.startswith('rpt:')

    def _finish():
        time.sleep(0.25)
        with ctx.solver_state_lock:
            ctx.solver_state['report_job']['status'] = 'done'
            ctx.solver_state['report_job']['version'] = 1

    threading.Thread(target=_finish, daemon=True).start()
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    out = chat_tools.wait_for_job(job_id, 3)
    assert out['terminal'] is True
    assert out['completed'] is True
    assert out['timed_out'] is False
    assert out['job']['id'] == job_id
    assert out['job']['family'] == 'report_job'
    assert out['job']['status'] == 'done'
    assert out['job']['report_id'] == 'rep-1'


def test_wait_for_job_report_timeout_leaves_job_running(
    tmp_projects_dir, install_network, seeded_identity,
):
    from harness import job_adapters as ja

    ctx = _publish_running_report(install_network)
    owner = str(seeded_identity['user_id'])
    job_id = ja.register_report_job(
        tool_name='generate_report',
        tool_use_id='tu-rpt',
        report_id='rep-1',
        owner_user_id=owner,
        project_uuid=getattr(ctx, 'project_uuid', None),
    )
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    out = chat_tools.wait_for_job(job_id, 0.3)
    assert out['terminal'] is False
    assert out['timed_out'] is True
    with ctx.solver_state_lock:
        assert ctx.solver_state['report_job']['status'] == 'running'


def test_report_handoff_mints_rpt_job_id(
    tmp_projects_dir, install_network, monkeypatch, seeded_identity,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution', 'write'}),
    )
    _publish_running_report(install_network)
    owner = str(seeded_identity['user_id'])

    def _fake_generate(**_kwargs):
        return {'status': 'running', 'report_id': 'rep-hand'}

    monkeypatch.setitem(chat_tools.DISPATCHERS, 'generate_report', _fake_generate)
    session = chat_service.ChatSession(owner_user_id=owner)
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-rpt', 'name': 'generate_report', 'input': {}},
        [],
    ))
    result = next(p['result'] for ev, p in frames if ev == 'tool_result')
    assert result['status'] == 'running'
    assert result['job_id'].startswith('rpt:')
    assert result['family'] == 'report_job'
    assert result['monitor'] == 'get_report_status'


def test_unknown_report_job_refuses(
    tmp_projects_dir, install_network, seeded_identity,
):
    from fastapi import HTTPException

    owner = str(seeded_identity['user_id'])
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    with pytest.raises(HTTPException) as ei:
        chat_tools.wait_for_job('rpt:' + ('0' * 32), 1)
    assert ei.value.status_code == 404
    assert ei.value.detail['error_kind'] == 'job_not_found'
