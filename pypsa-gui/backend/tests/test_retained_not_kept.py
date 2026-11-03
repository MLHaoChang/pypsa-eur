"""LC-03 slice 2 (PR-N): a section that cannot be kept is stored as a typed
`not_kept` stub, {status: 'not_kept', reason, bytes}, and every past-run read
names that reason instead of the generic "ran outside a chat-started solve".

GK B (16) conditions: (h) old runs' reads and exports are byte-equal (pinned
below); (i) every reader treats a stub as absent-with-reason, never data;
(j) the live digest failure and the past-run read name the same reason from
one constant.
"""
from __future__ import annotations

import hashlib

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

import harness.confirm as harness_confirm
from services import chat_service, chat_tools, retained_results, run_evidence
from services.period_utils import MULTI_PERIOD_NOT_DIGESTED
from services.pypsa_service import PyPSAService
from tests.oj_grid import NAME, build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_tools import helpers_state  # noqa: F401

GENERATORS = 2000
GENERIC = 'ran outside a chat-started'


def _multi_period_oj_grid() -> pypsa.Network:
    n = build_oj_grid()
    n.investment_periods = [2030]
    return n


def _many_generators() -> pypsa.Network:
    """One bus, one load and 2,000 generators, each its own carrier."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2026-06-01', periods=2, freq='h'))
    n.add('Bus', 'B1', v_nom=110)
    n.add('Load', 'Demand', bus='B1', p_set=50.0)
    names = [f'G{i:04d}' for i in range(GENERATORS)]
    n.add('Carrier', [f'c{i:04d}' for i in range(GENERATORS)])
    n.add('Generator', names, bus='B1', p_nom=1.0,
          marginal_cost=[10.0 + i / 100 for i in range(GENERATORS)],
          carrier=[f'c{i:04d}' for i in range(GENERATORS)])
    return n


def _solved_twice(build, client, session_ctx, install_network, monkeypatch):
    install_network(build())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            older = _solve_once(session)
            latest = _solve_once(session)
        chat_tools.set_chat_session(None)
        yield {'older': older, 'latest': latest}
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.fixture
def multi_period(client, session_ctx, install_network, monkeypatch):
    yield from _solved_twice(_multi_period_oj_grid, client, session_ctx, install_network, monkeypatch)


@pytest.fixture
def many(client, session_ctx, install_network, monkeypatch):
    yield from _solved_twice(_many_generators, client, session_ctx, install_network, monkeypatch)


# ── RED: the past-run read names the stub's reason ─────────────────────────


@pytest.mark.parametrize('kind', ['prices', 'price_drivers', 'generators', 'storage_dispatch'])
def test_a_multi_period_past_run_refuses_with_its_reason(multi_period, kind):
    out = chat_tools.get_results(result_kind=kind, run_id=multi_period['older']['run_id'])
    assert out['status'] == 'unavailable' and 'data' not in out
    assert out['unavailable_kind'] == 'historical_results_unavailable'
    assert out['reason'] == MULTI_PERIOD_NOT_DIGESTED
    assert out['run_id'] == multi_period['older']['run_id']
    assert GENERIC not in out['message'] and 'multi-period' in out['message']


@pytest.mark.parametrize('kind', ['prices', 'generators'])
def test_the_live_and_past_reads_name_the_same_reason(multi_period, kind):
    """(j): the current run's live digest failure and an older run's kept stub."""
    live = chat_tools.get_results(result_kind=kind, run_id=multi_period['latest']['run_id'])
    past = chat_tools.get_results(result_kind=kind, run_id=multi_period['older']['run_id'])
    assert live['status'] == past['status'] == 'unavailable'
    assert live['reason'] == past['reason'] == MULTI_PERIOD_NOT_DIGESTED


def test_a_large_networks_past_summary_refuses_over_the_section_cap(many):
    run_id = many['older']['run_id']
    for kind in ('statistics', 'cost_breakdown'):
        out = chat_tools.get_results(result_kind=kind, run_id=run_id)
        assert out['status'] == 'unavailable' and 'data' not in out
        assert out['reason'] == run_evidence.OVER_SECTION_CAP
        assert GENERIC not in out['message'] and '16 KB' in out['message']
        assert out['section_bytes'] > run_evidence.MAX_SECTION_BYTES


# ── (i): every reader treats a stub as absent-with-reason ──────────────────


def test_the_stub_is_stored_typed_and_verifies(multi_period):
    run_id = multi_period['older']['run_id']
    root, project_id, _owner = run_evidence._authority()
    record = run_evidence._load_run(root, project_id, run_id)
    blob, reason = run_evidence._section_state(root, record, retained_results.PRICES_SECTION)
    assert reason is None
    import json
    assert json.loads(blob) == {'status': 'not_kept', 'reason': MULTI_PERIOD_NOT_DIGESTED,
                                'bytes': None}
    # The run's evidence still verifies: a stub is absent, not corrupt.
    assert run_evidence.get_run_evidence(run_id)['evidence_available'] is True
    assert run_evidence._checked_manifest(root, project_id, run_id)[1] == 'completed'


def test_the_run_view_listing_and_export_treat_a_stub_as_absent(multi_period, client):
    run_id = multi_period['older']['run_id']
    section = retained_results.PRICES_SECTION
    listing = {s['name']: s for s in run_evidence.get_run_evidence(run_id)['sections']}
    assert listing[section] == {'name': section, 'available': False, 'status': 'not_kept',
                                'reason': MULTI_PERIOD_NOT_DIGESTED}
    assert listing[retained_results.SUMMARY_SECTION] == {
        'name': retained_results.SUMMARY_SECTION, 'available': True}
    with pytest.raises(HTTPException) as exc:
        run_evidence.get_run_evidence(run_id, section)
    detail = exc.value.detail
    assert detail['error_kind'] == 'evidence_unavailable'
    assert detail['reason'] == MULTI_PERIOD_NOT_DIGESTED and detail['section_status'] == 'not_kept'
    assert section not in detail['available_sections']
    assert retained_results.SUMMARY_SECTION in detail['available_sections']
    with pytest.raises(HTTPException):
        run_evidence.export_bytes(run_id, section)
    view = client.get('/api/results/run-view', params={'run_id': run_id, 'section': section})
    assert view.status_code == 409 and view.json()['detail']['reason'] == MULTI_PERIOD_NOT_DIGESTED
    file = client.get('/api/results/run-evidence.json', params={'run_id': run_id, 'section': section})
    assert file.status_code == 409 and file.json()['detail']['reason'] == MULTI_PERIOD_NOT_DIGESTED
    with pytest.raises(HTTPException) as exc:
        chat_tools.export_run_evidence(run_id=run_id, section=section)
    assert exc.value.detail['reason'] == MULTI_PERIOD_NOT_DIGESTED


def test_a_checked_read_of_a_stub_is_not_data(multi_period):
    run_id = multi_period['older']['run_id']
    _pid, manifest, data, status = run_evidence.checked_run_section(
        run_id, retained_results.PRICES_SECTION)
    assert (manifest, data, status) == (None, None, 'not_kept')
    _pid, manifest, data, status = run_evidence.checked_run_section(
        run_id, retained_results.SUMMARY_SECTION)
    assert status == 'completed' and data is not None


def test_comparing_a_run_with_a_stub_reads_its_metrics(multi_period):
    """compare reads metrics, never section data; a stub does not block it."""
    run_id, base = multi_period['older']['run_id'], multi_period['latest']['run_id']
    out = run_evidence.compare_project_runs(run_id, base)
    assert out['comparable'] is True


# ── (h): old runs' reads and exports are byte-equal ────────────────────────

_OLD_SECTIONS = {
    'summary': {'solver_kind': 'lopf', 'condition': 'optimal'},
    'results_prices': {'status': 'ok', 'prices': {'overall': {'mean_price': 50.0}}},
}
# The sha256 of each export of this pre-stub run, taken on master before PR-N.
_PINNED = {
    None: 'b5a7da70ece2e7d6b95baf8e26600522f8f1dfbc162cef6159292d02ce8c6285',
    'results_prices': '8a7a8c56a5dd3607e3f45e40699d33985597a02356606c17151ac2bd734a5f53',
}


def test_an_old_runs_reads_and_exports_are_byte_equal(helpers_state, client, oj_grid_project):  # noqa: F811
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-oldformat1', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             started_at='2026-10-01T00:00:00+00:00',
                             finished_at='2026-10-01T00:01:00+00:00',
                             sections=_OLD_SECTIONS)
    for section, pin in _PINNED.items():
        blob, _name = run_evidence.export_bytes('fg-oldformat1', section)
        assert hashlib.sha256(blob).hexdigest() == pin, section
    listing = run_evidence.get_run_evidence('fg-oldformat1')['sections']
    assert listing == [{'name': 'results_prices', 'available': True},
                       {'name': 'summary', 'available': True}]


@pytest.fixture
def oj_grid_project(client, session_ctx, install_network):
    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        yield
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)
