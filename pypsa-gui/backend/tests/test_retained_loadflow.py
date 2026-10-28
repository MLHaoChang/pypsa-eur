"""LC-03 PR-C: the load flow of a past run is kept with that run.

At completion the run's immutable manifest gets a bounded `loadflow` section:
the same oracle get_loadflow_summary computes, captured while that run's
results are the current ones. get_loadflow_summary(run_id) then answers for
an older run from its section, never from the current results; a legacy run
without the section refuses with its exact run ID.

oj-grid is solved twice (L1 at 300 MVA, 15 %), then L1 is re-rated to
150 MVA and solved again (30 %), so the retained and current values differ
by the hand calculation in tests/oj_grid.py.
"""
import pytest

from services import chat_service, chat_tools, result_views as rv, run_evidence
from tests.oj_grid import ORACLE
from tests.test_oj01_latest_loadflow_journey import _solve_once, oj_grid  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def rerated(oj_grid, client, monkeypatch):  # noqa: F811 — the imported fixture
    import harness.confirm as harness_confirm

    response = client.put('/api/network/lines/L1', json={'name': 'L1', 'bus0': 'B1', 'bus1': 'B2', 's_nom': 150})
    assert response.status_code == 200, response.text
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        newest = _solve_once(session)
    return {**oj_grid, 'newest': newest}


def _util(summary):
    return {row['name']: row['util_pct'] for row in summary['top']}


def test_an_older_run_answers_from_its_own_retained_section(rerated):
    current = chat_tools.get_loadflow_summary()
    assert _util(current)['L1'] == pytest.approx(30.0, abs=0.01)
    old = chat_tools.get_loadflow_summary(run_id=rerated['older']['run_id'])
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['run'] == {'run_id': rerated['older']['run_id'],
                          'finished_at': rerated['older']['finished_at'], 'is_current': False}
    assert old['run_is_current'] is False and old['inputs_changed_since_run'] is True
    assert _util(old) == pytest.approx(ORACLE['util_pct'], abs=0.01)   # L1 15 %, not 30 %
    assert [row['name'] for row in old['top']] == ORACLE['ranking']
    # The Load flow tab shows the current results, so nothing points the model at it.
    assert 'ui_reference' not in old and 'evidence_token' not in old
    assert old['claims']['full_ac_thermal_compliance'] is False


def test_the_newest_run_is_still_read_as_current(rerated):
    latest = chat_tools.get_loadflow_summary(run_id=rerated['newest']['run_id'])
    assert latest['scope'] == 'current' and latest['run_is_current'] is True
    assert _util(latest)['L1'] == pytest.approx(30.0, abs=0.01)


def test_the_section_is_in_the_immutable_manifest(rerated):
    detail = run_evidence.get_run_evidence(rerated['older']['run_id'])
    assert {'name': 'loadflow', 'available': True} in detail['sections']
    section = run_evidence.get_run_evidence(rerated['older']['run_id'], 'loadflow')
    assert section['data']['status'] == 'ok' and section['data']['scope'] == 'current'


def test_a_legacy_run_without_the_section_refuses_with_its_id(rerated):
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-legacy01', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'summary': {'solver_kind': 'lopf'}})
    out = chat_tools.get_loadflow_summary(run_id='fg-legacy01')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-legacy01'
    assert out['unavailable_kind'] == 'historical_loadflow_unavailable'
    assert 'top' not in out and out['run_is_current'] is False


def test_the_registry_declares_the_retained_section():
    assert rv.view('loadflow').historical_section == 'loadflow'
    assert {v.view_id for v in rv.RESULT_VIEWS if v.historical_section} == {'loadflow', 'summary', 'capex', 'dispatch', 'prices'}


def test_a_past_run_is_not_rewindowed_or_resourced(rerated):
    """The section holds one source and window; another request refuses."""
    run_id = rerated['older']['run_id']
    windowed = chat_tools.get_loadflow_summary(run_id=run_id, window_from=0, window_to=5)
    assert windowed['status'] == 'unavailable' and 'top' not in windowed
    assert windowed['unavailable_kind'] == 'historical_scope_mismatch'
    assert windowed['captured'] == {'source': 'lopf', 'window_from': 0, 'window_to': 23}
    acpf = chat_tools.get_loadflow_summary(run_id=run_id, source='ac_pf')
    assert acpf['unavailable_kind'] == 'historical_scope_mismatch'
    one = chat_tools.get_loadflow_summary(run_id=run_id, limit=1)
    assert [row['name'] for row in one['top']] == ['T1'] and one['top_truncated'] is True


def test_corrupt_or_unfinished_sections_refuse_with_the_run_id(rerated):
    root, project_id, owner = run_evidence._authority()
    run_id = rerated['older']['run_id']
    path = run_evidence._evidence_path(root, run_id, 'sections', 'loadflow.json')
    path.write_text(path.read_text().replace('"T1"', '"TX"'))   # hash no longer matches
    out = chat_tools.get_loadflow_summary(run_id=run_id)
    assert out['status'] == 'unavailable' and out['run_id'] == run_id and 'top' not in out
    assert out['reason'] == 'artifact_corrupt'
    run_evidence.start_run(root, project_id, run_id='fg-running01', kind='simulation',
                           actor_id=owner)
    out = chat_tools.get_loadflow_summary(run_id='fg-running01')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-running01'
    assert out['reason'] == 'run_not_finished'


def test_a_failed_capture_is_recorded_not_silent(oj_grid, monkeypatch):  # noqa: F811
    import harness.confirm as harness_confirm
    from services import loadflow_summary

    def boom(*a, **k):
        raise RuntimeError('capture failed')

    monkeypatch.setattr(loadflow_summary, 'get_loadflow_summary', boom)
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        run = _solve_once(session)
    detail = run_evidence.get_run_evidence(run['run_id'])
    assert 'loadflow' not in {s['name'] for s in detail['sections']}
    assert 'loadflow_not_captured' in detail['warnings']
