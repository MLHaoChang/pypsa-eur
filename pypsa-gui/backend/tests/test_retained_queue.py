"""LC-03 slice 2: a solve-queue run keeps its own past-run sections.

The queue solves the open project in its resident context and records the
solve's input fingerprint, so at completion its results are the current ones
and the same capture as a chat-started solve applies. Hand oracle: oj-grid's
gas plant at 70 EUR/MWh (queued), then 60 (foreground); the queued run keeps
LCOE 70 and opex 70 x sum(90 - 80 pu_t), never the later 60.
"""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import copy
import types
import uuid

import pytest

import harness.confirm as harness_confirm
from services import chat_service, chat_tools, run_evidence, solve_queue as sq
from services.pypsa_service import PyPSAService
from services.workflow_tools import solve_freshness
from tests.oj_grid import NAME
from tests.test_oj01_latest_loadflow_journey import _solve_once, oj_grid  # noqa: F401
from tests.test_retained_summary import GAS_MWH
from tests.test_workflow_tools import helpers_state  # noqa: F401
from services.workflow_tools import wait_for_job


def _price_gas(client, cost):
    response = client.put('/api/network/generators/Gas',
                          json={'name': 'Gas', 'bus': 'B1', 'marginal_cost': cost})
    assert response.status_code == 200, response.text


@pytest.fixture
def queued(oj_grid, client, monkeypatch):
    _price_gas(client, 70)
    job = chat_tools.solve_queue_enqueue(NAME)
    job_id = str(job.get('id') or job.get('job_id'))
    waited = {}
    for _ in range(5):
        waited = wait_for_job(job_id, 25)
        if waited.get('completed'):
            break
    assert waited.get('completed'), (job, waited)
    run_id = run_evidence.queue_run_id(job_id)
    queued_job = sq.solve_queue._jobs[uuid.UUID(job_id)]
    # The queue's solved context at 70, held apart: a later job publishing from
    # it while the foreground holds another solve (GK B condition a).
    live = PyPSAService.get_active_context()
    solved_apart = copy.copy(live)
    solved_apart.network = copy.deepcopy(live.network)
    solved_apart.solver_state = dict(live.solver_state)
    _price_gas(client, 60)
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        newest = _solve_once(session)
    chat_tools.set_chat_session(None)
    yield {'queue_run': run_id, 'newest': newest, 'job': queued_job, 'solved_apart': solved_apart}
    sq.solve_queue.reset_for_tests()


def test_a_queued_run_answers_from_its_own_kept_results(queued):
    current = chat_tools.get_results(result_kind='economics_by_carrier')
    assert current['by_carrier']['gas']['lcoe_eur_per_mwh']['total'] == pytest.approx(60, rel=1e-6)
    old = chat_tools.get_results(result_kind='economics_by_carrier', run_id=queued['queue_run'])
    assert old['status'] == 'ok', old
    assert old['scope'] == 'historical' and old['section'] == 'results_capacity'
    gas = old['data']['by_carrier']['gas']
    assert gas['lcoe_eur_per_mwh']['total'] == pytest.approx(70, rel=1e-6)   # not 60
    assert gas['opex_meur']['total'] == pytest.approx(70 * GAS_MWH / 1e6, rel=1e-6)
    summary = chat_tools.get_results(result_kind='cost_breakdown', run_id=queued['queue_run'])
    assert summary['data']['opex'] == pytest.approx(70 * GAS_MWH, rel=1e-6)


def test_a_queued_run_records_its_solved_inputs(queued):
    record = run_evidence.get_run_evidence(queued['queue_run'])
    assert record['kind'] == 'solve_queue' and record['status'] == 'completed'
    assert isinstance(record.get('fingerprint'), str)
    assert not [w for w in record.get('warnings') or [] if w.endswith('_not_captured')], record


def _job_like(queued):
    """A second queue job of the same project, claimed and about to publish."""
    real = queued['job']
    job = types.SimpleNamespace(
        id=uuid.uuid4(), kind=real.kind, project_id=real.project_id,
        project_key=getattr(real, 'project_key', None), storage_dir=real.storage_dir,
        enqueued_by_user_id=real.enqueued_by_user_id, objective=real.objective,
        solve_time=real.solve_time, condition='optimal', error=None, status='running')
    assert run_evidence.start_queue_run(job) is not None
    return job


def test_the_capture_reads_the_jobs_own_context_never_the_foreground(queued):
    """GK B (a): a job publishing from its own context keeps that context's
    results; the foreground network and its freshness are left as they were."""
    foreground = PyPSAService.get_active_context()
    before = (foreground.network, dict(foreground.network.meta), solve_freshness())
    job = _job_like(queued)
    run_evidence.publish_queue_run(job, status='completed', ctx=queued['solved_apart'])
    run_id = run_evidence.queue_run_id(str(job.id))
    old = chat_tools.get_results(result_kind='economics_by_carrier', run_id=run_id)
    assert old['status'] == 'ok', old
    assert old['data']['by_carrier']['gas']['lcoe_eur_per_mwh']['total'] == pytest.approx(70, rel=1e-6)
    after = PyPSAService.get_active_context()
    assert after is foreground and after.network is before[0]
    assert dict(after.network.meta) == before[1] and solve_freshness() == before[2]
    current = chat_tools.get_results(result_kind='economics_by_carrier')
    assert current['by_carrier']['gas']['lcoe_eur_per_mwh']['total'] == pytest.approx(60, rel=1e-6)


@pytest.mark.parametrize('status', ['failed', 'aborted'])
def test_a_job_that_did_not_complete_keeps_nothing(queued, status):
    """GK B (c): failed, aborted, disowned and superseded jobs (the queue
    publishes those as failed or aborted) capture no section."""
    job = _job_like(queued)
    run_evidence.publish_queue_run(job, status=status, ctx=queued['solved_apart'])
    record = run_evidence.get_run_evidence(run_evidence.queue_run_id(str(job.id)))
    assert record['status'] != 'completed'
    assert not set(record.get('sections') or {}) - {'summary'}, record.get('sections')
    assert record.get('fingerprint') is None
