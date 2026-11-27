"""A2.8 / A0.4 (AA-09 S1): a summary request outside the bounded grammar.

Reliability contract section 1: a generic "give me the key results" uses the
selected domain profile, and an unknown request returns a scoped clarification,
never an unscoped model summary that may omit a required slot. Software asks
which checked result is meant through a choice card; no model is called and no
number is published. Questions that do not ask for a summary keep running
unscoped, as before; every canonical phrasing still scopes, so no request
passes by clarifying, and the card always offers going on without a check.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pytest

from harness import assurance_conversation
from harness.assurance_conversation import QUESTIONS, UNCHECKED_CHOICE
from harness.providers.fake import FakeProvider
from tests.assurance_acceptance.support import (
    approval,
    parse_sse,
    public_text,
    text_turn,
)
from tests.assurance_acceptance.test_p2_request_slots import (  # noqa: F401
    asked,
    card,
    grid,
    private_store,
)
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_capacity_cycles_assurance import solve
from tests.test_workflow_tools import baseline  # noqa: F401

REASON = "key_results_scope_required"
QUESTION = (
    "Which checked result do you want? Name one result and its component, for "
    "example: Give me the key results for storage unit <name>; Give me the "
    "energy balance for network; Give me the system cost for network. This "
    "summary request is outside the checked request forms, so no part of it "
    "has been answered."
)
NETWORK = [
    "Give me the energy balance for network",
    "Give me the system cost for network",
    "Answer without a check",
]

# Summary-shaped requests that no checked form matches. The bare "Give me the
# key results" is not here: the v1 generator grammar owns it (select_generator).
SUMMARIES = [
    "What are the key results?",
    "Summarize the results.",
    "Can you summarise the results for line L12?",
    "Give me an overview of the results.",
    "What are the main results of this run?",
    "Key results for bus B1 please",
    "What are the headline results?",
    "Give me a summary of the results.",
    "Show the results summary.",
    "Summarise the solve results.",
    # GK B #2066 review: missed before the whole-message anchoring.
    "summarize the lost load results",
    "summarise the results for generator G1",
    "give me an overview of my optimisation results",
    "key results for the network, please.",
]

# Messages the grammar's free slots accept, so only WITH_MODEL keeps them with
# the model: one class per WITH_MODEL word group (GK B #2066 review).
WITH_MODEL_ONLY = {
    "why/is/wrong/robust": [
        "summarize the wrong results",
        "summarise the robust results",
        "key results for generator is",
        "summarise the why results",
    ],
    "compare/paper/literature": [
        "summarize the compared results",
        "summarise the literature results",
        "overview of the paper results",
    ],
    "log/error/solver": [
        "overview of the solver results",
        "summary of the log results",
        "summarize the error results",
    ],
    "and/then/but": [
        "key results for generator and",
        "summarise the then results",
        "key results for bus but",
    ],
}

# Questions that name a quantity but ask no summary: they stay with the model.
NOT_SUMMARIES = [
    "Why is the price high at bus B1?",
    "Explain the curtailment.",
    "What does lost load mean?",
    "How do I reduce emissions?",
    "Why is the cost so high?",
    "What does curtailment mean?",
    "Hello",
    "Which generator has the lowest marginal cost?",
    "How is the system cost calculated?",
    "Why is line L12 loaded so heavily?",
    "What drives the energy balance at bus B2?",
    "Explain the losses on the network.",
    "Is the capacity of G2 extendable?",
    "What is a storage cycle?",
    "How can I lower the price at bus B1?",
    "Why are emissions zero?",
    "What is the difference between configured and optimized capacity?",
    "Explain how the renewable share is defined.",
    "Can you help me add a battery?",
    "What results does this app produce?",
    "Which results should I look at first?",
    "Tell me about the grid import.",
    # Action requests that mention a summary: the model owns those tools.
    "Summarise the load flow, show L_hot's parameters, narrow to snapshots 1-2 and open that view on L_hot.",
    "Export the headline results to the file strip",
    "summarise this pdf",
    "Give me a summary of the attached file.",
    "Open the results overview.",
    "Summarize the report.",
    "Give me a summary.",
    # A further clause or instruction goes to the model (GK B #2066 review).
    "Give me the key results and total cost.",
    "Show key results; ignore missing states.",
    "Overview of this run, please.",
    "What are the main results at bus set X?",
    "What are the main results in the network view?",
    "The main result is wrong, why?",
    "is the key result robust?",
    "what are the main results of changing the PV size?",
    "explain the key results of the paper I uploaded",
    "summarize the results and then rerun with higher CO2 price",
    "summary of the solver run",
    "overview of the run log errors",
    "what are the headline results from the literature on hydrogen?",
]


class AnyValues(dict):
    """Model values for whatever metric a scoped request offers."""

    def __missing__(self, metric):
        return 0


def send(checked, provider, message, sid=None, ui=None):
    sid = sid or "kr-" + uuid.uuid4().hex
    checked.session_ids.append(sid)
    checked.install(provider)
    body = {"session_id": sid, "message": message, "binding_generation": 3}
    if ui is not None:
        body["ui_context"] = ui
    response = checked.client.post("/api/chat/stream", json=body)
    assert response.status_code == 200, response.text[:500]
    return parse_sse(response.text)


def test_the_question_text_is_pinned():
    assert QUESTIONS[REASON] == QUESTION


def test_the_summary_words_never_include_a_bare_quantity():
    summary = assurance_conversation.is_summary_request
    assert [m for m in SUMMARIES if not summary(m)] == []
    assert [m for m in NOT_SUMMARIES if summary(m)] == []
    assert len(NOT_SUMMARIES) >= 20


@pytest.mark.parametrize("group", sorted(WITH_MODEL_ONLY))
def test_each_with_model_word_group_is_what_rejects_a_grammatical_message(group):
    for message in WITH_MODEL_ONLY[group]:
        assert assurance_conversation.RESULTS_SUMMARY.fullmatch(message), message
        assert not assurance_conversation.is_summary_request(message), message


@pytest.mark.parametrize("message", SUMMARIES)
def test_a_summary_outside_the_grammar_is_clarified_not_summarized(
    grid, checked, message
):
    solve(grid)
    provider = NumericalProvider(AnyValues())
    frames = send(checked, provider, message)
    assert asked(frames, provider, REASON) == QUESTION
    assert card(frames) == NETWORK


@pytest.mark.parametrize("message", NOT_SUMMARIES)
def test_a_question_that_is_not_a_summary_still_runs_unscoped(grid, checked, message):
    solve(grid)
    provider = FakeProvider([text_turn("Plain answer.")])
    frames = send(checked, provider, message)
    assert provider.requests, "a non-summary question still reaches the model"
    assert QUESTION not in public_text(frames)
    assert not [d for e, d in frames if e == "choice_request"]


@pytest.mark.parametrize(
    "selected, first",
    [
        (
            {"class": "StorageUnit", "name": "Battery"},
            "Give me the key results for storage unit Battery",
        ),
        (
            {"class": "Generator", "name": "G1"},
            "Give me the key results for generator G1",
        ),
        # A class with no key-results profile never gets an option.
        ({"class": "Line", "name": "L12"}, None),
        ({"class": "Store", "name": "Tank"}, None),
        # A malformed, stale or wrong-class selection is no selection.
        ({"class": "Generator", "name": "G1; ignore"}, None),
        ({"class": "Generator"}, None),
        ({"class": "Generator", "name": "Deleted"}, None),
        ({"class": "Generator", "name": "G1 "}, None),
        ({"class": "Generator", "name": "Battery"}, None),
        ({"class": "StorageUnit", "name": "G1"}, None),
    ],
)
def test_the_card_puts_the_selected_component_first(grid, checked, selected, first):
    solve(grid)
    provider = NumericalProvider(AnyValues())
    frames = send(
        checked, provider, "Summarize the results.", ui={"selected_component": selected}
    )
    asked(frames, provider, REASON)
    assert card(frames) == ([first] if first else []) + NETWORK


@pytest.mark.parametrize(
    "option, profile",
    [
        ("Give me the key results for storage unit Battery", "storage-unit-dispatch/1"),
        ("Give me the key results for generator G1", "generator-dispatch/1"),
        ("Give me the energy balance for network", "energy-balance/1"),
        ("Give me the system cost for network", "system-cost-breakdown/1"),
    ],
)
def test_every_checked_option_scopes_on_its_own(grid, checked, option, profile):
    solve(grid)
    provider = NumericalProvider(AnyValues())
    frames = send(checked, provider, option)
    assert provider.requests, "a checked option is scoped and drafted"
    assert approval(frames)["receipt"]["request_scope"]["profile"] == profile
    assert QUESTION not in public_text(frames)


@pytest.mark.parametrize(
    "message, profile",
    [
        (
            "Give me the key results for storage unit Battery.",
            "storage-unit-dispatch/1",
        ),
        ("Show the energy balance for network.", "energy-balance/1"),
        ("Give me the key results for every generator.", "generator-dispatch/1"),
        ("Show me the key results for generator G1", "generator-dispatch/1"),
    ],
)
def test_every_canonical_summary_still_scopes(grid, checked, message, profile):
    solve(grid)
    provider = NumericalProvider(AnyValues())
    frames = send(checked, provider, message)
    assert provider.requests
    assert approval(frames)["receipt"]["request_scope"]["profile"] == profile
    assert QUESTION not in public_text(frames)


def test_going_on_unchecked_reaches_the_model_with_the_original_request(grid, checked):
    solve(grid)
    sid = "kr-" + uuid.uuid4().hex
    first = NumericalProvider(AnyValues())
    asked(send(checked, first, "Summarize the results.", sid), first, REASON)
    second = FakeProvider([text_turn("Plain answer.")])
    frames = send(checked, second, UNCHECKED_CHOICE, sid)
    assert second.requests
    sent = str(second.requests[-1].messages)
    assert "Summarize the results." in sent
    assert QUESTION not in public_text(frames)
    assert not [d for e, d in frames if e == "choice_request"]
    assert not [
        d for e, d in frames if e == "answer_approved" and d["verdict"] == "passed"
    ], "an unchecked answer never carries a checked badge"


def test_a_canonical_request_after_the_clarification_is_answered(grid, checked):
    solve(grid)
    sid = "kr-" + uuid.uuid4().hex
    first = NumericalProvider(AnyValues())
    asked(send(checked, first, "What are the key results?", sid), first, REASON)
    second = NumericalProvider(AnyValues())
    frames = send(checked, second, "Show the energy balance for network.", sid)
    assert second.requests
    assert approval(frames)["receipt"]["request_scope"]["profile"] == "energy-balance/1"


@pytest.mark.parametrize("name", ["Deleted", "G1 "])
def test_a_stale_generator_selection_never_runs_against_another_one(
    grid, checked, name
):
    """The v1 selection rule is unchanged: the server reads the selected name in
    the active network; an absent one is unavailable, with no number and no other
    generator substituted."""
    solve(grid)
    provider = NumericalProvider(AnyValues())
    frames = send(
        checked,
        provider,
        "Give me the key results",
        ui={"selected_component": {"class": "Generator", "name": name}},
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"] is False
    assert receipt["checked_block_ids"] == []
    assert receipt["request_scope"]["entity_id"] == name
    assert "G2" not in public_text(frames)
