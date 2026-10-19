"""LC-10: one ladder for "this", "the" and "latest" (LC-C11).

The first rung that yields exactly one target wins: said explicitly, on
screen now, discussed last, only one exists, otherwise ask. UI context is an
untrusted hint: every rung re-reads the active network or the project's run
records, hint text is cleaned and capped before it is echoed, and the answer
always names what it resolved and what it ignored. Read tier: nothing is
activated, selected or opened.
"""
from __future__ import annotations

import math
import re
from typing import Any

from fastapi import HTTPException

from services.pypsa_service import PyPSAService

STORAGE = "storage"
STORAGE_CLASSES = ("StorageUnit", "Store")
MAX_CANDIDATES = 12
MAX_HINT_CHARS = 128
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
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


def _refuse(message: str) -> None:
    raise HTTPException(422, {"error_kind": "invalid_reference", "message": message})


def active_project_key() -> str:
    """What "the same project" means for the discussed-last rung."""
    ctx = PyPSAService.get_active_context()
    return ctx.project_uuid or ctx.loaded_project or ""


def _hint(value: Any) -> str | None:
    """A browser hint as plain bounded text, or None when it is not text."""
    if not isinstance(value, str):
        return None
    value = _CONTROL.sub("", value).strip()
    return value[:MAX_HINT_CHARS] or None


def _hints(session: Any) -> dict:
    hints = getattr(session, "turn_ui_context", None)
    return hints if isinstance(hints, dict) else {}


def _crud_attrs() -> dict[str, str]:
    from services.chat_tools import _GENERIC_CRUD_ATTRS

    return _GENERIC_CRUD_ATTRS


def _classes(component_class: str | None) -> tuple[str, ...]:
    if component_class is None:
        return tuple(_crud_attrs())
    if component_class == STORAGE:
        return STORAGE_CLASSES
    if component_class not in _crud_attrs():
        _refuse(f"Unknown component_class {component_class!r}.")
    return (component_class,)


def _frame(klass: str):
    return getattr(PyPSAService.get_network(), _crud_attrs()[klass])


def _exists(klass: str, name: Any) -> bool:
    return klass in _crud_attrs() and isinstance(name, str) and name in _frame(klass).index


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


def _answer(status: str, via: str, says: str, ignored: list[str], **extra: Any) -> dict:
    return {"version": 1, "status": status, "via": via, "says": says, "ignored": ignored, **extra}


def _check_project_access() -> None:
    """A saved project is rechecked under the acting user; an unsaved network
    is the caller's own context."""
    from services import chat_tools, project_registry

    ctx = PyPSAService.get_active_context()
    with chat_tools._acting() as (db, user):
        if ctx.project_uuid:
            project_registry.resolve_project(db, user, ctx.project_uuid)


def _last_touched(session: Any, classes: tuple[str, ...]) -> tuple[str, str] | None:
    """The last component a successful tool call named in this project."""
    key = active_project_key()
    for project, klass, name in reversed(list(getattr(session, "touched_components", ()) or ())):
        if project == key and klass in classes and _exists(klass, name):
            return klass, name
    return None


def _component(session: Any, component_class: str | None, name: str | None) -> dict:
    selected = _hints(session).get("selected_component")
    sel = None
    if isinstance(selected, dict):
        klass, sel_name = _hint(selected.get("class")), _hint(selected.get("name"))
        sel = (klass, sel_name) if klass and sel_name else None
    if component_class is None and name is None:
        if sel is None:
            _refuse("Name a component_class (or 'storage') to resolve against.")
        component_class = sel[0] if sel[0] in _crud_attrs() else None
        if component_class is None:
            _refuse("Name a component_class (or 'storage') to resolve against.")
    classes = _classes(component_class)
    label = {None: "component", STORAGE: "storage"}.get(component_class, component_class)
    ignored: list[str] = []

    def resolved(klass: str, found: str, via: str, how: str) -> dict:
        return _answer("resolved", via, f"{klass} {found}, {how}.", ignored,
                       target={"component_class": klass, "name": found})

    def ask(names: list[tuple[str, str]]) -> dict:
        return _answer("ambiguous", "ask", f"{len(names)} {label} candidates; which one is meant is not known.",
                       ignored, candidates=[_candidate(k, n) for k, n in names[:MAX_CANDIDATES]],
                       total_candidates=len(names), next=_ASK)

    if name is not None:
        found = [(klass, name) for klass in classes if _exists(klass, name)]
        if len(found) == 1:
            return resolved(*found[0], "explicit", "as named")
        if found:
            return ask(found)
        return _answer("not_found", "explicit", f"No {label} named {name!r} in the active project.", ignored)
    mismatched = False
    if sel is not None:
        if sel[0] not in classes:
            mismatched = True
            ignored.append(f"The selection is {sel[0]} {sel[1]}, not a {label}.")
        elif not _exists(*sel):
            ignored.append(f"The selected {sel[0]} {sel[1]} is not in the active project.")
        else:
            return resolved(sel[0], sel[1], "selected", "selected on screen")
    # A selection of another class means the user is pointing elsewhere, so
    # neither an earlier topic nor "the only one" may stand in for it.
    touched = None if mismatched else _last_touched(session, classes)
    if touched:
        return resolved(*touched, "discussed", "the one discussed last")
    names = [(klass, n) for klass in classes for n in _frame(klass).index]
    if len(names) == 1 and not mismatched:
        return resolved(*names[0], "only", f"the only {label} in the project")
    if not names:
        return _answer("not_found", "none", f"The active project has no {label}.", ignored)
    return ask(names)


def _run_target(record: dict) -> dict:
    return {k: record.get(k) for k in ("run_id", "status", "mode", "started_at", "finished_at")}


def _listed(skipped: list[dict]) -> str:
    return ", ".join(f"{s['run_id']} ({s['status']})" for s in skipped)


def _run(session: Any, run_id: str | None, latest: bool) -> dict:
    from services import run_evidence

    _root, project_id, _owner = run_evidence._authority()
    ignored: list[str] = []

    def resolved(record: dict, via: str, how: str, **extra: Any) -> dict:
        return _answer("resolved", via, f"Run {record['run_id']} ({record.get('status')}), {how}.",
                       ignored, target=_run_target(record), **extra)

    def recorded(candidate: Any, via: str, how: str, what: str) -> dict | None:
        try:
            return resolved(run_evidence.get_run_evidence(candidate), via, how)
        except HTTPException:
            ignored.append(f"The {what} run {_hint(candidate)} is not recorded.")
            return None

    if run_id is not None:
        return resolved(run_evidence.get_run_evidence(run_id), "explicit", "as named")
    if not latest:
        shown = _hints(session).get("results_source")
        if isinstance(shown, dict) and shown.get("scope") == "current":
            source = _hint(shown.get("source")) or "current"
            return _answer("resolved", "displayed",
                           f"The current {source} results of the active network, shown in the results view.",
                           ignored, target={"scope": "current", "source": source})
        if isinstance(shown, dict) and shown.get("scope") == "historical" and _hint(shown.get("run_id")):
            if shown.get("project_id") != project_id:
                ignored.append("The displayed run belongs to another project.")
            elif out := recorded(_hint(shown["run_id"]), "displayed", "shown in the results view", "displayed"):
                return out
        discussed = getattr(session, "discussed_refs", None) or {}
        if discussed.get("run_id"):
            if discussed.get("project_id") != project_id:
                ignored.append("The discussed run belongs to another project.")
            elif out := recorded(discussed["run_id"], "discussed", "the run discussed last", "discussed"):
                return out
    completed, skipped, records, offset = [], [], [], 0
    while True:
        page = run_evidence.list_project_runs(limit=run_evidence.MAX_LIMIT, offset=offset)
        records.extend(page["items"])
        if not page["has_more"]:
            break
        offset += len(page["items"])
    for record in records:
        if record["status"] == "completed":
            completed.append(record)
        elif not completed:
            skipped.append({"run_id": record["run_id"], "status": record["status"]})
    if not completed:
        says = "No completed run in the active project."
        if skipped:
            says += f" Not used: {_listed(skipped)}."
        return _answer("not_found", "none", says, ignored, skipped=skipped)
    note = f"; newer runs not used: {_listed(skipped)}" if skipped else ""
    if latest:
        return resolved(completed[0], "latest_completed", "the newest completed run" + note, skipped=skipped)
    if len(completed) == 1:
        return resolved(completed[0], "only", "the only completed run" + note, skipped=skipped)
    return _answer("ambiguous", "ask", f"{len(completed)} completed runs; which one is meant is not known.",
                   ignored, skipped=skipped, total_candidates=len(completed), next=_ASK,
                   candidates=[_run_target(r) for r in completed[:MAX_CANDIDATES]])


def _snapshot(session: Any) -> dict:
    index = _hints(session).get("snapshot_index")
    snapshots = PyPSAService.get_network().snapshots
    if type(index) is int and 0 <= index < len(snapshots):
        return _answer("resolved", "selected", f"Snapshot {index} ({snapshots[index]}), shown on screen.", [],
                       target={"snapshot_index": index, "snapshot": str(snapshots[index])})
    ignored = [] if index is None else ["The displayed snapshot is not in the active network."]
    return _answer("not_found", "none", "No snapshot is shown on screen; ask which hour is meant.", ignored)


def resolve_reference(target: str, component_class: str | None = None, name: str | None = None,
                      run_id: str | None = None, latest: bool = False) -> dict:
    from services import chat_tools

    session = chat_tools.chat_session()
    if target == "component":
        if name is not None and (not isinstance(name, str) or not name or len(name) > 256):
            _refuse("name must be a nonblank string of at most 256 characters.")
        _check_project_access()
        return _component(session, component_class, name)
    if target == "run":
        if run_id is not None and (not isinstance(run_id, str) or not run_id or len(run_id) > 64):
            _refuse("run_id must be a nonblank string of at most 64 characters.")
        return _run(session, run_id, bool(latest))
    if target == "snapshot":
        _check_project_access()
        return _snapshot(session)
    _refuse("target must be 'component', 'run' or 'snapshot'.")
