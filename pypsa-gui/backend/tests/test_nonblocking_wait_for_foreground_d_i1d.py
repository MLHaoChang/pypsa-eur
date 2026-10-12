"""Issue 11 — wait_for_job accepts foreground simulation handles (fg:…)."""
from __future__ import annotations

import threading
import time

import pytest

from services import chat_service, chat_tools, utterance_admission as ua
from tests.test_nonblocking_foreground_solve_d_i1 import _hold_solver_running


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    ua._reset_for_tests()
    from harness import job_adapters as ja
    ja._reset_for_tests()
    yield
    ua._reset_for_tests()
    ja._reset_for_tests()


def test_handoff_result_includes_stable_foreground_job_id(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(install_network, monkeypatch)
    session = chat_service.ChatSession()
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        [],
    ))
    result = next(p['result'] for ev, p in frames if ev == 'tool_result')
    assert result['status'] == 'started'
    assert result['detached_progress'] is True
    job_id = result['job_id']
    assert isinstance(job_id, str) and job_id.startswith('fg:')
    worker.join(timeout=4)
    assert done.is_set()


def test_wait_for_job_observes_foreground_completion(
    tmp_projects_dir, install_network, monkeypatch, seeded_identity,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(
        install_network, monkeypatch, hold_seconds=0.4,
    )
    session = chat_service.ChatSession(
        owner_user_id=str(seeded_identity['user_id']),
    )
    chat_tools.set_chat_session(session)
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        [],
    ))
    job_id = next(p['result']['job_id'] for ev, p in frames if ev == 'tool_result')

    t0 = time.monotonic()
    out = chat_tools.wait_for_job(job_id, 3)
    assert time.monotonic() - t0 < 3.0
    assert out['terminal'] is True
    assert out['completed'] is True
    assert out['timed_out'] is False
    assert out['job']['id'] == job_id
    assert out['job']['family'] == 'foreground_simulation'
    assert out['job']['status'] == 'completed'
    worker.join(timeout=4)
    assert done.is_set()


def test_wait_for_job_foreground_timeout_leaves_solve_running(
    tmp_projects_dir, install_network, monkeypatch, seeded_identity,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(
        install_network, monkeypatch, hold_seconds=2.0,
    )
    session = chat_service.ChatSession(
        owner_user_id=str(seeded_identity['user_id']),
    )
    chat_tools.set_chat_session(session)
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        [],
    ))
    job_id = next(p['result']['job_id'] for ev, p in frames if ev == 'tool_result')

    out = chat_tools.wait_for_job(job_id, 0)
    assert out['timed_out'] is True
    assert out['terminal'] is False
    assert not done.is_set()
    with ctx.solver_state_lock:
        assert ctx.solver_state['status'] == 'running'
    worker.join(timeout=4)


def test_wait_for_job_unknown_foreground_handle_404(
    tmp_projects_dir, install_network,
):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as failure:
        chat_tools.wait_for_job('fg:does-not-exist', 0)
    assert failure.value.status_code == 404
    assert failure.value.detail['error_kind'] == 'job_not_found'
