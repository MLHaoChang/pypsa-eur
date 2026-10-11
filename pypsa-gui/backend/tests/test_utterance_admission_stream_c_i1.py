"""Issue 02 / C-I1 — optional utterance fields gate run_turn via admission."""
from __future__ import annotations

import threading
import time

import pytest

from services import chat_service, utterance_admission as ua
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


def _happy_client():
    return FakeAnthropicClient([(
        [_text_event('ok')],
        _FakeFinalMessage(
            content=[_text_block('ok')],
            usage=_FakeUsage(input_tokens=1, output_tokens=1),
        ),
    )])


def test_omitted_utterance_fields_preserve_old_behavior(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.session as harness_session
    import pypsa

    monkeypatch.setattr(harness_session, 'CONFIRMATION_TTL_SECONDS', 5.0)
    n = pypsa.Network()
    n.add('Bus', 'B1')
    n.add('Bus', 'B2')
    install_network(n, name=None)

    session = chat_service.ChatSession()
    turn1_events = [
        _tool_use_event('tu-d', 'delete_component',
                        {'component_class': 'Bus', 'name': 'B2'}),
    ]
    turn1_final = _FakeFinalMessage(
        content=[_tool_use_block('tu-d', 'delete_component',
                                 {'component_class': 'Bus', 'name': 'B2'})],
        usage=_FakeUsage(),
    )
    turn2_events = [_text_event('deleted.')]
    turn2_final = _FakeFinalMessage(
        content=[_text_block('deleted.')], usage=_FakeUsage(),
    )
    client1 = FakeAnthropicClient([
        (turn1_events, turn1_final), (turn2_events, turn2_final),
    ])

    streamed1: list = []
    done1 = threading.Event()

    def _run1():
        for ev in chat_service.run_turn(session, 'delete B2', client=client1):
            streamed1.append(ev)
        done1.set()

    t = threading.Thread(target=_run1)
    t.start()
    deadline = time.monotonic() + 3.0
    token = None
    while time.monotonic() < deadline:
        with session._lock:
            if session._turn_in_flight and session.pending_confirmations:
                token = next(iter(session.pending_confirmations))
                break
        time.sleep(0.02)
    assert token

    client2 = FakeAnthropicClient([])
    events2 = list(chat_service.run_turn(session, 'another', client=client2))
    kinds2 = [p.get('error_kind') for ev, p in events2 if ev == 'error']
    assert 'turn_already_in_flight' in kinds2
    assert not client2.calls

    session.record_decision(token, 'approve')
    done1.wait(5.0)
    t.join()


def test_admitted_utterance_starts_turn_and_releases_lease(
    tmp_projects_dir, install_network,
):
    import pypsa

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)

    session = chat_service.ChatSession()
    client = _happy_client()
    events = list(chat_service.run_turn(
        session, 'Limit battery to 7.5 MW',
        client=client,
        utterance_id='u1',
        generation=1,
    ))
    assert any(ev == 'token' or ev == 'turn_done' for ev, _ in events)
    assert client.calls
    assert ua._DEFAULT.active_lease(session.session_id) is None
    # A fresh id can admit again after release.
    again = list(chat_service.run_turn(
        session, 'next',
        client=_happy_client(),
        utterance_id='u2',
        generation=1,
    ))
    assert any(ev == 'turn_done' or ev == 'token' for ev, _ in again)


def test_duplicate_utterance_id_does_not_start_turn(
    tmp_projects_dir, install_network,
):
    import pypsa

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)

    session = chat_service.ChatSession()
    list(chat_service.run_turn(
        session, 'first',
        client=_happy_client(),
        utterance_id='u1',
        generation=1,
    ))
    client2 = FakeAnthropicClient([])
    events = list(chat_service.run_turn(
        session, 'replay',
        client=client2,
        utterance_id='u1',
        generation=1,
    ))
    kinds = [p.get('error_kind') for ev, p in events if ev == 'error']
    reasons = [p.get('reason') for ev, p in events if ev == 'session_done']
    assert kinds == ['duplicate']
    assert reasons == ['duplicate']
    assert not client2.calls
    with session._lock:
        assert session._turn_in_flight is False


def test_stale_generation_does_not_start_turn(tmp_projects_dir, install_network):
    import pypsa

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)

    session = chat_service.ChatSession()
    ua._DEFAULT.bump_generation(session_id=session.session_id)
    client = FakeAnthropicClient([])
    events = list(chat_service.run_turn(
        session, 'hello',
        client=client,
        utterance_id='u1',
        generation=1,
    ))
    kinds = [p.get('error_kind') for ev, p in events if ev == 'error']
    assert kinds == ['stale_generation']
    assert not client.calls


def test_queued_while_lease_held_does_not_start_turn(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.session as harness_session
    import pypsa

    monkeypatch.setattr(harness_session, 'CONFIRMATION_TTL_SECONDS', 5.0)
    n = pypsa.Network()
    n.add('Bus', 'B1')
    n.add('Bus', 'B2')
    install_network(n, name=None)

    session = chat_service.ChatSession()
    turn1_events = [
        _tool_use_event('tu-d', 'delete_component',
                        {'component_class': 'Bus', 'name': 'B2'}),
    ]
    turn1_final = _FakeFinalMessage(
        content=[_tool_use_block('tu-d', 'delete_component',
                                 {'component_class': 'Bus', 'name': 'B2'})],
        usage=_FakeUsage(),
    )
    turn2_events = [_text_event('deleted.')]
    turn2_final = _FakeFinalMessage(
        content=[_text_block('deleted.')], usage=_FakeUsage(),
    )
    client1 = FakeAnthropicClient([
        (turn1_events, turn1_final), (turn2_events, turn2_final),
    ])

    done1 = threading.Event()

    def _run1():
        list(chat_service.run_turn(
            session, 'delete B2',
            client=client1,
            utterance_id='u1',
            generation=1,
        ))
        done1.set()

    t = threading.Thread(target=_run1)
    t.start()
    deadline = time.monotonic() + 3.0
    token = None
    while time.monotonic() < deadline:
        with session._lock:
            if session._turn_in_flight and session.pending_confirmations:
                token = next(iter(session.pending_confirmations))
                break
        time.sleep(0.02)
    assert token
    assert ua._DEFAULT.active_lease(session.session_id)

    client2 = FakeAnthropicClient([])
    events2 = list(chat_service.run_turn(
        session, 'correction',
        client=client2,
        utterance_id='u2',
        generation=1,
    ))
    kinds2 = [p.get('error_kind') for ev, p in events2 if ev == 'error']
    reasons2 = [p.get('reason') for ev, p in events2 if ev == 'session_done']
    assert kinds2 == ['queued']
    assert reasons2 == ['queued']
    assert 'turn_already_in_flight' not in kinds2
    assert not client2.calls
    assert ua._DEFAULT.pending_utterance_id(session.session_id) == 'u2'

    session.record_decision(token, 'approve')
    done1.wait(5.0)
    t.join()


def test_blank_text_with_utterance_fields_is_rejected_empty(
    tmp_projects_dir, install_network,
):
    import pypsa

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)

    session = chat_service.ChatSession()
    client = FakeAnthropicClient([])
    events = list(chat_service.run_turn(
        session, '   ',
        client=client,
        utterance_id='u-empty',
        generation=1,
    ))
    kinds = [p.get('error_kind') for ev, p in events if ev == 'error']
    assert kinds == ['rejected_empty']
    assert not client.calls
