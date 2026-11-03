"""Issue 09 PR2 — compatible provider-profile switching on a durable thread.

GK B conditions (d)-(f): a stale generation or an in-flight turn refuses and
writes nothing; an unknown or unconfigured profile is one typed refusal that
names no key or env var; a same-wire switch keeps the episode, a cross-wire
switch starts a child episode carrying only structured facts and budgets.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from harness import eventlog
from services import chat_service
from services import durable_conversation as dc
from services import llm_config
from services import utterance_admission as ua


OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'


def _profile(pid, wire, model='m-1'):
    return SimpleNamespace(id=pid, wire=wire, model=model)


SONNET = _profile('sonnet', 'anthropic', 'claude-sonnet')
OPUS = _profile('opus', 'anthropic', 'claude-opus')
GPT = _profile('gpt', 'openai', 'gpt-x')


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


def _bound_thread(profile=SONNET):
    tid = dc.create_thread()['thread_id']
    dc.checkpoint_thread(
        tid, generation=1, goals=['Keep curtailment under 5 %'],
        units={'energy': 'MWh'},
    )
    dc.reserve_budget(tid, key='turn-1', kind='tokens', amount=10, unit='tokens')
    dc.settle_budget(tid, key='turn-1', amount=7, unit='tokens')
    view, session = dc.switch_profile(tid, generation=1, profile=profile)
    return tid, view, session


def test_same_wire_switch_keeps_episode_and_history():
    tid, view, session = _bound_thread(SONNET)
    session.messages.append({'role': 'user', 'content': 'hello'})
    out, same = dc.switch_profile(tid, generation=view['generation'], profile=OPUS)
    assert same is session
    assert out['episode_id'] == session.session_id
    assert (same.profile_id, same.bound_wire, same.model) == ('opus', 'anthropic', 'claude-opus')
    assert list(same.messages) == [{'role': 'user', 'content': 'hello'}]
    assert out['generation'] == view['generation'] + 1
    last = out['profile_lineage'][-1]
    assert (last['profile_id'], last['wire'], last['carried']) == ('opus', 'anthropic', 'episode')


def test_cross_wire_switch_starts_child_with_facts_and_budgets_only():
    tid, view, session = _bound_thread(SONNET)
    session.messages.append({'role': 'assistant', 'content': [
        {'type': 'thinking', 'thinking': 'secret chain', 'signature': 'sig'},
        {'type': 'tool_use', 'id': 'toolu_1', 'name': 'get_network_summary', 'input': {}},
    ]})
    session.messages.append({'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'toolu_1', 'content': 'raw rows'},
    ]})
    out, child = dc.switch_profile(tid, generation=view['generation'], profile=GPT)

    assert child is not session
    assert out['episode_id'] == child.session_id
    assert chat_service.get_session(session.session_id) is None
    assert (child.profile_id, child.bound_wire, child.model) == ('gpt', 'openai', 'gpt-x')
    text = json.dumps(list(child.messages))
    for foreign in ('thinking', 'tool_use', 'tool_result', 'raw rows', 'secret chain'):
        assert foreign not in text
    assert out['goals'] == ['Keep curtailment under 5 %']
    assert out['units'] == {'energy': 'MWh'}
    assert [c['amount'] for c in out['budgets']['final_charges']] == [7]
    assert out['profile_lineage'][-1]['carried'] == 'facts_budgets'
    assert out['episode_lineage'][-1]['reason'] == 'profile_switch'


def test_stale_generation_refuses_and_writes_nothing():
    tid, view, session = _bound_thread(SONNET)
    before = dc.get_thread(tid)
    with pytest.raises(HTTPException) as failure:
        dc.switch_profile(tid, generation=view['generation'] - 1, profile=GPT)
    assert failure.value.status_code == 409
    assert failure.value.detail['error_kind'] == 'stale_generation'
    assert dc.get_thread(tid) == before
    assert session.profile_id == 'sonnet'


def test_switch_refuses_while_turn_in_flight():
    tid, view, session = _bound_thread(SONNET)
    before = dc.get_thread(tid)
    session._turn_in_flight = True
    for target in (OPUS, GPT):
        with pytest.raises(HTTPException) as failure:
            dc.switch_profile(tid, generation=view['generation'], profile=target)
        assert failure.value.detail['error_kind'] == 'turn_already_in_flight'
    assert dc.get_thread(tid) == before
    assert session.profile_id == 'sonnet'
    assert chat_service.get_session(session.session_id) is session


def test_profile_lineage_is_bounded():
    tid, view, _ = _bound_thread(SONNET)
    generation = view['generation']
    for index in range(dc.EPISODE_LINEAGE_MAX + 3):
        view, _ = dc.switch_profile(
            tid, generation=generation, profile=(OPUS if index % 2 else SONNET),
        )
        generation = view['generation']
    assert len(view['profile_lineage']) == dc.EPISODE_LINEAGE_MAX
    assert view['profile_lineage_dropped'] == 4


# ── HTTP: one typed refusal, no credential detail ───────────────────────────


@pytest.fixture
def http(client):
    created = client.post('/api/durable-conversations')
    assert created.status_code == 200, created.text
    return client, created.json()['thread_id']


def _post_profile(client, tid, profile_id, generation=1):
    return client.post(
        f'/api/durable-conversations/{tid}/profile',
        json={'generation': generation, 'profile_id': profile_id},
    )


def test_unknown_and_unconfigured_profiles_are_one_refusal(http, monkeypatch):
    client, tid = http
    real = llm_config.LLMProfile
    keyed = real(
        id='keyed', label='Keyed', preset='custom', wire='openai',
        base_url='https://example.invalid/v1', model='m', tools=True,
        vision=False, auth='bearer', fallback_model=None, max_output_tokens=None,
    )

    def resolve(pid):
        if pid == 'keyed':
            return keyed
        raise llm_config.ProfileNotConfiguredError(f'no configured profile {pid!r}')

    monkeypatch.setattr(llm_config, 'resolve_profile', resolve)
    if keyed.key_env:
        monkeypatch.delenv(keyed.key_env, raising=False)
    before = dc.get_thread(tid)
    unknown = _post_profile(client, tid, 'nope')
    unkeyed = _post_profile(client, tid, 'keyed')
    for response in (unknown, unkeyed):
        assert response.status_code == 422, response.text
        assert response.json()['detail']['error_kind'] == 'profile_unavailable'
    assert unknown.json() == unkeyed.json()
    body = unknown.text + unkeyed.text
    for leak in ('_API_KEY', 'key_env', 'nope', 'keyed', 'bearer'):
        assert leak not in body
    assert dc.get_thread(tid) == before


def test_http_switch_returns_thread_without_credentials(http, monkeypatch):
    client, tid = http
    real = llm_config.LLMProfile
    keyless = real(
        id='local', label='Local', preset='custom', wire='openai',
        base_url='http://127.0.0.1:11434/v1', model='llama', tools=True,
        vision=False, auth='none', fallback_model=None, max_output_tokens=None,
    )
    monkeypatch.setattr(llm_config, 'resolve_profile', lambda pid: keyless)
    response = _post_profile(client, tid, 'local')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['profile_lineage'][-1]['profile_id'] == 'local'
    assert 'key' not in json.dumps(body['profile_lineage']).lower()
    stale = _post_profile(client, tid, 'local', generation=1)
    assert stale.status_code == 409
    assert stale.json()['detail']['error_kind'] == 'stale_generation'


def test_same_wire_switch_rechecks_in_flight_under_session_lock(monkeypatch):
    """A turn that starts after the first in-flight check must still refuse."""
    tid, view, session = _bound_thread(SONNET)
    before = dc.get_thread(tid)
    real_live_episode = dc._live_episode

    def turn_starts_right_after(thread):
        live = real_live_episode(thread)
        with live._lock:
            live._turn_in_flight = True  # the loop sets it under this lock
        return live

    monkeypatch.setattr(dc, '_live_episode', turn_starts_right_after)
    with pytest.raises(HTTPException) as failure:
        dc.switch_profile(tid, generation=view['generation'], profile=OPUS)
    assert failure.value.detail['error_kind'] == 'turn_already_in_flight'
    assert dc.get_thread(tid) == before
    assert (session.profile_id, session.bound_wire, session.model) == (
        'sonnet', 'anthropic', 'claude-sonnet',
    )
