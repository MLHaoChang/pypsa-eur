"""A study or report record's run-evidence token never reaches the wire.

Each study and report worker record carries `_run_evidence`, the token
`run_evidence` uses to publish the run when the worker finishes. The token
holds the server's evidence root path. The status routes served the record
minus only the thread handle and stop event, so they disclosed that path.
`run_evidence.wire_record` is the one filter: every surface that serves these
records drops the token, and the publisher still reads it from the record.
No frontend code reads `_run_evidence`.
"""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import json

import pytest

from routers.report_jobs import JOB_KEY
from services import run_evidence
from services.pypsa_service import PyPSAService

ROOT_MARK = 'evidence-root-must-not-leak'

STUDY_ROUTES = {
    'fmea_sweep': '/api/results/fmea_sweep',
    'frontier': '/api/results/frontier',
    'mc': '/api/results/mc',
    'coupling_loop': '/api/results/coupling_loop',
    'margin_loop': '/api/results/margin_loop',
    'eh_study': '/api/results/eh_study',
    'investment_case': '/api/results/investment_case',
}


def _record(tmp_path, **extra):
    root = tmp_path / ROOT_MARK
    return {'status': 'done', 'started_at': 1.0, 'network_revision': 0, 'result': {'ok': 1},
            'iterations': [], 'error': None,
            '_run_evidence': {'root': root, 'project_id': 'p', 'run_id': 's-x', 'kind': 'k',
                              'started_at': 'now', 'actor_id': None},
            **extra}


@pytest.fixture
def active(client, session_ctx):
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    try:
        yield ctx
    finally:
        PyPSAService.reset_request_context(binding)


@pytest.mark.parametrize('key', list(STUDY_ROUTES))
def test_a_study_status_route_never_serves_the_evidence_token(client, active, tmp_path, key):
    record = _record(tmp_path)
    active.solver_state[key] = record
    try:
        response = client.get(STUDY_ROUTES[key])
    finally:
        active.solver_state[key] = None
    assert response.status_code == 200, response.text
    assert '_run_evidence' not in response.json()
    assert ROOT_MARK not in response.text
    # The record itself keeps the token: the publisher reads it there.
    assert record['_run_evidence']['run_id'] == 's-x'


def test_the_report_status_route_never_serves_the_evidence_token(client, session_ctx, tmp_path):
    from tests.conftest import install_network_into_backend
    from tests.oj_grid import NAME, build_oj_grid

    install_network_into_backend(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    ctx.solver_state[JOB_KEY] = _record(tmp_path, report_id='r1', mode='generate', version=1)
    try:
        response = client.get(f'/api/projects/{NAME}/reports/generate/status')
    finally:
        ctx.solver_state[JOB_KEY] = None
    assert response.status_code == 200, response.text
    assert '_run_evidence' not in response.json() and ROOT_MARK not in response.text


def test_wire_record_drops_only_the_process_handles():
    record = {'status': 'done', 'thread': object(), 'stop_event': object(),
              '_run_evidence': {'root': '/x'}, 'result': {'a': 1}, '_other': 2}
    assert run_evidence.wire_record(record) == {'status': 'done', 'result': {'a': 1}, '_other': 2}
    assert run_evidence.wire_record(None) is None
    assert set(record) == {'status', 'thread', 'stop_event', '_run_evidence', 'result', '_other'}


def test_the_study_publisher_still_reads_the_token(tmp_path):
    record = {'status': 'done', '_run_evidence': {
        'root': tmp_path, 'project_id': 'p-wire', 'run_id': run_evidence.study_run_id('mc'),
        'kind': 'monte_carlo', 'started_at': '2026-11-03T00:00:00+00:00', 'actor_id': None}}
    run_evidence.start_run(tmp_path, 'p-wire', run_id=record['_run_evidence']['run_id'],
                           kind='monte_carlo', started_at=record['_run_evidence']['started_at'],
                           mode='mc', job_id=record['_run_evidence']['run_id'], actor_id=None)
    assert json.dumps(run_evidence.wire_record(record)) == '{"status": "done"}'
    published = run_evidence.publish_study_run('mc', record)
    assert published is not None and published['status'] == 'completed', published
