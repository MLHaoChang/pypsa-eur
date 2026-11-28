"""
Expert catalogue (expert-catalogue issue 05, slice 01b): find_parameter and
open_parameter: schemas and routes.

The chat side of the parameter catalogue (spec §7.1). `find_parameter` is the
chat counterpart of `GET /api/catalogue` and the ⌘K palette: the same entries
and ranking, plus the active project's assets that carry each one.
`open_parameter` emits the `parameter` navigate kind `uiNavigate.ts` opens
through `openParameter()` (the Expert panel or, in Guided, the field drawer;
it never switches mode). Neither writes anything.
Dispatchers: `services/chat_dispatchers/expert_catalogue.py`.
"""

from __future__ import annotations

from harness.catalogue_base import _UI_EVENT, _t

ORDER = 2026112805

TOOLS = [
    _t(
        "find_parameter",
        "Find parameters in the parameter catalogue by what the user calls "
        "them (\"transformer tap\", \"running cost\", \"battery size\"): up "
        "to 8 entries, best first, each with entry_id, plain_name, "
        "model_name, asset_type, group, unit, short_help, guide (the "
        "parameter guide's text, quote it to explain the parameter) and the "
        "active project's assets that carry it: `assets` [{name, value, "
        "assumed}] (at most 10; `assumed` is true when the value was left "
        "at its default), `assets_total` and `assets_truncated`. Narrow with "
        "asset_type (an asset type id, e.g. pvRoof, bess) and/or function "
        "(dispatch, expansion, load_flow, short_circuit, grid_compliance, adequacy or finance). Use it to answer where a parameter is set, "
        "then open_parameter to take the user there. Talk to the user in "
        "plain names, never model names. Refused: invalid_parameter_filter "
        "(an unknown asset_type or function). Safety: read.",
        {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "The parameter in the user's words.",
            },
            "asset_type": {
                "type": "string",
                "description": "Only entries of this asset type (its id).",
            },
            "function": {
                "type": "string",
                "description": "Only entries this function reads (its id).",
            },
        },
        ["query"],
    ),
    _t(
        "open_parameter",
        "Take the user to one parameter's field: emits a ui_event "
        "(kind=parameter) that opens it in the Expert panel or, in Guided, "
        "in the field drawer over the current card; it never switches mode "
        "and changes nothing. entry_id is find_parameter's entry_id; asset "
        "names the component to open it on (without it the user picks one). "
        "An entry_id from before a rename opens the current entry and the "
        "result names it (`entry_id`, `renamed_from`). Refused, no "
        "navigation: unknown_parameter (no entry has that id: call "
        "find_parameter), component_not_found (no such asset of that "
        "parameter's class in the open project). Safety: read.",
        {
            "entry_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "asset": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "description": "The asset (component name) to open it on.",
            },
        },
        ["entry_id"],
    ),
]

ROUTES = {
    "find_parameter": [("GET", "/api/catalogue")],
    "open_parameter": _UI_EVENT,
}
