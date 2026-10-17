"""Two independently approved native cases; comparison never executes work."""
# ruff: noqa: F811 -- shared public harness fixtures
import pytest
import json

from services import chat_tools, experiments as ex
from services.solve_queue import solve_queue
from fastapi import HTTPException
from tests.test_experiment_case_advancement import arm, invoke, tool
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401


def completed_case(value, request_id, *, path='Generator.G1.marginal_cost', succeeds=True, baseline_run_id=None):
    plan = ex.start_experiment(title=request_id, request_id=request_id,
        baseline_run_id=baseline_run_id,
        cases=[{'id': 'case', 'changes': [{'path': path, 'value_type': 'number', 'value': value}]}])
    eid = ex.prepare_experiment(plan['experiment_id'])['experiment_id']
    session, state = arm(eid)
    result = invoke(session, state['next_call'])
    terminal = chat_tools.wait_for_job(result['cases'][0]['job']['id'], 25)
    assert terminal['terminal'] and terminal['completed'] is succeeds, terminal
    return eid, tool('refresh_experiment', experiment_id=eid)


def test_two_native_cases_compare_recorded_objectives_without_mutating_baseline(baseline):
    _ctx, project, root = baseline
    before = (root / 'network.nc').read_bytes()
    low, low_result = completed_case(20, 'low')
    high, high_result = completed_case(30, 'high')
    report = tool('compare_experiments', experiment_id=high, baseline_experiment_id=low)
    assert report['comparable'] and report['status'] == 'completed'
    assert report['deltas']['objective'] == {
        'case': {'value': 300, 'unit': 'EUR'}, 'baseline_case': {'value': 200, 'unit': 'EUR'},
        'delta': {'value': 100, 'unit': 'EUR'}, 'delta_pct': 50}
    assert report['case']['job_id'] == high_result['job_id']
    assert report['baseline_case']['job_id'] == low_result['job_id']
    assert report['project_id'] == project and report['inputs_differ']
    assert report['basis']['objective_basis'] == 'recorded_solver_objective'
    assert len(report['comparison_sha256']) == 64
    assert len(solve_queue.list_jobs()) == 2
    assert (root / 'network.nc').read_bytes() == before
    again = tool('compare_experiments', experiment_id=high, baseline_experiment_id=low)
    assert again['comparison_sha256'] == report['comparison_sha256']


def test_comparison_export_downloads_the_exact_inspected_report(baseline, client):
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    report = tool('compare_experiments', **args)
    exported = tool('export_experiment_comparison', **args, comparison_sha256=report['comparison_sha256'])
    delivery = chat_tools.get_file_delivery(exported['file_id'])
    response = client.get(delivery['download_url'])
    assert response.status_code == 200
    assert response.json() == report
    assert exported['comparison_sha256'] == report['comparison_sha256']
    assert len(solve_queue.list_jobs()) == 2


def test_reconciled_no_effect_case_remains_not_started_without_retry(baseline):
    from tests.test_experiment_reconciliation import stopped_before_effects
    good, _ = completed_case(20, 'good')
    stopped, _session, _armed, call = stopped_before_effects()
    assert tool(call['tool'], **call['args'])['status'] == 'not_started'
    report = tool('compare_experiments', experiment_id=good, baseline_experiment_id=stopped)
    assert not report['comparable'] and not report['deltas'] and report['status'] == 'partial'
    assert report['baseline_case']['status'] == 'not_started'
    assert report['baseline_case']['case_effects_reserved']
    assert report['baseline_case']['retry_available'] is False
    assert report['case']['objective'] == 200 and 'objective' not in report['baseline_case']
    assert len(solve_queue.list_jobs()) == 1


def test_real_infeasible_case_is_visible_without_a_comparison_delta(baseline):
    good, _ = completed_case(20, 'good')
    bad, failed = completed_case(1, 'bad', path='Generator.G1.p_nom', succeeds=False)
    report = tool('compare_experiments', experiment_id=good, baseline_experiment_id=bad)
    assert report['status'] == 'partial' and not report['comparable'] and report['deltas'] == {}
    assert failed['status'] == report['baseline_case']['status'] == 'failed'
    assert report['case']['objective'] == 200 and 'objective' not in report['baseline_case']
    assert len(solve_queue.list_jobs()) == 2


def test_zero_baseline_objective_has_no_percentage(baseline):
    ctx = baseline[0]
    # Nonzero native objective terms cancel: two hours of +5*20 and -5*20.
    ctx.network.add('Generator', 'G2', bus='B1', p_nom=5, p_min_pu=1, marginal_cost=-20)
    ctx.network.loads.at['L1', 'p_set'] = 10
    chat_tools.save_project(ctx.loaded_project)
    zero, _ = completed_case(20, 'zero')
    positive, _ = completed_case(40, 'positive')
    report = tool('compare_experiments', experiment_id=positive, baseline_experiment_id=zero)
    assert report['comparable']
    assert report['deltas']['objective']['delta'] == {'value': 200, 'unit': 'EUR'}
    assert report['deltas']['objective']['delta_pct'] is None


@pytest.mark.parametrize('damage', ['precision', 'weights', 'solver_config', 'evidence', 'units', 'mode', 'missing_ledger', 'corrupt_ledger'])
def test_changed_or_missing_native_sources_withhold_delta_and_stale_export(baseline, damage):
    from services import project_registry, workflow_tools as wt
    low, _ = completed_case(20, 'low')
    high, high_result = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    inspected = tool('compare_experiments', **args)
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, high_result['case_project_id'])
        directory = project_registry.project_dir(child)
        if damage in {'precision', 'weights', 'solver_config'}:
            ctx = wt._load_context(child)
            if damage == 'precision':
                ctx.network.generators.at['G1', 'p_nom'] += 1e-11
            elif damage == 'weights':
                ctx.network.snapshot_weightings.iloc[0, 0] += 1
            else:
                ctx.solver_state['solver_config'].solver_name = 'changed-solver'
            from routers import projects
            projects._save_context(ctx, child.name, storage_dir=directory, db=db, user=user, project_row=child)
        elif damage == 'evidence':
            (directory / 'run_evidence' / high_result['run_id'] / 'sections/summary.json').write_text('{}')
        elif damage in {'units', 'mode'}:
            path = directory / 'run_evidence' / high_result['run_id'] / 'manifest.json'
            manifest = json.loads(path.read_text())
            if damage == 'units':
                manifest['metrics']['objective']['unit'] = 'USD'
            else:
                manifest['mode'] = 'other-mode'
            path.write_text(json.dumps(manifest))
        else:
            ledger = baseline[2] / 'experiment_execution' / (high + '.json')
            if damage == 'missing_ledger':
                ledger.unlink()
            else:
                ledger.write_text('{corrupt')
    report = tool('compare_experiments', **args)
    assert not report['comparable'] and not report['deltas'] and report['status'] == 'partial'
    assert report['case']['status'] == 'unavailable' and 'objective' not in report['case']
    assert report['baseline_case']['objective'] == 200
    with pytest.raises(HTTPException) as error:
        tool('export_experiment_comparison', **args, comparison_sha256=inspected['comparison_sha256'])
    assert error.value.detail['error_kind'] == 'experiment_comparison_changed'
    assert len(solve_queue.list_jobs()) == 2
    if damage == 'missing_ledger':
        assert not ledger.exists()


def test_distinct_prepared_baselines_are_not_mistaken_for_declared_case_differences(baseline):
    ctx, _project, _root = baseline
    low, _ = completed_case(20, 'low')
    ctx.network.generators.at['G1', 'p_nom'] = 21
    chat_tools.save_project(ctx.loaded_project)
    high, _ = completed_case(30, 'high')
    report = tool('compare_experiments', experiment_id=high, baseline_experiment_id=low)
    assert not report['comparable'] and report['deltas'] == {}
    assert 'prepared_baselines_differ' in report['incomparable_reasons']
    assert report['case']['objective'] == 300 and report['baseline_case']['objective'] == 200


def test_historical_pair_does_not_restore_or_depend_on_current_baseline_inputs(baseline):
    ctx, _project, root = baseline
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    before = (root / 'network.nc').read_bytes()
    ctx.network.generators.at['G1', 'p_nom'] = 21
    report = tool('compare_experiments', experiment_id=high, baseline_experiment_id=low)
    assert report['comparable'] and report['deltas']['objective']['delta']['value'] == 100
    assert ctx.network.generators.at['G1', 'p_nom'] == 21
    assert (root / 'network.nc').read_bytes() == before


def test_foreign_owner_cannot_compare_or_export_known_ids(baseline, second_identity):
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    inspected = tool('compare_experiments', **args)
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(second_identity['user_id'])
    try:
        for name, extra in [('compare_experiments', {}), ('export_experiment_comparison', {'comparison_sha256': inspected['comparison_sha256']})]:
            with pytest.raises(HTTPException) as error:
                tool(name, **args, **extra)
            assert error.value.status_code == 404
    finally:
        chat_tools.set_acting_user(previous)


def test_cancelled_but_completed_case_keeps_result_and_invalidates_old_export(baseline):
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    before = tool('compare_experiments', **args)
    tool('cancel_experiment', experiment_id=high)
    current = tool('compare_experiments', **args)
    assert current['case']['cancelled'] and current['comparable']
    assert current['deltas']['objective']['delta']['value'] == 100
    with pytest.raises(HTTPException) as error:
        tool('export_experiment_comparison', **args, comparison_sha256=before['comparison_sha256'])
    assert error.value.detail['error_kind'] == 'experiment_comparison_changed'


def test_comparison_never_adopts_an_unreconciled_lost_return(baseline):
    from tests.test_experiment_case_advancement import prepared
    good, _ = completed_case(20, 'good')
    uncertain = prepared()
    session, armed = arm(uncertain)
    actual = {}
    def lost(**args):
        actual.update(chat_tools.run_sensitivity_sweep(**args)['cases'][0])
        raise RuntimeError('return lost')
    with pytest.raises(RuntimeError):
        invoke(session, armed['next_call'], lost)
    assert chat_tools.wait_for_job(actual['job']['id'], 25)['completed']
    files = list((baseline[2] / 'experiment_execution').glob('*.json'))
    before = {p: p.read_bytes() for p in files}
    report = tool('compare_experiments', experiment_id=good, baseline_experiment_id=uncertain)
    assert report['status'] == 'partial' and not report['deltas']
    assert report['baseline_case']['status'] == 'uncertain'
    assert report['baseline_case']['job_id'] is None and 'objective' not in report['baseline_case']
    assert all(p.read_bytes() == blob for p, blob in before.items())
    assert len(solve_queue.list_jobs()) == 2


def test_duplicate_plan_selection_is_refused(baseline):
    eid, _ = completed_case(20, 'one')
    with pytest.raises(HTTPException) as error:
        tool('compare_experiments', experiment_id=eid, baseline_experiment_id=eid)
    assert error.value.detail['error_kind'] == 'invalid_experiment_comparison'


def test_read_comparison_remains_available_under_a_foreign_lock_but_export_refuses(baseline, second_identity):
    from services import project_registry, project_locks
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, baseline[1])
        project_locks.release_lock(db, project.id, user.id)
        assert project_locks.acquire_lock(db, project.id, second_identity['user_id'])
        try:
            report = tool('compare_experiments', **args)
            assert report['comparable']
            with pytest.raises(HTTPException) as error:
                tool('export_experiment_comparison', **args, comparison_sha256=report['comparison_sha256'])
            assert error.value.detail['error_kind'] == 'project_locked'
        finally:
            project_locks.release_lock(db, project.id, second_identity['user_id'])
            assert project_locks.acquire_lock(db, project.id, user.id)


@pytest.mark.parametrize('name', ['compare_experiments', 'export_experiment_comparison'])
def test_strict_checked_chat_does_not_expand_into_comparison_tools(baseline, checked, name):
    from tests.assurance_acceptance.support import UNSAFE, public_text, text_turn, tool_turn
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    if name.startswith('export'):
        args['comparison_sha256'] = tool('compare_experiments', **args)['comparison_sha256']
    frames = checked.run([tool_turn(name, args), text_turn(UNSAFE), text_turn(UNSAFE)])
    assert any(event == 'tool_error' and p.get('tool_name') == name for event, p in frames)
    assert UNSAFE not in public_text(frames)
    assert len(solve_queue.list_jobs()) == 2


@pytest.mark.parametrize('decision', ['approve', 'deny', 'stale'])
def test_authenticated_guided_export_requires_current_ordinary_confirmation(baseline, client, monkeypatch, decision):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from services import chat_service
    from tests.assurance_acceptance.support import Harness, parse_sse, text_turn, tool_turn
    low, _ = completed_case(20, 'low')
    high, _ = completed_case(30, 'high')
    args = dict(experiment_id=high, baseline_experiment_id=low)
    report = tool('compare_experiments', **args)
    export_args = dict(args, comparison_sha256=report['comparison_sha256'])
    sid = 'comparison-export-' + decision
    harness = Harness(client, monkeypatch)
    harness.install([tool_turn('export_experiment_comparison', export_args), text_turn('Report inspected.')])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/chat/stream', json={'session_id': sid,
                'message': 'export_experiment_comparison for this exact report', 'ui_context': {'ui_mode': 'guided'}})
            deadline = time.monotonic() + 10
            token = None
            while time.monotonic() < deadline and not pending.done():
                session = chat_service.get_session(sid)
                if session:
                    with session._lock:
                        token = next((k for k, c in session.pending_confirmations.items()
                                      if c.tool_name == 'export_experiment_comparison'), None)
                if token:
                    break
                time.sleep(0.02)
            if token is None:
                client.post(f'/api/chat/{sid}/abort')
            assert token, 'Ordinary Guided write confirmation was not issued'
            if decision == 'stale':
                tool('cancel_experiment', experiment_id=high)
            response = client.post(f'/api/chat/{sid}/confirm', json={'token': token,
                'decision': 'deny' if decision == 'deny' else 'approve'})
            assert response.status_code == 200
            completed = pending.result(timeout=10)
        assert completed.status_code == 200
        frames = parse_sse(completed.text)
        assert any(event == 'tool_pending_confirmation' for event, _ in frames)
        errors = [p for event, p in frames if event in {'error', 'tool_error'}]
        if decision == 'approve':
            assert not errors, errors
            exports = [p['result'] for event, p in frames if event == 'tool_result'
                       and p.get('tool_name') == 'export_experiment_comparison']
            assert exports
            delivery = chat_tools.get_file_delivery(exports[0]['file_id'])
            downloaded = client.get(delivery['download_url'])
            assert downloaded.status_code == 200 and downloaded.json() == report
        else:
            assert any(p['error_kind'] == ('confirmation_denied' if decision == 'deny'
                                          else 'experiment_comparison_changed') for p in errors), errors
        assert client.post(f'/api/chat/{sid}/confirm', json={'token': token, 'decision': 'approve'}).status_code == 404
        assert len(solve_queue.list_jobs()) == 2
    finally:
        chat_service.drop_session(sid)


def test_referenced_baseline_evidence_loss_is_not_ignored_by_pair_comparison(baseline, monkeypatch):
    import time
    from services import run_evidence, chat_service
    session = chat_service.ChatSession()
    monkeypatch.setattr(session, 'wait_for_decision', lambda _: 'approve')
    frames = list(chat_service._dispatch_real_tool_call(session,
        {'id': 'baseline', 'name': 'run_simulation', 'input': {}}, []))
    result = next(p['result'] for event, p in frames if event == 'tool_result')
    assert 'job_id' in result, result
    assert chat_tools.wait_for_job(result['job_id'], 25)['completed']
    baseline_run = run_evidence.foreground_run_id(result['job_id'])
    # Solver completion precedes the detached evidence publisher. Wait for its
    # public terminal receipt; an unfinished marker is not baseline proof.
    deadline = time.monotonic() + 10
    evidence = run_evidence.get_run_evidence(baseline_run)
    while evidence['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(0.02)
        evidence = run_evidence.get_run_evidence(baseline_run)
    assert evidence['status'] == 'completed' and evidence['evidence_available'], evidence
    assert evidence['fingerprint']
    assert evidence['metrics']['objective'] == {'value': 100, 'unit': 'EUR'}
    chat_tools.save_project(baseline[0].loaded_project)
    low, _ = completed_case(20, 'low', baseline_run_id=baseline_run)
    high, _ = completed_case(30, 'high', baseline_run_id=baseline_run)
    args = dict(experiment_id=high, baseline_experiment_id=low)
    assert tool('compare_experiments', **args)['comparable']
    (baseline[2] / 'run_evidence' / baseline_run / 'sections/summary.json').write_text('{}')
    report = tool('compare_experiments', **args)
    assert not report['comparable'] and not report['deltas']
    assert report['status'] == 'unavailable'
    assert 'objective' not in report['case'] and 'objective' not in report['baseline_case']
    assert report['case']['case_effects_reserved'] and report['baseline_case']['case_effects_reserved']
    assert report['case']['job_id'] and report['baseline_case']['job_id']
    assert len(solve_queue.list_jobs()) == 2
