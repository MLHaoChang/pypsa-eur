"""LC-03 slice 2: a study or report run keeps its own past-run section.

A study run keeps `study_result`: what its status route served when it
finished, byte-equal and without the run-evidence token (GK B condition d). A
report run keeps `report_output`: ids and counts only (e). Both solve no
network, so a results read of such a run refuses `historical_scope_mismatch`
with the run's kind and the section it keeps; access loss refuses their
sections exactly as it refuses a solve's (f).
"""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import json

import pytest

from services import chat_tools, run_evidence
from services.pypsa_service import PyPSAService
from tests.oj_grid import NAME, build_oj_grid
from tests.test_retained_acl import _browser_get, _view, past_run  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def project(client, session_ctx, install_network):
    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        yield ctx
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _finish_study(ctx, key='mc', **result):
    record = {'status': 'running', 'result': None, 'error': None, 'started_at': 1.0,
              'network_revision': 0, 'thread': None, 'stop_event': None}
    ctx.solver_state[key] = record
    token = run_evidence.start_study_run(key, record)
    assert token is not None
    record.update(status='done', result=result or {'lole_h': 1.5, 'eens_mwh': 3.25})
    run_evidence.publish_study_run(key, record)
    return token['run_id']


def _finish_report(ctx):
    record = {'status': 'running', 'report_id': 'rep-1', 'version': None, 'mode': 'generate',
              'section': None, 'progress': {'done': 0, 'total': 3, 'current': None},
              'repairs': 0, 'prose_failures': [], 'error': None}
    assert run_evidence.start_report_run(record) is not None
    record.update(status='done', version=2, repairs=1,
                  progress={'done': 3, 'total': 3, 'current': None},
                  prose_failures=[{'section': 'intro', 'reason': 'the prose said X'}])
    run_evidence.publish_report_run(record)
    return record['_run_evidence']['run_id']


def test_a_study_run_keeps_its_status_route_payload_verbatim(project, client):
    run_id = _finish_study(project)
    kept = run_evidence.get_run_evidence(run_id, run_evidence.STUDY_SECTION)['data']
    served = client.get('/api/results/mc')
    assert served.status_code == 200, served.text
    assert json.dumps(kept, sort_keys=True) == json.dumps(served.json(), sort_keys=True)
    assert kept['result'] == {'lole_h': 1.5, 'eens_mwh': 3.25}
    assert '_run_evidence' not in kept and 'thread' not in kept
    view = client.get('/api/results/run-view',
                      params={'run_id': run_id, 'section': run_evidence.STUDY_SECTION})
    assert view.status_code == 200, view.text
    assert view.json()['data']['data'] == kept


def test_a_results_read_of_a_study_run_names_its_own_section(project):
    run_id = _finish_study(project)
    refused = chat_tools.get_results(result_kind='statistics', run_id=run_id)
    assert refused['status'] == 'unavailable'
    assert refused['unavailable_kind'] == 'historical_scope_mismatch'
    assert refused['run_kind'] == 'monte_carlo' and refused['kept_section'] == 'study_result'
    assert run_id in refused['message']


def test_an_oversized_study_result_is_not_kept_and_says_so(project):
    run_id = _finish_study(project, rows=['x' * 64] * 400)
    record = run_evidence.get_run_evidence(run_id)
    assert run_evidence.STUDY_SECTION not in {s['name'] for s in record['sections'] if s['available']}
    assert 'study_result_not_captured' in record['warnings']


def test_a_report_run_keeps_ids_and_counts_only(project):
    run_id = _finish_report(project)
    kept = run_evidence.get_run_evidence(run_id, run_evidence.REPORT_SECTION)['data']
    assert kept == {'report_id': 'rep-1', 'version': 2, 'mode': 'generate', 'section': None,
                    'sections_total': 3, 'sections_done': 3, 'repairs': 1, 'prose_failures': 1}
    assert 'the prose said X' not in json.dumps(kept)
    refused = chat_tools.get_results(result_kind='cost_breakdown', run_id=run_id)
    assert refused['unavailable_kind'] == 'historical_scope_mismatch'
    assert refused['run_kind'] == 'report' and refused['kept_section'] == 'report_output'


def test_a_solve_run_is_unchanged(project):
    """A solve's results read never takes the study/report refusal."""
    assert run_evidence.own_section('never-recorded-run') is None


def test_study_and_report_sections_refuse_like_a_solves_once_access_is_lost(past_run):
    browser = past_run['browser']
    ctx = PyPSAService.get_active_context()
    study_run, report_run = _finish_study(ctx), _finish_report(ctx)
    for run_id, section in ((study_run, 'study_result'), (report_run, 'report_output')):
        view = _browser_get(browser, '/api/results/run-view',
                            params={'run_id': run_id, 'section': section})
        assert view.status_code == 200, (section, view.text)
    solve_refused = None
    past_run['revoke']()
    solve_refused = _view(browser, past_run['run_id'])
    for run_id, section in ((study_run, 'study_result'), (report_run, 'report_output')):
        view = _browser_get(browser, '/api/results/run-view',
                            params={'run_id': run_id, 'section': section})
        assert (view.status_code, view.json()['detail']) == solve_refused, section
    # A run id never recorded refuses the same way: no existence oracle.
    assert _view(browser, 'never-recorded-run') == solve_refused
