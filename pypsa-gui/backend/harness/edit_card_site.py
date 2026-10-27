"""Old → new edit cards for the site, commercial and finance settings tools (Q3-CARDS-1, slice 2, group `site`).

set_site_connection, set_project_sites, set_finance_inputs, attach_tariff, import_urdb_tariff and
define_participants each register an `Adapter` here, in the style of the slice-1 adapters in
`harness/edit_card.py`. A preview writes nothing; the new values come from the tool's own pure
validator (`chat_tools._site_connection_plan`, `_attach_tariff_plan`, `_finance_request`,
`_urdb_request`, `site_service.validate_sites`) or, for the participants, from the handler run on an
isolated copy (`_sandbox`); the old values come from the store the tool writes, read only after the
caller's access was checked the way the handler checks it.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from harness.edit_card import (STUDY_LEAF_CAP, _SECRET_KEY, Adapter, _note, _refuse, _sandbox,
                               _setting, _settings_row, register)

_SECTION = "commercial"  # Commercial & parties, where the flow lists these settings


def _equal(a, b) -> bool:
    """Stored documents are equal when their JSON is (key order and int/float spelling aside)."""
    def norm(value):
        return json.dumps(value, sort_keys=True, default=str, allow_nan=False)

    try:
        return norm(a) == norm(b)
    except ValueError:  # a non-finite number: compare as the values they are
        return a == b


def _blank(value) -> bool:
    """A value the document would omit: nothing, an empty object or an empty list."""
    return value is None or value == {} or value == []


def _fingerprint(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _title(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


# ── cells and rows for whole-document settings ──────────────────────────────

def _cell(path: tuple, value):
    """A stored value as a card cell. A secret-like key only says whether it is set; a list or
    an object is summarised, never dumped."""
    if any(word in ".".join(map(str, path)).lower() for word in _SECRET_KEY):
        return None if value is None else "set"
    if isinstance(value, list):
        return _count(len(value), "item")
    if isinstance(value, dict):
        return _count(len(value), "field")
    return _setting(value)


def _doc_row(name: str, path: tuple, label: str, old, new, cell: Callable = _cell) -> dict:
    """One changed (or named) value. A change a count or "set" cannot show says "(changed)";
    the receipt then compares a fingerprint of the stored value."""
    row = _settings_row(name, ".".join(map(str, path)), label, None, None, says_name=label)
    old, new = (None if _blank(old) else old), (None if _blank(new) else new)
    old_cell, new_cell = cell(path, old), cell(path, new)
    if old_cell == new_cell and not _equal(old, new):
        new_cell = f"{new_cell} (changed)"
        row["fingerprint"] = _fingerprint(new)
    row["old"], row["new"], row["path"] = old_cell, new_cell, list(path)
    return row


def _walk(doc, path: tuple):
    for part in path:
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def _read_cell(row: dict, doc, cell: Callable = _cell):
    """The stored value for a row, as the card writes it (a note row has none)."""
    if row["role"] == "note":
        return None
    value = _walk(doc, tuple(row["path"]))
    value = None if _blank(value) else value
    if str(row["new"]).endswith(" (changed)") and row.get("fingerprint") == _fingerprint(value):
        return row["new"]
    return cell(tuple(row["path"]), value)


def _flat(doc, depth: int, skip=()) -> dict[tuple, Any]:
    """The document's leaves by key path; objects open `depth` levels, lists stay leaves."""
    out: dict[tuple, Any] = {}
    for key, value in (doc or {}).items():
        if key in skip:
            continue
        if isinstance(value, dict) and value and depth > 1:
            out.update({(key, *sub): leaf for sub, leaf in _flat(value, depth - 1).items()})
        else:
            out[(key,)] = value
    return out


def _doc_rows(name: str, old_doc, new_doc, label_of: Callable, *, depth: int = 2, skip=(), named=(),
              cell: Callable = _cell, old_extra: dict | None = None, new_extra: dict | None = None) -> list[dict]:
    """A row per leaf that differs, plus a row per leaf the user named; the extras add leaves."""
    before, after = _flat(old_doc, depth, skip), _flat(new_doc, depth, skip)
    before.update(old_extra or {})
    after.update(new_extra or {})
    rows = []
    for path in dict.fromkeys([*before, *after]):
        old, new = before.get(path), after.get(path)
        if _blank(old) and _blank(new) and path not in named:
            continue
        if not _equal(old, new) or path in named:
            rows.append(_doc_row(name, path, label_of(path), old, new, cell))
    return rows


def _cap(rows: list[dict], noun: str) -> list[dict]:
    if len(rows) <= STUDY_LEAF_CAP:
        return rows
    return rows[:STUDY_LEAF_CAP] + [_note("", "", noun, f"{len(rows) - STUDY_LEAF_CAP} more {noun} change.")]


def _changed(rows: list[dict]) -> bool:
    return any(row["role"] != "note" and (row["old"] != row["new"]) for row in rows)


def _acting() -> None:
    """The caller's identity, before anything is read (a caller without one gets the handler's 401)."""
    from services import chat_tools

    with chat_tools._acting():
        pass


def _solver_commercial() -> dict | None:
    from routers.simulation import get_solver_config

    commercial = (get_solver_config() or {}).get("commercial")
    return commercial if isinstance(commercial, dict) else None


# ── set_site_connection: the commercial root (poc_link, export_link, timezone) ──

_SITE_CONNECTION_WORDS = (("poc_link", "Point of connection"), ("export_link", "Export link"),
                          ("timezone", "Site time zone"))


def _site_connection_preview(args: dict) -> list[dict]:
    from services import chat_tools

    _acting()
    poc_link = args.get("poc_link")
    if not isinstance(poc_link, str) or not poc_link.strip():
        _refuse("invalid_edit_value", "Name the Link that is the point of connection (poc_link).")
    unset = chat_tools._UNSET
    sent = {key: args[key] for key in ("export_link", "timezone") if key in args}
    _, body, created = chat_tools._site_connection_plan(poc_link, args.get("export_link", unset),
                                                        args.get("timezone", unset))
    old = {} if created else (_solver_commercial() or {})
    rows = []
    for key, label in _SITE_CONNECTION_WORDS:
        if key == "poc_link" or key in sent:
            rows.append(_settings_row("Site connection", key, label, old.get(key), getattr(body.commercial, key),
                                      says_name=label))
    if not any(row["old"] != row["new"] for row in rows):
        _refuse("empty_edit", "The site connection already has these values.")
    return rows


def _site_connection_readback(args: dict, rows: list[dict]) -> list[Any]:
    stored = _solver_commercial() or {}
    return [_setting(stored.get(row["field"])) for row in rows]


register("set_site_connection", Adapter("settings_edit", _site_connection_preview, _site_connection_readback,
                                        section_id=_SECTION))


# ── attach_tariff: the project's import tariff ──────────────────────────────

def _tariff_cell(commercial: dict | None):
    """The import tariff as the panel names it: the pinned Library item, else the inline copy."""
    commercial = commercial or {}
    ref = commercial.get("import_tariff_ref")
    if isinstance(ref, dict):
        return f"{ref.get('id')} v{ref.get('version')}"
    inline = commercial.get("import_tariff")
    if isinstance(inline, dict) or commercial.get("import_tariff_id"):
        return f"{(inline or {}).get('name') or commercial.get('import_tariff_id')} (inline)"
    return None


def _attach_tariff_preview(args: dict) -> list[dict]:
    from services import chat_tools

    _acting()
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        _refuse("empty_edit", "Name the Library tariff to attach.")
    version = args.get("version")
    # The Library read runs under the caller's organisation, as the handler's does.
    ref, commercial, _ = chat_tools._attach_tariff_plan(name, version, bool(args.get("replace_inline")))
    stored = _solver_commercial() or {}
    old = _tariff_cell(stored)
    if isinstance(stored.get("import_tariff_ref"), dict) and stored["import_tariff_ref"] == ref:
        _refuse("empty_edit", f"Tariff “{name}” version {ref['version']} is already attached.")
    return [_settings_row("Commercial", "import_tariff_ref", "Import tariff", old, _tariff_cell(commercial),
                          says_name="Import tariff")]


def _attach_tariff_readback(args: dict, rows: list[dict]) -> list[Any]:
    return [_tariff_cell(_solver_commercial()) for _ in rows]


register("attach_tariff", Adapter("settings_edit", _attach_tariff_preview, _attach_tariff_readback,
                                  section_id=_SECTION))


# ── import_urdb_tariff: an uploaded URDB rate becomes a Library tariff ──────

def _urdb_plan(args: dict):
    """(Library name, the tariff the upload converts to, the Library's latest version of that
    name and its payload). Nothing is stored. The caller's write access to the Library is
    checked before the upload is read or any old item is."""
    from routers import library
    from services import chat_tools
    from services.commercial import hashing
    from services.library import common
    from services.library import items as items_store
    from services.library import urdb

    with chat_tools._acting() as (db, user):
        org = library._target_org(db, user, None, write=True)
        file_id, name = args.get("file_id"), args.get("name")
        if not isinstance(file_id, str) or not isinstance(name, str):
            _refuse("empty_edit", "Name the uploaded URDB file (file_id) and the Library tariff name.")
        keys = ("cyclic_year", "accept_partial", "valid_from", "item_index", "tariff_id", "jurisdiction")
        body = chat_tools._urdb_request(file_id, name, **{key: args[key] for key in keys if key in args})
        item_name = library._item_name(body.name)
        try:
            tariff, _refusals, _notes = urdb.urdb_to_tariff(
                body.urdb_response, name=item_name, cyclic_year=body.cyclic_year,
                accept_partial=body.accept_partial, tariff_id=body.tariff_id,
                jurisdiction=body.jurisdiction, valid_from=body.valid_from)
        except urdb.UrdbRefused as exc:
            _refuse("urdb_refused", str(exc)[:500], 422)
        except ValueError as exc:
            _refuse("urdb_invalid", str(exc)[:500], 422)
        payload = tariff.model_dump(mode="json")
        try:  # the Library's own refusals of a payload or a shipped name
            model = items_store.validate_payload("tariff", payload)
            if common.is_common("tariff", item_name):
                raise items_store.LibraryItemCommonReadonly(
                    f"tariff “{item_name}” is a shipped common Library item and cannot be overwritten; "
                    "store your own version under another name")
        except ValueError as exc:
            _refuse("urdb_invalid", str(exc)[:500], 422)
        latest = items_store.latest_ref(db, org, "tariff", item_name)
        latest_payload = items_store.resolve(db, org, latest) if latest is not None else None
        if latest is not None and latest.hash == hashing.library_item_digest(model):
            _refuse("empty_edit", f"Library tariff “{item_name}” already holds this rate (version {latest.version}).")
        return item_name, payload, latest, latest_payload


def _tariff_facts(payload: dict | None) -> dict:
    payload = payload or {}
    return {"items": len(payload.get("items") or []), "valid_from": payload.get("valid_from"),
            "unsupported_fields": payload.get("unsupported_fields") or []}


def _urdb_preview(args: dict) -> list[dict]:
    name, payload, latest, latest_payload = _urdb_plan(args)
    label = f"Library tariff · {name}"
    new_version = 1 if latest is None else latest.version + 1
    row = _settings_row("Library tariff", f"tariff.{name}", label, None if latest is None else f"v{latest.version}",
                        f"v{new_version}", says_name=label)
    row["words"] = {"old": "not in the Library" if latest is None else f"v{latest.version}",
                    "new": f"v{new_version} from the upload"}
    before, after = _tariff_facts(latest_payload), _tariff_facts(payload)
    rows = [row]
    for key, text in (("items", "Tariff items"), ("valid_from", "Valid from"),
                      ("unsupported_fields", "URDB fields not mapped")):
        if key == "items" or latest is None and key == "valid_from" or not _equal(before[key], after[key]):
            rows.append(_doc_row("Library tariff", (name, key), f"{label} \u00b7 {text}",
                                 None if latest is None else before[key], after[key]))
    return rows


def _urdb_readback(args: dict, rows: list[dict]) -> list[Any]:
    from routers import library
    from services import chat_tools
    from services.library import items as items_store

    with chat_tools._acting() as (db, user):
        org = library._target_org(db, user, None, write=True)
        name = library._item_name(args["name"])
        latest = items_store.latest_ref(db, org, "tariff", name)
        facts = _tariff_facts(items_store.resolve(db, org, latest) if latest is not None else None)
    return [(None if latest is None else f"v{latest.version}") if "path" not in row
            else _read_cell(row, {name: facts}) for row in rows]


register("import_urdb_tariff", Adapter("settings_edit", _urdb_preview, _urdb_readback, section_id=_SECTION))


# ── set_finance_inputs: the finance case's inputs ───────────────────────────

_FINANCE_WORDS = {
    "currency": "Currency", "currency_year": "Currency year", "price_basis": "Price basis",
    "financial_close": "Financial close", "acquisition_date": "Acquisition date",
    "construction_start": "Construction start", "analysis_years": "Analysis years",
    "capex_phasing": "Capex phasing", "contingency_share": "Contingency", "replacement_rule": "Replacements",
    "replacement_capex": "Replacement capex", "escalation": "Escalation", "cod_by_asset": "COD",
    "degradation_by_asset": "Degradation", "depreciation_class_by_asset": "Depreciation class",
    "construction_months_by_asset": "Construction months", "debt": "Debt", "incentives": "Incentives",
    "tax_equity": "Tax equity", "tax_pack_id": "Tax pack", "hebesatz_pct": "Hebesatz (%)",
    "state_rate": "State / provincial rate", "tax_losses": "Tax losses", "financing_fee_tax": "Financing fee tax",
    "pwa_met": "Prevailing wage and apprenticeship met", "small_business_163j": "Small business (§163(j) exempt)",
    "wacc_nominal": "WACC", "cost_of_equity": "Cost of equity", "inflation": "Inflation",
    "reserves_rate": "Reserves interest rate", "participants": "Participants",
    "terminal_value.method": "Terminal value method", "terminal_value.value": "Terminal value",
    "solve_ppa.contract_id": "PPA contract", "solve_ppa.target_irr": "Target equity IRR (post-tax)",
    "solve_ppa.target_year": "Target year", "island_outage.hours_per_year": "Expected outage hours a year",
    "island_outage.source": "Source of the outage hours",
}


def _finance_label(path: tuple) -> str:
    joined = ".".join(path)
    if joined in _FINANCE_WORDS:
        return _FINANCE_WORDS[joined]
    head = _FINANCE_WORDS.get(path[0], _title(path[0]))
    return head if len(path) == 1 else f"{head} · {path[1]}"


def _stored_finance():
    from routers.simulation import _state

    return getattr(_state["solver_config"], "finance", None)


def _finance_preview(args: dict) -> list[dict]:
    from routers import simulation
    from services import chat_tools
    from services.finance.investment_case_runner import finance_digest, finance_inputs_or_422
    from services.pypsa_service import PyPSAService

    _acting()
    finance = args.get("finance")
    clear = bool(args.get("clear"))
    if_match = args.get("if_match")
    body, tag = chat_tools._finance_request(if_match if isinstance(if_match, str) else "", finance, clear)
    if simulation._solver_in_flight_ctx(PyPSAService.get_active_context()):
        _refuse("solver_in_flight", "A solve is running on this project; change the finance inputs after it "
                "finishes.", 409)
    old = _stored_finance()
    stale = simulation._entity_tag(tag)
    if stale != "*" and stale != finance_digest(old):
        _refuse("finance_changed", "The finance inputs changed since they were read; reload and re-apply the edit.",
                412)
    new = None
    if body.finance is not None:
        try:
            new = finance_inputs_or_422(body.finance).model_dump(mode="json")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            _refuse("invalid_edit_value", str(detail.get("message") or exc.detail)[:300], exc.status_code)
    if new is None:
        if old is None:
            _refuse("empty_edit", "No finance inputs are stored; there is nothing to clear.")
        rows = [_doc_row("Finance inputs", ("finance",), "Finance inputs", old, None)]
    else:
        # A first save names its fields; a replacement shows what differs from the stored inputs.
        if old is None:  # nothing stored yet: the fields the call names (the rest are defaults)
            old_doc, new_doc = {}, {k: v for k, v in new.items() if k in finance}
        else:
            old_doc, new_doc = old, new
        rows = _cap(_doc_rows("Finance inputs", old_doc, new_doc, _finance_label, depth=2), "finance inputs")
    if not _changed(rows):
        _refuse("empty_edit", "The finance inputs already have these values.")
    return rows


def _finance_readback(args: dict, rows: list[dict]) -> list[Any]:
    stored = _stored_finance()
    return [_read_cell(row, {"finance": stored} if row.get("path") == ["finance"] else stored) for row in rows]


register("set_finance_inputs", Adapter("settings_edit", _finance_preview, _finance_readback, section_id=_SECTION))


# ── define_participants: the value-flow config ──────────────────────────────

_VF_WORDS = {"template": "Template", "participants": "Participants", "externals": "External parties",
             "asset_owners": "Asset owners", "tariff_payees": "Who is paid each tariff item",
             "export_revenue_to": "Export revenue goes to", "hub_members": "Energy hub",
             "allocation.basis": "Allocation key", "allocation.shares": "Allocation shares",
             "connection_fee_payee": "Connection fee payee"}
_VF_DERIVED = ("template_version", "built_digest", "built_inputs_digest")


def _vf_keyed(value_flows):
    """The config with its participants and asset owners keyed, so a card row names one."""
    if not isinstance(value_flows, dict):
        return value_flows
    keyed = dict(value_flows)
    if isinstance(keyed.get("participants"), list):
        keyed["participants"] = {str(p.get("id")): p for p in keyed["participants"] if isinstance(p, dict)}
    if isinstance(keyed.get("asset_owners"), list):
        keyed["asset_owners"] = {f"{a.get('component')}:{a.get('asset_id')}": a.get("owner")
                                 for a in keyed["asset_owners"] if isinstance(a, dict)}
    return keyed


def _vf_cell(path: tuple, value):
    if path[0] == "participants" and len(path) == 2 and isinstance(value, dict):
        return f"{value.get('role')} · {value.get('name')}"
    return _cell(path, value)


def _vf_label(path: tuple) -> str:
    joined = ".".join(path)
    if joined in _VF_WORDS:
        return _VF_WORDS[joined]
    if path[0] == "participants" and len(path) == 2:
        return f"Participant · {path[1]}"
    if path[0] == "asset_owners" and len(path) == 2:
        return f"Asset owner · {path[1]}"
    head = _VF_WORDS.get(path[0], _title(path[0]))
    return head if len(path) == 1 else f"{head} · {path[1]}"


def _stored_value_flows():
    commercial = getattr(_stored_state(), "commercial", None)
    return commercial.get("value_flows") if isinstance(commercial, dict) else None


def _stored_state():
    from routers.simulation import _state

    return _state["solver_config"]


def _participants_preview(args: dict) -> list[dict]:
    from routers import simulation
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    _acting()
    kwargs = {key: args[key] for key in ("template", "config", "clear", "replace") if key in args}
    if simulation._solver_in_flight_ctx(PyPSAService.get_active_context()):
        _refuse("solver_in_flight", "A solve is running on this project; change the participants after it "
                "finishes.", 409)
    old = _stored_value_flows()
    result: list[dict] = []
    # The handler on an isolated copy: its template build, its replace guard and the route's own
    # validation all run natively, and the stored config is the copy's after it.
    candidate = _sandbox(lambda: result.append(chat_tools.define_participants(**kwargs)))
    if result and result[0].get("saved") is False:
        _refuse("value_flows_drafts_need_pricing", str(result[0].get("message") or "The template needs contracts "
                "the project does not have."), 409)
    commercial = getattr(candidate.solver_state["solver_config"], "commercial", None)
    new = commercial.get("value_flows") if isinstance(commercial, dict) else None
    named = {("template",)} if kwargs.get("template") else set()
    rows = _doc_rows("Participants", _vf_keyed(old), _vf_keyed(new), _vf_label, depth=2, skip=_VF_DERIVED,
                     named=named, cell=_vf_cell)
    rows = _cap(rows, "participant settings")
    if not _changed(rows):
        _refuse("empty_edit", "The participants already have these values.")
    return rows


def _participants_readback(args: dict, rows: list[dict]) -> list[Any]:
    stored = _vf_keyed(_stored_value_flows())
    return [_read_cell(row, stored, _vf_cell) for row in rows]


register("define_participants", Adapter("settings_edit", _participants_preview, _participants_readback,
                                        section_id=_SECTION))


# ── set_project_sites: the project's site document ──────────────────────────

_SITE_WORDS = {"name": "Name", "buses": "Buses", "boundary": "Boundary", "origin.lng": "Origin longitude",
               "origin.lat": "Origin latitude"}


def _sites_keyed(doc: dict) -> dict:
    keyed = dict(doc)
    keyed["sites"] = {s["id"]: s for s in doc.get("sites") or [] if isinstance(s, dict) and "id" in s}
    return keyed


def _sites_cell(path: tuple, value):
    if value is None:
        return None
    if path[0] == "sites" and len(path) == 2:  # a whole site added or removed
        return ", ".join([_count(len(value.get("boundary") or []), "vertex", "vertices") + " boundary",
                          _count(len(value.get("buses") or []), "bus", "buses"),
                          _count(len(value.get("placements") or {}), "placement")])
    if path[0] == "sites" and len(path) == 4 and path[2] == "placements" and isinstance(value, dict):
        return ", ".join(f"{axis} {_setting(value.get(axis))}" for axis in ("x", "y", "heading"))
    if path[0] == "sites" and len(path) == 3 and path[2] == "boundary" and isinstance(value, list):
        return _count(len(value), "vertex", "vertices")
    return _cell(path, value)


def _sites_label(doc: dict) -> Callable[[tuple], str]:
    names = {sid: site.get("name") or sid for sid, site in doc.items()}

    def label(path: tuple) -> str:
        if path[0] == "sites":
            site = f"Site · {names.get(path[1], path[1])}"
            if len(path) == 2:
                return site
            rest = ".".join(path[2:])
            if path[2] == "placements" and len(path) == 4:
                return f"{site} · Placement {path[3]}"
            return f"{site} · {_SITE_WORDS.get(rest, _title(rest))}"
        if path[0] == "asset_footprints":
            return "Footprint · " + " · ".join(path[1:])
        return _title(".".join(path))

    return label


def _sites_project(args: dict):
    """The project's storage dir, resolved and lock-checked as `PUT .../sites` does it."""
    from routers import projects
    from services import chat_tools, project_registry

    with chat_tools._acting() as (db, user):
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            _refuse("empty_edit", "Name the project whose sites to edit.")
        dest, name = projects._resolve_project_src(name, db, user)
        if not dest.exists():
            _refuse("project_not_found", f"Project '{name}' not found", 404)
        projects._check_project_lock(db, project_registry.find_project(db, user, name), user)
        return dest


def _stored_sites(dest) -> dict:
    from services import site_service

    return site_service.read_sites(dest)


def _sites_preview(args: dict) -> list[dict]:
    from services import site_service

    dest = _sites_project(args)
    sites = args.get("sites")
    try:  # the route's own validation and size cap
        site_service.validate_sites(sites)
        site_service.serialise(sites)
    except site_service.SitesInvalid as exc:
        _refuse("invalid_edit_value", str(exc), 422)
    except site_service.SitesTooLarge as exc:
        _refuse("invalid_edit_value", str(exc), 413)
    old, new = _sites_keyed(_stored_sites(dest)), _sites_keyed(sites)
    # A site only one side has is one row; the sites both have show the values that differ.
    added = {i: s for i, s in new["sites"].items() if i not in old["sites"]}
    removed = {i: s for i, s in old["sites"].items() if i not in new["sites"]}
    both = {k: v for k, v in old.items() if k != "sites"}, {k: v for k, v in new.items() if k != "sites"}
    shared = [i for i in old["sites"] if i in new["sites"]]
    before = {**both[0], "sites": {i: old["sites"][i] for i in shared}}
    after = {**both[1], "sites": {i: new["sites"][i] for i in shared}}
    names = {**{i: s.get("name") for i, s in new["sites"].items()}, **{i: s.get("name") for i, s in old["sites"].items()}}
    rows = _doc_rows("Sites", before, after, _sites_label({i: {"name": n} for i, n in names.items()}),
                     depth=4, skip=("version",), cell=_sites_cell,
                     old_extra={("sites", i): s for i, s in removed.items()},
                     new_extra={("sites", i): s for i, s in added.items()})
    rows = _cap(rows, "site settings")
    if removed:
        rows.append(_note("", "", "sites", "A removed site also loses its cached map context."))
    if not _changed(rows):
        _refuse("empty_edit", "The project already has this site document.")
    return rows


def _sites_readback(args: dict, rows: list[dict]) -> list[Any]:
    stored = _sites_keyed(_stored_sites(_sites_project(args)))
    return [_read_cell(row, stored, _sites_cell) for row in rows]


register("set_project_sites", Adapter("settings_edit", _sites_preview, _sites_readback, section_id="project"))
