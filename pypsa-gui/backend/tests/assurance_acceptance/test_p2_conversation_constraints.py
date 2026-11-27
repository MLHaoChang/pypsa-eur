"""A2.8 / A6.6 (AA-09 S3): user constraints carried across turns.

Reliability contract section 1: "Preserve user constraints across turns." A
follow-up that switches the entity ("same for generator G2") keeps the checked
window, cost basis and metrics of the previous checked answer. The new entity
must exist in the active network under the same component class. The carryover
is revoked when the previous checked source changed (an edit or a new solve).
A request that adds an explicit filter ("excluding", "only", "without") that
no checked form supports gets a software clarification; the filter is never
dropped. Expected values are worked by hand below.

Network: one bus, loads 5 MW then 7 MW, generator weights 2 h and 1 h.
G1: 6 MW, 10 EUR/MWh, capital cost 3 EUR/MW; G2: 20 MW, 20 EUR/MWh, 1 EUR/MW.
Merit order: G1 5 MW then 6 MW; G2 0 MW then 1 MW.
Whole horizon  G1: 5*2 + 6*1 = 16 MWh, 160 EUR; G2: 1 MWh, 20 EUR.
Second hour    G1: 6 MWh, 60 EUR, peak 6 MW; G2: 1 MWh, 20 EUR, peak 1 MW.
Fixed (fraction 1): G1 3*6 = 18 EUR; G2 1*20 = 20 EUR.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pandas as pd
import pytest

from harness import assurance_conversation
from harness.assurance_conversation import QUESTIONS
from harness.providers.fake import FakeProvider
from services import chat_service, workflow_tools
from tests.assurance_acceptance.support import (
    approval,
    parse_sse,
    public_text,
    text_turn,
)
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

HOUR = "2026-01-01T01:00:00"
BASIS = {"currency": "EUR", "price_year": 2026, "annual_fixed_cost_fraction": 1}
G1_HOUR = {
    "dispatch_energy": 6,
    "marginal_expenditure": 60,
    "peak_dispatch": 6,
    "annual_fixed_cost": 18,
    "allocated_fixed_cost": 18,
    "total_cost": 78,
}
G2_HOUR = {
    "dispatch_energy": 1,
    "marginal_expenditure": 20,
    "peak_dispatch": 1,
    "annual_fixed_cost": 20,
    "allocated_fixed_cost": 20,
    "total_cost": 40,
}
CARRIED = "carried_entity_unavailable"
CHANGED = "carried_scope_changed"
CONSTRAINT = "unsupported_constraint"


def optimize(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


@pytest.fixture
def pair(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.generators.at["G1", "p_nom"] = 6
    n.generators.at["G1", "capital_cost"] = 3
    n.add("Generator", "G2", bus="B1", p_nom=20, marginal_cost=20, capital_cost=1)
    n.loads_t.p_set = pd.DataFrame({"L1": [5.0, 7.0]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2.0, 1.0]
    n.snapshot_weightings.loc[:, "objective"] = [2.0, 1.0]
    optimize(ctx)
    return ctx


@pytest.fixture
def chat(checked):
    sid = "carry-" + uuid.uuid4().hex
    checked.session_ids.append(sid)

    def send(message, provider=None, **options):
        provider = checked.install(provider or NumericalProvider({}))
        response = checked.client.post(
            "/api/chat/stream",
            json={
                "session_id": sid,
                "message": message,
                "binding_generation": 3,
                **options,
            },
        )
        assert response.status_code == 200, response.text[:500]
        return parse_sse(response.text), provider

    send.sid = sid
    return send


def first_scope(ctx, **overrides):
    return {
        "version": 1,
        "profile": "generator-cost/1",
        "project_id": ctx.project_uuid,
        "entity_id": "G1",
        "retain": False,
        "start": HOUR,
        "end": HOUR,
        "cost_basis": BASIS,
        **overrides,
    }


def seed(ctx, chat, **overrides):
    frames, _ = chat(
        "Check this window.",
        NumericalProvider(G1_HOUR),
        checked_scope=first_scope(ctx, **overrides),
    )
    assert approval(frames)["receipt"]["request_complete"], public_text(frames)


def clarified(frames, provider, reason):
    receipt = approval(frames)["receipt"]
    assert approval(frames)["verdict"] == "limited"
    assert receipt["request_complete"] is False
    assert receipt["checked_block_ids"] == [] and receipt["evidence_ids"] == []
    assert {s["disposition"] for s in receipt["result_slots"]} == {
        "needs_clarification"
    }
    assert {s["reason"] for s in receipt["result_slots"]} == {reason}
    assert not provider.requests, "a software clarification calls no model"
    return public_text(frames)


def test_the_hand_oracle_holds_for_the_first_entity(pair, chat):
    seed(pair, chat)


@pytest.mark.parametrize(
    "message",
    [
        "Same for generator G2.",
        "And the same for generator G2",
        "Now for generator G2.",
        "What about generator G2?",
    ],
)
def test_an_entity_switch_keeps_window_basis_and_metrics(pair, chat, message):
    seed(pair, chat)
    frames, _ = chat(message, NumericalProvider(G2_HOUR))
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"], public_text(frames)
    expected = {**first_scope(pair), "entity_id": "G2"}
    for key in ("profile", "project_id", "entity_id", "start", "end", "cost_basis"):
        assert receipt["request_scope"][key] == expected[key]
    assert "40 EUR" in public_text(frames) and "78 EUR" not in public_text(frames)


def test_a_switch_keeps_an_explicit_metric_subset(pair, chat):
    frames, _ = chat(
        "Check this window.",
        NumericalProvider({"total_cost": 78}),
        checked_scope=first_scope(pair, metrics=["total_cost"]),
    )
    assert approval(frames)["receipt"]["request_complete"]
    frames, _ = chat("Same for generator G2.", NumericalProvider({"total_cost": 40}))
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"]
    assert receipt["request_scope"]["metrics"] == ["total_cost"]


def test_the_switched_answer_seeds_the_next_followup(pair, chat):
    seed(pair, chat)
    chat("Same for generator G2.", NumericalProvider(G2_HOUR))
    frames, _ = chat("Same for generator G1.", NumericalProvider(G1_HOUR))
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"]
    assert receipt["request_scope"]["entity_id"] == "G1"
    assert receipt["request_scope"]["start"] == HOUR


@pytest.mark.parametrize(
    "message",
    [
        "Same for generator Nope.",
        "Same for storage unit G2.",
        "Same for line G2.",
        "Same for load L1.",
    ],
)
def test_a_missing_or_wrong_class_entity_is_clarified(pair, chat, message):
    seed(pair, chat)
    frames, provider = chat(message)
    assert clarified(frames, provider, CARRIED) == QUESTIONS[CARRIED]


def test_the_carryover_survives_a_recreated_session(pair, chat, checked):
    seed(pair, chat)
    chat_service.drop_session(chat.sid)
    assert checked.client.get("/api/chat/history").status_code == 200
    frames, _ = chat(
        "Same for generator G2.", NumericalProvider(G2_HOUR), binding_generation=8
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"], public_text(frames)
    assert receipt["request_scope"]["entity_id"] == "G2"
    assert receipt["request_scope"]["start"] == HOUR


def test_a_rewind_revokes_the_carryover(pair, chat):
    seed(pair, chat)
    session = chat_service.get_or_create_session(chat.sid)
    assert chat_service.rewind_session(session, turns=1) > 0
    frames, provider = chat("Same for generator G2.")
    clarified(frames, provider, "conversation_scope_required")


def test_a_project_change_revokes_the_carryover(pair, chat):
    from tests.test_conversation_scope import moved_binding

    seed(pair, chat)
    moved_binding(chat.sid, "project")
    frames, provider = chat("Same for generator G2.")
    clarified(frames, provider, "conversation_scope_changed")


def test_no_checked_context_never_guesses_an_entity_switch(pair, chat):
    frames, provider = chat("Same for generator G2.")
    clarified(frames, provider, "conversation_scope_required")


def test_a_new_solve_revokes_the_carryover(pair, chat):
    seed(pair, chat)
    pair.network.generators.at["G1", "p_nom"] = 7
    optimize(pair)
    frames, provider = chat("Same for generator G2.")
    assert clarified(frames, provider, CHANGED) == QUESTIONS[CHANGED]


def test_an_unsolved_edit_revokes_the_carryover(pair, chat):
    seed(pair, chat)
    pair.network.generators.at["G1", "marginal_cost"] = 11
    frames, provider = chat("Same for generator G2.")
    clarified(frames, provider, CHANGED)


def test_a_switch_from_a_comparison_needs_its_own_baseline(pair, chat):
    seed(pair, chat)
    pair.network.generators.at["G1", "capital_cost"] = 4
    optimize(pair)
    compared, _ = chat(
        "Compare that with the previous run.",
        NumericalProvider(
            {
                **G1_HOUR,
                "annual_fixed_cost": 24,
                "allocated_fixed_cost": 24,
                "total_cost": 84,
                "cost_delta": 6,
                "cost_change_percent": 6 / 78 * 100,
            }
        ),
    )
    assert approval(compared)["receipt"]["request_complete"], public_text(compared)
    frames, provider = chat("Same for generator G2.")
    reason = approval(frames)["receipt"]["result_slots"][0]["reason"]
    assert reason == "previous_compatible_run_required"
    assert not provider.requests


# ── explicit filter constraints ─────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Give me the system cost for network excluding taxes.",
        "Give me the system cost for network, only for wind.",
        "Excluding G2, give me the energy balance for network.",
        "Give me the energy balance for network without the battery",
        "Give me the energy balance for network except for bus B2.",
        "Show the energy balance for network; ignoring curtailment.",
        "Give me the system cost for network apart from taxes.",
        "Give me the energy balance for network other than bus B2.",
        # A head the grammar refuses (a repeated chart series) still flags the filter.
        "Give me the energy balance for network; chart wind, wind excluding taxes",
    ],
)
def test_a_checked_request_with_an_added_filter_is_clarified(pair, chat, message):
    frames, provider = chat(message)
    assert clarified(frames, provider, CONSTRAINT) == QUESTIONS[CONSTRAINT]


@pytest.mark.parametrize(
    "message",
    [
        "Excluding taxes, what's the cost?",
        "Why is the price high without wind?",
        "What are the emissions only for gas?",
        "Can the network run without the battery?",
        "Which generators run except at night?",
        "Explain the energy balance excluding losses.",
    ],
)
def test_filter_words_in_other_questions_stay_with_the_model(pair, chat, message):
    frames, provider = chat(message, FakeProvider([text_turn("Plain answer.")]))
    assert provider.requests
    assert QUESTIONS[CONSTRAINT] not in public_text(frames)


@pytest.mark.parametrize(
    "message",
    [
        "Give me the energy balance for network, please.",
        "Give me the energy balance for network please",
        "Give me the system cost for network, thanks.",
        "Give me the system cost for network thanks",
    ],
)
def test_politeness_after_a_checked_request_is_not_a_filter(message):
    assert not assurance_conversation._filtered(message, "p1", None)


def test_the_question_texts_are_pinned():
    assert QUESTIONS[CARRIED] == (
        "That component is not in the active network under the same component "
        "class as the previous checked result. Please name a component of that "
        "class, or state the full result scope."
    )
    assert QUESTIONS[CHANGED] == (
        "The network changed (an edit or a new solve) since the checked result "
        "this follow-up refers to, so its window and basis are not carried over. "
        "Please state the full result scope again."
    )
    assert QUESTIONS[CONSTRAINT] == (
        "That request adds a filter (such as excluding, only or without) that no "
        "checked result supports. Please ask for the result without the filter, "
        "or ask without a check; the filter has not been ignored."
    )
