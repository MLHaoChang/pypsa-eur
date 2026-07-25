"""A7 — per-turn aggregate tool-result character budget."""
from __future__ import annotations

from services import chat_service


def test_apply_turn_budget_passes_until_exhausted():
    budget = {"used": 0}
    # Force a tiny cap for the test.
    chat_service.MAX_TOOL_RESULT_CHARS_PER_TURN = 50
    try:
        first = chat_service._apply_turn_tool_result_budget("x" * 40, budget)
        assert first == "x" * 40
        assert budget["used"] == 40
        second = chat_service._apply_turn_tool_result_budget("y" * 40, budget)
        # Second payload pushes past the cap → omitted stub for THIS call
        # because used (40) was still < 50 when checked... wait, we check
        # before adding: 40 < 50 so second is ALSO included and used=80.
        # Third should be omitted.
        assert second == "y" * 40
        assert budget["used"] == 80
        third = chat_service._apply_turn_tool_result_budget("z" * 40, budget)
        assert "per_turn_tool_result_budget" in third
        assert "_omitted" in third or "omitted" in third.lower()
        assert "40" in third  # original length reported
    finally:
        chat_service.MAX_TOOL_RESULT_CHARS_PER_TURN = 40_000


def test_apply_turn_budget_omits_when_already_at_cap():
    budget = {"used": 40_000}
    out = chat_service._apply_turn_tool_result_budget("payload-here", budget)
    assert "per_turn_tool_result_budget" in out
    assert budget["used"] == 40_000  # omitted stub does not grow the counter
