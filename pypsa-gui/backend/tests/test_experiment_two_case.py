"""Shared ordinary two-case admission; native solver and immutable evidence."""
# ruff: noqa: F811 -- shared public harness fixtures
import pytest
from fastapi import HTTPException

from services import experiments as ex, chat_tools
from services.solve_queue import solve_queue
from tests.test_experiment_case_advancement import arm, invoke, tool
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401
from tests.test_chat_tool_executor_starvation import saturated_pool  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401


def prepared_pair(limits=None, *, first_path='Generator.G1.marginal_cost', first_value=20):
    plan = ex.start_experiment(title='Shared two-case plan', request_id='pair', limits=limits,
        cases=[{'id': 'low', 'changes': [{'path': first_path, 'value': first_value, 'value_type': 'number'}]},
               {'id': 'high', 'changes': [{'path': 'Generator.G1.marginal_cost', 'value': 30, 'value_type': 'number'}]}])
    return ex.prepare_experiment(plan['experiment_id'])['experiment_id']


def finish(session, state, eid, *, succeeds=True):
    result = invoke(session, state['next_call'])
    job = result['cases'][0]['job']['id']
    terminal = chat_tools.wait_for_job(job, 25)
    assert terminal['terminal'] and terminal['completed'] is succeeds, terminal
    return tool('refresh_experiment', experiment_id=eid)


def test_two_cases_advance_in_order_with_independent_native_results_and_one_baseline(baseline):
    ctx, project, root = baseline
    before = (root / 'network.nc').read_bytes()
    eid = prepared_pair()
    session, state = arm(eid)
    assert state['status'] == 'ready' and state['attempts_used'] == 0
    assert len(state['next_call']['args']['cases']) == 1
    assert not solve_queue.list_jobs()
    first = finish(session, state, eid)
    assert first['cases'][0]['objective'] == 200
    assert first['cases'][1]['status'] == 'ready'
    assert first['status'] == 'ready' and first['attempts_used'] == 1
    assert first['next_call']['args'] != state['next_call']['args']
    session, resumed = arm(eid)
    final = finish(session, resumed, eid)
    assert final['status'] == 'completed' and final['result_available']
    assert [c['objective'] for c in final['cases']] == [200, 300]
    assert len({c['job_id'] for c in final['cases']}) == 2
    assert len({c['case_project_id'] for c in final['cases']}) == 2
    assert all(c['evidence_sha256'] for c in final['cases'])
    assert final['attempts_used'] == 2 and 'next_call' not in final
    detail = tool('get_experiment', experiment_id=eid)
    assert len(detail['job_ids']) == 2
    assert [c['status'] for c in detail['cases']] == ['completed', 'completed']
    assert (root / 'network.nc').read_bytes() == before
    assert ctx.project_uuid == project and not ctx.results_unsaved


def test_same_plan_comparison_and_digest_bound_download_use_explicit_cases(baseline, client):
    eid = prepared_pair()
    session, state = arm(eid)
    second = finish(session, state, eid)
    finish(session, second, eid)
    args = dict(experiment_id=eid, baseline_experiment_id=eid, case_id='high', baseline_case_id='low')
    report = tool('compare_experiments', **args)
    assert report['comparable'] and report['inputs_differ']
    assert report['case']['case_id'] == 'high' and report['baseline_case']['case_id'] == 'low'
    assert report['deltas']['objective']['delta'] == dict(value=100, unit='EUR')
    assert report['deltas']['objective']['delta_pct'] == 50
    exported = tool('export_experiment_comparison', **args, comparison_sha256=report['comparison_sha256'])
    response = client.get(chat_tools.get_file_delivery(exported['file_id'])['download_url'])
    assert response.status_code == 200 and response.json() == report
    assert len(solve_queue.list_jobs()) == 2


def test_distinct_long_case_ids_keep_distinct_reserved_scenario_names(baseline):
    cases = [{'id': 'a' * 24 + suffix, 'changes': [{'path': 'Generator.G1.marginal_cost', 'value': value, 'value_type': 'number'}]}
             for suffix, value in [('one', 20), ('two', 30)]]
    eid = ex.start_experiment(title='Long identifiers', request_id='long', cases=cases)['experiment_id']
    ex.prepare_experiment(eid)
    session, state = arm(eid)
    first = state['next_call']['args']['cases'][0]['new_name']
    state = finish(session, state, eid)
    second = state['next_call']['args']['cases'][0]['new_name']
    assert first != second and len(first) <= 64 and len(second) <= 64
    final = finish(session, state, eid)
    assert final['status'] == 'completed'



@pytest.mark.parametrize('policy', ['stop', 'continue'])
def test_native_failure_obeys_policy_without_hiding_failed_case(baseline, policy):
    eid = prepared_pair({'failure_policy': policy}, first_path='Generator.G1.p_nom', first_value=1)
    session, state = arm(eid)
    current = finish(session, state, eid, succeeds=False)
    assert current['cases'][0]['status'] == 'failed'
    assert 'objective' not in current['cases'][0] and current['cases'][1]['status'] == 'ready'
    if policy == 'stop':
        assert current['status'] == 'stopped' and 'next_call' not in current
        assert len(solve_queue.list_jobs()) == 1
    else:
        final = finish(session, current, eid)
        assert final['status'] == 'partial' and not final['result_available']
        assert final['cases'][1]['objective'] == 300 and 'next_call' not in final
        report = tool('compare_experiments', experiment_id=eid, baseline_experiment_id=eid,
                      case_id='high', baseline_case_id='low')
        assert not report['comparable'] and not report['deltas']
        assert report['case']['objective'] == 300 and report['baseline_case']['status'] == 'failed'


def test_shared_attempt_cap_counts_waits_and_survives_new_chat(baseline):
    eid = prepared_pair({'max_tool_attempts': 2})
    session, armed = arm(eid)
    result = invoke(session, armed['next_call'])
    job = result['cases'][0]['job']['id']
    invoke(session, dict(tool='wait_for_job', args=dict(job_id=job, timeout_seconds=20)))
    session, current = arm(eid)
    assert current['attempts_used'] == 2 and current['status'] == 'budget_stopped'
    assert current['cases'][0]['objective'] == 200
    assert current['cases'][1]['status'] == 'ready' and 'next_call' not in current
    with pytest.raises(HTTPException) as error:
        invoke(session, armed['next_call'])
    assert error.value.detail['error_kind'] == 'experiment_budget_exhausted'
    assert len(solve_queue.list_jobs()) == 1


def test_shared_time_cap_is_not_renewed_at_transition(baseline, monkeypatch):
    from harness import experiment_execution as engine
    eid = prepared_pair({'max_elapsed_seconds': 60})
    session, armed = arm(eid)
    current = finish(session, armed, eid)
    monkeypatch.setattr(engine, '_clock', lambda: current['started_at'] + 61)
    session, resumed = arm(eid)
    assert resumed['status'] == 'budget_stopped' and resumed['started_at'] == current['started_at']
    assert 'next_call' not in resumed and resumed['cases'][0]['objective'] == 200
    with pytest.raises(HTTPException) as error:
        invoke(session, current['next_call'])
    assert error.value.detail['error_kind'] == 'experiment_budget_exhausted'
    assert len(solve_queue.list_jobs()) == 1


def test_old_case_call_cannot_replay_after_next_case_is_offered(baseline):
    eid = prepared_pair()
    session, armed = arm(eid)
    current = finish(session, armed, eid)
    with pytest.raises(HTTPException):
        invoke(session, armed['next_call'])
    final = finish(session, current, eid)
    session, resumed = arm(eid)
    for call in (armed['next_call'], current['next_call']):
        with pytest.raises(HTTPException):
            invoke(session, call)
    assert final['status'] == resumed['status'] == 'completed'
    assert len(solve_queue.list_jobs()) == 2


@pytest.mark.parametrize('policy', ['stop', 'continue'])
def test_uncertain_no_effects_require_explicit_case_settlement_before_progression(baseline, policy):
    eid = prepared_pair({'failure_policy': policy})
    session, armed = arm(eid)
    def interrupted(**_):
        raise RuntimeError('stopped before native effects')
    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], interrupted)
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['status'] == 'needs_review' and 'next_call' not in current
    call = current['cases'][0]['reconciliation']['next_call']
    assert call['args']['case_id'] == 'low'
    settled = tool(call['tool'], **call['args'])
    assert settled['status'] == 'not_started' and settled['case_effects_reserved']
    session, current = arm(eid)
    assert current['attempts_used'] == 1 and current['cases'][0]['status'] == 'not_started'
    with pytest.raises(HTTPException):
        invoke(session, armed['next_call'])
    if policy == 'stop':
        assert current['status'] == 'stopped' and 'next_call' not in current
    else:
        final = finish(session, current, eid)
        assert final['status'] == 'partial' and final['cases'][1]['objective'] == 300
    assert len(solve_queue.list_jobs()) == (0 if policy == 'stop' else 1)


@pytest.mark.parametrize('phase,outcome,jobs', [('queue_started', 'partial_effects', 0), ('job_recorded', 'unresolved', 1)])
def test_partial_or_unknown_native_effects_block_continue_even_after_reconciliation(baseline, monkeypatch, phase, outcome, jobs):
    from tests.test_experiment_reconciliation import fail_checkpoint
    eid = prepared_pair({'failure_policy': 'continue'})
    session, armed = arm(eid)
    with monkeypatch.context() as fault:
        fail_checkpoint(fault, phase)
        result = invoke(session, armed['next_call'])
    assert result['status'] == 'partial'
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['status'] == 'needs_review' and 'next_call' not in current
    call = current['cases'][0]['reconciliation']['next_call']
    settled = tool(call['tool'], **call['args'])
    assert settled['reconciliation']['outcome'] == outcome
    session, current = arm(eid)
    assert current['status'] == 'needs_review' and current['cases'][0]['case_effects_reserved']
    assert 'next_call' not in current and current['attempts_used'] == 1
    assert len(solve_queue.list_jobs()) == jobs
    if jobs:
        assert chat_tools.wait_for_job(solve_queue.list_jobs()[0]['id'], 25)['terminal']


def test_lost_native_return_requires_explicit_matching_case_recovery(baseline):
    eid = prepared_pair({'failure_policy': 'continue'})
    session, armed = arm(eid)
    actual = {}
    def lost(**args):
        actual.update(chat_tools.run_sensitivity_sweep(**args)['cases'][0])
        raise RuntimeError('lost native return')
    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], lost)
    assert chat_tools.wait_for_job(actual['job']['id'], 25)['completed']
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['status'] == 'needs_review' and 'next_call' not in current
    call = current['cases'][0]['reconciliation']['next_call']
    recovered = tool(call['tool'], **call['args'])
    assert recovered['reconciliation']['outcome'] == 'receipt_recovered'
    assert recovered['objective'] == 200 and recovered['job_id'] == actual['job']['id']
    session, current = arm(eid)
    final = finish(session, current, eid)
    assert final['status'] == 'completed' and final['attempts_used'] == 2
    assert len(solve_queue.list_jobs()) == 2


def test_cancel_retains_first_result_and_blocks_stale_second_call(baseline):
    eid = prepared_pair()
    session, armed = arm(eid)
    second = finish(session, armed, eid)
    cancelled = tool('cancel_experiment', experiment_id=eid)
    assert cancelled['cancelled'] and cancelled['status'] == 'cancelled'
    assert cancelled['cases'][0]['objective'] == 200 and 'next_call' not in cancelled
    session, current = arm(eid)
    assert current['cancelled'] and current['attempts_used'] == 1
    with pytest.raises(HTTPException):
        invoke(session, second['next_call'])
    assert len(solve_queue.list_jobs()) == 1


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'missing_case', 'changed_owner'])
def test_shared_checkpoint_loss_never_replaces_a_reservation(baseline, damage):
    import json
    eid = prepared_pair()
    arm(eid)
    path = baseline[2] / 'experiment_execution' / (eid + '.json')
    data = json.loads(path.read_text())
    if damage == 'missing':
        path.unlink()
    elif damage == 'corrupt':
        path.write_text('{broken')
    else:
        if damage == 'missing_case':
            del data['cases']['high']
        else:
            data['cases']['high']['owner_id'] = 'someone-else'
        path.write_text(json.dumps(data))
    for name in ['advance_experiment', 'refresh_experiment', 'cancel_experiment', 'get_experiment']:
        with pytest.raises(HTTPException) as error:
            tool(name, experiment_id=eid)
        assert error.value.detail['error_kind'] == 'experiment_checkpoint_unreadable'
    assert not solve_queue.list_jobs()
    if damage == 'missing':
        assert not path.exists()


def test_baseline_drift_blocks_second_case_before_reserving(baseline):
    ctx, _, _ = baseline
    eid = prepared_pair()
    session, armed = arm(eid)
    second = finish(session, armed, eid)
    ctx.network.generators.at['G1', 'marginal_cost'] += 1e-11
    with pytest.raises(HTTPException):
        invoke(session, second['next_call'])
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['attempts_used'] == 1 and current['cases'][0]['objective'] == 200
    assert not current['cases'][1]['case_effects_reserved']
    assert len(solve_queue.list_jobs()) == 1


def test_foreign_owner_cannot_select_shared_cases_or_reconcile(baseline, second_identity):
    eid = prepared_pair()
    arm(eid)
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(second_identity['user_id'])
    try:
        calls = [('advance_experiment', {}), ('refresh_experiment', {}), ('cancel_experiment', {}),
                 ('reconcile_experiment', dict(case_id='low', receipt_sha256='0' * 64)),
                 ('compare_experiments', dict(baseline_experiment_id=eid, case_id='low', baseline_case_id='high'))]
        for name, args in calls:
            with pytest.raises(HTTPException) as error:
                tool(name, experiment_id=eid, **args)
            assert error.value.status_code == 404
    finally:
        chat_tools.set_acting_user(previous)
    assert not solve_queue.list_jobs()


@pytest.mark.parametrize('case_id,baseline_case_id', [(None, None), ('low', 'low'), ('unknown', 'high'), ('low', None)])
def test_shared_comparison_requires_two_distinct_explicit_cases(baseline, case_id, baseline_case_id):
    eid = prepared_pair()
    with pytest.raises(HTTPException) as error:
        tool('compare_experiments', experiment_id=eid, baseline_experiment_id=eid,
             case_id=case_id, baseline_case_id=baseline_case_id)
    assert error.value.detail['error_kind'] == 'invalid_experiment_comparison'
    assert not solve_queue.list_jobs()


def test_checked_chat_does_not_acquire_shared_case_execution_authority(baseline, checked):
    from tests.assurance_acceptance.support import UNSAFE, public_text, text_turn, tool_turn
    eid = prepared_pair()
    frames = checked.run([tool_turn('advance_experiment', {'experiment_id': eid}), text_turn(UNSAFE), text_turn(UNSAFE)])
    assert any(event == 'tool_error' and p.get('tool_name') == 'advance_experiment' for event, p in frames)
    assert UNSAFE not in public_text(frames) and not solve_queue.list_jobs()


@pytest.mark.parametrize('second_decision', ['approve', 'deny'])
def test_each_case_needs_its_own_authenticated_http_confirmation(baseline, client, monkeypatch, second_decision):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from services import chat_service
    from tests.assurance_acceptance.support import Harness, parse_sse, text_turn, tool_turn
    eid = prepared_pair()
    _, current = arm(eid)
    before = (baseline[2] / 'network.nc').read_bytes()
    sid = 'two-case-http-' + second_decision
    harness = Harness(client, monkeypatch)
    try:
        for index, decision in enumerate(['approve', second_decision]):
            call = current['next_call']
            harness.install([tool_turn('advance_experiment', {'experiment_id': eid}),
                             tool_turn(call['tool'], call['args']), text_turn('Inspect the case receipt.')])
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(client.post, '/api/chat/stream', json={'session_id': sid,
                    'message': 'advance_experiment and run_sensitivity_sweep for this exact next case'})
                deadline, token = time.monotonic() + 10, None
                while time.monotonic() < deadline and not pending.done():
                    session = chat_service.get_session(sid)
                    if session:
                        with session._lock:
                            token = next((k for k, card in session.pending_confirmations.items()
                                          if card.tool_name == 'run_sensitivity_sweep'), None)
                    if token:
                        break
                    time.sleep(0.02)
                if token is None:
                    client.post(f'/api/chat/{sid}/abort')
                assert token, 'Each case must issue a fresh native execution confirmation'
                assert client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': decision}).status_code == 200
                response = pending.result(timeout=15)
            assert response.status_code == 200
            frames = parse_sse(response.text)
            errors = [p for event, p in frames if event in {'error', 'tool_error'}]
            if decision == 'deny':
                assert len(errors) == 1 and errors[0]['error_kind'] == 'confirmation_denied'
            else:
                assert not errors, errors
                outputs = [p['result'] for event, p in frames if event == 'tool_result' and p.get('tool_name') == call['tool']]
                assert len(outputs) == 1
                assert chat_tools.wait_for_job(outputs[0]['cases'][0]['job']['id'], 25)['completed']
            current = tool('refresh_experiment', experiment_id=eid)
            assert current['attempts_used'] == index + (decision == 'approve')
        assert current['cases'][0]['objective'] == 200
        if second_decision == 'approve':
            assert current['status'] == 'completed' and current['cases'][1]['objective'] == 300
        else:
            assert current['cases'][1]['status'] == 'ready' and not current['cases'][1]['case_effects_reserved']
        assert len(solve_queue.list_jobs()) == (2 if second_decision == 'approve' else 1)
        assert (baseline[2] / 'network.nc').read_bytes() == before
    finally:
        chat_service.drop_session(sid)


def test_live_case_fence_blocks_other_case_submission_and_reconciliation(baseline):
    import contextvars
    import copy
    import threading
    from concurrent.futures import ThreadPoolExecutor
    eid = prepared_pair({'failure_policy': 'continue'})
    session, armed = arm(eid)
    second = copy.deepcopy(armed['next_call'])
    second['args']['cases'][0]['new_name'] = armed['cases'][1]['case_name']
    second['args']['cases'][0]['changes'][0]['value'] = 30
    entered, release = threading.Event(), threading.Event()
    def delayed(**args):
        entered.set()
        assert release.wait(10)
        return chat_tools.run_sensitivity_sweep(**args)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, invoke, session, armed['next_call'], delayed)
        try:
            assert entered.wait(5)
            for call in (armed['next_call'], second):
                with pytest.raises(HTTPException) as error:
                    invoke(session, call)
                assert error.value.detail['error_kind'] == 'experiment_worker_running'
            with pytest.raises(HTTPException) as error:
                tool('reconcile_experiment', experiment_id=eid, case_id='high', receipt_sha256='0' * 64)
            assert error.value.detail['error_kind'] == 'experiment_worker_running'
            assert not future.done() and not solve_queue.list_jobs()
        finally:
            release.set()
        job = future.result(timeout=15)['cases'][0]['job']['id']
    assert chat_tools.wait_for_job(job, 25)['completed']
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['attempts_used'] == 1
    assert finish(session, current, eid)['status'] == 'completed'


def test_second_case_worker_starvation_reserves_nothing(baseline, saturated_pool, monkeypatch):
    # The worker barrier is the same agreed native boundary as single-case tests.
    # The first case uses native fixtures; the second traverses the executor.
    from harness import budget
    from services import chat_service
    release, _ = saturated_pool
    eid = prepared_pair()
    session, armed = arm(eid)
    current = finish(session, armed, eid)
    monkeypatch.setattr(session, 'wait_for_decision', lambda _: 'approve')
    monkeypatch.setattr(budget, 'PER_TOOL_TIMEOUT_SECONDS', 0.05)
    call = current['next_call']
    frames = list(chat_service._dispatch_real_tool_call(session,
        {'id': 'starved-second', 'name': call['tool'], 'input': call['args']}, []))
    assert any(event == 'tool_error' and p['error_kind'] == 'tool_not_started' for event, p in frames)
    release.set()
    chat_service._TOOL_EXECUTOR.submit(lambda: None).result(timeout=5)
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['attempts_used'] == 1 and not current['cases'][1]['case_effects_reserved']
    assert len(solve_queue.list_jobs()) == 1


def test_lost_first_case_source_blocks_stale_second_admission(baseline):
    from services import project_registry, workflow_tools as wt
    eid = prepared_pair({'failure_policy': 'continue'})
    session, armed = arm(eid)
    second = finish(session, armed, eid)
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, second['cases'][0]['case_project_id'])
        saved = wt._load_context(child)
        saved.network.meta.pop('assistant_solve_exact_input_fingerprint')
        saved.network.export_to_netcdf(project_registry.project_dir(child) / 'network.nc')
    with pytest.raises(HTTPException):
        invoke(session, second['next_call'])
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['status'] == 'needs_review' and current['cases'][0]['status'] == 'unavailable'
    assert not current['cases'][0]['result_available'] and 'objective' not in current['cases'][0]
    assert not current['cases'][1]['case_effects_reserved'] and current['attempts_used'] == 1
    assert len(solve_queue.list_jobs()) == 1


def test_shared_export_rechecks_parent_cancellation_and_selection(baseline):
    eid = prepared_pair()
    session, state = arm(eid)
    finish(session, finish(session, state, eid), eid)
    args = dict(experiment_id=eid, baseline_experiment_id=eid, case_id='high', baseline_case_id='low')
    before = tool('compare_experiments', **args)
    tool('cancel_experiment', experiment_id=eid)
    current = tool('compare_experiments', **args)
    assert current['comparable'] and current['case']['cancelled'] and current['baseline_case']['cancelled']
    assert current['deltas']['objective']['delta']['value'] == 100
    with pytest.raises(HTTPException) as error:
        tool('export_experiment_comparison', **args, comparison_sha256=before['comparison_sha256'])
    assert error.value.detail['error_kind'] == 'experiment_comparison_changed'
    assert len(solve_queue.list_jobs()) == 2


def test_future_case_cannot_be_admitted_before_first_case(baseline):
    import copy
    eid = prepared_pair({'failure_policy': 'continue'})
    session, armed = arm(eid)
    future = copy.deepcopy(armed['next_call'])
    future['args']['cases'][0]['new_name'] = armed['cases'][1]['case_name']
    future['args']['cases'][0]['changes'][0]['value'] = 30
    with pytest.raises(HTTPException):
        invoke(session, future)
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['attempts_used'] == 0 and all(not c['case_effects_reserved'] for c in current['cases'])
    chat_tools.set_chat_session(None)
    with pytest.raises(HTTPException):
        chat_tools.run_sensitivity_sweep(**future['args'])
    assert not solve_queue.list_jobs()


def test_tracked_wait_can_settle_after_native_job_finishes_without_refresh(baseline):
    eid = prepared_pair()
    session, armed = arm(eid)
    result = invoke(session, armed['next_call'])
    job = result['cases'][0]['job']['id']
    # External native completion races the caller's already selected bounded wait.
    assert chat_tools.wait_for_job(job, 25)['completed']
    settled = invoke(session, dict(tool='wait_for_job', args=dict(job_id=job, timeout_seconds=20)))
    assert settled['completed']
    current = tool('refresh_experiment', experiment_id=eid)
    assert current['cases'][0]['objective'] == 200 and current['attempts_used'] == 2
    assert not current['cases'][1]['case_effects_reserved']
    assert current['next_call']['tool'] == 'run_sensitivity_sweep'
    assert len(solve_queue.list_jobs()) == 1
