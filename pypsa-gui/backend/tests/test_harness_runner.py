"""Study harness issue 02: the server-side runner (`harness/runner.py`).

Runs advance with no chat session open, on the runner-owned session
`run:<task_id>`, through the ordinary dispatch path. Real project, real solve
queue; no provider calls. Cards are decided by the `Runner(decisions=...)`
test seam, which the application's runner does not have."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import threading
import time
from pathlib import Path

import pytest
from harness import attention, eventlog, runner as harness_runner
from harness import session as harness_session
from services import assistant_tasks as tasks, chat_tools, email_service, harness_locks
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, stored, wait_until,
)
from tests.test_harness_run_skeleton import BRIEF, RUN, foreground, site, start, two_buses  # noqa: F401


class Clicks:
    """Cards the user already decided (the pre-clicked test hook): each used once."""

    def __init__(self, decision='approve', refs=None):
        self.decision, self.refs, self.used = decision, refs, []

    def __call__(self, task_id, ref, args_hash):
        if self.refs is not None and ref not in self.refs:
            return None
        self.used.append(ref)
        return self.decision


def task_path(root, task):
    return Path(root) / 'assistant_tasks' / (task['task_id'] + '.json')


def settle(runner, path, task_id, done, limit=80):
    """Tick as the worker would, joining each dispatch and waiting on jobs."""
    for _ in range(limit):
        runner.tick(path)
        flight = runner._inflight.get(task_id)
        if flight is not None:
            flight[1].join(120)
        record = json.loads(Path(path).read_text())
        if done(record):
            return record
        if any(s['status'] in ('waiting', 'running') for s in record['steps']):
            wait_until(lambda: all(s['status'] not in ('waiting', 'running')
                                   for s in json.loads(Path(path).read_text())['steps']), 120)
    raise AssertionError(json.loads(Path(path).read_text()))


@pytest.fixture
def detached(session):
    """The run starts from chat; then no chat session is open."""
    def go(**kwargs):
        task = start(**kwargs)
        chat_tools.set_chat_session(None)
        return task
    yield go
    harness_locks._reset_for_tests()
    email_service.set_delivery_hook_for_tests(None)
    email_service.reset_outbox_for_tests()


def queue_rows():
    from db.models import SolveJobRow
    from db.session import SessionLocal
    with SessionLocal() as db:
        return db.query(SolveJobRow).count()


def until_waiting_on(runner, path, task_id, ref):
    return settle(runner, path, task_id, lambda r: (r['run'].get('waiting') or {}).get('ref') == ref
                  or any(s['status'] == 'waiting' for s in r['steps']))


def frames_of(task_id, owner):
    log = harness_session.ChatSession(session_id='run:' + task_id, owner_user_id=owner)
    path = eventlog.path_for(log)
    return [e for e in eventlog.read_events(path) if e['type'] == 'runner/frame'] if path.exists() else []


# ── The tracer: no panel, pre-clicked cards ─────────────────────────────────


def test_a_run_advances_with_no_chat_session_through_every_stage(detached, site):
    project_id, root = site
    task = detached()
    path = task_path(root, task)
    clicks = Clicks()
    runner = harness_runner.Runner('a', decisions=clicks)
    chat_tools.set_acting_user(None)  # the runner acts as the run's owner, not as this thread's user
    with foreground(two_buses()) as front:  # another project in the foreground
        record = settle(runner, path, task['task_id'],
                        lambda r: r['run']['status'] in ('completed', 'needs_user', 'failed'))
        assert PyPSAService.get_active_context() is front
        assert list(front.network.buses.index) == ['F1', 'F2'] and not front.network_revision
    assert record['run']['status'] == 'completed' and record['status'] == 'completed', record['run']
    assert [s['id'] for s in record['steps']] == ['inputs.1', 'input_gate.1', 'input_gate.2', 'input_gate.3', 'solve.1']
    assert all(s['session_id'] == 'run:' + task['task_id'] for s in record['steps'])
    assert clicks.used == ['solve', 'solve.1']  # the COP-1 Continue and the solve's own card
    log = harness_session.ChatSession(session_id='run:' + task['task_id'], owner_user_id=record['owner_id'])
    events = eventlog.read_events(eventlog.path_for(log))
    called = [b['name'] for e in events if e['type'] == 'assistant/attempt'
              for b in e['message']['content'] if b.get('type') == 'tool_use']
    assert called == [s['tool'] for s in record['steps']]
    assert sum(1 for e in events if e['type'] == 'runner/frame' and e['frame'] == 'tool_running') == 5


# ── Writes land in the run's project only (GK B condition 1) ─────────────────


def test_a_runner_write_lands_in_the_run_project_as_the_same_tool_does_from_chat(detached, site, session, monkeypatch):
    from services import dirty_state
    project_id, root = site
    ctx = PyPSAService.get_active_context()
    revision, undo = dirty_state.revision(ctx), len(ctx.undo.stack)
    frames, _ = dispatch(session, monkeypatch, 'update_component', edit(21.0))  # the same tool from chat
    chat_delta = (dirty_state.revision(ctx) - revision, len(ctx.undo.stack) - undo)
    task = detached()
    path = task_path(root, task)
    record = json.loads(path.read_text())
    record['steps'].append({'id': 'inputs.2', 'tool': 'update_component', 'args': edit(25.0), 'status': 'pending'})
    path.write_text(json.dumps(record))
    revision, undo = dirty_state.revision(ctx), len(ctx.undo.stack)
    runner = harness_runner.Runner('a', decisions=Clicks(refs={'inputs.2'}))
    from services import change_log_service
    logged, user_ts = len(change_log_service.get_all()), dict(ctx.user_ts)
    bus = {'component_class': 'Bus', 'name': 'F1', 'attrs': {'v_nom': 20.0}}
    with foreground(two_buses()) as front:  # the user switched project mid-stage
        front_frames, _ = dispatch(harness_session.ChatSession(), monkeypatch, 'update_component', bus)
        settle(runner, path, task['task_id'], lambda r: r['steps'][1]['status'] != 'pending')
        assert 'G1' not in front.network.generators.index and front.network.buses.at['F1', 'v_nom'] == 20.0
        assert front.user_ts == {} and len(front.undo.stack) == 1
    assert 'tool_error' not in [e for e, _ in front_frames]
    assert json.loads(path.read_text())['steps'][1]['status'] == 'completed'
    assert ctx.network.generators.at['G1', 'marginal_cost'] == 25.0 and 'F1' not in ctx.network.buses.index
    assert (dirty_state.revision(ctx) - revision, len(ctx.undo.stack) - undo) == chat_delta == (1, 1)
    assert ctx.user_ts == user_ts
    entries = change_log_service.get_all()[:len(change_log_service.get_all()) - logged]  # newest first
    assert sorted((e['name'], e['org_id']) for e in entries) == sorted([('F1', front.org_id), ('G1', ctx.org_id)])


# ── Restart (design §6.6; GK B condition 8) ──────────────────────────────────


def until_solve_waits(detached, site, monkeypatch, follow=False):
    """Runner A dispatches the solve, then "dies": its follower never runs."""
    from harness import continuation
    _, root = site
    task = detached()
    path = task_path(root, task)
    if not follow:
        monkeypatch.setattr(continuation, 'follow_queue_job', lambda *a, **k: threading.Event())
    a = harness_runner.Runner('a', decisions=Clicks())
    record = settle(a, path, task['task_id'], lambda r: any(s['status'] == 'waiting' for s in r['steps']))
    monkeypatch.undo()
    if not follow:
        step = record['steps'][-1]
        tasks.forget_queue_follower((task['task_id'], step['id'], step['attempt_id']))  # gone with the process
    return task, path, record


def later(seconds=harness_runner.LEASE_SECONDS + 1):
    return lambda: time.time() + seconds


def test_restart_settles_a_finished_queue_job_from_its_row_and_requeues_nothing(detached, site, monkeypatch):
    from harness import continuation
    task, path, record = until_solve_waits(detached, site, monkeypatch)
    rows = queue_rows()
    job = record['steps'][-1]['job']['jobs'][0]
    assert wait_until(lambda: continuation._queue_job_status(job) == 'completed', 120)
    b = harness_runner.Runner('b', clock=later(), decisions=Clicks())
    assert b.tick(path) != 'leased_elsewhere'
    record = json.loads(path.read_text())
    assert record['steps'][-1]['status'] == 'completed' and record['run']['runner']['restarts'] == 1
    record = settle(b, path, task['task_id'], lambda r: r['run']['status'] != 'running')
    assert record['run']['status'] == 'completed' and queue_rows() == rows


def test_restart_settles_a_lost_job_uncertain_needs_the_user_and_requeues_nothing(detached, site, monkeypatch):
    task, path, record = until_solve_waits(detached, site, monkeypatch)
    record['steps'][-1]['job']['jobs'][0]['job_id'] = '00000000-0000-4000-8000-000000000000'  # gone with the process
    path.write_text(json.dumps(record))
    rows = queue_rows()
    b = harness_runner.Runner('b', clock=later(), decisions=Clicks())
    assert b.tick(path) == 'uncertain'
    record = json.loads(path.read_text())
    assert record['steps'][-1]['status'] == 'uncertain'
    assert record['run']['status'] == 'needs_user' and record['run']['reason'] == 'uncertain'
    assert b.tick(path) == 'needs_user' and queue_rows() == rows


def test_restart_follows_a_job_still_live_in_the_queue(detached, site, monkeypatch):
    from harness import continuation
    task, path, record = until_solve_waits(detached, site, monkeypatch)
    followed = []
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: 'running')
    monkeypatch.setattr(continuation, 'follow_queue_job', lambda p, t: followed.append(t) or threading.Event())
    monkeypatch.setattr(tasks, '_reconcile_lost', lambda path, task: None)  # the follower is registered
    b = harness_runner.Runner('b', clock=later(), decisions=Clicks())
    b.tick(path)
    assert followed == [(task['task_id'], 'solve.1', record['steps'][-1]['attempt_id'])]
    assert json.loads(path.read_text())['steps'][-1]['status'] == 'waiting'


def test_three_restarts_in_an_hour_pause_the_run(detached, site):
    _, root = site
    task = detached()
    path = task_path(root, task)
    now = time.time()
    for n, name in enumerate('abc'):
        harness_runner.Runner(name, clock=lambda n=n: now + n * 100).tick(path)
    record = json.loads(path.read_text())
    assert record['run']['runner']['restarts'] == 2 and record['run']['status'] != 'paused'
    harness_runner.Runner('d', clock=lambda: now + 300).tick(path)
    record = json.loads(path.read_text())
    assert record['run']['status'] == 'paused' and record['run']['reason'] == 'restarts'


def test_two_consecutive_runner_errors_pause_the_run(detached, site, monkeypatch):
    _, root = site
    task = detached()
    path = task_path(root, task)
    runner = harness_runner.Runner('a')
    monkeypatch.setattr(runner, '_step', lambda *a: 1 / 0)
    assert runner.tick(path) == 'error' and json.loads(path.read_text())['run']['status'] == 'running'
    assert runner.tick(path) == 'error'
    record = json.loads(path.read_text())
    assert record['run']['status'] == 'paused' and record['run']['reason'] == 'runner_errors'


# ── Lease (GK B condition 5) ─────────────────────────────────────────────────


def test_only_the_lease_holder_acts_and_another_takes_over_once_it_lapses(detached, site):
    _, root = site
    task = detached()
    path = task_path(root, task)
    now = time.time()
    a = harness_runner.Runner('a', clock=lambda: now, decisions=Clicks())
    b = harness_runner.Runner('b', clock=lambda: now + harness_runner.LEASE_SECONDS - 1, decisions=Clicks())
    a.tick(path)
    a._inflight[task['task_id']][1].join(60)
    steps = json.loads(path.read_text())['steps']
    assert b.tick(path) == 'leased_elsewhere' and json.loads(path.read_text())['steps'] == steps
    b.clock = lambda: now + harness_runner.LEASE_SECONDS + 1
    assert b.tick(path) != 'leased_elsewhere'
    runner = json.loads(path.read_text())['run']['runner']
    assert runner['lease_owner'] == 'b' and runner['restarts'] == 1
    assert a.tick(path) == 'leased_elsewhere'


# ── ACL and the per-dispatch re-check (SC-07, SC-16; GK B condition 3) ───────


def test_a_revoked_acl_stops_the_next_tick_with_no_effect(detached, site, monkeypatch):
    from fastapi import HTTPException
    from services import project_registry
    _, root = site
    task = detached()
    path = task_path(root, task)
    before, rows = json.loads(path.read_text())['steps'], queue_rows()

    def revoked(db, user, ref, **kw):
        raise HTTPException(404, 'Project not found')
    monkeypatch.setattr(project_registry, 'resolve_project', revoked)
    runner = harness_runner.Runner('a', decisions=Clicks())
    assert runner.tick(path) == 'access'
    record = json.loads(path.read_text())
    assert record['run']['status'] == 'needs_user' and record['run']['reason'] == 'access'
    assert record['steps'] == before and queue_rows() == rows
    assert frames_of(task['task_id'], record['owner_id']) == []
    assert runner.tick(path) == 'needs_user'


@pytest.mark.parametrize('change', ['project', 'acl', 'lock'])
def test_each_dispatch_rechecks_under_the_lock_and_refuses_with_no_effect(detached, site, monkeypatch, change):
    from fastapi import HTTPException
    from services import project_registry
    _, root = site
    task = detached()
    path = task_path(root, task)
    runner = harness_runner.Runner('a', decisions=Clicks())
    record = until_waiting_on(harness_runner.Runner('a'), path, task['task_id'], 'solve')
    tasks.continue_at_card  # the stage pause is the user's; the test clears it as the plan card does
    record['status'], record['run']['status'], record['run']['waiting'] = 'ready', 'running', None
    record.pop('pause')
    record['run'].pop('reason')
    path.write_text(json.dumps(record))
    rows, ctx = queue_rows(), PyPSAService.get_active_context()
    real = runner.recheck

    def changed_meanwhile(task_id, p, c):
        """The change lands between the tick and the in-lock re-check."""
        if change == 'project':
            c = foreign
        elif change == 'acl':
            monkeypatch.setattr(project_registry, 'resolve_project',
                                lambda *a, **k: (_ for _ in ()).throw(HTTPException(404, 'gone')))
        else:
            harness_locks.hold(record['project_id'], 'another-run')
        return real(task_id, p, c)
    foreign = PyPSAService.build_context()
    foreign.project_uuid = '00000000-0000-4000-8000-000000000001'
    monkeypatch.setattr(runner, 'recheck', changed_meanwhile)
    runner.tick(path)
    runner._inflight[task['task_id']][1].join(60)
    after = json.loads(path.read_text())
    assert after['steps'][-1]['status'] == 'pending' and 'claim' not in after['steps'][-1]
    assert after['run']['status'] == 'needs_user' and after['run']['reason'] == 'recheck'
    assert queue_rows() == rows
    assert not [f for f in frames_of(task['task_id'], after['owner_id'])
                if f['frame'] == 'tool_running' and f['step'] == 'solve.1']
    assert ctx.network.generators.at['G1', 'p_nom'] == 20


# ── The foreground and the harness hold (GK B conditions 2 and 4) ────────────


def test_a_foreground_write_during_the_solve_is_refused_409_and_none_is_lost(detached, site, client, monkeypatch):
    _, root = site
    from routers.simulation import _solver_in_flight
    task, path, record = until_solve_waits(detached, site, monkeypatch)  # its follower never settles it
    wait_until(lambda: not _solver_in_flight())  # the solver's own gate would answer the PUTs
    assert json.loads(path.read_text())['steps'][-1]['status'] == 'waiting'
    assert harness_locks.holder(record['project_id']) == task['task_id']  # the runner's own hold
    body = {'name': 'G1', 'bus': 'B1', 'marginal_cost': 30}
    refused = client.put('/api/network/generators/G1', json=body,
                         headers={'X-Harness-Run': task['task_id']})  # no header reaches the runner's marker
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['error_kind'] == 'project_locked'
    assert 'harness run is editing this project' in refused.json()['detail']['message']
    assert client.get('/api/network/generators').status_code == 200  # reads stay open
    frames, _ = dispatch(chat_tools.chat_session() or harness_session.ChatSession(), monkeypatch,
                         'update_component', edit(30.0))
    assert [p['error_kind'] for e, p in frames if e == 'tool_error'] == ['project_locked']
    assert PyPSAService.get_active_context().network.generators.at['G1', 'marginal_cost'] == 10
    harness_locks.release(record['project_id'], task['task_id'])
    assert client.put('/api/network/generators/G1', json=body).status_code == 200
    assert PyPSAService.get_active_context().network.generators.at['G1', 'marginal_cost'] == 30


def test_project_route_writes_to_the_held_project_are_refused_409_until_it_is_let_go(detached, site, client, monkeypatch):
    """GK B: `_enforce_project_lock` checks the hold by the path's project."""
    name = 'Tool Baseline'
    sid = client.post(f'/api/projects/{name}/snapshots', json={'label': 'base'}).json()['id']
    from routers.simulation import _solver_in_flight
    task, path, record = until_solve_waits(detached, site, monkeypatch)
    wait_until(lambda: not _solver_in_flight())  # the solver's own gate would answer first
    assert harness_locks.holder(record['project_id']) == task['task_id']
    snapshots = len(client.get(f'/api/projects/{name}/snapshots').json())
    for refused in (client.post(f'/api/projects/{name}/snapshots/{sid}/restore'),
                    client.post(f'/api/projects/{name}/snapshots', json={'label': 'during'}),
                    client.post(f'/api/projects/{name}', params={'force': True})):
        assert refused.status_code == 409, refused.text
        assert refused.json()['detail']['error_kind'] == 'project_locked', refused.text
    assert len(client.get(f'/api/projects/{name}/snapshots').json()) == snapshots
    harness_locks.release(record['project_id'], task['task_id'])
    assert client.post(f'/api/projects/{name}', params={'force': True}).status_code == 200


def test_the_user_can_cancel_a_run_that_holds_the_project(detached, site, monkeypatch):
    """GK B plan condition 4: stop, resume and reconcile stay open to the run's
    owner during the hold; another task id is still refused."""
    task, path, record = until_solve_waits(detached, site, monkeypatch)
    assert harness_locks.holder(record['project_id']) == task['task_id']
    session = chat_tools.chat_session() or harness_session.ChatSession()
    frames, _ = dispatch(session, monkeypatch, 'resume_task', {'task_id': 'not-a-task'})
    assert [p['error_kind'] for e, p in frames if e == 'tool_error'] == ['project_locked']
    frames, _ = dispatch(session, monkeypatch, 'cancel_task', {'task_id': task['task_id']})
    assert 'tool_error' not in [e for e, _ in frames]
    assert json.loads(path.read_text())['run']['status'] == 'cancelled'
    harness_runner.Runner('a').tick(path)  # what cancel's wake does
    assert harness_locks.holder(record['project_id']) is None
    from db.models import SolveJobRow
    from db.session import SessionLocal
    with SessionLocal() as db:  # the queued job keeps its own abort path: cancel does not abort it
        assert not [r for r in db.query(SolveJobRow).all() if r.status in ('cancelled', 'aborted')]


def test_the_runner_imports_and_leases_without_fcntl(detached, site, monkeypatch):
    """GK B: Windows has no fcntl; the lease uses harness/durable's lock."""
    import os
    import subprocess
    import sys
    probe = "import sys; sys.modules['fcntl'] = None; import harness.runner"
    assert subprocess.run([sys.executable, '-c', probe], cwd=Path(harness_runner.__file__).parent.parent,
                          capture_output=True, timeout=120).returncode == 0
    _, root = site
    task = detached()
    path = task_path(root, task)
    msvcrt = type('msvcrt', (), {'LK_NBLCK': 2, 'LK_UNLCK': 0, 'locking': staticmethod(lambda *a: None)})
    monkeypatch.setitem(sys.modules, 'fcntl', None)
    monkeypatch.setitem(sys.modules, 'msvcrt', msvcrt)
    from harness import durable
    monkeypatch.setattr(durable, 'os', type('os', (), {'name': 'nt', 'chmod': staticmethod(os.chmod)}))
    assert harness_runner.Runner('a')._lease(path) is not None


def test_a_request_never_runs_as_the_runner():
    assert harness_locks.in_run() is None
    harness_locks.hold('p', 't')
    try:
        assert harness_locks.refuses('p')
        token = harness_locks.enter_run('t')
        assert not harness_locks.refuses('p')
        harness_locks.leave_run(token)
    finally:
        harness_locks._reset_for_tests()


def test_a_carded_effectful_step_dispatches_without_deadlock_when_its_handler_locks(detached, site, monkeypatch):
    """The handler runs on the tool executor and takes the project's lock, as
    many do; the runner must not hold that lock across it."""
    from services import chat_tools as tools
    _, root = site
    task = detached()
    path = task_path(root, task)
    real = tools.DISPATCHERS['solve_queue_enqueue']
    bound, seen = [], []
    dispatch = harness_runner.Runner.dispatch

    def spied(self, path, ctx, *rest):
        bound.append(ctx)
        return dispatch(self, path, ctx, *rest)

    def locking(*args, **kwargs):
        seen.append((threading.current_thread().name, tasks.RUN_CONTEXT.get(), PyPSAService.get_active_context()))
        with PyPSAService.get_lock():
            return real(*args, **kwargs)
    monkeypatch.setattr(harness_runner.Runner, 'dispatch', spied)
    monkeypatch.setitem(tools.DISPATCHERS, 'solve_queue_enqueue', locking)
    monkeypatch.setattr(harness_runner, 'LOCK_TIMEOUT_SECONDS', 2.0)
    runner = harness_runner.Runner('a', decisions=Clicks())
    until_waiting_on(harness_runner.Runner('a'), path, task['task_id'], 'solve')
    runner.tick(path)  # the COP-1 Continue
    runner.tick(path)  # the solve's card, decided, dispatched
    flight = runner._inflight[task['task_id']][1]
    flight.join(30)
    assert not flight.is_alive()
    assert json.loads(path.read_text())['steps'][-1]['status'] in ('waiting', 'completed')
    # GK B (a): the handler ran on the tool executor, bound to the run's own context
    assert len(seen) == 1
    thread, run_ctx, active = seen[0]
    assert thread.startswith('chat-tool') and run_ctx is bound[-1] and active is bound[-1]
    assert str(active.project_uuid) == json.loads(path.read_text())['project_id']


def test_a_claim_that_lapses_while_the_handler_runs_still_lets_no_one_else_dispatch(detached, site, monkeypatch):
    """GK B (c): the claim only guards pending -> running; once the step runs,
    its `running` status is the authority, however late the clock reads."""
    from services import chat_tools as tools
    _, root = site
    task = detached()
    path = task_path(root, task)
    real = tools.DISPATCHERS['solve_queue_enqueue']
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        entered.set()
        release.wait(60)
        return real(*args, **kwargs)
    monkeypatch.setitem(tools.DISPATCHERS, 'solve_queue_enqueue', held)
    runner = harness_runner.Runner('a', decisions=Clicks())
    until_waiting_on(harness_runner.Runner('a'), path, task['task_id'], 'solve')
    runner.tick(path)  # the COP-1 Continue
    runner.tick(path)  # the solve's card, decided, dispatched
    try:
        assert entered.wait(30)
        record = json.loads(path.read_text())
        step = record['steps'][-1]
        assert step['status'] == 'running'
        late = time.time() + tasks.CLAIM_SECONDS + 1
        monkeypatch.setattr(tasks, 'time', type('Clock', (), {'time': staticmethod(lambda: late)}))
        rows = queue_rows()
        assert not tasks.claim_step(path, step['id'], 'chat-other')
        session = harness_session.ChatSession(owner_user_id=record['owner_id'])
        chat_tools.set_chat_session(session)
        session.task_id = task['task_id']
        frames, _ = dispatch(session, monkeypatch, 'solve_queue_enqueue', step['args'])
        assert [p['error_kind'] for e, p in frames if e == 'tool_error'] == ['task_step_claimed']
        assert 'tool_pending_confirmation' not in [e for e, _ in frames] and queue_rows() == rows
    finally:
        release.set()
        runner._inflight[task['task_id']][1].join(30)
    assert queue_rows() == rows + 1


def test_a_foreground_request_completes_while_the_runner_waits_on_a_live_card(detached, site, client):
    _, root = site
    task = detached()
    path = task_path(root, task)
    runner = harness_runner.Runner('a', decisions=Clicks(refs={'solve'}))
    until_waiting_on(runner, path, task['task_id'], 'solve.1')
    runner.present(task['task_id'])  # the user opened the run: the card is issued now
    assert runner.tick(path) == 'dispatched'
    session = harness_session.get_session('run:' + task['task_id'])
    assert wait_until(lambda: session.pending_confirmations, 10)
    pc = next(iter(session.pending_confirmations.values()))
    assert pc.expires_at - time.monotonic() > harness_session.CONFIRMATION_TTL_SECONDS - 10  # TTL from display
    started = time.monotonic()
    assert client.get('/api/network/generators').status_code == 200
    body = {'name': 'G1', 'bus': 'B1', 'marginal_cost': 12}
    assert client.put('/api/network/generators/G1', json=body).status_code == 200  # no hold before the decision
    assert time.monotonic() - started < 10
    session.record_decision(pc.token, 'deny')
    flight = runner._inflight[task['task_id']][1]
    flight.join(30)
    assert not flight.is_alive() and json.loads(path.read_text())['steps'][-1]['status'] == 'pending'


def test_no_card_is_issued_while_nobody_is_there(detached, site):
    _, root = site
    task = detached()
    path = task_path(root, task)
    runner = harness_runner.Runner('a')  # the application's runner: no decisions
    record = until_waiting_on(runner, path, task['task_id'], 'solve')
    assert record['run']['status'] == 'needs_user' and record['run']['waiting']['kind'] == 'card'
    assert record['status'] == 'paused_at_card'  # the chat's own plan card still works
    assert runner.tick(path) == 'waiting' and harness_session.get_session('run:' + task['task_id']) \
        .pending_confirmations == {}


def test_the_decision_seam_is_unreachable_from_the_application():
    import pathlib
    assert harness_runner.Runner()._decisions is None
    backend = pathlib.Path(harness_runner.__file__).parent.parent
    users = [p for p in backend.rglob('*.py') if 'decisions=' in p.read_text()
             and 'tests' not in p.parts and p.name != 'runner.py']
    assert users == []


# ── One holder per step: the chat and the runner (GK B condition 7) ──────────


def until_solve_card(detached, site):
    _, root = site
    task = detached()
    path = task_path(root, task)
    until_waiting_on(harness_runner.Runner('a'), path, task['task_id'], 'solve')
    session = harness_session.ChatSession(owner_user_id=json.loads(path.read_text())['owner_id'])
    chat_tools.set_chat_session(session)
    tasks.continue_at_card(session, task['task_id'])
    record = json.loads(path.read_text())
    return task, path, session, record['steps'][-1]['args']


def test_a_step_the_chat_holds_at_its_card_is_never_carded_or_run_by_the_runner(detached, site, monkeypatch):
    from harness import loop
    task, path, session, args = until_solve_card(detached, site)
    rows = queue_rows()
    gate = threading.Event()
    chat_frames = []

    def chat():
        monkeypatch.setattr(session, 'wait_for_decision', lambda _: gate.wait(30) and 'approve')
        chat_frames.extend(loop._dispatch_real_tool_call(session, {'id': 'chat-solve', 'name': 'solve_queue_enqueue',
                                                                   'input': args}, []))
    worker = threading.Thread(target=__import__('contextvars').copy_context().run, args=(chat,))
    worker.start()
    assert wait_until(lambda: (json.loads(path.read_text())['steps'][-1].get('claim') or {}).get('by')
                      == session.session_id, 10)
    runner = harness_runner.Runner('a', decisions=Clicks())
    assert runner.tick(path) == 'claimed_elsewhere'
    assert task['task_id'] not in runner._inflight
    gate.set()
    worker.join(60)
    assert [e for e, _ in chat_frames].count('tool_pending_confirmation') == 1
    assert [f for f in frames_of(task['task_id'], json.loads(path.read_text())['owner_id'])
            if f['step'] == 'solve.1'] == []
    assert queue_rows() == rows + 1


def test_the_chat_and_the_runner_racing_for_one_step_run_it_once(detached, site, monkeypatch):
    from harness import loop
    task, path, session, args = until_solve_card(detached, site)
    rows = queue_rows()
    monkeypatch.setattr(session, 'wait_for_decision', lambda _: 'approve')
    runner = harness_runner.Runner('a', decisions=Clicks())
    start_line = threading.Barrier(2)
    chat_frames = []

    def chat():
        start_line.wait()
        try:
            chat_frames.extend(loop._dispatch_real_tool_call(
                session, {'id': 'chat-solve', 'name': 'solve_queue_enqueue', 'input': args}, []))
        except Exception:  # noqa: BLE001 — a refused admit is a lost race
            pass

    def run():
        start_line.wait()
        runner.tick(path)
        flight = runner._inflight.get(task['task_id'])
        if flight:
            flight[1].join(60)
    copy = __import__('contextvars').copy_context
    threads = [threading.Thread(target=copy().run, args=(chat,)), threading.Thread(target=copy().run, args=(run,))]
    [t.start() for t in threads]
    [t.join(90) for t in threads]
    runner_cards = [f for f in frames_of(task['task_id'], json.loads(path.read_text())['owner_id'])
                    if f['frame'] == 'tool_pending_confirmation' and f['step'] == 'solve.1']
    chat_cards = [e for e, _ in chat_frames if e == 'tool_pending_confirmation']
    assert len(runner_cards) + len(chat_cards) == 1
    assert queue_rows() == rows + 1


# ── Notices (GK B condition 10) ──────────────────────────────────────────────


def mail(monkeypatch, configured=True):
    sent = []
    email_service.reset_outbox_for_tests()
    email_service.set_delivery_hook_for_tests(sent.append)
    monkeypatch.setattr(email_service, 'get_status', lambda: {'configured': configured})
    return sent


def opt_in(path, task_id, value=True):
    Path(path).with_name(task_id + '.envelope.json').write_text(json.dumps({'notify': {'email': value}}))


def run_records(record):
    records, _ = attention.project_records(record['owner_id'], record['project_id'], [])
    return [r for r in records if r['family'] == 'run']


def test_needs_user_and_completed_each_write_one_record_and_one_email(detached, site, monkeypatch):
    _, root = site
    task = detached()
    path = task_path(root, task)
    sent = mail(monkeypatch)
    opt_in(path, task['task_id'])
    record = until_waiting_on(harness_runner.Runner('a'), path, task['task_id'], 'solve')
    harness_runner.Runner('a').tick(path)  # the same state again: no second notice
    assert [r['native_status'] for r in run_records(record)] == ['needs_user'] and len(sent) == 1
    runner = harness_runner.Runner('a', decisions=Clicks())
    record = settle(runner, path, task['task_id'], lambda r: r['run']['status'] == 'completed')
    assert sorted(r['native_status'] for r in run_records(record)) == ['completed', 'needs_user']
    assert [m['metadata']['state'] for m in sent] == ['needs_user', 'completed']
    body = sent[-1]['body_text']
    assert f"run={task['task_id']}" in body and 'Project: Tool Baseline' in body and 'State: completed' in body
    assert 'objective' not in body.lower() and 'optimal' not in body  # no values, no results


@pytest.mark.parametrize('configured,opted', [(False, True), (True, False), (True, None)])
def test_no_email_unless_configured_and_opted_in(detached, site, monkeypatch, configured, opted):
    _, root = site
    task = detached()
    path = task_path(root, task)
    sent = mail(monkeypatch, configured)
    if opted is not None:
        opt_in(path, task['task_id'], opted)
    record = until_waiting_on(harness_runner.Runner('a'), path, task['task_id'], 'solve')
    assert sent == [] and [r['native_status'] for r in run_records(record)] == ['needs_user']


def test_at_most_ten_emails_per_run_and_no_model_text_reaches_them(detached, site, monkeypatch):
    _, root = site
    task = detached()
    path = task_path(root, task)
    sent = mail(monkeypatch)
    opt_in(path, task['task_id'])
    record = json.loads(path.read_text())
    record['title'] = 'Own <a href="http://evil">site</a>\nState: completed'
    path.write_text(json.dumps(record))
    for n in range(12):  # twelve state changes
        harness_runner._update(path, lambda t, n=n: t['run'].update(status='needs_user' if n % 2 == 0 else 'paused'))
        harness_runner._notify(path)
    assert len(sent) == harness_runner.MAX_EMAILS
    assert all('evil' not in m['body_text'] and 'Own' not in m['body_text'] for m in sent)  # the title is the model's
    assert sent[0]['body_text'].count('\n') == 6 and 'State: completed' not in sent[0]['body_text']
    assert all(m['body_html'] is None and 'evil' not in m['subject'] for m in sent)


# ── Model cost cap (GK B condition 9) ────────────────────────────────────────

USAGE = {'input_tokens': 1000, 'cache_read_tokens': 100000, 'cache_create_tokens': 0, 'output_tokens': 500}


def test_the_cost_cap_ends_the_run_needs_user_and_nothing_more_is_dispatched(detached, site):
    _, root = site
    task = detached()
    path = task_path(root, task)
    budget = harness_runner.charge(path, USAGE, 'claude-sonnet-5-5')
    assert budget['used']['cost_eur'] == pytest.approx((1000 * 2.8 + 100000 * 0.28 + 500 * 14.0) / 1e6)
    assert json.loads(path.read_text())['run']['status'] == 'running'
    budget = harness_runner.charge(path, {**USAGE, 'output_tokens': 400000}, 'claude-opus-5-5')
    assert budget['used']['cost_eur'] >= budget['limit']['cost_eur']
    record = json.loads(path.read_text())
    assert record['run']['status'] == 'needs_user' and record['run']['reason'] == 'budget'
    steps = record['steps']
    assert harness_runner.Runner('a', decisions=Clicks()).tick(path) == 'needs_user'
    assert json.loads(path.read_text())['steps'] == steps


@pytest.mark.parametrize('usage,model,rates', [
    (USAGE, 'gpt-made-up', None),
    ({k: v for k, v in USAGE.items() if k != 'cache_read_tokens'}, 'claude-haiku-5-5', None),
    ({**USAGE, 'output_tokens': -1}, 'claude-haiku-5-5', None),
    (USAGE, 'claude-haiku-5-5', '{"claude-haiku": {"input_tokens": NaN, "cache_read_tokens": 0, '
                                '"cache_create_tokens": 0, "output_tokens": 0}}'),
    (USAGE, 'claude-haiku-5-5', '{"claude-haiku": {"input_tokens": -1, "cache_read_tokens": 0, '
                                '"cache_create_tokens": 0, "output_tokens": 0}}'),
])
def test_an_unmeterable_call_fails_closed(detached, site, monkeypatch, usage, model, rates):
    _, root = site
    task = detached()
    path = task_path(root, task)
    if rates:
        monkeypatch.setenv('PYPSA_GUI_MODEL_RATES_EUR', rates)
    budget = harness_runner.charge(path, usage, model)
    record = json.loads(path.read_text())
    assert budget['used']['unmetered'] and record['run']['status'] == 'needs_user'
    assert record['run']['reason'] == 'budget'


def test_a_valid_rate_override_is_used(monkeypatch):
    monkeypatch.setenv('PYPSA_GUI_MODEL_RATES_EUR', json.dumps({'local': {k: 1.0 for k in harness_runner.USAGE_KEYS}}))
    assert harness_runner._rate('local-model')['output_tokens'] == 1.0
    with pytest.raises(ValueError):
        harness_runner._rate('claude-opus-5-5')


# ── Resume and the app's lifecycle ───────────────────────────────────────────


def test_the_users_resume_lets_a_stopped_run_look_again(detached, site, session):
    _, root = site
    task = detached()
    path = task_path(root, task)
    harness_runner._update(path, lambda t: harness_runner._stop_in(t, 'paused', 'runner_errors'))
    chat_tools.set_chat_session(session)
    view = tasks.resume_task(task['task_id'])
    assert view['run']['status'] == 'running' and 'reason' not in view['run']


def test_the_runner_is_off_under_pytest_unless_asked(monkeypatch):
    monkeypatch.delenv('PYPSA_GUI_HARNESS_RUNNER', raising=False)
    assert not harness_runner.enabled() and harness_runner.start_default() is None


def test_a_step_the_runner_holds_is_refused_to_the_chat_before_any_card(detached, site, monkeypatch):
    task, path, session, args = until_solve_card(detached, site)
    rows = queue_rows()
    assert tasks.claim_step(path, 'solve.1', 'run:' + task['task_id'])  # the runner's claim
    frames, _ = dispatch(session, monkeypatch, 'solve_queue_enqueue', args)
    assert [p['error_kind'] for e, p in frames if e == 'tool_error'] == ['task_step_claimed']
    assert 'tool_pending_confirmation' not in [e for e, _ in frames] and queue_rows() == rows


def test_the_runner_refuses_to_lock_or_wake_a_path_outside_assistant_tasks(tmp_path):
    inside = tmp_path / "project" / "assistant_tasks"
    inside.mkdir(parents=True)
    with harness_runner._file_lock(inside / "a.json"):
        pass
    outside = [tmp_path / "project" / "a.json", inside / "nested" / "a.json", inside / ".." / "a.json", inside / "a.txt"]
    for path in outside:
        with pytest.raises(ValueError):
            with harness_runner._file_lock(path):
                pass
    assert not (tmp_path / "project" / "a.json.lease").exists()
    runner = harness_runner.Runner()
    runner.wake(tmp_path / "project" / "a.json")
    assert runner._due == set()
