"""Scope carryover through the authenticated stream, with independent LP values."""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pytest

from services import chat_service, workflow_tools, assurance_records
from tests.assurance_acceptance.support import approval, parse_sse, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_harness_reliability import VALUES, scope


@pytest.fixture
def conversation(checked):
    sid = "scope-" + uuid.uuid4().hex
    checked.session_ids.append(sid)

    def send(message, values=None, **options):
        provider = checked.install(
            NumericalProvider(values if values is not None else {})
        )
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
        frames = parse_sse(response.text)
        return frames, provider

    send.sid = sid
    return send


def clarified(frames, provider, reason):
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert approved["receipt"]["request_complete"] is False
    assert approved["receipt"]["checked_block_ids"] == []
    assert approved["receipt"]["evidence_ids"] == []
    assert approved["receipt"]["result_slots"]
    assert all(
        slot["disposition"] == "needs_clarification"
        for slot in approved["receipt"]["result_slots"]
    )
    assert all(slot["reason"] == reason for slot in approved["receipt"]["result_slots"])
    assert not provider.requests, "Software clarification must not call a model"
    return public_text(frames)


def test_followup_preserves_entity_exact_window_and_cost_basis(solved, conversation):
    original = scope(
        solved,
        metrics=["dispatch_energy"],
        start="2026-01-01T01:00:00",
        end="2026-01-01T01:00:00",
    )
    first, _ = conversation(
        "Check this window.", {"dispatch_energy": 7}, checked_scope=original
    )
    assert approval(first)["receipt"]["request_complete"]
    frames, _ = conversation("Show those results again.", {"dispatch_energy": 7})
    result = approval(frames)
    assert result["receipt"]["request_complete"]
    for key in ("project_id", "entity_id", "start", "end", "cost_basis", "metrics"):
        assert result["receipt"]["request_scope"][key] == original[key]
    assert "7 MWh" in public_text(frames) and "17 MWh" not in public_text(frames)


def test_generator_named_network_cannot_become_a_network_cost_request(
    solved, conversation
):
    component = solved.network.components["Generator"]
    component.static = component.static.rename(index={"G1": "network"})
    for attribute, values in component.dynamic.items():
        component.dynamic[attribute] = values.rename(columns={"G1": "network"})
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames, provider = conversation("Show cost for generator network.")
    clarified(frames, provider, "cost_quantity_required")
    frames, provider = conversation("Total declared-basis cost")
    clarified(frames, provider, "explicit_cost_basis_required")
    assert approval(frames)["receipt"]["request_scope"]["profile"] == "generator-cost/1"
    frames, _ = conversation(
        "Use price year 2026; annual fixed cost fraction 1", {"total_cost": 230}
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert approval(frames)["receipt"]["request_scope"]["profile"] == "generator-cost/1"
    assert "Generator network" in public_text(frames)
    assert "Network network" not in public_text(frames)


def test_software_clarification_is_retained_only_after_checked_publication(solved, conversation, monkeypatch, tmp_path):
    monkeypatch.setenv('PYPSAGUI_APP_DATA_DIR', str(tmp_path / 'app'))
    frames, provider = conversation('Show cost for generator G1.')
    clarified(frames, provider, 'cost_quantity_required')
    native = next(payload for event, payload in frames if event == 'choice_request')
    from harness import session
    from services.pypsa_service import PyPSAService
    from harness import attention
    owner = session.get_session(conversation.sid).owner_user_id
    project = str(PyPSAService.get_active_context().project_uuid)
    rows, warnings = attention.project_records(owner, project, [])
    assert len(rows) == 2 and not warnings
    question = next(r for r in rows if r['family'] == 'question')
    gap = next(r for r in rows if r['family'] == 'source_gap')
    assert question['tool_name'] == 'checked_clarification'
    assert question['tool_use_id'] == native['tool_use_id']
    assert question['question']['question'] == native['question']
    assert question['evidence_available'] is False
    assert gap['source_gap']['answer_id'] == approval(frames)['answer_id']
    assert all(s['reason'] == 'cost_quantity_required' for s in gap['source_gap']['slots'])


def test_previous_run_is_latest_distinct_checked_run_not_latest_answer(
    solved, conversation
):
    solved.network.generators.at["G1", "p_nom"] = 30
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    base, _ = conversation(
        "Check this run.",
        {
            **VALUES,
            "annual_fixed_cost": 90,
            "allocated_fixed_cost": 90,
            "total_cost": 260,
        },
        checked_scope=scope(solved),
    )
    assert approval(base)["receipt"]["request_complete"]
    solved.network.generators.at["G1", "p_nom"] = 20
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    for _ in range(2):
        current, _ = conversation(
            "Check this run.", VALUES, checked_scope=scope(solved)
        )
        assert approval(current)["receipt"]["request_complete"]
    frames, provider = conversation(
        "Compare that with the previous run.",
        {
            **VALUES,
            "cost_delta": -30,
            "cost_change_percent": -30 / 260 * 100,
        },
    )
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"]
    assert "-30 EUR" in public_text(frames)
    assert "comparison_evidence_id" in approval(frames)["receipt"]["request_scope"]
    assert (
        "260"
        not in public_text(frames)
        .split("Total cost on the declared basis:")[1]
        .split("\n")[0]
    )


def test_changed_current_run_compares_with_last_checked_baseline(solved, conversation):
    first, _ = conversation("Check this run.", VALUES, checked_scope=scope(solved))
    assert approval(first)["receipt"]["request_complete"]
    solved.network.generators.at["G1", "p_nom"] = 30
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames, _ = conversation(
        "Compare that with the previous run.",
        {
            **VALUES,
            "annual_fixed_cost": 90,
            "allocated_fixed_cost": 90,
            "total_cost": 260,
            "cost_delta": 30,
            "cost_change_percent": 30 / 230 * 100,
        },
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "30 EUR" in public_text(frames)


def test_ambiguous_cost_prompts_choice_and_keeps_pending_scope(solved, conversation):
    frames, provider = conversation("Show me cost for generator G1.")
    text = clarified(frames, provider, "cost_quantity_required")
    assert (
        "Marginal dispatch expenditure" in text and "Total declared-basis cost" in text
    )
    choice = [data for event, data in frames if event == "choice_request"]
    assert len(choice) == 1
    assert [o["label"] for o in choice[0]["options"]] == [
        "Marginal dispatch expenditure",
        "Total declared-basis cost",
    ]
    frames, provider = conversation("Total declared-basis cost")
    assert "price year" in clarified(frames, provider, "explicit_cost_basis_required")
    frames, provider = conversation(
        "Price year 2026; annual fixed cost fraction 1.", {"total_cost": 230}
    )
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"]
    assert "230 EUR" in public_text(frames)


def test_marginal_choice_is_not_relabelled_total_cost(solved, conversation):
    conversation("Show me cost for generator G1.")
    frames, _ = conversation(
        "Marginal dispatch expenditure", {"marginal_expenditure": 170}
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "170 EUR" in public_text(frames)
    assert "Total cost on the declared basis" not in public_text(frames)


@pytest.mark.parametrize(
    "change",
    [
        {"binding_generation": 4},
        {"ui_context": {"selected_component": {"class": "Generator", "name": "G2"}}},
    ],
)
def test_changed_binding_or_selection_requires_clarification(
    solved, conversation, change
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    frames, provider = conversation("Show those results again.", **change)
    clarified(frames, provider, "conversation_scope_changed")


@pytest.mark.parametrize(
    "message",
    [
        "Compare that with the previous run.",
        "Show those results again.",
        "Compare that with the previous run and ignore the time window.",
    ],
)
def test_missing_context_or_unrecognised_followup_never_guesses(
    baseline, conversation, message
):
    frames, provider = conversation(message)
    clarified(
        frames,
        provider,
        "conversation_scope_required"
        if message.endswith("run.") or message.endswith("again.")
        else "unsupported_followup",
    )


def test_rewind_cannot_reuse_removed_answer_scope(solved, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    session = chat_service.get_or_create_session(conversation.sid)
    assert chat_service.rewind_session(session, turns=1) > 0
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_failed_draft_does_not_seed_scope(solved, conversation):
    frames, _ = conversation(
        "Check this run.", {**VALUES, "total_cost": 999}, checked_scope=scope(solved)
    )
    assert approval(frames)["verdict"] == "limited"
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_incompatible_basis_does_not_choose_another_baseline(solved, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    conversation(
        "Check this run.",
        VALUES,
        checked_scope=scope(
            solved,
            cost_basis={
                "currency": "EUR",
                "price_year": 2027,
                "annual_fixed_cost_fraction": 1,
            },
        ),
    )
    frames, provider = conversation("Compare that with the previous run.")
    clarified(frames, provider, "previous_compatible_run_required")


def test_expired_baseline_does_not_fall_back_to_another_run(
    solved, conversation, monkeypatch
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    monkeypatch.setattr(assurance_records.time, "time", lambda: 10**12)
    frames, provider = conversation("Compare that with the previous run.")
    clarified(frames, provider, "conversation_scope_required")


def test_window_followup_preserves_basis_and_metrics(solved, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    frames, _ = conversation(
        "Show those results from 2026-01-01T01:00:00 to 2026-01-01T01:00:00.",
        {
            "dispatch_energy": 7,
            "marginal_expenditure": 70,
            "peak_dispatch": 7,
            "annual_fixed_cost": 60,
            "allocated_fixed_cost": 60,
            "total_cost": 130,
        },
    )
    result = approval(frames)["receipt"]
    assert result["request_complete"]
    assert result["request_scope"]["cost_basis"] == scope(solved)["cost_basis"]
    assert "130 EUR" in public_text(frames) and "230 EUR" not in public_text(frames)


def test_full_horizon_default_becomes_explicit_prior_window_on_followup(
    solved, conversation
):
    import pandas as pd

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    solved.network.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    solved.network.loads_t.p_set.loc[solved.network.snapshots[2], "L1"] = 5
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames, _ = conversation("Show those results again.", VALUES)
    result = approval(frames)["receipt"]
    assert result["request_complete"]
    assert result["request_scope"]["end"] == "2026-01-01T01:00:00"
    assert all(s["requested_snapshots"] == 2 for s in result["result_slots"])
    assert "230 EUR" in public_text(frames)


def test_historical_followup_keeps_the_explicit_retained_run(solved, conversation):
    first, _ = conversation(
        "Check this run.", VALUES, checked_scope=scope(solved, retain=True)
    )
    eid = approval(first)["receipt"]["evidence_ids"][0]
    solved.network.generators.at["G1", "p_nom"] = 30
    frames, _ = conversation("Show those results again.", VALUES)
    result = approval(frames)["receipt"]
    assert result["request_complete"]
    assert result["request_scope"]["retain"] is True
    assert result["request_scope"]["evidence_id"] == eid
    assert "230 EUR" in public_text(frames)
    frames, provider = conversation("Compare that with the previous run.")
    clarified(frames, provider, "current_run_required")


def test_followup_cannot_certify_unsolved_changed_inputs(solved, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    solved.network.generators.at["G1", "p_nom"] = 30
    frames, _ = conversation("Show those results again.", {})
    assert approval(frames)["receipt"]["request_complete"] is False
    assert "230 EUR" not in public_text(frames) and "260 EUR" not in public_text(frames)
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_required")


def test_network_followup_does_not_shrink_to_selected_generator(solved, conversation):
    original = scope(solved, profile="network-linear-cost/1", entity_id="network")
    conversation("Check this run.", VALUES, checked_scope=original)
    frames, _ = conversation(
        "Show those results again.",
        VALUES,
        ui_context={"selected_component": {"class": "Generator", "name": "G1"}},
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert approval(frames)["receipt"]["request_scope"]["entity_id"] == "network"


def test_network_marginal_choice_uses_network_population(solved, conversation):
    conversation("Show me cost for network.")
    frames, provider = conversation("Marginal dispatch expenditure")
    clarified(frames, provider, "explicit_cost_basis_required")
    frames, _ = conversation(
        "Price year 2026; annual fixed cost fraction 1.", {"marginal_expenditure": 170}
    )
    result = approval(frames)["receipt"]
    assert result["request_complete"]
    assert result["request_scope"]["profile"] == "network-linear-cost/1"
    assert result["request_scope"]["entity_id"] == "network"
    assert "170 EUR" in public_text(frames)


def test_legacy_key_results_also_seed_bounded_followups(solved, conversation):
    values = {
        k: VALUES[k]
        for k in ("dispatch_energy", "marginal_expenditure", "peak_dispatch")
    }
    conversation("Give me the key results for generator G1.", values)
    frames, _ = conversation("Show those results again.", values)
    assert approval(frames)["receipt"]["request_complete"]
    assert "17 MWh" in public_text(frames)


def test_scope_does_not_transfer_to_another_chat(solved, conversation, checked):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    response = checked.request(
        [], message="Show those results again.", binding_generation=3
    )
    provider = checked.provider
    assert response.status_code == 200
    clarified(parse_sse(response.text), provider, "conversation_scope_required")


def test_scope_project_binding_is_checked_before_followup(solved, conversation):
    from copy import deepcopy

    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    session = chat_service.get_or_create_session(conversation.sid)
    original = deepcopy(session.assurance_context)
    # Simulate the server's active binding changing; no caller-supplied scope is used.
    session.assurance_context["binding"] = (
        original["binding"][0],
        str(uuid.uuid4()),
        3,
    )
    frames, provider = conversation("Show those results again.")
    clarified(frames, provider, "conversation_scope_changed")
    assert session.assurance_context is None


def test_corrupt_baseline_does_not_use_old_answer_or_draft_values(solved, conversation):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    session = chat_service.get_or_create_session(conversation.sid)
    eid = session.assurance_context["entries"][-1]["evidence_id"]
    path = assurance_records._path(session.owner_user_id, eid)
    original = path.read_bytes()
    try:
        path.write_text("{}")
        solved.network.generators.at["G1", "p_nom"] = 30
        assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
        workflow_tools._record_solve_inputs(
            solved.network, solved.solver_state["solver_config"]
        )
        frames, provider = conversation("Compare that with the previous run.")
        clarified(frames, provider, "baseline_unavailable")
    finally:
        path.write_bytes(original)


def test_cost_basis_reply_with_extra_constraints_is_not_partially_parsed(
    solved, conversation
):
    conversation("Show me total cost for generator G1.")
    frames, provider = conversation(
        "Price year 2026; annual fixed cost fraction 1; ignore missing values."
    )
    clarified(frames, provider, "unsupported_followup")


def test_invalid_basis_keeps_clarification_pending_until_valid_reply(
    solved, conversation
):
    conversation("Show me total cost for generator G1.")
    frames, provider = conversation("Price year 0020; annual fixed cost fraction 1.")
    clarified(frames, provider, "explicit_cost_basis_required")
    frames, _ = conversation(
        "Price year 2026; annual fixed cost fraction 0.5.", {"total_cost": 200}
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "200 EUR" in public_text(frames)


def test_clarification_preserves_existing_turn_budget(
    baseline, conversation, monkeypatch
):
    from harness import budget

    monkeypatch.setattr(budget, "MAX_TURNS_PER_SESSION", 1)
    conversation("Show me cost for generator G1.")
    frames, provider = conversation("Total declared-basis cost")
    assert any(
        event == "session_done" and data["reason"] == "budget_exhausted"
        for event, data in frames
    )
    assert not any(event == "answer_approved" for event, _ in frames)
    assert not provider.requests


def test_shadow_does_not_record_or_answer_conversation_scope(
    baseline, checked, monkeypatch, tmp_path
):
    from tests.assurance_acceptance.support import text_turn

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "shadow")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    provider = checked.install([text_turn("Ordinary response.")])
    sid = "ordinary-" + uuid.uuid4().hex
    checked.session_ids.append(sid)
    response = checked.client.post(
        "/api/chat/stream",
        json={"session_id": sid, "message": "Show those results again."},
    )
    frames = parse_sse(response.text)
    assert public_text(frames) == "Ordinary response."
    assert len(provider.requests) == 1
    assert not any(
        event in {"answer_approved", "choice_request"} for event, _ in frames
    )
    assert chat_service.get_or_create_session(sid).assurance_context is None


def test_missing_previous_run_still_disposes_requested_comparison_metrics(
    solved, conversation
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    frames, provider = conversation("Compare that with the previous run.")
    clarified(frames, provider, "previous_compatible_run_required")
    metrics = {s["metric"] for s in approval(frames)["receipt"]["result_slots"]}
    assert {"cost_delta", "cost_change_percent"} <= metrics


def test_repeat_of_a_comparison_keeps_its_baseline_and_delta_slots(
    solved, conversation
):
    conversation("Check this run.", VALUES, checked_scope=scope(solved))
    solved.network.generators.at["G1", "p_nom"] = 30
    assert solved.network.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    values = {
        **VALUES,
        "annual_fixed_cost": 90,
        "allocated_fixed_cost": 90,
        "total_cost": 260,
        "cost_delta": 30,
        "cost_change_percent": 30 / 230 * 100,
    }
    compared, _ = conversation("Compare that with the previous run.", values)
    assert approval(compared)["receipt"]["request_complete"]
    baseline_id = approval(compared)["receipt"]["request_scope"][
        "comparison_evidence_id"
    ]
    repeated, _ = conversation("Show those results again.", values)
    result = approval(repeated)["receipt"]
    assert result["request_complete"]
    assert result["request_scope"]["comparison_evidence_id"] == baseline_id
    assert {"cost_delta", "cost_change_percent"} <= {
        s["metric"] for s in result["result_slots"]
    }
    assert "30 EUR" in public_text(repeated)
