"""Issue 11 / D-I1 — foreground solve hands off without holding the turn lease."""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from services import chat_service, chat_tools, utterance_admission as ua
from services.pypsa_service import PyPSAService
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


def _hold_solver_running(install_network, monkeypatch, hold_seconds: float = 1.5):
    """Start path returns started; status stays running until a delayed flip.

    Installs a real BufferedLogQueue so solver_log_bridge does not short-circuit
    on a missing queue (that path already returns immediately today).
    """
    import pypsa
    from routers.simulation import BufferedLogQueue

    n = pypsa.Network()
    n.add('Bus', 'B1')
    n.add('Generator', 'G1', bus='B1', p_nom=10, marginal_cost=1)
    n.add('Load', 'L1', bus='B1', p_set=5)
    install_network(n, name=None)
    ctx = PyPSAService.get_active_context()
    log_queue = BufferedLogQueue(maxlen=100)

    def fake_run():
        with ctx.solver_state_lock:
            ctx.solver_state['status'] = 'running'
            ctx.solver_state['kind'] = 'lopf'
            ctx.solver_state['objective'] = None
            ctx.solver_state['log_queue'] = log_queue
        return {'status': 'started'}

    monkeypatch.setattr('routers.simulation.run', fake_run)
    monkeypatch.setattr(chat_tools, 'run_simulation', fake_run)

    done = threading.Event()

    def finish_later():
        time.sleep(hold_seconds)
        with ctx.solver_state_lock:
            ctx.solver_state['status'] = 'completed'
            ctx.solver_state['objective'] = 1.0
        done.set()

    worker = threading.Thread(target=finish_later, daemon=True)
    worker.start()
    return ctx, worker, done


def test_run_simulation_tool_result_is_started_not_completed(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(install_network, monkeypatch)
    session = chat_service.ChatSession()
    collector: list = []
    t0 = time.monotonic()
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        collector,
    ))
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f'dispatch waited on solver ({elapsed:.2f}s)'
    results = [p for ev, p in frames if ev == 'tool_result']
    assert results
    payload = results[0]
    # Prefer structured result; fall back to string search on the frame.
    result = payload.get('result', payload)
    if isinstance(result, dict):
        assert result.get('status') == 'started'
        assert result.get('running') is True
        assert 'final_status' not in result
    else:
        assert 'started' in str(result)
        assert 'final_status' not in str(result)
    with ctx.solver_state_lock:
        assert ctx.solver_state['status'] == 'running'
    worker.join(timeout=3)
    assert done.is_set()


def test_dispatch_returns_before_slow_solver_finishes(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(
        install_network, monkeypatch, hold_seconds=2.0,
    )
    session = chat_service.ChatSession()
    collector: list = []
    list(chat_service._dispatch_real_tool_call(
        session,
        {'id': uuid.uuid4().hex, 'name': 'run_simulation', 'input': {}},
        collector,
    ))
    status = chat_tools.get_simulation_status()
    assert status.get('running') is True or status.get('status') == 'running'
    with ctx.solver_state_lock:
        assert ctx.solver_state['status'] == 'running'
    worker.join(timeout=4)
    assert done.is_set()


def test_slow_solve_releases_turn_so_read_answers_before_completion(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.confirm as harness_confirm

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(
        install_network, monkeypatch, hold_seconds=2.0,
    )

    session = chat_service.ChatSession()
    turn_a = [
        _tool_use_event('tu-sim', 'run_simulation', {}),
    ]
    final_a = _FakeFinalMessage(
        content=[_tool_use_block('tu-sim', 'run_simulation', {})],
        usage=_FakeUsage(),
    )
    # After tool result the model ends the turn.
    turn_a2 = [_text_event('Solve started.')]
    final_a2 = _FakeFinalMessage(
        content=[_text_block('Solve started.')], usage=_FakeUsage(),
    )
    client_a = FakeAnthropicClient([(turn_a, final_a), (turn_a2, final_a2)])
    list(chat_service.run_turn(
        session, 'run the lopf',
        client=client_a,
        utterance_id='utt-solve',
        generation=1,
    ))
    assert ua._DEFAULT.active_lease(session.session_id) is None
    with session._lock:
        assert session._turn_in_flight is False
    with ctx.solver_state_lock:
        assert ctx.solver_state['status'] == 'running'

    # Turn B: a read while the worker is still running.
    turn_b = [
        _tool_use_event('tu-st', 'get_simulation_status', {}),
    ]
    final_b = _FakeFinalMessage(
        content=[_tool_use_block('tu-st', 'get_simulation_status', {})],
        usage=_FakeUsage(),
    )
    turn_b2 = [_text_event('Still solving.')]
    final_b2 = _FakeFinalMessage(
        content=[_text_block('Still solving.')], usage=_FakeUsage(),
    )
    client_b = FakeAnthropicClient([(turn_b, final_b), (turn_b2, final_b2)])
    events_b = list(chat_service.run_turn(
        session, 'is it done?',
        client=client_b,
        utterance_id='utt-read',
        generation=1,
    ))
    assert client_b.calls
    assert any(ev == 'token' or ev == 'turn_done' for ev, _ in events_b)
    assert not done.is_set(), 'read finished before the slow worker completed'
    with ctx.solver_state_lock:
        assert ctx.solver_state['status'] == 'running'

    worker.join(timeout=4)
    assert done.is_set()
    with ctx.solver_state_lock:
        assert ctx.solver_state['status'] == 'completed'
