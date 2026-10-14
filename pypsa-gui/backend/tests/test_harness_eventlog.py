"""
Chat harness issue 13 (part a): the session event log, written in shadow mode.

Every model-visible input of a session goes to an append-only `events.jsonl`
(owner- and session-bound, under the app-data directory) beside today's
path: the turn's messages as `user/message`, `assistant/attempt` and
`tool/result`, the per-turn addenda as `context/injected`, and the three
other ways the provider history changes (`history/trim`, `history/rewind`,
`history/reset`). `derive_messages(events)` replays them and must equal the
history the next provider request is seeded from (`session.messages`).

Not yet the source of truth: chat.jsonl, the pending-turn WAL and the frames
are unchanged here (the switch is part b).
"""
from __future__ import annotations

import json
import os

import pytest

from harness import eventlog


BIG_SECRET = "sk-ant-api03-" + "x" * 40


def _tool_turn(name, args, tool_id="tu_1"):
    return {"blocks": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _text_turn(text="ok"):
    return {"blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}


@pytest.fixture
def tools(monkeypatch):
    from services import chat_tools
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects",
                        lambda **kw: {"rows": [{"name": "P1"}, {"name": "P2"}]})


def _session(owner="u1"):
    from harness.session import ChatSession
    return ChatSession(model="m", owner_user_id=owner)


def _turn(session, message, *turns, ui_context=None):
    from harness.providers.fake import FakeProvider
    from services import chat_service
    fake = FakeProvider(list(turns))
    return list(chat_service.run_turn(session, message, provider=fake, ui_context=ui_context))


def _events(session):
    return eventlog.read_events(eventlog.path_for(session))


def _types(events):
    return [e["type"] for e in events]


# ── what a turn writes ─────────────────────────────────────────────────────

def test_a_tool_turn_is_logged_in_order(tools):
    session = _session()
    _turn(session, "list my projects", _tool_turn("list_projects", {}), _text_turn("two"))
    events = _events(session)
    assert _types(events) == ["turn/start", "user/message", "assistant/attempt",
                              "tool/result", "assistant/attempt", "turn/record", "turn/end"]
    assert [e["seq"] for e in events] == sorted({e["seq"] for e in events})
    assert events[0]["user"] == "list my projects"
    assert events[-1]["reason"] == "completed"
    assert events[-1]["usage"]["output_tokens"] == 7
    header = eventlog.read_header(eventlog.path_for(session))
    assert header["format"] == eventlog.FORMAT and header["session_id"] == session.session_id
    assert header["parent"] is None and header["inherited_event_count"] == 0


def test_every_addendum_has_a_context_event(tools):
    session = _session()
    _turn(session, "hello", _text_turn(), ui_context={"ui_mode": "guided", "active_tab": "results"})
    injected = [e for e in _events(session) if e["type"] == "context/injected"]
    assert injected and {e["kind"] for e in injected} <= {"ui_context", "workflow_step", "checked_request"}
    ui = next(e for e in injected if e["kind"] == "ui_context")
    user = next(e for e in _events(session) if e["type"] == "user/message")
    content = user["message"]["content"]
    text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in content)
    assert ui["text"] in text


def test_the_turns_messages_are_written_once_at_turn_end(tools, monkeypatch):
    """Batched per turn: during the turn only `turn/start` is on disk (the
    WAL's job); the rest arrives with one write at `turn/end`."""
    from services import chat_tools

    seen = {}

    def peek(**kw):
        seen["types"] = _types(_events(session))
        return {"rows": []}
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects", peek)
    session = _session()
    _turn(session, "x", _tool_turn("list_projects", {}), _text_turn())
    assert seen["types"] == ["turn/start"]
    assert _types(_events(session))[-1] == "turn/end"


# ── derive_messages ────────────────────────────────────────────────────────

def _scenario_plain(session, tools_):
    _turn(session, "hello", _text_turn("hi"))


def _scenario_tools(session, tools_):
    _turn(session, "list", _tool_turn("list_projects", {}), _text_turn("two"))
    _turn(session, "again", _tool_turn("list_projects", {}, "tu_2"), _text_turn("still two"))


def _scenario_trim(session, tools_):
    for i in range(5):
        _turn(session, f"turn {i}", _tool_turn("list_projects", {}, f"tu_{i}"), _text_turn(f"a{i}"))


def _scenario_rewind(session, tools_):
    from harness.history import rewind_session
    _turn(session, "one", _text_turn("1"))
    _turn(session, "two", _tool_turn("list_projects", {}), _text_turn("2"))
    assert rewind_session(session, turns=1) > 0
    _turn(session, "two again", _text_turn("2b"))


def _scenario_reset(session, tools_):
    _turn(session, "one", _text_turn("1"))
    session.reset_history()
    session.append_history_message({"role": "user", "content": "rehydrated question"})
    session.append_history_message({"role": "assistant", "content": [{"type": "text", "text": "rehydrated answer"}]})
    _turn(session, "two", _text_turn("2"))


@pytest.mark.parametrize("scenario", [_scenario_plain, _scenario_tools, _scenario_trim,
                                      _scenario_rewind, _scenario_reset],
                         ids=["plain", "tools", "trim", "rewind", "reset"])
def test_derive_messages_equals_the_history_the_provider_is_seeded_from(scenario, tools, monkeypatch):
    from harness import history
    monkeypatch.setattr(history, "SESSION_MESSAGES_MAX", 6)
    session = _session()
    scenario(session, tools)
    assert eventlog.derive_messages(_events(session)) == list(session.messages)


def test_a_checked_turns_replaced_tool_results_are_what_is_logged():
    session = _session()
    session.assurance_turn = object()
    session.append_history_message({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "raw diagnostic"}]})
    session.assurance_turn = None
    eventlog.flush(session)
    logged = _events(session)[-1]["message"]["content"][0]["content"]
    assert logged == session.messages[-1]["content"][0]["content"] != "raw diagnostic"


# ── crash recovery ─────────────────────────────────────────────────────────

def _torn_log(session):
    """A turn that started, ran a tool call, and never ended (the process died)."""
    eventlog.turn_start(session, "list")
    eventlog.record(session, "user/message", message={"role": "user", "content": "list"})
    eventlog.record(session, "assistant/attempt", message={"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu_9", "name": "list_projects", "input": {}}]})
    eventlog.flush(session)                      # the batch reached disk; turn/end never did
    path = eventlog.path_for(session)
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"seq": 99, "type": "tool/res')  # and the last write was torn
    return path


def test_a_torn_tail_gets_synthetic_closers():
    session = _session()
    path = _torn_log(session)
    events = eventlog.read_events(path, repair=True)
    closers = events[-2:]
    assert closers[0]["type"] == "tool/result" and closers[0]["error_kind"] == "interrupted"
    assert closers[0]["message"]["content"][0]["tool_use_id"] == "tu_9"
    assert closers[1] == {**closers[1], "type": "turn/end", "reason": "interrupted"}
    # appended as an ordinary batch: a second read sees the same log
    assert eventlog.read_events(path, repair=True) == events
    msgs = eventlog.derive_messages(events)
    assert msgs[-1]["content"][0]["type"] == "tool_result"   # paired, no orphan tool_use


def test_reading_without_repair_never_writes():
    session = _session()
    path = _torn_log(session)
    before = path.read_bytes()
    eventlog.read_events(path)
    assert path.read_bytes() == before


def test_a_corrupt_line_in_the_middle_is_not_a_history():
    """A torn write is only the last line. A broken line earlier means the log
    cannot be read: it is not folded up to the break, and nothing is rewritten.
    `resume` leaves the session alone so the caller can fall back to chat.jsonl."""
    session = _session()
    _turn(session, "first", _text_turn("one"))
    _turn(session, "second", _text_turn("two"))
    path = eventlog.path_for(session)
    lines = path.read_text(encoding="utf-8").splitlines()
    second = next(i for i, line in enumerate(lines) if '"user": "second"' in line)
    assert 0 < second < len(lines) - 1
    lines.insert(second, "{not-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(json.JSONDecodeError):
        eventlog.read_events(path)
    with pytest.raises(json.JSONDecodeError):
        eventlog.read_events(path, repair=True)
    assert path.read_bytes() == before

    from harness.session import ChatSession
    fresh = ChatSession(model="m", owner_user_id=session.owner_user_id)
    fresh.session_id = session.session_id
    fresh.messages.append({"role": "user", "content": "keep me"})
    assert eventlog.resume(fresh) is False
    assert list(fresh.messages) == [{"role": "user", "content": "keep me"}]


# ── fork ───────────────────────────────────────────────────────────────────

def test_a_fork_shares_the_prefix_and_owns_nothing_else(tools):
    parent = _session()
    _turn(parent, "one", _text_turn("1"))
    _turn(parent, "two", _tool_turn("list_projects", {}), _text_turn("2"))
    events = _events(parent)
    second = [e for e in events if e["type"] == "turn/start"][1]
    child = eventlog.fork(eventlog.path_for(parent), second["seq"])
    child_path = eventlog.path_for(child)
    header = eventlog.read_header(child_path)
    assert header["parent"]["session_id"] == parent.session_id
    assert header["inherited_event_count"] == len([e for e in events if e["seq"] < second["seq"]])
    # shared, not copied: the child's own file holds only its header
    assert len(child_path.read_text().splitlines()) == 1
    prefix = [e for e in events if e["seq"] < second["seq"]]
    assert eventlog.read_events(child_path) == prefix
    assert list(child.messages) == eventlog.derive_messages(prefix)
    # and nothing that belongs to the parent's run comes with it
    assert child.session_id != parent.session_id
    assert child.pending_confirmations == {} and child.assurance_context is None
    assert child.owner_user_id == parent.owner_user_id


def test_a_fork_continues_on_its_own_log(tools):
    parent = _session()
    _turn(parent, "one", _text_turn("1"))
    start = _events(parent)[0]["seq"]
    child = eventlog.fork(eventlog.path_for(parent), start)
    _turn(child, "different first question", _text_turn("x"))
    child_events = eventlog.read_events(eventlog.path_for(child))
    assert child_events[0]["user"] == "different first question"
    assert eventlog.derive_messages(child_events) == list(child.messages)
    assert _events(parent)[0]["user"] == "one"           # the parent is untouched


# ── safety ─────────────────────────────────────────────────────────────────

def test_secrets_are_redacted_before_the_write(tools):
    session = _session()
    _turn(session, f"my key is {BIG_SECRET}", _text_turn())
    assert BIG_SECRET not in eventlog.path_for(session).read_text()


def test_the_log_lives_under_app_data_owner_and_session_bound(tools):
    import app_paths
    session = _session(owner="u1")
    _turn(session, "x", _text_turn())
    path = eventlog.path_for(session)
    assert app_paths.app_data_dir().resolve() in path.resolve().parents
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert eventlog.path_for(_session(owner="u2")).parent != path.parent


def test_a_log_that_cannot_be_written_never_costs_the_turn(tools, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(eventlog, "_write_lines", broken)
    session = _session()
    frames = _turn(session, "x", _text_turn("still answered"))
    assert any(e == "turn_done" for e, _ in frames)
    assert session.messages[-1]["content"][0]["text"] == "still answered"
