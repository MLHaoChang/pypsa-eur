"""CP-04 public read model: ACLs, exact job identity and cursor recovery."""
import threading
import time

import pytest

from harness import job_adapters as ja


@pytest.fixture
def project(client, seeded_identity, session_ctx):
    assert client.post('/api/projects/Activity', params={'force': True, 'rebind': True}).status_code == 200
    ctx = session_ctx(client)
    ja._reset_for_tests()
    yield ctx, str(seeded_identity['user_id'])
    ja._reset_for_tests()


def register(ctx, owner, family):
    args = dict(tool_use_id='tu-' + family, owner_user_id=owner, project_uuid=ctx.project_uuid, ctx=ctx)
    with ctx.solver_state_lock:
        if family == 'foreground':
            ctx.solver_state.update(status='running', thread=threading.Thread(), kind='lopf')
            return ja.register_foreground_solve(tool_name='run_simulation', kind='lopf', **args)
        if family == 'study':
            ctx.solver_state['fmea_sweep'] = {'status': 'running', 'iterations': [], 'started_at': time.time()}
            return ja.register_study_job(study_key='fmea_sweep', tool_name='run_fmea_sweep', **args)
        ctx.solver_state['report_job'] = {'status': 'running', 'report_id': 'r1', 'started_at': time.time()}
        return ja.register_report_job(tool_name='generate_report', report_id='r1', **args)


def test_activity_requires_authentication(anon_client):
    assert anon_client.get('/api/chat/activity').status_code == 401


def test_owned_families_and_replacement_never_borrow_success(client, project):
    ctx, owner = project
    ids = [register(ctx, owner, family) for family in ('foreground', 'study', 'report')]
    foreign = register(ctx, 'another-owner', 'study')
    # Register our own study again after the foreign one replaced its source.
    ids[1] = register(ctx, owner, 'study')
    got = client.get('/api/chat/activity').json()
    rows = {r['source_id']: r for r in got['items']}
    assert set(ids) <= set(rows)
    assert foreign not in rows
    assert all(rows[j]['native_status'] == 'running' for j in ids)
    with ctx.solver_state_lock:
        ctx.solver_state.update(status='completed', thread=threading.Thread())
        ctx.solver_state['fmea_sweep'] = {'status': 'done'}
        ctx.solver_state['report_job'] = {'status': 'done', 'report_id': 'r1'}
    rows = client.get('/api/chat/activity').json()['items']
    assert all(r['status'] == 'unavailable' for r in rows)
    assert all(r['native_status'] == 'unknown' for r in rows)
    assert all(r['evidence_available'] is False for r in rows)


def test_native_completion_is_not_erased_or_invented(client, project):
    ctx, owner = project
    job = register(ctx, owner, 'foreground')
    with ctx.solver_state_lock:
        ctx.solver_state.update(status='failed', condition='infeasible')
    response = client.get('/api/chat/activity', params={'filter': 'completed'})
    assert response.status_code == 200, response.text
    row = response.json()['items'][0]
    assert row['source_id'] == job
    assert row['status'] == 'failed'
    assert row['native_status'] == 'failed'
    assert row['evidence_available'] is False


def test_cross_owner_and_project_reads_fail_closed(client, other_org_client, project):
    ctx, owner = project
    register(ctx, owner, 'foreground')
    own = client.get('/api/chat/activity').json()
    foreign = other_org_client.get('/api/chat/activity', params={'project_id': ctx.project_uuid})
    assert foreign.status_code == 404
    assert other_org_client.get('/api/chat/activity', params={'cursor': own['cursor']}).status_code in (404, 409)
    assert client.get('/api/chat/activity', params={'project_id': 'not-a-project'}).status_code == 404


def test_cursor_is_stable_scoped_and_requires_resnapshot_after_loss(client, project):
    ctx, owner = project
    job = register(ctx, owner, 'foreground')
    first = client.get('/api/chat/activity').json()
    duplicate = client.get('/api/chat/activity', params={'cursor': first['cursor']}).json()
    assert duplicate['cursor'] == first['cursor']
    assert duplicate['revision'] == first['revision']
    assert duplicate['resnapshot_required'] is False
    with ctx.solver_state_lock:
        ctx.solver_state['status'] = 'completed'
    changed = client.get('/api/chat/activity', params={'cursor': first['cursor']}).json()
    assert changed['revision'] > first['revision']
    assert changed['items'][0]['source_id'] == job
    assert changed['items'][0]['status'] == 'completed'
    lost = client.get('/api/chat/activity', params={'cursor': 'missing:1'}).json()
    assert lost['resnapshot_required'] is True
    assert lost['items'] == changed['items']


def test_page_bounds_and_snapshot_pagination(client, project):
    ctx, owner = project
    for _ in range(3):
        register(ctx, owner, 'foreground')
    first = client.get('/api/chat/activity', params={'limit': 2}).json()
    assert len(first['items']) == 2
    assert first['next_page']
    page = client.get('/api/chat/activity', params={'limit': 2, 'page_token': first['next_page']}).json()
    assert len(page['items']) == 1
    assert not set(r['id'] for r in first['items']) & set(r['id'] for r in page['items'])
    assert client.get('/api/chat/activity', params={'limit': 51}).status_code == 422
    assert client.get('/api/chat/activity', params={'page_token': first['next_page'], 'filter': 'running'}).status_code == 409


def test_persisted_queue_and_gridspine_recover_without_readmission(client, project):
    import uuid
    from services import solve_job_store
    from services.solve_queue import SolveJob, solve_queue
    ctx, owner = project
    saved = []
    for kind in ('solve', 'gridspine'):
        job = SolveJob(id=uuid.uuid4(), project_id=ctx.loaded_project, project_key=ctx.registry_key,
                       storage_dir=ctx.storage_dir, kind=kind)
        solve_job_store.record_enqueued(job, enqueued_by_user_id=uuid.UUID(owner), solver_config_json=None)
        job.status = 'interrupted'
        assert solve_job_store.record_status(job)
        saved.append(str(job.id))
    response = client.get('/api/chat/activity', params={'filter': 'needs_you'})
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert {r['source_id'] for r in rows} == set(saved)
    assert {r['family'] for r in rows} == {'solve_queue', 'gridspine'}
    assert all(r['status'] == 'needs_review' for r in rows)
    assert solve_queue.list_jobs() == []


def test_task_checkpoints_are_bounded_metadata_not_arguments_or_results(client, project):
    import json
    from pathlib import Path
    import uuid
    ctx, owner = project
    directory = Path(ctx.storage_dir) / 'assistant_tasks'
    directory.mkdir()
    task_id = str(uuid.uuid4())
    task = dict(task_id=task_id, project_id=ctx.project_uuid, owner_id=owner, title='Inspect uncertain write',
                status='needs_review', steps=[dict(id='write', tool='update_component', status='uncertain',
                args={'private': 'DO NOT EXPOSE'}, result={'objective': 987654321})])
    (directory / f'{task_id}.json').write_text(json.dumps(task))
    (directory / 'malformed.json').write_text('{bad')
    response = client.get('/api/chat/activity', params={'filter': 'needs_you'})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['source_id'] == task_id
    assert 'DO NOT EXPOSE' not in response.text and '987654321' not in response.text
    assert 'task_checkpoint_unavailable' in response.json()['warnings']


def test_confirmation_expiry_is_observed_without_consuming_or_approving(client, project):
    import uuid
    from harness import session as sessions
    from services.pypsa_service import PyPSAService
    ctx, owner = project
    session = sessions.get_or_create_session('activity-expiry-' + uuid.uuid4().hex, owner_user_id=owner)
    binding = PyPSAService._request_ctx.set(ctx)
    try:
        pc = session.issue_confirmation(tool_name='update_component', args={'value': 'private'}, safety_tier='write')
    finally:
        PyPSAService._request_ctx.reset(binding)
    response = client.get('/api/chat/activity', params={'filter': 'needs_you'})
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert len(rows) == 1 and rows[0]['session_id'] == session.session_id
    assert pc.token not in response.text and 'private' not in response.text
    assert pc.token in session.pending_confirmations
    pc.expires_at = 0
    assert client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items'] == []
    assert not session.confirmation_decisions
    sessions.drop_session(session.session_id)


def test_live_native_worker_does_not_block_reads_and_closing_observer_does_not_cancel(client, project, monkeypatch):
    from routers import simulation
    ctx, owner = project
    started, release = threading.Event(), threading.Event()

    def slow_solver(config, network, lock, stop, logs, **kwargs):
        started.set()
        assert release.wait(10)
        return 'aborted' if stop.is_set() else 'warning', 'fixture completion'

    monkeypatch.setattr(simulation, 'run_simulation', slow_solver)
    # Native start admission still performs its own checks. A simple saved
    # one-bus network meets prerequisites; the expensive solver alone is fake.
    ctx.network.add('Bus', 'b')
    ctx.network.add('Generator', 'g', bus='b', p_nom=1)
    ctx.network.add('Load', 'l', bus='b', p_set=0.5)
    try:
        response = client.post('/api/simulation/run')
        assert response.status_code == 200, response.text
        assert started.wait(2)
        job = register_existing(ctx, owner)
        before = time.monotonic()
        observed = client.get('/api/chat/activity')
        assert observed.status_code == 200, observed.text
        assert time.monotonic() - before < 2
        row = next(r for r in observed.json()['items'] if r['source_id'] == job)
        assert row['native_status'] == 'running'
        assert ctx.solver_state['thread'].is_alive()
        assert not ctx.solver_state['stop_event'].is_set()
        assert client.get('/api/network/buses').status_code == 200
    finally:
        release.set()
        worker = ctx.solver_state.get('thread')
        if worker:
            worker.join(5)
    row = next(r for r in client.get('/api/chat/activity').json()['items'] if r['source_id'] == job)
    assert row['native_status'] == 'failed'


def register_existing(ctx, owner):
    return ja.register_foreground_solve(tool_name='run_simulation', tool_use_id='real-native-thread',
        owner_user_id=owner, project_uuid=ctx.project_uuid, kind=ctx.solver_state.get('kind'), ctx=ctx)


def test_needs_you_filter_is_applied_before_the_result_bound(client, project, monkeypatch):
    from services import assistant_activity as activity
    ctx, owner = project
    job = register(ctx, owner, 'foreground')
    with ctx.solver_state_lock:
        ctx.solver_state['thread'] = None  # Unestablished origin needs attention.
    completed = [activity._row(ctx.project_uuid, 'assistant_task', str(i), 'Completed task', 'completed')
                 for i in range(500)]
    monkeypatch.setattr(activity, '_tasks', lambda *args: (completed, []))
    response = client.get('/api/chat/activity', params={'filter': 'needs_you'})
    assert response.status_code == 200, response.text
    assert any(row['source_id'] == job for row in response.json()['items'])


def test_older_collector_cannot_overwrite_a_newer_snapshot(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import uuid
    from services import assistant_activity as activity
    user, project = SimpleNamespace(id='owner'), SimpleNamespace(id=uuid.uuid4())
    entered, release = threading.Event(), threading.Event()
    calls = []

    def collect(**kwargs):
        calls.append(1)
        state = 'running' if len(calls) == 1 else 'failed'
        if state == 'running':
            entered.set()
            assert release.wait(5)
        return [activity._row(str(project.id), 'solve_queue', 'one', 'Solve', state)], []

    monkeypatch.setattr(activity, 'collect', collect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(activity.snapshot, db=None, user=user, project=project)
        assert entered.wait(2)
        try:
            latest = activity.snapshot(db=None, user=user, project=project)
        finally:
            release.set()
        late = first.result(3)
    assert late['cursor'] == latest['cursor']
    assert late['items'][0]['status'] == 'failed'


@pytest.mark.parametrize('family', ['foreground', 'study', 'report'])
def test_legacy_wait_cannot_certify_a_replaced_native_worker(project, family):
    ctx, owner = project
    job = register(ctx, owner, family)
    # Bind this direct adapter call to the same request context the HTTP path uses.
    from services.pypsa_service import PyPSAService
    token = PyPSAService._request_ctx.set(ctx)
    try:
        with ctx.solver_state_lock:
            if family == 'foreground':
                ctx.solver_state.update(status='completed', thread=threading.Thread())
            else:
                ctx.solver_state['fmea_sweep' if family == 'study' else 'report_job'] = {'status': 'done'}
        wait = {'foreground': ja.wait_foreground_job, 'study': ja.wait_study_job, 'report': ja.wait_report_job}[family]
        observed = wait(job, 0, actor_id=owner, stop=threading.Event())
        assert observed['completed'] is False
        assert observed['terminal'] is False
        assert observed['job']['status'] == 'unknown'
        assert observed['timed_out'] is True
    finally:
        PyPSAService._request_ctx.reset(token)


def test_native_http_pending_approval_is_visible_without_legacy_context_slot(client, project, monkeypatch):
    import concurrent.futures
    import uuid
    from harness import session as sessions
    ctx, _owner = project
    assert ctx.chat_state.session is None  # This field is a dormant Phase-0 stub.
    sid = 'activity-http-' + uuid.uuid4().hex
    monkeypatch.setattr(sessions, 'CONFIRMATION_TTL_SECONDS', 10)
    token = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat/stream', json={
            'session_id': sid,
            'script': [{'type': 'tool_call', 'tool_use_id': 'pending-native',
                        'name': 'delete_component', 'args': {'name': 'private-preview'},
                        'safety_tier': 'destructive', 'result': {'deleted': True}}],
        })
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                live = sessions.get_session(sid)
                if live:
                    with live._lock:
                        if live.pending_confirmations:
                            token = next(iter(live.pending_confirmations))
                            break
                if future.done():
                    pytest.fail('Stream ended before ordinary confirmation: ' + future.result().text)
                time.sleep(0.01)
            assert token, 'Native HTTP stream never issued its confirmation'
            response = client.get('/api/chat/activity', params={'filter': 'needs_you'})
            assert response.status_code == 200, response.text
            rows = response.json()['items']
            assert any(row.get('session_id') == sid and row['family'] == 'confirmation' for row in rows)
            assert token not in response.text and 'private-preview' not in response.text
            assert ctx.chat_state.session is None
        finally:
            if token:
                denied = client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': 'deny'})
                assert denied.status_code == 200, denied.text
            elif sessions.get_session(sid):
                sessions.get_session(sid).abort_event.set()
            result = future.result(timeout=12)
            sessions.drop_session(sid)
        assert result.status_code == 200, result.text
        assert 'tool_running' not in result.text
        assert client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items'] == []


def test_confirmation_origin_stays_with_its_project_after_navigation(client, project):
    import uuid
    from harness import session as sessions
    from services.pypsa_service import PyPSAService
    ctx, owner = project
    session = sessions.get_or_create_session('activity-origin-' + uuid.uuid4().hex, owner_user_id=owner)
    binding = PyPSAService._request_ctx.set(ctx)
    try:
        pc = session.issue_confirmation(tool_name='delete_component', args={'private': True}, safety_tier='destructive')
    finally:
        PyPSAService._request_ctx.reset(binding)
    try:
        origin_project = str(ctx.project_uuid)
        assert pc.project_id == origin_project
        assert client.post('/api/projects/Elsewhere', params={'force': True, 'rebind': True}).status_code == 200
        assert client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items'] == []
        original = client.get('/api/chat/activity', params={'filter': 'needs_you', 'project_id': origin_project})
        assert original.status_code == 200, original.text
        assert [row['session_id'] for row in original.json()['items']] == [session.session_id]
        # A legacy/unattested origin is never guessed from the reader's project.
        pc.project_id = None
        assert client.get('/api/chat/activity', params={'project_id': origin_project}).json()['items'] == []
        assert pc.token in session.pending_confirmations
    finally:
        sessions.drop_session(session.session_id)
