"""
Guided-mode spec §3.6 — the assistant can open the hub-design panel.

`ui_open_panel`'s `panel_id` is an enum (`SAFETY_PANEL_ENUM`); a value the
frontend accepts but the schema does not is a panel the model is told it
cannot open. The frontend normalises `HubDesign` / `hubDesign` / `hub_design`
to the `hubDesign` slide panel (ChatPanel._normalizePanelId).

No jsonschema dependency exists in the venv, so the input is checked with a
minimal validator for exactly what `_t()` emits: an object schema with
`required` names and per-property `type` / `enum`.
"""
from __future__ import annotations

from typing import Any

from services import chat_tools_schema
from services.chat_tools_schema import SAFETY_PANEL_ENUM

_PY_TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float),
             "object": dict, "array": list}


def _tool(name: str) -> dict[str, Any]:
    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == name:
            return tool
    raise AssertionError(f"no tool named {name!r} in the schema")


def _errors(schema: dict[str, Any], value: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    for req in schema.get("required", []):
        if req not in value:
            errs.append(f"missing {req}")
    props = schema.get("properties", {})
    for key, v in value.items():
        spec = props.get(key)
        if spec is None:
            errs.append(f"unknown {key}")
            continue
        typ = spec.get("type")
        if typ and not isinstance(v, _PY_TYPES[typ]):
            errs.append(f"{key}: not {typ}")
        if "enum" in spec and v not in spec["enum"]:
            errs.append(f"{key}: {v!r} not in enum")
    return errs


def test_hub_design_ids_are_in_the_panel_enum():
    assert "HubDesign" in SAFETY_PANEL_ENUM
    assert "hubDesign" in SAFETY_PANEL_ENUM


def test_ui_open_panel_accepts_hub_design():
    schema = _tool("ui_open_panel")["input_schema"]
    assert _errors(schema, {"panel_id": "hubDesign"}) == []
    assert _errors(schema, {"panel_id": "HubDesign"}) == []


def test_the_validator_is_not_vacuous():
    schema = _tool("ui_open_panel")["input_schema"]
    assert _errors(schema, {"panel_id": "noSuchPanel"}) != []
    assert _errors(schema, {}) != []


def test_ui_open_panel_description_names_hub_design():
    assert "hubDesign" in _tool("ui_open_panel")["description"]


def test_panel_enum_has_no_duplicates():
    assert len(SAFETY_PANEL_ENUM) == len(set(SAFETY_PANEL_ENUM))
