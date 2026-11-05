"""A0.4: the frozen v1 contract snapshot; drift without a reviewed mapping fails CI."""

import copy
import json
import uuid

import pytest

from harness import assurance_contracts as contracts
from harness import calculations, catalogue, effects
from harness.assurance_contracts import breaking_changes


@pytest.fixture(scope="module")
def live():
    return contracts.build()


@pytest.fixture(scope="module")
def frozen():
    return json.loads(contracts.SNAPSHOT.read_text())


def test_live_contracts_match_the_reviewed_snapshot(live, frozen):
    assert live == frozen, (
        "Assurance contracts changed: review the change, then run "
        "`python -m harness.assurance_contracts --write` from pypsa-gui/backend."
    )
    assert contracts.main(["--check"]) == 0


def test_every_profile_and_checked_tool_is_frozen(live):
    assert set(live["profiles"]) == set(calculations.PROFILES)
    assert set(live["request_scope"]["properties"]["profile"]["enum"]) == set(
        calculations.PROFILES
    )
    assert {"update_component", "get_component"} <= set(live["checked_tools"])
    assert (
        live["effect_guard"]["properties"]["tool_name"]["const"] == "update_component"
    )


def test_changed_checked_tool_parameter_fails_the_check(monkeypatch):
    tools = copy.deepcopy(catalogue.TOOLS)
    tool = next(t for t in tools if t["name"] == "update_component")
    tool["input_schema"].setdefault("properties", {})["force"] = {"type": "boolean"}
    monkeypatch.setattr(catalogue, "TOOLS", tools)
    assert contracts.main(["--check"]) == 1


def test_changed_metric_definition_fails_the_check(monkeypatch):
    definitions = copy.deepcopy(calculations.DEFINITIONS)
    definitions["dispatch_energy"]["unit"] = "kWh"
    monkeypatch.setattr(calculations, "DEFINITIONS", definitions)
    assert contracts.main(["--check"]) == 1


def test_profile_without_a_request_scope_entry_fails_closed(monkeypatch):
    monkeypatch.setattr(
        calculations,
        "PROFILES",
        {**calculations.PROFILES, "unmapped/1": ("peak_dispatch",)},
    )
    with pytest.raises(ValueError, match="unmapped/1"):
        contracts.build()


def test_profile_metric_without_a_formula_fails_closed(monkeypatch):
    profiles = dict(calculations.PROFILES)
    profiles["generator-dispatch/1"] += ("made_up",)
    monkeypatch.setattr(calculations, "PROFILES", profiles)
    with pytest.raises(ValueError, match="made_up"):
        contracts.build()


def _edit(document, path, value):
    *head, last = path
    for key in head:
        document = document[key]
    if value is KeyError:
        del document[last]
    else:
        document[last] = value


SCOPE = ("request_scope", "properties")
GEN_ENERGY = ("profiles", "generator-dispatch/1", "definitions", "dispatch_energy")


@pytest.mark.parametrize(
    "path,value",
    [
        ((*SCOPE, "entity_id"), KeyError),
        ((*SCOPE, "entity_id", "maxLength"), 64),
        ((*SCOPE, "entity_id", "pattern"), "^[A-Z]+$"),
        (
            ("request_scope", "required"),
            ["version", "profile", "project_id", "entity_id", "start"],
        ),
        ((*SCOPE, "profile", "enum"), ["generator-dispatch/1"]),
        (
            (
                "profiles",
                "generator-dispatch/1",
                "definitions",
                "dispatch_energy",
                "unit",
            ),
            "kWh",
        ),
        (("profiles", "generator-dispatch/1", "metrics"), ["dispatch_energy"]),
        (("profiles", "generator-dispatch/1"), KeyError),
        (("checked_tools", "update_component"), "0" * 64),
        (("effect_guard", "properties", "operation_id", "maxLength"), 30),
        ((*GEN_ENERGY, "sign"), "positive_is_output"),
        ((*GEN_ENERGY, "label"), KeyError),
        ((*GEN_ENERGY, "inputs"), ["p", "energy_weight", "extra"]),
        (("directions", "profiles"), "accepts"),
        (("directions", "request_scope"), KeyError),
    ],
)
def test_narrowing_or_changing_a_frozen_contract_is_breaking(frozen, path, value):
    changed = copy.deepcopy(frozen)
    _edit(changed, path, value)
    assert breaking_changes(frozen, changed)


@pytest.mark.parametrize(
    "path,value",
    [
        (
            ("profiles", "new-profile/1"),
            {"metrics": ["x"], "definitions": {"x": {"formula_id": "x/1"}}},
        ),
        ((*SCOPE, "note"), {"type": "string", "maxLength": 10}),
        ((*SCOPE, "entity_id", "maxLength"), 256),
        ((*SCOPE, "profile", "enum"), None),
        (("checked_tools", "new_tool"), "0" * 64),
        (("pending",), {}),
        (("new_contract",), {"type": "object"}),
    ],
)
def test_additive_changes_stay_in_version_one(frozen, path, value):
    changed = copy.deepcopy(frozen)
    if value is None:
        changed["request_scope"]["properties"]["profile"]["enum"].append(
            "new-profile/1"
        )
    else:
        _edit(changed, path, value)
    assert breaking_changes(frozen, changed) == []


def test_the_same_widening_is_additive_on_input_and_breaking_on_output(frozen):
    """A reader of a returned contract breaks on a new field or enum value."""
    accepts, returns = copy.deepcopy(frozen), copy.deepcopy(frozen)
    accepts["request_scope"]["properties"]["profile"]["enum"].append("new/1")
    accepts["request_scope"]["properties"]["note"] = {"type": "string"}
    assert breaking_changes(frozen, accepts) == []
    definition = returns["profiles"]["generator-dispatch/1"]["definitions"]
    definition["dispatch_energy"]["unit_enum"] = ["MWh", "GWh"]
    definition["new_metric"] = {"formula_id": "x/1"}
    assert breaking_changes(frozen, returns) == [
        "$.profiles.generator-dispatch/1 changed"
    ]


def test_every_contract_declares_a_direction(monkeypatch, live):
    assert set(live["directions"]) == set(live) - {"version", "pending", "directions"}
    monkeypatch.setattr(
        contracts,
        "DIRECTIONS",
        {k: v for k, v in contracts.DIRECTIONS.items() if k != "profiles"},
    )
    with pytest.raises(ValueError, match="profiles"):
        contracts.build()


def test_write_refuses_a_non_additive_change(tmp_path, monkeypatch, frozen):
    snapshot = tmp_path / "assurance_contracts.v1.json"
    narrowed = copy.deepcopy(frozen)
    narrowed["profiles"]["generator-dispatch/1"]["metrics"].append("only_in_old")
    snapshot.write_text(json.dumps(narrowed))
    monkeypatch.setattr(contracts, "SNAPSHOT", snapshot)
    assert contracts.main(["--write"]) == 1
    assert json.loads(snapshot.read_text()) == narrowed


def _contract(**overrides):
    return {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "project_id": "p",
        "tool_name": "update_component",
        "arguments": {"component_class": "Load", "name": "L1", "attrs": {"p_set": 6.0}},
        "before_sha256": "a" * 64,
        **overrides,
    }


def test_published_guard_schema_is_the_enforced_one():
    assert effects.validate(_contract())["tool_name"] == "update_component"
    effects.EffectContract.model_validate(_contract())
    for forged in (
        {"tool_name": "delete_component"},
        {"confirmed": True},
        {"version": 2},
        {"arguments": {"component_class": "Load", "name": "L1", "attrs": {}}},
        {
            "arguments": {
                "component_class": "Load",
                "name": "L1",
                "attrs": {"p_set": "6"},
            }
        },
    ):
        with pytest.raises(ValueError):
            effects.validate(_contract(**forged))
        with pytest.raises(ValueError):
            effects.EffectContract.model_validate(_contract(**forged))


def test_contracts_route_serves_the_frozen_snapshot(frozen):
    """`/reliability/contracts` serves what CI freezes, and still carries the
    fields it served before the snapshot existed (AA-V1-CHANGE-1: additive)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from harness.assurance import Draft, ProfileDraft
    from harness.assurance_scope import RequestScope
    from routers import chat

    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    response = TestClient(app).get("/api/chat/reliability/contracts")
    assert response.status_code == 200
    body = response.json()
    assert body == frozen
    assert body["version"] == 1
    for key, model in (
        ("request_scope", RequestScope),
        ("observed_draft", Draft),
        ("computed_draft", ProfileDraft),
    ):
        assert body[key] == json.loads(json.dumps(model.model_json_schema()))
    assert {"effect_guard", "profiles", "checked_tools", "pending"} <= set(body)


def test_the_build_ships_the_snapshot_where_the_route_reads_it():
    """The packaged app's `/reliability/contracts` reads the snapshot from disk,
    so the PyInstaller allowlist must ship it beside `assurance_contracts.py`."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    spec = (backend.parent / "pypsa-gui.spec").read_text(encoding="utf-8")
    assert (
        '(str(BACKEND / "harness" / "assurance_contracts.v1.json"), "harness")' in spec
    )
    module = Path(contracts.__file__).resolve()
    assert contracts.SNAPSHOT.resolve().parent == module.parent
    assert module.parent.relative_to(backend).as_posix() == "harness"


def test_every_adapter_receipt_and_checked_tool_has_a_returned_shape(live):
    """Issue 02: evidence identity, the receipt and tool results are frozen."""
    from harness.assurance import CHECKED_TOOLS
    from services.assurance_records import INPUT_ADAPTERS, POLICY_VERSION

    assert set(INPUT_ADAPTERS.values()) <= set(live["evidence_identity"])
    assert {"scoped_result", "calculated_result"} <= set(live["evidence_identity"])
    assert set(live["answer_receipt"]) == {POLICY_VERSION}
    assert set(live["checked_tool_results"]) == CHECKED_TOOLS
    for key in ("evidence_identity", "answer_receipt", "checked_tool_results"):
        assert live["directions"][key] == "returns"
    assert not {"evidence_and_receipt_identity", "checked_tool_result_shapes"} & set(
        live["pending"]
    )


def test_a_checked_tool_without_a_returned_shape_fails_closed(monkeypatch):
    from harness import assurance_shapes

    monkeypatch.setattr(
        assurance_shapes,
        "TOOL_RESULTS",
        {
            k: v
            for k, v in assurance_shapes.TOOL_RESULTS.items()
            if k != "get_component"
        },
    )
    with pytest.raises(ValueError, match="get_component"):
        contracts.build()


def test_a_new_returned_field_breaks_and_a_new_version_is_additive(frozen):
    changed = copy.deepcopy(frozen)
    changed["answer_receipt"]["1"]["properties"]["confidence"] = {"type": "number"}
    changed["evidence_identity"]["static-input/1"]["required"].append("note")
    changed["checked_tool_results"]["get_component"]["title"] = "Renamed"
    assert sorted(breaking_changes(frozen, changed)) == [
        "$.answer_receipt.1 changed",
        "$.checked_tool_results.get_component changed",
        "$.evidence_identity.static-input/1 changed",
    ]
    added = copy.deepcopy(frozen)
    added["answer_receipt"]["2"] = {"type": "object"}
    added["evidence_identity"]["static-input/2"] = {"type": "object"}
    added["checked_tool_results"]["new_tool"] = {"type": "object"}
    assert breaking_changes(frozen, added) == []


def test_a_result_outside_its_shape_is_refused():
    from harness import assurance_shapes

    meta = {"version": 1, "outcome": "completed", "evidence_ids": ["e"]}
    run = {
        "run_id": "r",
        "status": "completed",
        "finished_at": "2026-01-01T00:00:00Z",
        "mode": "lopf",
        "unsaved_inputs": False,
        "metrics": {"objective": {"value": 1.0, "unit": "EUR"}},
        "_assurance": meta,
    }
    assurance_shapes.validate_tool_result("get_run_evidence", run)
    with pytest.raises(ValueError, match="get_run_evidence"):
        assurance_shapes.validate_tool_result("get_run_evidence", {**run, "note": "x"})
    with pytest.raises(ValueError):
        assurance_shapes.validate_tool_result(
            "ui_open_panel", {"_assurance": {**meta, "outcome": "maybe"}}
        )
