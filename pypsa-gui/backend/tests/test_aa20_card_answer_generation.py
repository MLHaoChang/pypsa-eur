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

from harness import assurance_conversation
from harness.providers.fake import FakeProvider
from tests.assurance_acceptance.support import (
    approval,
    parse_sse,
    public_text,
    text_turn,
)
from tests.assurance_acceptance.test_p2_request_slots import blocks, grid  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_capacity_cycles_assurance import PROFILES, solve
from tests.test_conversation_scope import clarified
from tests.test_harness_reliability import VALUES, scope
from services import workflow_tools
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def client_chat(checked):
    """One chat session whose every send carries the next generation, as ChatPanel does."""
    sid = "aa20-" + uuid.uuid4().hex
    checked.session_ids.append(sid)
    sent = []

    def send(message, values=None, provider=None, **options):
        provider = checked.install(
            provider or NumericalProvider(values if values is not None else {})
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
    frames, provider = client_chat("What is the capacity of generator G1?")
    clarified(frames, provider, "capacity_meaning_required")
    frames, provider = client_chat("Configured capacity", {"configured_capacity": 20})
    assert client_chat.generations == [1, 2]
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"] is True
    assert blocks(checked, frames) == {"configured_capacity": (pytest.approx(20), "MW")}


def test_repeat_followup_on_a_new_generation_keeps_the_checked_scope(
    solved, client_chat
):
    first, _ = client_chat("Check this run.", VALUES, checked_scope=scope(solved))
    assert approval(first)["receipt"]["request_complete"]
    frames, provider = client_chat("Show those results again.", VALUES)
    assert client_chat.generations == [1, 2]
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"]


def test_card_answer_after_an_interleaved_turn_is_refused(
    solved, client_chat, checked, monkeypatch
):
    # G4: only owner and project are compared now, so the anchor check is what stops
    # a stale card answer: a turn in between leaves the card no longer the last answer.
    reasons = []
    state = assurance_conversation._state

    def spy(turn, ui):
        found = state(turn, ui)
        reasons.append(found[1])
        return found

    monkeypatch.setattr(assurance_conversation, "_state", spy)
    frames, provider = client_chat("Show me cost for generator G1.")
    clarified(frames, provider, "cost_quantity_required")
    client_chat("Hello.", provider=FakeProvider([text_turn("Hello.")]))
    reasons.clear()
    frames, _ = client_chat(
        "Total declared-basis cost", provider=FakeProvider([text_turn("Which one?")])
    )
    assert client_chat.generations == [1, 2, 3]
    assert reasons == ["conversation_scope_required"]
    assert not [d for e, d in frames if e == "choice_request"]
    assert "price year" not in public_text(frames).lower()


def test_compare_after_a_repeat_on_new_generations_keeps_the_baseline(
    solved, client_chat
):
    # The checked-run history carries over from one send to the next (the entries
    # carry-over compares owner and project, not the generation).
    first, _ = client_chat("Check this run.", VALUES, checked_scope=scope(solved))
    assert approval(first)["receipt"]["request_complete"]
    solved.network.generators.at["G1", "p_nom"] = 30
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    changed = {
        **VALUES,
        "annual_fixed_cost": 90,
        "allocated_fixed_cost": 90,
        "total_cost": 260,
    }
    frames, _ = client_chat("Show those results again.", changed)
    assert approval(frames)["receipt"]["request_complete"]
    frames, _ = client_chat(
        "Compare that with the previous run.",
        {**changed, "cost_delta": 30, "cost_change_percent": 30 / 230 * 100},
    )
    assert client_chat.generations == [1, 2, 3]
    assert approval(frames)["receipt"]["request_complete"]
    assert "30 EUR" in public_text(frames)
