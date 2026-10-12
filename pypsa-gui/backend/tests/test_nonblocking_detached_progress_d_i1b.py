"""Issue 11 — detached solver progress after foreground handoff."""
from __future__ import annotations

import time

import pytest

from services import chat_service, utterance_admission as ua
from tests.test_nonblocking_foreground_solve_d_i1 import _hold_solver_running


@pytest.fixture(autouse=True)
def _reset_admission():
    ua._reset_for_tests()
    yield
    ua._reset_for_tests()


def test_handoff_starts_detached_monitor_without_awaiting_bridge(
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
    t0 = time.monotonic()
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        collector,
    ))
    assert time.monotonic() - t0 < 1.0
    result = next(p['result'] for ev, p in frames if ev == 'tool_result')
    assert result['status'] == 'started'
    assert result.get('detached_progress') is True
    assert 'final_status' not in result
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not session.detached_monitor_active('tu-sim'):
        time.sleep(0.02)
    assert session.detached_monitor_active('tu-sim')
    worker.join(timeout=4)
    assert done.is_set()


def test_detached_monitor_collects_phase_lines_after_turn_returns(
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
    list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        [],
    ))
    # Wait until the detached thread has subscribed.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not session.detached_monitor_active('tu-sim'):
        time.sleep(0.02)
    assert session.detached_monitor_active('tu-sim')

    with ctx.solver_state_lock:
        q = ctx.solver_state['log_queue']
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with q._sub_lock:
            if q._subscribers:
                break
        time.sleep(0.01)
    else:
        raise AssertionError('detached monitor never subscribed to log_queue')
    q.put('[PHASE] Optimising')
    q.put('[VALIDATION] ok')

    collected: list[dict] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(collected) < 2:
        collected.extend(session.drain_detached_progress())
        time.sleep(0.05)
    kinds = {p['kind'] for p in collected}
    assert 'PHASE' in kinds
    assert 'VALIDATION' in kinds
    assert all(p['tool_use_id'] == 'tu-sim' for p in collected)

    worker.join(timeout=4)
    assert done.is_set()
    # Monitor should go inactive after terminal status.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and session.detached_monitor_active('tu-sim'):
        time.sleep(0.05)
    assert not session.detached_monitor_active('tu-sim')


def test_read_turn_answers_while_detached_progress_still_flows(
    tmp_projects_dir, install_network, monkeypatch,
):
    import harness.confirm as harness_confirm
    from tests.test_chat_e2e import (
        FakeAnthropicClient,
        _FakeFinalMessage,
        _FakeUsage,
        _text_block,
        _text_event,
        _tool_use_block,
        _tool_use_event,
    )

    monkeypatch.setattr(
        harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}),
    )
    ctx, worker, done = _hold_solver_running(
        install_network, monkeypatch, hold_seconds=2.0,
    )
    session = chat_service.ChatSession()
    list(chat_service._dispatch_real_tool_call(
        session,
        {'id': 'tu-sim', 'name': 'run_simulation', 'input': {}},
        [],
    ))
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not session.detached_monitor_active('tu-sim'):
        time.sleep(0.02)

    with ctx.solver_state_lock:
        ctx.solver_state['log_queue'].put('[PHASE] still going')

    turn_b = [_tool_use_event('tu-st', 'get_simulation_status', {})]
    final_b = _FakeFinalMessage(
        content=[_tool_use_block('tu-st', 'get_simulation_status', {})],
        usage=_FakeUsage(),
    )
    turn_b2 = [_text_event('Still solving.')]
    final_b2 = _FakeFinalMessage(
        content=[_text_block('Still solving.')], usage=_FakeUsage(),
    )
    client_b = FakeAnthropicClient([(turn_b, final_b), (turn_b2, final_b2)])
    events = list(chat_service.run_turn(
        session, 'status?', client=client_b,
        utterance_id='read-1', generation=1,
    ))
    assert any(ev in ('token', 'turn_done') for ev, _ in events)
    assert not done.is_set()
    assert session.detached_monitor_active('tu-sim')

    worker.join(timeout=4)
    assert done.is_set()
