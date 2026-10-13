"""
Shared checks for tool ACTIONS a read tool hands the assistant — an existing
tool name plus the exact arguments to call it with (guided-mode spec §6.3).

Lifted from ``test_energy_hub_review.py`` so the review findings and the
``suggest_eh_setup`` suggestions are validated by one function: an action
the assistant would run must be one the tool surface accepts unchanged.
"""
from __future__ import annotations

from services.chat_tools_schema import TOOLS


def _validate_action(action: dict) -> None:
    """The named tool exists and accepts these args unchanged."""
    tool = next((t for t in TOOLS if t["name"] == action["tool"]), None)
    assert tool is not None, action
    schema = tool["input_schema"]
    assert set(action["args"]) <= set(schema["properties"]), action
    assert set(schema.get("required", [])) <= set(action["args"]), action
    for key, spec in schema["properties"].items():
        if key in action["args"] and "enum" in spec:
            assert action["args"][key] in spec["enum"], (key, action)
    if action["tool"] == "run_eh_study":
        from services.adequacy.eh_study import validate_stages
        from services.adequacy.eh_study_runner import (
            _PACK_FACTORY,
            EhStudyRequest,
            McOptions,
            apply_pack_overrides,
            resolve_dtc_attribution,
        )
        body = EhStudyRequest.model_validate(action["args"])
        apply_pack_overrides(_PACK_FACTORY[body.archetype](), body.pack_overrides,
                             raise_http=True)
        McOptions.model_validate(body.mc or {})
        resolve_dtc_attribution(body.dtc_config, body.dtc_attribution)
        if body.stages is not None:
            validate_stages(body.stages)


def _validate_eh_tag_action(action: dict) -> None:
    """An ``update_component`` / ``bulk_update_components`` action that
    writes Energy Hub tags: valid for its tool, every written key is an
    ``EH_CUSTOM_COLUMNS`` column of that class, and every value is already
    the stored value of its declared kind (``coerce_eh_value`` is identity)."""
    from models.energy_hub import EH_CUSTOM_COLUMNS
    from services.adequacy.eh_columns import coerce_eh_value

    _validate_action(action)
    args = action["args"]
    assert action["tool"] in ("update_component", "bulk_update_components"), action
    writes = args["attrs"] if action["tool"] == "update_component" else args["updates"]
    cls = args["component_class"]
    assert writes and set(writes) <= set(EH_CUSTOM_COLUMNS[cls]), action
    for col, value in writes.items():
        stored = coerce_eh_value(cls, col, value)
        assert stored == value and type(stored) is type(value), (col, value)
    if action["tool"] == "bulk_update_components":
        assert len(args["names"]) >= 2 and len(set(args["names"])) == len(args["names"])
