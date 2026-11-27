"""The site-location run gate (TEMPLATE-GAPS-1 Q1 a, template-e2e 01).

A missing site location blocks Run like any other required input. One predicate
decides it, and both the server's start paths (`POST /run`, `/run_ac_pf`, the
reliability study start) and the project flow's `site_location_missing` item
call it, so the Run button, the chat and a direct call agree.

The location is set when a bus is placed (the RP-01 rule, `_bus_coord`) or the
project's site anchor (`site_anchor.json`) is confirmed. A draft anchor does not
count, and an unreadable anchor file fails closed (not set).
"""

from __future__ import annotations

import pathlib

CODE = "site_location_missing"
MESSAGE = "The site has no location: set its latitude and longitude before running."


def placed_buses(n) -> int:
    from services.network_geometry import _bus_coord

    return sum(1 for b in n.buses.index if _bus_coord(n, b) is not None)


def anchor_confirmed(project_dir: pathlib.Path | None) -> bool:
    if project_dir is None:
        return False
    from services.site_scene import anchor
    from services.site_scene.contract import SceneError

    try:
        stored = anchor.read_anchor(project_dir).get("anchor")
    except SceneError:
        return False
    return bool(stored) and stored.get("confirmed") is True


def is_missing(n_buses: int, placed: int, anchored: bool) -> bool:
    """The one predicate. A network with no buses is the validator's own
    `buses_empty`, not a missing location."""
    return n_buses > 0 and placed == 0 and not anchored


def refusal(n, project_dir: pathlib.Path | None) -> dict | None:
    """The typed refusal a run start answers (422), or None. A scratch network
    (no project directory, so no anchor can exist) is not a project and is not
    gated."""
    if project_dir is None:
        return None
    if not is_missing(len(n.buses), placed_buses(n), anchor_confirmed(project_dir)):
        return None
    return {
        "code": CODE,
        "error_kind": "site_location_missing",
        "message": MESSAGE,
        "field": "x",
        "component": "Bus",
    }


def active_refusal() -> dict | None:
    """`refusal` on the foreground network and its project."""
    from services.network_lengths import active_project_dir
    from services.pypsa_service import PyPSAService

    return refusal(PyPSAService.get_network(), active_project_dir())
