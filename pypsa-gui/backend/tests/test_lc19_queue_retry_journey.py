"""LC-19 Tier A, OJ-34: queue three sensitivities, follow, retry, cancel.

The saved `oj-grid` baseline; the sweep's three cases solve on the real queue
with real HiGHS. "Load 150" asks for more than Gas (100 MW) can give in the dark hours:
valid inputs, an infeasible solve. The queue's solver is wrapped so the
"Gas 70" case is held running until the test lets it go: the retry then waits
behind it, and "cancel everything still queued" has a running job to leave
alone. Only provider HTTP is mocked.
"""
import threading
import time
import uuid

import pytest

from services import chat_tools, project_registry, solver_service
from services import workflow_tools as wt
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.journey_wire import run_journey
from tests.oj_grid import NAME, build_oj_grid
from tests.test_oj07_edit_relaunch_walk_journey import _approve
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

CASES = [
    {'new_name': 'oj Gas 60', 'changes': [{'component_class': 'Generator', 'names': ['Gas'],
                                           'attribute': 'marginal_cost', 'value': 60}]},
    {'new_name': 'oj Load 150', 'changes': [{'component_class': 'Load', 'names': ['Demand'],
                                             'attribute': 'p_set', 'value': 150}]},
    {'new_name': 'oj Gas 70', 'changes': [{'component_class': 'Generator', 'names': ['Gas'],
                                           'attribute': 'marginal_cost', 'value': 70}]},
]


@pytest.fixture
def baseline(client, session_ctx, install_network, monkeypatch):
    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    release = threading.Event()
    real = solver_service.run_simulation

    def held(config, n, lock, stop_event, log_queue, **kwargs):
        if n.generators.at['Gas', 'marginal_cost'] == 70:
            while not release.is_set() and not stop_event.is_set():
                stop_event.wait(0.05)
            if stop_event.is_set():
                return 'aborted', 'aborted'
        return real(config, n, lock, stop_event, log_queue, **kwargs)

    monkeypatch.setattr(solver_service, 'run_simulation', held)
    try:
        yield {'project_id': chat_tools.project_readiness()['project_id'], 'release': release}
    finally:
        release.set()
        _wait(lambda: not [j for j in solve_queue.list_jobs() if j['status'] in ('queued', 'running')])
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _wait(condition, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    raise AssertionError('timed out')


def _status(job_id):
    return (solve_queue.get_job(uuid.UUID(job_id)) or {}).get('status')


def _case_fingerprint(project_id):
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, project_id)
        return wt._network_input_fingerprint(wt._load_context(project))


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_oj34_queue_follow_retry_cancel(baseline, kind, model, base_url, auth):
    provider = (kind, model, base_url, auth)
    cards = []
    calls, results, frames, session = run_journey(
        *provider, [lambda r: ('run_sensitivity_sweep', {'baseline_project_id': baseline['project_id'],
                                                        'cases': CASES}),
                    lambda r: (None, {})],
        'Queue the three sensitivity cases.', on_frame=_approve(cards))
    assert calls == ['run_sensitivity_sweep', None], results
    assert 'cases' in results[0], results[0]
    assert [c['tool_name'] for c in cards] == ['run_sensitivity_sweep']  # one card, three jobs
    jobs = {c['name']: c['job']['id'] for c in results[0]['cases']}
    assert len(jobs) == 3
    failed, held = jobs['oj Load 150'], jobs['oj Gas 70']
    _wait(lambda: _status(failed) == 'failed' and _status(held) == 'running')
    assert _status(jobs['oj Gas 60']) == 'completed'
    failed_project = next(c['project_id'] for c in results[0]['cases'] if c['name'] == 'oj Load 150')
    before = _case_fingerprint(failed_project)

    calls, results, frames, _ = run_journey(*provider, [lambda r: ('solve_queue_list', {}), lambda r: (None, {})],
                                            "What's in the queue?", session=session)
    listed = {j['id']: j['status'] for j in results[0]['jobs']}
    assert listed[failed] == 'failed' and listed[held] == 'running'
    assert results[0]['running'] == [held]

    cards = []
    calls, results, frames, _ = run_journey(
        *provider, [lambda r: ('solve_queue_requeue', {'job_id': failed}), lambda r: (None, {})],
        'Retry the failed one.', session=session, on_frame=_approve(cards))
    assert [c['tool_name'] for c in cards] == ['solve_queue_requeue']
    retry = results[0]['id']
    assert retry != failed and results[0]['already_queued'] is False
    assert _status(retry) == 'queued'  # behind the held job: one job, same project
    assert _case_fingerprint(failed_project) == before  # the same saved inputs
    old = solve_queue._jobs[uuid.UUID(failed)]
    new = solve_queue._jobs[uuid.UUID(retry)]
    assert new.project_key == old.project_key and new.solver_config_json == old.solver_config_json

    cards = []
    calls, results, frames, _ = run_journey(
        *provider, [lambda r: ('solve_queue_cancel_queued', {}), lambda r: (None, {})],
        'Cancel everything still queued.', session=session, on_frame=_approve(cards))
    assert [c['tool_name'] for c in cards] == ['solve_queue_cancel_queued']
    assert results[0] == {'cancelled': 1}
    assert _status(retry) == 'aborted'
    assert _status(held) == 'running'  # the running job is never touched
    baseline['release'].set()
    _wait(lambda: _status(held) == 'completed')
    # No fresh enqueue happened anywhere along the way.
    assert len(solve_queue.list_jobs()) == 4


def test_oj34_a_denied_retry_queues_nothing(baseline):
    calls, results, frames, session = run_journey(
        *PROVIDERS[1], [lambda r: ('run_sensitivity_sweep', {'baseline_project_id': baseline['project_id'],
                                                             'cases': CASES[1:2]}), lambda r: (None, {})],
        'Queue the no-gas case.', on_frame=_approve([]))
    assert 'cases' in results[0], results[0]
    failed = results[0]['cases'][0]['job']['id']
    _wait(lambda: _status(failed) == 'failed')

    def deny(event, data):
        if event == 'tool_pending_confirmation':
            chat_tools.chat_session().record_decision(data['confirmation_token'], 'deny')
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('solve_queue_requeue', {'job_id': failed}), lambda r: (None, {})],
        'Retry the failed one.', session=session, on_frame=deny)
    assert [d['error_kind'] for e, d in frames if e == 'tool_error'] == ['confirmation_denied']
    assert len(solve_queue.list_jobs()) == 1
