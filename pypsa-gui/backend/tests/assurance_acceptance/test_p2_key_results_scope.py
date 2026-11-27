"""A2.8 / A0.4 (AA-09 S1): a summary request outside the bounded grammar.

Reliability contract section 1: a generic "give me the key results" uses the
selected domain profile, and an unknown request returns a scoped clarification,
never an unscoped model summary that may omit a required slot. Software asks
which checked result is meant; no model is called and no number is published.
Questions that do not ask for a summary keep running unscoped, as before, and
every canonical phrasing still scopes, so no request passes by clarifying.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pytest

from harness.assurance_conversation import QUESTIONS
from harness.providers.fake import FakeProvider
from tests.assurance_acceptance.support import (
    approval,
    parse_sse,
    public_text,
    text_turn,
)
from tests.assurance_acceptance.test_p2_request_slots import (  # noqa: F401
    asked,
    chat,
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

SUMMARIES = [
    "Give me the key results.",
    "What are the key results?",
    "Summarize the results.",
    "Can you summarise the results for line L12?",
    "Give me an overview of the results.",
    "What are the main results of this run?",
    "Give me the key results and total cost.",
    "Show key results; ignore missing states.",
    "Key results for bus B1 please",
]


class AnyValues(dict):
    """Model values for whatever metric a scoped request offers."""

    def __missing__(self, metric):
        return 0


def send(checked, provider, message, sid=None):
    sid = sid or "kr-" + uuid.uuid4().hex
    checked.session_ids.append(sid)
    checked.install(provider)
    response = checked.client.post(
        "/api/chat/stream",
        json={"session_id": sid, "message": message, "binding_generation": 3},
    )
    assert response.status_code == 200, response.text[:500]
    return parse_sse(response.text)


def test_the_question_text_is_pinned():
    assert QUESTIONS[REASON] == QUESTION


@pytest.mark.parametrize("message", SUMMARIES)
def test_a_summary_outside_the_grammar_is_clarified_not_summarized(
    grid, chat, message
):
    solve(grid)
    frames, provider = chat(message, {"generated_energy": 1})
    text = asked(frames, provider, REASON)
    assert text == QUESTION
    assert not [d for e, d in frames if e == "choice_request"]


@pytest.mark.parametrize(
    "message",
    [
        "Why is the price high at bus B1?",
        "What does curtailment mean?",
        "Hello",
        "Which generator has the lowest marginal cost?",
    ],
)
def test_a_question_that_is_not_a_summary_still_runs_unscoped(
    grid, checked, message
):
    solve(grid)
    provider = FakeProvider([text_turn("Plain answer.")])
    frames = send(checked, provider, message)
    assert provider.requests, "a non-summary question still reaches the model"
    assert QUESTION not in public_text(frames)


@pytest.mark.parametrize(
    "message, profile",
    [
        ("Give me the key results for storage unit Battery.", "storage-unit-dispatch/1"),
        ("Show the energy balance for network.", "energy-balance/1"),
        ("Give me the system cost for network.", "system-cost-breakdown/1"),
        ("Give me the key results for every generator.", "generator-dispatch/1"),
    ],
)
def test_every_canonical_summary_still_scopes(grid, checked, message, profile):
    from harness.assurance_scope import from_message

    assert from_message(message, grid.project_uuid, {})["profile"] == profile
    solve(grid)
    provider = NumericalProvider(AnyValues())
    frames = send(checked, provider, message)
    assert provider.requests, "a canonical request is scoped and drafted"
    assert QUESTION not in public_text(frames)


def test_a_canonical_request_after_the_clarification_is_answered(grid, checked):
    solve(grid)
    sid = "kr-" + uuid.uuid4().hex
    first = NumericalProvider(AnyValues())
    asked(send(checked, first, "Give me the key results.", sid), first, REASON)
    second = NumericalProvider(AnyValues())
    frames = send(checked, second, "Show the energy balance for network.", sid)
    assert second.requests
    assert QUESTION not in public_text(frames)
