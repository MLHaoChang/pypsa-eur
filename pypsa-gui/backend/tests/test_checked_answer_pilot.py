"""Pilot opt-in uses the existing assurance path; history hints are not approval."""

# ruff: noqa: F811 -- shared fixtures.
import pytest

from services import chat_service
from tests.assurance_acceptance.support import (
    approval,
    parse_sse,
    public_text,
    text_turn,
)
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_storage_cost_assurance import COSTS, priced, scope  # noqa: F401
from tests.test_storage_assurance import private_store, storage  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def test_pilot_checks_storage_cost_only_for_an_explicit_checked_request(
    priced, checked, monkeypatch
):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "pilot")
    frames = checked.run(
        NumericalProvider({"total_cost": COSTS["total_cost"]}),
        checked_scope=scope(priced, metrics=["total_cost"]),
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "51 EUR" in public_text(frames)
    init = next(data for event, data in frames if event == "session_init")
    assert init["assurance"] == {"version": 1, "mode": "strict", "state": "checking"}
    checked.install([text_turn("Ordinary pilot reply.")])
    response = checked.client.post(
        "/api/chat/stream",
        json={"session_id": "ordinary-pilot", "message": "Reply normally."},
    )
    assert response.status_code == 200
    ordinary = parse_sse(response.text)
    assert "Ordinary pilot reply." in public_text(ordinary)
    assert not any(event == "answer_approved" for event, _ in ordinary)
    chat_service.drop_session("ordinary-pilot")


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
def test_rollback_refuses_a_checked_request_before_calling_a_provider(
    checked, monkeypatch, mode
):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", mode)
    provider = checked.install([text_turn("Unapproved value 999 EUR.")])
    response = checked.client.post(
        "/api/chat/stream",
        json={
            "message": "Check results.",
            "assurance": {"version": 1, "mode": "strict"},
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error_kind"] == "assurance_unavailable"
    assert not provider.requests


@pytest.mark.parametrize("invalidate", [False, True])
def test_checked_history_retains_retry_policy_without_conferring_approval(
    baseline, checked, monkeypatch, invalidate
):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "pilot")
    approved = approval(checked.load_fact())
    if invalidate:
        baseline[0].network.loads.at["L1", "p_set"] = 7
    response = checked.client.get("/api/chat/history")
    assert response.status_code == 200
    turn = response.json()["turns"][-1]
    assert turn["requires_checked"] is True
    if invalidate:
        assert "answer_id" not in turn and "receipt" not in turn
        assert turn["assistant"] == [
            {"type": "text", "text": "Previous checked answer is unavailable."}
        ]
    else:
        assert (
            turn["answer_id"] == approved["answer_id"]
            and turn["receipt"] == approved["receipt"]
        )
