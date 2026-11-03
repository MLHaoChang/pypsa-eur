"""Issue 09 PR1 — restart, reconcile and bounded rotation of a durable thread.

GK B conditions (a)-(c): a live turn refuses resume; a dead worker's lapsed
ownership lease lets resume take over; the interrupted turn's effects are
listed as unverified and nothing is replayed; the episode lineage is bounded
and rotations never reset parent accounting.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from harness import eventlog
from harness import runner
from services import chat_service
from services import durable_conversation as dc
from services import utterance_admission as ua


OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    import security

    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()
    root = tmp_path / PROJECT
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dc, '_authority', lambda: (root, PROJECT, OWNER))
    monkeypatch.setattr(eventlog, '_root', lambda: tmp_path / 'chat-events')
    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    yield
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


def _started_thread():
    tid = dc.create_thread()['thread_id']
    view, session = dc.resume_episode(tid, generation=1)
    return tid, view, session


def _interrupt_turn_with_tool_call(session):
    """A turn that called a tool and never ended: the worker died."""
    eventlog.turn_start(session, 'Solve the base case')
    eventlog.message(session, {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'Solving.'},
        {'type': 'tool_use', 'id': 'toolu_solve', 'name': 'run_optimization',
         'input': {'secret_like': 'never copied'}},
        {'type': 'tool_use', 'id': 'toolu_read', 'name': 'get_network_summary',
         'input': {}},
    ]})
    eventlog.message(session, {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'toolu_read', 'content': 'ok'},
    ]})
    eventlog.flush(session)


def _die_and_restart(monkeypatch, *, lapse=True):
    """The owner process is gone; a fresh one (another replica id) starts."""
    import security

    chat_service._reset_sessions_for_tests()
    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-beta')
    if lapse:
        now = time.time() + runner.LEASE_SECONDS + 1
        monkeypatch.setattr(dc, '_wall_clock', lambda: now)


# ── (a) live turn vs abandoned turn ─────────────────────────────────────────


def test_resume_refuses_while_turn_in_flight_and_keeps_episode():
    tid, view, session = _started_thread()
    session._turn_in_flight = True
    with pytest.raises(HTTPException) as failure:
        dc.resume_episode(tid, generation=view['generation'])
    assert failure.value.status_code == 409
    assert failure.value.detail['error_kind'] == 'turn_already_in_flight'
    after = dc.get_thread(tid)
    assert after['episode_id'] == session.session_id
    assert after['generation'] == view['generation']
    assert chat_service.get_session(session.session_id) is session


def test_live_owner_lease_still_refuses_another_replica(monkeypatch):
    tid, view, session = _started_thread()
    _die_and_restart(monkeypatch, lapse=False)
    with pytest.raises(HTTPException) as failure:
        dc.resume_episode(tid, generation=view['generation'])
    assert failure.value.detail['error_kind'] == 'wrong_replica'


def test_lapsed_lease_resume_takes_over_and_reconciles(monkeypatch):
    tid, view, session = _started_thread()
    old_episode = session.session_id
    _interrupt_turn_with_tool_call(session)
    dc.checkpoint_thread(tid, generation=view['generation'], job_ids=['job-7'])
    _die_and_restart(monkeypatch)

    resumed, new_session = dc.resume_episode(tid, generation=view['generation'])

    assert resumed['owner_replica_id'] == 'replica-beta'
    assert resumed['episode_id'] == new_session.session_id != old_episode
    assert resumed['generation'] == view['generation'] + 1
    assert resumed['episode_lineage'][-1]['reason'] == 'takeover'
    entries = resumed['reconcile']
    assert {'kind': 'turn', 'episode_id': old_episode, 'status': 'unverified'} in [
        {k: e.get(k) for k in ('kind', 'episode_id', 'status')} for e in entries
    ]
    tools = [e for e in entries if e['kind'] == 'tool_call']
    assert [(e['tool'], e['tool_use_id'], e['status']) for e in tools] == [
        ('run_optimization', 'toolu_solve', 'unverified'),
    ]
    assert 'input' not in tools[0] and 'never copied' not in str(entries)
    jobs = [e for e in entries if e['kind'] == 'job']
    assert [(e['job_id'], e['status']) for e in jobs] == [('job-7', 'unverified')]


def test_takeover_refuses_other_controls_until_resume(monkeypatch):
    tid, view, _ = _started_thread()
    _die_and_restart(monkeypatch)
    for call in (
        lambda: dc.checkpoint_thread(tid, generation=view['generation'], goals=['x']),
        lambda: dc.rebind_scope(tid, generation=view['generation']),
    ):
        with pytest.raises(HTTPException) as failure:
            call()
        assert failure.value.detail['error_kind'] == 'wrong_replica'
        assert failure.value.detail['resume_required'] is True


def test_clean_resume_has_no_reconcile_entries():
    tid, view, session = _started_thread()
    eventlog.turn_start(session, 'hello')
    eventlog.turn_end(session, 'completed')
    resumed, _ = dc.resume_episode(tid, generation=view['generation'])
    assert resumed['reconcile'] == []
    assert resumed['episode_lineage'][-1]['reason'] == 'resume'


# ── (b) reconcile never replays ─────────────────────────────────────────────


def test_reconcile_calls_no_tool_and_leaves_old_log_unchanged(monkeypatch):
    from harness import loop

    tid, view, session = _started_thread()
    _interrupt_turn_with_tool_call(session)
    log_path = eventlog.path_for(session)
    before = log_path.read_bytes()
    _die_and_restart(monkeypatch)
    calls = []
    monkeypatch.setattr(
        loop, 'dispatch_tool_call', lambda *a, **k: calls.append('dispatch'),
    )
    resumed, new_session = dc.resume_episode(tid, generation=view['generation'])
    assert calls == []
    assert log_path.read_bytes() == before
    assert not any(
        isinstance(block, dict) and block.get('type') == 'tool_use'
        for message in new_session.messages
        for block in (message.get('content') or [] if isinstance(message.get('content'), list) else [])
    )
    assert any(e['kind'] == 'tool_call' for e in resumed['reconcile'])


# ── (c) bounded lineage, exact accounting ───────────────────────────────────


def test_lineage_is_bounded_and_rotations_keep_ledger_and_refs(monkeypatch):
    tid = dc.create_thread()['thread_id']
    generation = 1
    dc.checkpoint_thread(
        tid, generation=generation, goals=['Cap import at 40 MW'],
        units={'power': 'MW'},
    )
    episodes = []
    total = 0
    for index in range(dc.EPISODE_LINEAGE_MAX + 5):
        view, session = dc.resume_episode(tid, generation=generation)
        generation = view['generation']
        episodes.append(session.session_id)
        key = f'turn-{index}'
        dc.reserve_budget(tid, key=key, kind='tokens', amount=3, unit='tokens')
        dc.settle_budget(tid, key=key, amount=2, unit='tokens')
        total += 2

    final = dc.get_thread(tid)
    lineage = final['episode_lineage']
    assert len(lineage) == dc.EPISODE_LINEAGE_MAX
    assert [e['episode_id'] for e in lineage] == episodes[-dc.EPISODE_LINEAGE_MAX:]
    assert final['episode_lineage_dropped'] == 5
    assert final['goals'] == ['Cap import at 40 MW']
    assert final['units'] == {'power': 'MW'}
    assert sum(c['amount'] for c in final['budgets']['final_charges']) == total
    assert len(final['budgets']['final_charges']) == dc.EPISODE_LINEAGE_MAX + 5
    # Only the current episode stays resident: rotation never grows the cap.
    resident = [sid for sid in episodes if chat_service.get_session(sid) is not None]
    assert resident == [episodes[-1]]


def test_reconcile_list_is_bounded(monkeypatch):
    tid, view, session = _started_thread()
    eventlog.turn_start(session, 'many tools')
    eventlog.message(session, {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': f'toolu_{i}', 'name': 'set_component', 'input': {}}
        for i in range(dc.RECONCILE_MAX + 10)
    ]})
    eventlog.flush(session)
    _die_and_restart(monkeypatch)
    resumed, _ = dc.resume_episode(tid, generation=view['generation'])
    assert len(resumed['reconcile']) == dc.RECONCILE_MAX
    assert resumed['reconcile_dropped'] > 0
