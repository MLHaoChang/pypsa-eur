"""
Chat harness issue 13 (part a): replay. Fork a recorded session just before
one of its turns, run that turn again on another provider, and put the two
trajectories side by side (tools called, final text, usage).
"""
from __future__ import annotations

import pytest

from harness import eventlog, replay


def _tool_turn(name, args, tool_id="tu_1"):
    return {"blocks": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _text_turn(text="ok"):
    return {"blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}


@pytest.fixture
def recorded(monkeypatch):
    from harness.providers.fake import FakeProvider
    from harness.session import ChatSession
    from services import chat_service, chat_tools

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects", lambda **kw: {"rows": [{"name": "P1"}]})
    monkeypatch.setitem(chat_tools.DISPATCHERS, "get_meta", lambda **kw: {"snapshots": 24})
    session = ChatSession(model="m", owner_user_id="u1")
    list(chat_service.run_turn(session, "hello", provider=FakeProvider([_text_turn("hi")])))
    list(chat_service.run_turn(session, "list my projects", provider=FakeProvider(
        [_tool_turn("list_projects", {}), _text_turn("one project: P1")])))
    return eventlog.path_for(session)


def _second_turn_seq(path):
    return [e for e in eventlog.read_events(path) if e["type"] == "turn/start"][1]["seq"]


def test_replay_runs_the_turn_again_and_reports_both_trajectories(recorded):
    from harness.providers.fake import FakeProvider

    other = FakeProvider([_tool_turn("get_meta", {}, "tu_x"), _tool_turn("list_projects", {}, "tu_y"),
                          _text_turn("P1 is the only one")])
    out = replay.replay(recorded, _second_turn_seq(recorded), other)
    assert out["user"] == "list my projects"
    assert out["original"]["tools"] == ["list_projects"]
    assert out["original"]["text"] == "one project: P1"
    assert out["original"]["usage"]["output_tokens"] == 7
    assert out["replayed"]["tools"] == ["get_meta", "list_projects"]
    assert out["replayed"]["text"] == "P1 is the only one"
    assert out["replayed"]["usage"]["output_tokens"] == 12
    assert out["replayed"]["reason"] == "completed"


def test_the_replayed_turn_sees_exactly_the_prefix(recorded):
    from harness.providers.fake import FakeProvider

    other = FakeProvider([_text_turn("x")])
    replay.replay(recorded, _second_turn_seq(recorded), other)
    sent = other.requests[0].messages
    texts = [m["content"] if isinstance(m["content"], str) else
             " ".join(b.get("text", "") for b in m["content"] if isinstance(b, dict))
             for m in sent]
    assert "hello" in texts[0] and "hi" in texts[1]
    assert "list my projects" in texts[-1]
    assert not any("one project: P1" in t for t in texts)   # the original answer is not in it


def test_the_replayed_turn_asks_for_a_real_model(recorded):
    """The fork sends a model name on the wire: the one the caller names (the
    CLI passes the profile's), else the one the recorded turn ran on."""
    from harness.providers.fake import FakeProvider

    start = _second_turn_seq(recorded)
    assert eventlog.read_events(recorded)[start - 1]["model"] == "m"
    default = FakeProvider([_text_turn("x")])
    replay.replay(recorded, start, default)
    assert default.requests[0].model == "m"
    named = FakeProvider([_text_turn("x")])
    replay.replay(recorded, start, named, model="claude-other")
    assert named.requests[0].model == "claude-other"


def test_the_fork_is_bound_to_the_replay_profile(recorded, monkeypatch):
    """The turn's profile sets the tools payload, the token cap and the
    fallback model, so a replay on a profile runs under that profile, not
    whatever the model name would imply."""
    from harness.providers.fake import FakeProvider
    from services import chat_tools

    seen = []
    real = chat_tools.set_turn_profile
    monkeypatch.setattr(chat_tools, "set_turn_profile",
                        lambda p: (seen.append(getattr(p, "id", None)), real(p))[1])
    replay.replay(recorded, _second_turn_seq(recorded), FakeProvider([_text_turn("x")]),
                  profile_id="anthropic-opus")
    assert "anthropic-opus" in seen


def test_replay_needs_a_turn_start(recorded):
    from harness.providers.fake import FakeProvider

    first = eventlog.read_events(recorded)[1]["seq"]          # a user/message, not a turn start
    with pytest.raises(ValueError, match="turn/start"):
        replay.replay(recorded, first, FakeProvider([]))


def test_the_original_log_is_untouched(recorded):
    from harness.providers.fake import FakeProvider

    before = recorded.read_bytes()
    replay.replay(recorded, _second_turn_seq(recorded), FakeProvider([_text_turn("x")]))
    assert recorded.read_bytes() == before


def test_the_cli_prints_the_trajectories_side_by_side(recorded, capsys, monkeypatch):
    import importlib.util
    import pathlib
    from harness.providers.fake import FakeProvider

    path = pathlib.Path(__file__).resolve().parents[1] / "smoke" / "replay_session.py"
    spec = importlib.util.spec_from_file_location("replay_session", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    fake = FakeProvider([_text_turn("again")])
    calls = {}
    real_replay = replay.replay

    def spy(*a, **kw):
        calls.update(kw)
        return real_replay(*a, **kw)
    monkeypatch.setattr(cli, "provider_for", lambda profile_id: (fake, "stub-model"))
    monkeypatch.setattr(replay, "replay", spy)
    code = cli.main(["--events", str(recorded), "--at", str(_second_turn_seq(recorded)),
                     "--profile", "anthropic-sonnet"])
    out = capsys.readouterr().out
    assert code == 0
    assert "list_projects" in out and "again" in out and "original" in out and "replayed" in out
    assert fake.requests[0].model == "stub-model"           # the profile's model, not a placeholder
    assert calls["profile_id"] == "anthropic-sonnet"
