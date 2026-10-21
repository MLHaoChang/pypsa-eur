"""OJ-07/08/10 (VS-4, LC-05 with LC-12): "Set L1 and L2 s_nom to 500 and relaunch
the simulation, then show me the load flow."

Tier A: the `oj-grid` fixture solved once by real HiGHS, then a scripted model
over the real provider wires. One `start_task` plan (edit, edit, run, walk);
each edit shows its card and is read back natively before the run is admitted;
the run shows its own card and releases the turn. The solve is held before
HiGHS by a gate, so the job ends after the turn: the conversation then
continues with one continuation turn ("job <id> finished: <status>") that
walks the new run's load flow. Only provider HTTP is mocked.
"""
import json
import re
import threading
import time

import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import NAME, build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

MESSAGE = 'Set L1 and L2 s_nom to 500 and relaunch the simulation, then show me the load flow.'
TITLE = 'Set L1 and L2 s_nom to 500, re-solve, show the load flow'


def _plan(l2=500):
    return [{'id': 'l1', 'tool': 'update_component',
             'args': {'component_class': 'Line', 'name': 'L1', 'attrs': {'s_nom': 500}}},
            {'id': 'l2', 'tool': 'update_component',
             'args': {'component_class': 'Line', 'name': 'L2', 'attrs': {'s_nom': l2}}},
            {'id': 'run', 'tool': 'run_simulation', 'args': {}},
            {'id': 'walk', 'tool': 'get_loadflow_summary', 'args': {'run_id': '$run.run_id'}},
            {'id': 'show', 'tool': 'ui_open_panel', 'args': {'panel_id': 'results', 'results_tab': 'loadflow'}}]


class Gate:
    """Holds the real solve before HiGHS: until `open` is set, or for
    `hold_seconds`. An abort (stop_event) ends the hold as the solver would."""

    def __init__(self):
        self.open = threading.Event()
        self.hold_seconds = None
        self.starts = []

    def wrap(self, real):
        def run_simulation(config, n, lock, stop_event, log_queue, **kwargs):
            self.starts.append(time.monotonic())
            log_queue.put('Held before HiGHS by the journey fixture.')
            deadline = time.monotonic() + (self.hold_seconds if self.hold_seconds is not None else 300)
            while time.monotonic() < deadline and not stop_event.is_set():
                if self.hold_seconds is None and self.open.is_set():
                    break
                stop_event.wait(0.05)
            if stop_event.is_set():
                return 'aborted', 'aborted'
            return real(config, n, lock, stop_event, log_queue, **kwargs)
        return run_simulation


@pytest.fixture
def oj_grid(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm
    import routers.simulation as simulation

    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    gate = Gate()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            _solve_once(session)
        chat_tools.set_chat_session(None)
        monkeypatch.setattr(simulation, 'run_simulation', gate.wrap(simulation.run_simulation))
        yield gate
    finally:
        gate.open.set()
        _settle_solver()
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _settle_solver(timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and PyPSAService.get_solver_state().get('status') == 'running':
        time.sleep(0.05)


def _approve(cards):
    """The browser's half: every card is read, then approved by a click."""
    def on_frame(event, data):
        if event == 'tool_pending_confirmation':
            cards.append(data)
            chat_tools.chat_session().record_decision(data['confirmation_token'], 'approve')
    return on_frame


def _plan_steps(plan):
    return [lambda r: ('start_task', {'title': TITLE, 'request_id': 'oj07', 'steps': plan}),
            lambda r: ('update_component', r[0]['next_step']['args']),
            lambda r: ('update_component', r[1]['_task']['next_step']['args']),
            lambda r: ('run_simulation', {}),
            lambda r: (None, {})]


def _walk_steps(task_id):
    return [lambda r: ('resume_task', {'task_id': task_id}),
            lambda r: ('get_loadflow_summary', r[0]['next_step']['args']),
            lambda r: ('ui_open_panel', r[1]['_task']['next_step']['args']),
            lambda r: (None, {})]


def _walk_answer(results):
    task, lf = results[0], results[1]
    top = ', '.join(f"{row['name']} {row['util_pct']} % ({row['peak_flow']} MW of {row['rating']['value']} MVA)"
                    for row in lf['top'][:3])
    return (f"The re-solve finished after {'; '.join(task['changes'])}. "
            f"Run {lf['run']['run_id']} is current. Highest loading: {top}.")


def _await_due(session, timeout=120):
    from harness import continuation
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        due = continuation.due(session)
        if due:
            return due
        time.sleep(0.1)
    raise AssertionError(f'no continuation: {PyPSAService.get_solver_state().get("status")}')


def _numbers(text):
    return {float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', text)}


def _start(provider, gate, plan=None, cards=None):
    cards = [] if cards is None else cards
    runs_before = chat_tools.list_project_runs(status='completed')['total_count']
    calls, results, frames, session = run_journey(*provider, _plan_steps(plan or _plan()), MESSAGE,
                                                  on_frame=_approve(cards))
    return calls, results, frames, session, cards, runs_before


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_edit_edit_run_then_the_conversation_walks_the_new_run(oj_grid, kind, model, base_url, auth):
    provider = (kind, model, base_url, auth)
    calls, results, frames, session, cards, runs_before = _start(provider, oj_grid)
    assert calls == ['start_task', 'update_component', 'update_component', 'run_simulation', None]

    # One plan shown: edit, edit, run, walk.
    plan = results[0]
    assert [s['tool'] for s in plan['steps']] == ['update_component', 'update_component', 'run_simulation',
                                                 'get_loadflow_summary', 'ui_open_panel']
    # Edit approval, edit approval, then the run's own approval: three cards in order.
    assert [c['tool_name'] for c in cards] == ['update_component', 'update_component', 'run_simulation']
    assert [c['edit_card']['says'] for c in cards[:2]] == ['L1 s_nom 300 → 500 MVA', 'L2 s_nom 300 → 500 MVA']
    assert 'edit_card' not in cards[2]
    # Each edit read back natively before the run was admitted.
    for result in results[1:3]:
        assert result['_edit_receipt']['outcome'] == 'verified'
    lines = PyPSAService.get_network().lines
    assert (float(lines.at['L1', 's_nom']), float(lines.at['L2', 's_nom'])) == (500.0, 500.0)

    # The run released the turn: one job, still running, the plan waiting on it.
    run = results[3]
    job = run['job_id']
    assert run['running'] is True and job.startswith('fg:')
    assert run['_task']['status'] == 'waiting_job'
    assert run['_task']['next_step'] == {'id': 'run', 'tool': 'run_simulation', 'status': 'waiting',
                                         'job_id': job}
    assert frames[-1][0] == 'turn_done'
    assert len(oj_grid.starts) == 1

    # The job ends after the turn; the harness offers exactly one continuation.
    oj_grid.open.set()
    due = _await_due(session)
    assert due == [{'job_id': job, 'status': 'completed', 'task_id': plan['task_id'],
                    'message': f'job {job} finished: completed (plan: {TITLE})'}]

    calls, results, frames, session = run_journey(*provider, _walk_steps(plan['task_id']), '',
                                                  session=session, answer=_walk_answer,
                                                  continuation_job_id=job)
    assert calls == ['resume_task', 'get_loadflow_summary', 'ui_open_panel', None]
    # The continuation is the turn's user message; nobody typed it.
    users = [json.dumps(m['content'], ensure_ascii=False) for m in session.messages if m['role'] == 'user']
    assert sum(f'job {job} finished: completed' in u for u in users) == 1
    task, lf = results[0], results[1]
    assert task['changes'] == ['L1 s_nom 300 → 500 MVA', 'L2 s_nom 300 → 500 MVA']
    # OJ-01 behaviour on the NEW run: one more completed run, and it is current.
    runs = chat_tools.list_project_runs(status='completed')
    assert runs['total_count'] == runs_before + 1
    assert lf['status'] == 'ok' and lf['run']['run_id'] == runs['items'][0]['run_id']
    assert lf['run']['is_current'] is True
    # Hand calculation: the flows do not move, the ratings do (45 MW of 500 MVA = 9 %).
    by_name = {row['name']: row for row in lf['top']}
    assert by_name['L1']['rating']['value'] == 500.0 and by_name['L2']['rating']['value'] == 500.0
    assert by_name['L1']['util_pct'] == pytest.approx(9.0, abs=0.01)
    assert by_name['T1']['util_pct'] == pytest.approx(45.0, abs=0.01)
    assert chat_tools.get_task(plan['task_id'])['status'] == 'completed'
    events = [d for e, d in frames if e == 'ui_event']
    assert len(events) == 1 and events[0]['results_tab'] == 'loadflow', [d for e, d in frames if e == 'tool_error']
    assert not [d for e, d in frames if e in {'error', 'tool_error'}]

    # Every number in the words is a fact of this turn's results.
    text = ''.join(d.get('delta', '') for e, d in frames if e == 'token')
    assert 'L1 s_nom 300 → 500 MVA' in text, text
    facts = _numbers(json.dumps(results, ensure_ascii=False))
    assert _numbers(text) <= facts, _numbers(text) - facts

    # Once per job: a second continuation for the same job is refused, and no
    # further solve ever started.
    from harness import continuation
    assert continuation.due(session) == []
    frames = list(chat_service.run_turn(session, '', continuation_job_id=job))
    assert [e for e, _ in frames] == ['error', 'session_done']
    assert frames[0][1]['error_kind'] == 'continuation_unavailable'
    assert len(oj_grid.starts) == 1


def test_a_solve_longer_than_25_seconds_still_continues(oj_grid):
    oj_grid.hold_seconds = 26.0
    began = time.monotonic()
    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    assert time.monotonic() - began < 20  # the turn did not wait for the job
    job = results[3]['job_id']
    due = _await_due(session, timeout=180)
    assert time.monotonic() - oj_grid.starts[0] > 25
    assert due[0]['message'] == f'job {job} finished: completed (plan: {TITLE})'
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _walk_steps(results[0]['task_id']), '',
                                            session=session, answer=_walk_answer, continuation_job_id=job)
    assert calls == ['resume_task', 'get_loadflow_summary', 'ui_open_panel', None]
    assert results[1]['run']['is_current'] is True
    assert chat_tools.list_project_runs(status='completed')['total_count'] == runs_before + 1


def test_a_restart_during_the_job_gives_one_continuation_and_no_second_run(oj_grid):
    from harness import continuation, session as harness_session

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']
    # The chat process loses the session mid-job (closed tab, evicted, rebuilt).
    harness_session.drop_session(session.session_id)
    reopened = harness_session.get_or_create_session(session.session_id, owner_user_id=session.owner_user_id)
    assert reopened is not session and reopened.task_id is None
    oj_grid.open.set()
    due = _await_due(reopened)
    assert [d['job_id'] for d in due] == [job]
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _walk_steps(task_id), '', session=reopened,
                                            answer=_walk_answer, continuation_job_id=job)
    assert calls == ['resume_task', 'get_loadflow_summary', 'ui_open_panel', None]
    assert continuation.due(reopened) == []
    refused = list(chat_service.run_turn(reopened, '', continuation_job_id=job))
    assert refused[0][1]['error_kind'] == 'continuation_unavailable'
    assert len(oj_grid.starts) == 1
    assert chat_tools.list_project_runs(status='completed')['total_count'] == runs_before + 1


def test_oj08_progress_then_cancel_gives_no_walk(oj_grid):
    from harness import continuation

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']

    # "What's happening?": progress from the native job, the turn is free.
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('wait_for_job', {'job_id': job, 'timeout_seconds': 0}), lambda r: (None, {})],
        "What's happening?", session=session)
    assert calls == ['wait_for_job', None]
    assert results[0]['job']['id'] == job and results[0]['job']['status'] == 'running'

    # "Cancel it.": a separate approved action with its own receipt.
    cancel_cards = []
    calls, results, frames, _ = run_journey(*PROVIDERS[1], [lambda r: ('abort_simulation', {}), lambda r: (None, {})],
                                            'Cancel it.', session=session, on_frame=_approve(cancel_cards))
    assert [c['tool_name'] for c in cancel_cards] == ['abort_simulation']
    assert results[0]['status'] == 'abort_requested'
    _settle_solver()
    assert PyPSAService.get_solver_state().get('status') == 'aborted'

    # The walk does not start: no continuation, the run step failed, no new run.
    time.sleep(0.5)
    assert continuation.due(session) == []
    refused = list(chat_service.run_turn(session, '', continuation_job_id=job))
    assert refused[0][1]['error_kind'] == 'continuation_unavailable'
    task = chat_tools.get_task(task_id)
    assert task['next_step']['id'] == 'run' and task['next_step']['status'] == 'failed'
    assert chat_tools.list_project_runs(status='completed')['total_count'] == runs_before
    assert len(oj_grid.starts) == 1


def test_oj10_a_refused_edit_stops_the_run(oj_grid):
    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid, plan=_plan(l2=-500))
    assert calls == ['start_task', 'update_component', 'update_component', 'run_simulation', None]
    # L1 was carded and verified; L2 was refused before any card.
    assert [c['tool_name'] for c in cards] == ['update_component']
    errors = [d for e, d in frames if e == 'tool_error']
    assert errors[0]['tool_name'] == 'update_component'
    assert 'L2' in errors[0]['message'] and 's_nom' in errors[0]['message'], errors[0]
    assert float(PyPSAService.get_network().lines.at['L2', 's_nom']) == 300.0
    # The run never starts: refused as out of order, no card, no job.
    assert errors[1]['tool_name'] == 'run_simulation'
    assert errors[1]['error_kind'] == 'task_step_out_of_order'
    assert oj_grid.starts == []
    task = chat_tools.get_task(results[0]['task_id'])
    assert task['next_step']['id'] == 'l2' and task['completed_steps'] == 1


def test_no_continuation_after_the_plan_is_cancelled(oj_grid):
    from harness import continuation

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']
    chat_tools.set_chat_session(session)
    chat_tools.cancel_task(task_id)
    oj_grid.open.set()
    _settle_solver()
    time.sleep(0.5)
    assert continuation.due(session) == []
    refused = list(chat_service.run_turn(session, '', continuation_job_id=job))
    assert refused[0][1]['error_kind'] == 'continuation_unavailable'


def test_no_continuation_for_another_session(oj_grid):
    from harness import continuation

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job = results[3]['job_id']
    oj_grid.open.set()
    _await_due(session)
    other = chat_service.ChatSession()
    assert continuation.due(other) == []
    refused = list(chat_service.run_turn(other, '', continuation_job_id=job))
    assert refused[0][1]['error_kind'] == 'continuation_unavailable'
    assert [d['job_id'] for d in continuation.due(session)] == [job]


def test_the_panel_lists_the_continuation_over_http(oj_grid, client):
    from harness import session as harness_session

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job = results[3]['job_id']
    session.owner_user_id = chat_tools.acting_user_id()
    with harness_session._SESSIONS_LOCK:
        harness_session._SESSIONS[session.session_id] = session
    assert client.get(f'/api/chat/{session.session_id}/continuations').json() == {'items': []}
    oj_grid.open.set()
    _await_due(session)
    listed = client.get(f'/api/chat/{session.session_id}/continuations').json()['items']
    assert [(i['job_id'], i['status']) for i in listed] == [(job, 'completed')]
    # A continuation is not a user message: typed words with it are refused.
    response = client.post('/api/chat/stream', json={'session_id': session.session_id, 'message': 'go on',
                                                     'continuation_job_id': job})
    assert response.status_code == 422 and response.json()['detail']['error_kind'] == 'invalid_continuation'
    # Listing claimed nothing.
    assert [i['job_id'] for i in client.get(f'/api/chat/{session.session_id}/continuations').json()['items']] == [job]
    assert client.get('/api/chat/not-a-session/continuations').json() == {'items': []}


def _run_outside_the_plan(monkeypatch):
    """Job B: the user's own solve from another chat, not part of the plan."""
    import harness.confirm as harness_confirm
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        other = chat_service.ChatSession()
        chat_tools.set_chat_session(other)
        return _solve_once(other)


def _manifest(run_id):
    from services import run_evidence
    root, project_id, _ = run_evidence._authority()
    return run_evidence._load_run(root, project_id, run_id)


def test_a_settled_step_keeps_its_own_jobs_record(oj_grid, monkeypatch):
    """Settled from job A's own manifest; a later job B changes nothing in it."""
    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']
    oj_grid.open.set()
    _await_due(session)
    chat_tools.set_chat_session(session)
    step = chat_tools.get_task(task_id)
    # The step's result is A's manifest: objective and input fingerprint.
    from services import assistant_tasks
    _, raw = assistant_tasks._read(task_id)
    settled = next(s for s in raw['steps'] if s['id'] == 'run')
    record = _manifest(settled['result']['run_id'])
    assert record['job_id'] == job and settled['result']['fingerprint'] == record['fingerprint']
    assert settled['result']['objective'] == record['metrics']['objective']['value']
    assert step['next_step']['id'] == 'walk'
    # Job B on changed inputs: A's step still names A's record.
    PyPSAService.get_network().lines.at['L1', 's_nom'] = 600.0
    newest = _run_outside_the_plan(monkeypatch)
    assert newest['run_id'] != settled['result']['run_id']
    assert _manifest(newest['run_id'])['fingerprint'] != record['fingerprint']
    _, again = assistant_tasks._read(task_id)
    assert next(s for s in again['steps'] if s['id'] == 'run')['result'] == settled['result']
    assert len(oj_grid.starts) == 2  # A, then the user's own B; the plan ran once


def test_a_job_replaced_before_settlement_never_completes(oj_grid, monkeypatch):
    """Job A ends, job B takes the slot before A's follower looks again: A's
    record is gone, so the step settles failed, never with B's numbers."""
    from harness import continuation
    from services import assistant_tasks

    monkeypatch.setattr(continuation, 'POLL_SECONDS', 3600.0)
    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']
    oj_grid.open.set()
    _settle_solver()
    PyPSAService.get_network().lines.at['L1', 's_nom'] = 600.0
    _run_outside_the_plan(monkeypatch)
    continuation._WAKE.set()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, raw = assistant_tasks._read(task_id)
            run = next(s for s in raw['steps'] if s['id'] == 'run')
            if run['status'] != 'waiting':
                break
            time.sleep(0.05)
    finally:
        continuation._WAKE.clear()
    assert run['status'] == 'failed' and run['job']['outcome'] == 'unknown'
    assert 'result' not in run
    chat_tools.set_chat_session(session)
    assert chat_tools.get_task(task_id)['completed_steps'] == 2
    assert [(d['job_id'], d['status']) for d in continuation.due(session)] == [(job, 'unknown')]
    assert len(oj_grid.starts) == 2


def test_a_quick_solve_goes_on_in_the_same_turn_with_no_continuation(oj_grid):
    from harness import continuation

    oj_grid.open.set()  # nothing holds the solve: it settles within the grace
    steps = _plan_steps(_plan())[:4] + [lambda r: ('get_loadflow_summary', r[3]['_task']['next_step']['args']),
                                        lambda r: (None, {})]
    runs_before = chat_tools.list_project_runs(status='completed')['total_count']
    calls, results, frames, session = run_journey(*PROVIDERS[1], steps, MESSAGE, on_frame=_approve([]))
    assert calls == ['start_task', 'update_component', 'update_component', 'run_simulation',
                     'get_loadflow_summary', None]
    assert results[3]['running'] is False and results[3]['_task']['next_step']['id'] == 'walk'
    assert results[4]['run']['is_current'] is True
    # The turn that held the job went on with the plan: no second walk follows.
    assert continuation.due(session) == []
    refused = list(chat_service.run_turn(session, '', continuation_job_id=results[3]['job_id']))
    assert refused[0][1]['error_kind'] == 'continuation_unavailable'
    assert chat_tools.list_project_runs(status='completed')['total_count'] == runs_before + 1
    assert len(oj_grid.starts) == 1


def test_a_process_restart_loses_the_job_and_continues_once_uncertain(oj_grid):
    """The job registry dies with the process: the step settles uncertain, the
    conversation is told once, and nothing runs again."""
    from harness import continuation, job_adapters

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']
    with job_adapters._LOCK:
        job_adapters._FOREGROUND.pop(job)  # what a restart leaves: the task file, no handle
    due = continuation.due(session)
    assert [(d['job_id'], d['status']) for d in due] == [(job, 'unknown')]
    chat_tools.set_chat_session(session)
    task = chat_tools.get_task(task_id)
    assert task['status'] == 'needs_review' and task['next_step']['status'] == 'uncertain'
    calls, results, frames, _ = run_journey(*PROVIDERS[1], [lambda r: ('get_task', {'task_id': task_id}),
                                                            lambda r: (None, {})],
                                            '', session=session, continuation_job_id=job)
    assert calls == ['get_task', None]
    assert continuation.due(session) == []
    # Still waiting would be a second run; the run step needs explicit reconciliation.
    calls, results, frames, _ = run_journey(*PROVIDERS[1], [lambda r: ('run_simulation', {}), lambda r: (None, {})],
                                            'Run it again.', session=session, on_frame=_approve([]))
    errors = [d for e, d in frames if e == 'tool_error']
    assert errors and errors[0]['error_kind'] == 'task_step_uncertain'
    assert len(oj_grid.starts) == 1


def test_a_failed_solve_continues_with_its_failure(oj_grid, monkeypatch):
    import routers.simulation as simulation

    from harness import continuation

    monkeypatch.setattr(continuation, 'GRACE_SECONDS', 0.2)
    oj_grid.hold_seconds = 1.0  # past the grace, then HiGHS reports infeasible
    monkeypatch.setattr(simulation, 'run_simulation', oj_grid.wrap(lambda *a, **k: ('failed', 'infeasible')))
    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    due = _await_due(session)
    assert [(d['status'], d['message'].split(' (')[0]) for d in due] == [
        ('failed', f"job {results[3]['job_id']} finished: failed")]
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('get_simulation_log_history', {}), lambda r: (None, {})], '',
        session=session, continuation_job_id=due[0]['job_id'])
    assert calls == ['get_simulation_log_history', None]
    chat_tools.set_chat_session(session)
    task = chat_tools.get_task(due[0]['task_id'])
    assert task['next_step']['id'] == 'run' and task['next_step']['status'] == 'failed'
    assert chat_tools.list_project_runs(status='completed')['total_count'] == runs_before


def test_stop_waiting_withdraws_the_continuation_and_the_job_runs_on(oj_grid, client):
    from harness import continuation, session as harness_session

    calls, results, frames, session, cards, runs_before = _start(PROVIDERS[1], oj_grid)
    job, task_id = results[3]['job_id'], results[0]['task_id']
    session.owner_user_id = chat_tools.acting_user_id()
    with harness_session._SESSIONS_LOCK:
        harness_session._SESSIONS[session.session_id] = session
    response = client.post(f'/api/chat/{session.session_id}/continuations/{job}/dismiss')
    assert response.json() == {'ok': True}
    assert PyPSAService.get_solver_state().get('status') == 'running'  # the solve is not cancelled
    oj_grid.open.set()
    _settle_solver()
    time.sleep(0.5)
    assert PyPSAService.get_solver_state().get('status') == 'completed'
    assert continuation.due(session) == []
    chat_tools.set_chat_session(session)
    assert chat_tools.get_task(task_id)['next_step']['id'] == 'walk'  # the plan still settled
    assert client.post(f'/api/chat/{session.session_id}/continuations/{job}/dismiss').json() == {'ok': False}
