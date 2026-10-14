"""
Chat harness issue 17: a goal with a round budget.

`set_goal(objective, max_rounds)` puts a goal on the chat session. After a
turn ends, while the goal is active and rounds remain, the loop runs the next
round itself on the same stream: a user message `Continue toward the goal
(round n of N).`, admitted through utterance admission like any other. A
round never changes what a tool needs (every card still blocks), and the
session and daily budgets still apply.

Rounds stop when the model completes or pauses the goal, the rounds are used
up, the turn ended on a Choice card (the user answers, then rounds go on), a
card was declined, the turn failed or hit a budget, a task step needs review,
the admission generation moved, or the turn was a checked one. Only a person
resumes a paused or blocked goal (`POST /api/chat/{id}/goal`).

Decided in Q17: default 5 rounds, ceiling 20, no tier changes.
"""
from __future__ import annotations

import uuid

import pytest

from harness import goals


def _tool_turn(name, args, tool_id="tu_1"):
    return {"blocks": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _text_turn(text="ok"):
    return {"blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}


def _session():
    from harness.session import ChatSession
    return ChatSession(model="claude-sonnet-5", owner_user_id="u1")


def _run(session, message, *turns, **kw):
    from harness.providers.fake import FakeProvider
    from services import chat_service

    fake = FakeProvider(list(turns))
    events = list(chat_service.run_turn(session, message, provider=fake, **kw))
    return events, fake


def _user_texts(fake):
    out = []
    for request in fake.requests:
        content = request.messages[-1]["content"]
        out.append(content if isinstance(content, str) else
                   "\n".join(b.get("text", "") for b in content if isinstance(b, dict)))
    return out


def _states(events):
    return [p["goal"] for e, p in events if e == "goal_state"]


def _set(objective="tidy the network", **extra):
    return _tool_turn("set_goal", {"objective": objective, **extra}, f"tu_{uuid.uuid4().hex[:6]}")


# ── catalogue ───────────────────────────────────────────────────────────────

def test_the_goal_tools_are_read_tier_service_calls_and_the_frame_is_named():
    from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
    from harness.events import FRAMES
    from harness.toolsets import CONTROL_TOOLS
    from services.chat_tools import DISPATCHERS

    names = {t["name"] for t in TOOLS}
    for name in ("set_goal", "pause_goal", "complete_goal"):
        assert name in names and name in DISPATCHERS and name in CONTROL_TOOLS, name
        assert safety_tier_for(name) == "read"
        assert TOOL_ROUTES[name] == ["_service_call_"]
    assert "goal_state" in FRAMES
    assert (goals.DEFAULT_ROUNDS, goals.MAX_ROUNDS) == (5, 20)


# ── set_goal ────────────────────────────────────────────────────────────────

def test_set_goal_defaults_to_five_rounds_and_reports_the_state():
    session = _session()
    events, _ = _run(session, "work on it", _set(), _text_turn("on it"), _tool_turn("complete_goal", {"summary": "done"}),
                     _text_turn("finished"))
    first = _states(events)[0]
    assert first == {"objective": "tidy the network", "max_rounds": 5, "rounds_used": 0,
                     "phase": "active", "reason": None}
    names = [e for e, _ in events]
    assert names.index("goal_state") == names.index("tool_result") + 1


@pytest.mark.parametrize("args", [
    {"objective": ""}, {"objective": "   "}, {"objective": 7},
    {"objective": "x", "max_rounds": 0}, {"objective": "x", "max_rounds": 21},
    {"objective": "x", "max_rounds": True}, {"objective": "x", "max_rounds": "5"},
    {"objective": "x" * 501},
])
def test_a_bad_goal_is_a_typed_refusal(args):
    session = _session()
    events, _ = _run(session, "go", _tool_turn("set_goal", args), _text_turn())
    errors = [p for e, p in events if e == "tool_error"]
    assert errors and errors[0]["error_kind"] == "invalid_goal"
    assert session.goal is None


def test_a_second_goal_is_refused_while_one_is_set():
    session = _session()
    goals.set_goal(session, "first", 3)
    goals.pause(session, by="user")
    with pytest.raises(Exception) as refused:
        goals.set_goal(session, "second", 3)
    assert refused.value.detail["error_kind"] == "goal_already_set"
    assert session.goal["objective"] == "first"


# ── rounds ──────────────────────────────────────────────────────────────────

def test_rounds_run_on_the_same_stream_until_the_goal_is_complete():
    session = _session()
    events, fake = _run(
        session, "make the network solve",
        _set("make the network solve"), _text_turn("starting"),                 # the user's turn
        _text_turn("checked the buses"),                                         # round 1
        _tool_turn("complete_goal", {"summary": "it solves"}, "tu_c"), _text_turn("done"),  # round 2
    )
    texts = _user_texts(fake)
    assert "Continue toward the goal (round 1 of 5)." in texts[2]
    assert "Continue toward the goal (round 2 of 5)." in texts[3]
    assert len(fake.requests) == 5                                               # and no round 3
    assert [e for e, _ in events].count("turn_done") == 3
    assert session.goal["phase"] == "complete" and session.goal["reason"] == "it solves"
    # One stream: the panel closes it on session_done, which no round sends.
    assert "session_done" not in [e for e, _ in events]


def test_each_round_carries_the_goal_and_a_turn_without_one_does_not():
    session = _session()
    _, plain = _run(session, "hello", _text_turn())
    assert "Goal:" not in _user_texts(plain)[0]
    goals.set_goal(session, "add a battery", 2)
    _, fake = _run(session, "go", _text_turn("a"), _text_turn("b"), _text_turn("c"))
    for text in _user_texts(fake):
        assert 'Goal: "add a battery"' in text and "complete_goal" in text


def test_rounds_stop_when_they_are_used_up():
    session = _session()
    goals.set_goal(session, "explore", 2)
    events, fake = _run(session, "go", _text_turn("0"), _text_turn("1"), _text_turn("2"), _text_turn("never"))
    assert len(fake.requests) == 3
    assert (session.goal["phase"], session.goal["reason"], session.goal["rounds_used"]) == (
        "paused", "rounds_used_up", 2)
    assert _states(events)[-1]["phase"] == "paused"


def test_the_model_can_pause_its_goal():
    session = _session()
    goals.set_goal(session, "explore", 5)
    _, fake = _run(session, "go", _tool_turn("pause_goal", {"reason": "need the tariff"}), _text_turn("paused"),
                   _text_turn("never"))
    assert len(fake.requests) == 2
    assert session.goal["phase"] == "paused" and session.goal["reason"] == "need the tariff"


def test_a_choice_card_waits_for_the_user_then_rounds_go_on():
    session = _session()
    goals.set_goal(session, "pick a site", 3)
    ask = _tool_turn("ask_user", {"title": "Site", "question": "Which?", "options": [
        {"label": "A", "recommended": True}, {"label": "B"}]})
    _, fake = _run(session, "go", ask, _text_turn("asked"), _text_turn("never"))
    assert len(fake.requests) == 2
    assert session.goal["phase"] == "active" and session.goal["rounds_used"] == 0
    _, fake = _run(session, "Site: A", _text_turn("noted"), _tool_turn("complete_goal", {}), _text_turn("done"))
    assert "Continue toward the goal (round 1 of 3)." in _user_texts(fake)[1]


def test_a_failed_turn_blocks_the_goal_before_the_error_frame():
    from harness.providers.fake import FakeProvider
    from services import chat_service

    session = _session()
    goals.set_goal(session, "explore", 3)

    class Broken(FakeProvider):
        def stream(self, request):
            raise RuntimeError("boom")
    events = list(chat_service.run_turn(session, "go", provider=Broken([])))
    names = [e for e, _ in events]
    assert "error" in names
    assert names.index("goal_state") < names.index("error")      # the panel closes on `error`
    assert session.goal["phase"] == "blocked" and session.goal["reason"] == "error"


def test_a_budget_hit_blocks_the_goal(monkeypatch):
    from harness import budget

    session = _session()
    goals.set_goal(session, "explore", 5)
    monkeypatch.setattr(budget, "MAX_OUTPUT_TOKENS_PER_SESSION", 4)       # two text turns of 2
    events, fake = _run(session, "go", _text_turn("0"), _text_turn("1"), _text_turn("never"))
    assert len(fake.requests) == 2
    assert session.goal["phase"] == "blocked" and session.goal["reason"] == "budget"
    # The goal's state reaches the panel before the frame that closes the stream.
    assert [e for e, _ in events][-2:] == ["goal_state", "session_done"]
    assert events[-1][1]["reason"] == "budget_exhausted"


# ── what blocks or pauses a goal, decided after the turn ────────────────────

def _after(session, *frames, checked=False):
    watch = goals.Watch()
    for event, payload in frames:
        watch.note(event, payload)
    return goals.after_turn(session, watch, checked=checked)


DONE = ("turn_done", {"usage": {}})


@pytest.mark.parametrize("frames,phase,reason", [
    ([("tool_error", {"error_kind": "confirmation_denied"}), DONE], "paused", "card_declined"),
    ([("tool_error", {"error_kind": "confirmation_expired"}), DONE], "paused", "card_declined"),
    ([("tool_error", {"error_kind": "task_step_uncertain"}), DONE], "blocked", "needs_review"),
    ([("session_done", {"reason": "aborted"})], "paused", "aborted"),
    ([("session_done", {"reason": "daily_budget_exhausted"})], "blocked", "budget"),
    ([("session_done", {"reason": "turn_already_in_flight"})], "blocked", "error"),
])
def test_what_the_turn_showed_decides_the_phase(frames, phase, reason):
    session = _session()
    goals.set_goal(session, "x", 5)
    assert _after(session, *frames) is None
    assert (session.goal["phase"], session.goal["reason"]) == (phase, reason)


def test_a_checked_turn_never_continues():
    session = _session()
    goals.set_goal(session, "x", 5)
    assert _after(session, DONE, checked=True) is None
    assert (session.goal["phase"], session.goal["reason"]) == ("blocked", "checked_turn")


def test_a_generation_change_blocks_the_goal():
    from services import utterance_admission as ua

    session = _session()
    goals.set_goal(session, "x", 5)
    ua._DEFAULT.bump_generation(session_id=session.session_id)
    assert _after(session, DONE) is None
    assert (session.goal["phase"], session.goal["reason"]) == ("blocked", "generation_changed")


def test_a_task_step_needing_review_blocks_the_goal(monkeypatch):
    from services import assistant_tasks

    session = _session()
    session.task_id = "t1"
    monkeypatch.setattr(assistant_tasks, "get_task", lambda task_id: {"status": "needs_review"})
    goals.set_goal(session, "x", 5)
    assert _after(session, DONE) is None
    assert (session.goal["phase"], session.goal["reason"]) == ("blocked", "needs_review")


# ── admission ───────────────────────────────────────────────────────────────

def test_a_queued_correction_runs_before_the_next_round(monkeypatch):
    """Rounds go through utterance admission, so a correction the user sent
    during a turn is answered first; the round follows it."""
    from services import chat_tools
    from services import utterance_admission as ua

    session = _session()
    goals.set_goal(session, "x", 2)

    def correct(**kw):
        ua.admit_utterance(ua.StableUtterance("u-fix", "actually, use bus 7", 1, session.session_id))
        return {"rows": []}
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects", correct)
    _, fake = _run(session, "go", _tool_turn("list_projects", {}), _text_turn("a"),
                   _text_turn("b"), _tool_turn("complete_goal", {}, "tu_c"), _text_turn("c"),
                   utterance_id="u-go", generation=1)
    texts = _user_texts(fake)
    assert "actually, use bus 7" in texts[2]
    assert "Continue toward the goal (round 1 of 2)." in texts[3]


# ── the event log ───────────────────────────────────────────────────────────

def test_the_goal_is_in_the_event_log_and_the_history_still_derives():
    from harness import eventlog

    session = _session()
    _run(session, "go", _set("x", max_rounds=1), _text_turn("a"), _text_turn("b"))
    events = eventlog.read_events(eventlog.path_for(session))
    kinds = [e["type"] for e in events]
    assert "goal/set" in kinds and "goal/round" in kinds and "goal/phase" in kinds
    rnd = next(e for e in events if e["type"] == "goal/round")
    assert (rnd["round"], rnd["of"]) == (1, 1)
    start = next(e for e in events if e["type"] == "turn/start" and e["seq"] > rnd["seq"])
    assert start["user"] == "Continue toward the goal (round 1 of 1)."
    assert eventlog.derive_messages(events) == list(session.messages)


# ── a person pauses, resumes and stops (POST /api/chat/{id}/goal) ───────────

@pytest.fixture
def owned(seeded_identity):
    from services import chat_service
    sid = f"goal-{uuid.uuid4().hex[:8]}"
    sess = chat_service.get_or_create_session(sid, owner_user_id=str(seeded_identity["user_id"]))
    yield sess
    chat_service.drop_session(sid)


def test_only_a_person_resumes_and_a_resume_grants_fresh_rounds(client, owned):
    goals.set_goal(owned, "x", 3)
    owned.goal.update(rounds_used=3, phase="paused", reason="rounds_used_up")
    r = client.post(f"/api/chat/{owned.session_id}/goal", json={"action": "resume"})
    assert r.status_code == 200
    goal = r.json()["goal"]
    assert (goal["phase"], goal["rounds_used"], goal["reason"]) == ("active", 0, None)
    r = client.post(f"/api/chat/{owned.session_id}/goal", json={"action": "pause"})
    assert r.json()["goal"]["phase"] == "paused" and r.json()["goal"]["reason"] == "user"
    r = client.post(f"/api/chat/{owned.session_id}/goal", json={"action": "stop"})
    assert r.json() == {"ok": True, "goal": None} and owned.goal is None


def test_a_resume_with_no_goal_is_typed(client, owned):
    r = client.post(f"/api/chat/{owned.session_id}/goal", json={"action": "resume"})
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "no_goal"


def test_an_unknown_or_foreign_session_is_not_found(client):
    r = client.post(f"/api/chat/no-such-{uuid.uuid4().hex}/goal", json={"action": "pause"})
    assert r.status_code == 200 and r.json() == {"ok": False, "reason": "unknown_session"}


def test_a_bad_action_is_refused(client, owned):
    r = client.post(f"/api/chat/{owned.session_id}/goal", json={"action": "restart"})
    assert r.status_code == 422


def test_history_carries_the_goal_key(client):
    r = client.get("/api/chat/history")
    assert r.status_code == 200 and "goal" in r.json()
