"""Native screening journeys. Strict refusal is unsupported coverage, NOT admitted facts."""
import pytest

from services import chat_tools
from tests.journey_wire import run_journey
from tests.oj_grid import build_oj_grid
from tests.oj_grid_bad import build_oj_grid_bad, loss_capture
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def seeded(client, install_network, session_ctx, bound_ctx):
    n = build_oj_grid_bad()
    install_network(n)
    assert client.post('/api/projects/oj-grid-bad').status_code == 200
    ctx = session_ctx(client)
    ctx.solver_state.update(status='completed', condition='optimal', last_lost_load=loss_capture(ctx.network))
    with bound_ctx(client):
        yield ctx


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[p[0] for p in PROVIDERS])
def test_unchecked_oj19_returns_exactly_five_native_findings(seeded, kind, model, base_url, auth):
    calls, results, frames, _ = run_journey(kind, model, base_url, auth,
        [lambda r: ('get_results', {'result_kind': 'plausibility'}), lambda r: (None, {})],
        'Do these results make sense?', answer=lambda r: 'Native screening: ' + ' '.join(f['explanation'] for f in r[0]['findings']))
    assert calls == ['get_results', None]
    got = results[0]
    assert [f['rule_id'] for f in got['findings']] == ['lost_load', 'branch_overload', 'curtailment_share', 'storage_simultaneous', 'build_at_bound']
    assert [f['severity'] for f in got['findings']] == ['error', 'error', 'warning', 'warning', 'warning']
    assert all(f['facts'] and f['threshold'] and f['basis'] for f in got['findings'])
    assert got['findings'][0]['facts'][0]['value'] == 12.
    assert got['findings'][1]['facts'][0]['value'] == 1.6
    assert got['findings'][2]['facts'][0]['value'] == 0.6
    assert not [d for e, d in frames if e in {'error', 'tool_error', 'answer_approved'}]


def test_oj18_clean_real_highs_solve_reports_coverage(client, install_network, session_ctx):
    n = build_oj_grid()
    status, condition = n.optimize(solver_name='highs', solver_options={'threads': 1, 'log_to_console': False})
    assert condition == 'optimal'
    install_network(n)
    assert client.post('/api/projects/oj-grid').status_code == 200
    ctx = session_ctx(client)
    ctx.solver_state.update(status='completed', condition='optimal', last_lost_load=None)
    res = client.get('/api/results/plausibility')
    assert res.status_code == 200, res.text
    assert res.json()['findings'] == []
    assert res.json()['coverage']['rules']['lost_load']['not_evaluated']


def test_checked_turn_refuses_plausibility_read_as_unsupported(seeded, client, monkeypatch):
    """Unsupported coverage: no new facts are admitted or certified here."""
    from tests.assurance_acceptance.support import Harness, approval, public_text, text_turn, tool_turn
    # Positive native-read control: do not confuse an absent feature with a strict refusal.
    assert len(client.get('/api/results/plausibility').json()['findings']) == 5
    calls = []
    original = chat_tools.DISPATCHERS['get_results']
    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'get_results', spy)
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'strict')
    harness = Harness(client, monkeypatch)
    try:
        frames = harness.run([tool_turn('get_results', {'result_kind': 'plausibility'}), text_turn('Lost load is 12 MWh.'), text_turn('Lost load is 12 MWh.')])
        assert calls == []
        assert '12 MWh' not in public_text(frames)
        assert approval(frames)['verdict'] == 'limited'
    finally:
        from services import chat_service
        for sid in harness.session_ids:
            chat_service.drop_session(sid)


def test_unsolved_and_running_reads_refuse_without_all_pass(seeded, client):
    seeded.network._objective = None
    res = client.get('/api/results/plausibility')
    assert res.status_code == 409 and res.json()['detail']['error_kind'] == 'plausibility_results_unavailable'
    seeded.network._objective = 1.
    seeded.solver_state['status'] = 'running'
    res = client.get('/api/results/plausibility')
    assert res.status_code == 409 and 'findings' not in res.json()


def test_historical_scope_and_loaded_provenance_are_explicit(seeded, client):
    got = chat_tools.get_results('plausibility', run_id='past-run')
    assert got['status'] == 'unavailable' and got['unavailable_kind'] == 'historical_scope_mismatch'
    res = client.get('/api/results/plausibility')
    assert res.status_code == 200
    assert res.json()['results_current'] is False
    assert res.json()['inputs_changed_since_solve'] is None


def test_other_organisation_cannot_read_the_seeded_results(seeded, other_org_client):
    res = other_org_client.get('/api/results/plausibility')
    assert res.status_code in (403, 404, 409)
    assert 'findings' not in res.json()


def test_read_preserves_network_series_and_undo_history(seeded, client):
    import pandas as pd
    before = seeded.network.generators.copy(deep=True)
    dispatch = seeded.network.generators_t.p.copy(deep=True)
    history = client.get('/api/network/undo/info').json()
    assert client.get('/api/results/plausibility').status_code == 200
    pd.testing.assert_frame_equal(before, seeded.network.generators)
    pd.testing.assert_frame_equal(dispatch, seeded.network.generators_t.p)
    assert client.get('/api/network/undo/info').json() == history


def test_native_ens_report_is_the_cap_source_and_pf_is_not_relabelled(seeded, client):
    seeded.solver_state['adequacy_report'] = {'target': {'energy_target_set': True, 'system': {'by_period': [{'period': '2026', 'cap_mwh': 20.}]}}}
    got = client.get('/api/results/plausibility').json()
    assert got['findings'][1]['rule_id'] == 'lost_load'  # branch error ranks before within-cap loss warning
    assert got['findings'][1]['severity'] == 'warning'
    assert got['findings'][1]['basis'] == 'services.adequacy.report.build_adequacy_report:target.system.by_period'
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as blocked:
        chat_tools.get_results('plausibility', source='ac_pf')
    assert blocked.value.status_code == 409
    assert 'LOPF' in blocked.value.detail['message']


def test_existing_results_tool_does_not_reintroduce_a_removed_stub():
    import ast
    from pathlib import Path
    from harness.catalogue import RESULTS_ENUM, TOOLS
    assert 'plausibility' in RESULTS_ENUM
    tree = ast.parse((Path(__file__).parents[1] / 'smoke/qa_e2e.py').read_text())
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'removed' for t in node.targets)]
    assert len(assignments) == 1
    assert not ast.literal_eval(assignments[0].value) & {t['name'] for t in TOOLS}
