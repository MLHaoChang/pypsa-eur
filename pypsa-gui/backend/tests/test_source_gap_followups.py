"""Explicit checked follow-ups record publication lineage, never resolution."""
# ruff: noqa: F811 -- shared pytest fixtures.
import concurrent.futures
import copy
import json
import threading
import uuid

import pytest

from harness import attention, session as sessions
from harness.providers.fake import FakeProvider
from services import chat_service
from tests.assurance_acceptance.support import approval, parse_sse
from tests.test_assistant_decisions import origin, install, inspect  # noqa: F401
from tests.test_assistant_source_gaps import publish, gap_row
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_conversation_scope import conversation  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def parent_row(client, source):
    return next(r for r in client.get('/api/chat/activity').json()['items'] if r.get('decision_id') == source)


def prepare(client, row):
    response = inspect(client, row)
    assert response.status_code == 200, response.text
    prepared = response.json()['prepared_followup']
    assert prepared is not None
    return prepared


def send(client, monkeypatch, prepared, sid=None, **changes):
    provider = install(monkeypatch, [{'blocks': [{'type': 'text', 'text': '{"schema_version":1,"blocks":[]}'}]}])
    body = {'session_id': sid or 'gap-followup-' + uuid.uuid4().hex, **copy.deepcopy(prepared), **changes}
    response = client.post('/api/chat/stream', json=body)
    return response, provider


def refused(response, provider):
    assert response.status_code in (200, 422), response.text
    assert 'event: answer_approved' not in response.text
    assert not provider.requests, 'A refused reference must not reach the provider'
    if response.status_code == 200:
        assert 'source_gap_followup_unavailable' in response.text


def test_explicit_followup_survives_old_session_loss_and_records_exact_limited_publication(client, origin, monkeypatch):
    ctx, _owner, sid, _app = origin
    old = publish(client, sid, monkeypatch)
    row = gap_row(client)
    prepared = prepare(client, row)
    assert prepared['checked_scope']['project_id'] == str(ctx.project_uuid)
    assert prepared['checked_scope']['metrics'] == ['dispatch_energy', 'marginal_expenditure', 'peak_dispatch']
    sessions.drop_session(sid)
    capture = attention.checked_answer
    def duplicate(native, project):
        capture(native, project)
        capture(native, project)
    monkeypatch.setattr(attention, 'checked_answer', duplicate)
    new_sid = 'separate-followup-' + uuid.uuid4().hex
    response, _ = send(client, monkeypatch, prepared, new_sid)
    new = approval(parse_sse(response.text))
    assert new['receipt']['request_complete'] is False
    assert inspect(client, row).status_code == 409
    current = parent_row(client, row['decision_id'])
    assert current['status'] == 'needs_review' and current['native_status'] == 'source_gap_recorded'
    detail = inspect(client, current).json()
    assert detail['source_gap']['answer_id'] == old['answer_id']
    assert detail['revision'] == 2 and detail['evidence_available'] is False
    assert len(detail['followups']) == 1
    link = detail['followups'][0]
    assert link['answer_id'] == new['answer_id'] and link['turn_id'] == new['turn_id']
    assert link['session_id'] == new_sid and link['request_complete'] is False
    assert 'value' not in link and sessions.get_session(sid) is None
    sessions.drop_session(new_sid)
    from services import assistant_activity
    assistant_activity._SCOPES.clear()
    assert inspect(client, parent_row(client, row['decision_id'])).json()['followups'] == [link]


def test_complete_native_followup_does_not_resolve_the_historical_gap(solved, conversation, checked, client, monkeypatch, tmp_path):
    monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(tmp_path / 'gap-app'))
    solved.solver_state['status'] = 'failed'
    first, _ = conversation('Give me the key results for generator G1.', {})
    assert approval(first)['receipt']['request_complete'] is False
    row = gap_row(client)
    prepared = prepare(client, row)
    solved.solver_state['status'] = 'completed'
    checked.install(NumericalProvider())
    response = client.post('/api/chat/stream', json={'session_id': 'complete-followup-' + uuid.uuid4().hex, **prepared})
    approved = approval(parse_sse(response.text))
    assert approved['receipt']['request_complete'] is True
    latest = parent_row(client, row['decision_id'])
    assert latest['status'] == 'needs_review'
    detail = inspect(client, latest).json()
    assert detail['followups'][0]['answer_id'] == approved['answer_id']
    assert detail['followups'][0]['request_complete'] is True and detail['evidence_available'] is False
    assert len([r for r in client.get('/api/chat/activity').json()['items'] if r['family'] == 'source_gap']) == 1


@pytest.mark.parametrize('field', ['entity_id', 'metrics', 'project_id', 'cost_basis', 'evidence_id', 'retain', 'window'])
def test_modified_scope_is_refused_before_provider_work(client, origin, monkeypatch, field):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    prepared = copy.deepcopy(prepare(client, row))
    changes = dict(entity_id='different', metrics=['peak_dispatch'], project_id=str(uuid.uuid4()),
        cost_basis=dict(currency='EUR', price_year=2026, annual_fixed_cost_fraction=1),
        evidence_id=str(uuid.uuid4()), retain=True)
    if field == 'window':
        prepared['checked_scope'].update(start='2026-01-01', end='2026-01-02')
    else:
        prepared['checked_scope'][field] = changes[field]
    response, provider = send(client, monkeypatch, prepared)
    refused(response, provider)
    assert inspect(client, row).status_code == 200


@pytest.mark.parametrize('change', [
    {'message': 'Add another constraint'}, {'attachment_file_ids': ['file']}, {'script': []},
    {'checked_operation': {}}, {'utterance_id': 'voice'}, {'generation': 1}, {'input_mode': 'voice'},
    {'source_gap_ref': {'source_id': 'invented', 'revision': True, 'digest': 'x'}},
])
def test_other_message_or_admission_channels_cannot_reuse_the_link(client, origin, monkeypatch, change):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    response, provider = send(client, monkeypatch, prepare(client, gap_row(client)), **change)
    refused(response, provider)


def test_link_requires_explicit_strict_opt_in_and_never_falls_back(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    prepared = prepare(client, gap_row(client))
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'pilot')
    response, provider = send(client, monkeypatch, prepared)
    refused(response, provider)
    response, _ = send(client, monkeypatch, prepared, assurance={'version': 1, 'mode': 'strict'})
    assert approval(parse_sse(response.text))['receipt']['request_complete'] is False


def test_foreign_project_stale_and_lost_origins_are_refused_without_activation(client, other_org_client, origin, monkeypatch):
    ctx, _owner, sid, app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    prepared = prepare(client, row)
    assert other_org_client.post('/api/projects/Foreign', params={'force': True, 'rebind': True}).status_code == 200
    response, provider = send(other_org_client, monkeypatch, prepared)
    refused(response, provider)
    changed = copy.deepcopy(prepared)
    changed['source_gap_ref']['digest'] = '0' * 64
    response, provider = send(client, monkeypatch, changed)
    refused(response, provider)
    assert client.post('/api/projects/New active project', params={'force': True, 'rebind': True}).status_code == 200
    response, provider = send(client, monkeypatch, prepared)
    refused(response, provider)
    assert client.get('/api/chat/activity').json()['items'] == []
    assert inspect(client, row).json()['project_id'] == row['project_id']
    assert str(ctx.project_uuid) != row['project_id']  # inspection left the new active project bound
    list(app.rglob('decisions.json'))[0].write_text('{}')
    assert inspect(client, row).status_code == 404


def test_clarification_scope_cannot_silently_choose_a_cost_quantity(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch, 'Show cost for generator G1.')
    detail = inspect(client, gap_row(client)).json()
    assert detail['prepared_followup'] is None and detail['followup_unavailable_reason']


def test_links_are_bounded_and_old_versions_cannot_repeat_a_send(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    initial = prepare(client, row)
    for _ in range(attention.MAX_FOLLOWUPS):
        prepared = prepare(client, parent_row(client, row['decision_id']))
        response, _ = send(client, monkeypatch, prepared)
        approval(parse_sse(response.text))
    current = parent_row(client, row['decision_id'])
    detail = inspect(client, current).json()
    assert len(detail['followups']) == 8 and detail['prepared_followup'] is None
    response, provider = send(client, monkeypatch, initial)
    refused(response, provider)
    full = copy.deepcopy(initial)
    full['source_gap_ref'].update(revision=current['decision_revision'], digest=current['decision_digest'])
    response, provider = send(client, monkeypatch, full)
    refused(response, provider)


@pytest.mark.parametrize('existing', [0, 7])
def test_concurrently_admitted_native_followups_append_unique_links(client, origin, monkeypatch, existing):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    for _ in range(existing):
        response, _ = send(client, monkeypatch, prepare(client, parent_row(client, row['decision_id'])))
        approval(parse_sse(response.text))
    prepared = prepare(client, parent_row(client, row['decision_id']))
    admit = attention.admit_followup
    barrier = threading.Barrier(2)
    def together(*args, **kwargs):
        result = admit(*args, **kwargs)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(attention, 'admit_followup', together)
    monkeypatch.setattr(chat_service, '_provider_for_profile', lambda *a, **kw: (
        FakeProvider([{'blocks': [{'type': 'text', 'text': '{"schema_version":1,"blocks":[]}'}]}]), None))
    def request(i):
        return client.post('/api/chat/stream', json={'session_id': f'concurrent-gap-{i}-{uuid.uuid4().hex}', **prepared})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(request, range(2)))
    ids = {approval(parse_sse(r.text))['answer_id'] for r in answers}
    detail = inspect(client, parent_row(client, row['decision_id'])).json()
    linked = {i['answer_id'] for i in detail['followups']}
    assert len(linked) == min(8, existing + 2)
    assert len(linked & ids) == min(2, 8 - existing)
    assert detail['revision'] == 1 + min(8, existing + 2)
    if existing:
        assert 'decision_history_unavailable' in client.get('/api/chat/activity').json()['warnings']


def test_journal_write_failure_does_not_block_published_followup_or_claim_a_link(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    prepared = prepare(client, row)
    from harness import owned_snapshots
    def fail(*a, **kw):
        raise OSError('private link failure')
    monkeypatch.setattr(owned_snapshots, 'write', fail)
    response, _ = send(client, monkeypatch, prepared)
    approval(parse_sse(response.text))
    assert 'followups' not in inspect(client, row).json()
    activity = client.get('/api/chat/activity')
    assert 'decision_history_unavailable' in activity.json()['warnings']
    assert 'private link failure' not in activity.text


def test_unpublished_followup_never_creates_a_link(client, origin, monkeypatch):
    _ctx, _owner, sid, _app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    prepared = prepare(client, row)
    from harness import eventlog
    def fail(*a, **kw):
        raise OSError('cannot persist publication history')
    monkeypatch.setattr(eventlog, 'turn_record', fail)
    response, _ = send(client, monkeypatch, prepared)
    assert response.status_code == 200 and 'event: answer_approved' not in response.text
    assert 'followups' not in inspect(client, row).json()


@pytest.mark.parametrize('fault', ['value', 'duplicate', 'duplicate_turn', 'self', 'complete', 'time'])
def test_corrupt_followup_history_is_unavailable_and_cannot_seed_a_request(client, origin, monkeypatch, fault):
    _ctx, _owner, sid, app = origin
    publish(client, sid, monkeypatch)
    row = gap_row(client)
    prepared = prepare(client, row)
    response, _ = send(client, monkeypatch, prepared)
    approval(parse_sse(response.text))
    current = parent_row(client, row['decision_id'])
    latest = prepare(client, current)
    path = list(app.rglob('decisions.json'))[0]
    data = json.loads(path.read_text())
    parent = next(r for r in data['records'] if r['source_id'] == row['decision_id'])
    link = parent['followups'][0]
    if fault == 'value':
        link['value'] = 999
    elif fault == 'duplicate':
        parent['followups'].append(link.copy())
    elif fault == 'duplicate_turn':
        parent['followups'].append(dict(link, answer_id=str(uuid.uuid4())))
    elif fault == 'self':
        link['answer_id'] = parent['source_gap']['answer_id']
    elif fault == 'complete':
        link['request_complete'] = 1
    else:
        link['observed_at'] = parent['created_at'] - 1
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    response, provider = send(client, monkeypatch, latest)
    refused(response, provider)
    assert inspect(client, current).status_code == 404
    assert path.read_bytes() == before
