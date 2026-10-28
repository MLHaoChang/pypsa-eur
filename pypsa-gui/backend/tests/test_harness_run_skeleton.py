"""Study harness issue 01: the harness run skeleton on eh_site.

`start_task(run=...)` (task file version 3), the profile schema
(`services/run_profiles.py`), typed gates over native facts
(`services/run_gates.py`), step-by-step mode only. Real project and real
solve queue, ordinary dispatch seam; no provider calls."""
# ruff: noqa: F811 -- imported pytest fixtures
import contextlib
import copy
import importlib
import json

import pytest
from services import assistant_tasks as tasks, chat_tools, flow_service, run_gates, run_profiles
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, refused, session, stored, tool_result, wait_until,
)

RUN = {'profile': 'eh_site@1', 'mode': 'stepwise'}
BRIEF = {'objective': 'Size the own site supply through 2035.', 'cards': ['capacity']}


@pytest.fixture
def site(baseline):
    """The baseline project, stamped as an eh_site project (its template file)."""
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({'id': 'eh_site', 'site_type': 'industrial'}))
    return project_id, root


def start(request_id='run-1', **kwargs):
    return chat_tools.start_task(title='Own site study', steps=[], request_id=request_id,
                                 brief=kwargs.pop('brief', BRIEF), run=kwargs.pop('run', RUN), **kwargs)


def drive(session, monkeypatch, task, root, limit=20):
    """Run the plan as the chat turn does: each next_step as an ordinary
    offered call, the user's Continue at each card pause, waits on jobs."""
    carded = []
    for _ in range(limit):
        view = tasks.get_task(task['task_id'])
        if view['status'] == 'paused_at_card':
            tasks.continue_at_card(session, task['task_id'])
            continue
        step = view.get('next_step')
        if view['status'] in ('completed', 'needs_review') or step is None:
            return view, carded
        frames, _ = dispatch(session, monkeypatch, step['tool'], step['args'])
        assert not [p for e, p in frames if e == 'tool_error'], frames
        if [p for e, p in frames if e == 'tool_pending_confirmation']:
            carded.append(step['tool'])
        assert wait_until(lambda: all(s['status'] != 'waiting' for s in stored(root, task['task_id'])['steps']))
    raise AssertionError('the run did not settle')


def stages(view):
    return {s['stage']: s for s in view['run']['stages']}


# ── The tracer bullet ────────────────────────────────────────────────────────


def test_eh_site_run_reaches_handoff_step_by_step_with_every_run_carded(session, site, monkeypatch):
    project_id, root = site
    task = start()
    assert task['run']['stage'] == 'inputs' and task['next_step']['tool'] == 'get_weather_choice'
    assert task['next_step']['args'] == {'project_id': project_id}
    view, carded = drive(session, monkeypatch, task, root)
    assert view['status'] == 'completed' and view['run']['status'] == 'completed', view
    assert carded == ['solve_queue_enqueue']  # COP-1: the run step has its card; reads run without one
    by = stages(view)
    for name in ('intake', 'setup', 'inputs', 'input_gate', 'solve', 'result_gate', 'handoff'):
        assert by[name]['status'] == 'passed', by[name]
    assert {s for s in by if by[s]['status'] == 'skipped'} == {'explore', 'report'}
    gate = {r['rule']: r for r in by['input_gate']['gate']}
    assert gate['in.validate.errors'] == {'rule': 'in.validate.errors', 'verdict': 'pass', 'fact': 'validate.errors',
                                          'value': 0, 'unit': 'count'}
    assert {r['rule']: r['value'] for r in by['result_gate']['gate']} == {'res.solve.status': 'optimal', 'res.fresh': True}
    assert view['run']['scope'] == 'eh_site:industrial'
    record = stored(root, task['task_id'])
    assert record['version'] == 3 and [s['id'] for s in record['steps']] == \
        ['inputs.1', 'input_gate.1', 'input_gate.2', 'input_gate.3', 'solve.1']
    assert record['run']['stages']['input_gate']['gate']['rules'][0]['facts'][0]['source'] == \
        'services.chat_tools.validate_network:errors'


def test_validation_error_stops_at_the_input_gate_and_enqueues_no_solve(session, site, monkeypatch):
    from services.pypsa_service import PyPSAService
    _, root = site
    PyPSAService.get_network().generators.at['G1', 'bus'] = 'nowhere'  # a dangling bus: a validation error
    task = start('run-invalid')
    view, carded = drive(session, monkeypatch, task, root)
    assert view['status'] == 'needs_review' and view['run']['status'] == 'needs_user'
    assert view['run']['stage'] == 'input_gate' and 'next_step' not in view
    gate = {r['rule']: r for r in stages(view)['input_gate']['gate']}
    assert gate['in.validate.errors']['verdict'] == 'fail' and gate['in.validate.errors']['value'] >= 1
    assert stages(view)['input_gate']['status'] == 'failed' and stages(view)['solve']['status'] == 'pending'
    assert carded == [] and not any(s['tool'] == 'solve_queue_enqueue' for s in stored(root, task['task_id'])['steps'])


def test_a_reader_that_raises_is_not_evaluated_and_fails_a_block_rule(session, site, monkeypatch):
    def broken(env):
        raise RuntimeError('reader down')
    fact = run_gates.FACTS['validate.errors']
    monkeypatch.setitem(run_gates.FACTS, 'validate.errors', run_gates.Fact(fact.reads, fact.key, fact.unit, broken))
    _, root = site
    view, _ = drive(session, monkeypatch, start('run-raise'), root)
    gate = {r['rule']: r for r in stages(view)['input_gate']['gate']}
    assert gate['in.validate.errors']['verdict'] == 'not_evaluated' and 'RuntimeError' in gate['in.validate.errors']['reason']
    assert view['run']['status'] == 'needs_user' and stages(view)['input_gate']['status'] == 'failed'


@pytest.mark.parametrize('value', [None, float('nan')])
def test_a_fact_reading_none_or_nan_fails_its_rule(value, monkeypatch):
    rule = run_gates.check_rule({'id': 'r', 'fact': 'validate.errors', 'op': 'le', 'value': 0, 'severity': 'block',
                                 'basis': 'x'})
    fact = run_gates.FACTS['validate.errors']
    monkeypatch.setitem(run_gates.FACTS, 'validate.errors', run_gates.Fact(fact.reads, fact.key, fact.unit, lambda env: value))
    env = run_gates.Env(task={}, ctx=None, project=None, project_dir=None)
    results = run_gates.evaluate([rule], env)
    assert results[0]['verdict'] == 'not_evaluated' and not run_gates.passed(results)
    warn = run_gates.evaluate([{**rule, 'severity': 'warn'}], env)
    assert warn[0]['verdict'] == 'not_evaluated'  # listed as not evaluated, never a pass


def test_sc12_resolve_task_step_cannot_pass_a_gate(session, site, monkeypatch):
    from services.pypsa_service import PyPSAService
    _, root = site
    PyPSAService.get_network().generators.at['G1', 'bus'] = 'nowhere'
    task = start('run-sc12')
    drive(session, monkeypatch, task, root)
    before = stored(root, task['task_id'])['run']
    refused('task_resolution_not_needed', tasks.resolve_task_step, task['task_id'], 'completed',
            result={'verdict': 'pass', 'validate.errors': 0, 'run': {'status': 'running'}})
    assert stored(root, task['task_id'])['run'] == before
    with pytest.raises(TypeError):  # no argument writes the run block
        chat_tools.start_task(title='x', steps=[], request_id='y', brief=BRIEF, run=RUN, gate={'passed': True})


def test_sc12_a_reconciled_step_still_meets_its_gate_from_native_facts(session, site, monkeypatch):
    """The step completes from its own native evidence; the gate then reads
    the network, not anything the caller said."""
    from services.pypsa_service import PyPSAService
    _, root = site
    task = start('run-sc12-native')
    assert tasks.get_task(task['task_id'])['next_step']['tool'] == 'get_weather_choice'
    ticket = tasks.begin_step(session, 'get_weather_choice', task['next_step']['args'])
    tasks.finish_step(ticket, error='timed out')  # a read: failed, retryable
    PyPSAService.get_network().generators.at['G1', 'bus'] = 'nowhere'
    tasks._late_receipt(tasks._path(task['task_id'])[0], ticket, {'ok': True})
    view = tasks.resolve_task_step(task['task_id'], 'completed', result={'validate.errors': 0})
    assert view['run']['stage'] == 'input_gate'  # the inputs gate passed; the next gate waits on its steps


def test_cancel_stops_the_run(session, site):
    task = start('run-cancel')
    view = tasks.cancel_task(task['task_id'])
    assert view['status'] == 'cancelled' and view['run']['status'] == 'cancelled'


def test_same_request_is_idempotent_and_a_different_run_conflicts(session, site):
    first = start('run-same')
    assert start('run-same')['task_id'] == first['task_id']
    refused('task_request_conflict', start, 'run-same', brief={**BRIEF, 'objective': 'Another goal.'})


def test_wrong_template_stops_at_setup(session, baseline, monkeypatch):
    _, _, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({'id': 'eh_datacenter'}))
    view = start('run-dc')
    assert view['run']['stage'] == 'setup' and view['run']['status'] == 'needs_user'
    gate = {r['rule']: r for r in stages(view)['setup']['gate']}
    assert gate['set.project.template']['verdict'] == 'fail' and gate['set.project.template']['value'] == 'eh_datacenter'


# ── start_task admission ─────────────────────────────────────────────────────


def test_run_admission_refusals(session, site):
    refused('invalid_task', start, 'a', run={'profile': 'eh_site@1', 'mode': 'envelope'})
    refused('invalid_task', start, 'b', run={'profile': 'nope@1', 'mode': 'stepwise'})
    refused('invalid_task', start, 'c', run={'profile': 'eh_site@1'})
    refused('invalid_brief', start, 'd', brief={**BRIEF, 'phases': [{'card': 'capacity', 'steps': ['s']}]})
    refused('invalid_brief', start, 'e', brief=None)
    refused('invalid_task', chat_tools.start_task, title='t', request_id='f', brief=BRIEF, run=RUN,
            steps=[{'id': 's', 'tool': 'validate_network', 'args': {}}])


def test_version_1_and_2_files_read_unchanged(session, site):
    from tests.test_assistant_tasks_study_brief import brief, reads
    _, root = site
    plain = chat_tools.start_task(title='Checks', steps=reads(2), request_id='v1')
    briefed = chat_tools.start_task(title='Brief', steps=reads(2), request_id='v2', brief=brief(reads(2)))
    for task, version in ((plain, 1), (briefed, 2)):
        record = stored(root, task['task_id'])
        assert record['version'] == version and 'run' not in record
        view = tasks.get_task(task['task_id'])
        assert 'run' not in view and view['next_step']['id'] == 's0'
    assert set(tasks.get_task(briefed['task_id'])['brief']) == {'objective', 'cards', 'cases', 'phase', 'phases'}


def big(stages):
    profile = copy.deepcopy(run_profiles.EH_SITE)
    profile['stages'] = {name: {'actions': [{'tool': 'validate_network', 'args': {}}] * count, 'rules': []}
                         for name, count in stages.items()}
    return profile


def test_step_limit_is_per_stage(session, site, monkeypatch):
    monkeypatch.setitem(run_profiles.PROFILES, 'big@1', big({'inputs': 25}))
    refused('invalid_task', start, 'big', run={'profile': 'big@1', 'mode': 'stepwise'})
    run_profiles.load(big({'inputs': 10, 'input_gate': 10, 'handoff': 10}))  # 3 stages × 10 steps
    monkeypatch.setitem(run_profiles.PROFILES, 'long@1', big({'inputs': 10, 'input_gate': 10, 'handoff': 10}))
    assert start('long', run={'profile': 'long@1', 'mode': 'stepwise'})['run']['stage'] == 'inputs'


# ── Profile load (B4, §6.0) ──────────────────────────────────────────────────


def with_rule(**rule):
    profile = copy.deepcopy(run_profiles.EH_SITE)
    profile['stages']['handoff'] = {'actions': [], 'rules': [
        {'id': 'h', 'fact': 'validate.errors', 'op': 'eq', 'value': 0, 'severity': 'block', 'basis': 'x', **rule}]}
    return profile


def test_the_fact_registry_is_closed_and_versioned():
    assert run_gates.FACTS_VERSION == 1
    with pytest.raises(run_profiles.ProfileError, match='unknown fact'):
        run_profiles.load(with_rule(fact='kpi.made_up'))
    with pytest.raises(run_profiles.ProfileError, match='wildcard'):
        run_profiles.load(with_rule(fact='validate.*'))
    with pytest.raises(run_profiles.ProfileError, match='unit'):
        run_profiles.load(with_rule(unit='MW'))
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load(with_rule(op='roughly'))


@pytest.mark.parametrize('entry', [{'component': 'grid_code', 'attr': 'voltage_limit_pu'},
                                   {'component': 'site', 'attr': 'grid_code_thd_limit'},
                                   {'component': 'goal', 'attr': 'target_lole_h'},
                                   {'component': 'site', 'attr': 'it_load'},
                                   {'component': 'Load', 'attr': 'p_set'}])
def test_a_never_tunable_attr_is_refused_at_load(entry):
    profile = copy.deepcopy(run_profiles.EH_SITE)
    profile['tunable'] = [*profile['tunable'], entry]
    with pytest.raises(run_profiles.ProfileError, match='never tunable'):
        run_profiles.load(profile)


def test_a_stage_tool_without_its_bound_context_test_is_refused():
    profile = copy.deepcopy(run_profiles.EH_SITE)
    profile['stages']['inputs'] = {'actions': [{'tool': 'fetch_site_context', 'args': {}}], 'rules': []}
    with pytest.raises(run_profiles.ProfileError, match='run.s own project'):
        run_profiles.load(profile)


def test_eh_site_card_scope_is_read_from_flow_scope():
    from services import flow_scope
    hospital = run_profiles.scope_for({'id': 'eh_site', 'site_type': 'hospital'})
    assert hospital['preset'] == 'eh_site:hospital'
    assert hospital['cards'] == {k: list(v) for k, v in flow_scope.SITE_TYPE_PRESETS['hospital'].items()}


# ── Each rule names what it reads; a renamed row fails here ─────────────────


def test_every_fact_names_a_reader_that_exists_and_returns_its_key(session, site, monkeypatch):
    for name, fact in run_gates.FACTS.items():
        if fact.reads.startswith('task.'):
            continue
        module, function = fact.reads.rsplit('.', 1)
        assert callable(getattr(importlib.import_module(module), function)), name
    from services import workflow_tools
    project_id, root = site
    assert 'errors' in chat_tools.validate_network()
    assert 'ready' in workflow_tools.project_readiness(project_id)
    assert 'islands_without_generation' in chat_tools.diagnose_network()
    assert 'results_current' in workflow_tools.solve_freshness()
    assert flow_service.read_template(root)['id'] == 'eh_site'
    rules = [r for stage in run_profiles.resolve('eh_site@1')['stages'].values() for r in stage.get('rules', [])]
    assert rules and all(r['reads'] == run_gates.FACTS[r['fact']].reads + ':' + run_gates.FACTS[r['fact']].key
                         for r in rules)


def test_solve_facts_read_the_queue_jobs_status_and_condition(session, site, monkeypatch):
    _, root = site
    task = start('run-keys')
    drive(session, monkeypatch, task, root)
    record = stored(root, task['task_id'])
    from services import project_registry
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, record['project_id'])
        job = run_gates._solve_job(run_gates.Env(task=record, ctx=None, project=project, project_dir=root))
        assert job['project_key'] == project_registry.registry_key(project)  # the run's project's job, by its stored id
    assert {'status', 'condition'} <= set(job) and job['id'] == record['steps'][-1]['job']['job_id']


# ── §6.6 bound-context sweep ─────────────────────────────────────────────────


@contextlib.contextmanager
def foreground(network):
    """Another project in the foreground: a request context with no turn
    cell, as the runner's thread sees the process."""
    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.build_context()
    ctx.network = network
    cell = PyPSAService._turn_cell.set(None)
    token = PyPSAService.bind_request_context(ctx)
    try:
        yield ctx
    finally:
        PyPSAService.reset_request_context(token)
        PyPSAService._turn_cell.reset(cell)


def two_buses():
    import pypsa
    n = pypsa.Network()
    n.add('Bus', 'F1')
    n.add('Bus', 'F2')
    return n


def enqueued_for(queued, project_id):
    """The job record's project is the run's (the §6.6 solve-path row)."""
    from routers.solve_queue import _visible_job_or_404
    from services import project_registry
    with chat_tools._acting() as (db, user):
        job = _visible_job_or_404(db, user, str(queued.get('job_id') or queued.get('id')))
        return job['project_key'] == project_registry.registry_key(project_registry.resolve_project(db, user, project_id))


def test_each_stage_tool_resolves_the_project_through_the_bound_context(session, site):
    """With another network in the foreground, each tool of the eh_site stage
    subsets, run on the run's bound context, reads the run's project (§6.6)."""
    from services import workflow_tools
    from services.pypsa_service import PyPSAService
    from services.study.runner import _on_ctx
    project_id, _ = site
    run_ctx = PyPSAService.get_active_context()
    run_ctx.network.generators.at['G1', 'bus'] = 'nowhere'  # only the run's project has this error
    tools = {a['tool'] for stage in run_profiles.resolve('eh_site@1')['stages'].values() for a in stage.get('actions', [])}
    assert tools <= run_profiles.BOUND_CONTEXT_TOOLS
    with foreground(two_buses()) as front:
        assert PyPSAService.get_network() is front.network
        assert chat_tools.diagnose_network()['bus_count'] == 2 and chat_tools.validate_network()['errors'] == 0
        seen = {
            'validate_network': _on_ctx(run_ctx, chat_tools.validate_network)['errors'] >= 1,
            'diagnose_network': _on_ctx(run_ctx, chat_tools.diagnose_network)['bus_count'] == 1,
            'project_readiness': _on_ctx(run_ctx, workflow_tools.project_readiness)['name'] == 'Tool Baseline'
                                 and _on_ctx(run_ctx, workflow_tools.project_readiness)['ready'] is False,
            'get_weather_choice': isinstance(_on_ctx(run_ctx, chat_tools.get_weather_choice, project_id), dict),
            'solve_queue_enqueue': enqueued_for(_on_ctx(run_ctx, chat_tools.solve_queue_enqueue, project_id), project_id),
        }
        assert PyPSAService.get_network() is front.network  # the foreground is untouched
    assert seen == {tool: True for tool in run_profiles.BOUND_CONTEXT_TOOLS}


# ── The assistant inventory: no new catalogue tool ──────────────────────────


def test_no_new_catalogue_tool():
    from harness.catalogue import TOOLS
    start_task = next(t for t in TOOLS if t['name'] == 'start_task')
    schema = start_task.get('input_schema') or start_task.get('parameters')
    assert schema['properties']['run']['properties']['profile']['enum'] == sorted(run_profiles.PROFILES)
    assert not any(t['name'].startswith(('run_profile', 'harness_run', 'evaluate_gate')) for t in TOOLS)


# ── GK B conditions on the plan ──────────────────────────────────────────────


def until_paused_at_solve(session, monkeypatch, task, root):
    for _ in range(10):
        view = tasks.get_task(task['task_id'])
        if view['status'] == 'paused_at_card':
            assert view['paused_at_card'] == 'solve'
            return view
        step = view['next_step']
        dispatch(session, monkeypatch, step['tool'], step['args'])
    raise AssertionError('the run never reached the solve card')


def test_b1_an_edit_after_the_input_gate_rechecks_it_before_the_solve(session, site, monkeypatch):
    from services.pypsa_service import PyPSAService
    _, root = site
    task = start('run-b1')
    view = until_paused_at_solve(session, monkeypatch, task, root)
    assert stages(view)['input_gate']['status'] == 'passed'
    PyPSAService.get_network().generators.at['G1', 'bus'] = 'nowhere'  # an edit during the pause
    tasks.continue_at_card(session, task['task_id'])
    record = stored(root, task['task_id'])
    assert not any(s['id'].startswith('solve.') for s in record['steps'])  # nothing to enqueue
    gate = record['run']['stages']['input_gate']
    assert gate['status'] == 'failed' and gate['attempts'] == 2 and record['run']['status'] == 'needs_user'
    verdicts = {r['rule']: (r['verdict'], r['facts'][0]['value']) for r in gate['gate']['rules']}
    assert verdicts['in.validate.errors'][0] == 'fail' and verdicts['in.validate.errors'][1] >= 1
    assert tasks.begin_step(session, 'solve_queue_enqueue', {'project_id': site[0]}) is None  # no plan step
    # The user fixes it; resume checks the same gate again and re-enters the solve stage.
    PyPSAService.get_network().generators.at['G1', 'bus'] = 'B1'
    view = tasks.resume_task(task['task_id'])
    assert stages(view)['input_gate']['status'] == 'passed' and view['status'] == 'paused_at_card'
    refused('task_pause_needs_user', tasks.resume_task, task['task_id'])  # refused while the card waits
    assert [s['id'] for s in stored(root, task['task_id'])['steps'] if s['id'].startswith('solve.')] == ['solve.1']


def test_b1_a_gate_whose_inputs_change_while_it_reads_is_not_evaluated(session, site, monkeypatch):
    from services.pypsa_service import PyPSAService
    real = run_gates._validate

    def editing(env):
        value = real(env)
        PyPSAService.get_network().generators.at['G1', 'marginal_cost'] = 99.0
        return value
    monkeypatch.setitem(run_gates.FACTS, 'validate.errors', run_gates.Fact(
        'services.chat_tools.validate_network', 'errors', 'count', editing))
    ctx = PyPSAService.get_active_context()
    task = {'project_id': str(ctx.project_uuid), 'steps': []}
    gate = tasks._gate_now(task, run_profiles.CORE_INPUT_RULES, ctx)
    assert not gate['passed'] and {r['reason'] for r in gate['rules']} == {'inputs changed during evaluation'}


def test_b2_the_gate_reads_the_context_it_is_given_and_only_the_runs_project(session, site):
    """Foreground B has a validation error, the run's project A is clean."""
    import pypsa
    from services.pypsa_service import PyPSAService
    ctx_a = PyPSAService.get_active_context()
    task = {'project_id': str(ctx_a.project_uuid), 'steps': []}
    assert task['project_id'] == site[0]
    rules = [run_gates.check_rule(r) for r in run_profiles.CORE_INPUT_RULES]
    broken = pypsa.Network()
    broken.add('Bus', 'X')
    broken.add('Generator', 'g', bus='nowhere')
    with foreground(broken) as ctx_b:
        assert chat_tools.validate_network()['errors'] >= 1  # the foreground's own error
        assert tasks._gate_now(task, rules, ctx_a)['passed'] is True
        reverse = tasks._gate_now(task, rules, ctx_b)
    assert reverse['passed'] is False
    assert {r['reason'] for r in reverse['rules']} == {"the context is not the run's project"}


def test_racing_evaluations_advance_once(session, site, monkeypatch):
    """finish_step and get_task evaluating the same gate: one stage advance,
    one materialisation of the next stage's steps."""
    _, root = site
    task = start('run-race')
    path = tasks._path(task['task_id'])[0]
    real, raced = run_gates.gate, []

    def racing(rules, env):
        if not raced and rules and rules[0]['id'] == 'in.validate.errors':
            raced.append(True)
            tasks._advance_run(path)  # get_task's evaluation lands first
        return real(rules, env)
    monkeypatch.setattr(run_gates, 'gate', racing)
    until_paused_at_solve(session, monkeypatch, task, root)
    record = stored(root, task['task_id'])
    assert raced and record['run']['stages']['input_gate']['attempts'] == 1
    assert [s['id'] for s in record['steps'] if s['id'].startswith('solve.')] == ['solve.1']


def test_get_task_writes_the_run_block_only_for_a_caller_who_may_edit(session, site, monkeypatch):
    from fastapi import HTTPException
    import routers.projects
    _, root = site
    task = start('run-lock')
    assert task['run']['stage'] == 'inputs'

    def locked(db, project, user):
        raise HTTPException(409, {'error_kind': 'project_locked', 'message': 'locked'})
    monkeypatch.setattr(routers.projects, '_check_project_lock', locked)
    view = tasks.get_task(task['task_id'])
    dispatch(session, monkeypatch, view['next_step']['tool'], view['next_step']['args'])
    before = stored(root, task['task_id'])
    view = tasks.get_task(task['task_id'])
    assert view['run']['stage'] == 'inputs' and view['run']['gate_due'] is True
    assert stored(root, task['task_id']) == before
    monkeypatch.undo()
    assert tasks.get_task(task['task_id'])['run']['stage'] == 'input_gate'


def test_a_profile_version_is_frozen_at_start(session, site, monkeypatch):
    later = copy.deepcopy(run_profiles.EH_SITE)
    later['version'] = 2
    later['stages']['handoff'] = {'actions': [], 'rules': [
        {'id': 'h.never', 'fact': 'validate.errors', 'op': 'lt', 'value': 0, 'severity': 'block', 'basis': 'x'}]}
    monkeypatch.setitem(run_profiles.PROFILES, 'eh_site@2', later)
    _, root = site
    task = start('run-v1')
    view, _ = drive(session, monkeypatch, task, root)
    assert view['run']['profile'] == 'eh_site@1' and view['run']['status'] == 'completed'
    assert stages(view)['handoff']['gate'] == []
    refused('invalid_task', start, 'run-v9', run={'profile': 'eh_site@9', 'mode': 'stepwise'})


def test_the_schema_loosening_does_not_reach_ordinary_plans(session, site):
    from tests.test_assistant_tasks_study_brief import reads
    refused('invalid_task', chat_tools.start_task, title='t', steps=[], request_id='p1')
    refused('invalid_brief', chat_tools.start_task, title='t', steps=reads(2), request_id='p2',
            brief={'objective': 'A goal.', 'cards': ['capacity']})


def test_resume_rechecks_a_failed_gate_from_native_facts_only(session, site, monkeypatch):
    from services.pypsa_service import PyPSAService
    _, root = site
    PyPSAService.get_network().generators.at['G1', 'bus'] = 'nowhere'
    task = start('run-resume')
    drive(session, monkeypatch, task, root)
    view = tasks.resume_task(task['task_id'])  # still broken: the same gate fails again
    assert stages(view)['input_gate']['status'] == 'failed'
    assert stored(root, task['task_id'])['run']['stages']['input_gate']['attempts'] == 2
    with pytest.raises(TypeError):
        tasks.resume_task(task['task_id'], result={'passed': True})


def test_another_projects_context_never_spends_a_gate(session, site):
    """A run advanced with the foreground of another project writes nothing:
    no not_evaluated gate, no attempt used."""
    _, root = site
    task = start('run-foreign')
    path = tasks._path(task['task_id'])[0]
    before = stored(root, task['task_id'])
    with foreground(two_buses()) as front:
        tasks._advance_run(path, front)
    assert stored(root, task['task_id']) == before
