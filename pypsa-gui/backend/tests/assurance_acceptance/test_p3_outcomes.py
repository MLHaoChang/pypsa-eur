"""A3: real reads are receipts, unexecuted simulations cannot be completion."""

import json

from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)


def test_real_read_outcome_is_classified_without_implying_a_solve(baseline, harness):
    frames = harness.load_fact()
    result = next(d["result"] for e, d in frames if e == "tool_result")
    assert result.get("_assurance", {}).get("outcome") == "completed"
    assert "simulation completed" not in public_text(frames).lower()


def test_model_cannot_invent_a_completed_action_receipt(harness):
    draft = json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "solve",
                    "kind": "action",
                    "tool_use_id": "never-executed",
                    "status": "completed",
                    "text": "Simulation completed successfully.",
                }
            ],
        }
    )
    frames = harness.run([text_turn(draft), text_turn(draft)])
    assert approval(frames)["verdict"] == "limited"
    assert "Simulation completed successfully." not in public_text(frames)


def test_navigation_is_submitted_until_the_browser_acknowledges(baseline, harness):
    frames = harness.run(
        [
            tool_turn(
                "ui_open_panel",
                {"panel_id": "results", "results_tab": "loadflow"},
                call_id="navigate",
            ),
            text_turn("The load-flow view has been opened."),
            text_turn("The load-flow view has been opened."),
        ]
    )
    result = next(d["result"] for e, d in frames if e == "tool_result")
    assert result.get("ui_navigated") is not True, (
        "Navigation sent was treated as view acknowledgement"
    )
    assert result.get("_assurance", {}).get("outcome") == "submitted"
    assert "has been opened" not in public_text(frames)
