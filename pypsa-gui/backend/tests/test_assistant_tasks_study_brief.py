"""LC-23 slice 1: the study brief on `start_task` (owner decisions AI-567b, COP-1).

SC-05 admission, SC-11 freshness, SC-12 native reconciliation and the COP-1
card pause. Real project, ordinary dispatch seam; no provider calls."""
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
