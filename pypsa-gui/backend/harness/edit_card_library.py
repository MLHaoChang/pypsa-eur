"""Old → new edit cards for the Library, map-import and weather tools (Q3-CARDS-1, group "library").

`put_library_item`, `import_library_series`, `import_meter_data`, `import_map_geometry`
and `set_weather_choice` write to the Library, the project's map files and its metadata.
Each preview checks the caller's access first (the handler's own check), reads the old
value from the store the tool writes, and takes the new value from the same pure
function the handler stores from (`library.items.prepare_item`,
`library.series_store.prepare_series`, `routers.library._meter_parts`,
`map_geometry_import.plan_apply`, `weather_prefs.validate`). A preview writes nothing.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from harness.edit_card import (
    STUDY_LEAF_CAP, _SECRET_KEY, Adapter, _note, _refuse, _same, _setting, _settings_row, _title, register,
)
from harness.edit_card_site import _fingerprint

_TEXT_CAP = 120  # a free-text string the card shows is cut here
_KIND_WORDS ={"tariff": "Tariff", "contract": "Contract", "connection_agreement": "Connection agreement"}


# ── cells ───────────────────────────────────────────────────────────────────

def _short(text: str) -> str:
    """A free-text string as the card shows it: cut at `_TEXT_CAP` characters."""
    return text if len(text) <= _TEXT_CAP else text[:_TEXT_CAP] + "…"


def _cell(field: str, value):
    """A value as the card shows it: a secret-like key only says it is set, a list or an
    object is summarised and never dumped, a long text is cut."""
    if any(word in field.lower() for word in _SECRET_KEY):
        return None if value in (None, "") else "set"
    if isinstance(value, list):
        return f"{len(value)} items"
    if isinstance(value, dict):
        return f"{len(value)} fields"
    if isinstance(value, str):
        return _short(value)
    return _setting(value)


def _row(name: str, field: str, label: str, old, new, unit: str = "") -> dict:
    """A settings row for two stored values. Cells that read the same while the values
    differ (a rotated secret, an edited list, a long text cut alike) say so on the new side
    and carry a fingerprint of the new value, which the receipt compares to the stored one."""
    row = _settings_row(name, field, label, None, None, unit, says_name=label)
    row["old"], row["new"] = _cell(field, old), _cell(field, new)
    if row["old"] is not None and row["old"] == row["new"] and not _same(old, new):
        row["new"] = f"{row['new']} (changed)"
        row["fingerprint"] = _fingerprint(new)
    return row


def _text_row(name: str, field: str, label: str, old: str | None, new: str | None, *, changed: bool = False,
              unit: str = "", fingerprint_of=None) -> dict:
    """A row of two cell texts. `fingerprint_of` is the new value behind the cell: the row then
    carries its fingerprint, and one whose two cells read the same says "(changed)"."""
    row = _settings_row(name, field, label, old, new, unit, says_name=label)
    if changed and old is not None and old == new:
        row["new"] = f"{new} (changed)"
    if fingerprint_of is not None:
        row["fingerprint"] = _fingerprint(fingerprint_of)
    return row


_DIFFERS = "differs from the card"


def _settle(row: dict, cell, value=None):
    """The stored cell. A row with a fingerprint reads back as its `new` only when the stored
    value has that fingerprint; the same cell on another value cannot pass for it."""
    fingerprint = row.get("fingerprint")
    if fingerprint is None:
        return cell
    if _fingerprint(value) == fingerprint:
        return row["new"]
    return cell if cell != row["new"] else _DIFFERS


def _refuse_http(exc: HTTPException):
    """A route's refusal with a text detail becomes the card's refusal; a coded one passes."""
    if isinstance(exc.detail, str):
        _refuse("invalid_edit_value", exc.detail, exc.status_code)
    raise exc


def _capped(rows: list[dict], what: str) -> list[dict]:
    if len(rows) <= STUDY_LEAF_CAP:
        return rows
    return rows[:STUDY_LEAF_CAP] + [_note("", "", what, f"{len(rows) - STUDY_LEAF_CAP} more {what} change.")]


def _read_rows(rows: list[dict], read) -> list[Any]:
    """A readback: one stored cell per row, none for a note. `read(row)` gives `(cell, value)`:
    the cell as the card writes it and the stored value behind it."""
    return [None if row["role"] == "note" else _settle(row, *read(row)) for row in rows]


# ── the Library: org, old versions ──────────────────────────────────────────

def _library(write: bool, use):
    """Run `use(db, org)` as the acting user, after the Library's own access check."""
    from routers.library import _target_org
    from services import chat_tools

    with chat_tools._acting() as (db, user):
        return use(db, _target_org(db, user, None, write=write))


def _stale(exc: Exception):
    _refuse("library_ref_stale", f"The stored version cannot be read ({exc}); nothing was changed.", 409)


def _flatten(doc: Any, prefix: tuple = ()) -> dict[tuple, Any]:
    """The leaves of a payload: nested objects are walked, a list or an empty object is one leaf."""
    if isinstance(doc, dict) and doc:
        out: dict[tuple, Any] = {}
        for key, value in doc.items():
            out.update(_flatten(value, (*prefix, str(key))))
        return out
    return {prefix: doc}


def _dig(doc: Any, path: tuple):
    for part in path:
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


# ── put_library_item ────────────────────────────────────────────────────────

def _item_inputs(args: dict) -> tuple[str, Any, str]:
    """`(kind, ItemIn, name)` as the handler builds them, or its refusal."""
    from routers.library import ItemIn
    from services import chat_tools

    try:
        body = ItemIn(payload=args.get("payload"), meta=args.get("meta") or {})
    except ValueError as exc:
        _refuse("invalid_edit_value", chat_tools._safe_text(exc, 300))
    name = args.get("name")
    if not isinstance(name, str):
        _refuse("invalid_edit_value", "Name the Library item to save.")
    return chat_tools._library_kind(args.get("kind")).value, body, name


def _prepared(kind: str, name: str, body):
    from routers.library import _item_name
    from services.library import items as I

    try:
        return I.prepare_item(kind, _item_name(name), body.payload, body.meta)
    except I.LibraryItemCommonReadonly as exc:
        _refuse("library_item_common_readonly", str(exc), 409)
    except ValueError as exc:
        _refuse("invalid_edit_value", str(exc))
    except HTTPException as exc:
        _refuse_http(exc)


def _item_label(kind: str, name: str, *parts: str) -> str:
    return " · ".join([f"{_KIND_WORDS[kind]} '{name}'", *parts])


def _item_rows(kind: str, name: str, old_payload, new_payload, old_meta, new_meta, old_version, named_meta) -> list[dict]:
    from services.library import items as I

    row_name = f"Library {_KIND_WORDS[kind].lower()}"
    rows = [_text_row(row_name, "version", _item_label(kind, name, "Version"),
                      None if old_version is None else f"v{old_version}",
                      f"v{(old_version or 0) + 1}")]
    before = _flatten(old_payload) if old_payload is not None else {}
    after = _flatten(new_payload)
    leaves = []
    for path, value in after.items():
        if old_payload is None and value is None:
            continue  # a default the new item does not carry
        if path in before and _same(before[path], value):
            continue
        field = "payload." + ".".join(path)
        leaves.append(_row(row_name, field, _item_label(kind, name, *(_title(p) for p in path)),
                           before.get(path), value))
    for path in before:
        if path not in after and before[path] is not None:  # a leaf the new payload drops
            leaves.append(_row(row_name, "payload." + ".".join(path),
                               _item_label(kind, name, *(_title(p) for p in path)), before[path], None))
    rows += _capped(leaves, "fields")
    for key in named_meta:
        rows.append(_row(row_name, f"meta.{key}", _item_label(kind, name, "Meta", _title(key)),
                         (old_meta or {}).get(key), new_meta.get(key)))
    return rows


def _put_item_preview(args: dict) -> list[dict]:
    import json

    from services.library import items as I

    kind, body, name = _item_inputs(args)

    def use(db, org):  # the access check (`_target_org`, write) has passed before any old value is read
        prepared = _prepared(kind, name, body)
        latest = I.latest_ref(db, org, kind, prepared.name)
        if latest is None:
            return prepared, None, None, None
        if latest.hash == prepared.digest:  # the same content is the same version; its stored meta stays
            _refuse("empty_edit", f"{_KIND_WORDS[kind]} '{prepared.name}' already holds this content "
                    f"(v{latest.version}); nothing would be saved.")
        try:
            return prepared, latest.version, I.resolve(db, org, latest), I.item_meta(db, org, latest)
        except (I.LibraryRefNotFound, I.LibraryRefStale) as exc:
            _stale(exc)

    prepared, old_version, old_payload, old_meta = _library(True, use)
    named = [key for key in body.meta if key in type(prepared.meta).model_fields]
    return _item_rows(kind, prepared.name, old_payload, json.loads(prepared.data), old_meta,
                      prepared.meta.model_dump(), old_version, named)


def _put_item_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.library import items as I

    kind, body, name = _item_inputs(args)
    from routers.library import _item_name

    item = _item_name(name).strip()

    def use(db, org):
        latest = I.latest_ref(db, org, kind, item)
        if latest is None:
            return None, None, None
        try:
            return latest.version, I.resolve(db, org, latest), I.item_meta(db, org, latest)
        except (I.LibraryRefNotFound, I.LibraryRefStale):
            return latest.version, None, None

    version, payload, meta = _library(False, use)

    def read(row):
        field = row["field"]
        if field == "version":
            cell = None if version is None else f"v{version}"
            return cell, cell
        if payload is None:
            return None, None
        if field.startswith("meta."):
            value = (meta or {}).get(field.removeprefix("meta."))
        else:
            value = _dig(payload, tuple(field.removeprefix("payload.").split(".")))
        return _cell(field, value), value

    return _read_rows(rows, read)


register("put_library_item", Adapter("settings_edit", _put_item_preview, _put_item_readback))


# ── import_library_series / import_meter_data ───────────────────────────────

def _series_cells(series) -> dict[str, Any]:
    return {"points": len(series),
            "first": series.index[0].isoformat() if len(series) else None,
            "last": series.index[-1].isoformat() if len(series) else None,
            "timezone": str(series.index.tz) if series.index.tz is not None else "no zone"}


_SERIES_LABELS = {"points": "Points", "first": "First timestamp", "last": "Last timestamp",
                  "timezone": "Time zone", "source_unit": "Unit the file is read in",
                  "settlement": "Settlement", "label": "Timestamp label"}
_METER_META = ("source_unit", "settlement", "label")


def _series_upload(args: dict):
    """The active project's upload, as the handler resolves it, and the Library router."""
    from routers import library as L
    from services import chat_tools

    file_id = args.get("file_id")
    if not isinstance(file_id, str) or not isinstance(args.get("name"), str):
        _refuse("invalid_edit_value", "Name the uploaded file (file_id) and the series name.")
    upload = chat_tools._active_upload(file_id)  # the handler's first step: an upload of the active project
    return upload, L


def _series_preview_for(args: dict, meter: bool) -> list[dict]:
    from services.library import series_store as S

    upload, L = _series_upload(args)
    timezone = args.get("timezone")

    def use(db, org):  # the Library write check has passed before the file or any old version is read
        from services import chat_tools

        try:
            name = L._upload_name(args["name"])
            data = chat_tools._sync(L.read_capped(upload))
            if meter:
                unit, settlement, label = args.get("unit"), args.get("settlement", "15min"), args.get("label", "start")
                series, meta, _hist = L._meter_parts(data, upload.filename or "", unit, settlement, label, timezone)
            else:
                series = L._parsed(L.series_io.parse_upload, data, upload.filename or "", timezone,
                                   max_points=L.MAX_POINTS)
                meta = {"source": "upload"}
            _meta, _blob, _tz, digest = S.prepare_series(name, series, meta)
        except Exception as exc:  # noqa: BLE001 — the route's own mapping of a refused file
            refusal = L._refuse_422(exc)
            if isinstance(refusal.detail, str):
                _refuse("invalid_edit_value", refusal.detail, refusal.status_code)
            raise refusal from exc
        latest = S.latest_ref(db, org, name)
        old_series = old_meta = None
        if latest is not None:
            try:
                old_series, old_meta = S.resolve(db, org, latest), S.series_meta(db, org, latest)
            except (S.LibraryRefNotFound, S.LibraryRefStale) as exc:
                _stale(exc)
            if latest.hash == digest:
                if meter:
                    try:
                        L.meter_meta_clash(name, old_meta, meta)
                    except HTTPException as exc:
                        _refuse("meter_meta_conflict", str(exc.detail.get("message", "")), 409)
                _refuse("empty_edit", f"Library series '{name}' already holds exactly this data (v{latest.version}); "
                        "nothing would be saved.")
        return name, series, meta, latest, old_series, old_meta

    name, series, meta, latest, old_series, old_meta = _library(True, use)
    row_name = f"Library series {name}"
    old_cells = _series_cells(old_series) if old_series is not None else {}
    new_cells = _series_cells(series)

    def label(part: str) -> str:
        return f"Series '{name}' · {part}"

    rows = [_text_row(row_name, "version", label("Version"), None if latest is None else f"v{latest.version}",
                      f"v{(latest.version if latest else 0) + 1}")]
    for key in ("points", "first", "last", "timezone"):
        rows.append(_settings_row(row_name, key, label(_SERIES_LABELS[key]), old_cells.get(key), new_cells[key],
                                  says_name=label(_SERIES_LABELS[key])))
    if meter:
        for key in _METER_META:
            rows.append(_settings_row(row_name, key, label(_SERIES_LABELS[key]), (old_meta or {}).get(key), meta[key],
                                      says_name=label(_SERIES_LABELS[key])))
    return rows


def _series_readback_for(args: dict, rows: list[dict]) -> list[Any]:
    from routers.library import _upload_name
    from services.library import series_store as S

    name = _upload_name(args["name"])

    def use(db, org):
        latest = S.latest_ref(db, org, name)
        if latest is None:
            return None, None, {}
        try:
            return latest.version, S.resolve(db, org, latest), S.series_meta(db, org, latest)
        except (S.LibraryRefNotFound, S.LibraryRefStale):
            return latest.version, None, {}

    version, series, meta = _library(False, use)
    cells = _series_cells(series) if series is not None else {}

    def read(row):
        field = row["field"]
        if field == "version":
            cell = None if version is None else f"v{version}"
        else:
            cell = meta.get(field) if field in _METER_META else cells.get(field)
        return cell, cell

    return _read_rows(rows, read)


register("import_library_series", Adapter("settings_edit", lambda a: _series_preview_for(a, False),
                                          _series_readback_for))
register("import_meter_data", Adapter("settings_edit", lambda a: _series_preview_for(a, True), _series_readback_for))


# ── set_weather_choice ──────────────────────────────────────────────────────

_WEATHER = "Weather for generated profiles"
_SOURCE_WORDS = {"synthetic": "Synthetic", "atlite": "ERA5"}


def _weather_project(args: dict):
    """The project, after the handler's access and lock checks."""
    from routers import profiles
    from services import chat_tools

    project_id = args.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        _refuse("empty_edit", "Name the project to edit.")
    proj = chat_tools._authorized_project(project_id)
    with chat_tools._acting() as (db, user):
        profiles._check_lock(proj, db, user)
    return proj


def _saved_weather(proj) -> dict:
    from services.profile_gen import weather_prefs

    return weather_prefs.saved_choice(proj.directory) or {"source": None, "year": None}


def _weather_preview(args: dict) -> list[dict]:
    from pydantic import ValidationError

    from routers.profiles import WeatherChoice
    from services.profile_gen import weather_prefs

    proj = _weather_project(args)
    try:
        choice = WeatherChoice(source=args.get("source"), year=args.get("year"))
        weather_prefs.validate(choice.source, choice.year)
    except ValidationError as exc:
        _refuse("invalid_edit_value", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                               for e in exc.errors(include_url=False)[:3]))
    except weather_prefs.WeatherChoiceError as exc:
        _refuse("weather_choice", str(exc))
    old = _saved_weather(proj)
    if old["source"] == choice.source and old["year"] == choice.year:
        _refuse("empty_edit", f"The weather choice is already {_SOURCE_WORDS[choice.source]} {choice.year}.")
    source = _settings_row("Weather", "source", f"{_WEATHER} · Source", old["source"], choice.source,
                           says_name=f"{_WEATHER} · Source")
    source["words"] = {"old": _SOURCE_WORDS.get(old["source"], "not set"), "new": _SOURCE_WORDS[choice.source]}
    year = _settings_row("Weather", "year", f"{_WEATHER} · Year", old["year"], choice.year,
                         says_name=f"{_WEATHER} · Year")
    return [source, year]


def _weather_readback(args: dict, rows: list[dict]) -> list[Any]:
    saved = _saved_weather(_weather_project(args))
    return [saved[row["field"]] for row in rows]


register("set_weather_choice", Adapter("settings_edit", _weather_preview, _weather_readback, section_id="profiles"))


# ── import_map_geometry ─────────────────────────────────────────────────────

_IMPORT = "Import geometry"


def _route_cell(layout: dict, key: str) -> str:
    from services import map_layout_service

    points = map_layout_service.route_points(layout, key)
    return f"{len(points)} bend points" if points else "straight line"


def _site_cell(sites: dict, site_id: str) -> str | None:
    site = next((s for s in sites["sites"] if isinstance(s, dict) and s.get("id") == site_id), None)
    if site is None:
        return None
    return f"{_short(str(site['name']))} · {len(site['boundary'])} vertices · {len(site['buses'])} buses"


def _site_body(site: dict) -> dict:
    """What identifies a site apart from its id, which every match draws fresh."""
    return {key: site.get(key) for key in ("name", "buses", "boundary", "origin", "placements")}


def _site_matches(sites: dict, fingerprint: str) -> int:
    return sum(1 for s in sites["sites"] if isinstance(s, dict) and _fingerprint(_site_body(s)) == fingerprint)


def _map_import(args: dict):
    """`(dest, plan or None, report, derive)` after the handler's own checks, in its order."""
    from routers import projects
    from services import chat_tools, project_registry, project_settings

    name, file_id, apply = args.get("name"), args.get("file_id"), bool(args.get("apply", False))
    tolerance = args.get("tolerance_m", 50.0)
    if not isinstance(name, str) or not isinstance(file_id, str):
        _refuse("invalid_edit_value", "Name the project and the uploaded file (file_id).")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        _refuse("invalid_edit_value", "tolerance_m is a number of metres.")
    active, data = chat_tools._map_geometry_upload(name, file_id)
    with chat_tools._acting() as (db, user):
        dest, canonical = projects._resolve_project_src(name, db, user)  # the access check, before any old value
        if not dest.exists():
            _refuse("project_not_found", f"Project '{canonical}' not found", 404)
        projects._check_import_tolerance(float(tolerance))
        features = projects._parse_import_features(data, None)
        if apply:
            projects._check_project_lock(db, project_registry.find_project(db, user, name), user)
        report = projects._import_match_report(dest, canonical, features, float(tolerance))
    plan = None
    if apply:
        from services import map_geometry_import as mgi, map_layout_service, site_service

        try:
            plan = mgi.plan_apply(dest, report)
        except (map_layout_service.MapLayoutInvalid, site_service.SitesInvalid) as exc:
            _refuse("invalid_edit_value", str(exc))
    return dest, plan, report, project_settings.derive_lengths_enabled(dest)


def _length_rows(dest, plan: dict) -> list[dict]:
    from services import network_lengths
    from services.network_geometry import _branch_geometry_km
    from services.pypsa_service import PyPSAService

    keys = [k for k in plan["routes"] if k.split(":", 1)[0] in network_lengths.LENGTH_KINDS]
    routes = network_lengths.interior_routes(plan["layout"]) if plan["layout"] is not None else {}
    network, rows = PyPSAService.get_network(), []
    with PyPSAService.get_lock():
        targets, _skipped = network_lengths._targets(network, keys)
        for kind, name in targets:
            frame = getattr(network, network_lengths.LENGTH_KINDS[kind])
            geo = _branch_geometry_km(network, str(frame.at[name, "bus0"]), str(frame.at[name, "bus1"]),
                                      routes.get(f"{kind}:{name}"))
            if geo is None:
                continue  # an unplaced bus: the handler skips it
            old, new = float(frame.at[name, "length"]), float(geo[0])
            if not _same(old, new):
                rows.append({"component_class": kind.capitalize(), "name": name, "field": "length", "label": "Length",
                             "old": old, "new": new, "unit": "km", "role": "typed"})
    return rows


def _map_preview(args: dict) -> list[dict]:
    from services import map_layout_service, site_service

    dest, plan, report, derive = _map_import(args)
    if plan is None:
        # Without `apply` the handler only reports the matches and writes nothing.
        text = (f"Preview only: {len(report['routes'])} route(s) and {len(report['sites'])} site(s) match, "
                f"{len(report['unmatched'])} feature(s) do not. Nothing is written until apply is on.")
        return [_note("Settings", _IMPORT, "apply", text)]
    before_layout = map_layout_service.read_map_layout(dest) if plan["layout"] is not None else None
    routes = []
    for key in plan["routes"]:
        old, new = _route_cell(before_layout, key), _route_cell(plan["layout"], key)
        old_points = map_layout_service.route_points(before_layout, key)
        if old_points != map_layout_service.route_points(plan["layout"], key) or old != new:
            routes.append(_text_row(_IMPORT, f"route:{key}", f"{_IMPORT} · Routes · {key}", old, new,
                                    changed=True, fingerprint_of=map_layout_service.route_points(plan["layout"], key)))
    # A site's id is drawn fresh by every match, so a row is keyed by the file's feature, never by that id.
    # The row carries a fingerprint of the site the card computed and how many identical sites the
    # project holds already, so the receipt needs one more than that.
    sites, seen = [], {}
    stored = {s["id"]: s for s in plan["sites"]["sites"] if isinstance(s, dict)} if plan["sites"] else {}
    old_sites = site_service.read_sites(dest) if plan["sites"] is not None else {"sites": []}
    for found, sid in zip(report["sites"], plan["site_ids"]):
        body = _site_body(stored[sid])
        row = _text_row(_IMPORT, f"site:{found['feature']}", f"{_IMPORT} · Sites · {_short(str(found['name']))}",
                        None, _site_cell(plan["sites"], sid), fingerprint_of=body)
        row["prior"] = _site_matches(old_sites, row["fingerprint"]) + seen.get(row["fingerprint"], 0)
        seen[row["fingerprint"]] = seen.get(row["fingerprint"], 0) + 1
        sites.append(row)
    rows = _capped(routes, "routes") + _capped(sites, "sites")
    if derive:
        lengths = _length_rows(dest, plan)
        rows += _capped(lengths, "lengths")
        if any(r["component_class"] == "Line" for r in lengths):
            rows.append(_note("Line", "", "length", "Line impedances and per-km costs follow the new lengths."))
    if report["unmatched"]:
        rows.append(_note("Settings", _IMPORT, "unmatched",
                          f"{len(report['unmatched'])} feature(s) match no branch or site and are skipped."))
    if not any(r["role"] == "typed" for r in rows):
        _refuse("empty_edit", "Nothing in this file would change the routes, sites or lengths.")
    return rows


def _map_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import chat_tools, map_layout_service, project_registry, site_service
    from services.pypsa_service import PyPSAService
    from routers import projects

    with chat_tools._acting() as (db, user):
        dest, _canonical = projects._resolve_project_src(args["name"], db, user)
    layout, sites = map_layout_service.read_map_layout(dest), site_service.read_sites(dest)
    network = PyPSAService.get_network()

    def read(row):
        field = row["field"]
        if field.startswith("route:"):
            key = field.removeprefix("route:")
            return _route_cell(layout, key), map_layout_service.route_points(layout, key)
        if field.startswith("site:"):  # the site this row added: more identical sites than before the write
            found = [_site_body(s) for s in sites["sites"]
                     if isinstance(s, dict) and _fingerprint(_site_body(s)) == row["fingerprint"]]
            return (row["new"], found[0]) if len(found) > row["prior"] else (None, None)
        frame = network.lines if row["component_class"] == "Line" else network.links
        length = float(frame.at[row["name"], "length"]) if row["name"] in frame.index else None
        return length, length

    return _read_rows(rows, read)


register("import_map_geometry", Adapter("settings_edit", _map_preview, _map_readback))
