"""LC-03: the Summary family is kept with its run (retained_results).

At completion the run's immutable manifest gets a `results_summary` section:
the Summary tab's two reads (get_results statistics and cost_breakdown) as
they answered while that run's results were current. get_results(run_id=…)
then answers an older run from it, never from the current results.

oj-grid is solved twice at Gas 50 EUR/MWh, then Gas is re-priced to 60 and
solved again. The dispatch is the same (tests/oj_grid.py: gas_t = 90 - 80 pu_t),
so the hand calculation below fixes both runs' operating cost.
"""
import pytest

from services import chat_service, chat_tools, result_views as rv, retained_results, run_evidence
from tests.oj_grid import HOURS, LOAD_MW, SOLAR_MW, SOLAR_PU
from tests.test_oj01_latest_loadflow_journey import _solve_once, oj_grid  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

GAS_MWH = sum(LOAD_MW - SOLAR_MW * pu for pu in SOLAR_PU)   # all solar used, gas the rest
assert len(SOLAR_PU) == HOURS


@pytest.fixture
def repriced(oj_grid, client, monkeypatch):  # noqa: F811 — the imported fixture
    import harness.confirm as harness_confirm

    response = client.put('/api/network/generators/Gas',
                          json={'name': 'Gas', 'bus': 'B1', 'marginal_cost': 60})
    assert response.status_code == 200, response.text
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        newest = _solve_once(session)
    return {**oj_grid, 'newest': newest}


def _gas(rows):
    return next(r for r in rows if r['level_0'] == 'Generator' and r['level_1'] == 'gas')


def test_an_older_run_answers_from_its_own_kept_results(repriced):
    current = chat_tools.get_results(result_kind='cost_breakdown')
    assert current['opex'] == pytest.approx(60 * GAS_MWH, rel=1e-6)
    old = chat_tools.get_results(result_kind='cost_breakdown', run_id=repriced['older']['run_id'])
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['run'] == {'run_id': repriced['older']['run_id'],
                          'finished_at': repriced['older']['finished_at'], 'is_current': False}
    assert old['run_is_current'] is False and old['inputs_changed_since_run'] is True
    assert old['data']['opex'] == pytest.approx(50 * GAS_MWH, rel=1e-6)    # not 60
    assert old['data']['total'] == pytest.approx(50 * GAS_MWH, rel=1e-6)
    stats = chat_tools.get_results(result_kind='statistics', run_id=repriced['older']['run_id'])
    assert _gas(stats['data'])['Supply'] == pytest.approx(GAS_MWH, rel=1e-6)
    assert _gas(stats['data'])['Operational Expenditure'] == pytest.approx(50 * GAS_MWH, rel=1e-6)
    # Today's freshness never rides a past run's answer.
    assert not set(old['data']) & {'inputs_changed_since_solve', 'results_current', 'freshness_note'}


def test_an_older_run_answers_its_capacity_from_its_own_kept_results(repriced):
    """Capacity family: the Capacity Expansion tab's by-carrier economics."""
    current = chat_tools.get_results(result_kind='economics_by_carrier')
    assert current['by_carrier']['gas']['opex_meur']['total'] == pytest.approx(60 * GAS_MWH / 1e6, rel=1e-6)
    run_id = repriced['older']['run_id']
    old = chat_tools.get_results(result_kind='economics_by_carrier', run_id=run_id)
    assert old['status'] == 'ok' and old['scope'] == 'historical'
    assert old['section'] == 'results_capacity' and old['run']['is_current'] is False
    gas = old['data']['by_carrier']['gas']
    assert gas['opex_meur']['total'] == pytest.approx(50 * GAS_MWH / 1e6, rel=1e-6)   # not 60
    assert gas['lcoe_eur_per_mwh']['total'] == pytest.approx(50, rel=1e-6)
    assert gas['dispatch_gwh']['total'] == pytest.approx(GAS_MWH / 1e3, rel=1e-6)
    # The tab's other read, cost_breakdown, is kept once, with Summary.
    assert chat_tools.get_results(result_kind='cost_breakdown', run_id=run_id)['section'] == 'results_summary'


def test_the_newest_run_is_read_as_current(repriced):
    latest = chat_tools.get_results(result_kind='cost_breakdown', run_id=repriced['newest']['run_id'])
    assert latest['scope'] == 'current' and latest['run_is_current'] is True
    assert latest['run']['is_current'] is True
    assert latest['data']['opex'] == pytest.approx(60 * GAS_MWH, rel=1e-6)


def test_the_kept_section_is_the_api_read_at_completion(repriced, client):
    """API/chat parity: the section is what the results API served for that run."""
    detail = run_evidence.get_run_evidence(repriced['newest']['run_id'])
    assert {'name': 'results_summary', 'available': True} in detail['sections']
    assert {'name': 'loadflow', 'available': True} in detail['sections']
    assert {'name': 'results_capacity', 'available': True} in detail['sections']
    for kind, section in retained_results.KEPT_KINDS.items():
        kept = run_evidence.get_run_evidence(repriced['newest']['run_id'], section)['data']
        assert kept[kind] == client.get(f'/api/results/{kind}').json()


def test_a_current_run_whose_results_are_not_current_answers_from_its_section(repriced, monkeypatch):
    from services import workflow_tools

    monkeypatch.setattr(workflow_tools, 'solve_freshness',
                        lambda ctx=None: {'inputs_changed_since_solve': False, 'results_current': False})
    out = chat_tools.get_results(result_kind='cost_breakdown', run_id=repriced['newest']['run_id'])
    assert out['scope'] == 'historical' and out['run']['is_current'] is False
    assert out['data']['opex'] == pytest.approx(60 * GAS_MWH, rel=1e-6)


def test_a_legacy_run_refuses_with_its_id(repriced):
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-legacy02', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'summary': {'solver_kind': 'lopf'}})
    out = chat_tools.get_results(result_kind='statistics', run_id='fg-legacy02')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-legacy02'
    assert out['unavailable_kind'] == 'historical_results_unavailable'
    assert 'data' not in out and out['run_is_current'] is False


def test_a_kind_no_family_keeps_refuses_for_a_run(repriced):
    out = chat_tools.get_results(result_kind='prices', run_id=repriced['older']['run_id'])
    assert out['status'] == 'unavailable' and 'data' not in out
    assert out['unavailable_kind'] == 'historical_scope_mismatch'
    assert out['kept_kinds'] == ['statistics', 'cost_breakdown', 'economics_by_carrier']


def test_a_capacity_run_without_its_section_refuses_with_its_id(repriced):
    root, project_id, owner = run_evidence._authority()
    run_evidence.publish_run(root, project_id, run_id='fg-legacy03', kind='simulation',
                             status='completed', fingerprint='0' * 64, actor_id=owner,
                             sections={'results_summary': {'statistics': [], 'cost_breakdown': {}}})
    out = chat_tools.get_results(result_kind='economics_by_carrier', run_id='fg-legacy03')
    assert out['status'] == 'unavailable' and out['run_id'] == 'fg-legacy03'
    assert out['unavailable_kind'] == 'historical_capacity_unavailable'
    assert 'data' not in out


def test_a_corrupt_section_refuses_with_the_run_id(repriced):
    root, _project_id, _owner = run_evidence._authority()
    run_id = repriced['older']['run_id']
    path = run_evidence._evidence_path(root, run_id, 'sections', 'results_summary.json')
    path.write_text(path.read_text().replace('"gas"', '"oil"'))   # hash no longer matches
    out = chat_tools.get_results(result_kind='statistics', run_id=run_id)
    assert out['status'] == 'unavailable' and out['run_id'] == run_id
    assert out['reason'] == 'artifact_corrupt' and 'data' not in out


def test_the_registry_declares_every_family_it_keeps():
    assert ({v.historical_section for v in rv.RESULT_VIEWS if v.historical_section}
            == {a.section for a in retained_results.ADAPTERS})


def test_every_kept_kind_has_one_family_and_every_view_read_is_kept():
    kinds = [k for a in retained_results.ADAPTERS for k in a.kinds]
    assert len(kinds) == len(set(kinds)) == len(retained_results.KEPT_KINDS)
    for v in rv.RESULT_VIEWS:
        if v.historical_section and v.historical_section != 'loadflow':
            read_kinds = {r.args['result_kind'] for r in v.reads if r.tool == 'get_results'}
            assert read_kinds <= set(retained_results.KEPT_KINDS), v.view_id


def test_results_that_are_not_current_are_not_kept(oj_grid, monkeypatch):  # noqa: F811
    """A family that cannot vouch for its run stores nothing and says so; the
    other families keep theirs."""
    import harness.confirm as harness_confirm
    from services import workflow_tools

    monkeypatch.setattr(workflow_tools, 'solve_freshness',
                        lambda ctx=None: {'inputs_changed_since_solve': None, 'results_current': False})
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        run = _solve_once(session)
    detail = run_evidence.get_run_evidence(run['run_id'])
    names = {s['name'] for s in detail['sections']}
    assert not names & {'results_summary', 'results_capacity'} and 'loadflow' in names
    assert {'results_summary_not_captured', 'results_capacity_not_captured'} <= set(detail['warnings'])
    assert 'loadflow_not_captured' not in detail['warnings']


def test_an_oversized_section_is_dropped_not_the_manifest(oj_grid, monkeypatch):  # noqa: F811
    import harness.confirm as harness_confirm

    monkeypatch.setattr(run_evidence, 'MAX_SECTION_BYTES', 1024)   # the solver summary fits, the families do not
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        run = _solve_once(session)
    detail = run_evidence.get_run_evidence(run['run_id'])
    assert detail['status'] == 'completed'
    assert {'results_summary_not_captured', 'loadflow_not_captured'} <= set(detail['warnings'])


@pytest.mark.parametrize('run_id', ['', '../x', 'a' * 65, 7])
def test_a_malformed_run_id_is_refused(run_id):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        chat_tools.get_results(result_kind='statistics', run_id=run_id)
    assert exc.value.status_code == 422
    assert exc.value.detail['error_kind'] == 'invalid_results_argument'
