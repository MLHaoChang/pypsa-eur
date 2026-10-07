"""
A turn that does not finish leaves a record — for display, never for replay.

`chat.jsonl` was written only when a turn ended with `turn_done`. A turn ended
by an abort, the tool-call cap, a mid-turn project switch, a stream error or a
client disconnect left nothing, although its tools ran, so a reload showed a
conversation without the turn that changed the network.

Such a turn is now recorded with `interrupted: true` and the reason it ended.
Its assistant half is what the user SAW (streamed text, and the names of the
tools it asked for), not provider messages. GET /history returns it for the
panel to show and skips it when it rebuilds the session's history: the step it
stopped in can end on a `tool_use` with no `tool_result`, which would make every
later turn of the session invalid to the provider.

Also here: each record now carries `turn_usage`, the turn's own spend. `usage`
is the session's running total, so the daily cap summed turn 1 once per later
turn of its session — three 30-token turns read as 180.
"""
from __future__ import annotations

import time

import pypsa
import pytest

from harness import budget as harness_budget
from harness import history as harness_history
from routers import chat as chat_router
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.test_chat_e2e import (
    FakeAnthropicClient, _FakeFinalMessage, _FakeUsage, _text_block,
    _text_event, _tool_use_block, _tool_use_event,
)


@pytest.fixture
def project(tmp_projects_dir, install_network):
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="Rec")
    chat_service._reset_sessions_for_tests()
    yield PyPSAService.get_active_context()
    chat_service._reset_sessions_for_tests()


def _records(ctx) -> list[dict]:
    return harness_history.read_all_turns(ctx)


def _text_turn(text: str = "ok") -> FakeAnthropicClient:
    return FakeAnthropicClient([
        ([_text_event(text)],
         _FakeFinalMessage(content=[_text_block(text)], usage=_FakeUsage())),
    ])


def _tool_then_text(tool: str, args: dict, text: str = "never sent"):
    """One step that streams text and calls `tool`, then a closing text step."""
    return FakeAnthropicClient([
        ([_text_event("Looking. "), _tool_use_event("tu-1", tool, args)],
         _FakeFinalMessage(content=[_text_block("Looking. "),
                                    _tool_use_block("tu-1", tool, args)],
                           usage=_FakeUsage())),
        ([_text_event(text)],
         _FakeFinalMessage(content=[_text_block(text)], usage=_FakeUsage())),
    ])


# ── The record ───────────────────────────────────────────────────────────────


def test_an_aborted_turn_is_recorded_with_what_it_showed(project, monkeypatch):
    session = chat_service.ChatSession()
    real = chat_tools.DISPATCHERS["list_components"]

    def _list_then_abort(**kwargs):
        session.abort_event.set()  # the user pressed Stop while it ran
        return real(**kwargs)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", _list_then_abort)

    events = list(chat_service.run_turn(
        session, "list buses", client=_tool_then_text(
            "list_components", {"component_class": "Bus"})))

    assert ("session_done", {"reason": "aborted"}) in events
    (rec,) = _records(project)
    assert rec["interrupted"] is True
    assert rec["interrupted_reason"] == "aborted"
    assert rec["user"] == "list buses"
    assert rec["session_id"] == session.session_id
    assert rec["assistant"] == [
        {"type": "text", "text": "Looking. "},
        {"type": "tool_use", "name": "list_components"},
    ], "the record is what the user saw, not the provider's blocks"
    assert rec["turn_usage"]["output_tokens"] == 20


def test_a_client_disconnect_mid_stream_is_recorded(project):
    gen = chat_service.run_turn(
        chat_service.ChatSession(), "hello", client=_text_turn("half an answer"))
    for event, _payload in gen:
        if event == "token":
            gen.close()  # what Starlette does when the client goes away
            break

    (rec,) = _records(project)
    assert rec["interrupted_reason"] == "disconnected"
    assert rec["assistant"] == [{"type": "text", "text": "half an answer"}]


def test_the_tool_call_cap_is_recorded_as_its_reason(project, monkeypatch):
    monkeypatch.setattr(harness_budget, "MAX_TOOL_CALLS_PER_TURN", 0)

    list(chat_service.run_turn(
        chat_service.ChatSession(), "list", client=_tool_then_text(
            "list_components", {"component_class": "Bus"})))

    (rec,) = _records(project)
    assert rec["interrupted_reason"] == "tool_call_cap_exceeded"


def test_a_completed_turn_writes_one_ordinary_record(project):
    list(chat_service.run_turn(chat_service.ChatSession(), "hi", client=_text_turn()))

    (rec,) = _records(project)
    assert "interrupted" not in rec
    assert rec["assistant"] == [{"type": "text", "text": "ok"}]


def test_a_refusal_before_the_model_spoke_writes_nothing(project, monkeypatch):
    """Nothing ran and the error frame already said why; a record would only
    repeat the user's message as if something had happened."""
    monkeypatch.setattr(harness_budget, "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", 1)
    monkeypatch.setattr(harness_history, "_today_token_spend", lambda _ctx: 5)

    events = list(chat_service.run_turn(
        chat_service.ChatSession(), "hi", client=_text_turn()))

    assert events[-1][1]["reason"] == "daily_budget_exhausted"
    assert _records(project) == []


# ── GET /history shows it and does not replay it ─────────────────────────────


def test_history_returns_the_interrupted_turn_but_never_replays_it(project):
    base = {"session_id": "s-1", "model": chat_service.DEFAULT_MODEL}
    chat_service.append_turn(project, {
        **base, "ts": 1.0, "user": "first",
        "assistant": [{"type": "text", "text": "answered"}]})
    chat_service.append_turn(project, {
        **base, "ts": 2.0, "user": "second",
        "assistant": [{"type": "text", "text": "Looking. "},
                      {"type": "tool_use", "name": "update_component"}],
        "interrupted": True, "interrupted_reason": "aborted"})

    h = chat_router.chat_history(limit=200, actor=None)

    assert [t["user"] for t in h["turns"]] == ["first", "second"]
    assert h["turns"][1]["interrupted"] is True
    assert h["last_session_id"] == "s-1"
    replayed = list(chat_service.get_session("s-1").messages)
    assert replayed == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "answered"}]},
    ], (
        "the interrupted turn was replayed into the model's history; its "
        "tool_use carries no id and no result, so the next turn is a 400"
    )


# ── The daily cap counts each turn once ──────────────────────────────────────


def test_the_daily_spend_counts_each_turn_once(project):
    session = chat_service.ChatSession()
    for _ in range(3):
        list(chat_service.run_turn(session, "hi", client=_text_turn()))

    # _FakeUsage: 10 in + 20 out per turn.
    assert [r["usage"]["output_tokens"] for r in _records(project)] == [20, 40, 60]
    assert harness_history._today_token_spend(project) == 90, (
        "summing the running total counts turn 1 three times and turn 2 twice"
    )


def test_an_interrupted_turn_counts_toward_the_daily_spend(project, monkeypatch):
    """It reached the provider and was billed. Before it had a record, its
    spend was missing from the cap altogether."""
    list(chat_service.run_turn(chat_service.ChatSession(), "hi", client=_text_turn()))
    session = chat_service.ChatSession()
    real = chat_tools.DISPATCHERS["list_components"]

    def _abort(**kwargs):
        session.abort_event.set()
        return real(**kwargs)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", _abort)
    list(chat_service.run_turn(session, "list", client=_tool_then_text(
        "list_components", {"component_class": "Bus"})))

    # 30 for the completed turn, 30 for the one step the aborted turn ran.
    assert harness_history._today_token_spend(project) == 60


def test_a_legacy_record_still_counts_its_running_total(project):
    """No `turn_usage` → the old reading, which over-counts but never hides
    spend. Guessing a per-turn figure from neighbouring records is worse."""
    chat_service.append_turn(project, {
        "ts": time.time(), "session_id": "old", "model": chat_service.DEFAULT_MODEL,
        "user": "x", "assistant": [], "usage": {"input_tokens": 7, "output_tokens": 3}})

    assert harness_history._today_token_spend(project) == 10
