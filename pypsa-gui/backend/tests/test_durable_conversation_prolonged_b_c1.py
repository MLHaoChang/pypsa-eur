"""Issue 09 / B-C1 — prolonged durable journey: refs and budget settle survive.

create -> checkpoint -> rebind -> resume -> settle -> generation bump, over the
HTTP router for thread lifecycle and the service ledger for budgets (no budget
route exists yet). Fakes only; no LiveKit, media or provider calls.
"""
from __future__ import annotations

import collections

import pytest

from services import chat_service
from services import durable_conversation as dc
from services import utterance_admission as ua


OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'
BASE = '/api/durable-conversations'

REFS = {
    'current': {'project': 'P1', 'run_id': 'R7', 'asset': 'Battery-1',
                'source': 'dispatch', 'window': '2030-01-01/2030-01-07'},
    'discussed': {'run_id': 'R3', 'asset': 'Gas-2'},
}
GOALS = ['Limit battery to 7.5 MW']
UNITS = {'power': 'MW', 'energy': 'MWh'}
EVIDENCE = [{'run_id': 'R7', 'revision': 4}]


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()

    def authority():
        root = tmp_path / PROJECT
        root.mkdir(parents=True, exist_ok=True)
        return root, PROJECT, OWNER

    monkeypatch.setattr(dc, '_authority', authority)
    yield
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


def _get(client, tid):
    resp = client.get(f'{BASE}/{tid}')
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post(client, tid, action, generation):
    resp = client.post(f'{BASE}/{tid}/{action}', json={'generation': generation})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_facts(thread):
    assert thread['goals'] == GOALS
    assert thread['units'] == UNITS
    assert thread['refs'] == REFS
    assert thread['evidence_revisions'] == EVIDENCE


def test_prolonged_journey_refs_and_budget_settle_survive(client):
    tid = client.post(BASE).json()['thread_id']
    assert client.post(
        f'{BASE}/{tid}/checkpoint',
        json={'generation': 1, 'goals': GOALS, 'units': UNITS, 'refs': REFS,
              'evidence_revisions': EVIDENCE},
    ).status_code == 200
    dc.reserve_budget(tid, key='turn-1', kind='stt_seconds', amount=30, unit='s')
    dc.reserve_budget(tid, key='turn-2', kind='tts_chars', amount=500, unit='chars')

    rebound = _post(client, tid, 'rebind', 1)
    assert rebound['generation'] == 2
    token = rebound['credentials']['token']
    _assert_facts(rebound)

    resumed = _post(client, tid, 'resume', 2)
    assert resumed['generation'] == 3
    first_episode = resumed['episode_id']
    assert first_episode
    _assert_facts(resumed)
    assert [r['key'] for r in resumed['budgets']['reservations']] == [
        'turn-1', 'turn-2',
    ]
    with pytest.raises(Exception) as stale:
        dc.authorize_bridge_credentials(tid, token)
    assert stale.value.status_code == 401

    dc.settle_budget(tid, key='turn-1', amount=24, unit='s', cached=6)
    resumed2 = _post(client, tid, 'resume', 3)
    assert resumed2['generation'] == 4
    assert resumed2['episode_id'] not in (None, first_episode)
    assert chat_service.get_session(first_episode) is None
    _assert_facts(resumed2)

    dc.settle_budget(tid, key='turn-2', amount=420, unit='chars')
    final = _post(client, tid, 'rebind', 4)
    assert final['generation'] == 5
    _assert_facts(final)
    budgets = _get(client, tid)['budgets']
    assert budgets['reservations'] == []
    assert {c['key']: (c['amount'], c['unit']) for c in budgets['final_charges']} == {
        'turn-1': (24, 's'), 'turn-2': (420, 'chars'),
    }
    assert budgets['cached_usage'] == {'s': 6, 'chars': 0}


def test_settle_counts_once_across_resume_and_rebind(client):
    tid = client.post(BASE).json()['thread_id']
    dc.reserve_budget(tid, key='once', kind='stt_seconds', amount=10, unit='s')
    dc.settle_budget(tid, key='once', amount=9, unit='s', cached=2)
    _post(client, tid, 'resume', 1)
    _post(client, tid, 'rebind', 2)

    with pytest.raises(Exception) as again:
        dc.settle_budget(tid, key='once', amount=9, unit='s', cached=2)
    assert again.value.detail['error_kind'] == 'budget_already_settled'
    with pytest.raises(Exception) as rereserve:
        dc.reserve_budget(tid, key='once', kind='stt_seconds', amount=10, unit='s')
    assert rereserve.value.detail['error_kind'] == 'budget_already_settled'

    budgets = _get(client, tid)['budgets']
    assert len(budgets['final_charges']) == 1
    assert budgets['cached_usage'] == {'s': 2}


def test_many_cycles_with_compaction_keep_refs_and_budget(client):
    tid = client.post(BASE).json()['thread_id']
    client.post(
        f'{BASE}/{tid}/checkpoint',
        json={'generation': 1, 'goals': GOALS, 'units': UNITS, 'refs': REFS,
              'evidence_revisions': EVIDENCE},
    )
    generation = 1
    messages = collections.deque(
        {'role': 'user', 'content': f'turn {i}'} for i in range(400)
    )
    for cycle in range(6):
        key = f'cycle-{cycle}'
        dc.reserve_budget(tid, key=key, kind='stt_seconds', amount=5, unit='s')
        compacted = dc.compact_model_context(tid, messages, max_len=20)
        _assert_facts(compacted)
        assert len(messages) <= 20
        action = 'resume' if cycle % 2 == 0 else 'rebind'
        generation = _post(client, tid, action, generation)['generation']
        dc.settle_budget(tid, key=key, amount=4, unit='s', cached=1)

    thread = _get(client, tid)
    assert thread['generation'] == 7
    _assert_facts(thread)
    assert thread['budgets']['reservations'] == []
    assert [c['key'] for c in thread['budgets']['final_charges']] == [
        f'cycle-{n}' for n in range(6)
    ]
    assert sum(c['amount'] for c in thread['budgets']['final_charges']) == 24
    assert thread['budgets']['cached_usage'] == {'s': 6}


def test_stale_generation_after_journey_leaves_facts_intact(client):
    tid = client.post(BASE).json()['thread_id']
    client.post(
        f'{BASE}/{tid}/checkpoint',
        json={'generation': 1, 'goals': GOALS, 'units': UNITS, 'refs': REFS,
              'evidence_revisions': EVIDENCE},
    )
    dc.reserve_budget(tid, key='open', kind='tts_chars', amount=100, unit='chars')
    _post(client, tid, 'rebind', 1)
    _post(client, tid, 'resume', 2)

    stale = client.post(
        f'{BASE}/{tid}/checkpoint',
        json={'generation': 1, 'goals': ['overwritten'], 'refs': {'current': {}}},
    )
    assert stale.status_code == 409
    assert stale.json()['detail']['error_kind'] == 'stale_generation'
    assert client.post(
        f'{BASE}/{tid}/rebind', json={'generation': 2},
    ).status_code == 409

    thread = _get(client, tid)
    assert thread['generation'] == 3
    _assert_facts(thread)
    assert thread['budgets']['reservations'][0]['key'] == 'open'
