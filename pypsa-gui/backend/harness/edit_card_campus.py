"""Old → new edit cards for the study and campus settings tools (Q3-CARDS-1, slice 2, group `campus`).

`gridspine_update_config`, `gridspine_set_dispatch_source`, `gridspine_edit_template_param`,
`campus_set_island_config`, `campus_set_library` and `campus_draft_campus`.

Each preview resolves the project the way its tool does (access first, and the edit lock for the
tools that check it), reads the old value from the store the tool writes, and takes the new value
from the service's own `planned_*` / `checked_*` function, the one the write calls. A preview
creates nothing: the readers here use the `_if_any` paths, not the ones that mkdir.
"""
from __future__ import annotations

import json
import pathlib
from contextlib import contextmanager
from typing import Any, Callable

import yaml
from fastapi import HTTPException

from harness.edit_card import (
    _SECRET_KEY,
    STUDY_LEAF_CAP,
    Adapter,
    _note,
    _refuse,
    _same,
    _setting,
    _settings_row,
    register,
)


def _title(key: str) -> str:
    return key.replace("_", " ").capitalize()


# ── shared: the handler's refusals, the project, cells ─────────────────────

@contextmanager
def _as_card_refusal():
    """A service refusal becomes the card's refusal, with the status it had. Not for access: the
    project's own access refusal comes from `_with_project`, before anything is read."""
    try:
        yield
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("error_kind"):
            raise
        text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        _refuse("invalid_edit_value" if exc.status_code in (413, 422) else "edit_refused", text, exc.status_code)


def _project_id(args: dict) -> str:
    project_id = args.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        _refuse("empty_edit", "Name the project to edit.")
    return project_id


def _with_project(args: dict, run: Callable[[Any, Any, Any], Any], *, lock: str | None = None):
    """`run(project, db, user)` on the project the call names, resolved under the acting user.
    `lock`: "campus" checks the edit lock as the island tools do, "write" as the older campus
    write tools do; None (a read, or a tool the lock does not gate) checks access only."""
    from services import chat_tools

    project_id = _project_id(args)
    with chat_tools._acting() as (db, user):
        if lock == "write":
            project = chat_tools._gridspine_project_for_write(db, user, project_id)
        else:
            project = chat_tools._gridspine_project(db, user, project_id)
            if lock == "campus":
                chat_tools._campus_check_lock(db, user, project)
        return run(project, db, user)


def _cell(field: str, value):
    """A value as the card shows it: a secret-like key only says whether it is set; a short
    list of scalars reads as its members, any other list or object is summarised."""
    if any(word in field.lower() for word in _SECRET_KEY):
        return None if value is None else "set"
    if isinstance(value, list):
        if len(value) <= 6 and all(not isinstance(v, (list, dict)) for v in value):
            return ", ".join(str(v) for v in value) if value else "none"
        return f"{len(value)} items"
    if isinstance(value, dict):
        return f"{len(value)} fields"
    return _setting(value)


def _on_off(row: dict) -> dict:
    if isinstance(row["old"], bool) or isinstance(row["new"], bool):
        row["words"] = {"old": "not set" if row["old"] is None else "On" if row["old"] else "Off",
                        "new": "not set" if row["new"] is None else "On" if row["new"] else "Off"}
    return row


def _differs(old, new) -> bool:
    return not _same(old, new)


def _capped(rows: list[dict], what: str) -> list[dict]:
    """At most STUDY_LEAF_CAP rows, then one note for the rest."""
    if len(rows) <= STUDY_LEAF_CAP:
        return rows
    return rows[:STUDY_LEAF_CAP] + [_note("Settings", what, "more", f"{len(rows) - STUDY_LEAF_CAP} more changes.")]


# ── study config: gridspine_update_config and gridspine_set_dispatch_source ──

_CONFIG_NAME = "Study settings"
# The planning panel's labels and units (GridspinePanel.tsx NUMERIC_FIELDS and the screen checkbox).
_CONFIG_WORDS = {
    "hours": ("Hours", ""),
    "k": ("k (hours per criterion)", ""),
    "window": ("UC window", "h"),
    "overlap": ("UC overlap", "h"),
    "screen": ("Screen N-1 / N-2 at the selected hours", ""),
    "n2_prune_threshold_pct": ("N-2 prune threshold", "%"),
}
_SOURCE_FIELDS = ("from_dispatch", "from_network", "from_external", "from_external_loads")


def _source_words(db, project, config: dict) -> str:
    """The dispatch source in the planning panel's words (GridspinePanel.tsx `describeSource`)."""
    from services import gridspine_service as gs

    if config.get("from_network"):
        name = gs._project_for_network(db, project, config["from_network"])
        return f"A solved project's network: {name or config['from_network']}"
    if config.get("from_dispatch"):
        return f"A finished study's directory: {config['from_dispatch']}"
    if config.get("from_external"):
        return f"Your own tables: {pathlib.Path(config['from_external']).name}"
    return "Generate here (unit commitment)"


def _source_row(db, project, before: dict, after: dict) -> dict:
    return _settings_row(_CONFIG_NAME, "dispatch_source", "Dispatch source", _source_words(db, project, before),
                         _source_words(db, project, after), says_name="Dispatch source")


def _source_unchanged(before: dict, after: dict) -> bool:
    return all(_same(before.get(k), after.get(k)) for k in _SOURCE_FIELDS)


def _config_preview(args: dict) -> list[dict]:
    from services import gridspine_service as gs

    patch = {k: v for k, v in args.items() if k != "project_id" and v is not None}
    if not patch:
        _refuse("empty_edit", "Name a study setting to change.")

    def run(project, db, user):
        with _as_card_refusal():
            old, new = gs.planned_config_update(project, patch, db=db, user=user)
        before, after = old.to_json(), new.to_json()
        rows = []
        for key in patch:
            if key == "from_dispatch":
                continue
            label, unit = _CONFIG_WORDS[key]
            rows.append(_on_off(_settings_row(_CONFIG_NAME, key, label, before.get(key), after.get(key), unit,
                                              says_name=label)))
        if "from_dispatch" in patch or not _source_unchanged(before, after):
            rows.append(_source_row(db, project, before, after))
        if all(_same(r["old"], r["new"]) for r in rows) and _source_unchanged(before, after):
            _refuse("empty_edit", "The study already has these settings.")
        return rows

    return _with_project(args, run)


def _config_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import gridspine_service as gs

    def run(project, db, user):
        stored = gs.stored_config(project).to_json()
        return [_source_words(db, project, stored) if row["field"] == "dispatch_source" else _setting(stored.get(row["field"]))
                for row in rows]

    return _with_project(args, run)


register("gridspine_update_config", Adapter("settings_edit", _config_preview, _config_readback, section_id="project"))


def _dispatch_preview(args: dict) -> list[dict]:
    from services import chat_tools
    from services import gridspine_service as gs

    source = chat_tools.dispatch_source_arg(args.get("from_dispatch"), args.get("from_project"))

    def run(project, db, user):
        with _as_card_refusal():
            old, new = gs.planned_dispatch_source(db, project, source, user)
        before, after = old.to_json(), new.to_json()
        if _source_unchanged(before, after):
            _refuse("empty_edit", "The study already uses this dispatch source.")
        return [_source_row(db, project, before, after)]

    return _with_project(args, run)


register("gridspine_set_dispatch_source", Adapter("settings_edit", _dispatch_preview, _config_readback,
                                                  section_id="project"))


# ── gridspine_edit_template_param: the project's template overlay ──────────

def _template_preview(args: dict) -> list[dict]:
    from services import gridspine_service as gs

    unit_id, param = args.get("unit_id"), args.get("param")
    if not isinstance(unit_id, str) or not isinstance(param, str):
        _refuse("empty_edit", "Name the unit and the parameter to edit.")

    def run(project, db, user):
        with _as_card_refusal():
            _, entry = gs.planned_template_edit(project, unit_id, param, args.get("value"), args.get("source"), "chat")
            old = gs.template_param(project, unit_id, param)
        old_value, old_source = old if old else (None, None)
        if _same(old_value, entry["value"]) and old_source == entry["source"]:
            _refuse("empty_edit", f"{unit_id} · {param} already has this value and source.")
        label = f"{unit_id} · {param}"
        return [_settings_row("Unit templates", f"{unit_id}.{param}", label, old_value, entry["value"],
                              says_name=label),
                _settings_row("Unit templates", f"{unit_id}.{param}.source", f"{label} source", old_source,
                              entry["source"], says_name=f"{label} source")]

    return _with_project(args, run)


def _template_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import gridspine_service as gs

    unit_id, param = args.get("unit_id"), args.get("param")

    def run(project, db, user):
        stored = gs.template_param(project, unit_id, param) or (None, None)
        return [stored[1] if row["field"].endswith(".source") else stored[0] for row in rows]

    return _with_project(args, run)


register("gridspine_edit_template_param", Adapter("settings_edit", _template_preview, _template_readback,
                                                  section_id="project"))


# ── whole-document payloads: the island sidecar and the equipment library ──
# Both show only what differs, one row per changed value in the document's own words; a member
# (an island unit, a library entry) added or removed is one row, never a row per value.

def _walk(path: list[str], value, flat: dict, paths: dict) -> None:
    if isinstance(value, dict) and value:
        for key, inner in value.items():
            _walk([*path, str(key)], inner, flat, paths)
    else:
        field = ".".join(path)
        flat[field], paths[field] = value, path


def _flatten(doc, groups: dict[str, str]):
    """`(values by field, path by field, members)` of a document. `groups` names the top-level
    keys that hold named members: "dict" (a mapping by name) or "list" (entries with an `id`).
    `members` is `{field: kind}` for each; a member's own values are walked beneath it."""
    flat, paths, members = {}, {}, {}
    for key, value in (doc or {}).items():
        shape = groups.get(key)
        if shape == "dict" and isinstance(value, dict):
            items = [(str(name), spec) for name, spec in value.items()]
        elif shape == "list" and isinstance(value, list):
            items = [(str(e.get("id")), e) for e in value if isinstance(e, dict)]
        else:
            _walk([key], value, flat, paths)
            continue
        for name, spec in items:
            members[f"{key}.{name}"] = (spec or {}).get("kind", "entry") if isinstance(spec, dict) else "entry"
            _walk([key, name], {k: v for k, v in spec.items() if k != "id"} if isinstance(spec, dict) else spec,
                  flat, paths)
    return flat, paths, members


def _document_rows(name: str, before_doc, after_doc, groups: dict[str, str], label: Callable[[list[str]], tuple[str, str]],
                   member_label: Callable[[str], str], member_words: tuple[str, str]) -> list[dict]:
    """The rows for what differs between two documents: a member present on one side only is
    one row; a member on both sides has one row per changed value."""
    b, bp, bm = _flatten(before_doc, groups)
    a, ap, am = _flatten(after_doc, groups)
    rows = []
    for member in dict.fromkeys([*bm, *am]):
        if (member in bm) != (member in am):
            old, new = bm.get(member), am.get(member)
            row = _settings_row(name, member, member_label(member), old, new, says_name=member_label(member))
            row["member"] = True  # the row says the member is there or not; the readback reads it that way
            row["words"] = {"old": member_words[0] if old is None else "in the document",
                            "new": member_words[1] if new is None else "added"}
            rows.append(row)
    changed_members = {m for m in {*bm, *am} if (m in bm) != (m in am)}

    def under_changed(field: str) -> bool:
        return any(field.startswith(m + ".") for m in changed_members)

    for field in dict.fromkeys([*b, *a]):
        if under_changed(field) or not _differs(b.get(field), a.get(field)):
            continue
        text, unit = label(ap.get(field) or bp[field])
        old, new = _cell(field, b.get(field)), _cell(field, a.get(field))
        if old == new:
            new = f"{new} (changed)"
        row = _settings_row(name, field, text, old, new, unit, says_name=text)
        if isinstance(b.get(field), bool) or isinstance(a.get(field), bool):
            row["words"] = {"old": "not set" if b.get(field) is None else "On" if b.get(field) else "Off",
                            "new": "not set" if a.get(field) is None else "On" if a.get(field) else "Off"}
        rows.append(row)
    return rows


def _document_readback(name: str, doc, groups: dict[str, str], rows: list[dict]) -> list[Any]:
    flat, _, members = _flatten(doc, groups)
    return [None if row["role"] == "note" else
            members.get(row["field"]) if row.get("member") else
            _cell(row["field"], flat.get(row["field"])) for row in rows]


# campus_set_island_config: the island sidecar (island_config.json).

_ISLAND_GROUPS = {"units": "dict"}
_ISLAND_NAME = "Island data"
# CampusIslandSection.tsx: the requirements the form edits, and their units.
_ISLAND_WORDS = {
    "sustained_h": ("Sustained ride-through", "h"),
    "bridge_s": ("UPS bridge", "s"),
    "fuel_autonomy_h": ("Fuel autonomy", "h"),
    "genset_redundancy_n": ("Genset redundancy (N+k)", ""),
    "scenarios": ("Scenarios", ""),
    "linearised_uc": ("Linearised unit commitment for the fixed-size gensets", ""),
}
_SUFFIX = {"source": " source", "note": " note"}


def _island_label(path: list[str]) -> tuple[str, str]:
    tail = _SUFFIX.get(path[-1]) if len(path) > 1 else None
    core = path[:-1] if (tail or (path[-1] == "value" and len(path) > 1)) else path
    suffix = tail or ""
    if core == ["preset"]:
        return "Control preset" + suffix, ""
    if core[0] == "requirements" and len(core) == 2:
        text, unit = _ISLAND_WORDS.get(core[1], (_title(core[1]), ""))
        return text + suffix, "" if suffix else unit
    if core[0] == "units" and len(core) == 3 and core[2] == "control":
        return f"Control mode of {core[1]}" + suffix, ""
    if core[0] == "units":
        return " · ".join([core[1], *(_title(p) for p in core[2:])]) + suffix, ""
    return " · ".join(_title(p) for p in core) + suffix, ""


def _island_sidecar_path(project) -> pathlib.Path:
    from services import project_registry
    from services.adequacy.island_config import SIDECAR_NAME

    return project_registry.project_dir(project) / SIDECAR_NAME


def _stored_island(project):
    path = _island_sidecar_path(project)
    try:
        return json.loads(path.read_text()) if path.is_file() else None
    except ValueError:
        return None  # an unreadable sidecar is replaced by the save; there is no old value to show


def _island_preview(args: dict) -> list[dict]:
    from services import campus_electrical_service as ce
    from services import project_registry

    config = args.get("config")

    def run(project, db, user):
        with _as_card_refusal():
            try:
                ce.checked_island_text(project, config)
            except (TypeError, ValueError) as exc:
                _refuse("invalid_edit_value", f"The island config is not valid JSON: {exc}")
        old = _stored_island(project)
        rows = _document_rows(_ISLAND_NAME, old, config, _ISLAND_GROUPS, _island_label,
                              lambda member: f"Unit {member.split('.', 1)[1]}", ("not set", "removed"))
        if not rows:
            _refuse("empty_edit", "The island data already has these values.")
        rows = _capped(rows, _ISLAND_NAME)
        if (project_registry.project_dir(project) / ce.SUBDIR / ce.ISLAND_STUDY_FILE).is_file():
            rows.append(_note("Settings", _ISLAND_NAME, "study", "The island study made with the current data is discarded."))
        return rows

    return _with_project(args, run, lock="campus")


def _island_readback(args: dict, rows: list[dict]) -> list[Any]:
    return _with_project(args, lambda project, db, user: _document_readback(
        _ISLAND_NAME, _stored_island(project), _ISLAND_GROUPS, rows))


register("campus_set_island_config", Adapter("settings_edit", _island_preview, _island_readback, section_id="assets"))


# campus_set_library: the project's copy of the equipment library (campus_assets.yaml).

_LIBRARY_GROUPS = {k: "list" for k in ("transformers", "cables", "capacitor_banks", "shunt_reactors", "statcoms",
                                       "switchgear")}
_LIBRARY_NAME = "Equipment library"
_LIBRARY_KINDS = {"transformers": "Transformers", "cables": "Cables", "capacitor_banks": "Capacitor banks",
                  "shunt_reactors": "Shunt reactors", "statcoms": "STATCOMs", "switchgear": "Switchgear"}


def _library_label(path: list[str]) -> tuple[str, str]:
    # The library has no form: the user reads and edits its YAML, so the labels are its own field names.
    tail = _SUFFIX.get(path[-1]) if len(path) > 1 else None
    core = path[:-1] if (tail or (path[-1] == "value" and len(path) > 1)) else path
    head = [_LIBRARY_KINDS[core[0]], *core[1:]] if core[0] in _LIBRARY_KINDS else core
    return " · ".join(head) + (tail or ""), ""


def _library_member_label(member: str) -> str:
    kind, _, ident = member.partition(".")
    return f"{_LIBRARY_KINDS.get(kind, kind)} · {ident}"


def _stored_library(project):
    """`(parsed library, is the shipped default)`: the project's copy when it has one."""
    from services import campus_electrical_service as ce

    own = ce.project_registry.project_dir(project) / ce.SUBDIR / ce.LIBRARY_FILE
    shipped = not own.is_file()
    text = pathlib.Path(ce.cs.ASSET_LIBRARY_PATH).read_text() if shipped else own.read_text()
    return yaml.safe_load(text), shipped


def _library_preview(args: dict) -> list[dict]:
    from services import campus_electrical_service as ce

    text = args.get("yaml")
    if not isinstance(text, str):
        _refuse("invalid_edit_value", "The equipment library is YAML text.")

    def run(project, db, user):
        with _as_card_refusal():
            new = ce.checked_library(project, text)
        old, shipped = _stored_library(project)
        rows = _document_rows(_LIBRARY_NAME, old, new, _LIBRARY_GROUPS, _library_label, _library_member_label,
                              ("not in the library", "removed"))
        if not rows:
            _refuse("empty_edit", "The library already has these values.")
        rows = _capped(rows, _LIBRARY_NAME)
        if shipped:
            rows.append(_note("Settings", _LIBRARY_NAME, "copy", "This saves the project's own copy of the library; "
                                                                  "Reset to default brings back the shipped one."))
        return rows

    return _with_project(args, run, lock="write")


def _library_readback(args: dict, rows: list[dict]) -> list[Any]:
    return _with_project(args, lambda project, db, user: _document_readback(
        _LIBRARY_NAME, _stored_library(project)[0], _LIBRARY_GROUPS, rows))


register("campus_set_library", Adapter("settings_edit", _library_preview, _library_readback, section_id="assets"))


# ── campus_draft_campus: a new campus file drafted from the solved network ──
# The tool creates (or, with overwrite, replaces) the campus file, so there is no single old
# value: each section shows how many items the file holds now ("not set" when there is no file)
# and how many the draft holds. The draft is the service's own, computed without a write.

_CAMPUS_SECTIONS = (("buses", "Buses"), ("transformers", "Transformers"), ("cables", "Cables"), ("units", "Units"))
_CAMPUS_NAME = "Campus file"


def _campus_cells(spec) -> dict[str, Any]:
    """The card's value for each campus section: an item count; the coupling bus's name; the frequency."""
    campus = spec.get("campus") if isinstance(spec, dict) else None
    if not isinstance(campus, dict):
        return {}
    cells = {key: len(campus[key]) for key, _ in _CAMPUS_SECTIONS if isinstance(campus.get(key), dict)}
    pcc = campus.get("pcc")
    if isinstance(pcc, dict):
        cells["pcc"] = pcc.get("pypsa_name") or pcc.get("bus")
    if campus.get("f_hz") is not None:
        cells["f_hz"] = campus["f_hz"]
    return cells


def _stored_campus(project):
    from services import campus_electrical_service as ce

    path = ce.project_registry.project_dir(project) / ce.SUBDIR / ce.CAMPUS_FILE
    try:
        return yaml.safe_load(path.read_text()) if path.is_file() else None
    except (OSError, yaml.YAMLError):
        return None


def _draft_preview(args: dict) -> list[dict]:
    from services import campus_electrical_service as ce

    def run(project, db, user):
        with _as_card_refusal():
            text, skipped = ce.planned_draft(project, bool(args.get("overwrite")))
        old_spec, new_spec = _stored_campus(project), yaml.safe_load(text)
        old, new = _campus_cells(old_spec), _campus_cells(new_spec)
        rows = []
        for field, label, unit in (("pcc", "Point of common coupling", ""), *((k, w, "") for k, w in _CAMPUS_SECTIONS),
                                   ("f_hz", "System frequency", "Hz")):
            sections_same = _same((old_spec or {}).get("campus", {}).get(field), new_spec["campus"].get(field))
            if old_spec is not None and sections_same:
                continue
            if field in new or field in old:
                rows.append(_settings_row(_CAMPUS_NAME, field, label, old.get(field), new.get(field), unit,
                                          says_name=label))
        if not rows:
            _refuse("empty_edit", "The campus file already matches the draft.")
        if old_spec is not None:
            rows.append(_note("Settings", _CAMPUS_NAME, "replaced", "Your edits to the campus file are replaced by the draft."))
        if skipped:
            shown = "; ".join(str(s) for s in skipped[:3]) + (f"; and {len(skipped) - 3} more" if len(skipped) > 3 else "")
            rows.append(_note("Settings", _CAMPUS_NAME, "skipped", f"Left out of the draft: {shown}."))
        return rows

    return _with_project(args, run, lock="write")


def _draft_readback(args: dict, rows: list[dict]) -> list[Any]:
    def run(project, db, user):
        cells = _campus_cells(_stored_campus(project))
        return [None if row["role"] == "note" else cells.get(row["field"]) for row in rows]

    return _with_project(args, run)


register("campus_draft_campus", Adapter("settings_edit", _draft_preview, _draft_readback, section_id="connection"))
