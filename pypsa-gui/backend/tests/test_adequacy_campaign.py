"""
The campaign budget — what makes a chain of studies one thing.

Every engine caps itself. Nothing capped chaining them, so an agent asked to
"hit LOLE <= 3 h/yr at least cost" could reach for a frontier, two loops and a
sweep — each call inside its own limit, ~50 full capacity-expansion solves in
total, unattended, on a shared solver.

The two properties that matter: the estimates are the ENGINES' own promises
(a budget built on restated constants lies the moment one of them moves), and
a study that fails to start does not burn budget.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools as T
from services.adequacy import campaign as C


@pytest.fixture(autouse=True)
def _clean_campaign():
    C.reset()
    yield
    C.reset()


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B1", carrier="gas", p_nom=200.0)
    return n


# ── Lifecycle ──────────────────────────────────────────────────────────────


def test_a_campaign_needs_a_stated_objective():
    """It is what the budget is being spent ON."""
    with pytest.raises(C.CampaignError, match="objective"):
        C.start("   ")


def test_the_default_budget_is_thirty_solves():
    assert C.DEFAULT_BUDGET_SOLVES == 30
    assert C.start("hit LOLE <= 3 h/yr")["budget_solves"] == 30


def test_a_second_campaign_is_refused_while_one_is_open():
    """Starting a second would silently discard the first one's log."""
    C.start("first")
    with pytest.raises(C.CampaignError, match="already running"):
        C.start("second")


def test_a_refusal_does_not_quote_the_running_objective_back():
    """
    Review finding 5. The objective is free text somebody typed, and on a
    shared context that somebody need not be the caller — the shape of the
    lock-holder-email finding. campaign_status is the authorised way to read
    it.
    """
    C.start("decommission the Hunterston units before 2032")
    with pytest.raises(C.CampaignError) as exc:
        C.start("something else")
    assert "Hunterston" not in str(exc.value)
    assert "campaign_status" in str(exc.value)


def test_the_budget_refusal_does_not_quote_it_either():
    C.start("decommission the Hunterston units before 2032", 1)
    C.record("frontier", 1)
    with pytest.raises(C.CampaignBudgetError) as exc:
        C.check("frontier", 5)
    assert "Hunterston" not in str(exc.value)


def test_the_campaign_lives_in_the_project_context_not_a_module_global():
    """
    Every study record this gates lives in the per-project context. A module
    global meant two tenants sharing one budget on a multi-user server.
    """
    from services.pypsa_service import PyPSAService

    C.start("x", 10)
    ctx = PyPSAService.get_active_context()
    assert C._CAMPAIGN_KEY in ctx.solver_state
    assert not hasattr(C, "_ACTIVE"), "the module global is gone"
    C.end()
    assert C._CAMPAIGN_KEY not in ctx.solver_state


@pytest.mark.parametrize("budget", [0, -1, C.MAX_BUDGET_SOLVES + 1])
def test_an_out_of_range_budget_is_refused(budget):
    with pytest.raises(C.CampaignError, match="budget_solves"):
        C.start("x", budget)


def test_status_is_inactive_before_and_after():
    assert C.status()["active"] is False
    C.start("x")
    assert C.status()["active"] is True
    C.end()
    assert C.status()["active"] is False


def test_ending_returns_the_log_and_clears_the_server():
    C.start("x", 10)
    C.record("frontier", 4)
    final = C.end(note="done")
    assert final["active"] is False
    assert final["spent_solves"] == 4
    assert final["entries"][0]["study"] == "frontier"
    assert final["note"] == "done"
    assert C.status()["entries"] == []


def test_ending_nothing_is_refused():
    with pytest.raises(C.CampaignError, match="no campaign"):
        C.end()


# ── The budget ─────────────────────────────────────────────────────────────


def test_spending_accumulates_and_remaining_falls():
    C.start("x", 30)
    C.record("frontier", 12)
    C.record("margin_loop", 9)
    status = C.status()
    assert status["spent_solves"] == 21
    assert status["remaining_solves"] == 9


def test_a_study_that_would_overrun_is_refused_with_the_numbers():
    C.start("hit LOLE <= 3 h/yr", 10)
    C.record("frontier", 8)
    with pytest.raises(C.CampaignBudgetError) as exc:
        C.check("margin_loop", 9)
    message = str(exc.value)
    assert "9" in message and "2 of 10" in message
    # The objective is deliberately NOT quoted — see
    # test_the_budget_refusal_does_not_quote_it_either. The numbers are what
    # the agent needs to decide; the objective it already knows.
    assert "hit LOLE <= 3 h/yr" not in message


def test_a_study_that_exactly_fits_is_allowed():
    C.start("x", 10)
    C.record("frontier", 8)
    C.check("margin_loop", 2)          # must not raise


def test_the_gate_is_inert_with_no_campaign_running():
    """A single study asked for directly is not a campaign."""
    C.check("frontier", 10_000)        # must not raise
    assert C.record("frontier", 12) is None


def test_an_unknown_study_is_refused():
    with pytest.raises(C.CampaignBudgetError, match="unknown study") as exc:
        C.check("guesswork", 1)
    assert exc.value.detail["error_kind"] == "unknown_study"


def test_a_budget_refusal_is_typed_for_the_error_path():
    """
    The dispatcher reads `exc.detail['error_kind']`. Without it a refusal the
    agent must answer by REPORTING arrives as an indistinguishable
    `tool_error` it is likely to retry.
    """
    C.start("x", 1)
    with pytest.raises(C.CampaignBudgetError) as exc:
        C.check("frontier", 12)
    assert exc.value.detail["error_kind"] == "campaign_budget_exhausted"
    assert "12" in exc.value.detail["message"]


# ── The estimates ──────────────────────────────────────────────────────────


def test_monte_carlo_is_charged_nothing_because_it_solves_nothing():
    """
    Charging it would price the one study that can be run freely as if it were
    the most expensive.
    """
    assert C.estimate_solves(_network(), "mc", draws=2000) == 0


def test_the_frontier_costs_one_solve_per_target_plus_its_restore():
    """
    Review finding 4: the closing `_restore_base` is a full re-solve the
    route's own budget excludes, so the worst case is targets + 1.
    """
    n = _network()
    assert C.estimate_solves(n, "frontier",
                             targets_permyriad=[1.0, 2.0, 3.0]) == 4


def test_the_frontier_default_matches_the_engine_not_a_restated_constant():
    from services.adequacy.frontier import DEFAULT_TARGETS_PERMYRIAD

    assert C.estimate_solves(_network(), "frontier") == len(
        DEFAULT_TARGETS_PERMYRIAD) + 1


def test_a_loop_costs_its_own_budget_plus_the_closing_restore():
    """
    `coupling.py`: "the wall-time budget the route promises is
    `max_solves + 1` (the closing restore is outside it)". Charging only
    `max_solves` let a campaign overrun by one solve per study.
    """
    from services.adequacy.coupling import MAX_LOOP_SOLVES

    n = _network()
    assert C.estimate_solves(n, "coupling_loop") == MAX_LOOP_SOLVES + 1
    assert C.estimate_solves(n, "coupling_loop", max_solves=3) == 4


def test_the_margin_loop_pays_for_its_probe_as_well():
    """
    It pays TWO solves outside its budget: the probing solve that measures its
    starting margin (spec 2.3) and the closing restore every study runs.
    """
    n = _network()
    assert C.estimate_solves(n, "margin_loop", max_solves=3) == 5
    assert (C.estimate_solves(n, "margin_loop", max_solves=3)
            == C.estimate_solves(n, "coupling_loop", max_solves=3) + 1)


def test_the_sweep_charges_its_contingencies_plus_the_closing_re_solve():
    n = _network()
    n.add("Carrier", "hydrogen")
    n.add("Bus", "B2")
    for i in range(3):
        n.add("Link", f"link{i}", bus0="B1", bus1="B2", carrier="hydrogen",
              p_nom=50.0, outage_rate_value=0.05, mttr_hours=40.0)
    from services.adequacy.sweep import class_b_contingencies

    expected = len(class_b_contingencies(n)) + 1
    assert C.estimate_solves(n, "fmea_sweep") == expected
    assert C.estimate_solves(n, "fmea_sweep",
                             scenarios=[{"a": 1}, {"b": 2}]) == expected + 2


# ── Wiring into the study tools ────────────────────────────────────────────


def test_tools_are_registered_and_tiered():
    from services import chat_service
    from services import chat_tools_schema as S

    names = {"start_campaign", "campaign_status", "end_campaign"}
    assert names <= set(T.DISPATCHERS)
    assert names <= {t["name"] for t in S.TOOLS}
    assert chat_service._safety_tier_for("campaign_status") == "read"
    assert chat_service._safety_tier_for("start_campaign") == "write"
    assert chat_service._safety_tier_for("end_campaign") == "write"


def test_a_failed_start_does_not_burn_budget(install_network):
    """
    Check-then-record, never charge-then-refund. The frontier refuses without
    a VOLL — and a refusal that costs 12 solves would make the budget a lie.
    """
    install_network(_network())
    T.start_campaign("hit LOLE <= 3 h/yr", 30)
    with pytest.raises(HTTPException) as exc:
        T.run_frontier_study()
    assert exc.value.status_code == 422
    assert T.campaign_status()["spent_solves"] == 0
    assert T.campaign_status()["entries"] == []


def test_an_overrunning_study_is_refused_before_it_starts(install_network):
    install_network(_network())
    T.start_campaign("x", 2)
    with pytest.raises(C.CampaignBudgetError):
        T.run_frontier_study(targets_permyriad=[1.0, 2.0, 3.0])
    assert T.campaign_status()["spent_solves"] == 0


def test_the_study_tools_are_unchanged_with_no_campaign(install_network):
    """
    The gate must not alter behaviour for a user who never opens a campaign —
    the frontier still refuses for its own reason, not the budget's.
    """
    install_network(_network())
    with pytest.raises(HTTPException) as exc:
        T.run_frontier_study()
    assert exc.value.status_code == 422


def test_monte_carlo_runs_free_inside_an_exhausted_campaign(install_network):
    """
    The zero charge has to hold at the boundary too: a campaign with nothing
    left can still sample, because sampling costs no solves.
    """
    install_network(_network())
    T.start_campaign("x", 1)
    C.record("frontier", 1)
    assert T.campaign_status()["remaining_solves"] == 0
    C.check("mc", C.estimate_solves(T.PyPSAService.get_network(), "mc"))


def test_the_system_prompt_teaches_the_campaign_loop():
    from services import chat_service

    prompt = chat_service._build_system_prompt(chat_service.ChatSession())
    assert "start_campaign" in prompt
    assert "campaign_status before choosing" in prompt
    assert "not by your counting" in prompt
