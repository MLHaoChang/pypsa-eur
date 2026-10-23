"""LC-23: the study brief on `start_task` (owner decisions AI-567b, COP-1).

Slice 1: SC-05 admission, SC-11 freshness, SC-12 native reconciliation and the
COP-1 card pause. Slice 2: queued run steps (SC-08/09/13), what a brief can run
(SC-10), the refresh rule and the correction draft (SC-14). Real project and
real solve queue, ordinary dispatch seam; no provider calls."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import uuid
from concurrent.futures import Future

import pytest
from fastapi import HTTPException
from harness import edit_card
from services import assistant_tasks as tasks, chat_service, chat_tools, run_evidence
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture
def session(baseline):
    value = chat_service.ChatSession()
    chat_tools.set_chat_session(value)
    return value


def refused(kind, call, *args, **kwargs):
    with pytest.raises(HTTPException) as failure:
        call(*args, **kwargs)
    assert failure.value.detail['error_kind'] == kind, failure.value.detail
    return failure.value.detail


def reads(count):
    tools = ['project_readiness', 'dispatch_status', 'validate_network']
    return [{'id': f's{i}', 'tool': tools[i % 3], 'args': {}} for i in range(count)]


def brief(steps, cards=('capacity',)):
    ids = [s['id'] for s in steps]
    size = -(-len(ids) // len(cards))
    return {'objective': 'Compare a battery with a bigger connection through 2035.',
            'cards': list(cards), 'cases': [{'name': 'Base'}],
            'phases': [{'card': card, 'steps': ids[i * size:(i + 1) * size]} for i, card in enumerate(cards)]}


def edit(value):
    return {'component_class': 'Generator', 'name': 'G1', 'attrs': {'marginal_cost': value}}


def run(session, tool, args=None, result=None):
    ticket = tasks.begin_step(session, tool, args or {})
    assert ticket is not None
    return tasks.finish_step(ticket, result={'ok': True} if result is None else result)


# ── SC-12: never complete a step from a caller-supplied result ───────────────


def test_sc12_made_up_completed_result_is_refused_without_native_evidence(session):
    task = chat_tools.start_task(title='Export', request_id='sc12-export', steps=[
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}}])
    tasks.finish_step(tasks.begin_step(session, 'export_network_nc', {}), error='worker interrupted')
    refused('task_resolution_unverifiable', chat_tools.resolve_task_step,
            task['task_id'], 'completed', {'file_id': '0123456789abcdef'})
    view = chat_tools.get_task(task['task_id'])
    assert view['status'] == 'needs_review' and view['completed_steps'] == 0


def test_sc12_late_worker_receipt_resolves_from_the_harness_record_not_the_caller(session, baseline):
    task = chat_tools.start_task(title='Readiness', request_id='sc12-late', steps=[
        {'id': 'ready', 'tool': 'project_readiness', 'args': {}}])
    ticket = tasks.begin_step(session, 'project_readiness', {})
    worker = Future()
    tasks.register_worker(ticket, worker)
    tasks.finish_step(ticket, error='tool_timeout')
    worker.set_result({'ready': True, 'native': 'from the worker'})
    view = chat_tools.resolve_task_step(task['task_id'], 'completed', {'ready': True, 'native': 'forged'})
    assert view['status'] == 'completed'
    record = json.loads((baseline[2] / 'assistant_tasks' / (task['task_id'] + '.json')).read_text())
    assert record['steps'][0]['result'] == {'ready': True, 'native': 'from the worker'}


def test_sc12_late_worker_failure_is_no_receipt(session):
    task = chat_tools.start_task(title='Readiness', request_id='sc12-late-fail', steps=[
        {'id': 'ready', 'tool': 'project_readiness', 'args': {}}])
    ticket = tasks.begin_step(session, 'project_readiness', {})
    worker = Future()
    tasks.register_worker(ticket, worker)
    tasks.finish_step(ticket, error='tool_timeout')
    worker.set_result({'ok': False})
    refused('task_resolution_unverifiable', chat_tools.resolve_task_step, task['task_id'], 'completed', {'ok': True})
    assert chat_tools.resolve_task_step(task['task_id'], 'retry')['next_step']['status'] == 'pending'


def test_sc12_uncertain_edit_resolves_only_when_its_native_readback_matches(session):
    task = chat_tools.start_task(title='Edit', request_id='sc12-edit', steps=[
        {'id': 'cost', 'tool': 'update_component', 'args': edit(25.0)}])
    ticket = tasks.begin_step(session, 'update_component', edit(25.0))
    card = edit_card.preview('update_component', edit(25.0))
    view = tasks.finish_step(ticket, result={'_edit_receipt': edit_card.receipt(card, edit(25.0))})
    assert view['status'] == 'needs_review'
    refused('task_resolution_unverifiable', chat_tools.resolve_task_step,
            task['task_id'], 'completed', {'_edit_receipt': {'outcome': 'verified'}})
    PyPSAService.get_network().generators.at['G1', 'marginal_cost'] = 25.0  # the write did land
    view = chat_tools.resolve_task_step(task['task_id'], 'completed', {'forged': True})
    assert view['status'] == 'completed' and view['changes']


def test_sc12_lost_job_resolves_only_from_its_published_run(session, baseline):
    ctx, project_id, root = baseline
    task = chat_tools.start_task(title='Solve', request_id='sc12-job', steps=[
        {'id': 'solve', 'tool': 'run_simulation', 'args': {}}])
    ticket = tasks.begin_step(session, 'run_simulation', {})
    job_id = 'fg:' + uuid.uuid4().hex
    tasks.await_job(ticket, job_id, session.session_id)
    chat_tools.resume_task(task['task_id'])  # this process never registered the job: uncertain
    assert chat_tools.get_task(task['task_id'])['status'] == 'needs_review'
    refused('task_resolution_unverifiable', chat_tools.resolve_task_step,
            task['task_id'], 'completed', {'run_id': 'fg-made-up', 'objective': 1.0})
    run_id = run_evidence.foreground_run_id(job_id)
    run_evidence.start_run(root, project_id, run_id=run_id, kind='optimization', fingerprint='f' * 64, job_id=job_id)
    run_evidence.publish_run(root, project_id, run_id=run_id, kind='optimization', status='completed',
                             fingerprint='f' * 64, job_id=job_id,
                             metrics={'objective': {'value': 42.0, 'unit': 'EUR'}})
    view = chat_tools.resolve_task_step(task['task_id'], 'completed', {'objective': 1.0})
    assert view['status'] == 'completed'
    record = json.loads((root / 'assistant_tasks' / (task['task_id'] + '.json')).read_text())
    assert record['steps'][0]['result']['run_id'] == run_id
    assert record['steps'][0]['result']['objective'] == 42.0


# ── SC-05: brief admission ───────────────────────────────────────────────────


def test_sc05_step_limits_with_and_without_a_brief(session):
    refused('invalid_task', chat_tools.start_task, title='Long', request_id='plain-13', steps=reads(13))
    view = chat_tools.start_task(title='Long', request_id='brief-24', steps=reads(24), brief=brief(reads(24)))
    assert view['total_steps'] == 24 and len(json.dumps(view)) <= 3800
    refused('invalid_task', chat_tools.start_task, title='Long', request_id='brief-25',
            steps=reads(25), brief=brief(reads(25)))


@pytest.mark.parametrize('change', [
    lambda b: b.update(cards=['dynamics']),
    lambda b: b.update(objective=''),
    lambda b: b.update(objective='x' * 241),
    lambda b: b['phases'][0]['steps'].pop(),
    lambda b: b['phases'][0]['steps'].reverse(),
    lambda b: b['phases'].append({'card': 'capacity', 'steps': []}),
    lambda b: b.update(cases=[{'name': str(i)} for i in range(9)]),
    lambda b: b.update(extra=True),
])
def test_sc05_malformed_brief_is_refused_and_nothing_saved(session, baseline, change):
    value = brief(reads(3))
    change(value)
    refused('invalid_brief', chat_tools.start_task, title='Bad', request_id='bad', steps=reads(3), brief=value)
    assert not list((baseline[2] / 'assistant_tasks').glob('*.json'))


def test_sc05_brief_cannot_carry_controls_or_rebinding(session):
    for tool in ('start_task', 'activate_project', 'ask_user'):
        steps = [{'id': 'a', 'tool': tool, 'args': {}}]
        with pytest.raises(HTTPException):
            chat_tools.start_task(title='Nested', request_id='nested-' + tool, steps=steps, brief=brief(steps))


def test_brief_view_names_server_limits_and_condenses(session):
    steps = reads(4)
    view = chat_tools.start_task(title='Study', request_id='view', steps=steps,
                                 brief=brief(steps, cards=('capacity', 'grid')))
    assert view['brief']['objective'].startswith('Compare')
    assert view['brief']['cards'] == ['capacity', 'grid']
    assert view['brief']['phase'] == {'card': 'capacity', 'index': 1, 'of': 2}
    assert all(step['limit'] == {'args_chars': 1800, 'result_chars': 4000, 'wait': None} for step in view['steps'])
    run_step = [{'id': 'solve', 'tool': 'run_simulation', 'args': {}}]
    solve = chat_tools.start_task(title='Solve', request_id='view-solve', steps=run_step, brief=brief(run_step))
    assert solve['steps'][0]['limit']['wait'] == 'job'
    plain = chat_tools.start_task(title='Plain', request_id='view-plain', steps=reads(1))
    assert 'limit' not in plain['steps'][0]  # plain views keep their size


def test_same_request_with_a_different_brief_conflicts(session):
    steps = reads(2)
    chat_tools.start_task(title='Study', request_id='same', steps=steps, brief=brief(steps))
    other = brief(steps)
    other['objective'] = 'Something else entirely.'
    refused('task_request_conflict', chat_tools.start_task, title='Study', request_id='same', steps=steps, brief=other)


def test_plain_task_file_stays_version_1(session, baseline):
    task = chat_tools.start_task(title='Plain', request_id='plain', steps=reads(1))
    path = baseline[2] / 'assistant_tasks' / (task['task_id'] + '.json')
    run(session, 'project_readiness')
    assert json.loads(path.read_text())['version'] == 1


# ── COP-1: pause at each card boundary, cleared only by the user ─────────────


def test_cop1_pause_at_card_boundary_needs_the_user(session):
    steps = reads(2)
    task = chat_tools.start_task(title='Study', request_id='pause', steps=steps,
                                 brief=brief(steps, cards=('capacity', 'grid')))
    view = run(session, 'project_readiness')
    assert view['status'] == 'paused_at_card' and view['paused_at_card'] == 'grid'
    refused('task_paused_at_card', tasks.admit, session, 'dispatch_status', {})
    refused('task_pause_needs_user', chat_tools.resume_task, task['task_id'])
    assert tasks.admit(session, 'validate_network', {}) is None  # unrelated reads still pass
    message = tasks.continue_at_card(session, task['task_id'])
    assert 'grid' in message
    assert chat_tools.get_task(task['task_id'])['status'] == 'ready'
    assert run(session, 'dispatch_status')['status'] == 'completed'


def test_cop1_no_pause_inside_one_card(session):
    steps = reads(2)
    chat_tools.start_task(title='Study', request_id='one-card', steps=steps, brief=brief(steps))
    assert run(session, 'project_readiness')['status'] == 'ready'


def test_cop1_continue_is_refused_when_not_paused(session):
    steps = reads(2)
    task = chat_tools.start_task(title='Study', request_id='not-paused', steps=steps, brief=brief(steps))
    refused('task_not_paused', tasks.continue_at_card, session, task['task_id'])


# ── SC-11: freshness is an unbroken fingerprint chain ────────────────────────


def edit_plan(session, request_id):
    steps = [{'id': 'cost', 'tool': 'update_component', 'args': edit(25.0)},
             {'id': 'check', 'tool': 'project_readiness', 'args': {}}]
    return chat_tools.start_task(title='Study', request_id=request_id, steps=steps, brief=brief(steps))


def test_sc11_plan_edit_keeps_the_chain(session):
    task = edit_plan(session, 'chain')
    ticket = tasks.begin_step(session, 'update_component', edit(25.0))
    card = edit_card.preview('update_component', edit(25.0))
    PyPSAService.get_network().generators.at['G1', 'marginal_cost'] = 25.0
    tasks.finish_step(ticket, result={'_edit_receipt': edit_card.receipt(card, edit(25.0))})
    view = chat_tools.get_task(task['task_id'])
    assert view['status'] == 'ready' and 'brief_stale' not in view


def test_sc11_user_edit_outside_the_plan_marks_the_brief_out_of_date(session):
    task = edit_plan(session, 'user-edit')
    PyPSAService.get_network().generators.at['G1', 'marginal_cost'] = 11.0
    assert chat_tools.get_task(task['task_id'])['brief_stale'] == 'inputs_changed_outside_plan'
    refused('task_brief_stale', tasks.admit, session, 'update_component', edit(25.0))


def test_sc11_model_edit_outside_the_plan_marks_the_brief_out_of_date(session):
    task = edit_plan(session, 'model-edit')
    chat_tools.update_component(**edit(12.0))  # an off-plan call: admit lets it pass, the chain breaks
    assert chat_tools.get_task(task['task_id'])['brief_stale'] == 'inputs_changed_outside_plan'
    refused('task_brief_stale', tasks.begin_step, session, 'update_component', edit(25.0))


def test_cop1_turn_continue_is_server_written_and_refuses_a_message(session):
    steps = reads(2)
    task = chat_tools.start_task(title='Study', request_id='turn', steps=steps,
                                 brief=brief(steps, cards=('capacity', 'grid')))
    run(session, 'project_readiness')
    frames = list(chat_service.run_turn(session, 'continue please', continue_task_id=task['task_id']))
    assert [e for e, _ in frames] == ['error', 'session_done']
    assert frames[0][1]['error_kind'] == 'plan_continue_unavailable'
    assert chat_tools.get_task(task['task_id'])['status'] == 'paused_at_card'


def test_cop1_turn_continue_for_a_plan_not_paused_is_refused(session):
    steps = reads(2)
    task = chat_tools.start_task(title='Study', request_id='turn-ready', steps=steps, brief=brief(steps))
    frames = list(chat_service.run_turn(session, '', continue_task_id=task['task_id']))
    assert frames[0][1]['error_kind'] == 'plan_continue_unavailable'


def test_unreadable_inputs_fail_closed(session, monkeypatch):
    task = edit_plan(session, 'unreadable')
    monkeypatch.setattr(tasks, '_fingerprint', lambda: None)
    assert chat_tools.get_task(task['task_id'])['brief_stale'] == 'inputs_changed_outside_plan'
    refused('task_brief_stale', tasks.admit, session, 'update_component', edit(25.0))


def test_activity_lists_a_long_paused_brief(session, baseline):
    from services.assistant_activity import _tasks
    steps = reads(14)
    task = chat_tools.start_task(title='Long study', request_id='activity', steps=steps,
                                 brief=brief(steps, cards=('capacity', 'grid')))
    for tool in ('project_readiness', 'dispatch_status', 'validate_network') * 3:
        if chat_tools.get_task(task['task_id'])['status'] == 'paused_at_card':
            break
        run(session, tool)
    owner = json.loads((baseline[2] / 'assistant_tasks' / (task['task_id'] + '.json')).read_text())['owner_id']
    rows, warnings = _tasks(baseline[2], baseline[1], owner)
    assert not warnings
    assert [(r['native_status'], r['status'], r['needs_you']) for r in rows] == [('paused_at_card', 'waiting_for_input', True)]


def test_sc11_reconcile_never_adopts_an_off_plan_edit(session):
    """GK probe: the step's write landed and the user also edited another field."""
    steps = [{'id': 'cost', 'tool': 'update_component', 'args': edit(25.0)},
             {'id': 'again', 'tool': 'update_component', 'args': edit(30.0)}]
    task = chat_tools.start_task(title='Study', request_id='reconcile-chain', steps=steps, brief=brief(steps))
    ticket = tasks.begin_step(session, 'update_component', edit(25.0))
    card = edit_card.preview('update_component', edit(25.0))
    assert tasks.finish_step(ticket, result={'_edit_receipt': edit_card.receipt(card, edit(25.0))})['status'] == 'needs_review'
    network = PyPSAService.get_network()
    network.generators.at['G1', 'marginal_cost'] = 25.0
    network.generators.at['G1', 'p_nom'] += 7
    view = chat_tools.resolve_task_step(task['task_id'], 'completed')
    assert view['completed_steps'] == 1 and view['brief_stale'] == 'inputs_changed_outside_plan'
    refused('task_brief_stale', tasks.begin_step, session, 'update_component', edit(30.0))


# ── Slice 2: queued run steps wait on their jobs (SC-08/09) ──────────────────


def sweep_case(name='Plan Sensitivity', value=20):
    return {'new_name': name, 'changes': [{'component_class': 'Generator', 'names': ['G1'],
                                           'attribute': 'marginal_cost', 'value': value}]}


def sweep_step(session, project_id, request_id, cases):
    """A brief whose one step is a sweep; the sweep is submitted and the step waits on its jobs."""
    from harness import continuation
    args = {'baseline_project_id': project_id, 'cases': cases}
    steps = [{'id': 'sweep', 'tool': 'run_sensitivity_sweep', 'args': args}]
    task = chat_tools.start_task(title='Sweep', request_id=request_id, steps=steps, brief=brief(steps))
    ticket = tasks.begin_step(session, 'run_sensitivity_sweep', args)
    submitted = chat_tools.run_sensitivity_sweep(project_id, cases)
    path = tasks.await_job(ticket, None, session.session_id, jobs=continuation.queue_jobs(submitted))
    assert path is not None
    for case in submitted['cases']:
        assert chat_tools.wait_for_job(case['job']['id'], 25)['completed']
    return task, ticket, path, submitted


def stored(root, task_id):
    return json.loads((root / 'assistant_tasks' / (task_id + '.json')).read_text())


def test_queue_step_settles_completed_only_from_native_records(session, baseline):
    from harness import continuation
    _, project_id, root = baseline
    task, ticket, path, submitted = sweep_step(session, project_id, 'q-settle', [sweep_case()])
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'waiting'
    view = continuation.settle_queue_from_record(path, ticket)
    assert view['status'] == 'completed'
    result = stored(root, task['task_id'])['steps'][0]['result']
    job_id = submitted['cases'][0]['job']['id']
    assert result['final_status'] == 'completed'
    assert result['runs'] == [{'job_id': job_id, 'run_id': run_evidence.queue_run_id(job_id),
                               'project_id': submitted['cases'][0]['project_id'], 'objective': pytest.approx(200)}]


def test_queue_step_with_inputs_edited_after_the_solve_is_uncertain(session, baseline):
    from harness import continuation
    from services import project_registry, workflow_tools as wt
    _, project_id, root = baseline
    task, ticket, path, submitted = sweep_step(session, project_id, 'q-drift', [sweep_case()])
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, submitted['cases'][0]['project_id'])
        saved = wt._load_context(child)
        saved.network.generators.at['G1', 'marginal_cost'] = 33.0  # an edit saved after the solve
        saved.network.export_to_netcdf(project_registry.project_dir(child) / 'network.nc')
    view = continuation.settle_queue_from_record(path, ticket)
    assert view['status'] == 'needs_review'
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'uncertain'


@pytest.mark.parametrize('native', ['failed', 'aborted'])
def test_queue_step_whose_only_job_failed_is_failed(session, baseline, monkeypatch, native):
    from harness import continuation
    _, project_id, root = baseline
    task, ticket, path, _ = sweep_step(session, project_id, 'q-' + native, [sweep_case()])
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: native)
    continuation.settle_queue_from_record(path, ticket)
    step = stored(root, task['task_id'])['steps'][0]
    assert step['status'] == 'failed' and native in step['error']


def test_queue_step_with_mixed_outcomes_is_uncertain_not_failed(session, baseline, monkeypatch):
    """GK change 1: effects landed for one case, so a retry would re-enqueue it."""
    from harness import continuation
    _, project_id, root = baseline
    task, ticket, path, submitted = sweep_step(session, project_id, 'q-mixed',
                                               [sweep_case(), sweep_case('Plan Sensitivity 40', 40)])
    failed = submitted['cases'][1]['job']['id']
    real = continuation._queue_job_status
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: 'failed' if job['job_id'] == failed else real(job))
    continuation.settle_queue_from_record(path, ticket)
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'uncertain'


def test_queue_follower_settles_uncertain_at_its_cap(session, baseline, monkeypatch):
    from harness import continuation
    _, project_id, root = baseline
    task, ticket, path, _ = sweep_step(session, project_id, 'q-cap', [sweep_case()])
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: 'running')
    monkeypatch.setattr(continuation, 'MAX_QUEUE_FOLLOW_SECONDS', 0.3)
    assert continuation.follow_queue_job(path, ticket).wait(10)
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'uncertain'


def dispatch(session, monkeypatch, tool, args):
    monkeypatch.setattr(session, 'wait_for_decision', lambda _: 'approve')
    results = []
    frames = list(chat_service._dispatch_real_tool_call(session, {'id': 'call-' + tool, 'name': tool, 'input': args}, results))
    return frames, results


def tool_result(results):
    content = results[-1]['content']
    return json.loads(content.removeprefix('<untrusted_data>').removesuffix('</untrusted_data>'))


def wait_until(predicate, seconds=60):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_loop_queued_step_waits_settles_and_continues_once_pausing_at_the_next_card(session, baseline, monkeypatch):
    """GK change 3: the continuation fires, and the next card's step stays refused until the user continues."""
    from harness import continuation
    _, project_id, root = baseline
    args = {'baseline_project_id': project_id, 'cases': [sweep_case()]}
    steps = [{'id': 'sweep', 'tool': 'run_sensitivity_sweep', 'args': args},
             {'id': 'check', 'tool': 'validate_network', 'args': {}}]
    task = chat_tools.start_task(title='Sweep then check', request_id='q-loop', steps=steps,
                                 brief={'objective': 'Price sensitivity, then a grid check.', 'cards': ['capacity', 'grid'],
                                        'phases': [{'card': 'capacity', 'steps': ['sweep']}, {'card': 'grid', 'steps': ['check']}]})
    frames, results = dispatch(session, monkeypatch, 'run_sensitivity_sweep', args)
    assert not [p for e, p in frames if e == 'tool_error'], frames
    step = stored(root, task['task_id'])['steps'][0]
    assert step['status'] == 'waiting' and step['job']['kind'] == 'queue'
    said = tool_result(results)
    assert said['waiting'] is True and said['job_id'] == step['job']['job_id']
    assert wait_until(lambda: stored(root, task['task_id'])['steps'][0]['status'] != 'waiting')
    record = stored(root, task['task_id'])
    assert record['steps'][0]['status'] == 'completed' and record['status'] == 'paused_at_card'
    due = continuation.due(session)
    assert [d['job_id'] for d in due] == [step['job']['job_id']]
    continuation.claim(session, step['job']['job_id'])
    assert continuation.due(session) == []
    refused('task_paused_at_card', tasks.admit, session, 'validate_network', {})
    refused('task_paused_at_card', tasks.begin_step, session, 'validate_network', {})


def test_loop_queued_step_of_a_plan_cancelled_mid_call_promises_no_wait(session, baseline, monkeypatch):
    """GK, LC-24: when await_job finds the step no longer running (the plan was Stopped mid-call),
    nothing follows it, so the result promises no wait."""
    from harness import continuation
    _, project_id, _ = baseline
    args = {'baseline_project_id': project_id, 'cases': [sweep_case()]}
    steps = [{'id': 'sweep', 'tool': 'run_sensitivity_sweep', 'args': args}]
    chat_tools.start_task(title='Sweep', request_id='q-stopped', steps=steps, brief=brief(steps))
    monkeypatch.setattr(tasks, 'await_job', lambda *a, **kw: None)  # the step stopped running during the call
    frames, results = dispatch(session, monkeypatch, 'run_sensitivity_sweep', args)
    assert not [p for e, p in frames if e == 'tool_error'], frames
    said = tool_result(results)
    assert 'waiting' not in said and 'plan waits' not in said.get('note', '')
    assert continuation.due(session) == []


def test_loop_all_reused_sweep_step_neither_waits_nor_logs_a_failure(session, baseline, monkeypatch, caplog):
    """GK: a sweep with no job has nothing to wait on; it completes from its receipt."""
    _, project_id, root = baseline
    first = chat_tools.run_sensitivity_sweep(project_id, [sweep_case()])
    assert chat_tools.wait_for_job(first['cases'][0]['job']['id'], 25)['completed']
    args = {'baseline_project_id': project_id, 'cases': [sweep_case()]}
    steps = [{'id': 'sweep', 'tool': 'run_sensitivity_sweep', 'args': args}]
    task = chat_tools.start_task(title='Reuse', request_id='q-reuse-loop', steps=steps, brief=brief(steps))
    frames, results = dispatch(session, monkeypatch, 'run_sensitivity_sweep', args)
    assert not [p for e, p in frames if e == 'tool_error'], frames
    assert 'waiting' not in tool_result(results)
    assert 'could not wait on queued jobs' not in caplog.text
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'completed'


def test_sc09_queued_step_lost_with_a_restart_is_uncertain_and_never_requeued(session, baseline):
    from services.solve_queue import solve_queue
    _, project_id, root = baseline
    task, _, _, _ = sweep_step(session, project_id, 'q-lost', [sweep_case()])  # no follower: as after a restart
    jobs = len(solve_queue.list_jobs())
    view = chat_tools.resume_task(task['task_id'])
    assert view['status'] == 'needs_review'
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'uncertain'
    refused('task_step_uncertain', tasks.begin_step, session, 'run_sensitivity_sweep',
            {'baseline_project_id': project_id, 'cases': [sweep_case()]})
    assert len(solve_queue.list_jobs()) == jobs


def test_sc13_cancel_during_a_queued_job_stops_the_plan_and_leaves_the_job_running(session, baseline, monkeypatch):
    from harness import continuation
    _, project_id, root = baseline
    task, ticket, path, _ = sweep_step(session, project_id, 'q-cancel', [sweep_case()])
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: 'running')
    follower = continuation.follow_queue_job(path, ticket)
    chat_tools.cancel_task(task['task_id'])
    assert follower.wait(10)
    record = stored(root, task['task_id'])
    assert record['status'] == 'cancelled'
    assert continuation.due(session) == []
    assert stored(root, task['task_id'])['steps'][0]['status'] == 'waiting'  # reported as it is, nothing settled it


# ── Slice 2: SC-08, SC-10, SC-14, refresh rule ───────────────────────────────


def test_sc08_run_waits_for_the_edits_verified_receipt_and_names_its_limit(session):
    steps = [{'id': 'cost', 'tool': 'update_component', 'args': edit(25.0)},
             {'id': 'solve', 'tool': 'run_simulation', 'args': {}}]
    task = chat_tools.start_task(title='Edit then run', request_id='sc08', steps=steps, brief=brief(steps))
    limits = {s['id']: s['limit'] for s in chat_tools.get_task(task['task_id'])['steps']}
    assert limits['cost'] == {'args_chars': 1800, 'result_chars': 4000, 'wait': None}
    assert limits['solve'] == {'args_chars': 1800, 'result_chars': 4000, 'wait': 'job'}
    ticket = tasks.begin_step(session, 'update_component', edit(25.0))
    card = edit_card.preview('update_component', edit(25.0))
    tasks.finish_step(ticket, result={'_edit_receipt': edit_card.receipt(card, edit(25.0))})  # read-back differs
    refused('task_step_out_of_order', tasks.admit, session, 'run_simulation', {})
    assert tasks.begin_step(session, 'run_simulation', {}) is None  # not the next step: never run as one


@pytest.mark.parametrize('ask', ['three-cases', 'commercial'])
def test_sc10_cases_execution_cannot_run_are_refused(session, baseline, ask):
    steps = [{'id': 'exp', 'tool': 'start_experiment', 'args': {
        'title': 'Cases', 'request_id': 'sc10', 'domain': 'commercial' if ask == 'commercial' else 'network',
        'cases': [{'id': 'a', 'changes': []}]}}]
    plan = brief(steps)
    if ask == 'three-cases':
        plan['cases'] = [{'name': n} for n in ('Base', 'Battery', 'Bigger connection')]
    detail = refused('brief_cases_not_runnable', chat_tools.start_task, title='Cases', request_id='sc10-' + ask,
                     steps=steps, brief=plan)
    assert 'one or two network cases' in detail['message'] and 'follow-up brief' in detail['message']
    assert not (baseline[2] / 'assistant_tasks').exists() or not list((baseline[2] / 'assistant_tasks').glob('*.json'))


def test_sc10_brief_and_advance_share_one_admission_rule(session, baseline, monkeypatch):
    """GK change 5: changing the rule moves both refusals together."""
    from harness import experiment_execution as engine
    from tests.test_experiment_case_advancement import prepared
    three = brief(reads(1))
    three['cases'] = [{'name': n} for n in ('A', 'B', 'C')]
    monkeypatch.setattr(engine, 'runnable', lambda domain, count: domain == 'network' and count <= 3)
    assert chat_tools.start_task(title='Three', request_id='rule-3', steps=reads(1), brief=three)['brief']
    eid = prepared()
    monkeypatch.setattr(engine, 'runnable', lambda domain, count: False)
    refused('brief_cases_not_runnable', chat_tools.start_task, title='One', request_id='rule-1', steps=reads(1), brief=brief(reads(1)))
    refused('experiment_adapter_unavailable', chat_tools.DISPATCHERS['advance_experiment'], experiment_id=eid)


def test_wait_for_job_is_no_step_under_a_brief_but_stays_in_a_plain_plan(session):
    steps = [{'id': 'wait', 'tool': 'wait_for_job', 'args': {'job_id': 'fg:0'}}]
    refused('invalid_task_tool', chat_tools.start_task, title='Wait', request_id='wait-brief', steps=steps, brief=brief(steps))
    assert chat_tools.start_task(title='Wait', request_id='wait-plain', steps=steps)['status'] == 'ready'


@pytest.mark.parametrize('native,status,words', [
    ('running', 'failed', 'Case not finished; retry after it completes.'),
    ('submitting', 'failed', 'Case not finished; retry after it completes.'),
    ('unavailable', 'failed', 'experiment_evidence_unavailable'),
    ('completed', 'completed', None)])
def test_refresh_experiment_completes_only_a_completed_case(session, native, status, words):
    steps = [{'id': 'look', 'tool': 'refresh_experiment', 'args': {'experiment_id': 'e1'}}]
    task = chat_tools.start_task(title='Refresh', request_id='refresh-' + native, steps=steps, brief=brief(steps))
    view = run(session, 'refresh_experiment', {'experiment_id': 'e1'},
               {'status': native, 'last_error': 'experiment_evidence_unavailable' if native == 'unavailable' else None})
    assert view['steps'][0]['status'] == status
    if words:
        assert words in view['next_step']['error']
    assert task['task_id']


def test_sc14_failed_solve_drafts_a_correction_and_never_retries_by_itself(session, baseline):
    steps = [{'id': 'solve', 'tool': 'run_simulation', 'args': {}}]
    task = chat_tools.start_task(title='Solve', request_id='sc14', steps=steps, brief=brief(steps))
    ticket = tasks.begin_step(session, 'run_simulation', {})
    path = tasks.await_job(ticket, 'fg:' + uuid.uuid4().hex, session.session_id)
    view = tasks.settle_job(path, ticket, tasks._read(task['task_id'])[1]['steps'][0]['job']['job_id'], 'failed', 'failed')
    correction = view['next_step']['correction']
    assert correction['triage'] == ['get_simulation_log_history', 'diagnose_network', 'validate_network']
    assert 'infeasible' not in json.dumps(correction)  # names the triage, decides nothing
    assert len(json.dumps(view)) <= 3800
    refused('task_step_needs_retry', tasks.begin_step, session, 'run_simulation', {})
    refused('task_step_needs_retry', tasks.admit, session, 'run_simulation', {})
    assert chat_tools.resolve_task_step(task['task_id'], 'retry')['next_step']['status'] == 'pending'
    assert tasks.begin_step(session, 'run_simulation', {}) is not None


def test_a_sweep_whose_cases_were_all_reused_completes_from_its_receipt(session, baseline):
    _, project_id, _ = baseline
    first = chat_tools.run_sensitivity_sweep(project_id, [sweep_case()])
    assert chat_tools.wait_for_job(first['cases'][0]['job']['id'], 25)['completed']
    args = {'baseline_project_id': project_id, 'cases': [sweep_case()]}
    steps = [{'id': 'sweep', 'tool': 'run_sensitivity_sweep', 'args': args}]
    chat_tools.start_task(title='Reuse', request_id='q-reuse', steps=steps, brief=brief(steps))
    ticket = tasks.begin_step(session, 'run_sensitivity_sweep', args)
    again = chat_tools.run_sensitivity_sweep(project_id, [sweep_case()])
    assert again['cases'][0]['reused'] and 'job' not in again['cases'][0]
    assert tasks.finish_step(ticket, result=again)['status'] == 'completed'


def test_an_enqueue_receipt_is_never_completion_without_its_record(session, baseline):
    _, project_id, _ = baseline
    steps = [{'id': 'enq', 'tool': 'solve_queue_enqueue', 'args': {'project_id': project_id}}]
    chat_tools.start_task(title='Enqueue', request_id='q-enq', steps=steps)
    ticket = tasks.begin_step(session, 'solve_queue_enqueue', {'project_id': project_id})
    view = tasks.finish_step(ticket, result={'id': str(uuid.uuid4()), 'status': 'completed'})
    assert view['steps'][0]['status'] == 'uncertain'


def test_sc10_a_direct_sweep_step_is_held_to_the_same_rule(session, baseline):
    _, project_id, _ = baseline
    cases = [sweep_case(f'Case {n}', 20 + n) for n in range(3)]
    steps = [{'id': 'sweep', 'tool': 'run_sensitivity_sweep', 'args': {'baseline_project_id': project_id, 'cases': cases}}]
    refused('brief_cases_not_runnable', chat_tools.start_task, title='Three', request_id='sweep-3', steps=steps, brief=brief(steps))


def test_a_step_is_retried_at_most_twice(session):
    task = chat_tools.start_task(title='Export', request_id='retry-bound', steps=[
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}}])
    for attempt in range(3):
        tasks.finish_step(tasks.begin_step(session, 'export_network_nc', {}), error='x')
        if attempt < 2:
            view = chat_tools.resolve_task_step(task['task_id'], 'retry')
            assert view['next_step']['status'] == 'pending'
    path, _ = tasks._read(task['task_id'])
    before = path.read_bytes()
    refused('task_step_retry_limit', chat_tools.resolve_task_step, task['task_id'], 'retry')
    assert path.read_bytes() == before
    view = chat_tools.get_task(task['task_id'])
    assert view['next_step']['retries'] == 2
    assert 'correction' not in view['next_step']


def test_a_refused_retry_does_not_count(session):
    task = chat_tools.start_task(title='Export', request_id='retry-worker', steps=[
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}}])
    ticket = tasks.begin_step(session, 'export_network_nc', {})
    worker = Future()
    tasks.register_worker(ticket, worker)
    tasks.finish_step(ticket, error='tool_timeout')
    refused('task_step_running', chat_tools.resolve_task_step, task['task_id'], 'retry')
    assert tasks._read(task['task_id'])[1]['steps'][0].get('retries', 0) == 0
    worker.set_result(None)
    for count in (1, 2):
        view = chat_tools.resolve_task_step(task['task_id'], 'retry')
        assert view['next_step']['retries'] == count
        tasks.finish_step(tasks.begin_step(session, 'export_network_nc', {}), error='x')


def test_retry_limit_correction_stops_instead_of_triage(session):
    steps = [{'id': 'solve', 'tool': 'run_simulation', 'args': {}}]
    task = chat_tools.start_task(title='Solve', request_id='retry-triage', steps=steps, brief=brief(steps))
    for attempt in range(3):
        ticket = tasks.begin_step(session, 'run_simulation', {})
        job_id = 'fg:' + uuid.uuid4().hex
        path = tasks.await_job(ticket, job_id, session.session_id)
        view = tasks.settle_job(path, ticket, job_id, 'failed', 'failed')
        if attempt < 2:
            chat_tools.resolve_task_step(task['task_id'], 'retry')
    assert view['next_step']['correction'] == {
        'stop': 'retry_limit',
        'then': 'explain what failed; the user decides: fix the inputs, or cancel_task',
    }
    assert view['next_step']['retries'] == 2
    assert len(json.dumps(view)) <= 3800


def test_cancel_still_works_at_the_retry_limit(session):
    task = chat_tools.start_task(title='Export', request_id='retry-cancel', steps=[
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}}])
    for attempt in range(3):
        tasks.finish_step(tasks.begin_step(session, 'export_network_nc', {}), error='x')
        if attempt < 2:
            chat_tools.resolve_task_step(task['task_id'], 'retry')
    assert chat_tools.cancel_task(task['task_id'])['status'] == 'cancelled'
    assert chat_tools.get_task(task['task_id'])['status'] == 'cancelled'


def test_old_task_files_without_retries_still_retry(session):
    task = chat_tools.start_task(title='Export', request_id='retry-legacy', steps=[
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}}])
    tasks.finish_step(tasks.begin_step(session, 'export_network_nc', {}), error='x')
    path, saved = tasks._read(task['task_id'])
    saved['steps'][0].pop('retries', None)
    path.write_text(json.dumps(saved))
    view = chat_tools.resolve_task_step(task['task_id'], 'retry')
    assert view['next_step']['status'] == 'pending'
    assert view['next_step']['retries'] == 1
    assert tasks._read(task['task_id'])[1]['steps'][0]['retries'] == 1
