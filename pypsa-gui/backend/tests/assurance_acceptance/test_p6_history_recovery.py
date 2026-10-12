"""A6: current public replay/export cannot retain private rejected model prose."""

import json

import pytest

from tests.assurance_acceptance.support import UNSAFE, approval, public_text, text_turn


@pytest.mark.parametrize("route", ["/api/chat/history", "/api/chat/export"])
def test_rejected_draft_does_not_enter_public_history_or_export(
    baseline, harness, route
):
    frames = harness.run([text_turn(UNSAFE, thinking=UNSAFE), text_turn(UNSAFE)])
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    response = harness.client.get(route)
    assert response.status_code == 200
    record = response.json()["turns"][-1]
    assert record["answer_id"] == approved["answer_id"]
    assert record["receipt"] == approved["receipt"]
    text = "".join(
        block.get("text", "")
        for block in record["assistant"]
        if block.get("type") == "text"
    )
    assert text.strip() and text == public_text(frames)
    assert UNSAFE not in json.dumps(response.json()), (
        "Private unchecked draft survived public replay"
    )


def test_history_rehydrates_approved_answer_identity_and_receipt(baseline, harness):
    approved = approval(harness.load_fact())
    response = harness.client.get("/api/chat/history")
    assert response.status_code == 200
    turn = response.json()["turns"][-1]
    assert turn["answer_id"] == approved["answer_id"]
    assert turn["receipt"] == approved["receipt"]


def test_approved_answer_fetch_does_not_execute_tools_or_model_again(baseline, harness):
    approved = approval(harness.load_fact())
    count = len(harness.provider.requests)
    for _ in range(3):
        response = harness.client.get(f"/api/chat/answers/{approved['answer_id']}")
        assert response.status_code == 200
    assert len(harness.provider.requests) == count
