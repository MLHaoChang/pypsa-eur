"""LC-10: one ladder for "this", "the" and "latest" (LC-C11).

The first rung that yields exactly one target wins: said explicitly, on
screen now, discussed last, only one exists, otherwise ask. UI context is an
untrusted hint: every rung re-reads the active network or the project's run
records, and the answer always names what it resolved and what it ignored.
Read tier: nothing is activated, selected or opened.
"""
from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException

from services.pypsa_service import PyPSAService

STORAGE = "storage"
STORAGE_CLASSES = ("StorageUnit", "Store")
MAX_CANDIDATES = 12
_KEY_FIELDS = {
    "Bus": ("v_nom", "carrier"),
    "Line": ("bus0", "bus1", "s_nom"),
    "Transformer": ("bus0", "bus1", "s_nom"),
    "Link": ("bus0", "bus1", "p_nom"),
    "Generator": ("bus", "carrier", "p_nom"),
    "StorageUnit": ("bus", "p_nom", "max_hours"),
    "Store": ("bus", "e_nom"),
    "Load": ("bus", "p_set"),
    "ShuntImpedance": ("bus",),
}
_ASK = ("Ask the user with one ask_user Choice listing these candidates plus "
        "'Something else'; never pick one yourself.")


def _refuse(kind: str, message: str) -> None:
    raise HTTPException(422, {"error_kind": kind, "message": message})


def _classes(component_class: str) -> tuple[str, ...]:
    from services.chat_tools import _GENERIC_CRUD_ATTRS

    if component_class == STORAGE:
        return STORAGE_CLASSES
    if component_class not in _GENERIC_CRUD_ATTRS:
        _refuse("invalid_reference", f"Unknown component_class {component_class!r}.")
    return (component_class,)


def _frame(klass: str):
    from services.chat_tools import _GENERIC_CRUD_ATTRS

    return getattr(PyPSAService.get_network(), _GENERIC_CRUD_ATTRS[klass])


def _exists(klass: str, name: Any) -> bool:
    return isinstance(name, str) and name in _frame(klass).index


def _plain(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _candidate(klass: str, name: str) -> dict:
    row = _frame(klass).loc[name]
    out = {"component_class": klass, "name": name}
    for key in _KEY_FIELDS.get(klass, ()):
        if key in row.index:
            out[key] = _plain(row[key])
    return out


def _check_project_access() -> None:
    """A saved project is rechecked under the acting user; an unsaved network
    is the caller's own context."""
    from services import chat_tools, project_registry

    ctx = PyPSAService.get_active_context()
    with chat_tools._acting() as (db, user):
        if ctx.project_uuid:
            project_registry.resolve_project(db, user, ctx.project_uuid)


def _last_touched(session: Any, classes: tuple[str, ...]) -> tuple[str, str] | None:
    """The last component a successful tool call in this conversation named."""
    failed: set[str] = set()
    for message in reversed(list(getattr(session, "messages", ()) or ())):
        blocks = message.get("content") if isinstance(message, dict) else None
        if not isinstance(blocks, list):
            continue
        for block in reversed(blocks):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and block.get("is_error"):
                failed.add(block.get("tool_use_id"))
            if (block.get("type") != "tool_use" or block.get("id") in failed
                    or block.get("name") == "resolve_reference"):
                continue
            args = block.get("input") or {}
            klass, name = args.get("component_class"), args.get("name")
            if klass in classes and isinstance(name, str) and _exists(klass, name):
                return klass, name
    return None


def _component(session: Any, component_class: str | None, name: str | None) -> dict:
    hints = getattr(session, "turn_ui_context", None) or {}
    selected = hints.get("selected_component") if isinstance(hints, dict) else None
    sel = None
    if isinstance(selected, dict) and isinstance(selected.get("class"), str) \
            and isinstance(selected.get("name"), str):
        sel = (selected["class"], selected["name"])
    if component_class is None:
        if name is not None or sel is None:
            _refuse("invalid_reference", "Name a component_class (or 'storage') to resolve against.")
        component_class = sel[0]
    classes = _classes(component_class)
    label = "storage" if component_class == STORAGE else component_class
    ignored: list[str] = []

    def done(klass: str, found: str, via: str, how: str) -> dict:
        return {"version": 1, "status": "resolved", "via": via,
                "target": {"component_class": klass, "name": found},
                "says": f"{klass} {found}, {how}.", "ignored": ignored}

    if name is not None:
        for klass in classes:
            if _exists(klass, name):
                return done(klass, name, "explicit", "as named")
        return {"version": 1, "status": "not_found", "via": "explicit", "ignored": ignored,
                "says": f"No {label} named {name!r} in the active project."}
    if sel is not None:
        if sel[0] not in classes:
            ignored.append(f"The selection is {sel[0]} {sel[1]}, not a {label}.")
        elif not _exists(*sel):
            ignored.append(f"The selected {sel[0]} {sel[1]} is not in the active project.")
        else:
            return done(sel[0], sel[1], "selected", "selected on screen")
    mismatched = bool(ignored) and sel is not None and sel[0] not in classes
    touched = None if mismatched else _last_touched(session, classes)
    if touched:
        return done(*touched, "discussed", "the one discussed last")
    names = [(klass, n) for klass in classes for n in _frame(klass).index]
    if len(names) == 1 and not mismatched:
        return done(*names[0], "only", f"the only {label} in the project")
    if not names:
        return {"version": 1, "status": "not_found", "via": "none", "ignored": ignored,
                "says": f"The active project has no {label}."}
    return {"version": 1, "status": "ambiguous", "via": "ask", "ignored": ignored,
            "candidates": [_candidate(k, n) for k, n in names[:MAX_CANDIDATES]],
            "total_candidates": len(names), "next": _ASK,
            "says": f"{len(names)} {label} candidates; which one is meant is not known."}


def _run(session: Any, run_id: str | None, latest: bool) -> dict:
    from services import run_evidence

    _root, project_id, _owner = run_evidence._authority()
    ignored: list[str] = []

    def done(record: dict, via: str, how: str, skipped: list[dict] | None = None) -> dict:
        out = {"version": 1, "status": "resolved", "via": via, "ignored": ignored,
               "target": {k: record.get(k) for k in ("run_id", "status", "mode", "started_at", "finished_at")},
               "says": f"Run {record['run_id']} ({record.get('status')}), {how}."}
        if skipped is not None:
            out["skipped"] = skipped
        return out

    if run_id is not None:
        return done(run_evidence.get_run_evidence(run_id), "explicit", "as named")
    if not latest:
        hints = getattr(session, "turn_ui_context", None) or {}
        shown = hints.get("results_source") if isinstance(hints, dict) else None
        if isinstance(shown, dict) and shown.get("scope") == "historical" and shown.get("run_id"):
            if shown.get("project_id") != project_id:
                ignored.append("The displayed run belongs to another project.")
            else:
                try:
                    return done(run_evidence.get_run_evidence(str(shown["run_id"])), "displayed",
                                "shown in the results view")
                except HTTPException:
                    ignored.append(f"The displayed run {shown['run_id']} is not recorded.")
        discussed = getattr(session, "discussed_refs", None) or {}
        if discussed.get("run_id"):
            if discussed.get("project_id") != project_id:
                ignored.append("The discussed run belongs to another project.")
            else:
                try:
                    return done(run_evidence.get_run_evidence(discussed["run_id"]), "discussed",
                                "the run discussed last")
                except HTTPException:
                    ignored.append(f"The discussed run {discussed['run_id']} is no longer recorded.")
    skipped: list[dict] = []
    offset = 0
    while True:
        page = run_evidence.list_project_runs(limit=run_evidence.MAX_LIMIT, offset=offset)
        for record in page["items"]:
            if record["status"] == "completed":
                note = ""
                if skipped:
                    note = "; newer runs not used: " + ", ".join(
                        f"{s['run_id']} ({s['status']})" for s in skipped)
                return done(record, "latest_completed", "the newest completed run" + note, skipped)
            skipped.append({"run_id": record["run_id"], "status": record["status"]})
        if not page["has_more"]:
            break
        offset += len(page["items"])
    says = "No completed run in the active project."
    if skipped:
        says += " Not used: " + ", ".join(f"{s['run_id']} ({s['status']})" for s in skipped) + "."
    return {"version": 1, "status": "not_found", "via": "none", "ignored": ignored,
            "skipped": skipped, "says": says}


def resolve_reference(target: str, component_class: str | None = None, name: str | None = None,
                      run_id: str | None = None, latest: bool = False) -> dict:
    from services import chat_tools

    session = chat_tools.chat_session()
    if target == "component":
        if name is not None and (not isinstance(name, str) or not name or len(name) > 256):
            _refuse("invalid_reference", "name must be a nonblank string of at most 256 characters.")
        _check_project_access()
        return _component(session, component_class, name)
    if target == "run":
        if run_id is not None and (not isinstance(run_id, str) or not run_id or len(run_id) > 64):
            _refuse("invalid_reference", "run_id must be a nonblank string of at most 64 characters.")
        return _run(session, run_id, bool(latest))
    _refuse("invalid_reference", "target must be 'component' or 'run'.")
