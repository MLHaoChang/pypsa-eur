"""
Energization (template-e2e 02): set_energization_period: schema and route.

The chat side of Guided section 3's per-period connection drawer. One tool, one
route, `PUT /api/projects/{name}/flow/energization`; its handler is the only
writer (`energization.set_period_mw`), so chat and the drawer share its
validation, refusals and write rules.
Dispatchers: `services/chat_dispatchers/energization.py`.

The route is under `/api/projects/`, not a `NETWORK_EDIT_PREFIXES` prefix, and
the handler takes its own undo step, edit counter bump and unsaved mark (that
prefix has no middleware), so the tool declares no NETWORK_WRITES: declaring it
would add a second bump and a second undo step to every call.
"""

from __future__ import annotations

from harness.catalogue_base import _t

ORDER = 2026112801

TOOLS = [
    _t(
        "set_energization_period",
        "Set the grid connection's energized capacity (MW) for ONE period of "
        "the active project's energization schedule, flat across that "
        "period's snapshots: the same edit as Guided section 3's connection "
        "drawer. `link` is the connection Link's name; `period` is a model "
        "year on a multi-period network, or \"all\" (or null) on a "
        "single-period one; `mw` is a number above 0. Read the current values "
        "first with get_project_flow: the connection row's `energization` "
        "lists each Link and its `by_period` MW. One undo step per call; the "
        "network is marked unsaved and solved dispatch is cleared. Refused, "
        "nothing written: energization_invalid_request (shape), "
        "energization_invalid_mw (not a number, not finite, or not above 0), "
        "energization_no_schedule (the Link has none; this tool never creates "
        "one), energization_unknown_period, energization_period_not_flat (the "
        "period's MW varies within it; its range is named and it is never "
        "scaled; tell the user to set it on the network). An MW above the "
        "Link's rating is accepted, and the answer's `note` says it does not "
        "raise the Link's capacity: relay that. Safety: write.",
        {
            "name": {"type": "string", "description": "The project's name (the open project)."},
            "link": {"type": "string", "description": "The connection Link's name."},
            "period": {
                "type": ["string", "integer", "null"],
                "description": "A model year, or \"all\" / null on a single-period network.",
            },
            "mw": {"type": "number", "description": "The energized MW for that period, above 0."},
        },
        ["name", "link", "mw"],
    ),
]

ROUTES = {
    "set_energization_period": [("PUT", "/api/projects/{name}/flow/energization")],
}
