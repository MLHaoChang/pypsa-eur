"""Issue 06 — a foreground solve handoff publishes a listable run.

The chat harness handoff for run_simulation / run_ac_pf_stage records a running
marker, and the detached solver monitor publishes the immutable manifest when the
solver state turns terminal. No real solve runs: the solver state is flipped by
a fake worker, the same way the issue 11 tests do.
"""
from __future__ import annotations

import threading
import time

import pytest

from services import chat_service, chat_tools, utterance_admission as ua
from services import run_evidence as re_
from services.pypsa_service import PyPSAService

PROJECT = '33333333-3333-3333-3333-333333333333'
OWNER = 'owner-fg'


@pytest.fixture(autouse=True)
def _reset():
    ua._reset_for_tests()
    from harness import job_adapters as ja
    ja._reset_for_tests()
    yield
    ua._reset_for_tests()
    ja._reset_for_tests()


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    root = tmp_path / PROJECT
    root.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (root, PROJECT, OWNER))
    return root


def _start_solve(install_network, monkeypatch, *, final: dict, hold: float = 0.3,
                 kind: str = 'lopf'):
    import pypsa
    from routers.simulation import BufferedLogQueue
    import harness.confirm as harness_confirm

    monkeypatch.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
    n = pypsa.Network()
    n.add('Bus', 'B1')
    n.add('Generator', 'G1', bus='B1', p_nom=10, marginal_cost=1)
    n.add('Load', 'L1', bus='B1', p_set=5)
    install_network(n, name=None)
    ctx = PyPSAService.get_active_context()
    log_queue = BufferedLogQueue(maxlen=100)

    def fake_run():
        with ctx.solver_state_lock:
            ctx.solver_state.update(status='running', kind=kind, objective=None,
                                    condition=None, solve_time=None, log_queue=log_queue, thread=worker)
        # Start the worker only once 'running' is published, so 'completed'
        # can never precede it.
        if worker.ident is None:
            worker.start()
        return {'status': 'started'}

    monkeypatch.setattr('routers.simulation.run', fake_run)
    monkeypatch.setattr(chat_tools, 'run_simulation', fake_run)
    monkeypatch.setattr('routers.simulation.run_ac_pf', fake_run)
    monkeypatch.setattr(chat_tools, 'run_ac_pf_stage', fake_run)

    def finish_later():
        time.sleep(hold)
        with ctx.solver_state_lock:
            ctx.solver_state.update(final)

    # Publish the same worker identity production claims with status=running.
    worker = threading.Thread(target=finish_later, daemon=True)
    session = chat_service.ChatSession()
    tool = 'run_simulation' if kind == 'lopf' else 'run_ac_pf_stage'
    frames = list(chat_service._dispatch_real_tool_call(
        session, {'id': 'tu-1', 'name': tool, 'input': {}}, []))
    result = next(p['result'] for ev, p in frames if ev == 'tool_result')
    return ctx, session, result


def _wait_for_status(root_status, want, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        items = re_.list_project_runs()['items']
        if items and items[0]['status'] == want:
            return items
        time.sleep(0.05)
    raise AssertionError(f'run never reached {want}: {re_.list_project_runs()}')


def test_handoff_records_running_marker_then_completed_run(
    tmp_projects_dir, install_network, monkeypatch, evidence_root,
):
    ctx, _session, result = _start_solve(
        install_network, monkeypatch,
        final={'status': 'completed', 'objective': 1234.5, 'solve_time': 2.5,
               'condition': 'optimal'},
    )
    job_id = result['job_id']
    run_id = re_.foreground_run_id(job_id)
    running = re_.list_project_runs()['items']
    assert [r['run_id'] for r in running] == [run_id]
    assert running[0]['status'] == 'running' and running[0]['job_id'] == job_id

    items = _wait_for_status(evidence_root, 'completed')
    assert items[0]['run_id'] == run_id and items[0]['evidence_available'] is True
    assert items[0]['mode'] and items[0]['fingerprint']
    detail = re_.get_run_evidence(run_id)
    assert detail['metrics']['objective'] == {'value': 1234.5, 'unit': 'EUR'}
    assert detail['metrics']['solve_seconds'] == {'value': 2.5, 'unit': 's'}
    section = re_.get_run_evidence(run_id, section='summary')
    assert section['data']['condition'] == 'optimal'
    assert section['data']['metrics']['objective']['value'] == 1234.5
    assert detail['started_at'] <= detail['finished_at']


def test_failed_solve_publishes_failed_run_without_metrics(
    tmp_projects_dir, install_network, monkeypatch, evidence_root,
):
    _ctx, _session, result = _start_solve(
        install_network, monkeypatch,
        final={'status': 'failed', 'condition': 'infeasible'},
    )
    _wait_for_status(evidence_root, 'failed')
    detail = re_.get_run_evidence(re_.foreground_run_id(result['job_id']))
    assert detail['status'] == 'failed' and detail['metrics'] == {}
    assert detail['error']['message'] == 'infeasible'
    assert detail['sections'] == []


def test_aborted_solve_is_recorded_as_interrupted(
    tmp_projects_dir, install_network, monkeypatch, evidence_root,
):
    _start_solve(install_network, monkeypatch, final={'status': 'aborted'})
    items = _wait_for_status(evidence_root, 'interrupted')
    assert items[0]['evidence_available'] is False


def test_ac_pf_handoff_publishes_ac_pf_mode(
    tmp_projects_dir, install_network, monkeypatch, evidence_root,
):
    _ctx, _session, result = _start_solve(
        install_network, monkeypatch, kind='ac_pf',
        final={'status': 'completed', 'condition': 'ac_pf_ok', 'solve_time': 1.0,
               'ac_pf_converged_count': 3, 'ac_pf_total_snapshots': 4},
    )
    items = _wait_for_status(evidence_root, 'completed')
    assert items[0]['kind'] == 'ac_pf' and items[0]['mode'] == 'ac_pf'
    metrics = re_.get_run_evidence(re_.foreground_run_id(result['job_id']))['metrics']
    assert metrics['ac_pf_converged_count'] == {'value': 3, 'unit': 'snapshots'}
    assert 'objective' not in metrics


def test_duplicate_publication_is_idempotent(
    tmp_projects_dir, install_network, monkeypatch, evidence_root,
):
    ctx, _session, result = _start_solve(
        install_network, monkeypatch,
        final={'status': 'completed', 'objective': 5.0, 'solve_time': 1.0},
    )
    _wait_for_status(evidence_root, 'completed')
    run_id = re_.foreground_run_id(result['job_id'])
    first = re_.get_run_evidence(run_id)
    token = {'root': evidence_root, 'project_id': PROJECT, 'actor_id': OWNER,
             'run_id': run_id, 'job_id': result['job_id'], 'kind': 'simulation',
             'solver_kind': 'lopf', 'started_at': first['started_at'],
             'unsaved_inputs': first['unsaved_inputs']}
    assert re_.publish_foreground_run(token, ctx)['run_id'] == run_id
    assert re_.list_project_runs()['total_count'] == 1


def test_non_terminal_solver_publishes_nothing(
    tmp_projects_dir, install_network, monkeypatch, evidence_root,
):
    ctx, _session, result = _start_solve(
        install_network, monkeypatch, hold=30,
        final={'status': 'completed', 'objective': 1.0},
    )
    token = {'root': evidence_root, 'project_id': PROJECT, 'actor_id': OWNER,
             'run_id': re_.foreground_run_id(result['job_id']), 'job_id': result['job_id'],
             'kind': 'simulation', 'solver_kind': 'lopf', 'started_at': re_._now(),
             'unsaved_inputs': False}
    assert re_.publish_foreground_run(token, ctx) is None
    assert re_.list_project_runs()['items'][0]['status'] == 'running'
    with ctx.solver_state_lock:
        ctx.solver_state['status'] = 'idle'


def test_no_project_authority_does_not_break_the_solve(
    tmp_projects_dir, install_network, monkeypatch,
):
    from fastapi import HTTPException

    def refuse():
        raise HTTPException(409, {'error_kind': 'run_project_required', 'message': 'x'})

    monkeypatch.setattr(re_, '_authority', refuse)
    ctx, session, result = _start_solve(
        install_network, monkeypatch,
        final={'status': 'completed', 'objective': 1.0}, hold=0.2,
    )
    assert result['status'] == 'started' and result['job_id'].startswith('fg:')
    out = chat_tools.wait_for_job(result['job_id'], 3)
    assert out['completed'] is True
