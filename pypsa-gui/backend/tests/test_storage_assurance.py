"""Independent storage LP values through the authenticated checked stream."""

# ruff: noqa: F811 -- imported shared fixtures.
import hashlib
import io
import json
import zipfile

import pandas as pd
import pytest

from services import assurance_records, chat_service, workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_conversation_scope import conversation  # noqa: F401
from tests.test_conversation_scope import clarified
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "storage-unit-dispatch/1"
VALUES = {
    "charged_energy": 10,
    "discharged_energy": 4,
    "net_storage_energy": -6,
    "peak_charging_power": 5,
    "peak_discharging_power": 2,
    "final_state_of_charge": 0,
}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


@pytest.fixture
def storage(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.loads_t.p_set = pd.DataFrame({"L1": [0, 2, 2]}, index=n.snapshots)
    n.generators_t.p_max_pu = pd.DataFrame({"G1": [1, 0, 0]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "stores"] = [2, 1, 1]
    n.snapshot_weightings.loc[:, "generators"] = [3, 4, 5]
    n.snapshot_weightings.loc[:, "objective"] = [1, 3, 1]
    n.add(
        "StorageUnit",
        "Battery",
        bus="B1",
        p_nom=10,
        max_hours=2,
        efficiency_store=0.8,
        efficiency_dispatch=0.5,
        state_of_charge_initial=0,
        cyclic_state_of_charge=False,
    )
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    # Independently: charge 5 MW * 2 h * .8 = 8 MWh; discharge each
    # 2 MW * 1 h / .5 = 4 MWh, leaving 4 then 0 MWh.
    assert list(n.storage_units_t.p_store.Battery) == pytest.approx([5, 0, 0])
    assert list(n.storage_units_t.p_dispatch.Battery) == pytest.approx([0, 2, 2])
    assert list(n.storage_units_t.state_of_charge.Battery) == pytest.approx([8, 4, 0])
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


def scope(ctx, **options):
    return {
        "version": 1,
        "profile": PROFILE,
        "project_id": ctx.project_uuid,
        "entity_id": "Battery",
        "retain": False,
        **options,
    }


def answer(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return response.json()


def test_storage_key_results_use_raw_split_power_and_storage_durations(
    storage, checked
):
    frames = checked.run(
        NumericalProvider(VALUES),
        message="Check battery results.",
        checked_scope=scope(storage),
    )
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in record["blocks"]} == VALUES
    text = public_text(frames)
    assert "StorageUnit Battery" in text and "Generator Battery" not in text
    assert "10 MWh" in text and "-6 MWh" in text
    assert "stores" in text and "not the change in stored energy" in text


def test_selected_storage_key_results_use_the_same_raw_checked_profile(
    storage, checked
):
    frames = checked.run(
        NumericalProvider(VALUES),
        message="Give me the key results.",
        ui_context={"selected_component": {"class": "StorageUnit", "name": "Battery"}},
    )
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"]
    assert record["receipt"]["request_scope"]["profile"] == PROFILE
    assert {b["metric"]: b["value"] for b in record["blocks"]} == VALUES


def test_storage_message_window_with_punctuation_keeps_exact_requested_boundaries(
    storage, checked
):
    values = {
        **VALUES,
        "charged_energy": 0,
        "discharged_energy": 2,
        "net_storage_energy": 2,
        "peak_charging_power": 0,
        "final_state_of_charge": 4,
    }
    frames = checked.run(
        NumericalProvider(values),
        message="Give me the key results for storage unit Battery; from 2026-01-01T01:00:00 to 2026-01-01T01:00:00.",
    )
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in record["blocks"]} == values
    assert record["receipt"]["request_scope"]["end"] == "2026-01-01T01:00:00"


def test_selected_storage_generic_grammar_cannot_ignore_added_quantities(storage):
    from harness.assurance_scope import from_message

    ui = {"selected_component": {"class": "StorageUnit", "name": "Battery"}}
    for message in (
        "Give me the key results and total cost.",
        "Show key results; ignore missing states.",
    ):
        assert from_message(message, storage.project_uuid, ui) is None


def test_storage_window_preserves_trajectory_and_uses_last_requested_state(
    storage, checked
):
    values = {
        **VALUES,
        "charged_energy": 0,
        "discharged_energy": 2,
        "net_storage_energy": 2,
        "peak_charging_power": 0,
        "final_state_of_charge": 4,
    }
    frames = checked.run(
        NumericalProvider(values),
        message="Check battery window.",
        checked_scope=scope(
            storage, start="2026-01-01T01:00:00", end="2026-01-01T01:00:00"
        ),
    )
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in record["blocks"]} == values


def test_storage_whole_message_and_restart_repeat_use_same_checked_scope(
    storage, checked, conversation
):
    first, _ = conversation("Give me the key results for storage unit Battery.", VALUES)
    assert approval(first)["receipt"]["request_complete"]
    chat_service.drop_session(conversation.sid)
    response = checked.client.get("/api/chat/history")
    assert response.status_code == 200
    frames, _ = conversation(
        "Show those results again.",
        VALUES,
        binding_generation=8,
        ui_context={"selected_component": {"class": "StorageUnit", "name": "Battery"}},
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert {
        b["metric"]: b["value"] for b in answer(checked, frames)["blocks"]
    } == VALUES


@pytest.mark.parametrize(
    "fault", ["missing", "balance", "net", "power", "state", "prior"]
)
def test_invalid_raw_storage_trajectory_cannot_publish_values(storage, checked, fault):
    n = storage.network
    if fault == "missing":
        n.storage_units_t.p_store.at[n.snapshots[0], "Battery"] = float("nan")
    elif fault == "balance":
        n.storage_units_t.state_of_charge.at[n.snapshots[1], "Battery"] = 5
    elif fault == "net":
        n.storage_units_t.p.at[n.snapshots[1], "Battery"] = 3
    elif fault == "power":
        n.storage_units_t.p_dispatch.at[n.snapshots[1], "Battery"] = 11
    elif fault == "state":
        n.storage_units_t.state_of_charge.at[n.snapshots[0], "Battery"] = 21
    else:
        n.storage_units_t.state_of_charge.at[n.snapshots[0], "Battery"] = float("nan")
    window = (
        {"start": "2026-01-01T01:00:00", "end": "2026-01-01T02:00:00"}
        if fault == "prior"
        else {}
    )
    frames = checked.run(NumericalProvider({}), checked_scope=scope(storage, **window))
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"] is False
    assert record["blocks"] == []
    assert all(
        slot["disposition"] == "unavailable"
        for slot in record["receipt"]["result_slots"]
    )
    assert "10 MWh" not in public_text(frames)


def test_storage_current_evidence_invalidates_after_edit(storage, checked):
    frames = checked.run(NumericalProvider(VALUES), checked_scope=scope(storage))
    record = approval(frames)
    evidence_id = record["receipt"]["evidence_ids"][0]
    storage.network.storage_units.at["Battery", "efficiency_store"] = 0.9
    response = checked.client.get("/api/chat/evidence/" + evidence_id)
    assert response.status_code == 409


def test_storage_retained_export_keeps_independent_values_after_edit(storage, checked):
    frames = checked.run(
        NumericalProvider(VALUES), checked_scope=scope(storage, retain=True)
    )
    record = approval(frames)
    storage.network.storage_units.at["Battery", "efficiency_store"] = 0.9
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200
    evidence = assurance_records.evidence(
        assurance_records.actor_id(),
        record["receipt"]["evidence_ids"][0],
    )
    assert {f["metric"]: f["value"] for f in evidence["facts"]} == VALUES
    exported = checked.client.get(f"/api/chat/answers/{record['answer_id']}/export")
    assert exported.status_code == 200
    assert {
        b["metric"]: b["value"] for b in exported.json()["answer"]["blocks"]
    } == VALUES
    zipped = checked.client.get(
        f"/api/chat/answers/{record['answer_id']}/export?format=zip"
    )
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["request_scope"]["profile"] == PROFILE
        for name, metadata in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == metadata["sha256"]
        assert (
            b"Battery,charged_energy,10.0,MWh,storage-charge-energy/1"
            in bundle.read("facts.csv")
        )


@pytest.mark.parametrize("fault", ["value", "unit", "formula", "omit", "extra"])
def test_storage_model_fabrication_or_omission_cannot_escape_publication(
    storage, checked, fault
):
    def mutate(draft):
        if fault == "value":
            draft["blocks"][0]["value"] = 999
        elif fault == "unit":
            draft["blocks"][0]["unit"] = "MW"
        elif fault == "formula":
            draft["blocks"][0]["formula_id"] = "invented/1"
        elif fault == "omit":
            draft["blocks"].pop()
        else:
            draft["narrative"] = "Battery operation is always safe."

    frames = checked.run(
        NumericalProvider(VALUES, mutate), checked_scope=scope(storage)
    )
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"] is False
    assert record["blocks"] == []
    assert "999" not in public_text(frames) and "always safe" not in public_text(frames)


def test_storage_cost_request_remains_an_explicit_unavailable_slot(storage, checked):
    frames = checked.run(
        NumericalProvider(VALUES),
        checked_scope=scope(storage, metrics=["charged_energy", "total_cost"]),
    )
    record = answer(checked, frames)
    assert record["receipt"]["request_complete"] is False
    assert {b["metric"]: b["value"] for b in record["blocks"]} == {"charged_energy": 10}
    slot = next(
        s for s in record["receipt"]["result_slots"] if s["metric"] == "total_cost"
    )
    assert (
        slot["disposition"] == "unavailable" and slot["reason"] == "unsupported_metric"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "cyclic",
        "inflow",
        "spill",
        "duplicate",
        "misaligned",
        "running",
        "failed",
        "weight",
    ],
)
def test_storage_unsupported_or_unavailable_sources_never_claim_completion(
    storage, checked, fault
):
    n = storage.network
    if fault == "cyclic":
        n.storage_units.at["Battery", "cyclic_state_of_charge"] = True
    elif fault == "inflow":
        n.storage_units.at["Battery", "inflow"] = 1
    elif fault == "spill":
        n.storage_units_t.spill = pd.DataFrame(
            {"Battery": [1, 0, 0]}, index=n.snapshots
        )
    elif fault == "duplicate":
        n.storage_units_t.p_store = pd.concat([n.storage_units_t.p_store] * 2, axis=1)
    elif fault == "misaligned":
        n.storage_units_t.p_store = n.storage_units_t.p_store.iloc[::-1]
    elif fault in {"running", "failed"}:
        storage.solver_state["status"] = fault
    else:
        n.snapshot_weightings.at[n.snapshots[0], "stores"] = float("nan")
    frames = checked.run(NumericalProvider({}), checked_scope=scope(storage))
    assert approval(frames)["receipt"]["request_complete"] is False
    assert approval(frames)["receipt"]["checked_block_ids"] == []


def test_storage_standing_loss_and_fractional_efficiencies_use_chronological_duration(
    storage,
):
    from harness import storage_calculations

    raw = storage_calculations.source(storage, "Battery")
    raw["index"] = raw["index"][:2]
    raw["inputs"] = {k: v[:2] for k, v in raw["inputs"].items()}
    raw["initial_state"] = 10
    raw["inputs"].update(
        p_store=[0, 1],
        p_dispatch=[1, 0],
        p=[1, -1],
        state_of_charge=[4.1, 4.08],
        standing_loss=[0.1, 0.2],
    )
    # First: 10*.9**2 - 2*1/.5 = 4.1. Second: 4.1*.8 + 1*.8 = 4.08.
    facts, slots = storage_calculations.calculate(raw, scope(storage))
    assert all(s["disposition"] == "answered" for s in slots)
    assert {f["metric"]: f["value"] for f in facts} == pytest.approx(
        {
            "charged_energy": 1,
            "discharged_energy": 2,
            "net_storage_energy": 1,
            "peak_charging_power": 1,
            "peak_discharging_power": 1,
            "final_state_of_charge": 4.08,
        }
    )


def test_storage_simultaneous_operation_cannot_be_reconstructed_from_net_power(storage):
    from harness import storage_calculations

    raw = storage_calculations.source(storage, "Battery")
    raw["inputs"]["p_store"][0] = 6.25
    raw["inputs"]["p_dispatch"][0] = 0.5
    raw["inputs"]["p"][0] = -5.75
    # Extra 1.25 MW charging adds 2 MWh; .5 MW discharge consumes 2 MWh.
    # The original states stay 8, 4, 0; clipping net power would give 11.5/4.
    facts, slots = storage_calculations.calculate(raw, scope(storage))
    assert all(s["disposition"] == "answered" for s in slots)
    assert {f["metric"]: f["value"] for f in facts} == {
        **VALUES,
        "charged_energy": 12.5,
        "discharged_energy": 5,
        "net_storage_energy": -7.5,
        "peak_charging_power": 6.25,
    }


def test_storage_named_network_still_requires_matching_component_selection(
    storage, conversation
):
    component = storage.network.components["StorageUnit"]
    component.static = component.static.rename(index={"Battery": "network"})
    for attribute, values in component.dynamic.items():
        component.dynamic[attribute] = values.rename(columns={"Battery": "network"})
    workflow_tools._record_solve_inputs(
        storage.network, storage.solver_state["solver_config"]
    )
    first, _ = conversation(
        "Check battery results.",
        VALUES,
        checked_scope=scope(storage, entity_id="network"),
    )
    assert approval(first)["receipt"]["request_complete"]
    frames, provider = conversation(
        "Show those results again.",
        VALUES,
        ui_context={"selected_component": {"class": "Generator", "name": "network"}},
    )
    clarified(frames, provider, "conversation_scope_changed")


@pytest.mark.parametrize(
    "options",
    [
        {
            "cost_basis": {
                "currency": "EUR",
                "price_year": 2026,
                "annual_fixed_cost_fraction": 1,
            }
        },
        {"comparison_evidence_id": "00000000-0000-0000-0000-000000000000"},
        {"start": "2026-01-01T00:00:00"},
    ],
)
def test_storage_unsupported_scope_is_rejected_before_provider(
    storage, checked, options
):
    provider = checked.install(NumericalProvider(VALUES))
    response = checked.client.post(
        "/api/chat/stream",
        json={
            "session_id": "storage-invalid-scope",
            "message": "Check battery results.",
            "checked_scope": scope(storage, **options),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "invalid_assurance_request"
    assert not provider.requests


@pytest.mark.parametrize(
    "fault",
    [
        "initial",
        "efficiency",
        "loss",
        "zero_duration",
        "nonexistent_window",
        "reversed_window",
    ],
)
def test_storage_raw_bounds_and_exact_windows_cannot_be_rounded_into_validity(
    storage, fault
):
    from harness import storage_calculations

    raw = storage_calculations.source(storage, "Battery")
    requested = scope(storage)
    if fault == "initial":
        raw["initial_state"] = -1
    elif fault == "efficiency":
        raw["inputs"]["efficiency_dispatch"][1] = 0
    elif fault == "loss":
        raw["inputs"]["standing_loss"][0] = 1.1
    elif fault == "zero_duration":
        raw["inputs"]["duration"][0] = 0
    elif fault == "nonexistent_window":
        requested.update(start="2026-01-01T00:30:00", end="2026-01-01T01:00:00")
    else:
        requested.update(start="2026-01-01T02:00:00", end="2026-01-01T01:00:00")
    if "window" in fault:
        with pytest.raises((KeyError, ValueError)):
            storage_calculations.calculate(raw, requested)
    else:
        facts, slots = storage_calculations.calculate(raw, requested)
        assert facts == []
        assert all(s["disposition"] == "unavailable" for s in slots)
