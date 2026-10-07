"""
`ask_user` and the `choice_request` frame (chat harness issue 04).

The tool is non-blocking by design (owner decision Q4): the loop emits a
`choice_request` frame for the panel, the model receives `{status:
"presented"}` and the pick arrives as the next user message. These tests
drive the REAL loop with the FakeProvider.
"""
from __future__ import annotations

import pytest


def _ask_turn(options, **extra):
    return {
        "blocks": [{
            "type": "tool_use", "id": "tu_ask_1", "name": "ask_user",
            "input": {
                "title": "Q1 — Which storage option?",
                "question": "The verdict flips on this one.",
                "options": options,
                **extra,
            },
        }],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _closing_turn():
    return {"blocks": [{"type": "text", "text": "Pick one above."}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}


def test_catalogue_declares_ask_user_as_a_read_tool_with_the_ui_sentinel():
    from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
    from services.chat_tools import DISPATCHERS

    names = {t["name"] for t in TOOLS}
    assert "ask_user" in names and "ask_user" in DISPATCHERS
    assert safety_tier_for("ask_user") == "read"
    assert TOOL_ROUTES["ask_user"] == ["_ui_event_"]


def test_ask_user_yields_a_choice_request_and_a_presented_result():
    from harness.events import FRAMES
    from harness.providers.fake import FakeProvider
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    fake = FakeProvider([
        _ask_turn([
            {"label": "Battery", "description": "Cheapest for a 2 h peak.", "recommended": True},
            {"label": "Thermal store"},
        ]),
        _closing_turn(),
    ])
    events = list(chat_service.run_turn(session, "grill me on storage", provider=fake))
    names = [n for n, _ in events]
    assert "choice_request" in FRAMES
    # The tool lifecycle, in order, with the card frame between running and result.
    i_req = names.index("tool_request")
    i_run = names.index("tool_running")
    i_choice = names.index("choice_request")
    i_res = names.index("tool_result")
    assert i_req < i_run < i_choice < i_res
    assert "ui_event" not in names, "a choice is a card, not navigation"
    assert names[-1] == "turn_done"

    choice = dict(events)["choice_request"]
    assert choice["tool_use_id"] == "tu_ask_1"
    assert choice["title"] == "Q1 — Which storage option?"
    assert choice["options"] == [
        {"label": "Battery", "description": "Cheapest for a 2 h peak.", "recommended": True},
        {"label": "Thermal store"},
    ]
    assert choice["allow_free_text"] is True

    result = dict(events)["tool_result"]["result"]
    assert result["status"] == "presented" and result["awaiting"] == "user"
    assert result["options"] == ["Battery", "Thermal store"]
    # The model's second request carried the presented result, not the answer.
    second = fake.requests[1].messages[-1]
    assert second["role"] == "user"
    assert "presented" in str(second["content"])


@pytest.mark.parametrize("options,why", [
    ([], "non-empty"),
    ([{"label": "A", "recommended": True}, {"label": "B", "recommended": True}], "at most one"),
    ([{"label": "A"}, {"label": "a"}], "distinct"),
    ([{"label": f"o{i}"} for i in range(9)], "at most 8"),
])
def test_a_half_built_card_is_an_invalid_tool_args_error(options, why):
    from harness.providers.fake import FakeProvider
    from services import chat_service

    session = chat_service.ChatSession(model="claude-sonnet-5")
    fake = FakeProvider([_ask_turn(options), _closing_turn()])
    events = list(chat_service.run_turn(session, "ask", provider=fake))
    names = [n for n, _ in events]
    assert "choice_request" not in names
    err = dict(events)["tool_error"]
    assert err["error_kind"] == "invalid_tool_args"
    assert why in err["message"]


def test_the_free_text_flag_and_whitespace_are_normalised():
    from services.chat_tools import ask_user

    out = ask_user("  Q2 —  size ", "How\nbig?", [{"label": " 2 MW ", "description": "a  b"}],
                   allow_free_text=False)
    assert out["title"] == "Q2 — size" and out["question"] == "How big?"
    assert out["options"] == [{"label": "2 MW", "description": "a b"}]
    assert out["allow_free_text"] is False and out["kind"] == "choice"
