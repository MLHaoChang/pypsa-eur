"""Durable observations are metadata, never recovered execution authority."""
import json
import os
import subprocess
import sys
import threading

import pytest
from fastapi import HTTPException

from harness import job_adapters as ja


@pytest.fixture
def origin(client, seeded_identity, session_ctx, tmp_path, monkeypatch):
    monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(tmp_path / 'app'))
    assert client.post('/api/projects/History', params={'force': True, 'rebind': True}).status_code == 200
    ja._reset_for_tests()
    yield session_ctx(client), str(seeded_identity['user_id']), tmp_path / 'app'
    ja._reset_for_tests()


def start(ctx, owner, family):
    args = dict(tool_use_id='history-call', owner_user_id=owner, project_uuid=ctx.project_uuid, ctx=ctx)
    with ctx.solver_state_lock:
        record = dict(status='running', thread=threading.Thread(), started_at=1)
        if family == 'foreground':
            ctx.solver_state.update(record)
            return ja.register_foreground_solve(tool_name='run_simulation', kind='lopf', **args)
        if family == 'study':
            ctx.solver_state['fmea_sweep'] = record
            return ja.register_study_job(study_key='fmea_sweep', tool_name='run_fmea_sweep', **args)
        ctx.solver_state['report_job'] = record
        return ja.register_report_job(tool_name='generate_report', report_id='report-private', **args)


def native_record(ctx, family):
    return ctx.solver_state if family == 'foreground' else ctx.solver_state[
        'fmea_sweep' if family == 'study' else 'report_job']


@pytest.mark.parametrize('family', ['foreground', 'study', 'report'])
def test_registration_survives_loss_without_inventing_completion(client, origin, family):
    ctx, owner, _ = origin
    job = start(ctx, owner, family)
    # No observer was open before the process-local registries were lost.
    ja._reset_for_tests()
    native_record(ctx, family).update(status='completed' if family == 'foreground' else 'done')
    response = client.get('/api/chat/activity', params={'filter': 'needs_you'})
    assert response.status_code == 200, response.text
    row = next(r for r in response.json()['items'] if r['source_id'] == job)
    assert row['status'] == 'needs_review'
    assert row['native_status'] == 'interrupted'
    assert row['observation'] == 'retained'
    assert row['evidence_available'] is False
    assert 'last observed' in row['reason'].lower()
    assert row['observed_at'] > 0
    assert 'not restarted' in row['reason'].lower()


@pytest.mark.parametrize(('family', 'terminal'), [('foreground', 'completed'), ('study', 'failed'), ('report', 'aborted')])
def test_observed_terminal_survives_replacement_and_registry_loss(client, origin, family, terminal):
    ctx, owner, app = origin
    job = start(ctx, owner, family)
    native_record(ctx, family).update(status=terminal, objective=123456789, error='private error', section='private section')
    first = client.get('/api/chat/activity', params={'filter': 'completed'}).json()
    assert first['items'][0]['source_id'] == job
    start(ctx, owner, family)  # Same native slot, different worker.
    ja._reset_for_tests()
    result = client.get('/api/chat/activity', params={'filter': 'completed', 'cursor': first['cursor']})
    assert result.status_code == 200, result.text
    row = next(r for r in result.json()['items'] if r['source_id'] == job)
    assert row['native_status'] == terminal
    assert row['observation'] == 'retained'
    assert row['evidence_available'] is False
    assert row['evidence_status'] == 'not_checked'
    assert 'retained' in row['reason'].lower()
    stored = ''.join(p.read_text() for p in app.rglob('*.json'))
    assert '123456789' not in stored and 'private error' not in stored
    assert 'private section' not in stored and 'report-private' not in stored
    # Durable Activity records cannot resurrect a numerical wait adapter.
    with pytest.raises(HTTPException) as exc:
        ja.wait_foreground_job(job, 0, actor_id=owner, stop=threading.Event()) if family == 'foreground' else (
            ja.wait_study_job(job, 0, actor_id=owner, stop=threading.Event()) if family == 'study' else
            ja.wait_report_job(job, 0, actor_id=owner, stop=threading.Event()))
    assert exc.value.status_code == 404


def test_history_is_owner_project_scoped_after_navigation(client, other_org_client, origin):
    ctx, owner, _ = origin
    project = str(ctx.project_uuid)
    job = start(ctx, owner, 'foreground')
    ja._reset_for_tests()
    assert other_org_client.get('/api/chat/activity', params={'project_id': project}).status_code == 404
    assert client.post('/api/projects/Other', params={'force': True, 'rebind': True}).status_code == 200
    assert client.get('/api/chat/activity').json()['items'] == []
    original = client.get('/api/chat/activity', params={'project_id': project}).json()
    assert [r['source_id'] for r in original['items']] == [job]


@pytest.mark.parametrize('damage', ['malformed', 'oversized', 'symlink', 'foreign_scope', 'extra_fields'])
def test_untrusted_history_refuses_without_rewriting_or_leaking(client, origin, damage, tmp_path):
    ctx, owner, app = origin
    start(ctx, owner, 'foreground')
    path = next(app.rglob('observations.json'))
    data = json.loads(path.read_text())
    if damage == 'malformed':
        path.write_text('{private malformed')
    elif damage == 'oversized':
        path.write_text(' ' * 1000001)
    elif damage == 'symlink':
        target = tmp_path / 'private.json'
        target.write_text(json.dumps(data))
        path.unlink()
        path.symlink_to(target)
    elif damage == 'foreign_scope':
        data['owner_id'] = 'another-owner'
        path.write_text(json.dumps(data))
    else:
        data['records'][0]['approval_token'] = 'private approval'
        path.write_text(json.dumps(data))
    before = path.read_bytes()
    ja._reset_for_tests()
    result = client.get('/api/chat/activity')
    assert result.status_code == 200, result.text
    assert result.json()['items'] == []
    assert 'activity_history_unavailable' in result.json()['warnings']
    assert 'private' not in result.text
    start(ctx, owner, 'foreground')
    client.get('/api/chat/activity')
    assert path.read_bytes() == before


def test_clean_process_can_read_retained_origin_without_native_registries(client, origin):
    from pathlib import Path
    ctx, owner, app = origin
    job = start(ctx, owner, 'foreground')
    script = ('import json,sys; from harness.activity_history import project_rows; '
              'print(json.dumps(project_rows(sys.argv[1], sys.argv[2], [])))')
    env = {**os.environ, 'PYPSAGUI_APP_DATA_DIR': str(app),
           'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    # This subprocess imports no application singleton/session or old cache.
    result = subprocess.run([sys.executable, '-c', script, owner, str(ctx.project_uuid)],
                            env=env, text=True, capture_output=True, timeout=10, check=True)
    rows, warnings = json.loads(result.stdout)
    assert warnings == []
    assert rows[0]['source_id'] == job
    assert rows[0]['native_status'] == 'interrupted'
    assert rows[0]['observation'] == 'retained'
    assert rows[0]['observed_at'] > 0


def test_concurrent_process_writes_keep_both_origins(client, origin):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import time
    import uuid
    ctx, owner, app = origin
    first = start(ctx, owner, 'foreground')
    ids = ['fg:' + uuid.uuid4().hex for _ in range(2)]
    rows = [dict(source_id=job, family='foreground_simulation', tool_name='run_simulation',
                 native_status='running', registered_at=time.time()) for job in ids]
    script = ('import json,sys; from harness.activity_history import remember; '
              'remember(sys.argv[1],sys.argv[2],json.loads(sys.argv[3]))')
    env = {**os.environ, 'PYPSAGUI_APP_DATA_DIR': str(app),
           'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    def write(row):
        return subprocess.run([sys.executable, '-c', script, owner, str(ctx.project_uuid), json.dumps(row)],
                              env=env, text=True, capture_output=True, timeout=15, check=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, rows))
    ja._reset_for_tests()
    response = client.get('/api/chat/activity')
    assert response.status_code == 200, response.text
    assert {r['source_id'] for r in response.json()['items']} == {first, *ids}
    assert all(r['status'] == 'needs_review' for r in response.json()['items'])


def test_newest_origin_bound_is_stable_across_polls(client, origin, monkeypatch):
    from harness import activity_history as history
    ctx, owner, app = origin
    monkeypatch.setattr(history, 'MAX_RECORDS', 2)
    jobs = [start(ctx, owner, 'foreground') for _ in range(3)]
    first = client.get('/api/chat/activity').json()
    second = client.get('/api/chat/activity', params={'cursor': first['cursor']}).json()
    assert first['cursor'] == second['cursor']
    assert len(first['items']) == 3  # The live view is not the durable cap.
    assert 'activity_history_truncated' in first['warnings']
    path = next(app.rglob('observations.json'))
    stored = json.loads(path.read_text())['records']
    assert {r['source_id'] for r in stored} == set(jobs[1:])
    ja._reset_for_tests()
    recovered = client.get('/api/chat/activity').json()
    assert {r['source_id'] for r in recovered['items']} == set(jobs[1:])
    assert all(r['status'] == 'needs_review' for r in recovered['items'])
    assert 'activity_history_truncated' in recovered['warnings']


def test_expired_observations_are_not_reissued_or_presented_as_current(client, origin):
    from harness import activity_history as history
    ctx, owner, app = origin
    start(ctx, owner, 'foreground')
    path = next(app.rglob('observations.json'))
    data = json.loads(path.read_text())
    for record in data['records']:
        record['created_at'] -= history.RETENTION_SECONDS + 1
        record['observed_at'] -= history.RETENTION_SECONDS + 1
    path.write_text(json.dumps(data))
    ja._reset_for_tests()
    response = client.get('/api/chat/activity')
    assert response.status_code == 200, response.text
    assert response.json()['items'] == []
    assert json.loads(path.read_text())['records'] == []


def test_concurrent_terminal_read_cannot_be_regressed_by_late_running_read(client, origin):
    from harness import activity_history as history
    ctx, owner, _ = origin
    job = start(ctx, owner, 'foreground')
    late = ja.activity_jobs(ctx, owner_id=owner, project_id=str(ctx.project_uuid))
    ctx.solver_state['status'] = 'completed'
    assert client.get('/api/chat/activity').json()['items'][0]['native_status'] == 'completed'
    history.project_rows(owner, str(ctx.project_uuid), late)
    ja._reset_for_tests()
    rows = client.get('/api/chat/activity', params={'filter': 'completed'}).json()['items']
    assert [r['source_id'] for r in rows] == [job]
    assert rows[0]['native_status'] == 'completed'


def test_storage_write_failure_preserves_native_read_and_discloses_loss(client, origin, monkeypatch):
    from harness import activity_history as history
    ctx, owner, _ = origin
    job = start(ctx, owner, 'foreground')
    ctx.solver_state['status'] = 'failed'
    def fail(*_args):
        raise OSError('private filesystem location')
    monkeypatch.setattr(history, '_write', fail)
    response = client.get('/api/chat/activity')
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['source_id'] == job
    assert response.json()['items'][0]['native_status'] == 'failed'
    assert response.json()['items'][0]['observation'] == 'live'
    assert 'activity_history_unavailable' in response.json()['warnings']
    assert 'private filesystem' not in response.text
    ja._reset_for_tests()
    rows = client.get('/api/chat/activity').json()['items']
    assert rows[0]['status'] == 'needs_review'  # Failed status was never saved.


@pytest.mark.parametrize('family', ['foreground', 'study', 'report'])
def test_native_wait_retains_terminal_metadata_without_an_activity_observer(client, origin, family):
    from services.pypsa_service import PyPSAService
    ctx, owner, _ = origin
    job = start(ctx, owner, family)
    terminal = 'completed' if family == 'foreground' else 'done'
    native_record(ctx, family).update(status=terminal)
    binding = PyPSAService._request_ctx.set(ctx)
    try:
        wait = {'foreground': ja.wait_foreground_job, 'study': ja.wait_study_job,
                'report': ja.wait_report_job}[family]
        result = wait(job, 0, actor_id=owner, stop=threading.Event())
        assert result['completed'] is True and result['terminal'] is True
    finally:
        PyPSAService._request_ctx.reset(binding)
    ja._reset_for_tests()
    rows = client.get('/api/chat/activity', params={'filter': 'completed'}).json()['items']
    assert [r['source_id'] for r in rows] == [job]
    assert rows[0]['native_status'] == terminal
    assert rows[0]['observation'] == 'retained'

