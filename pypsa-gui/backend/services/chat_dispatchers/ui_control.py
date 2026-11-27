"""
UI control: ui_select_component and ui_open_panel: dispatchers.

Moved out of `services/chat_tools.py` (RETRO-FRAG-1), function bodies
unchanged. Schemas and routes: `harness/catalogue_tools/ui_control.py`.

The chat_tools helpers stay in chat_tools and are reached through the
proxies below, at call time: chat_tools imports this module part-way through
its own import, and tests that patch `chat_tools.<helper>` still apply.
The helpers and tables only this family uses moved here with it; chat_tools
re-exports them, which is a binding, so a later monkeypatch of
`chat_tools.<moved name>` does not reach this module: patch it here.

"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services.chat_dispatchers.adequacy_studies import _CtTable
from services.pypsa_service import PyPSAService


def _ct():
    from services import chat_tools

    return chat_tools


def _acting(*args, **kwargs):
    return _ct()._acting(*args, **kwargs)


_GENERIC_CRUD_ATTRS = _CtTable("_GENERIC_CRUD_ATTRS")


def ui_select_component(
    component_class: str, name: str, open_editor: bool | None = None
) -> dict:
    if open_editor is not None and type(open_editor) is not bool:
        raise HTTPException(
            400,
            detail={
                "error_kind": "invalid_navigation",
                "field": "open_editor",
                "message": "Use a boolean for open_editor.",
            },
        )
    if open_editor:
        if component_class not in {
            "Bus",
            "Line",
            "Transformer",
            "Link",
            "Generator",
            "StorageUnit",
            "Store",
            "Load",
        }:
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": "component_class",
                    "message": "Choose a component with an Edit form in Properties.",
                },
            )
        attr = _GENERIC_CRUD_ATTRS[component_class]
        df = getattr(PyPSAService.get_network(), attr)
        if name not in df.index:
            raise HTTPException(
                404,
                detail={
                    "error_kind": "component_not_found",
                    "component_class": component_class,
                    "name": name,
                    "message": f"{component_class} '{name}' is not in the network.",
                },
            )
        return {
            "_ui_event": True,
            "kind": "open_properties",
            "component_class": component_class,
            "name": name,
        }
    from services.asset_results import compute as asset_compute

    try:
        df = getattr(
            PyPSAService.get_network(), asset_compute.attr_for(component_class)
        )
    except KeyError:
        df = None
    if df is None or name not in df.index:
        raise HTTPException(
            404,
            detail={
                "error_kind": "component_not_found",
                "component_class": component_class,
                "name": name,
                "message": f"{component_class} '{name}' is not in the network.",
            },
        )
    return {
        "_ui_event": True,
        "kind": "select_component",
        "component_class": component_class,
        "name": name,
    }


# LC-14 item 5: the sections of each panel chat can scroll to, and the title the user sees for it.
# Keyed by the lower-camel panel id; the catalogue's PANEL_SECTION_ENUM is the union (a test pins it).
_PANEL_SECTIONS: dict[str, tuple[str, frozenset[str]]] = {
    "campusElectrical": (
        "the grid connection",
        frozenset(
            {
                "campus_file",
                "grid_connection",
                "connection_limits",
                "grid_codes",
                "library",
            }
        ),
    ),
    "campusStudy": ("the grid study", frozenset({"study_settings", "island"})),
    "campusResults": (
        "the grid study results",
        frozenset({"grid_code_results", "electrical_design"}),
    ),
    "gridspine": (
        "Planning → dynamics",
        frozenset(
            {
                "dispatch_source",
                "configuration",
                "ranked_snapshots",
                "assumptions_ledger",
                "readback",
                "connection_capacity",
                "connection_assessment",
            }
        ),
    ),
}


_PANEL_SECTION_ORDER = [
    "campus_file",
    "grid_connection",
    "connection_limits",
    "grid_codes",
    "library",
    "study_settings",
    "island",
    "grid_code_results",
    "electrical_design",
    "dispatch_source",
    "configuration",
    "ranked_snapshots",
    "assumptions_ledger",
    "readback",
    "connection_capacity",
    "connection_assessment",
]


def ui_open_panel(
    panel_id: str,
    results_tab: str | None = None,
    bottom_tab: str | None = None,
    compare_rail: bool | None = None,
    compare_a: str | None = None,
    compare_b: str | None = None,
    compare_tab: str | None = None,
    canvas_view: str | None = None,
    guided_step: str | None = None,
    panel_section: str | None = None,
    loadflow_source: str | None = None,
    window_from: int | None = None,
    window_to: int | None = None,
    highlight_branch: dict | None = None,
    highlight_projects: list[str] | None = None,
) -> dict:
    """
    Navigate the GUI. Emits a ``ui_event`` SSE frame (kind=navigate) that
    ChatPanel applies to uiStore — slide panels, Results sub-tabs, bottom
    asset tabs, and the A|B compare rail.

    Load flow arguments (live-voice 07) need panel results and results_tab
    loadflow. With any of them the view is read first: an unavailable view or
    an unknown branch refuses with no event. Without them the call is unchanged.

    `highlight_projects` (LC-17, Scenarios only): up to four projects the
    caller may open, highlighted in the scenario tree; any other refuses.
    """
    from harness.catalogue import (
        BOTTOM_TAB_ENUM,
        COMPARE_TAB_ENUM,
        RESULTS_TAB_ENUM,
        SAFETY_PANEL_ENUM,
    )

    for key, value, allowed in (
        ("panel_id", panel_id, SAFETY_PANEL_ENUM),
        ("results_tab", results_tab, RESULTS_TAB_ENUM),
        ("bottom_tab", bottom_tab, BOTTOM_TAB_ENUM),
        ("compare_tab", compare_tab, COMPARE_TAB_ENUM),
    ):
        if (key == "panel_id" or value is not None) and (
            not isinstance(value, str) or value not in allowed
        ):
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": key,
                    "message": f"Choose a supported {key} from the tool schema.",
                },
            )
    for key, value, valid in (
        ("compare_rail", compare_rail, type(compare_rail) is bool),
        ("compare_a", compare_a, isinstance(compare_a, str)),
        ("compare_b", compare_b, isinstance(compare_b, str)),
    ):
        if value is not None and not valid:
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": key,
                    "message": f"Use the declared {key} type from the tool schema.",
                },
            )
    if canvas_view is not None and not (
        (panel_id in {"Topology", "topology"} and canvas_view == "blank")
        or (
            panel_id in {"MapCanvas", "map"}
            and canvas_view in ("satellite", "hybrid", "site")
        )
    ):
        raise HTTPException(
            400,
            detail={
                "error_kind": "invalid_navigation",
                "field": "canvas_view",
                "message": "Choose blank for topology, or satellite, hybrid or site for map.",
            },
        )
    if guided_step is not None:
        from harness import workflows

        steps = [step.id for step in workflows.get("hub-design").steps]
        if (
            panel_id not in {"HubDesign", "hubDesign"}
            or not isinstance(guided_step, str)
            or guided_step not in steps
        ):
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": "guided_step",
                    "message": f"Choose a Guided card: {', '.join(steps[:-1])} or {steps[-1]}. "
                    "Cards exist only in the hub design.",
                },
            )
    if panel_section is not None:
        by_name = {
            k.lower(): v for k, v in _PANEL_SECTIONS.items()
        }  # CampusStudy, GridSpine: either spelling
        title, valid = by_name.get(panel_id.lower(), (None, frozenset()))
        if title is None:
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": "panel_section",
                    "message": "Sections exist only in the grid connection, the grid study, the grid study "
                    "results and Planning → dynamics.",
                },
            )
        if not isinstance(panel_section, str) or panel_section not in valid:
            ids = [s for s in _PANEL_SECTION_ORDER if s in valid]
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": "panel_section",
                    "message": f"Choose a section of {title}: {', '.join(ids[:-1])} or {ids[-1]}."
                    if len(ids) > 1
                    else f"Choose a section of {title}: {ids[0]}.",
                },
            )
    if highlight_projects is not None:
        if (
            panel_id not in {"Scenarios", "scenarios"}
            or not isinstance(highlight_projects, list)
            or not 1 <= len(highlight_projects) <= 4
            or not all(isinstance(p, str) for p in highlight_projects)
        ):
            raise HTTPException(
                400,
                detail={
                    "error_kind": "invalid_navigation",
                    "field": "highlight_projects",
                    "message": "Pass 1–4 project ids with panel_id scenarios.",
                },
            )
        from services import project_registry

        with _acting() as (
            db,
            user,
        ):  # an id the caller cannot open refuses like an unknown one
            highlight_projects = [
                str(project_registry.resolve_project(db, user, p).id)
                for p in highlight_projects
            ]
    loadflow = _loadflow_navigation_args(
        panel_id, results_tab, loadflow_source, window_from, window_to, highlight_branch
    )
    facts = None
    if loadflow:
        from harness.navigation import loadflow_target

        _view, facts = loadflow_target(
            loadflow.get("loadflow_source"),
            loadflow.get("window_from"),
            loadflow.get("window_to"),
            loadflow.get("highlight"),
        )
    event: dict[str, Any] = {
        "_ui_event": True,
        "kind": "navigate",
        "panel_id": panel_id,
    }
    if results_tab:
        event["results_tab"] = results_tab
    event.update(loadflow)
    if facts is not None:
        event["highlight_facts"] = facts
    if bottom_tab:
        event["bottom_tab"] = bottom_tab
    if compare_rail is not None:
        event["compare_rail"] = bool(compare_rail)
    if compare_a:
        event["compare_a"] = compare_a
    if compare_b:
        event["compare_b"] = compare_b
    if compare_tab:
        event["compare_tab"] = compare_tab
    if canvas_view is not None:
        event["canvas_view"] = canvas_view
    if guided_step is not None:
        event["guided_step"] = guided_step
    if panel_section is not None:
        event["panel_section"] = panel_section
    if highlight_projects:
        event["highlight_projects"] = highlight_projects
    return event


def _loadflow_navigation_args(
    panel_id, results_tab, source, window_from, window_to, highlight
) -> dict:
    """Validate the optional Load flow navigation arguments (shape only)."""

    def refuse(field: str, message: str) -> None:
        raise HTTPException(
            400,
            detail={
                "error_kind": "invalid_navigation",
                "field": field,
                "message": message,
            },
        )

    given = {
        k: v
        for k, v in (
            ("loadflow_source", source),
            ("window_from", window_from),
            ("window_to", window_to),
            ("highlight", highlight),
        )
        if v is not None
    }
    if not given:
        return {}
    if panel_id != "results" or results_tab != "loadflow":
        refuse(
            next(iter(given)),
            "Load flow arguments need panel_id results and results_tab loadflow.",
        )
    if source is not None and source not in ("lopf", "ac_pf"):
        refuse("loadflow_source", "Choose lopf or ac_pf.")
    if (window_from is None) != (window_to is None):
        refuse("window_from", "Give window_from and window_to together, or neither.")
    if window_from is not None:
        if type(window_from) is not int or type(window_to) is not int:
            refuse("window_from", "Window positions are integer snapshot indexes.")
        if window_from < 0 or window_to < window_from:
            refuse("window_from", "Use 0 <= window_from <= window_to.")
        network = PyPSAService.get_active_context().network
        n_snapshots = len(network.snapshots) if network is not None else 0
        # No network: the view read below refuses with no_network instead.
        if network is not None and not 0 <= window_from <= window_to < n_snapshots:
            refuse("window_from", f"Use 0 <= window_from <= window_to < {n_snapshots}.")
    if highlight is not None:
        if (
            not isinstance(highlight, dict)
            or set(highlight) != {"component", "name"}
            or highlight["component"] not in ("Line", "Transformer")
            or not isinstance(highlight["name"], str)
            or not 1 <= len(highlight["name"]) <= 128
        ):
            refuse(
                "highlight_branch",
                "highlight_branch is {component: Line or Transformer, name: 1-128 characters}.",
            )
        given["highlight"] = {
            "component": highlight["component"],
            "name": highlight["name"],
        }
    return given


DISPATCHERS = {
    "ui_select_component": ui_select_component,
    "ui_open_panel": ui_open_panel,
}
