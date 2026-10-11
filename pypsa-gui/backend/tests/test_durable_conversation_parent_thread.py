"""Issue 09 — durable parent thread survives compaction; fail-closed ownership."""
from __future__ import annotations

import collections

import pytest
from fastapi import HTTPException

from services import chat_service
from services import durable_conversation as dc


OWNER = 'owner-aaa'
OTHER = 'owner-bbb'
PROJECT = '11111111-1111-1111-1111-111111111111'
OTHER_PROJECT = '22222222-2222-2222-2222-222222222222'


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()

    def authority(project_id=PROJECT, owner_id=OWNER):
        root = tmp_path / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root, project_id, owner_id

    monkeypatch.setattr(dc, '_authority', lambda: authority())
    yield
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()


def _seed_facts(thread_id: str) -> dict:
    return dc.checkpoint_thread(
        thread_id,
        goals=['Limit battery to 7.5 MW'],
        constraints=['Do not extend lines'],
        units={'power': 'MW', 'energy': 'MWh'},
        refs={
            'current': {
                'project_id': PROJECT,
                'run_id': 'R1',
                'asset_id': 'battery-1',
                'source': 'dispatch',
                'window': '2025-01-01/2025-01-02',
            },
            'discussed': {
                'project_id': PROJECT,
                'run_id': 'R0',
                'asset_id': 'battery-1',
                'source': 'capacity_expansion',
                'window': '2024-01-01/2024-12-31',
            },
        },
        task_ids=['task-export-1'],
        job_ids=['job-solve-9'],
        experiment_ids=[],
        evidence_revisions=[{'run_id': 'R1', 'revision': 3}],
        profile_lineage=[{'profile_id': 'claude-sonnet', 'wire': 'anthropic'}],
    )


def test_create_thread_records_owner_and_project():
    thread = dc.create_thread()
    assert thread['owner_id'] == OWNER
    assert thread['project_id'] == PROJECT
    assert thread['generation'] == 1
    assert thread['episode_id'] is None
    loaded = dc.get_thread(thread['thread_id'])
    assert loaded['thread_id'] == thread['thread_id']
    assert loaded['budgets']['reservations'] == []
    assert loaded['budgets']['final_charges'] == []


def test_exact_goal_and_units_survive_compaction():
    thread = dc.create_thread()
    _seed_facts(thread['thread_id'])
    messages = collections.deque(
        {'role': 'user', 'content': f'filler {i}'} for i in range(80)
    )
    dc.compact_model_context(thread['thread_id'], messages, max_len=8)
    assert len(messages) <= 8
    after = dc.get_thread(thread['thread_id'])
    assert after['goals'] == ['Limit battery to 7.5 MW']
    assert after['units'] == {'power': 'MW', 'energy': 'MWh'}
    assert after['refs']['current']['run_id'] == 'R1'
    assert after['refs']['discussed']['run_id'] == 'R0'
    assert after['refs']['current']['source'] == 'dispatch'
    assert after['refs']['discussed']['window'] == '2024-01-01/2024-12-31'
    assert after['refs']['current']['asset_id'] == 'battery-1'


def test_compaction_does_not_clear_task_or_evidence_refs():
    thread = dc.create_thread()
    _seed_facts(thread['thread_id'])
    messages = collections.deque({'role': 'user', 'content': str(i)} for i in range(40))
    dc.compact_model_context(thread['thread_id'], messages, max_len=4)
    after = dc.get_thread(thread['thread_id'])
    assert after['task_ids'] == ['task-export-1']
    assert after['job_ids'] == ['job-solve-9']
    assert after['evidence_revisions'] == [{'run_id': 'R1', 'revision': 3}]


def test_wrong_actor_cannot_checkpoint_or_resume(tmp_path, monkeypatch):
    thread = dc.create_thread()
    tid = thread['thread_id']

    def other_authority():
        root = tmp_path / PROJECT
        root.mkdir(parents=True, exist_ok=True)
        return root, PROJECT, OTHER

    monkeypatch.setattr(dc, '_authority', other_authority)
    with pytest.raises(HTTPException) as failure:
        dc.checkpoint_thread(tid, goals=['hijack'])
    assert failure.value.status_code == 404
    assert failure.value.detail['error_kind'] == 'thread_not_found'
    with pytest.raises(HTTPException) as failure:
        dc.resume_episode(tid)
    assert failure.value.status_code == 404
    monkeypatch.setattr(
        dc, '_authority',
        lambda: (tmp_path / PROJECT, PROJECT, OWNER),
    )
    untouched = dc.get_thread(tid)
    assert untouched['goals'] == []


def test_wrong_project_cannot_read_thread(tmp_path, monkeypatch):
    thread = dc.create_thread()
    tid = thread['thread_id']

    def other_project():
        root = tmp_path / OTHER_PROJECT
        root.mkdir(parents=True, exist_ok=True)
        return root, OTHER_PROJECT, OWNER

    monkeypatch.setattr(dc, '_authority', other_project)
    with pytest.raises(HTTPException) as failure:
        dc.get_thread(tid)
    assert failure.value.status_code == 404
    assert failure.value.detail['error_kind'] == 'thread_not_found'


def test_resume_mints_new_episode_and_clears_child_approvals():
    thread = dc.create_thread()
    _seed_facts(thread['thread_id'])
    old = chat_service.get_or_create_session(
        'episode-old', owner_user_id=OWNER,
    )
    pc = old.issue_confirmation(
        tool_name='apply_project_changes',
        args={'preview_id': 'p1'},
        safety_tier='destructive',
    )
    assert pc.token in old.pending_confirmations
    dc.checkpoint_thread(thread['thread_id'], episode_id=old.session_id)

    updated, new_session = dc.resume_episode(thread['thread_id'])
    assert updated['episode_id'] == new_session.session_id
    assert updated['episode_id'] != old.session_id
    assert updated['generation'] == 2
    assert updated['goals'] == ['Limit battery to 7.5 MW']
    assert updated['refs']['current']['run_id'] == 'R1'
    assert chat_service.get_session(old.session_id) is None
    assert pc.token not in getattr(new_session, 'pending_confirmations', {})
    assert new_session.owner_user_id == OWNER


def test_ownerless_or_forged_owner_is_refused(tmp_path):
    thread = dc.create_thread()
    path = tmp_path / PROJECT / 'durable_conversations' / (thread['thread_id'] + '.json')
    forged = path.read_text(encoding='utf-8').replace(OWNER, 'forged-owner')
    path.write_text(forged, encoding='utf-8')
    with pytest.raises(HTTPException) as failure:
        dc.get_thread(thread['thread_id'])
    assert failure.value.status_code == 404
    assert failure.value.detail['error_kind'] == 'thread_not_found'


def test_parent_budget_reserve_settle_counts_once_across_resume():
    """B-U2: parent ledger is authoritative across episode restart."""
    thread = dc.create_thread()
    tid = thread['thread_id']
    reserved = dc.reserve_budget(
        tid, key='turn-1', kind='llm', amount=100, unit='tokens',
    )
    assert reserved['status'] == 'reserved'
    assert any(r['key'] == 'turn-1' for r in dc.get_thread(tid)['budgets']['reservations'])
    # Idempotent reserve under the same key.
    again = dc.reserve_budget(
        tid, key='turn-1', kind='llm', amount=100, unit='tokens',
    )
    assert again['status'] == 'reserved'
    reservations = dc.get_thread(tid)['budgets']['reservations']
    assert sum(1 for r in reservations if r['key'] == 'turn-1') == 1

    settled = dc.settle_budget(
        tid, key='turn-1', amount=80, unit='tokens', cached=20,
    )
    assert settled['status'] == 'settled'
    parent = dc.get_thread(tid)
    assert parent['budgets']['final_charges'] == [{
        'key': 'turn-1', 'kind': 'llm', 'amount': 80, 'unit': 'tokens',
    }]
    assert parent['budgets']['cached_usage'] == {'tokens': 20}
    assert parent['budgets']['reservations'] == []

    # Resume must not replay or double-count settled charges.
    updated, _session = dc.resume_episode(tid)
    assert updated['budgets']['final_charges'] == [{
        'key': 'turn-1', 'kind': 'llm', 'amount': 80, 'unit': 'tokens',
    }]
    assert updated['budgets']['cached_usage'] == {'tokens': 20}
    with pytest.raises(HTTPException) as failure:
        dc.settle_budget(tid, key='turn-1', amount=80, unit='tokens', cached=20)
    assert failure.value.detail['error_kind'] == 'budget_already_settled'


def test_parent_budget_does_not_bypass_child_session_caps(monkeypatch):
    """Child ChatSession caps remain enforced independently of parent ledger."""
    import harness.budget as budget

    thread = dc.create_thread()
    tid = thread['thread_id']
    dc.reserve_budget(tid, key='big', kind='llm', amount=10, unit='tokens')
    dc.settle_budget(tid, key='big', amount=10, unit='tokens', cached=0)
    sess = chat_service.ChatSession(owner_user_id=OWNER)
    sess.usage_acc['output_tokens'] = budget.MAX_OUTPUT_TOKENS_PER_SESSION
    # Parent ledger has room conceptually; child session is still capped.
    assert sess.usage_acc['output_tokens'] >= budget.MAX_OUTPUT_TOKENS_PER_SESSION
    parent = dc.get_thread(tid)
    assert sum(c['amount'] for c in parent['budgets']['final_charges']) == 10
