"""
The chat tool catalogue's shared builders and route sentinels (RETRO-FRAG-1).

Split out of `harness/catalogue.py` so a per-tool fragment under
`harness/catalogue_tools/` builds its entries without importing the catalogue
itself (which imports every fragment). `harness/catalogue.py` re-exports every
name here. A fragment that needs one of the catalogue's shared enums moves it
here in the same PR.
"""

from __future__ import annotations

from typing import Any


def _t(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Factory: one tool entry in Anthropic format."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


def _empty(name: str, description: str) -> dict[str, Any]:
    return _t(name, description, {}, [])


# LC-19: the re-run rule the solve tools and the load flow share (harness/run_lifecycle.py).
_RERUN_NOTE = (
    "When the current project's results already match its inputs, the call is refused with "
    "results_current before any card: tell the user and ask; only on their yes call it again "
    "with rerun_current: true. "
)


# TOOL_ROUTES sentinels for tools with no HTTP route (see catalogue.NON_HTTP_SENTINELS).
_SERVICE_CALL = ["_service_call_"]
_DERIVED = ["_derived_"]
_UI_EVENT = ["_ui_event_"]
_CHAT_JSONL = ["_chat_jsonl_"]
