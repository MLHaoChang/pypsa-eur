"""End-to-end verification for Track A (A4–A8) through ``run_turn``.

Uses the same FakeAnthropicClient harness as ``test_chat_e2e.py`` so each
item is exercised via the real turn loop (system prompt, tool dispatch,
history trim, SSE events) — not helper-only unit checks.
"""

from __future__ import annotations

import threading

import pypsa
import pytest

from services import chat_service, chat_tools
from services.project_context import ProjectContext
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _RaiseNThenSucceedClient,
    _install_fake_anthropic_module,
    _text_block,
    _text_event,
    _tool_use_block,
    _tool_use_event,
    _zero_retry_delays,
)


@pytest.fixture
def fake_anthropic_module():
    import sys

    prev = sys.modules.get("anthropic")
    mod = _install_fake_anthropic_module({})
    yield mod
    if prev is None:
        sys.modules.pop("anthropic", None)
    else:
        sys.modules["anthropic"] = prev


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def _text_only_turn(text: str = "ok"):
    return (
        [_text_event(text)],
        _FakeFinalMessage(
            content=[_text_block(text)],
            usage=_FakeUsage(input_tokens=5, output_tokens=3),
        ),
    )


def _system_text(call_kwargs: dict) -> str:
    system = call_kwargs.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        return "\n".join(parts)
    return str(system or "")


def test_e2e_a4_live_network_meta_reaches_anthropic_system(
    tmp_projects_dir, install_network,
):
    """A4: bound project meta is present in the SDK system prompt."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, s_nom=100)
    install_network(n, name="E2EMeta")

    session = chat_service.ChatSession()
    client = FakeAnthropicClient([_text_only_turn("oriented")])
    events = list(chat_service.run_turn(session, "ping", client=client))

    assert any(e == "turn_done" for e, _ in events)
    assert client.calls, "expected at least one Anthropic stream call"
    system = _system_text(client.calls[0])
    assert "Working with E2EMeta:" in system
    assert "2 buses" in system
    assert "1 lines" in system


def test_e2e_a7_per_turn_tool_result_budget_omits_later_tools(
    tmp_projects_dir, install_network, monkeypatch,
):
    """A7: after the per-step char budget fills, later tool_results are stubs."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="E2EBudget")

    monkeypatch.setattr(chat_service, "MAX_TOOL_RESULT_CHARS_PER_TURN", 80)
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "get_meta",
        lambda: "X" * 60,
    )

    # Three parallel read-tier tools in one assistant step share one budget.
    turn1_events = [
        _tool_use_event("tu-a", "get_meta", {}),
        _tool_use_event("tu-b", "get_meta", {}),
        _tool_use_event("tu-c", "get_meta", {}),
    ]
    turn1_final = _FakeFinalMessage(
        content=[
            _tool_use_block("tu-a", "get_meta", {}),
            _tool_use_block("tu-b", "get_meta", {}),
            _tool_use_block("tu-c", "get_meta", {}),
        ],
        usage=_FakeUsage(input_tokens=20, output_tokens=10),
    )
    client = FakeAnthropicClient([
        (turn1_events, turn1_final),
        _text_only_turn("summarized"),
    ])
    session = chat_service.ChatSession()
    events = list(chat_service.run_turn(session, "meta please", client=client))

    assert any(e == "turn_done" for e, _ in events)
    assert len(client.calls) >= 2
    # Second model call carries the three tool_result blocks.
    tool_results = []
    for msg in client.calls[1]["messages"]:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append(str(block.get("content") or ""))
    assert len(tool_results) == 3
    omitted = [c for c in tool_results if "per_turn_tool_result_budget" in c]
    assert omitted, f"expected at least one omitted stub; got {tool_results!r}"
    assert any("XXXX" in c or "<untrusted_data>" in c for c in tool_results)


def test_e2e_a6_pairing_trim_then_run_turn_completes(
    tmp_projects_dir, install_network, monkeypatch,
):
    """A6: over-cap history trims in pairs; a follow-up turn still completes."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="E2ETrim")

    monkeypatch.setattr(chat_service, "SESSION_MESSAGES_MAX", 10)
    session = chat_service.ChatSession()
    for i in range(30):
        session.append_history_message({"role": "user", "content": f"u{i}"})
        session.append_history_message({
            "role": "assistant",
            "content": [{"type": "text", "text": f"a{i}"}],
        })
    assert len(session.messages) <= 10
    assert session.messages[0]["role"] == "user"

    client = FakeAnthropicClient([_text_only_turn("still coherent")])
    events = list(chat_service.run_turn(session, "continue", client=client))
    assert any(e == "turn_done" for e, _ in events)
    # History still starts on a user message (pairing invariant).
    assert session.messages[0]["role"] == "user"
    # The Anthropic call saw prior trimmed history + the new user message.
    roles = [m["role"] for m in client.calls[0]["messages"]]
    assert roles[0] == "user"
    assert roles.count("user") >= 1
    assert "assistant" in roles


def test_e2e_a8_opus_rate_limit_falls_back_through_run_turn(
    tmp_projects_dir, fake_anthropic_module, install_network, monkeypatch,
):
    """A8: exhausted Opus rate_limited retries → model_fallback + Sonnet success."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="E2EFallback")
    _zero_retry_delays(monkeypatch)

    session = chat_service.ChatSession(model=chat_service.OPUS_MODEL)
    client = _RaiseNThenSucceedClient(
        chat_service.MAX_STREAM_RETRIES + 1,
        fake_anthropic_module.RateLimitError("opus busy"),
        _text_only_turn("ok on sonnet"),
    )
    events = list(chat_service.run_turn(session, "hi", client=client))
    names = [e for e, _ in events]
    assert "model_fallback" in names
    assert "error" not in names
    assert "turn_done" in names or "token" in names
    fb = next(p for e, p in events if e == "model_fallback")
    assert fb["from_model"] == chat_service.OPUS_MODEL
    assert fb["to_model"] == chat_service.DEFAULT_MODEL
    assert session.model == chat_service.DEFAULT_MODEL
    assert client.calls[-1]["model"] == chat_service.DEFAULT_MODEL


def test_e2e_a5_read_all_turns_locked_during_append(
    tmp_projects_dir, monkeypatch,
):
    """A5 smoke: concurrent read_all_turns during append_turn stays well-formed.

    Full race coverage lives in ``test_chat_rotation_race.py``; this asserts
    the e2e path used by /history (read_all_turns) under load still returns
    dict turns while writers append.
    """
    from routers import projects as projects_router

    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    name = "E2ELock"
    (tmp_projects_dir / name).mkdir(parents=True, exist_ok=True)
    ctx = ProjectContext(network=pypsa.Network(), loaded_project=name)
    chat_service.get_persist_path(ctx)

    stop = threading.Event()
    errors: list[str] = []

    def writer() -> None:
        for i in range(25):
            chat_service.append_turn(ctx, {"i": i, "user": f"u{i}", "assistant": []})
        stop.set()

    def reader() -> None:
        while not stop.is_set():
            turns = chat_service.read_all_turns(ctx)
            for rec in turns:
                if not isinstance(rec, dict):
                    errors.append(f"non-dict: {rec!r}")
                    return

    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reader)
    t_r.start()
    t_w.start()
    t_w.join(timeout=20.0)
    stop.set()
    t_r.join(timeout=20.0)
    assert not errors, errors
    final = chat_service.read_all_turns(ctx)
    assert len(final) >= 1
    assert all(isinstance(r, dict) and "i" in r for r in final)
