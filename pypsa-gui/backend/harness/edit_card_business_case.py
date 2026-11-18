"""The `set_business_case_lines` edit card (business case 08b; GK B (54) D1, D2).

The rows are `services.business_case.chat_edit.plan`'s: a diff against the stored
config, each price with its source. The card shows in Guided and Expert (`is_edit`,
and `confirm.ALWAYS_CARD_TOOLS`), and its digest binds exactly the rows shown, so
`require_current` refuses `edit_preview_stale` when any of them moved.
"""

from __future__ import annotations

from typing import Any

from harness.edit_card import Adapter, register

_SECTION = "commercial"  # with the finance inputs (edit_card_site._SECTION)


def _stored():
    from routers.results import _state

    return getattr(_state["solver_config"], "business_case", None)


def _preview(args: dict) -> list[dict]:
    from harness.edit_card_site import _acting
    from services.business_case import chat_edit

    _acting()
    return chat_edit.plan(args)["rows"]


def _readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.business_case import chat_edit

    stored = _stored()
    return [chat_edit.read_cell(row, stored) for row in rows]


def _says(args: dict, rows: list[dict]) -> str:
    from services.business_case import chat_edit

    return chat_edit.says(rows)


register(
    "set_business_case_lines",
    Adapter("settings_edit", _preview, _readback, section_id=_SECTION, says=_says),
)


# Business case 14: the preset, filled by the server (one row per line, each default with
# its source and year; `services.business_case.preset_apply`).
def _preset_preview(args: dict) -> list[dict]:
    from harness.edit_card_site import _acting
    from services.business_case import preset_apply

    _acting()
    return preset_apply.plan(args)["rows"]


def _preset_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.business_case import preset_apply

    stored = _stored()
    return [preset_apply.read_cell(row, stored) for row in rows]


def _preset_says(args: dict, rows: list[dict]) -> str:
    from services.business_case import preset_apply

    return preset_apply.says(rows)


register(
    "apply_business_case_preset",
    Adapter(
        "settings_edit",
        _preset_preview,
        _preset_readback,
        section_id=_SECTION,
        says=_preset_says,
    ),
)
