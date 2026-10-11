"""Independent StorageUnit accounting oracles through checked publication."""

# ruff: noqa: F811 -- imported shared fixtures.
import io
import json
import zipfile

import pandas as pd
import pytest

from harness import calculations
from services import assurance_records, chat_service, workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_conversation_scope import clarified, conversation  # noqa: F401
from tests.test_storage_assurance import (
    VALUES as OPERATIONS,
    answer,
    private_store,  # noqa: F401
    storage,  # noqa: F401
)
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "storage-unit-cost/1"
BASIS = {"currency": "EUR", "price_year": 2026, "annual_fixed_cost_fraction": 0.5}
COSTS = {
    "storage_discharge_expenditure": 20,
    "storage_holding_expenditure": 16,
    "storage_operating_expenditure": 36,
    "annual_fixed_cost": 30,
    "allocated_fixed_cost": 15,
    "total_cost": 51,
}
VALUES = {**OPERATIONS, **COSTS}


def record_solve(ctx):
    assert ctx.network.optimize(solver_name="highs") == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(ctx.network, ctx.solver_state["solver_config"])


@pytest.fixture
def priced(storage):
    n = storage.network
    n.storage_units.at["Battery", "capital_cost"] = 3
    n.storage_units_t.marginal_cost = pd.DataFrame(
        {"Battery": [99, 2, 4]}, index=n.snapshots
    )
    n.storage_units_t.marginal_cost_storage = pd.DataFrame(
        {"Battery": [0.5, 1, 7]}, index=n.snapshots
    )
    record_solve(storage)
    # Independent objective-weighted arithmetic: discharge 0*99*1 +
    # 2*2*3 + 2*4*1 = 20; holding 8*.5*1 + 4*1*3 + 0*7*1 = 16.
    # Annual installed-capacity charge 10*3 = 30; half allocated = 15;
    # direct StorageUnit total = 20+16+15 = 51 (excludes generator purchase).
    assert list(n.storage_units_t.p_store.Battery) == pytest.approx([5, 0, 0])
    assert list(n.storage_units_t.p_dispatch.Battery) == pytest.approx([0, 2, 2])
    assert list(n.storage_units_t.state_of_charge.Battery) == pytest.approx([8, 4, 0])
    return storage


def scope(ctx, **options):
    return {
        "version": 1,
        "profile": PROFILE,
        "project_id": ctx.project_uuid,
        "entity_id": "Battery",
        "retain": False,
        "cost_basis": BASIS.copy(),
        **options,
    }


def test_direct_storage_cost_uses_independent_51_eur_oracle(priced, checked):
    frames = checked.run(NumericalProvider(VALUES), checked_scope=scope(priced))
    result = answer(checked, frames)
    assert result["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in result["blocks"]} == VALUES
    text = public_text(frames)
    assert "StorageUnit Battery" in text and "51 EUR" in text
    assert "charging electricity" in text and "price year 2026" in text
    assert "not the solver objective" in text


def test_cost_window_keeps_predecessor_but_allocates_only_declared_fraction(
    priced, checked
):
    frames = checked.run(
        NumericalProvider(
            {
                "storage_discharge_expenditure": 12,
                "storage_holding_expenditure": 12,
                "total_cost": 39,
            }
        ),
        checked_scope=scope(
            priced,
            metrics=[
                "storage_discharge_expenditure",
                "storage_holding_expenditure",
                "total_cost",
            ],
            start="2026-01-01T01:00:00",
            end="2026-01-01T01:00:00",
        ),
    )
    result = answer(checked, frames)
    assert result["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in result["blocks"]} == {
        "storage_discharge_expenditure": 12,
        "storage_holding_expenditure": 12,
        "total_cost": 39,
    }


@pytest.mark.parametrize("fraction,total", [(0, 36), (1, 66)])
def test_fixed_allocation_is_explicit_not_inferred_from_snapshot_count(
    priced, checked, fraction, total
):
    frames = checked.run(
        NumericalProvider({"total_cost": total}),
        checked_scope=scope(
            priced,
            metrics=["total_cost"],
            cost_basis={**BASIS, "annual_fixed_cost_fraction": fraction},
        ),
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert f"{total} EUR" in public_text(frames)


def test_extendable_capacity_uses_solved_power_capacity_not_energy_capacity(
    priced, checked
):
    priced.network.storage_units.at["Battery", "p_nom_extendable"] = True
    record_solve(priced)
    assert priced.network.storage_units.at["Battery", "p_nom_opt"] == pytest.approx(5)
    frames = checked.run(
        NumericalProvider({"annual_fixed_cost": 15, "total_cost": 43.5}),
        checked_scope=scope(priced, metrics=["annual_fixed_cost", "total_cost"]),
    )
    assert approval(frames)["receipt"]["request_complete"]


def test_missing_basis_never_invents_storage_economics(priced, checked):
    request = scope(priced, metrics=["charged_energy", "total_cost"])
    request.pop("cost_basis")
    frames = checked.run(
        NumericalProvider({"charged_energy": 10}), checked_scope=request
    )
    result = answer(checked, frames)
    assert not result["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in result["blocks"]} == {"charged_energy": 10}
    assert (
        result["receipt"]["result_slots"][1]["reason"] == "explicit_cost_basis_required"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "weight",
        "discharge_price",
        "holding_price",
        "quadratic",
        "overnight",
        "fom",
        "module",
        "capital",
        "overflow",
    ],
)
def test_invalid_cost_dependencies_do_not_become_a_complete_total(
    priced, checked, fault
):
    n = priced.network
    if fault == "weight":
        n.snapshot_weightings.at[n.snapshots[1], "objective"] = 0
    elif fault == "discharge_price":
        n.storage_units_t.marginal_cost.at[n.snapshots[1], "Battery"] = float("nan")
    elif fault == "holding_price":
        n.storage_units_t.marginal_cost_storage.at[n.snapshots[1], "Battery"] = float(
            "nan"
        )
    elif fault == "quadratic":
        n.storage_units.at["Battery", "marginal_cost_quadratic"] = 2
    elif fault == "overnight":
        n.storage_units.at["Battery", "overnight_cost"] = 20
    elif fault == "fom":
        n.storage_units.at["Battery", "fom_cost"] = 2
    elif fault == "module":
        n.storage_units.at["Battery", "p_nom_mod"] = 2
    elif fault == "overflow":
        n.storage_units.at["Battery", "capital_cost"] = 1e308
    else:
        n.storage_units.at["Battery", "capital_cost"] = float("nan")
    # Stamp current inputs to isolate the dependency guard from ordinary staleness.
    workflow_tools._record_solve_inputs(n, priced.solver_state["solver_config"])
    frames = checked.run(
        NumericalProvider({}), checked_scope=scope(priced, metrics=["total_cost"])
    )
    result = answer(checked, frames)
    assert not result["receipt"]["request_complete"]
    assert result["blocks"] == []
    assert result["receipt"]["result_slots"][0]["disposition"] == "unavailable"


@pytest.mark.parametrize("fault", ["state", "prior", "failed", "stale"])
def test_even_fixed_only_cost_requires_verified_storage_trajectory(
    priced, checked, fault
):
    n = priced.network
    if fault in {"state", "prior"}:
        n.storage_units_t.state_of_charge.at[n.snapshots[0], "Battery"] = 9
    elif fault == "failed":
        priced.solver_state["status"] = "failed"
    else:
        n.storage_units.at["Battery", "efficiency_store"] = 0.9
    window = (
        {"start": "2026-01-01T01:00:00", "end": "2026-01-01T01:00:00"}
        if fault == "prior"
        else {}
    )
    frames = checked.run(
        NumericalProvider({}),
        checked_scope=scope(
            priced, metrics=["annual_fixed_cost", "total_cost"], **window
        ),
    )
    result = answer(checked, frames)
    assert not result["receipt"]["request_complete"] and result["blocks"] == []


def test_signed_discharge_cost_is_not_clipped_or_charged_on_net_power(priced, checked):
    priced.network.storage_units_t.marginal_cost.loc[:, "Battery"] = [99, -2, -4]
    record_solve(priced)
    frames = checked.run(
        NumericalProvider({"storage_discharge_expenditure": -20, "total_cost": 11}),
        checked_scope=scope(
            priced, metrics=["storage_discharge_expenditure", "total_cost"]
        ),
    )
    assert approval(frames)["receipt"]["request_complete"]


@pytest.mark.parametrize("fault", ["value", "unit", "formula", "omit", "narrative"])
def test_fabricated_storage_economics_is_withheld(priced, checked, fault):
    def mutate(draft):
        if fault == "value":
            draft["blocks"][0]["value"] = 999
        elif fault == "unit":
            draft["blocks"][0]["unit"] = "MWh"
        elif fault == "formula":
            draft["blocks"][0]["formula_id"] = "invented/1"
        elif fault == "omit":
            draft["blocks"].pop()
        else:
            draft["narrative"] = "The battery earns guaranteed profits."

    frames = checked.run(
        NumericalProvider({"total_cost": 51}, mutate),
        checked_scope=scope(priced, metrics=["total_cost"]),
    )
    result = answer(checked, frames)
    assert not result["receipt"]["request_complete"] and result["blocks"] == []
    assert "999" not in public_text(frames) and "guaranteed" not in public_text(frames)


def test_retained_comparison_uses_same_storage_basis_and_signed_delta(priced, checked):
    priced.network.storage_units.at["Battery", "p_nom"] = 20
    record_solve(priced)
    base = checked.run(
        NumericalProvider({"total_cost": 66}),
        checked_scope=scope(priced, metrics=["total_cost"], retain=True),
    )
    base_id = approval(base)["receipt"]["evidence_ids"][0]
    priced.network.storage_units.at["Battery", "p_nom"] = 10
    record_solve(priced)
    expected = {
        "total_cost": 51,
        "cost_delta": -15,
        "cost_change_percent": -15 / 66 * 100,
    }
    frames = checked.run(
        NumericalProvider(expected),
        checked_scope=scope(
            priced, metrics=list(expected), comparison_evidence_id=base_id
        ),
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert {
        b["metric"]: b["value"] for b in answer(checked, frames)["blocks"]
    } == expected
    frames = checked.run(
        NumericalProvider({}),
        checked_scope=scope(
            priced,
            metrics=["total_cost"],
            cost_basis={**BASIS, "price_year": 2025},
            comparison_evidence_id=base_id,
        ),
    )
    assert not approval(frames)["receipt"]["request_complete"]


def test_retained_cost_export_and_replay_keep_checked_values_after_edit(
    priced, checked
):
    frames = checked.run(
        NumericalProvider({"total_cost": 51}),
        checked_scope=scope(priced, metrics=["total_cost"], retain=True),
    )
    approved = approval(frames)
    evidence_id = approved["receipt"]["evidence_ids"][0]
    priced.network.storage_units.at["Battery", "capital_cost"] = 100
    evidence = assurance_records.evidence(assurance_records.actor_id(), evidence_id)
    assert evidence["facts"][0]["value"] == 51
    exported = checked.client.get(f"/api/chat/answers/{approved['answer_id']}/export")
    assert exported.status_code == 200
    assert exported.json()["answer"]["blocks"][0]["value"] == 51
    zipped = checked.client.get(
        f"/api/chat/answers/{approved['answer_id']}/export?format=zip"
    )
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as bundle:
        assert (
            json.loads(bundle.read("manifest.json"))["request_scope"]["profile"]
            == PROFILE
        )
        assert b"Battery,total_cost,51.0,EUR" in bundle.read("facts.csv")
    repeat = checked.run(
        NumericalProvider({"total_cost": 51}),
        checked_scope={
            **scope(priced, metrics=["total_cost"], retain=True),
            "evidence_id": evidence_id,
        },
    )
    assert approval(repeat)["receipt"]["request_complete"]


def test_current_cost_evidence_invalidates_after_cost_edit(priced, checked):
    frames = checked.run(
        NumericalProvider({"total_cost": 51}),
        checked_scope=scope(priced, metrics=["total_cost"]),
    )
    priced.network.storage_units.at["Battery", "capital_cost"] = 4
    evidence_id = approval(frames)["receipt"]["evidence_ids"][0]
    assert checked.client.get("/api/chat/evidence/" + evidence_id).status_code == 409


def test_ambiguous_storage_cost_preserves_asset_type_through_restart(
    priced, checked, conversation
):
    frames, provider = conversation("Show cost for storage unit Battery.")
    clarified(frames, provider, "cost_quantity_required")
    frames, provider = conversation("Total declared-basis cost")
    clarified(frames, provider, "explicit_cost_basis_required")
    assert approval(frames)["receipt"]["request_scope"]["profile"] == PROFILE
    chat_service.drop_session(conversation.sid)
    assert checked.client.get("/api/chat/history").status_code == 200
    frames, _ = conversation(
        "Use price year 2026; annual fixed cost fraction 0.5",
        {"total_cost": 51},
        binding_generation=8,
        ui_context={"selected_component": {"class": "StorageUnit", "name": "Battery"}},
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "StorageUnit Battery" in public_text(frames)
    frames, _ = conversation(
        "Show those results again.", {"total_cost": 51}, binding_generation=8
    )
    assert approval(frames)["receipt"]["request_complete"]


def test_named_storage_cost_grammar_preserves_exact_window_and_basis(
    priced, conversation
):
    frames, _ = conversation(
        "Show total cost for storage unit Battery; from 2026-01-01T01:00:00 to 2026-01-01T01:00:00; price year 2026; annual fixed cost fraction 0.5.",
        {"total_cost": 39},
    )
    assert approval(frames)["receipt"]["request_complete"]
    request = approval(frames)["receipt"]["request_scope"]
    assert request["profile"] == PROFILE and request["end"] == "2026-01-01T01:00:00"
    assert request["cost_basis"] == BASIS


def test_cost_profile_is_discoverable_and_can_be_rolled_back(
    priced, checked, monkeypatch
):
    response = checked.client.get("/api/chat/reliability/capabilities")
    assert response.status_code == 200
    assert response.json()["profiles"][PROFILE]["enabled"]
    assert (
        "storage_operating_expenditure"
        in response.json()["profiles"][PROFILE]["default_metrics"]
    )
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "storage-unit-dispatch/1")
    frames = checked.run(
        NumericalProvider({}), checked_scope=scope(priced, metrics=["total_cost"])
    )
    assert not approval(frames)["receipt"]["request_complete"]


def test_unsupported_required_economics_never_silently_disappears(priced, checked):
    frames = checked.run(
        NumericalProvider({"total_cost": 51}),
        checked_scope=scope(
            priced, metrics=["total_cost", "NPV", "charging_purchase_cost"]
        ),
    )
    result = answer(checked, frames)
    assert not result["receipt"]["request_complete"]
    assert [s["metric"] for s in result["receipt"]["result_slots"]] == [
        "total_cost",
        "NPV",
        "charging_purchase_cost",
    ]
    assert all(
        s["reason"] == "unsupported_metric"
        for s in result["receipt"]["result_slots"][1:]
    )


def test_cost_input_override_respects_dynamic_prices_not_static_fallback(priced):
    priced.network.storage_units.at["Battery", "marginal_cost"] = 999
    workflow_tools._record_solve_inputs(
        priced.network, priced.solver_state["solver_config"]
    )
    raw = calculations.scoped_source(priced, scope(priced))
    facts, slots = calculations.scoped_calculate(raw, scope(priced))
    assert all(s["disposition"] == "answered" for s in slots)
    assert {f["metric"]: f["value"] for f in facts} == VALUES


def test_static_operating_prices_are_used_only_without_dynamic_overrides(
    priced, checked
):
    n = priced.network
    for attribute, price in (("marginal_cost", 2), ("marginal_cost_storage", 0.5)):
        n.components["StorageUnit"].dynamic[attribute] = pd.DataFrame(index=n.snapshots)
        n.storage_units.at["Battery", attribute] = price
    record_solve(priced)
    # Discharge: 2*3*2 + 2*1*2 = 16; holding: (8*1 + 4*3)*.5 = 10.
    frames = checked.run(
        NumericalProvider({"total_cost": 41}),
        checked_scope=scope(priced, metrics=["total_cost"]),
    )
    assert approval(frames)["receipt"]["request_complete"]


def test_simultaneous_storage_operation_uses_discharge_and_state_not_net_power(priced):
    raw = calculations.scoped_source(priced, scope(priced))
    inputs = raw["storage_unit"]["inputs"]
    inputs["p_store"][0], inputs["p_dispatch"][0], inputs["p"][0] = 6.25, 0.5, -5.75
    # Extra charging and discharging cancel in stored energy; direct cost adds
    # .5 MW * 99 EUR/MWh * 1 objective-hour = 49.5, while states stay 8/4/0.
    facts, slots = calculations.scoped_calculate(
        raw, scope(priced, metrics=["total_cost"])
    )
    assert slots[0]["disposition"] == "answered" and facts[0]["value"] == 100.5


def test_zero_cost_baseline_has_signed_delta_but_no_percentage(priced, checked):
    n = priced.network
    n.storage_units.at["Battery", "capital_cost"] = 0
    n.storage_units_t.marginal_cost.loc[:, "Battery"] = 0
    n.storage_units_t.marginal_cost_storage.loc[:, "Battery"] = 0
    record_solve(priced)
    first = checked.run(
        NumericalProvider({"total_cost": 0}),
        checked_scope=scope(priced, metrics=["total_cost"], retain=True),
    )
    base_id = approval(first)["receipt"]["evidence_ids"][0]
    n.storage_units.at["Battery", "capital_cost"] = 3
    n.storage_units_t.marginal_cost.loc[:, "Battery"] = [99, 2, 4]
    n.storage_units_t.marginal_cost_storage.loc[:, "Battery"] = [0.5, 1, 7]
    record_solve(priced)
    frames = checked.run(
        NumericalProvider({"total_cost": 51, "cost_delta": 51}),
        checked_scope=scope(
            priced,
            metrics=["total_cost", "cost_delta", "cost_change_percent"],
            comparison_evidence_id=base_id,
        ),
    )
    result = answer(checked, frames)
    assert not result["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in result["blocks"]} == {
        "total_cost": 51,
        "cost_delta": 51,
    }
    assert (
        result["receipt"]["result_slots"][-1]["reason"]
        == "baseline_denominator_zero_or_unavailable"
    )


def test_storage_operating_cost_choice_includes_holding_cost_and_keeps_basis(
    priced, conversation
):
    frames, provider = conversation("Show cost for storage unit Battery.")
    clarified(frames, provider, "cost_quantity_required")
    choice = next(data for event, data in frames if event == "choice_request")
    assert choice["options"][0]["label"] == "Direct StorageUnit operating expenditure"
    frames, provider = conversation("Direct StorageUnit operating expenditure")
    clarified(frames, provider, "explicit_cost_basis_required")
    frames, _ = conversation(
        "Price year 2026; annual fixed cost fraction 0.5",
        {"storage_operating_expenditure": 36},
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert approval(frames)["receipt"]["request_scope"]["profile"] == PROFILE


def test_storage_key_results_with_declared_basis_use_economics_defaults(
    priced, conversation
):
    frames, _ = conversation(
        "Show key results for storage unit Battery; price year 2026; annual fixed cost fraction 0.5.",
        VALUES,
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert approval(frames)["receipt"]["request_scope"]["profile"] == PROFILE


def test_previous_distinct_storage_run_comparison_uses_shared_conversation_path(
    priced, conversation
):
    priced.network.storage_units.at["Battery", "p_nom"] = 20
    record_solve(priced)
    frames, _ = conversation(
        "Check storage cost.",
        {"total_cost": 66},
        checked_scope=scope(priced, metrics=["total_cost"]),
    )
    assert approval(frames)["receipt"]["request_complete"]
    priced.network.storage_units.at["Battery", "p_nom"] = 10
    record_solve(priced)
    for _ in range(2):
        frames, _ = conversation(
            "Check storage cost.",
            {"total_cost": 51},
            checked_scope=scope(priced, metrics=["total_cost"]),
        )
        assert approval(frames)["receipt"]["request_complete"]
    frames, _ = conversation(
        "Compare that with the previous run.",
        {"total_cost": 51, "cost_delta": -15, "cost_change_percent": -15 / 66 * 100},
    )
    assert approval(frames)["receipt"]["request_complete"]


def test_storage_cost_named_network_keeps_type_during_clarification(
    priced, conversation
):
    component = priced.network.components["StorageUnit"]
    component.static = component.static.rename(index={"Battery": "network"})
    for attribute, values in component.dynamic.items():
        component.dynamic[attribute] = values.rename(columns={"Battery": "network"})
    record_solve(priced)
    frames, provider = conversation("Show cost for storage unit network.")
    clarified(frames, provider, "cost_quantity_required")
    frames, provider = conversation("Total declared-basis cost")
    clarified(frames, provider, "explicit_cost_basis_required")
    frames, _ = conversation(
        "Price year 2026; annual fixed cost fraction 0.5", {"total_cost": 51}
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "StorageUnit network" in public_text(
        frames
    ) and "Network network" not in public_text(frames)
    frames, provider = conversation(
        "Show those results again.",
        {"total_cost": 51},
        ui_context={"selected_component": {"class": "Generator", "name": "network"}},
    )
    clarified(frames, provider, "conversation_scope_changed")
