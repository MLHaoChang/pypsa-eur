"""AA-20: a clarification-card answer sent the way the real chat client sends it.

ChatPanel raises ``binding_generation`` on every send
(``const generation = ++streamGenerationRef.current``), so the answer to a
card always carries a newer generation than the turn that asked. The other
backend tests send one fixed generation, which is how the refusal went
unnoticed. This module drives ``/api/chat/stream`` with the client's sequence
1, 2, 3, ... in one session and one project.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pytest

from tests.assurance_acceptance.support import approval, parse_sse, public_text
from tests.assurance_acceptance.test_p2_request_slots import blocks, grid  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_capacity_cycles_assurance import PROFILES, solve
from tests.test_conversation_scope import clarified
from tests.test_harness_reliability import VALUES, scope
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def client_chat(checked):
    """One chat session whose every send carries the next generation, as ChatPanel does."""
    sid = "aa20-" + uuid.uuid4().hex
    checked.session_ids.append(sid)
    sent = []

    def send(message, values=None, **options):
        provider = checked.install(
            NumericalProvider(values if values is not None else {})
        )
        sent.append(len(sent) + 1)
        response = checked.client.post(
            "/api/chat/stream",
            json={
                "session_id": sid,
                "message": message,
                "binding_generation": sent[-1],
                **options,
            },
        )
        assert response.status_code == 200, response.text[:500]
        return parse_sse(response.text), provider

    send.generations = sent
    return send


def test_cost_cards_answered_on_new_generations_reach_the_checked_answer(
    solved, client_chat
):
    frames, provider = client_chat("Show me cost for generator G1.")
    clarified(frames, provider, "cost_quantity_required")
    frames, provider = client_chat("Total declared-basis cost")
    assert "price year" in clarified(frames, provider, "explicit_cost_basis_required")
    frames, provider = client_chat(
        "Price year 2026; annual fixed cost fraction 1.", {"total_cost": 230}
    )
    assert client_chat.generations == [1, 2, 3]
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"]
    assert "230 EUR" in public_text(frames)


@pytest.fixture
def capacity_profiles(tmp_path, monkeypatch):
    """The capacity profiles on, as in test_p2_request_slots (its fixture is autouse there)."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(PROFILES))


def test_capacity_card_answered_on_a_new_generation_reaches_the_checked_answer(
    capacity_profiles, grid, client_chat, checked
):
    solve(grid)
    client_chat("What is the capacity of generator G1?")
    frames, provider = client_chat("Configured capacity", {"configured_capacity": 20})
    assert client_chat.generations == [1, 2]
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"] is True
    assert blocks(checked, frames) == {
        "configured_capacity": (pytest.approx(20), "MW")
    }


def test_repeat_followup_on_a_new_generation_keeps_the_checked_scope(
    solved, client_chat
):
    first, _ = client_chat("Check this run.", VALUES, checked_scope=scope(solved))
    assert approval(first)["receipt"]["request_complete"]
    frames, provider = client_chat("Show those results again.", VALUES)
    assert client_chat.generations == [1, 2]
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"]
