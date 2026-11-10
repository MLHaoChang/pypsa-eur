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
    from services.assurance_records import (
        DERIVED_POLICY_VERSION,
        INPUT_ADAPTERS,
        POLICY_VERSION,
        PREDICATE_POLICY_VERSION,
    )

    assert set(INPUT_ADAPTERS.values()) <= set(live["evidence_identity"])
    assert {"scoped_result", "calculated_result"} <= set(live["evidence_identity"])
    assert set(live["answer_receipt"]) == {
        POLICY_VERSION,
        DERIVED_POLICY_VERSION,
        PREDICATE_POLICY_VERSION,
    }
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
    added["answer_receipt"]["4"] = {"type": "object"}
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


def _ui_open_panel_with_enum(monkeypatch, enum):
    """Swap ui_open_panel's results_tab enum in the catalogue for this test only."""
    tools = copy.deepcopy(catalogue.TOOLS)
    tool = next(t for t in tools if t["name"] == "ui_open_panel")
    tool["input_schema"]["properties"]["results_tab"]["enum"] = enum
    monkeypatch.setattr(catalogue, "TOOLS", tools)


def test_a_listed_input_addition_keeps_the_frozen_hash(live, frozen):
    """AA-V1-CHANGE-1: a checked tool accepting a listed new enum member is additive."""
    added = contracts.V1_INPUT_ADDITIONS["ui_open_panel"]["results_tab"]
    tool = next(t for t in catalogue.TOOLS if t["name"] == "ui_open_panel")
    assert set(added) <= set(tool["input_schema"]["properties"]["results_tab"]["enum"])
    assert (
        live["checked_tools"]["ui_open_panel"]
        == frozen["checked_tools"]["ui_open_panel"]
    )
    assert live["checked_tool_input_additions"]["ui_open_panel"]["results_tab"] == {
        "enum": list(added)
    }


def test_an_unlisted_enum_widening_is_additive_and_write_accepts_it(
    tmp_path, monkeypatch, frozen
):
    """G2P-21: a widening moves the hash; `checked_tool_inputs` shows it is additive."""
    _ui_open_panel_with_enum(monkeypatch, [*catalogue.RESULTS_TAB_ENUM, "not_listed"])
    widened = contracts.build()
    assert (
        widened["checked_tools"]["ui_open_panel"]
        != frozen["checked_tools"]["ui_open_panel"]
    )
    assert breaking_changes(frozen, widened) == []
    snapshot = tmp_path / "assurance_contracts.v1.json"
    snapshot.write_text(json.dumps(frozen))
    monkeypatch.setattr(contracts, "SNAPSHOT", snapshot)
    assert contracts.main(["--write"]) == 0
    assert json.loads(snapshot.read_text()) == widened


def test_dropping_an_original_enum_member_is_refused_by_write(
    tmp_path, monkeypatch, frozen
):
    gone = catalogue.RESULTS_TAB_ENUM[0]
    _ui_open_panel_with_enum(
        monkeypatch, [t for t in catalogue.RESULTS_TAB_ENUM if t != gone]
    )
    assert breaking_changes(frozen, contracts.build()) == [
        f"$.checked_tool_inputs.ui_open_panel.properties.results_tab.enum drops {gone!r}"
    ]
    snapshot = tmp_path / "assurance_contracts.v1.json"
    snapshot.write_text(json.dumps(frozen))
    monkeypatch.setattr(contracts, "SNAPSHOT", snapshot)
    assert contracts.main(["--write"]) == 1
    assert json.loads(snapshot.read_text()) == frozen


def test_dropping_a_listed_addition_breaks(monkeypatch, frozen):
    gone = contracts.V1_INPUT_ADDITIONS["ui_open_panel"]["results_tab"][0]
    _ui_open_panel_with_enum(
        monkeypatch, [t for t in catalogue.RESULTS_TAB_ENUM if t != gone]
    )
    monkeypatch.setattr(contracts, "V1_INPUT_ADDITIONS", {})
    assert sorted(breaking_changes(frozen, contracts.build())) == [
        "$.checked_tool_input_additions.ui_open_panel removed",
        "$.checked_tool_inputs.ui_open_panel.properties.results_tab.enum drops "
        f"{gone!r}",
    ]


def _input_edit(frozen, tool, edit):
    """`frozen` with `edit` applied to `tool`'s stored input schema, rehashed."""
    changed = copy.deepcopy(frozen)
    schema = changed["checked_tool_inputs"][tool]
    edit(schema)
    additions = {
        field: spec["enum"]
        for field, spec in changed["checked_tool_input_additions"].get(tool, {}).items()
    }
    changed["checked_tools"][tool] = contracts._schema_sha256(
        contracts._v1_input_schema(tool, schema, additions)
    )
    return changed


def _first_optional(schema):
    required = set(schema.get("required", []))
    return next(name for name in schema["properties"] if name not in required)


TOOL = "ui_open_panel"
TAB = ("properties", "results_tab")


def _at(schema, path):
    for key in path:
        schema = schema[key]
    return schema


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(
            lambda s: _at(s, TAB)["enum"].append("new_tab"), id="enum-widening"
        ),
        pytest.param(
            lambda s: s["properties"].__setitem__("extra", {"type": "string"}),
            id="optional-property",
        ),
        pytest.param(
            lambda s: s["properties"].__setitem__(
                "nested",
                {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
            ),
            id="new-nested-property",
        ),
        pytest.param(
            lambda s: s["properties"].__setitem__("required", {"type": "string"}),
            id="property-named-required",
        ),
        pytest.param(
            lambda s: s["properties"].__setitem__("default", {"type": "string"}),
            id="property-named-default",
        ),
        pytest.param(
            lambda s: _at(s, TAB).__setitem__("description", "Reworded."),
            id="description",
        ),
    ],
)
def test_widening_a_checked_tool_input_is_additive(frozen, edit):
    changed = _input_edit(frozen, TOOL, edit)
    assert changed["checked_tools"][TOOL] != frozen["checked_tools"][TOOL]
    assert breaking_changes(frozen, changed) == []


def test_a_nested_enum_widening_is_additive(frozen):
    nested = {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}
    base = _input_edit(
        frozen, TOOL, lambda s: s["properties"].__setitem__("nested", nested)
    )
    widened = _input_edit(
        base,
        TOOL,
        lambda s: s["properties"]["nested"]["items"]["enum"].append("c"),
    )
    assert breaking_changes(base, widened) == []
    narrowed = _input_edit(
        base,
        TOOL,
        lambda s: s["properties"]["nested"]["items"]["enum"].remove("a"),
    )
    assert breaking_changes(base, narrowed) == [
        f"$.checked_tool_inputs.{TOOL}.properties.nested.items.enum drops 'a'"
    ]


@pytest.mark.parametrize(
    "edit, expected",
    [
        (
            lambda s: s["properties"].pop(_first_optional(s)),
            "removed",
        ),
        (
            lambda s: s.setdefault("required", []).append(_first_optional(s)),
            "adds required",
        ),
        (lambda s: _at(s, TAB).__setitem__("pattern", "^a"), "added constraint"),
        (lambda s: _at(s, TAB).__setitem__("format", "date"), "added constraint"),
        (lambda s: _at(s, TAB).__setitem__("const", "results"), "added constraint"),
        (lambda s: _at(s, TAB).__setitem__("type", "integer"), "changed"),
        (lambda s: _at(s, TAB).__setitem__("default", "summary"), "added default"),
    ],
    ids=[
        "remove-optional",
        "add-required",
        "pattern",
        "format",
        "const",
        "type",
        "add-default",
    ],
)
def test_narrowing_a_checked_tool_input_breaks(frozen, edit, expected):
    changed = _input_edit(frozen, TOOL, edit)
    broken = breaking_changes(frozen, changed)
    assert broken and all(b.startswith(f"$.checked_tool_inputs.{TOOL}") for b in broken)
    assert any(expected in b for b in broken), broken


def test_additional_properties_true_to_false_breaks(frozen):
    base = _input_edit(
        frozen, TOOL, lambda s: s.__setitem__("additionalProperties", True)
    )
    closed = _input_edit(
        base, TOOL, lambda s: s.__setitem__("additionalProperties", False)
    )
    assert breaking_changes(base, closed) == [
        f"$.checked_tool_inputs.{TOOL}.additionalProperties changed"
    ]


def test_a_changed_or_removed_default_breaks(frozen):
    """G1: a default decides what an old call omitting the argument does."""
    base = _input_edit(frozen, TOOL, lambda s: _at(s, TAB).__setitem__("default", "a"))
    changed = _input_edit(base, TOOL, lambda s: _at(s, TAB).__setitem__("default", "b"))
    removed = _input_edit(base, TOOL, lambda s: _at(s, TAB).pop("default"))
    where = f"$.checked_tool_inputs.{TOOL}.properties.results_tab.default"
    assert breaking_changes(base, changed) == [f"{where} changed"]
    assert breaking_changes(base, removed) == [f"{where} removed"]


@pytest.mark.parametrize("name", ["title", "description", "default", "required"])
def test_a_property_named_like_a_keyword_is_a_property(frozen, name):
    """G2: inside `properties` every key is a property name, never a keyword."""
    prop = {"type": "string"}
    base = _input_edit(frozen, TOOL, lambda s: s["properties"].__setitem__(name, prop))
    assert breaking_changes(frozen, base) == []
    removed = _input_edit(base, TOOL, lambda s: s["properties"].pop(name))
    assert breaking_changes(base, removed) == [
        f"$.checked_tool_inputs.{TOOL}.properties.{name} removed"
    ]


def test_the_hash_skip_fails_closed(frozen):
    """G3: only a hash its stored schema explains, on both sides, is skipped."""
    widened = _input_edit(frozen, TOOL, lambda s: _at(s, TAB)["enum"].append("x"))
    changed = f"$.checked_tools.{TOOL} changed"
    old_without = copy.deepcopy(frozen)
    del old_without["checked_tool_inputs"]
    assert changed in breaking_changes(old_without, widened)
    old_missing_tool = copy.deepcopy(frozen)
    del old_missing_tool["checked_tool_inputs"][TOOL]
    assert changed in breaking_changes(old_missing_tool, widened)
    new_missing_tool = copy.deepcopy(widened)
    del new_missing_tool["checked_tool_inputs"][TOOL]
    assert changed in breaking_changes(frozen, new_missing_tool)
    bare_hash = copy.deepcopy(frozen)
    bare_hash["checked_tools"][TOOL] = "0" * 64
    assert breaking_changes(frozen, bare_hash) == [changed]
    removed = copy.deepcopy(widened)
    del removed["checked_tools"][TOOL]
    del removed["checked_tool_inputs"][TOOL]
    assert sorted(breaking_changes(frozen, removed)) == [
        f"$.checked_tool_inputs.{TOOL} removed",
        f"$.checked_tools.{TOOL} removed",
    ]


def test_a_new_checked_tool_input_is_additive(frozen):
    added = copy.deepcopy(frozen)
    added["checked_tools"]["new_tool"] = contracts._schema_sha256({"type": "object"})
    added["checked_tool_inputs"]["new_tool"] = {"type": "object"}
    assert breaking_changes(frozen, added) == []


# The v1 hashes as master froze them before G2P-21 added `checked_tool_inputs`,
# moved only by an additive input widening (each noted on its line).
V1_HASHES = {
    "compare_project_runs": "5ff81e9828bdd6ccb997ace8ad45a83d40efafd2ac4860887ca0f17247238a5f",
    "get_aggregate_load": "49cd7d3066d8405553280d53afeea4b809936b2157ae9e0670f4bba89eeb66a2",
    "get_component": "8fcf1af620907ec9eea2e325e5981fd665785455bab256b5ead3f13987acf2b3",
    "get_run_evidence": "4abaac2419832365fa560a3c15252f7900510c90f2b42c052298b438a97032cb",
    "get_timeseries": "379ddbdecfeb262575eee58c147e81cb6ffa59905d2c8738d12519f3303a5c88",
    "list_components": "6e4521eb8f8ac1bc72ab54928c82adbab7b47bf4bb8f627ff00b10ceb2dfa353",
    "ui_open_panel": "376cf3f7a156dae8bb641f93a8203f0f37d851b98100ae0d6f20212db5c8bbe1",  # LC-14: guided_step, panel_section widened the input (additive)
    "update_component": "620554ac6bc2ecc1d9abf2620676d5e050b7c0f5ad50d0c0d00c1de13f908734",
}


def test_adding_the_inputs_section_keeps_every_v1_hash(tmp_path, monkeypatch, live):
    """G4: on master's tree --write adds only `checked_tool_inputs`."""
    assert live["checked_tools"] == V1_HASHES
    before = copy.deepcopy(live)
    del before["checked_tool_inputs"]
    del before["directions"]["checked_tool_inputs"]
    snapshot = tmp_path / "assurance_contracts.v1.json"
    snapshot.write_text(json.dumps(before))
    monkeypatch.setattr(contracts, "SNAPSHOT", snapshot)
    assert contracts.main(["--write"]) == 0
    written = json.loads(snapshot.read_text())
    assert set(written) - set(before) == {"checked_tool_inputs"}
    assert {k: v for k, v in written.items() if k != "directions"} == {
        **{k: v for k, v in before.items() if k != "directions"},
        "checked_tool_inputs": live["checked_tool_inputs"],
    }
    assert written["checked_tools"] == V1_HASHES


@pytest.mark.parametrize(
    "additions, match",
    [
        ({"ui_open_panel": {"results_tab": ["no_such_tab"]}}, "no_such_tab"),
        ({"ui_open_panel": {"panel_id_missing": ["x"]}}, "panel_id_missing"),
        ({"ui_open_panel": {"window_from": [1]}}, "window_from"),
        ({"not_a_checked_tool": {"results_tab": ["x"]}}, "not_a_checked_tool"),
    ],
)
def test_a_listed_addition_the_live_schema_lacks_refuses_to_build(
    monkeypatch, additions, match
):
    monkeypatch.setattr(contracts, "V1_INPUT_ADDITIONS", additions)
    with pytest.raises(ValueError, match=match):
        contracts.build()
