"""Issue 02 — generation-aware result projection (GK B V1–V4).

A live turn admitted at generation G stops projecting results once a rebind
bumps the session to G+1: no further tool dispatch, no next model call, no
text deltas and no persisted assistant answer. Tool results already produced
stay in history as facts. A typed turn (no utterance ids) is untouched.
"""
from __future__ import annotations

import pytest

from services import chat_service, chat_tools, utterance_admission as ua
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _text_block,
    _text_event,
    _tool_use_block,
    _tool_use_event,
)


@pytest.fixture(autouse=True)
def _reset_admission():
    ua._reset_for_tests()
    yield
    ua._reset_for_tests()


@pytest.fixture
def network(tmp_projects_dir, install_network):
    import pypsa

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)
    return n


def _tool_turn(*ids):
    args = {'component_class': 'Bus'}
    return (
        [_tool_use_event(i, 'list_components', args) for i in ids],
        _FakeFinalMessage(
            content=[_tool_use_block(i, 'list_components', args) for i in ids],
            usage=_FakeUsage(),
        ),
    )


def _text_turn(text):
    return (
        [_text_event(text)],
        _FakeFinalMessage(content=[_text_block(text)], usage=_FakeUsage()),
    )


class _Reader:
    """Stands in for list_components; runs `during` on its first call."""

    def __init__(self, during=None):
        self.calls = 0
        self.during = during

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1 and self.during is not None:
            self.during()
        return {'components': ['B1']}


def _bump(session):
    return lambda: ua._DEFAULT.bump_generation(session_id=session.session_id)


def _kinds(events):
    return [p.get('error_kind') for ev, p in events if ev == 'error']


def _tool_results(session):
    return {
        b['tool_use_id']: b
        for m in session.messages
        if m.get('role') == 'user' and isinstance(m.get('content'), list)
        for b in m['content']
        if isinstance(b, dict) and b.get('type') == 'tool_result'
    }


def _assistant_text(session):
    return [
        b.get('text')
        for m in session.messages
        if m.get('role') == 'assistant'
        for b in (m.get('content') or [])
        if isinstance(b, dict) and b.get('type') == 'text'
    ]


def _live(session, client, text='list the buses', uid='u1', gen=1):
    return list(chat_service.run_turn(
        session, text, client=client, utterance_id=uid, generation=gen,
    ))


def test_bump_while_a_tool_runs_keeps_its_result_and_stops(network, monkeypatch):
    session = chat_service.ChatSession()
    reader = _Reader(during=_bump(session))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = FakeAnthropicClient([_tool_turn('tu-1'), _text_turn('one bus.')])

    events = _live(session, client)

    assert reader.calls == 1
    assert len(client.calls) == 1  # no next model call
    assert _kinds(events) == ['obsolete_generation']
    assert ('session_done', {'reason': 'obsolete_generation'}) in events
    assert not any(ev in ('turn_done', 'token') for ev, _ in events)
    results = _tool_results(session)
    assert not results['tu-1'].get('is_error')  # the executed fact stays
    assert 'one bus.' not in _assistant_text(session)


def test_a_later_tool_in_the_same_step_is_not_dispatched(network, monkeypatch):
    session = chat_service.ChatSession()
    reader = _Reader(during=_bump(session))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = FakeAnthropicClient([_tool_turn('tu-1', 'tu-2')])

    events = _live(session, client)

    assert reader.calls == 1
    results = _tool_results(session)
    assert not results['tu-1'].get('is_error')
    # Pairing kept: the undispatched call gets an error result.
    assert results['tu-2']['is_error'] is True
    assert results['tu-2']['content'] == 'obsolete_generation'
    assert _kinds(events) == ['obsolete_generation']


class _BumpingClient(FakeAnthropicClient):
    """Bumps the generation as the given (1-based) model call starts."""

    def __init__(self, turns, session, at_call):
        super().__init__(turns)
        self._session = session
        self._at = at_call

    def _next_turn(self, **kwargs):
        if len(self.calls) + 1 == self._at:
            ua._DEFAULT.bump_generation(session_id=self._session.session_id)
        return super()._next_turn(**kwargs)


def test_bump_during_the_answer_stream_sends_and_persists_no_text(network):
    session = chat_service.ChatSession()
    client = _BumpingClient([_text_turn('stale answer')], session, at_call=1)

    events = _live(session, client)

    assert not any(ev in ('token', 'turn_done') for ev, _ in events)
    assert _kinds(events) == ['obsolete_generation']
    assert 'stale answer' not in _assistant_text(session)


def test_bump_during_a_tool_step_runs_none_of_its_tools(network, monkeypatch):
    session = chat_service.ChatSession()
    reader = _Reader()
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = _BumpingClient([_tool_turn('tu-1')], session, at_call=1)

    events = _live(session, client)

    assert reader.calls == 0
    assert _kinds(events) == ['obsolete_generation']
    assert 'tu-1' not in _tool_results(session)


def test_an_unreadable_generation_fails_closed(network, monkeypatch):
    session = chat_service.ChatSession()
    reader = _Reader()
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = FakeAnthropicClient([_tool_turn('tu-1'), _text_turn('done.')])

    def broken(**_kwargs):
        raise RuntimeError('generation store unreadable')

    monkeypatch.setattr(ua._DEFAULT, 'project_result', broken)
    events = _live(session, client)

    assert reader.calls == 0 and not client.calls
    assert _kinds(events) == ['obsolete_generation']
    assert not any(ev == 'turn_done' for ev, _ in events)


def test_same_generation_live_turn_is_unchanged(network, monkeypatch):
    session = chat_service.ChatSession()
    reader = _Reader()
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = FakeAnthropicClient([_tool_turn('tu-1'), _text_turn('one bus.')])

    events = _live(session, client)

    assert reader.calls == 1 and len(client.calls) == 2
    assert not _kinds(events)
    assert any(ev == 'turn_done' for ev, _ in events)
    assert 'one bus.' in _assistant_text(session)


def test_typed_turn_ignores_a_bump(network, monkeypatch):
    session = chat_service.ChatSession()
    reader = _Reader(during=_bump(session))
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = FakeAnthropicClient([_tool_turn('tu-1'), _text_turn('one bus.')])

    events = list(chat_service.run_turn(session, 'list the buses', client=client))

    assert reader.calls == 1 and len(client.calls) == 2
    assert not _kinds(events)
    assert any(ev == 'turn_done' for ev, _ in events)


def test_new_generation_utterance_drains_once_after_the_obsolete_turn(
    network, monkeypatch,
):
    session = chat_service.ChatSession()
    sid = session.session_id
    queued: list = []

    def rebind_then_speak():
        new_gen = ua._DEFAULT.bump_generation(session_id=sid)
        queued.append(ua.admit_utterance(ua.StableUtterance(
            utterance_id='u2', text='and the lines?', generation=new_gen,
            session_id=sid,
        )))

    reader = _Reader(during=rebind_then_speak)
    monkeypatch.setitem(chat_tools.DISPATCHERS, 'list_components', reader)
    client = FakeAnthropicClient([_tool_turn('tu-1'), _text_turn('no lines.')])

    events = _live(session, client)

    # Queued behind the still-running obsolete turn, not admitted beside it.
    assert [d.status for d in queued] == ['queued']
    assert _kinds(events) == ['obsolete_generation']
    # The drain ran once, under the new generation, with its own model call.
    assert len(client.calls) == 2
    assert [ev for ev, _ in events].count('turn_done') == 1
    assert _assistant_text(session).count('no lines.') == 1
    assert ua._DEFAULT.active_lease(sid) is None
    assert ua._DEFAULT.pending_utterance_id(sid) is None


def test_bump_keeps_a_running_lease_and_drops_old_pending():
    coord = ua.TurnCoordinator()
    first = coord.admit_utterance(ua.StableUtterance('u1', 'a', 1, 's'))
    coord.admit_utterance(ua.StableUtterance('u2', 'b', 1, 's'))
    assert coord.bump_generation(session_id='s') == 2
    assert coord.active_lease('s') == first.lease_id
    assert coord.pending_utterance_id('s') is None
    assert coord.project_result(
        session_id='s', lease_id=first.lease_id, generation=1,
    ) == 'obsolete'
    nxt = coord.admit_utterance(ua.StableUtterance('u3', 'c', 2, 's'))
    assert nxt.status == 'queued'
    promoted = coord.release_lease(
        session_id='s', lease_id=first.lease_id, generation=1,
    )
    assert promoted.status == 'admitted' and promoted.generation == 2
    assert coord.active_utterance('s').utterance_id == 'u3'


def test_releasing_an_obsolete_lease_without_promotion_keeps_pending():
    coord = ua.TurnCoordinator()
    first = coord.admit_utterance(ua.StableUtterance('u1', 'a', 1, 's'))
    coord.bump_generation(session_id='s')
    coord.admit_utterance(ua.StableUtterance('u2', 'b', 2, 's'))
    assert coord.release_lease(
        session_id='s', lease_id=first.lease_id, generation=1, promote=False,
    ) is None
    assert coord.active_lease('s') is None
    assert coord.pending_utterance_id('s') == 'u2'
