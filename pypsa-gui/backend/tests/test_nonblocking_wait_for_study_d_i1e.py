"""Issue 11 — wait_for_job accepts adequacy/study handles (ad:…)."""
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


def _publish_running_study(install_network, *, key: str = 'fmea_sweep'):
    import pypsa
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)
    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        ctx.solver_state[key] = {
            'status': 'running',
            'rows': [],
            'error': None,
            'started_at': time.time(),
        }
    return ctx


def test_wait_for_job_observes_study_completion(
    tmp_projects_dir, install_network, seeded_identity,
):
    from harness import job_adapters as ja

    ctx = _publish_running_study(install_network)
    owner = str(seeded_identity['user_id'])
    job_id = ja.register_study_job(
        study_key='fmea_sweep',
        tool_name='run_fmea_sweep',
        tool_use_id='tu-fmea',
        owner_user_id=owner,
        project_uuid=getattr(ctx, 'project_uuid', None),
    )
    assert job_id.startswith('ad:')

    def _finish():
        time.sleep(0.25)
        with ctx.solver_state_lock:
            ctx.solver_state['fmea_sweep']['status'] = 'done'

    threading.Thread(target=_finish, daemon=True).start()
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    t0 = time.monotonic()
    out = chat_tools.wait_for_job(job_id, 3)
    assert time.monotonic() - t0 < 3.0
    assert out['terminal'] is True
    assert out['completed'] is True
    assert out['timed_out'] is False
    assert out['job']['id'] == job_id
    assert out['job']['family'] == 'adequacy_study'
    assert out['job']['study_key'] == 'fmea_sweep'
    assert out['job']['status'] == 'done'


@pytest.mark.parametrize(
    ('status', 'completed'),
    [('met', True), ('unreachable', False)],
)
def test_wait_for_job_treats_loop_verdicts_as_terminal(
    tmp_projects_dir, install_network, seeded_identity, status, completed,
):
    """Coupling/margin success is `met`; a finished negative verdict is `unreachable`."""
    from harness import job_adapters as ja

    ctx = _publish_running_study(install_network, key='coupling_loop')
    with ctx.solver_state_lock:
        ctx.solver_state['coupling_loop']['status'] = status
    owner = str(seeded_identity['user_id'])
    job_id = ja.register_study_job(
        study_key='coupling_loop',
        tool_name='run_coupling_loop',
        tool_use_id='tu-loop',
        owner_user_id=owner,
        project_uuid=getattr(ctx, 'project_uuid', None),
    )
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    t0 = time.monotonic()
    out = chat_tools.wait_for_job(job_id, 2)
    assert time.monotonic() - t0 < 1.5
    assert out['terminal'] is True
    assert out['completed'] is completed
    assert out['timed_out'] is False
    assert out['job']['status'] == status


def test_wait_for_job_study_timeout_leaves_study_running(
    tmp_projects_dir, install_network, seeded_identity,
):
    from harness import job_adapters as ja

    ctx = _publish_running_study(install_network)
    owner = str(seeded_identity['user_id'])
    job_id = ja.register_study_job(
        study_key='fmea_sweep',
        tool_name='run_fmea_sweep',
        tool_use_id='tu-fmea',
        owner_user_id=owner,
        project_uuid=getattr(ctx, 'project_uuid', None),
    )
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    out = chat_tools.wait_for_job(job_id, 0.3)
    assert out['terminal'] is False
    assert out['timed_out'] is True
    assert out['completed'] is False
    with ctx.solver_state_lock:
        assert ctx.solver_state['fmea_sweep']['status'] == 'running'


def test_study_handoff_mints_ad_job_id(
    tmp_projects_dir, install_network, monkeypatch, seeded_identity,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx = _publish_running_study(install_network)
    owner = str(seeded_identity['user_id'])

    def _fake_fmea(**_kwargs):
        return {'status': 'running'}

    # DISPATCHERS holds a gated wrapper; patch the entry the harness calls.
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'run_fmea_sweep', _fake_fmea)
    session = chat_service.ChatSession(owner_user_id=owner)
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-fmea', 'name': 'run_fmea_sweep', 'input': {}},
        [],
    ))
    result = next(p['result'] for ev, p in frames if ev == 'tool_result')
    assert result['status'] == 'running'
    job_id = result['job_id']
    assert isinstance(job_id, str) and job_id.startswith('ad:')
    assert result['study_key'] == 'fmea_sweep'
    assert result['monitor'] == "get_adequacy_results('fmea_sweep')"
    with ctx.solver_state_lock:
        assert ctx.solver_state['fmea_sweep']['status'] == 'running'


def test_unknown_study_job_refuses(
    tmp_projects_dir, install_network, seeded_identity,
):
    from fastapi import HTTPException

    owner = str(seeded_identity['user_id'])
    session = chat_service.ChatSession(owner_user_id=owner)
    chat_tools.set_chat_session(session)
    with pytest.raises(HTTPException) as ei:
        chat_tools.wait_for_job('ad:' + ('0' * 32), 1)
    assert ei.value.status_code == 404
    assert ei.value.detail['error_kind'] == 'job_not_found'
