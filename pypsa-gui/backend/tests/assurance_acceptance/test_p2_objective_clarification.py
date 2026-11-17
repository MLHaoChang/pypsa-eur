"""assistant-assurance 10 S3 (UX 25 Q1a/Q2a): "objective" alone asks which one.

A message naming the objective without a cost, or the objective against the
cost, gets one choice card with exactly three labels; no number is published
until one is picked. A bare "total cost" keeps today's route.

Hand values from `baseline`: G1 at 10 EUR/MWh serves L1's 5 MW for 2 hours
with weight 1, so the objective is 5 x 2 x 10 = 100 EUR. There is no fixed
cost, no slack generator and no reserve, so the reported total is 100 EUR and
every bridge term and the residual are 0.
"""
# ruff: noqa: F811 -- imported shared fixtures.
import pytest

from tests.assurance_acceptance.support import approval
from tests.assurance_acceptance.test_p2_request_slots import asked, blocks, card, chat, slots  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_capacity_cycles_assurance import solve
from tests.test_workflow_tools import baseline  # noqa: F401

LABELS = ["Solver objective", "Total declared-basis cost", "Objective-to-cost reconciliation"]
BRIDGE = {
    "objective_eur": 100,
    "reported_total_cost_eur": 100,
    "penalty_cost_eur": 0,
    "nonextendable_fixed_cost_eur": 0,
    "period_weighting_adjustment_eur": 0,
    "reserve_revenue_eur": 0,
    "residual_eur": 0,
}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "objective-bridge/1")


@pytest.fixture
def solved(baseline):
    return solve(baseline[0])


@pytest.mark.parametrize(
    "message",
    [
        "What is the objective?",
        "Show me the solver objective for the network",
        "What's the objective vs the total system cost?",
        "show the objective versus cost.",
    ],
)
def test_the_objective_alone_asks_which_with_exactly_three_labels(solved, chat, message):
    frames, provider = chat(message)
    text = asked(frames, provider, reason="objective_or_cost_required")
    assert card(frames) == LABELS
    assert "100" not in text


@pytest.mark.parametrize(
    "message",
    [
        "What is the total cost of the network?",
        "Show me the total system cost",
        "show me the cost for network",
        "What is the objective function of a unit commitment?",
    ],
)
def test_a_bare_total_cost_keeps_todays_route(message):
    from types import SimpleNamespace

    from harness import assurance_conversation

    assert assurance_conversation._objective(SimpleNamespace(), message, None, None) is None


def test_solver_objective_answers_the_objective_only(solved, chat, checked):
    chat("What is the objective?")
    frames, provider = chat("Solver objective", {"objective_eur": 100})
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"] is True
    assert blocks(checked, frames) == {"objective_eur": (pytest.approx(100), "EUR")}


def test_the_reconciliation_answers_every_row_in_bridge_order(solved, chat, checked):
    chat("What is the objective?")
    frames, _ = chat("objective-to-cost reconciliation.", BRIDGE)
    assert list(slots(frames)) == list(BRIDGE)
    assert blocks(checked, frames) == {
        metric: (pytest.approx(value, abs=1e-6), "EUR") for metric, value in BRIDGE.items()
    }


def test_total_declared_basis_cost_asks_for_its_basis_like_today(solved, chat):
    chat("What is the objective?")
    frames, provider = chat("Total declared-basis cost")
    asked(frames, provider, reason="explicit_cost_basis_required")


def test_a_reply_outside_the_three_labels_is_not_taken_as_a_choice(solved, chat):
    chat("What is the objective?")
    frames, _ = chat("Marginal dispatch expenditure")
    reasons = {s.get("reason") for s in approval(frames)["receipt"]["result_slots"]}
    assert reasons == {"unsupported_followup"}
