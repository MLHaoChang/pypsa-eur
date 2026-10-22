"""Action outcomes, detached workers and semantic queue failures."""
import pytest
from fastapi import HTTPException
from services import assistant_tasks as tasks, chat_tools, chat_service
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture
def session(baseline):
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    return session


@pytest.mark.parametrize('result', [
    {'job': {'id': 'job', 'status': 'failed'}, 'terminal': True, 'completed': False, 'timed_out': False},
    {'job': {'id': 'job', 'status': 'aborted'}, 'terminal': True, 'completed': False, 'timed_out': False},
])
def test_failed_terminal_job_cannot_complete_wait_step(session, result):
    task = chat_tools.start_task(title='Wait for result', request_id='failed-wait', steps=[
        {'id': 'wait', 'tool': 'wait_for_job', 'args': {'job_id': 'job'}}])
    ticket = tasks.begin_step(session, 'wait_for_job', {'job_id': 'job'})
    state = tasks.finish_step(ticket, result=result)
    assert state['completed_steps'] == 0 and state['status'] != 'completed'
    assert state['next_step']['status'] == 'failed'
    # LC-23 SC-12: a caller-supplied result is no evidence; without a native
    # record the step stays to review, and only an explicit retry goes on.
    with pytest.raises(HTTPException) as failure:
        chat_tools.resolve_task_step(task['task_id'], 'completed', {'job': {'status': 'completed'}})
    assert failure.value.detail['error_kind'] == 'task_resolution_unverifiable'
    assert chat_tools.resolve_task_step(task['task_id'], 'retry')['next_step']['status'] == 'pending'


def test_partial_effectful_submission_requires_review(session):
    task = chat_tools.start_task(title='Sweep', request_id='partial-sweep', steps=[
        {'id': 'submit', 'tool': 'run_sensitivity_sweep', 'args': {'baseline_project_id': 'Tool Baseline', 'cases': [
            {'new_name': 'Child', 'changes': [{'component_class': 'Generator', 'names': ['G1'], 'attribute': 'marginal_cost', 'value': 20}]}]}}])
    ticket = tasks.begin_step(session, 'run_sensitivity_sweep', task['next_step']['args'])
    state = tasks.finish_step(ticket, result={'status': 'partial', 'cases': [{'project_id': 'created'}]})
    assert state['status'] == 'needs_review' and state['completed_steps'] == 0


def test_choice_request_cannot_be_a_durable_action_step(session):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        chat_tools.start_task(title='Choice', request_id='choice', steps=[
            {'id': 'choose', 'tool': 'ask_user', 'args': {'title': 'Choose', 'question': 'Choose?', 'options': [{'label': 'A'}, {'label': 'B'}]}}])
