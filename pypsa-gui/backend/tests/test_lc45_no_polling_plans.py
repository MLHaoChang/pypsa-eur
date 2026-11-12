# ruff: noqa: F811 -- imported pytest fixtures
"""LC-45: a profile generation and a decision study run as `start_task` plan
steps, so the conversation continues by itself and the model never polls.

The real follower runs (short poll, short cap); only the native reads under it
(the queue job, the pending artifacts, the study record) are faked."""
import time

import pytest
from fastapi import HTTPException

from harness import continuation
from services import chat_tools
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    brief, dispatch, session, stored, tool_result, wait_until,
)
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(continuation, 'QUEUE_POLL_SECONDS', 0.05)

    def poll(*a, **k):
        raise AssertionError('polled')
    for name in ('solve_queue_list', 'get_study_findings'):
        monkeypatch.setitem(chat_tools.DISPATCHERS, name, poll)


def _wait_done(root, task_id, n=0):
    assert wait_until(lambda: stored(root, task_id)['steps'][n]['status'] != 'waiting', 30)
    return stored(root, task_id)['steps'][n]


# ── profile generation (L1, L2, L10) ─────────────────────────────────────────

ARGS = {'project_id': 'p', 'technology': 'solar', 'source': 'synthetic', 'points': []}


def _profile(monkeypatch, request_id, job, artifacts):
    """A one-step profile plan; `job` is the native queue row, `artifacts` the pending list."""
    import routers.solve_queue as sq
    steps = [{'id': 'gen', 'tool': 'generate_renewable_profile', 'args': ARGS}]
    task = chat_tools.start_task(title='Profile', request_id=request_id, steps=steps, brief=brief(steps))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'generate_renewable_profile',
                        lambda **k: {'id': 'job-1', 'kind': 'profile_gen', 'project_key': 'org:uuid-1', 'status': 'queued'})
    monkeypatch.setattr(sq, '_visible_job_or_404', lambda db, user, job_id: job)
    seen = []

    def pending(project_id):
        seen.append(project_id)
        return {'pending': artifacts}
    monkeypatch.setattr(chat_tools, 'list_pending_profiles', pending)
    return task, seen


def _artifact(job_id='job-1', **kw):
    return {'job_id': job_id, 'source': 'synthetic', 'technology': 'solar', **kw}


def test_profile_job_completes_the_step_from_its_own_artifact(session, baseline, monkeypatch):
    _, _, root = baseline
    task, seen = _profile(monkeypatch, 'lc45-p1', {'status': 'completed'}, [_artifact()])
    frames, results = dispatch(session, monkeypatch, 'generate_renewable_profile', ARGS)
    assert not [p for e, p in frames if e == 'tool_error'], frames
    assert tool_result(results)['waiting'] is True
    assert _wait_done(root, task['task_id'])['status'] == 'completed'
    assert seen and set(seen) == {'p'}  # read from the project the call named


def test_a_failed_profile_job_fails_the_step_with_its_own_error(session, baseline, monkeypatch):
    _, _, root = baseline
    task, _ = _profile(monkeypatch, 'lc45-p2', {'status': 'failed', 'error': 'ERA5 needs CDS credentials'}, [])
    dispatch(session, monkeypatch, 'generate_renewable_profile', ARGS)
    step = _wait_done(root, task['task_id'])
    assert step['status'] == 'failed' and 'ERA5 needs CDS credentials' in str(step)


@pytest.mark.parametrize('artifacts', [
    [_artifact('earlier-job')],                      # an earlier run's artifact (L1)
    [_artifact(source='atlite')],                    # not the source asked for
    [_artifact(technology='wind')],                  # not the technology asked for
    []])
def test_a_completed_profile_job_without_its_own_artifact_is_uncertain(session, baseline, monkeypatch, artifacts):
    _, _, root = baseline
    task, _ = _profile(monkeypatch, 'lc45-p3', {'status': 'completed'}, artifacts)
    dispatch(session, monkeypatch, 'generate_renewable_profile', ARGS)
    assert _wait_done(root, task['task_id'])['status'] == 'uncertain'


def test_an_unreadable_artifact_listing_is_uncertain(session, baseline, monkeypatch):
    _, _, root = baseline
    task, _ = _profile(monkeypatch, 'lc45-p4', {'status': 'completed'}, [])

    def boom(project_id):
        raise HTTPException(403, 'no')
    monkeypatch.setattr(chat_tools, 'list_pending_profiles', boom)
    dispatch(session, monkeypatch, 'generate_renewable_profile', ARGS)
    assert _wait_done(root, task['task_id'])['status'] == 'uncertain'


def test_an_unknown_job_kind_is_never_completed(monkeypatch):
    import routers.solve_queue as sq
    monkeypatch.setattr(sq, '_visible_job_or_404', lambda db, user, job_id: {'status': 'completed'})
    monkeypatch.setattr(continuation, '_queue_run', lambda job: {'x': 1})
    status, _, result = continuation.queue_outcome([{'job_id': 'j', 'project_id': 'p', 'kind': 'mystery'}])
    assert status == 'uncertain' and result['cases'][0]['status'] == 'unknown'


# ── decision study (L3, L4, L5, L6, L10) ─────────────────────────────────────

S1 = {'project': 'p', 'study_id': 's1'}


def _study(monkeypatch, request_id, records):
    """A two-step plan; `records` maps a stage to a list of successive records (the last repeats)."""
    steps = [{'id': 'options', 'tool': 'run_decision_study', 'args': {**S1, 'stage': 'options'}},
             {'id': 'tornado', 'tool': 'run_decision_study', 'args': {**S1, 'stage': 'tornado'}}]
    task = chat_tools.start_task(title='Decision study', request_id=request_id, steps=steps, brief=brief(steps))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'run_decision_study',
                        lambda **k: {'stage': k['stage'], 'run': {'status': 'running'}})
    queue = {stage: list(recs) for stage, recs in records.items()}

    def record(job):
        recs = queue[job['kind'].removeprefix('study_')]
        return dict(recs.pop(0) if len(recs) > 1 else recs[0])
    monkeypatch.setattr(continuation, '_study_record', record)
    return task


def _run(session, monkeypatch, stage):
    return dispatch(session, monkeypatch, 'run_decision_study', {**S1, 'stage': stage})


def _rec(status='done', started=1.0, **kw):
    return {'status': status, 'started_at': started, **kw}


def test_both_stages_continue_without_a_poll(session, baseline, monkeypatch):
    _, _, root = baseline
    task = _study(monkeypatch, 'lc45-s1', {'options': [_rec('running'), _rec()], 'tornado': [_rec('aborting'), _rec()]})
    _run(session, monkeypatch, 'options')
    assert _wait_done(root, task['task_id'], 0)['status'] == 'completed'
    _run(session, monkeypatch, 'tornado')
    assert _wait_done(root, task['task_id'], 1)['status'] == 'completed'


@pytest.mark.parametrize('extra', [
    {'pending': ['opt-b']}, {'budget_exhausted': True}, {'deadline_exceeded': 'solve_deadline_exceeded'}])
def test_a_run_that_left_something_unreached_is_not_plain_completed(session, baseline, monkeypatch, extra):
    _, _, root = baseline
    task = _study(monkeypatch, 'lc45-s2', {'options': [_rec(**extra)], 'tornado': [_rec()]})
    _run(session, monkeypatch, 'options')
    step = _wait_done(root, task['task_id'], 0)
    assert step['status'] == 'uncertain'


@pytest.mark.parametrize('status', ['failed', 'aborted'])
def test_a_failed_or_aborted_run_fails_the_step_with_the_record_error(session, baseline, monkeypatch, status):
    _, _, root = baseline
    task = _study(monkeypatch, 'lc45-s3-' + status, {'options': [_rec(status, error='solver died')], 'tornado': [_rec()]})
    _run(session, monkeypatch, 'options')
    step = _wait_done(root, task['task_id'], 0)
    assert step['status'] == 'failed' and 'solver died' in str(step)


def test_a_vanished_record_settles_uncertain(session, baseline, monkeypatch):
    _, _, root = baseline
    task = _study(monkeypatch, 'lc45-s4', {'options': [_rec('running'), {}], 'tornado': [_rec()]})
    _run(session, monkeypatch, 'options')
    assert _wait_done(root, task['task_id'], 0)['status'] == 'uncertain'


def test_a_newer_run_never_settles_this_step(session, baseline, monkeypatch):
    _, _, root = baseline
    task = _study(monkeypatch, 'lc45-s5', {'options': [_rec('running'), _rec('done', started=2.0)], 'tornado': [_rec()]})
    _run(session, monkeypatch, 'options')
    assert _wait_done(root, task['task_id'], 0)['status'] == 'uncertain'


def test_a_study_that_never_ends_settles_uncertain_at_the_cap(session, baseline, monkeypatch):
    _, _, root = baseline
    monkeypatch.setattr(continuation, 'MAX_QUEUE_FOLLOW_SECONDS', 0.3)
    task = _study(monkeypatch, 'lc45-s6', {'options': [_rec('running')], 'tornado': [_rec()]})
    start = time.monotonic()
    _run(session, monkeypatch, 'options')
    assert _wait_done(root, task['task_id'], 0)['status'] == 'uncertain'
    assert time.monotonic() - start < 20


def test_a_study_the_caller_cannot_read_is_refused_not_followed(session, baseline, monkeypatch):
    """The real record read, under an ACL that refuses another org's study (L6)."""
    _, _, root = baseline
    task = _study(monkeypatch, 'lc45-s7', {'options': [_rec()], 'tornado': [_rec()]})
    monkeypatch.undo()  # keep the real `_study_record`, so the real read runs

    def refused(name):
        raise HTTPException(403, 'not yours')
    monkeypatch.setattr(chat_tools, '_authorized_project', refused)
    assert continuation._study_record({'kind': 'study_options', 'study_id': 's1', 'project_id': 'other-org/p'}) == {}
    status, _, result = continuation.queue_outcome(
        [{'job_id': 'study:s1:options', 'kind': 'study_options', 'study_id': 's1', 'project_id': 'other-org/p', 'attempt': 1.0}])
    assert status == 'uncertain'


def test_a_restart_settles_a_waiting_study_step_uncertain(session, baseline, monkeypatch):
    """Boot recovery (`Runner._recover`) re-reads the waiting step; the record is gone."""
    from harness import runner as harness_runner
    _, _, root = baseline
    monkeypatch.setattr(continuation, 'QUEUE_POLL_SECONDS', 60)  # the live follower sleeps: only boot recovery settles
    task = _study(monkeypatch, 'lc45-s8', {'options': [_rec('running')], 'tornado': [_rec()]})
    _run(session, monkeypatch, 'options')
    assert wait_until(lambda: stored(root, task['task_id'])['steps'][0]['status'] == 'waiting', 10)
    path = root / 'assistant_tasks' / (task['task_id'] + '.json')
    monkeypatch.setattr(continuation, '_study_record', lambda job: {})  # the restart lost the record
    try:
        harness_runner.Runner('boot-x')._recover(path)
    except Exception:  # noqa: BLE001 — a non-run task fails after the step loop
        pass
    assert _wait_done(root, task['task_id'], 0)['status'] == 'uncertain'
