"""Old -> new edit cards for the record-keeping tools (Q3-CARDS-1, group "records").

`put_stress_scenarios`, `record_asset_health`, `set_study_parameter` and
`update_project_layout` write sidecar files and study records, not the network. Each
preview checks the caller's access first, reads the old value from the store the tool
writes and takes the new value from the tool's own pure validator; nothing is written.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from harness.edit_card import (
    STUDY_LEAF_CAP,
    Adapter,
    _note,
    _refuse,
    _settings_row,
    _setting,
    register,
)

_CELL_CAP = 120  # characters of free text one cell shows


def _same_json(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _title(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _plain(value):
    """A cell for a scalar or free text; a list or object is summarised, never dumped."""
    if isinstance(value, list):
        return f"{len(value)} items"
    if isinstance(value, dict):
        return f"{len(value)} fields"
    if isinstance(value, str) and len(value) > _CELL_CAP:
        return value[:_CELL_CAP] + "…"
    return _setting(value)


def _fingerprint(value) -> str:
    """Short content hash, so a series whose length did not change still reads as changed."""
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:6]


def _bulk(value):
    """A series or object cell: its size and a content tag."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return f"{_plain(value)} · #{_fingerprint(value)}"
    return _plain(value)


def _capped(rows: list[dict], what: str) -> list[dict]:
    """At most STUDY_LEAF_CAP rows, then one summary row."""
    shown = [r for r in rows if r["role"] != "note"]
    notes = [r for r in rows if r["role"] == "note"]
    if len(shown) <= STUDY_LEAF_CAP:
        return rows
    return shown[:STUDY_LEAF_CAP] + [_note("", "", what, f"{len(shown) - STUDY_LEAF_CAP} more changes not shown.")] + notes


# ── put_stress_scenarios ───────────────────────────────────────────────────

_STRESS_LABELS = {
    "id": "Id", "name": "Name", "kind": "Kind", "frequency_per_year": "Events/yr",
    "electrical_load_multiplier": "Load multiplier",
    "renewable_availability_multiplier": "Renewable availability multiplier",
    "profile_pack": "Profile pack",
}


def _stress_project(args: dict):
    from services import chat_tools

    return chat_tools._authorized_project(args.get("name"))


def _stress_new(args: dict) -> list[dict]:
    """The registry the PUT would store, from the route's own validator; nothing is saved."""
    from routers.adequacy_worksheet import StressScenariosPut
    from services.adequacy import stress

    try:
        body = StressScenariosPut(scenarios=args.get("scenarios"))
    except ValidationError as exc:
        _refuse("invalid_edit_value", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                               for e in exc.errors(include_url=False)[:3]))
    try:
        stress._validate(body.scenarios)
    except stress.StressValidationError as exc:
        _refuse("invalid_edit_value", str(exc), 422)
    return body.scenarios


def _stress_cell(key: str, value):
    # A series or object is summarised with a content tag, so an edit that keeps its size still shows.
    return _bulk(value) if isinstance(value, (list, dict)) else _plain(value)


def _scenario_name(scenario: dict) -> str:
    name = scenario.get("name")
    return str(name) if isinstance(name, str) and name.strip() else str(scenario.get("id"))


def _scenario_summary(scenario: dict | None):
    if scenario is None:
        return None
    freq = scenario.get("frequency_per_year")
    return f"{scenario.get('kind')} · {_plain(freq)} events/yr"


def _stress_preview(args: dict) -> list[dict]:
    from services.adequacy import stress

    project = _stress_project(args)  # access first
    new = _stress_new(args)
    old, error = stress.load_scenarios_checked(project.directory)
    if _same_json(old, new) and error is None:
        _refuse("empty_edit", "The project already has exactly these stress scenarios.")
    before = {str(s.get("id")): s for s in old if isinstance(s, dict)}
    after = {s["id"]: s for s in new}
    rows: list[dict] = []
    for sid in dict.fromkeys([*after, *before]):
        a, b = before.get(sid), after.get(sid)
        label = f"Stress scenario · {_scenario_name(b or a)}"
        if a is None or b is None:
            row = _settings_row("Stress scenarios", f"scenario.{sid}", label, _scenario_summary(a),
                                _scenario_summary(b), says_name=label)
            row["words"] = {"old": row["old"] if a else "Not in the list", "new": row["new"] if b else "Removed"}
            rows.append(row)
            continue
        for key in dict.fromkeys([*a, *b]):
            if _same_json(a.get(key), b.get(key)):
                continue
            field_label = f"{label} · {_STRESS_LABELS.get(key, _title(key))}"
            rows.append(_settings_row("Stress scenarios", f"scenario.{sid}.{key}", field_label,
                                      _stress_cell(key, a.get(key)), _stress_cell(key, b.get(key)),
                                      says_name=field_label))
    common_old = [s for s in before if s in after]
    common_new = [s for s in after if s in before]
    if common_old != common_new:
        rows.append(_settings_row("Stress scenarios", "order", "Stress scenarios · Order",
                                  ", ".join(common_old), ", ".join(common_new),
                                  says_name="Stress scenarios · Order"))
    if error is not None:
        rows.append(_note("", "", "unreadable", f"The stored list could not be read ({error}); this replaces it."))
    if not any(r["role"] != "note" for r in rows):
        _refuse("empty_edit", "The project already has exactly these stress scenarios.")
    return _capped(rows, "scenarios")


def _stress_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.adequacy import stress

    stored = {str(s.get("id")): s for s in stress.load_scenarios(_stress_project(args).directory)
              if isinstance(s, dict)}
    out = []
    for row in rows:
        field = row["field"]
        if row["role"] == "note":
            out.append(None)
        elif field == "order":
            out.append(_stored_order(rows, stored))
        else:
            _, sid, *key = field.split(".", 2)
            scenario = stored.get(sid)
            if not key:
                out.append(_scenario_summary(scenario))
            else:
                out.append(None if scenario is None or key[0] not in scenario
                           else _stress_cell(key[0], scenario[key[0]]))
    return out


def _stored_order(rows: list[dict], stored: dict) -> str:
    """The ids the card names in its order row, as the stored list now orders them."""
    named = set(next(r for r in rows if r["field"] == "order")["new"].split(", "))
    return ", ".join(sid for sid in stored if sid in named)


register("put_stress_scenarios", Adapter("settings_edit", _stress_preview, _stress_readback,
                                         section_id="scenarios"))


# ── record_asset_health ────────────────────────────────────────────────────

_HEALTH_LABELS = {
    "outage_rate_value": ("Outage rate", ""), "outage_rate_basis": ("Outage rate basis", ""),
    "mttr_hours": ("Repair time (MTTR)", "h"), "method": ("Method", ""), "source_ref": ("Source", ""),
    "measured_at": ("Measured on", ""), "confidence": ("Confidence", ""), "note": ("Note", ""),
}


def _health_new(args: dict) -> list[dict]:
    from routers.adequacy_worksheet import AssetHealthPut
    from services.adequacy import asset_health

    try:
        body = AssetHealthPut(entries=list(args.get("entries") or []))
    except (ValidationError, TypeError) as exc:
        _refuse("invalid_edit_value", str(exc)[:200])
    try:
        return asset_health.validate_entries(body.entries)
    except asset_health.AssetHealthValidationError as exc:
        _refuse("invalid_edit_value", str(exc), 422)


def _health_summary(entry: dict | None):
    if entry is None:
        return None
    parts = []
    if entry.get("outage_rate_value") is not None:
        parts.append(f"outage rate {_plain(entry['outage_rate_value'])}")
    if entry.get("mttr_hours") is not None:
        parts.append(f"MTTR {_plain(entry['mttr_hours'])} h")
    parts += [f"{entry['method']}", f"measured {entry['measured_at']}"]
    return " · ".join(parts)


def _health_preview(args: dict) -> list[dict]:
    from services import chat_tools
    from services.adequacy import asset_health

    project = chat_tools._authorized_project(args.get("name"))  # access first
    new = _health_new(args)
    old = asset_health.load_asset_health(project.directory)["entries"]
    if _same_json(sorted(old, key=lambda e: (e["component"], e["name"])),
                  sorted(new, key=lambda e: (e["component"], e["name"]))):
        _refuse("empty_edit", "The ledger already holds exactly these entries.")
    before = {(e["component"], e["name"]): e for e in old}
    after = {(e["component"], e["name"]): e for e in new}
    rows: list[dict] = []
    for key in dict.fromkeys([*after, *before]):
        a, b = before.get(key), after.get(key)
        asset = f"Asset health · {key[0]} {key[1]}"
        field = f"health:{key[0]}:{key[1]}"
        if a is None or b is None:
            row = _settings_row("Asset health", field, asset, _health_summary(a), _health_summary(b),
                                says_name=asset)
            row["words"] = {"old": row["old"] if a else "Not recorded", "new": row["new"] if b else "Removed"}
            rows.append(row)
            continue
        for attr, (word, unit) in _HEALTH_LABELS.items():
            if _same_json(a.get(attr), b.get(attr)):
                continue
            label = f"{asset} · {word}"
            rows.append(_settings_row("Asset health", f"{field}:{attr}", label, _plain(a.get(attr)),
                                      _plain(b.get(attr)), unit, says_name=label))
    return _capped(rows, "entries")


def _health_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import chat_tools
    from services.adequacy import asset_health

    stored = {(e["component"], e["name"]): e for e in
              asset_health.load_asset_health(chat_tools._authorized_project(args.get("name")).directory)["entries"]}
    out = []
    for row in rows:
        if row["role"] == "note":
            out.append(None)
            continue
        _, rest = row["field"].split(":", 1)
        component, rest = rest.split(":", 1)
        name, _, attr = rest.rpartition(":") if rest.rpartition(":")[2] in _HEALTH_LABELS else (rest, "", "")
        entry = stored.get((component, name))
        if not attr:
            out.append(_health_summary(entry))
        else:
            out.append(None if entry is None else _plain(entry.get(attr)))
    return out


register("record_asset_health", Adapter("settings_edit", _health_preview, _health_readback,
                                        section_id="assets"))


# ── set_study_parameter ────────────────────────────────────────────────────

def _ledger_edit(args: dict):
    """(study, old row, new row) of the assumption `key`, from the route's own ledger edit."""
    from routers import studies
    from services.study import ledger as study_ledger
    from services.study import library as study_library

    from harness.edit_card import _study

    study = _study(args)  # access first
    studies._refuse_unless_enabled()
    fields = {k: args[k] for k in ("key", "value", "unit") if k in args}
    if args.get("source"):
        fields["source"] = args["source"]
    try:
        edit = studies.LedgerEdit(**fields)
    except ValidationError as exc:
        _refuse("invalid_edit_value", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                               for e in exc.errors(include_url=False)[:3]))
    ledger, _stored = studies._current_ledger(study)
    try:
        after = studies._ledger_after(study, ledger, studies.LedgerPut(rows=[edit]), None, datetime.now(tz=UTC))
    except (study_ledger.LedgerEditError, study_library.LibraryError) as exc:
        _refuse("invalid_edit_value", str(exc))
    old = next(r for r in ledger.rows if r.key == edit.key)
    new = next(r for r in after.rows if r.key == edit.key)
    return study, old, new


def _stored_ledger_row(args: dict):
    study = _study_for(args)
    ledger = study.ledger
    return None if ledger is None else next((r for r in ledger.rows if r.key == args.get("key")), None)


def _study_for(args: dict):
    from harness.edit_card import _study

    return _study(args)


_LEDGER_ATTRS = (("value", None), ("source", "Source"), ("status", "Status"), ("provenance", "Provenance"))


def _ledger_preview(args: dict) -> list[dict]:
    _, old, new = _ledger_edit(args)
    rows = []
    for attr, word in _LEDGER_ATTRS:
        a, b = getattr(old, attr), getattr(new, attr)
        named = attr == "value" or (attr == "source" and bool(args.get("source")))
        if not named and _same_json(a, b):
            continue
        label = new.label if word is None else f"{new.label} · {word}"
        rows.append(_settings_row("Assumption", f"ledger.{new.key}.{attr}", label, _plain(a), _plain(b),
                                  new.unit if attr == "value" else "", says_name=label))
    if all(_same_json(getattr(old, a), getattr(new, a)) for a, _ in _LEDGER_ATTRS):
        _refuse("empty_edit", f"{new.label} already has this value, source and status.")
    return rows


def _ledger_readback(args: dict, rows: list[dict]) -> list[Any]:
    stored = _stored_ledger_row(args)
    return [None if row["role"] == "note" or stored is None
            else _plain(getattr(stored, row["field"].rsplit(".", 1)[1])) for row in rows]


register("set_study_parameter", Adapter("settings_edit", _ledger_preview, _ledger_readback, section_id="project"))


# ── update_project_layout ──────────────────────────────────────────────────

_LAYOUT_LABELS = {"nodes": "Nodes", "edges": "Edges", "version": "Format version", "savedAt": "Saved at",
                  "assetMode": "Asset display"}


def _layout_dir(args: dict):
    """The project's storage directory, by the route's own access check; nothing is read yet."""
    from routers.projects import _resolve_project_src
    from services import chat_tools

    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        _refuse("empty_edit", "Name the project to edit.")
    with chat_tools._acting() as (db, user):
        src, canonical = _resolve_project_src(name, db, user)
    if not src.exists():
        raise HTTPException(404, f"Project '{canonical}' not found")
    return src


def _stored_layout(src) -> dict:
    try:
        data = json.loads((src / "layout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # the route's own reading of a missing or unusable file
    return data if isinstance(data, dict) else {}


def _layout_cell(value):
    return _plain(value)


def _layout_change(key: str, old, new) -> str | None:
    """Which members of a nodes / edges list were added, removed or edited."""
    if not (isinstance(old, list) and isinstance(new, list)) and not (old is None and isinstance(new, list)) \
            and not (new is None and isinstance(old, list)):
        return None
    ids = lambda items: {i["id"]: i for i in items or [] if isinstance(i, dict) and "id" in i}  # noqa: E731
    before, after = ids(old), ids(new)
    if len(before) != len(old or []) or len(after) != len(new or []):
        return None
    edited = sum(1 for i in before if i in after and not _same_json(before[i], after[i]))
    return (f"{_LAYOUT_LABELS.get(key, _title(key))}: {len(set(after) - set(before))} added, "
            f"{len(set(before) - set(after))} removed, {edited} moved or edited.")


def _layout_preview(args: dict) -> list[dict]:
    from routers.projects import _serialized_layout

    src = _layout_dir(args)  # access first
    layout = args.get("layout")
    if not isinstance(layout, dict):
        _refuse("invalid_edit_value", "layout must be an object.")
    _serialized_layout(layout)  # the route's size cap
    old = _stored_layout(src)
    if _same_json(old, layout):
        _refuse("empty_edit", "The project already has this layout.")
    rows: list[dict] = []
    for key in dict.fromkeys([*old, *layout]):
        if _same_json(old.get(key), layout.get(key)):
            continue
        label = f"Diagram layout · {_LAYOUT_LABELS.get(key, _title(key))}"
        rows.append(_settings_row("Layout", f"layout.{key}", label, _layout_cell(old.get(key)),
                                  _layout_cell(layout.get(key)), says_name=label))
        change = _layout_change(key, old.get(key), layout.get(key))
        if change:
            rows.append(_note("", "", f"layout.{key}.change", change))
    return _capped(rows, "layout")


def _layout_readback(args: dict, rows: list[dict]) -> list[Any]:
    stored = _stored_layout(_layout_dir(args))
    return [None if row["role"] == "note" else _layout_cell(stored.get(row["field"].split(".", 1)[1]))
            for row in rows]


register("update_project_layout", Adapter("settings_edit", _layout_preview, _layout_readback,
                                          section_id="project"))
