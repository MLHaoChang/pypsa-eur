"""A2.8 (AA-03 S3): request slots and the meaning of "capacity".

A bare "what is the capacity of <class> <name>?" has three meanings (owner
freeze AA03-FREEZE-1 item 3): the configured input, the optimized result and
the available capacity. Software asks which one through the existing choice
card; no number is published until the meaning is chosen, and
``request_complete`` stays false while any requested slot is unmapped.
Expected values are worked by hand from the fixture network.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import uuid

import pandas as pd
import pytest

from tests.assurance_acceptance.support import approval, parse_sse, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_capacity_cycles_assurance import PROFILES, answered, ask, solve
from tests.test_storage_assurance import storage  # noqa: F401
from tests.test_storage_cost_assurance import VALUES as STORAGE_VALUES
from tests.test_storage_cost_assurance import priced  # noqa: F401
from tests.test_storage_cost_assurance import scope as storage_scope
from tests.test_workflow_tools import baseline  # noqa: F401

MEANINGS = ["Configured capacity", "Optimized capacity", "Available capacity"]


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv(
        "PYPSA_GUI_ANSWER_ASSURANCE_PROFILES",
        ",".join((*PROFILES, "storage-unit-cost/1")),
    )


@pytest.fixture
def grid(baseline):
    """G1 fixed at 20 MW; G2 extendable from 0 MW, capped at 3 MW; one of each class."""
    ctx = baseline[0]
    n = ctx.network
    n.add(
        "Generator",
        "G2",
        bus="B1",
        p_nom_extendable=True,
        p_nom_max=3,
        capital_cost=1,
        marginal_cost=0,
    )
    n.add("Bus", "B2", v_nom=110)
    n.add("Line", "L12", bus0="B1", bus1="B2", s_nom=8, x=0.1)
    n.add("Transformer", "T12", bus0="B1", bus1="B2", s_nom=40, x=0.1)
    n.add("Link", "K12", bus0="B1", bus1="B2", p_nom=6)
    n.add("StorageUnit", "Battery", bus="B1", p_nom=10, max_hours=2)
    n.add("Store", "Tank", bus="B2", e_nom=12)
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
    return ctx


@pytest.fixture
def chat(checked):
    sid = "slots-" + uuid.uuid4().hex
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
        return parse_sse(response.text), provider

    return send


def asked(frames, provider, reason="capacity_meaning_required"):
    """A software clarification: limited, no number, every slot unmapped."""
    record = approval(frames)
    receipt = record["receipt"]
    assert record["verdict"] == "limited"
    assert receipt["request_complete"] is False
    assert receipt["checked_block_ids"] == [] and receipt["evidence_ids"] == []
    assert receipt["result_slots"]
    assert {s["disposition"] for s in receipt["result_slots"]} == {
        "needs_clarification"
    }
    assert {s["reason"] for s in receipt["result_slots"]} == {reason}
    assert not provider.requests, "a software clarification calls no model"
    return public_text(frames)


def card(frames):
    choices = [data for event, data in frames if event == "choice_request"]
    assert len(choices) == 1
    return [o["label"] for o in choices[0]["options"]]


# ── the bare form asks which capacity ─────────────────────────────────────


def test_a_bare_capacity_question_gets_the_choice_card_and_no_number(grid, chat):
    solve(grid)
    frames, provider = chat("What is the capacity of generator G1?")
    text = asked(frames, provider)
    assert card(frames) == MEANINGS
    assert "20 MW" not in text
    slots = approval(frames)["receipt"]["result_slots"]
    assert [s["metric"] for s in slots] == [
        "configured_capacity",
        "optimal_capacity",
        "available_capacity",
    ]


def blocks(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: (b["value"], b["unit"]) for b in response.json()["blocks"]}


def slots(frames):
    return {
        s["metric"]: (s["disposition"], s.get("reason"))
        for s in approval(frames)["receipt"]["result_slots"]
    }


@pytest.mark.parametrize(
    ("label", "metric", "value"),
    [
        ("Configured capacity", "configured_capacity", 20),
        ("optimized capacity.", "optimal_capacity", 20),
    ],
)
def test_each_answered_meaning_maps_to_its_own_slot(
    grid, chat, checked, label, metric, value
):
    solve(grid)
    chat("What is the capacity of generator G1?")
    frames, provider = chat(label, {metric: value})
    assert len(provider.requests) == 1
    assert approval(frames)["receipt"]["request_complete"] is True
    assert blocks(checked, frames) == {metric: (pytest.approx(value), "MW")}
    assert list(slots(frames)) == [metric]


def test_available_capacity_is_named_unavailable_and_never_derated(grid, chat, checked):
    grid.network.generators_t.p_max_pu = pd.DataFrame(
        {"G1": [0.5, 0.25]}, index=grid.network.snapshots
    )
    solve(grid)
    chat("What is the capacity of generator G1?")
    frames, _ = chat("Available capacity", {"available_capacity": 10})
    record = approval(frames)
    assert record["receipt"]["request_complete"] is False
    assert record["receipt"]["checked_block_ids"] == []
    assert slots(frames) == {
        "available_capacity": ("unavailable", "available_capacity_not_checked")
    }
    text = public_text(frames)
    assert not any(n in text for n in ("10 MW", "5 MW", "20 MW"))


# ── C1: the configured input, per class, in the class's own unit ─────────


@pytest.mark.parametrize(
    ("entity", "component_class", "value", "unit"),
    [
        ("G1", "Generator", 20, "MW"),
        ("Battery", "StorageUnit", 10, "MW"),
        ("K12", "Link", 6, "MW"),
        ("L12", "Line", 8, "MVA"),
        ("T12", "Transformer", 40, "MVA"),
        ("Tank", "Store", 12, "MWh"),
    ],
)
def test_configured_capacity_reads_each_class_input_nominal(
    grid, checked, entity, component_class, value, unit
):
    ctx = solve(grid)
    frames = ask(
        checked,
        ctx,
        "capacity/1",
        entity,
        component_class,
        {"configured_capacity": value},
        metrics=["configured_capacity"],
    )
    assert blocks(checked, frames) == {
        "configured_capacity": (pytest.approx(value), unit)
    }
    assert "before any solve" in public_text(frames)


def test_an_extendable_configured_input_differs_from_its_optimum_and_says_so(
    grid, checked
):
    ctx = solve(grid)
    # G2 is entered at p_nom 0 and the solve builds it to its 3 MW bound.
    values = {"configured_capacity": 0, "optimal_capacity": 3}
    frames = ask(
        checked, ctx, "capacity/1", "G2", "Generator", values, metrics=list(values)
    )
    assert answered(checked, frames) == pytest.approx(values)
    assert "Configured capacity is the input p_nom before the solve" in public_text(
        frames
    )


def test_configured_capacity_is_never_the_optimum(grid, checked):
    ctx = solve(grid)
    frames = ask(
        checked,
        ctx,
        "capacity/1",
        "G2",
        "Generator",
        {"configured_capacity": 3},
        metrics=["configured_capacity"],
    )
    assert approval(frames)["verdict"] == "limited"
    assert "3 MW" not in public_text(frames)


# ── C4: unknown, ambiguous or unsolved never yields a number ─────────────


@pytest.mark.parametrize(
    "message", ["What is the capacity of G1?", "What is the capacity of pump P1?"]
)
def test_a_missing_or_unknown_class_asks_for_the_component(grid, chat, message):
    solve(grid)
    frames, provider = chat(message)
    assert "name the component class" in asked(
        frames, provider, "capacity_component_required"
    )
    assert not [d for e, d in frames if e == "choice_request"]


def test_an_unknown_name_is_unavailable_not_a_number(grid, chat):
    solve(grid)
    chat("What is the capacity of generator Nope?")
    frames, _ = chat("Configured capacity", {"configured_capacity": 20})
    assert slots(frames) == {
        "configured_capacity": ("unavailable", "unknown_component")
    }
    assert "20 MW" not in public_text(frames)


def test_an_unsolved_network_answers_the_input_but_not_the_optimum(grid, chat, checked):
    chat("What is the capacity of generator G1?")
    frames, _ = chat("Optimized capacity", {"optimal_capacity": 20})
    assert slots(frames) == {"optimal_capacity": ("unavailable", "unverified_solve")}
    assert "20 MW" not in public_text(frames)
    chat("What is the capacity of generator G1?")
    frames, _ = chat("Configured capacity", {"configured_capacity": 20})
    assert blocks(checked, frames) == {"configured_capacity": (pytest.approx(20), "MW")}


# ── C5: the card accepts only its three meanings ─────────────────────────


@pytest.mark.parametrize(
    "reply",
    [
        "Installed capacity",
        "nominal",
        "Total declared-basis cost",
        "I mean the nameplate capacity please",
    ],
)
def test_a_card_answer_outside_the_three_meanings_is_refused(grid, chat, reply):
    solve(grid)
    chat("What is the capacity of generator G1?")
    frames, provider = chat(reply, {"configured_capacity": 20, "optimal_capacity": 20})
    assert approval(frames)["receipt"]["request_complete"] is False
    assert approval(frames)["receipt"]["checked_block_ids"] == []
    assert not provider.requests
    assert "20 MW" not in public_text(frames)


def test_the_card_survives_a_refused_answer(grid, chat, checked):
    solve(grid)
    chat("What is the capacity of generator G1?")
    frames, provider = chat("Installed capacity")
    assert card(frames) == MEANINGS
    frames, _ = chat("Configured capacity", {"configured_capacity": 20})
    assert blocks(checked, frames) == {"configured_capacity": (pytest.approx(20), "MW")}


def test_a_selected_line_keeps_its_pending_capacity_question(grid, chat, checked):
    solve(grid)
    ui = {"ui_context": {"selected_component": {"class": "Line", "name": "L12"}}}
    chat("What is the capacity of line L12?", **ui)
    frames, _ = chat("Configured capacity", {"configured_capacity": 8}, **ui)
    assert blocks(checked, frames) == {"configured_capacity": (pytest.approx(8), "MVA")}


# ── C6: a required unavailable slot is never dropped by adding metrics ───


def test_unanswerable_slots_stay_in_the_receipt_beside_answered_ones(grid, checked):
    # Unsolved: the optimum and its change are unverified, the input is not.
    values = {"configured_capacity": 20}
    metrics = [
        "optimal_capacity",
        "configured_capacity",
        "capacity_change",
        "available_capacity",
    ]
    frames = ask(
        checked, grid, "capacity/1", "G1", "Generator", values, metrics=metrics
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"] is False
    assert slots(frames) == {
        "optimal_capacity": ("unavailable", "unverified_solve"),
        "configured_capacity": ("answered", None),
        "capacity_change": ("unavailable", "unverified_solve"),
        "available_capacity": ("unavailable", "available_capacity_not_checked"),
    }
    assert answered(checked, frames) == pytest.approx(values)


# ── C6: the largest valid block set keeps the request's scope ────────────


def test_the_largest_valid_block_set_keeps_project_run_and_window(priced, checked):
    # No profile has 20 answerable metrics; storage-unit-cost/1 has the most (12).
    # The 20-slot bound itself is pinned in test_harness_reliability.
    window = {"start": "2026-01-01T00:00:00", "end": "2026-01-01T02:00:00"}
    frames = checked.run(
        NumericalProvider(STORAGE_VALUES), checked_scope=storage_scope(priced, **window)
    )
    record = approval(frames)
    receipt = record["receipt"]
    assert record["verdict"] == "passed" and receipt["request_complete"] is True, (
        receipt["limitations"]
    )
    assert len(receipt["checked_block_ids"]) == len(STORAGE_VALUES) == 12
    assert len(receipt["evidence_ids"]) == 1
    request = receipt["request_scope"]
    assert (request["project_id"], request["entity_id"]) == (
        priced.project_uuid,
        "Battery",
    )
    assert (request["start"], request["end"]) == (window["start"], window["end"])
    stored = checked.client.get("/api/chat/answers/" + record["answer_id"]).json()
    assert {b["metric"] for b in stored["blocks"]} == set(STORAGE_VALUES)


def test_a_new_complete_request_while_the_card_is_open_is_not_refused(
    grid, chat, checked
):
    solve(grid)
    chat("What is the capacity of generator G1?")
    frames, provider = chat("What is the capacity of line L12?")
    assert "Configured capacity" in asked(frames, provider)
    values = {"optimal_capacity": 3, "capacity_change": 3}
    frames, _ = chat("Show the capacity for generator G2.", values)
    assert answered(checked, frames) == pytest.approx(values)


def test_both_figures_each_say_which_capacity_they_are(grid, checked):
    ctx = solve(grid)
    values = {"configured_capacity": 0, "optimal_capacity": 3}
    frames = ask(
        checked, ctx, "capacity/1", "G2", "Generator", values, metrics=list(values)
    )
    text = public_text(frames)
    assert "The capacity after the solve is the solve's nominal capacity" in text
    assert "The configured capacity is the input before the solve." in text


def test_the_labelled_form_follows_up_with_its_own_class_selected(grid, chat, checked):
    # The existing "capacity for" form keeps its answer; a selected Line no longer
    # reads as a changed selection because the scope's own class is compared.
    solve(grid)
    ui = {"ui_context": {"selected_component": {"class": "Line", "name": "L12"}}}
    values = {"optimal_capacity": 8, "capacity_change": 0}
    frames, _ = chat("Show the capacity for line L12.", values, **ui)
    assert answered(checked, frames) == pytest.approx(values)
    frames, _ = chat("Show those results again.", values, **ui)
    assert answered(checked, frames) == pytest.approx(values)


def test_every_retained_pending_reason_survives_the_durable_checkpoint():
    from typing import get_args

    from harness import assurance_conversation, assurance_recovery

    pending = assurance_recovery.Context.model_fields["pending"].annotation
    assert set(get_args(get_args(pending)[0])) == assurance_conversation.PENDING
