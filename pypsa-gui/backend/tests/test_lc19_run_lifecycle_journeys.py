# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-19 Tier A: ready to run, which engine, re-run, infeasible (OJ-20, OJ-31-33).

The `oj-grid` fixture solved once by real HiGHS (OJ-07's fixture: the next
solve is held at a gate until released), then a scripted model over each real
provider wire. Only provider HTTP is mocked. OJ-33 has its own fixture,
`oj-grid-infeasible`: Bus B3 carries the whole load and no line reaches it.
"""
import time

import pypsa
import pytest

from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import LOAD_MW, NAME, build_oj_grid
from tests.test_oj07_edit_relaunch_walk_journey import Gate, _approve, _await_due, _settle_solver, oj_grid  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _cards(frames):
    return [d for e, d in frames if e == 'tool_pending_confirmation']


def _deny(cards):
    def on_frame(event, data):
        if event == 'tool_pending_confirmation':
            cards.append(data)
            chat_tools.chat_session().record_decision(data['confirmation_token'], 'deny')
    return on_frame


# ── OJ-20 · Ready to run? ────────────────────────────────────────────────────

@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_oj20_ready_to_run_reads_three_tools_and_runs_nothing(oj_grid, kind, model, base_url, auth):
    steps = [lambda r: ('project_readiness', {}), lambda r: ('validate_network', {}),
             lambda r: ('diagnose_network', {}), lambda r: (None, {})]
    def answer(results):
        issues = '; '.join(f"{i['severity']} {i['code']} ({i['component_class'] or 'network'} {i['name']})".rstrip()
                           for i in results[1]['issues'])
        return f"Ready to run: no errors. Warnings: {issues}. No island lacks generation."
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, steps,
                                            'Is the model ready to run? Anything odd in the inputs?', answer=answer)
    assert calls == ['project_readiness', 'validate_network', 'diagnose_network', None]
    readiness, validation, diagnosis = results
    assert readiness['ready'] is True and readiness['blockers'] == []
    assert validation['errors'] == 0
    codes = [i['code'] for i in validation['issues']]
    assert 'carrier_zero_co2' in codes
    text = ''.join(d.get('delta', '') for e, d in frames if e == 'token')
    assert all(code in text for code in codes)
    assert diagnosis['islands_without_generation'] == []
    assert not _cards(frames) and oj_grid.starts == []


# ── OJ-31 · Run, with the engine resolved ────────────────────────────────────

def _engine_options(readiness):
    return [{'label': e['label'], 'description': f"{e['kind']} ({e['tool']})"}
            for e in readiness['engines'] if e['kind'] in readiness['ask_engine']]


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_oj31_a_bare_run_asks_once_which_engine_then_cards_that_one(oj_grid, kind, model, base_url, auth):
    steps = [lambda r: ('project_readiness', {}),
             lambda r: ('ask_user', {'title': 'Which run?', 'question': 'This project can run either. Which one?',
                                     'options': _engine_options(r[0])}),
             lambda r: (None, {})]
    calls, results, frames, session = run_journey(kind, model, base_url, auth, steps, 'Run the study.',
                                                  answer='Which one?')
    assert calls == ['project_readiness', 'ask_user', None]
    assert results[0]['ask_engine'] == ['optimisation', 'loadflow']
    choices = [d for e, d in frames if e == 'choice_request']
    assert len(choices) == 1
    assert [o['label'] for o in choices[0]['options']] == [
        'Capacity expansion (sizing, dispatch, economics)', 'AC power flow']
    assert not _cards(frames)

    cards = []
    calls, results, frames, _ = run_journey(kind, model, base_url, auth,
                                            [lambda r: ('run_ac_pf_stage', {}), lambda r: (None, {})],
                                            'The AC power flow.', session=session, on_frame=_approve(cards))
    assert calls == ['run_ac_pf_stage', None]
    assert [c['tool_name'] for c in cards] == ['run_ac_pf_stage']
    assert oj_grid.starts == []  # the optimisation never started


def test_oj31_one_ready_engine_needs_no_question(oj_grid):
    chat_tools.update_component('Line', 'L1', {'s_nom': 500})  # the dispatch is now stale
    readiness = chat_tools.project_readiness()
    assert readiness['ask_engine'] is None
    loadflow = next(e for e in readiness['engines'] if e['kind'] == 'loadflow')
    assert loadflow['ready'] is False and 'optimisation' in loadflow['needs']
    optimisation = next(e for e in readiness['engines'] if e['kind'] == 'optimisation')
    assert optimisation['ready'] is True and optimisation['results_current'] is False


# ── OJ-32 · Re-run with unchanged inputs ─────────────────────────────────────

@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_oj32_rerun_of_current_results_asks_before_any_card(oj_grid, kind, model, base_url, auth):
    runs_before = chat_tools.list_project_runs(status='completed')['total_count']
    calls, results, frames, session = run_journey(
        kind, model, base_url, auth, [lambda r: ('run_simulation', {}), lambda r: (None, {})], 'Re-run it.',
        answer='The results are current; solve again anyway?')
    assert calls == ['run_simulation', None]
    assert 'results_current' in results[0]['error']
    latest = chat_tools.list_project_runs(limit=1, status='completed')['items'][0]['run_id']
    assert latest in results[0]['error']
    # Refused for the model only: no request row, no card, no job.
    assert not [d for e, d in frames if e in ('tool_request', 'tool_pending_confirmation', 'tool_error')]
    assert oj_grid.starts == []

    cards = []
    calls, results, frames, _ = run_journey(
        kind, model, base_url, auth, [lambda r: ('run_simulation', {'rerun_current': True}), lambda r: (None, {})],
        'Yes, solve again.', session=session, on_frame=_approve(cards))
    assert [c['tool_name'] for c in cards] == ['run_simulation']
    assert cards[0]['args'] == {'rerun_current': True}
    assert len(oj_grid.starts) == 1
    oj_grid.open.set()
    deadline = time.monotonic() + 120
    while chat_tools.list_project_runs(status='completed')['total_count'] < runs_before + 1:
        assert time.monotonic() < deadline, PyPSAService.get_solver_state().get('status')
        time.sleep(0.2)


def test_oj32_a_denied_rerun_card_starts_nothing(oj_grid):
    cards = []
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('run_simulation', {'rerun_current': True}), lambda r: (None, {})],
        'Solve it again anyway.', on_frame=_deny(cards))
    assert len(cards) == 1 and oj_grid.starts == []
    assert [d['error_kind'] for e, d in frames if e == 'tool_error'] == ['confirmation_denied']


def test_oj32_changed_inputs_need_no_flag(oj_grid):
    chat_tools.update_component('Line', 'L1', {'s_nom': 500})
    cards = []
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('run_simulation', {}), lambda r: (None, {})], 'Re-run it.',
        on_frame=_approve(cards))
    assert [c['tool_name'] for c in cards] == ['run_simulation'] and len(oj_grid.starts) == 1


def test_oj32_queueing_the_open_saved_project_is_asked_too(oj_grid):
    from services.solve_queue import solve_queue

    project_id = chat_tools.project_readiness()['project_id']
    enqueue = [lambda r: ('solve_queue_enqueue', {'project_id': project_id}), lambda r: (None, {})]
    # The solve filled in carriers the saved file lacks: its saved inputs are
    # not the solved ones, so the queue would solve something else. The card asks.
    cards = []
    calls, results, frames, _ = run_journey(*PROVIDERS[1], enqueue, 'Queue it again.', on_frame=_deny(cards))
    assert [c['tool_name'] for c in cards] == ['solve_queue_enqueue']
    # Saved, the queue would re-solve the current results: asked before any card.
    chat_tools.save_project(NAME)
    calls, results, frames, _ = run_journey(*PROVIDERS[1], enqueue, 'Queue it again.')
    assert 'results_current' in results[0]['error'] and not _cards(frames)
    assert solve_queue.list_jobs() == []


def test_oj32_a_current_load_flow_is_asked_about_too(oj_grid):
    from harness import run_lifecycle
    from services import dirty_state

    # The load flow ran on the current dispatch and nothing was edited since:
    # its results row reads fresh, so a re-run is asked about before any card.
    with PyPSAService.get_solver_state_lock():
        PyPSAService.get_solver_state().update(ac_pf_results={'converged': True},
                                               ac_pf_network_revision=dirty_state.revision())
    loadflow = next(e for e in chat_tools.project_readiness()['engines'] if e['kind'] == 'loadflow')
    assert loadflow['results_current'] is True
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('run_ac_pf_stage', {}), lambda r: (None, {})], 'Run the load flow again.')
    assert 'results_current' in results[0]['error'] and 'rerun_current: true' in results[0]['error']
    assert not _cards(frames) and oj_grid.starts == []
    assert run_lifecycle.check_rerun('run_ac_pf_stage', {'rerun_current': True}) is None
    # An edit makes the load flow stale: no question, the ordinary card decides.
    chat_tools.update_component('Line', 'L1', {'s_nom': 500})
    assert run_lifecycle.check_rerun('run_ac_pf_stage', {}) is None
    loadflow = next(e for e in chat_tools.project_readiness()['engines'] if e['kind'] == 'loadflow')
    assert loadflow['results_current'] is False


def test_oj32_a_plan_run_step_takes_the_flagged_rerun(oj_grid):
    """A plan's run step on current results is asked about too; the flagged
    call is still that step (the flag is no input), behind its own card."""
    cards = []
    plan = [{'id': 'run', 'tool': 'run_simulation', 'args': {}}]
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('start_task', {'title': 'Re-run', 'request_id': 'oj32', 'steps': plan}),
                        lambda r: ('run_simulation', {}),
                        lambda r: ('run_simulation', {'rerun_current': True}), lambda r: (None, {})],
        'Re-run it, yes even if nothing changed.', on_frame=_approve(cards))
    assert 'results_current' in results[1]['error']
    assert [c['tool_name'] for c in cards] == ['run_simulation'] and len(oj_grid.starts) == 1
    assert not [d for e, d in frames if e == 'tool_error']


# ── OJ-33 · Infeasible ───────────────────────────────────────────────────────

INFEASIBLE = 'oj-grid-infeasible'


def build_oj_grid_infeasible() -> pypsa.Network:
    """oj-grid with L2 and T1 gone and every supply moved to B1: B3 keeps the
    90 MW load and nothing can reach it, so the LP is infeasible."""
    n = build_oj_grid()
    n.remove('Line', 'L2')
    n.remove('Transformer', 'T1')
    for klass, name in (('Generator', 'Solar'), ('StorageUnit', 'Battery'), ('Store', 'H2 tank')):
        n.static(klass).at[name, 'bus'] = 'B1'
    assert n.loads.at['Demand', 'bus'] == 'B3' and n.loads_t.p_set.empty
    n.loads.at['Demand', 'p_set'] = LOAD_MW
    return n


@pytest.fixture
def oj_infeasible(client, session_ctx, install_network, monkeypatch):
    import routers.simulation as simulation

    from harness import continuation

    install_network(build_oj_grid_infeasible())
    assert client.post(f'/api/projects/{INFEASIBLE}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    gate = Gate()
    gate.hold_seconds = 1.0  # past the continuation grace, then the real HiGHS
    monkeypatch.setattr(continuation, 'GRACE_SECONDS', 0.2)
    monkeypatch.setattr(simulation, 'run_simulation', gate.wrap(simulation.run_simulation))
    try:
        yield gate
    finally:
        gate.open.set()
        _settle_solver()
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _triage_answer(results):
    log, diagnosis, validation = results
    island = diagnosis['islands_without_generation'][0]
    return (f"The solve was infeasible. Bus {island['buses'][0]} is an island: it has "
            f"{island['peak_load_mw']} MW of load and no generation (diagnose_network). "
            "Reconnect it with a line, or add generation there?")


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_oj33_an_infeasible_solve_is_triaged_and_never_rerun(oj_infeasible, kind, model, base_url, auth):
    cards = []
    plan = [{'id': 'run', 'tool': 'run_simulation', 'args': {}}]
    calls, results, frames, session = run_journey(
        kind, model, base_url, auth,
        [lambda r: ('start_task', {'title': 'Run it', 'request_id': 'oj33', 'steps': plan}),
         lambda r: ('run_simulation', {}), lambda r: (None, {})],
        'Run it.', on_frame=_approve(cards))
    assert calls == ['start_task', 'run_simulation', None]
    assert [c['tool_name'] for c in cards] == ['run_simulation']

    due = _await_due(session)
    assert [d['status'] for d in due] == ['failed']
    message = due[0]['message']
    assert 'triage' in message and 'diagnose_network' in message and 'validate_network' in message
    assert 'do not run it again' in message

    steps = [lambda r: ('get_simulation_log_history', {}), lambda r: ('diagnose_network', {}),
             lambda r: ('validate_network', {}), lambda r: (None, {})]
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, steps, '', session=session,
                                            continuation_job_id=due[0]['job_id'], answer=_triage_answer)
    assert calls == ['get_simulation_log_history', 'diagnose_network', 'validate_network', None]
    log = ' '.join(str(line) for line in results[0].get('lines', results[0].get('log', [])))
    assert 'nfeasib' in log
    # The cause is the seeded one, named by the native read, never guessed.
    island = results[1]['islands_without_generation']
    assert [i['buses'] for i in island] == [['B3']]
    text = ''.join(d.get('delta', '') for e, d in frames if e == 'token')
    assert 'Bus B3 is an island' in text
    # Nothing ran again and nothing was carded in the triage turn.
    assert not _cards(frames) and len(oj_infeasible.starts) == 1
    chat_tools.set_chat_session(session)
    task = chat_tools.get_task(due[0]['task_id'])
    assert task['next_step']['status'] == 'failed'


def test_oj33_wait_for_job_names_the_triage_of_a_failed_solve(oj_infeasible):
    oj_infeasible.hold_seconds = 0
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('run_simulation', {}),
                        lambda r: ('wait_for_job', {'job_id': r[0]['job_id'], 'timeout_seconds': 25}),
                        lambda r: (None, {})],
        'Run it.', on_frame=_approve([]))
    result = results[1]
    assert result['terminal'] and result['job']['status'] == 'failed', result
    assert [step['tool'] for step in result['triage']['read']] == [
        'get_simulation_log_history', 'diagnose_network', 'validate_network']
    assert 'Never run it again' in result['triage']['then']
    assert len(oj_infeasible.starts) == 1
