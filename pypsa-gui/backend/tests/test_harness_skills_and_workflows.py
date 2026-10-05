"""
`use_skill` (issue 05) and `start_workflow` / `advance_workflow` /
`end_workflow` with the per-turn step addendum (issue 06), driven through
the real loop with the FakeProvider.
"""
from __future__ import annotations

import pytest


def _tool_turn(name, args, tool_id="tu_1"):
    return {"blocks": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _text_turn(text="ok"):
    return {"blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}


def _run(session, message, *turns, ui_context=None):
    from harness.providers.fake import FakeProvider
    from services import chat_service

    fake = FakeProvider(list(turns))
    events = list(chat_service.run_turn(session, message, provider=fake, ui_context=ui_context))
    return events, fake


def _last_user_text(fake, request_index=0) -> str:
    content = fake.requests[request_index].messages[-1]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))


# ── catalogue ───────────────────────────────────────────────────────────────

def test_the_harness_tools_are_read_tier_service_calls():
    from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
    from services.chat_tools import DISPATCHERS

    names = {t["name"] for t in TOOLS}
    for name in ("use_skill", "start_workflow", "advance_workflow", "end_workflow"):
        assert name in names and name in DISPATCHERS, name
        assert safety_tier_for(name) == "read"
        assert TOOL_ROUTES[name] == ["_service_call_"]


# ── skills ──────────────────────────────────────────────────────────────────

def test_use_skill_returns_the_grill_procedure():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    events, fake = _run(session, "grill me", _tool_turn("use_skill", {"name": "Grill"}), _text_turn())
    result = dict(events)["tool_result"]["result"]
    assert result["name"] == "grill"
    assert "ask_user" in result["instructions"]
    assert "design tree" in result["instructions"]


def test_an_unknown_skill_is_a_typed_error():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    events, _ = _run(session, "x", _tool_turn("use_skill", {"name": "nope"}), _text_turn())
    err = dict(events)["tool_error"]
    assert err["error_kind"] == "unknown_skill"


def test_the_skill_catalogue_is_in_the_tools_on_prompt_only():
    from services import chat_service

    session = chat_service.ChatSession(session_id="0123456789abcdef0123456789abcdef")
    on = chat_service._build_system_prompt(session, include_tools=True)
    off = chat_service._build_system_prompt(session, include_tools=False)
    assert "use_skill" in on and "grill —" in on
    assert "use_skill" not in off and "grill —" not in off
    # The catalogue carries descriptions, never bodies.
    assert "design tree" not in on


# ── workflows ───────────────────────────────────────────────────────────────

def test_start_workflow_sets_state_and_returns_the_first_step():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    events, fake = _run(session, "build", _tool_turn("start_workflow", {"workflow_id": "build-network"}), _text_turn())
    result = dict(events)["tool_result"]["result"]
    assert (result["workflow"], result["step"], result["step_index"], result["step_count"]) == ("build-network", "orient", 1, 6)
    assert "`get_meta`" in result["instructions"]
    assert session.workflow == {"id": "build-network", "step": "orient"}
    # The turn that STARTED the workflow carried no addendum (state was set mid-turn).
    assert _last_user_text(fake, 0) == "build"


def test_the_next_turn_carries_the_current_step_outside_the_fence():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.workflow = {"id": "build-network", "step": "buses"}
    _, fake = _run(session, "add bus A", _text_turn())
    text = _last_user_text(fake, 0)
    assert text.startswith('Workflow "Build a network", step 2 of 6: "Buses and carriers". Done when: ')
    assert "`create_component`" in text
    assert "call advance_workflow" in text
    assert text.endswith("\n\nadd bus A")
    assert "<untrusted_data>" not in text  # no ui_context → no fence; the step is not data


def test_with_a_ui_context_the_step_follows_the_fence_and_precedes_the_words():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.workflow = {"id": "build-network", "step": "buses"}
    _, fake = _run(session, "add bus A", _text_turn(), ui_context={"panel": "topology"})
    text = _last_user_text(fake, 0)
    close = text.index("</untrusted_data>")
    assert text.index('Workflow "Build a network"') > close
    assert text.endswith("\n\nadd bus A")


def test_an_expert_turn_without_a_workflow_is_byte_identical():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    _, fake = _run(session, "hi", _text_turn())
    assert fake.requests[0].messages[-1]["content"] == "hi"


def test_advance_and_end():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.workflow = {"id": "build-network", "step": "orient"}
    events, _ = _run(session, "next", _tool_turn("advance_workflow", {"step": "branches"}), _text_turn())
    assert dict(events)["tool_result"]["result"]["step"] == "branches"
    assert session.workflow == {"id": "build-network", "step": "branches"}
    events, _ = _run(session, "stop", _tool_turn("end_workflow", {}, "tu_2"), _text_turn())
    assert dict(events)["tool_result"]["result"] == {"ended": "build-network"}
    assert session.workflow is None


@pytest.mark.parametrize("tool,args,kind", [
    ("start_workflow", {"workflow_id": "investment-decision"}, "unknown_workflow"),  # planned
    ("start_workflow", {"workflow_id": "nope"}, "unknown_workflow"),
    ("advance_workflow", {"step": "orient"}, "no_active_workflow"),
])
def test_workflow_refusals_are_typed(tool, args, kind):
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    events, _ = _run(session, "x", _tool_turn(tool, args), _text_turn())
    assert dict(events)["tool_error"]["error_kind"] == kind


def test_advancing_to_a_missing_step_is_typed():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.workflow = {"id": "build-network", "step": "orient"}
    events, _ = _run(session, "x", _tool_turn("advance_workflow", {"step": "nope"}), _text_turn())
    assert dict(events)["tool_error"]["error_kind"] == "unknown_workflow_step"


def test_a_client_context_rebinds_a_lost_workflow_but_never_overrides_one():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    _, fake = _run(session, "hi", _text_turn(),
                   ui_context={"workflow": {"id": "run-study", "step": "run"}})
    assert session.workflow == {"id": "run-study", "step": "run"}
    assert 'Workflow "Run a reliability study", step 2 of 3' in _last_user_text(fake, 0)
    _, fake = _run(session, "hi", _text_turn(),
                   ui_context={"workflow": {"id": "build-network", "step": "orient"}})
    assert session.workflow == {"id": "run-study", "step": "run"}
    # A pair naming no real step is ignored.
    fresh = chat_service.ChatSession(model="claude-sonnet-5")
    _run(fresh, "hi", _text_turn(), ui_context={"workflow": {"id": "run-study", "step": "nope"}})
    assert fresh.workflow is None


# ── Guided mode is the hub-design workflow's first consumer ─────────────────

def test_the_guided_addendum_is_the_hub_design_preamble():
    from harness import workflows
    from services import chat_service

    wf = workflows.get("hub-design")
    assert chat_service._guided_mode_addendum(None) == " ".join(wf.preamble.split())
    assert chat_service._GUIDED_STEPS == {s.id: s.title for s in wf.steps}
    assert chat_service._GUIDED_STEPS == {
        "start": "Start", "site": "Site", "goal": "Goal", "results": "Results", "improve": "Improve",
    }


def test_in_guided_the_rules_come_once_and_the_step_once():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.workflow = {"id": "hub-design", "step": "site"}
    _, fake = _run(session, "help", _text_turn(), ui_context={"ui_mode": "guided", "guided_step": "site"})
    text = _last_user_text(fake, 0)
    assert text.count("Guided mode is on.") == 1
    assert text.count('Workflow "Hub design, step by step"') == 1


def test_in_expert_hub_design_brings_its_steps_but_not_the_guided_rules():
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.workflow = {"id": "hub-design", "step": "site"}
    _, fake = _run(session, "help", _text_turn())
    text = _last_user_text(fake, 0)
    assert "Guided mode is on." not in text
    assert 'Workflow "Hub design, step by step", step 2 of 5' in text
