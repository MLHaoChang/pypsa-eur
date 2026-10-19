"""A closed Activity panel must not be required to retain native completion."""
# ruff: noqa: F811 -- shared pytest fixtures.
import threading
from contextlib import contextmanager
import json

import pytest

from harness import activity_history, completion_observer as observer, job_adapters as ja
from tests.test_assistant_activity_history import origin  # noqa: F401


def test_native_foreground_completion_is_retained_with_activity_closed(client, origin, monkeypatch):
    from routers import simulation
    ctx, owner, _app = origin
    started, release, saved = threading.Event(), threading.Event(), threading.Event()
    remember = activity_history.remember

    def observed(*args, **kwargs):
        result = remember(*args, **kwargs)
        if args[2]['native_status'] == 'failed':
            saved.set()
        return result

    def solver(config, network, lock, stop, logs, **kwargs):
        started.set()
        assert release.wait(10)
        return 'warning', 'fixture failure'

    monkeypatch.setattr(activity_history, 'remember', observed)
    monkeypatch.setattr(simulation, 'run_simulation', solver)
    ctx.network.add('Bus', 'b')
    ctx.network.add('Generator', 'g', bus='b', p_nom=1)
    ctx.network.add('Load', 'l', bus='b', p_set=0.5)
    worker = None
    try:
        response = client.post('/api/simulation/run')
        assert response.status_code == 200, response.text
        assert started.wait(2)
        worker = ctx.solver_state['thread']
        job = ja.register_foreground_solve(tool_name='run_simulation', tool_use_id='closed-panel',
            owner_user_id=owner, project_uuid=ctx.project_uuid, kind=ctx.solver_state['kind'], ctx=ctx)
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        # No Activity read, native wait or progress/session monitor ran here.
        captured = saved.wait(2)
        ja._reset_for_tests()
        with ctx.solver_state_lock:
            ctx.solver_state['thread'] = None
        response = client.get('/api/chat/activity')
        assert response.status_code == 200, response.text
        row = next(r for r in response.json()['items'] if r['source_id'] == job)
        assert row['native_status'] == 'failed', (captured, row)
        assert row['observation'] == 'retained' and row['evidence_available'] is False
        assert row['evidence_status'] == 'not_checked'
    finally:
        release.set()
        if worker is not None:
            worker.join(5)


@contextmanager
def running(ctx, owner, family='foreground', terminal='completed', publish=True):
    release = threading.Event()
    key = {'foreground': None, 'study': 'fmea_sweep', 'report': 'report_job'}[family]

    def work():
        assert release.wait(10)
        with ctx.solver_state_lock:
            record = ctx.solver_state if key is None else ctx.solver_state.get(key)
            if publish and record.get('thread') is worker:
                record.update(status=terminal, objective=123456789, error='private worker error')

    worker = threading.Thread(target=work)
    with ctx.solver_state_lock:
        record = dict(status='running', thread=worker)
        if key is None:
            ctx.solver_state.update(record)
        else:
            ctx.solver_state[key] = record
    worker.start()
    args = dict(tool_use_id='closed-activity', owner_user_id=owner, project_uuid=ctx.project_uuid, ctx=ctx)
    try:
        if family == 'foreground':
            job = ja.register_foreground_solve(tool_name='run_simulation', kind='lopf', **args)
        elif family == 'study':
            job = ja.register_study_job(study_key=key, tool_name='run_fmea_sweep', **args)
        else:
            job = ja.register_report_job(tool_name='generate_report', report_id='private-report', **args)
        yield job, release, worker
    finally:
        release.set()
        worker.join(5)
        assert not worker.is_alive()


def idle():
    with observer._CONDITION:
        assert observer._CONDITION.wait_for(lambda: not observer._PENDING and observer._RUNNER is None, timeout=5)


def retained(client, job):
    response = client.get('/api/chat/activity')
    assert response.status_code == 200, response.text
    return next(r for r in response.json()['items'] if r['source_id'] == job)


@pytest.mark.parametrize(('family', 'status'), [
    ('foreground', 'completed'), ('foreground', 'failed'), ('foreground', 'aborted'),
    ('study', 'done'), ('study', 'failed'), ('study', 'aborted'),
    ('report', 'done'), ('report', 'failed'), ('report', 'aborted'),
])
def test_closed_panel_retains_each_registered_family_without_values(client, origin, family, status):
    ctx, owner, app = origin
    with running(ctx, owner, family, status) as (job, release, worker):
        release.set()
        worker.join(3)
        idle()
    stored = ''.join(p.read_text() for p in app.rglob('observations.json'))
    assert '123456789' not in stored and 'private' not in stored
    ja._reset_for_tests()
    row = retained(client, job)
    assert row['native_status'] == status and row['observation'] == 'retained'
    assert row['evidence_available'] is False and row['evidence_status'] == 'not_checked'


def test_replaced_slot_cannot_complete_the_old_worker(client, origin):
    ctx, owner, _ = origin
    with running(ctx, owner) as (job, release, worker):
        with ctx.solver_state_lock:
            ctx.solver_state.update(status='completed', thread=threading.Thread())
        idle()
        assert worker.is_alive() and not release.is_set()
    ja._reset_for_tests()
    row = retained(client, job)
    assert row['native_status'] == 'unknown' and row['last_observed_status'] == 'running'


def test_exited_worker_without_terminal_status_is_needs_review(client, origin):
    ctx, owner, _ = origin
    with running(ctx, owner, publish=False) as (job, release, worker):
        release.set()
        worker.join(3)
        idle()
        assert ctx.solver_state['status'] == 'running'  # Observer did not mutate native state.
    row = retained(client, job)
    assert row['native_status'] == 'unknown' and row['status'] == 'needs_review'
    assert row['last_observed_status'] == 'running'


def test_journal_failure_keeps_work_and_uncertainty_visible(client, origin, monkeypatch):
    ctx, owner, _ = origin
    with running(ctx, owner, terminal='failed') as (job, release, worker):
        def fail(*a, **kw):
            raise OSError('private write failure')
        monkeypatch.setattr(activity_history, '_write', fail)
        release.set()
        worker.join(3)
        idle()
        assert ctx.solver_state['status'] == 'failed'
        with ctx.solver_state_lock:
            ctx.solver_state['thread'] = None
        response = client.get('/api/chat/activity')
        assert response.status_code == 200
        assert 'activity_completion_observation_incomplete' in response.json()['warnings']
        assert 'private' not in response.text
        row = next(r for r in response.json()['items'] if r['source_id'] == job)
        assert row['native_status'] == 'unknown' and row['evidence_available'] is False


def test_observer_start_failure_cannot_break_job_registration(client, origin, monkeypatch):
    ctx, owner, _ = origin
    def fail(*a, **kw):
        raise OSError('private watcher failure')
    monkeypatch.setattr(observer, 'watch', fail)
    with running(ctx, owner) as (job, release, worker):
        assert job and worker.is_alive() and not release.is_set()
        response = client.get('/api/chat/activity')
        assert response.status_code == 200 and 'private' not in response.text
        assert 'activity_completion_observation_incomplete' in response.json()['warnings']


def test_expiry_does_not_stop_or_certify_work(client, origin, monkeypatch):
    ctx, owner, _ = origin
    monkeypatch.setattr(observer, 'MAX_SECONDS', 0)
    with running(ctx, owner) as (job, release, worker):
        idle()
        assert worker.is_alive() and not release.is_set()
        assert observer.warnings(owner, str(ctx.project_uuid))
    ja._reset_for_tests()
    assert retained(client, job)['native_status'] == 'unknown'


def test_store_destination_is_bound_at_registration(client, origin, monkeypatch, tmp_path):
    ctx, owner, app = origin
    with running(ctx, owner) as (job, release, worker):
        other = tmp_path / 'different-app-data'
        monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(other))
        release.set()
        worker.join(3)
        idle()
        assert not other.exists()
        data = json.loads(next(app.rglob('observations.json')).read_text())
        assert next(r for r in data['records'] if r['source_id'] == job)['native_status'] == 'completed'
        monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(app))
    ja._reset_for_tests()
    assert retained(client, job)['native_status'] == 'completed'


def test_cap_is_bounded_and_missing_observation_is_not_success(client, origin, monkeypatch):
    from services.project_context import ProjectContext
    import pypsa
    ctx, owner, _ = origin
    sibling = ProjectContext(network=pypsa.Network(), project_uuid=ctx.project_uuid)
    monkeypatch.setattr(observer, 'MAX_PENDING', 1)
    with running(ctx, owner) as (first, release_first, worker_first):
        with running(sibling, owner) as (second, release_second, worker_second):
            assert observer.warnings(owner, str(ctx.project_uuid))
            response = client.get('/api/chat/activity')
            assert 'activity_completion_observation_incomplete' in response.json()['warnings']
            release_second.set()
            worker_second.join(3)
            idle()
        release_first.set()
        worker_first.join(3)
    ja._reset_for_tests()
    assert retained(client, first)['native_status'] == 'unknown'
    assert retained(client, second)['native_status'] == 'completed'


def test_busy_project_does_not_block_another_completion(client, origin, monkeypatch):
    from services.project_context import ProjectContext
    import pypsa
    ctx, owner, _ = origin
    sibling = ProjectContext(network=pypsa.Network(), project_uuid=ctx.project_uuid)
    saved = threading.Event()
    remember = activity_history.remember
    def observed(*args, **kwargs):
        result = remember(*args, **kwargs)
        if args[2]['native_status'] == 'completed':
            saved.set()
        return result
    monkeypatch.setattr(activity_history, 'remember', observed)
    with running(ctx, owner) as (first, _, _worker):
        with running(sibling, owner) as (second, release, worker):
            with ctx.solver_state_lock:
                release.set()
                worker.join(3)
                assert saved.wait(3), 'A busy lifecycle lock blocked the global observer'
        # The first worker has not published, so its result remains running.
        assert ctx.solver_state['status'] == 'running'
    idle()
    ja._reset_for_tests()
    assert retained(client, second)['native_status'] == 'completed'


def test_rebound_project_cannot_record_a_new_scope_as_old_completion(client, origin):
    import uuid
    ctx, owner, _ = origin
    original = ctx.project_uuid
    with running(ctx, owner) as (job, release, worker):
        with ctx.solver_state_lock:
            ctx.project_uuid = str(uuid.uuid4())
        release.set()
        worker.join(3)
        idle()
        ctx.project_uuid = original
    ja._reset_for_tests()
    assert retained(client, job)['native_status'] == 'unknown'


def test_weak_context_loss_retires_observation_without_stopping_worker(client, origin):
    import gc
    import weakref
    import pypsa
    from services.project_context import ProjectContext
    ctx, owner, _ = origin
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(10))
    worker.start()
    try:
        orphan = ProjectContext(network=pypsa.Network(), project_uuid=ctx.project_uuid)
        orphan.solver_state.update(status='running', thread=worker)
        job = ja.register_foreground_solve(tool_name='run_simulation', tool_use_id='weak-context',
            owner_user_id=owner, project_uuid=ctx.project_uuid, ctx=orphan, kind='lopf')
        reference = weakref.ref(orphan)
        del orphan
        gc.collect()
        idle()
        assert reference() is None and worker.is_alive() and not release.is_set()
    finally:
        release.set()
        worker.join(3)
    ja._reset_for_tests()
    assert retained(client, job)['native_status'] == 'unknown'


def test_concurrent_registrations_preserve_all_terminal_origins(client, origin):
    from concurrent.futures import ThreadPoolExecutor
    from services.project_context import ProjectContext
    import pypsa
    ctx, owner, _ = origin
    contexts = [ProjectContext(network=pypsa.Network(), project_uuid=ctx.project_uuid) for _ in range(4)]
    def enter(context):
        manager = running(context, owner)
        return manager, manager.__enter__()
    with ThreadPoolExecutor(max_workers=4) as pool:
        active = list(pool.map(enter, contexts))
    try:
        for _manager, (_job, release, _worker) in active:
            release.set()
        for _manager, (_job, _release, worker) in active:
            worker.join(3)
        idle()
    finally:
        for manager, _work in active:
            manager.__exit__(None, None, None)
    ja._reset_for_tests()
    for _manager, (job, _release, _worker) in active:
        assert retained(client, job)['native_status'] == 'completed'


def test_slow_journal_does_not_hold_native_locks_or_block_network_reads(client, origin, monkeypatch):
    import time
    ctx, owner, _ = origin
    entered, finish_write = threading.Event(), threading.Event()
    write = activity_history._write
    def slow(*args, **kwargs):
        entered.set()
        assert finish_write.wait(5)
        return write(*args, **kwargs)
    with running(ctx, owner) as (job, release, worker):
        monkeypatch.setattr(activity_history, '_write', slow)
        try:
            release.set()
            worker.join(3)
            assert not worker.is_alive() and entered.wait(3)
            assert ctx.solver_state_lock.acquire(blocking=False)
            ctx.solver_state_lock.release()
            before = time.monotonic()
            response = client.get('/api/network/buses')
            assert response.status_code == 200 and time.monotonic() - before < 2
        finally:
            finish_write.set()
        idle()
    ja._reset_for_tests()
    assert retained(client, job)['native_status'] == 'completed'


def test_corrupt_journal_cannot_be_repaired_by_background_capture(client, origin):
    ctx, owner, app = origin
    with running(ctx, owner) as (_job, release, worker):
        path = next(app.rglob('observations.json'))
        path.write_text('{private corruption')
        before = path.read_bytes()
        release.set()
        worker.join(3)
        idle()
        assert path.read_bytes() == before
        with ctx.solver_state_lock:
            ctx.solver_state['thread'] = None
        response = client.get('/api/chat/activity')
        assert response.status_code == 200 and 'private' not in response.text
        assert 'activity_completion_observation_incomplete' in response.json()['warnings']
        assert 'activity_history_unavailable' in response.json()['warnings']


def test_warning_scope_does_not_follow_project_navigation_or_cross_owners(client, other_org_client, origin, monkeypatch):
    ctx, owner, _ = origin
    original = str(ctx.project_uuid)
    monkeypatch.setattr(observer, 'MAX_SECONDS', 0)
    with running(ctx, owner) as (_job, _release, _worker):
        idle()
    assert client.post('/api/projects/Other', params={'force': True, 'rebind': True}).status_code == 200
    assert 'activity_completion_observation_incomplete' not in client.get('/api/chat/activity').json()['warnings']
    response = client.get('/api/chat/activity', params={'project_id': original})
    assert 'activity_completion_observation_incomplete' in response.json()['warnings']
    assert other_org_client.get('/api/chat/activity', params={'project_id': original}).status_code == 404


def test_native_observer_thread_start_failure_preserves_handoff(client, origin, monkeypatch):
    ctx, owner, _ = origin
    start = threading.Thread.start
    def limited(thread):
        if thread.name == 'native-completion-observer':
            raise RuntimeError('private thread limit')
        return start(thread)
    monkeypatch.setattr(threading.Thread, 'start', limited)
    with running(ctx, owner) as (_job, release, worker):
        assert worker.is_alive() and not release.is_set()
        idle()
        response = client.get('/api/chat/activity')
        assert response.status_code == 200 and 'private' not in response.text
        assert 'activity_completion_observation_incomplete' in response.json()['warnings']


def test_terminal_observation_is_readable_in_fresh_process_without_a_watcher(client, origin):
    import os
    from pathlib import Path
    import subprocess
    import sys
    ctx, owner, app = origin
    with running(ctx, owner) as (job, release, worker):
        release.set()
        worker.join(3)
        idle()
    script = ('import json,sys; from harness import activity_history,completion_observer as o; '
        'rows,warnings=activity_history.project_rows(sys.argv[1],sys.argv[2],[]); '
        'print(json.dumps(dict(rows=rows,warnings=warnings,pending=len(o._PENDING),idle=o._RUNNER is None)))')
    env = {**os.environ, 'PYPSAGUI_APP_DATA_DIR': str(app),
           'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    response = subprocess.run([sys.executable, '-c', script, owner, str(ctx.project_uuid)],
                              env=env, text=True, capture_output=True, timeout=10, check=True)
    data = json.loads(response.stdout)
    assert data['pending'] == 0 and data['idle'] is True and data['warnings'] == []
    row = next(r for r in data['rows'] if r['source_id'] == job)
    assert row['native_status'] == 'completed' and row['observation'] == 'retained'
