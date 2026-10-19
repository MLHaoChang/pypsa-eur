"""Explicit reconciliation reads native proof and never replays a case."""
# ruff: noqa: F811 -- shared public harness fixtures.
import pytest
import contextvars
import json
import os
import threading
import time
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException

from services import chat_tools
from services.solve_queue import solve_queue
from tests.test_experiment_case_advancement import prepared, arm, invoke, tool
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401


def test_pre_effect_interruption_can_settle_without_reopening_submission(baseline):
    _ctx, _project, root = baseline
    before = (root / 'network.nc').read_bytes()
    eid = prepared()
    session, armed = arm(eid)

    def interrupted(**_):
        raise RuntimeError('native handler stopped before effects')

    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], interrupted)
    uncertain = tool('refresh_experiment', experiment_id=eid)
    assert uncertain['status'] == 'uncertain'
    call = uncertain['reconciliation']['next_call']
    settled = tool(call['tool'], **call['args'])
    assert settled['status'] == 'not_started' and settled['executed'] is False
    assert settled['reconciliation']['outcome'] == 'no_effects_verified'
    assert settled['case_effects_reserved'] and not settled['result_available']
    assert 'next_call' not in settled and 'objective' not in settled
    with pytest.raises(HTTPException):
        invoke(session, armed['next_call'])
    assert not solve_queue.list_jobs()
    assert (root / 'network.nc').read_bytes() == before


def stopped_before_effects():
    eid = prepared()
    session, armed = arm(eid)
    def interrupted(**_):
        raise RuntimeError('stopped before effects')
    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], interrupted)
    return eid, session, armed, tool('refresh_experiment', experiment_id=eid)['reconciliation']['next_call']


def fail_checkpoint(monkeypatch, phase):
    """Fault the durable filesystem boundary before its next native effect."""
    replace = os.replace
    def fail(source, destination):
        from pathlib import Path
        destination = Path(destination)
        if destination.parent.name == 'submission_receipts' and json.loads(source.read_text())['phase'] == phase:
            raise OSError('private checkpoint disk failure')
        return replace(source, destination)
    monkeypatch.setattr(os, 'replace', fail)


@pytest.mark.parametrize(('phase', 'outcome', 'state', 'jobs'), [
    ('effect_started', 'no_effects_verified', 'not_started', 0),
    ('queue_started', 'partial_effects', 'uncertain', 0),
    ('job_recorded', 'unresolved', 'uncertain', 1),
])
def test_native_checkpoint_fault_never_guesses_unrecorded_effects(baseline, monkeypatch, phase, outcome, state, jobs):
    eid = prepared()
    session, armed = arm(eid)
    fail_checkpoint(monkeypatch, phase)
    partial = invoke(session, armed['next_call'])
    assert partial['status'] == 'partial'
    current = tool('refresh_experiment', experiment_id=eid)
    call = current['reconciliation']['next_call']
    result = tool(call['tool'], **call['args'])
    assert result['reconciliation']['outcome'] == outcome
    assert result['status'] == state and not result['result_available']
    assert 'objective' not in result and 'next_call' not in result
    assert result['job_id'] is None  # Even the sole actual job is not guessed.
    assert len(solve_queue.list_jobs()) == jobs
    if jobs:
        assert chat_tools.wait_for_job(solve_queue.list_jobs()[0]['id'], 25)['completed']
    with pytest.raises(HTTPException):
        invoke(session, armed['next_call'])


def test_live_detached_submission_cannot_be_reconciled_before_late_return(baseline):
    eid = prepared()
    session, armed = arm(eid)
    entered, release = threading.Event(), threading.Event()
    def late(**args):
        entered.set()
        assert release.wait(10)
        return chat_tools.run_sensitivity_sweep(**args)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, invoke, session, armed['next_call'], late)
        try:
            assert entered.wait(5)
            with pytest.raises(HTTPException) as error:
                tool('reconcile_experiment', experiment_id=eid, receipt_sha256='0' * 64)
            assert error.value.detail['error_kind'] == 'experiment_worker_running'
            assert not future.done() and not solve_queue.list_jobs()
        finally:
            release.set()
        actual = future.result(timeout=10)['cases'][0]
    assert chat_tools.wait_for_job(actual['job']['id'], 25)['completed']
    assert tool('refresh_experiment', experiment_id=eid)['status'] == 'completed'
    assert len(solve_queue.list_jobs()) == 1


def test_stale_confirmation_refuses_then_preserves_cancel_and_exhausted_caps(baseline, monkeypatch):
    from harness import experiment_execution as engine
    eid, _session, _armed, call = stopped_before_effects()
    cancelled = tool('cancel_experiment', experiment_id=eid)
    with pytest.raises(HTTPException) as error:
        tool(call['tool'], **call['args'])
    assert error.value.detail['error_kind'] == 'experiment_reconciliation_stale'
    monkeypatch.setattr(engine, '_clock', lambda: time.time() + 10000)
    current = tool('refresh_experiment', experiment_id=eid)
    fresh = current['reconciliation']['next_call']
    settled = tool(fresh['tool'], **fresh['args'])
    assert settled['cancelled'] and settled['attempts_used'] == cancelled['attempts_used'] == 1
    assert settled['status'] == 'not_started' and 'next_call' not in settled
    assert not solve_queue.list_jobs()


def test_duplicate_reconciliation_is_idempotent_without_reopening_call(baseline):
    eid, session, armed, call = stopped_before_effects()
    first = tool(call['tool'], **call['args'])
    second = tool(call['tool'], **call['args'])
    assert second['reconciliation']['observed_at'] == first['reconciliation']['observed_at']
    assert second['status'] == 'not_started' and second['attempts_used'] == 1
    with pytest.raises(HTTPException):
        invoke(session, armed['next_call'])
    assert not solve_queue.list_jobs()


@pytest.mark.parametrize('damage', ['missing', 'json', 'owner', 'plan', 'phase', 'unexpected', 'symlink'])
def test_missing_legacy_or_corrupt_proof_never_certifies_no_effects(baseline, damage):
    _ctx, _project, root = baseline
    eid, _session, _armed, call = stopped_before_effects()
    path = root / 'experiment_execution' / 'submission_receipts' / (eid + '.json')
    before = path.read_bytes()
    if damage == 'missing':
        path.unlink()
    elif damage == 'json':
        path.write_text('{private corruption')
    elif damage == 'symlink':
        target = root / 'foreign-proof.json'
        target.write_bytes(before)
        path.unlink()
        path.symlink_to(target)
    else:
        data = json.loads(before)
        data[{'owner': 'owner_id', 'plan': 'plan_sha256', 'phase': 'phase', 'unexpected': 'unexpected'}[damage]] = 'invalid'
        path.write_text(json.dumps(data))
    damaged = path.read_bytes() if path.exists() else None
    with pytest.raises(HTTPException) as error:
        tool(call['tool'], **call['args'])
    assert error.value.detail['error_kind'] in {'experiment_checkpoint_unreadable', 'experiment_reconciliation_unavailable'}
    assert (path.read_bytes() if path.exists() else None) == damaged
    assert not solve_queue.list_jobs()


def test_foreign_actor_cannot_use_a_known_receipt_digest(baseline, second_identity):
    _eid, _session, _armed, call = stopped_before_effects()
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(second_identity['user_id'])
    try:
        with pytest.raises(HTTPException) as error:
            tool(call['tool'], **call['args'])
        assert error.value.status_code == 404
    finally:
        chat_tools.set_acting_user(previous)


def test_strict_checked_chat_does_not_admit_reconciliation(baseline, checked):
    from tests.assurance_acceptance.support import UNSAFE, public_text, text_turn, tool_turn
    eid, _session, _armed, call = stopped_before_effects()
    frames = checked.run([tool_turn(call['tool'], call['args']), text_turn(UNSAFE), text_turn(UNSAFE)])
    assert any(event == 'tool_error' and p.get('tool_name') == call['tool'] for event, p in frames)
    assert UNSAFE not in public_text(frames)
    assert tool('refresh_experiment', experiment_id=eid)['status'] == 'uncertain'
    assert not solve_queue.list_jobs()


def test_current_owner_still_obeys_foreign_edit_lock(baseline, second_identity):
    from services import project_locks, project_registry
    _eid, _session, _armed, call = stopped_before_effects()
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, baseline[1])
        project_locks.release_lock(db, project.id, user.id)
        assert project_locks.acquire_lock(db, project.id, second_identity['user_id'])
        try:
            with pytest.raises(HTTPException) as error:
                tool(call['tool'], **call['args'])
            assert error.value.detail['error_kind'] == 'project_locked'
        finally:
            project_locks.release_lock(db, project.id, second_identity['user_id'])
            assert project_locks.acquire_lock(db, project.id, user.id)
    assert not solve_queue.list_jobs()


def test_fresh_process_lease_blocks_resolution_until_process_exits(baseline, tmp_path):
    _ctx, _project, root = baseline
    eid, _session, _armed, call = stopped_before_effects()
    ready = tmp_path / 'leased'
    script = '''
import json,sys,time
from pathlib import Path
from harness.experiment_reconciliation import _lease
root, eid, ready = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
record = json.loads((root / 'experiments' / (eid + '.json')).read_text())
with _lease(root, record):
    ready.write_text('held')
    time.sleep(30)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    child = subprocess.Popen([sys.executable, '-c', script, str(root), eid, str(ready)],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), 'The independent native fence was not established'
        with pytest.raises(HTTPException) as error:
            tool(call['tool'], **call['args'])
        assert error.value.detail['error_kind'] == 'experiment_worker_running'
    finally:
        child.terminate()
        child.communicate(timeout=5)
    settled = tool(call['tool'], **call['args'])
    assert settled['status'] == 'not_started' and not solve_queue.list_jobs()


def test_changed_fenced_proof_cannot_reuse_a_past_resolution(baseline):
    eid, _session, _armed, call = stopped_before_effects()
    assert tool(call['tool'], **call['args'])['status'] == 'not_started'
    path = baseline[2] / 'experiment_execution' / 'submission_receipts' / (eid + '.json')
    data = json.loads(path.read_text())
    data['phase'] = 'effect_started'
    path.write_text(json.dumps(data))
    with pytest.raises(HTTPException) as error:
        tool(call['tool'], **call['args'])
    assert error.value.detail['error_kind'] == 'experiment_checkpoint_unreadable'
    assert not solve_queue.list_jobs()


@pytest.mark.parametrize('decision', ['approve', 'deny', 'stale'])
def test_authenticated_http_reconciliation_requires_current_ordinary_confirmation(baseline, client, monkeypatch, decision):
    from services import chat_service
    from tests.assurance_acceptance.support import Harness, parse_sse, text_turn, tool_turn
    eid, _session, _armed, call = stopped_before_effects()
    sid = 'reconcile-http-' + decision
    harness = Harness(client, monkeypatch)
    harness.install([tool_turn(call['tool'], call['args']), text_turn('Receipt inspected.')])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/chat/stream', json={
                'session_id': sid, 'message': 'reconcile_experiment for this exact receipt',
                'ui_context': {'ui_mode': 'expert'}})
            deadline = time.monotonic() + 10
            token = None
            while time.monotonic() < deadline and not pending.done():
                session = chat_service.get_session(sid)
                if session:
                    with session._lock:
                        token = next((k for k, c in session.pending_confirmations.items()
                                      if c.tool_name == 'reconcile_experiment'), None)
                if token:
                    break
                time.sleep(0.02)
            if token is None:
                client.post(f'/api/chat/{sid}/abort')
            assert token, 'The ordinary destructive confirmation was not issued'
            if decision == 'stale':
                tool('cancel_experiment', experiment_id=eid)
            response = client.post(f'/api/chat/{sid}/confirm', json={
                'token': token, 'decision': 'deny' if decision == 'deny' else 'approve'})
            assert response.status_code == 200
            completed = pending.result(timeout=10)
        assert completed.status_code == 200
        frames = parse_sse(completed.text)
        assert any(event == 'tool_pending_confirmation' for event, _ in frames)
        errors = [p for event, p in frames if event in {'error', 'tool_error'}]
        if decision == 'approve':
            assert not errors, errors
            outputs = [p['result'] for event, p in frames if event == 'tool_result' and p.get('tool_name') == call['tool']]
            assert outputs and outputs[0]['status'] == 'not_started'
        else:
            assert any(p['error_kind'] == ('confirmation_denied' if decision == 'deny'
                                         else 'experiment_reconciliation_stale') for p in errors), errors
            assert tool('refresh_experiment', experiment_id=eid)['status'] == 'uncertain'
        assert client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': 'approve'}).status_code == 404
        assert not solve_queue.list_jobs()
    finally:
        chat_service.drop_session(sid)


def test_lost_native_return_recovers_exact_completed_case_without_resubmitting(baseline):
    _ctx, _project, root = baseline
    before = (root / 'network.nc').read_bytes()
    eid = prepared()
    session, armed = arm(eid)
    actual = {}

    def lost_return(**args):
        actual.update(chat_tools.run_sensitivity_sweep(**args)['cases'][0])
        raise RuntimeError('native return lost after queue admission')

    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], lost_return)
    assert chat_tools.wait_for_job(actual['job']['id'], 25)['completed']
    uncertain = tool('refresh_experiment', experiment_id=eid)
    assert uncertain['status'] == 'uncertain' and uncertain['job_id'] is None
    call = uncertain['reconciliation']['next_call']
    settled = tool(call['tool'], **call['args'])
    assert settled['status'] == 'completed' and settled['result_available']
    assert settled['reconciliation']['outcome'] == 'receipt_recovered'
    assert settled['case_project_id'] == actual['project_id']
    assert settled['job_id'] == actual['job']['id']
    assert settled['objective'] == 200 and settled['objective_unit'] == 'EUR'
    assert len(solve_queue.list_jobs()) == 1
    assert (root / 'network.nc').read_bytes() == before


def test_same_child_later_job_cannot_substitute_for_original_submission(baseline):
    eid = prepared()
    session, armed = arm(eid)
    actual = {}
    def lost(**args):
        actual.update(chat_tools.run_sensitivity_sweep(**args)['cases'][0])
        raise RuntimeError('return lost')
    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], lost)
    assert chat_tools.wait_for_job(actual['job']['id'], 25)['completed']
    # A separate user action creates a real, same-input second native job.
    other = chat_tools.solve_queue_enqueue(actual['project_id'])
    assert other['id'] != actual['job']['id']
    assert chat_tools.wait_for_job(other['id'], 25)['completed']
    path = baseline[2] / 'experiment_execution' / 'submission_receipts' / (eid + '.json')
    proof = json.loads(path.read_text())
    proof['job_id'] = other['id']
    path.write_text(json.dumps(proof))
    call = tool('refresh_experiment', experiment_id=eid)['reconciliation']['next_call']
    result = tool(call['tool'], **call['args'])
    assert result['reconciliation']['outcome'] == 'unresolved'
    assert not result['result_available'] and 'objective' not in result
    assert len(solve_queue.list_jobs()) == 2


def test_missing_handler_close_proof_is_not_a_no_effect_verdict(baseline):
    eid, _session, _armed, _call = stopped_before_effects()
    path = baseline[2] / 'experiment_execution' / 'submission_receipts' / (eid + '.json')
    data = json.loads(path.read_text())
    data['finished'] = False  # Lost final checkpoint / crash before close.
    path.write_text(json.dumps(data))
    call = tool('refresh_experiment', experiment_id=eid)['reconciliation']['next_call']
    result = tool(call['tool'], **call['args'])
    assert result['reconciliation']['outcome'] == 'unresolved'
    assert result['status'] == 'uncertain' and result['executed'] is None
    assert not result['result_available'] and not solve_queue.list_jobs()


@pytest.mark.parametrize('exception', [TimeoutError, OSError])
def test_native_failure_is_not_misreported_as_a_live_fence(baseline, exception):
    eid = prepared()
    session, armed = arm(eid)
    def failed(**_):
        raise exception('native failure')
    with pytest.raises(exception):
        invoke(session, armed['next_call'], failed)
    call = tool('refresh_experiment', experiment_id=eid)['reconciliation']['next_call']
    assert tool(call['tool'], **call['args'])['status'] == 'not_started'


@pytest.mark.parametrize('damage', ['precision', 'evidence', 'native_origin'])
def test_lost_return_with_changed_native_sources_withholds_values(baseline, damage):
    from services import project_registry, workflow_tools as wt, run_evidence
    eid = prepared()
    session, armed = arm(eid)
    actual = {}
    def lost(**args):
        actual.update(chat_tools.run_sensitivity_sweep(**args)['cases'][0])
        raise RuntimeError('return lost')
    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], lost)
    assert chat_tools.wait_for_job(actual['job']['id'], 25)['completed']
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, actual['project_id'])
        root = project_registry.project_dir(child)
        if damage == 'precision':
            ctx = wt._load_context(child)
            ctx.network.generators.at['G1', 'p_nom'] += 1e-11
            ctx.network.export_to_netcdf(root / 'network.nc')
        elif damage == 'evidence':
            run = run_evidence.queue_run_id(actual['job']['id'])
            (root / 'run_evidence' / run / 'sections/summary.json').write_text('{}')
        else:
            path = root / 'experiment_submission.json'
            data = json.loads(path.read_text())
            data['attempt_id'] = '0' * 32
            path.write_text(json.dumps(data))
    call = tool('refresh_experiment', experiment_id=eid)['reconciliation']['next_call']
    result = tool(call['tool'], **call['args'])
    assert result['reconciliation']['outcome'] == 'unresolved'
    assert not result['result_available'] and 'objective' not in result
    assert len(solve_queue.list_jobs()) == 1
