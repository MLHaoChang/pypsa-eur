# ruff: noqa: F811 -- imported pytest fixtures
"""LC-45: a profile generation and a decision study run as `start_task` plan
steps, so the conversation continues by itself and the model never polls."""
import pytest

from harness import continuation
from services import assistant_tasks as tasks, chat_tools
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    brief, dispatch, session, stored, tool_result, wait_until,
)
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture
def no_poll(monkeypatch):
    """Any poll call the model could make fails the test."""
    def poll(*a, **k):
        raise AssertionError("polled")
    for name in ("solve_queue_list", "get_study_findings", "list_pending_profiles"):
        monkeypatch.setitem(chat_tools.DISPATCHERS, name, poll)


def _wait_done(root, task_id, n=0):
    assert wait_until(lambda: stored(root, task_id)['steps'][n]['status'] != 'waiting')
    return stored(root, task_id)


def _profile_plan(root, request_id, monkeypatch, status, evidence):
    args = {'project_id': 'p', 'technology': 'solar', 'points': []}
    steps = [{'id': 'gen', 'tool': 'generate_renewable_profile', 'args': args}]
    task = chat_tools.start_task(title='Profile', request_id=request_id, steps=steps, brief=brief(steps))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'generate_renewable_profile',
                        lambda **k: {'id': 'job-1', 'kind': 'profile_gen', 'project_key': 'org:uuid-1', 'status': 'queued'})
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: status)
    monkeypatch.setattr(continuation, '_queue_job_error', lambda job: 'ERA5 needs CDS credentials')
    monkeypatch.setattr(continuation, '_profile_artifact', lambda job: evidence)
    return task, args


def test_profile_generation_continues_without_a_poll(session, baseline, monkeypatch, no_poll):
    _, _, root = baseline
    task, args = _profile_plan(root, 'lc45-p1', monkeypatch, 'completed', {'job_id': 'job-1'})
    frames, results = dispatch(session, monkeypatch, 'generate_renewable_profile', args)
    assert not [p for e, p in frames if e == 'tool_error'], frames
    assert tool_result(results)['waiting'] is True
    record = _wait_done(root, task['task_id'])
    assert record['steps'][0]['status'] == 'completed'


def test_a_failed_profile_job_fails_the_step_with_its_own_error(session, baseline, monkeypatch, no_poll):
    _, _, root = baseline
    task, args = _profile_plan(root, 'lc45-p2', monkeypatch, 'failed', None)
    dispatch(session, monkeypatch, 'generate_renewable_profile', args)
    record = _wait_done(root, task['task_id'])
    step = record['steps'][0]
    assert step['status'] == 'failed'
    assert 'ERA5 needs CDS credentials' in str(step)


def test_a_completed_profile_job_without_its_artifact_is_not_completed(monkeypatch):
    monkeypatch.setattr(continuation, '_queue_job_status', lambda job: 'completed')
    monkeypatch.setattr(continuation, '_profile_artifact', lambda job: None)
    status, _, result = continuation.queue_outcome([{'job_id': 'j', 'project_id': 'p', 'kind': 'profile_gen'}])
    assert status == 'uncertain' and result['cases'][0]['status'] == 'unverified'


def test_plan_jobs_for_a_study_stage_and_a_profile():
    args = {'project': 'p', 'study_id': 's1'}
    (job,) = continuation.plan_jobs('run_decision_study', args, {'stage': 'tornado', 'run': {'status': 'running'}})
    assert job['job_id'] == 'study:s1:tornado' and job['kind'] == 'study_tornado'
    (job,) = continuation.plan_jobs('run_decision_study', args, {'stage': 'options'})
    assert job['kind'] == 'study_options'
    assert continuation.plan_jobs('run_decision_study', {}, {'stage': 'options'}) == []
    (job,) = continuation.plan_jobs('generate_renewable_profile', {}, {'id': 'j', 'project_key': 'o:u'})
    assert job['kind'] == 'profile_gen' and job['project_id'] == 'u'


@pytest.mark.parametrize('record, expected', [
    ({'status': 'done'}, 'completed'), ({'status': 'running'}, 'running'),
    ({'status': 'aborting'}, 'running'), ({'status': 'failed'}, 'failed'), ({}, 'unknown')])
def test_study_record_status_maps_to_the_queue_vocabulary(monkeypatch, record, expected):
    monkeypatch.setattr(continuation, '_study_record', lambda job: record)
    assert continuation._queue_job_status({'job_id': 'x', 'kind': 'study_options'}) == expected


def _study_plan(root, request_id, monkeypatch, records):
    a1 = {'project': 'p', 'study_id': 's1', 'stage': 'options'}
    a2 = {'project': 'p', 'study_id': 's1', 'stage': 'tornado'}
    steps = [{'id': 'options', 'tool': 'run_decision_study', 'args': a1},
             {'id': 'tornado', 'tool': 'run_decision_study', 'args': a2}]
    task = chat_tools.start_task(title='Decision study', request_id=request_id, steps=steps, brief=brief(steps))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'run_decision_study',
                        lambda **k: {'stage': k['stage'], 'run': {'status': 'running'}})
    monkeypatch.setattr(continuation, '_study_record', lambda job: records[job['kind']])
    monkeypatch.setattr(continuation, '_queue_job_error', lambda job: records[job['kind']].get('error'))
    return task, a1, a2


def test_decision_study_runs_both_stages_without_a_poll(session, baseline, monkeypatch, no_poll):
    _, _, root = baseline
    task, a1, a2 = _study_plan(root, 'lc45-s1', monkeypatch,
                               {'study_options': {'status': 'done'}, 'study_tornado': {'status': 'done'}})
    dispatch(session, monkeypatch, 'run_decision_study', a1)
    assert _wait_done(root, task['task_id'], 0)['steps'][0]['status'] == 'completed'
    dispatch(session, monkeypatch, 'run_decision_study', a2)
    assert _wait_done(root, task['task_id'], 1)['steps'][1]['status'] == 'completed'


def test_a_failed_study_run_fails_the_step_with_the_run_error(session, baseline, monkeypatch, no_poll):
    _, _, root = baseline
    task, a1, _ = _study_plan(root, 'lc45-s2', monkeypatch,
                              {'study_options': {'status': 'failed', 'error': 'budget exhausted'},
                               'study_tornado': {}})
    dispatch(session, monkeypatch, 'run_decision_study', a1)
    step = _wait_done(root, task['task_id'], 0)['steps'][0]
    assert step['status'] == 'failed' and 'budget exhausted' in str(step)
