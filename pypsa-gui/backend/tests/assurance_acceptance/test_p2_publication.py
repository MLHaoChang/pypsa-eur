"""A2: no early prose/thinking leaks; identity/units/values cannot be fabricated."""

import pytest

from tests.assurance_acceptance.support import (
    UNSAFE,
    approval,
    public_text,
    text_turn,
    tool_turn,
)


@pytest.mark.parametrize("channel", ["prose", "thinking", "intermediate"])
def test_unvalidated_model_content_never_reaches_public_frames(
    baseline, harness, channel
):
    final = text_turn(UNSAFE, thinking=UNSAFE if channel == "thinking" else None)
    turns = (
        [
            tool_turn(
                "get_component",
                {"component_class": "Load", "name": "L1"},
                preamble=UNSAFE,
            )
        ]
        if channel == "intermediate"
        else []
    ) + [final, final]
    frames = harness.run(turns)
    assert UNSAFE not in public_text(frames), (
        "Unchecked prose was already sent to the user"
    )
    assert not any(event == "thinking" for event, _ in frames), (
        "Provider thinking leaked"
    )
    assert not any(
        UNSAFE in str(data) for event, data in frames if event == "tool_result"
    )


def test_approved_record_precedes_tokens_and_matches_idempotent_fetch(
    baseline, harness
):
    frames = harness.load_fact()
    approved = approval(frames)
    first = harness.client.get(f"/api/chat/answers/{approved['answer_id']}")
    request_count = len(harness.provider.requests)
    second = harness.client.get(f"/api/chat/answers/{approved['answer_id']}")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["text"] == public_text(frames)
    assert first.json()["receipt"] == approved["receipt"]
    assert len(harness.provider.requests) == request_count, (
        "Fetching an answer regenerated it"
    )


@pytest.mark.parametrize(
    "value,unit,entity",
    [
        (75, "MW", "L1"),
        (5, "MWh", "L1"),
        (5, "MW", "Ghost-9"),
        (-5, "MW", "L1"),
    ],
)
def test_wrong_value_unit_entity_or_sign_cannot_be_certified(
    baseline, harness, value, unit, entity
):
    frames = harness.load_fact(value=value, unit=unit, entity=entity)
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert "load-input" not in approved["receipt"]["checked_block_ids"]
    assert approved["receipt"]["limitations"]


def test_ledger_does_not_allow_contradictory_free_prose(baseline, harness):
    frames = harness.load_fact(narrative="L1 consumes 75 MW and the solve converged.")
    assert "75 MW" not in public_text(frames), (
        "A correct ledger certified contradictory narrative"
    )
    approval(frames)


def test_valid_fact_renders_human_text_instead_of_private_json(baseline, harness):
    frames = harness.load_fact()
    assert approval(frames)["verdict"] == "passed"
    text = public_text(frames)
    assert "L1" in text and "5" in text and "MW" in text
    assert '"schema_version"' not in text and '"tool_use_id"' not in text


@pytest.mark.parametrize(
    "draft",
    [
        "{broken",
        '{"schema_version":1,"blocks":[]}',
        '{"schema_version":1,"blocks":[{"kind":"magic"}]}',
    ],
)
def test_invalid_draft_has_bounded_repair_and_factual_fallback(harness, draft):
    frames = harness.run([text_turn(draft), text_turn(draft)])
    assert draft not in public_text(frames)
    assert approval(frames)["verdict"] == "limited"
    assert len(harness.provider.requests) <= 2
