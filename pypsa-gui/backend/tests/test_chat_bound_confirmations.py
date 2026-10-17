"""Ordinary dispatch and real project effects; no model calls."""
import uuid
import json
import hashlib
import pytest

from harness.session import ChatSession
from services import chat_tools
from harness.loop import dispatch_tool_call
from tests import test_workflow_tools as fixtures
from tests.test_chat_access import peer as peer_fixture

baseline = fixtures.baseline
helpers_state = fixtures.helpers_state
peer = peer_fixture


@pytest.mark.parametrize('mutation', ['caller', 'request_frame', 'card_frame'])
def test_changing_caller_arguments_cannot_replace_a_carded_write(baseline, mutation):
    session = ChatSession()
    args = {'component_class': 'Generator', 'names': ['G1'],
            'updates': {'marginal_cost': 20}}
    collector = []
    frames = []
    for event, data in dispatch_tool_call(
        session, {'id': uuid.uuid4().hex, 'name': 'bulk_update_components', 'input': args},
        collector, guided=True,
    ):
        frames.append((event, data))
        if event == 'tool_request' and mutation == 'request_frame':
            data['args']['updates']['marginal_cost'] = 99
        if event == 'tool_pending_confirmation':
            assert data['args']['updates']['marginal_cost'] == 20
            if mutation == 'caller':
                args['updates']['marginal_cost'] = 99
            elif mutation == 'card_frame':
                data['args']['updates']['marginal_cost'] = 99
            session.record_decision(data['confirmation_token'], 'approve')
    assert not [d for e, d in frames if e == 'tool_error'], frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 20


@pytest.mark.parametrize('change_at', ['tool_pending_confirmation', 'tool_running'])
@pytest.mark.parametrize('policy_change', ['exemptions', 'tool_tier'])
def test_changed_confirmation_policy_revokes_a_pending_write(baseline, monkeypatch, change_at, policy_change):
    from harness import confirm, catalogue

    session = ChatSession()
    collector, frames = [], []
    for event, data in dispatch_tool_call(
        session, {'id': 'policy-change', 'name': 'bulk_update_components', 'input': {
            'component_class': 'Generator', 'names': ['G1'], 'updates': {'marginal_cost': 20}}},
        collector, guided=True,
    ):
        frames.append((event, data))
        if event == change_at:
            if policy_change == 'exemptions':
                monkeypatch.setattr(confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            else:
                monkeypatch.setattr(catalogue, 'TOOLS', [
                    {**t, 'description': t['description'].replace('Safety: write', 'Safety: destructive')}
                    if t['name'] == 'bulk_update_components' else t for t in catalogue.TOOLS])
        if event == 'tool_pending_confirmation':
            session.record_decision(data['confirmation_token'], 'approve')
    assert any(d.get('error_kind') == 'confirmation_revoked' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    assert len(collector) == 1 and collector[0]['is_error']


def test_input_drift_while_waiting_requires_a_new_preview(baseline):
    preview = chat_tools.preview_project_changes([{
        'component_class': 'Generator', 'names': ['G1'],
        'attribute': 'marginal_cost', 'value': 20}])
    session, collector, frames = ChatSession(), [], []
    for event, data in dispatch_tool_call(
        session, {'id': 'source-change', 'name': 'apply_project_changes',
                  'input': {'preview_id': preview['preview_id']}}, collector,
    ):
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            chat_tools.bulk_update_components('Generator', ['G1'], {'p_nom': 50})
            session.record_decision(data['confirmation_token'], 'approve')
    assert any(d.get('error_kind') == 'preview_stale' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    assert chat_tools.get_component('Generator', 'G1')['p_nom'] == 50


@pytest.mark.parametrize('field', ['version', 'review_sha256'])
def test_legacy_or_unsealed_preview_cannot_ask_for_approval(baseline, field):
    preview = chat_tools.preview_project_changes([{
        'component_class': 'Generator', 'names': ['G1'],
        'attribute': 'marginal_cost', 'value': 20}])
    path = baseline[2] / 'assistant_previews' / (preview['preview_id'] + '.json')
    record = json.loads(path.read_text())
    record.pop(field)
    path.write_text(json.dumps(record))
    collector = []
    frames = list(dispatch_tool_call(ChatSession(), {
        'id': 'legacy-preview', 'name': 'apply_project_changes',
        'input': {'preview_id': preview['preview_id']}}, collector))
    assert not [d for e, d in frames if e == 'tool_pending_confirmation']
    assert any(d.get('error_kind') == 'invalid_preview' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    assert len(collector) == 1 and collector[0]['is_error']


@pytest.mark.parametrize('replace_with_valid_record', [False, True])
def test_replacing_a_persisted_preview_after_review_cannot_change_the_effect(baseline, replace_with_valid_record):
    preview = chat_tools.preview_project_changes([{
        'component_class': 'Generator', 'names': ['G1'],
        'attribute': 'marginal_cost', 'value': 20}])
    session, collector, frames = ChatSession(), [], []
    for event, data in dispatch_tool_call(
        session, {'id': 'preview-change', 'name': 'apply_project_changes',
                  'input': {'preview_id': preview['preview_id']}}, collector,
    ):
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            assert data['args']['reviewed_changes'][0]['after'] == 20
            path = baseline[2] / 'assistant_previews' / (preview['preview_id'] + '.json')
            record = json.loads(path.read_text())
            if replace_with_valid_record:
                replacement = chat_tools.preview_project_changes([{
                    'component_class': 'Generator', 'names': ['G1'],
                    'attribute': 'marginal_cost', 'value': 99}])
                replacement_path = path.with_name(replacement['preview_id'] + '.json')
                record = json.loads(replacement_path.read_text())
                record['preview_id'] = preview['preview_id']
                record.pop('review_sha256')
                # Fault injection includes a consistent checksum: a checksum
                # alone cannot replace the user's earlier reviewed content.
                record['review_sha256'] = hashlib.sha256(json.dumps(
                    record, sort_keys=True, separators=(',', ':'), allow_nan=False,
                ).encode()).hexdigest()
            else:
                record['changes'][0]['value'] = 99
            path.write_text(json.dumps(record))
            session.record_decision(data['confirmation_token'], 'approve')
    assert any(d.get('error_kind') == 'invalid_preview' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10


def test_a_preview_cannot_hide_changes_beyond_the_review_limit(baseline):
    from fastapi import HTTPException

    # Twenty-one existing assets, rather than twenty-one invalid targets.
    chat_tools.batch_create_components('Generator', [
        {'name': f'Extra{i}', 'bus': 'B1', 'p_nom': 20, 'marginal_cost': 10}
        for i in range(21)
    ])
    with pytest.raises(HTTPException) as failure:
        chat_tools.preview_project_changes([{
            'component_class': 'Generator', 'names': [f'Extra{i}' for i in range(21)],
            'attribute': 'marginal_cost', 'value': 20}])
    assert failure.value.detail['error_kind'] == 'invalid_preview'
    assert 'split' in failure.value.detail['message']
    assert chat_tools.get_component('Generator', 'Extra20')['marginal_cost'] == 10


def test_complete_preview_uses_owned_http_approval_and_single_use_token(baseline, client, seeded_identity):
    from harness import session as sessions

    session = sessions.get_or_create_session('bound-' + uuid.uuid4().hex,
                                            owner_user_id=str(seeded_identity['user_id']))
    preview = chat_tools.preview_project_changes([{
        'component_class': 'Generator', 'names': ['G1'],
        'attribute': 'marginal_cost', 'value': 20}])
    frames, collector = [], []
    try:
        for event, data in dispatch_tool_call(
            session, {'id': 'http-approval', 'name': 'apply_project_changes',
                      'input': {'preview_id': preview['preview_id']}}, collector,
        ):
            frames.append((event, data))
            if event == 'tool_pending_confirmation':
                assert data['args']['changes_total'] == 1
                assert data['args']['reviewed_changes'] == [{
                    'component_class': 'Generator', 'name': 'G1', 'attribute': 'marginal_cost',
                    'before': 10.0, 'after': 20, 'unit': 'currency/MWh'}]
                route = f'/api/chat/{session.session_id}/confirm'
                body = {'token': data['confirmation_token'], 'decision': 'approve'}
                response = client.post(route, json=body)
                assert response.status_code == 200, response.text
                assert client.post(route, json=body).status_code == 404
        assert not [d for e, d in frames if e == 'tool_error'], frames
        assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 20
    finally:
        sessions.drop_session(session.session_id)


def test_a_foreign_edit_lock_acquired_after_the_card_still_blocks_apply(baseline, client, peer):
    browser, peer_id = peer
    pid = baseline[1]
    assert client.put('/api/projects/Tool Baseline/members', json={'user_ids': [peer_id]}).status_code == 200
    preview = chat_tools.preview_project_changes([{
        'component_class': 'Generator', 'names': ['G1'],
        'attribute': 'marginal_cost', 'value': 20}])
    session, collector, frames = ChatSession(), [], []
    for event, data in dispatch_tool_call(
        session, {'id': 'lock-change', 'name': 'apply_project_changes',
                  'input': {'preview_id': preview['preview_id']}}, collector,
    ):
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            assert client.delete(f'/api/projects/{pid}/lock').status_code == 200
            assert browser.post(f'/api/projects/{pid}/activate').status_code == 200
            assert browser.post(f'/api/projects/{pid}/lock').status_code == 200
            session.record_decision(data['confirmation_token'], 'approve')
    assert any(d.get('error_kind') == 'project_locked' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
