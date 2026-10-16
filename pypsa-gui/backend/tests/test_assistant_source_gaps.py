"""Published checked gaps survive as read-only observations, never evidence."""
# ruff: noqa: F811 -- shared pytest fixtures.
import json
import os
import subprocess
import sys

import pytest

from harness import attention, session as sessions
from tests.test_assistant_decisions import origin, install, inspect  # noqa: F401
from tests.assurance_acceptance.support import approval, parse_sse
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_conversation_scope import conversation  # noqa: F401


def publish(client, sid, monkeypatch, message='Show key results for generator G1.', **options):
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'strict')
    install(monkeypatch, [{'blocks': [{'type': 'text', 'text': '{"schema_version":1,"blocks":[]}'}]}])
    response = client.post('/api/chat/stream', json={'session_id': sid, 'message': message, **options})
    assert response.status_code == 200, response.text
    return approval(parse_sse(response.text))


def gap_row(client):
    return next(r for r in client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items']
                if r['family'] == 'source_gap')


def test_native_published_gap_survives_process_loss_without_values_or_restored_authority(client, other_org_client, origin, monkeypatch):
    ctx, owner, sid, app = origin
    capture = attention.checked_answer
    def duplicate(native, project):
        capture(native, project)
        capture(native, project)
    monkeypatch.setattr(attention, 'checked_answer', duplicate)
    published = publish(client, sid, monkeypatch)
    row = gap_row(client)
    expected = [{k: s[k] for k in ('metric', 'disposition', 'reason')}
                for s in published['receipt']['result_slots'] if s['disposition'] != 'answered']
    assert expected and row['needs_you'] and row['status'] == 'needs_review'
    assert inspect(other_org_client, row).status_code == 404
    detail = inspect(client, row).json()
    gap = detail['source_gap']
    assert gap['answer_id'] == published['answer_id'] and gap['turn_id'] == published['turn_id']
    assert gap['slots'] == expected and gap['request_scope']['entity_id'] == 'G1'
    assert detail['evidence_available'] is False and detail['inspection_only'] is True
    assert detail['live_approval_available'] is False
    assert detail['session_id'] == sid and detail['project_id'] == str(ctx.project_uuid)
    assert gap_row(client) == row
    sessions.drop_session(sid)
    code = """
import json, sys
from harness import attention, session
rows, warnings = attention.project_records(sys.argv[1], sys.argv[2], [])
assert session.get_session(sys.argv[3]) is None
print(json.dumps([rows, warnings]))
"""
    recovered = subprocess.run([sys.executable, '-c', code, owner, str(ctx.project_uuid), sid],
        env=os.environ.copy(), capture_output=True, text=True, check=True, timeout=30)
    records, warnings = json.loads(recovered.stdout)
    assert records[0]['source_gap'] == gap and not warnings
    stored = list(app.rglob('decisions.json'))[0].read_text()
    assert '"value"' not in stored and '"text"' not in stored and '"facts"' not in stored
    assert inspect(client, row).status_code == 200
    assert sessions.get_session(sid) is None
    assert client.post('/api/projects/Other gap project', params={'force': True, 'rebind': True}).status_code == 200
    assert inspect(client, row).status_code == 200
    assert client.get('/api/chat/activity').json()['items'] == []


def test_later_clarification_and_unrelated_turn_never_resolve_prior_gaps(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch, 'Show cost for generator G1.')
    old = gap_row(client)
    detail = inspect(client, old).json()
    assert all(s['reason'] == 'cost_quantity_required' for s in detail['source_gap']['slots'])
    publish(client, sid, monkeypatch, 'Total declared-basis cost')
    assert inspect(client, old).status_code == 200
    rows = client.get('/api/chat/activity', params={'filter': 'needs_you'}).json()['items']
    assert len([r for r in rows if r['family'] == 'source_gap']) == 2
    assert not any(r['family'] == 'source_gap' for r in client.get('/api/chat/activity', params={'filter': 'completed'}).json()['items'])


def test_journal_failure_cannot_suppress_checked_publication(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    from harness import owned_snapshots
    def fail(*args, **kwargs):
        raise OSError('private journal failure')
    monkeypatch.setattr(owned_snapshots, 'write', fail)
    published = publish(client, sid, monkeypatch)
    assert published['receipt']['request_complete'] is False
    activity = client.get('/api/chat/activity')
    assert 'decision_history_unavailable' in activity.json()['warnings']
    assert 'private journal failure' not in activity.text


@pytest.mark.parametrize('fault', ['value', 'scope_extra', 'answer', 'turn', 'state', 'duplicate_metric'])
def test_corrupt_gap_is_unavailable_never_repaired(client, origin, monkeypatch, fault):
    _ctx, _owner, sid, app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    path = list(app.rglob('decisions.json'))[0]
    data = json.loads(path.read_text())
    record = next(r for r in data['records'] if r['family'] == 'source_gap')
    gap = record['source_gap']
    if fault == 'value':
        gap['slots'][0]['value'] = 999
    elif fault == 'scope_extra':
        gap['request_scope']['raw_data'] = 'must not import'
    elif fault == 'answer':
        gap['answer_id'] = row['source_id']
    elif fault == 'turn':
        record['tool_use_id'] = 'wrong-turn'
    elif fault == 'state':
        record['native_status'] = 'approved'
    else:
        gap['slots'].append(gap['slots'][0])
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert inspect(client, row).status_code == 404
    assert 'decision_history_unavailable' in client.get('/api/chat/activity').json()['warnings']
    publish(client, sid, monkeypatch)
    assert path.read_bytes() == before


def test_fully_checked_answer_does_not_clear_previous_gap_or_create_one(solved, conversation, client, monkeypatch, tmp_path):
    monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(tmp_path / 'gap-app'))
    first, _ = conversation('Show cost for generator G1.')
    assert approval(first)['receipt']['request_complete'] is False
    row = gap_row(client)
    full, _ = conversation('Give me the key results for generator G1.', {
        'dispatch_energy': 17, 'marginal_expenditure': 170, 'peak_dispatch': 7,
    })
    assert approval(full)['receipt']['request_complete'] is True
    assert inspect(client, row).status_code == 200
    gaps = [r for r in client.get('/api/chat/activity').json()['items'] if r['family'] == 'source_gap']
    assert len(gaps) == 1 and gaps[0]['decision_id'] == row['decision_id']


def test_partial_checked_answer_retains_only_missing_slots_and_exact_scope(solved, conversation, client, monkeypatch, tmp_path):
    monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(tmp_path / 'gap-app'))
    # Finite dispatch is still available; only raw marginal cost is unavailable.
    solved.network.generators.loc['G1', 'marginal_cost'] = float('nan')
    from services import workflow_tools
    workflow_tools._record_solve_inputs(solved.network, solved.solver_state['solver_config'])
    frames, _ = conversation('Give me the key results for generator G1.', {
        'dispatch_energy': 17, 'peak_dispatch': 7,
    })
    published = approval(frames)
    slots = published['receipt']['result_slots']
    assert any(s['disposition'] == 'answered' for s in slots)
    gap = inspect(client, gap_row(client)).json()['source_gap']
    assert [s['metric'] for s in gap['slots']] == ['marginal_expenditure']
    assert 'value' not in gap['slots'][0]
    assert gap['request_scope'] == published['receipt']['request_scope']


def test_unpublished_checked_answer_cannot_leave_a_gap(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    from harness import eventlog
    original = eventlog.turn_record
    def fail(*args, **kwargs):
        raise OSError('history refused')
    monkeypatch.setattr(eventlog, 'turn_record', fail)
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'strict')
    install(monkeypatch, [{'blocks': [{'type': 'text', 'text': '{"schema_version":1,"blocks":[]}'}]}])
    response = client.post('/api/chat/stream', json={'session_id': sid, 'message': 'Show key results for generator G1.'})
    assert response.status_code == 200
    assert 'event: answer_approved' not in response.text
    monkeypatch.setattr(eventlog, 'turn_record', original)
    assert not any(r['family'] == 'source_gap' for r in client.get('/api/chat/activity').json()['items'])


@pytest.mark.parametrize('profile', ['generator-dispatch/1', 'generator-cost/1', 'network-linear-cost/1',
                                   'storage-unit-dispatch/1', 'storage-unit-cost/1'])
def test_all_supported_request_profiles_keep_exact_bounded_descriptors(client, origin, monkeypatch, profile):
    ctx, _owner, sid, _app = origin
    scope = dict(version=1, profile=profile, project_id=str(ctx.project_uuid),
        entity_id='network' if profile == 'network-linear-cost/1' else '<img src=x>',
        start='2026-01-01T00:00:00', end='2026-01-01T01:00:00', retain=True,
        metrics=['unsupported_metric'])
    if profile != 'storage-unit-dispatch/1':
        scope['cost_basis'] = dict(currency='EUR', price_year=2026, annual_fixed_cost_fraction=0.5)
        scope['comparison_evidence_id'] = '11111111-1111-1111-1111-111111111111'
    published = publish(client, sid, monkeypatch, checked_scope=scope)
    gap = inspect(client, gap_row(client)).json()['source_gap']
    assert gap['request_scope'] == scope
    assert gap['answer_id'] == published['answer_id']
    assert len(gap['slots']) == 1 and gap['slots'][0]['metric'] == 'unsupported_metric'


def test_unavailable_requested_identifiers_remain_descriptors_not_authority(client, origin, monkeypatch):
    ctx, _owner, sid, _app = origin
    scope = dict(version=1, profile='generator-cost/1', project_id=str(ctx.project_uuid),
        entity_id='G1', retain=True, evidence_id='x' * 36, comparison_evidence_id='y' * 36,
        metrics=['total_cost'])
    result = publish(client, sid, monkeypatch, checked_scope=scope)
    gap = inspect(client, gap_row(client)).json()['source_gap']
    assert gap['request_scope'] == scope
    assert gap['slots'][0]['reason'] == result['receipt']['result_slots'][0]['reason']
    assert 'decision_history_unavailable' not in client.get('/api/chat/activity').json()['warnings']
