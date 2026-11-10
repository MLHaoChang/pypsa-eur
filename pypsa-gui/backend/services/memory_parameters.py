"""Assistant memory slice 03: reusable parameter suggestions (plan §1.1, §2.1, §7.1, §9).

Save for reuse reads a field's value, stored unit, carrier and money stamp from the
bound project itself; the client sends only which field (GK B P1). A suggestion is
offered next to the same field in a later project of the same org (§9 M1) and is
written only when the user applies it on the edit card (COPILOT-1): never silently.

The apply checks again what the save checked (§9 M6), now against the target: the
field still exists, its stored unit is the item's, the money stamp (currency and money
year) is the item's, and the carrier is the item's. Nothing is converted or
relabelled. The card's digest binds the item version and the target's current value
and source, so the user approves exactly what the card showed (#1257).

Refusals are `MemoryError`s with a typed kind; the routes turn them into HTTP errors.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import MemoryItem, Project, User
from services import memory_store as M

MAX_PER_FIELD = 3
SOURCE = "memory:{id}@v{version}"


def _refuse(message: str, kind: str, status: int = 422) -> M.MemoryError:
    return M.MemoryError(message, kind, status)


def is_money(spec) -> bool:
    """A cost field: its stored unit is a money unit (`EUR/MW`, `EUR/MW/yr`, ...)."""
    return spec.unit_storage.split("/", 1)[0] == "EUR"


def _network():
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_network()


def _finance_config():
    from services.pypsa_service import PyPSAService

    try:
        return PyPSAService.get_active_context().solver_state["solver_config"]
    except Exception:  # noqa: BLE001 - no bound config states no money basis
        return None


def _finance():
    from models.finance import FinanceInputs

    raw = getattr(_finance_config(), "finance", None)
    if not raw:
        return None
    try:
        return FinanceInputs.model_validate(raw)
    except Exception:  # noqa: BLE001 - an unusable case states no basis
        return None


def money_stamp(n, component_class: str, name: str, field: str) -> tuple[str | None, int | None]:
    """The currency and money year a cost field's value is in: its provenance entry's,
    else the investment case's currency and money year (else its base year). Either
    part is None when nothing states it; nothing is guessed (GK B P2)."""
    from services.asset_schema import provenance
    from services.library.reference_apply import case_base_year

    entry = provenance.get(n, component_class, name).get(field) or {}
    fin = _finance()
    currency = entry.get("currency") or (fin.currency if fin else None)
    year = entry.get("currency_year") or (fin.currency_year if fin else None)
    if year is None and fin is not None:
        year = case_base_year(n, _finance_config())
    return currency, year


def _frame(n, component_class: str):
    try:
        return n.components[component_class].static
    except Exception:  # noqa: BLE001 - an unknown class is refused by name
        raise _refuse(f"{component_class} is not a component class", "memory_field_unknown") from None


def _current(n, component_class: str, name: str, field: str) -> tuple[float | None, str | None, Any]:
    """The target's current value, its provenance source, and its carrier."""
    from services.asset_schema import provenance

    frame = _frame(n, component_class)
    if name not in frame.index:
        raise _refuse(f"There is no {component_class} named {name}.", "memory_target_unknown", 404)
    raw = frame.at[name, field] if field in frame.columns else None
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        value = None
    if value is not None and not math.isfinite(value):
        value = None
    carrier = frame.at[name, "carrier"] if "carrier" in frame.columns else None
    entry = provenance.get(n, component_class, name).get(field) or {}
    return value, entry.get("source"), (carrier or None)


def bound_project(db: DBSession, user: User, project_id: str) -> Project:
    """The caller's active project, access-checked; 409 when `project_id` is not the
    project this session has open (the body cannot rebind it)."""
    from fastapi import HTTPException

    from services import project_registry
    from services.pypsa_service import PyPSAService

    try:
        project = project_registry.resolve_project(db, user, project_id)
    except HTTPException:
        raise M.MemoryError("not found", "not_found", 404) from None
    if str(project.id) != str(PyPSAService.get_active_context().project_uuid):
        raise _refuse("This project is no longer the one open. Reload it and try again.",
                      "memory_project_not_active", 409)
    return project


def readable_project(db: DBSession, user: User, project_id: str) -> Project:
    """A project the caller may read (suggestions need read access only, GK B P6)."""
    from fastapi import HTTPException

    from services import project_registry

    try:
        return project_registry.resolve_project(db, user, project_id)
    except HTTPException:
        raise M.MemoryError("not found", "not_found", 404) from None


# ── Save for reuse ────────────────────────────────────────────────────────────

def save(db: DBSession, user: User, project: Project, component_class: str, name: str,
         field: str) -> M.ItemView:
    """Save the field's stored value for reuse (§2.1): the click is the confirmation.
    The value, unit, carrier and money stamp come from the project, never the client."""
    spec = M.parameter_spec(component_class, field)
    n = _network()
    value, _source, carrier = _current(n, component_class, name, field)
    if value is None:
        raise _refuse(f"{spec.label} has no value to save.", "memory_value_missing")
    body: dict[str, Any] = {"component_class": component_class, "field": field, "value": value,
                            "unit": spec.unit_storage}
    if carrier:
        body["carrier"] = str(carrier)
    if is_money(spec):
        currency, year = money_stamp(n, component_class, name, field)
        if currency is None or year is None:
            raise _refuse(f"{spec.label}: this project states no currency and money year for it, "
                          "so it is not saved for reuse.", "memory_money_basis_missing")
        body["currency"], body["currency_year"] = currency, year
    evidence = [{"kind": "project", "id": str(project.id), "label": project.name,
                 "at": datetime.now(UTC).isoformat(timespec="seconds")}]
    return M.create_item(
        db, user, kind="parameter", body=body, concept_keys=[f"param:{component_class}.{field}"],
        project_kind=project.project_kind, evidence=evidence, status="confirmed", origin="button",
        org_id=project.org_id, parameter_ok=True,
    )


# ── Suggestions ───────────────────────────────────────────────────────────────

def suggestions(db: DBSession, user: User, project: Project, component_class: str,
                name: str | None = None) -> dict[str, list[dict]]:
    """Confirmed `parameter` items for `component_class`, by field: only items of the
    project's org (§9 M1, user and org items alike), of its project kind or of any, and
    with no carrier or the target's carrier (GK B P3). At most 3 per field, ranked by
    times applied, then last used, then last updated."""
    from sqlalchemy import or_

    from services.asset_schema.fields import known_components

    if component_class not in known_components():
        raise _refuse(f"{component_class} is not a component class", "memory_field_unknown")
    carrier = None
    if name is not None:
        _v, _s, carrier = _current(_network(), component_class, name, "carrier")
    rows = db.scalars(select(MemoryItem).where(
        MemoryItem.kind == "parameter",
        MemoryItem.status == "confirmed",
        MemoryItem.org_id == project.org_id,
        or_(MemoryItem.project_kind.is_(None), MemoryItem.project_kind == project.project_kind),
    )).all()
    out: dict[str, list[tuple]] = {}
    for item in rows:
        if not M._can_read(db, user, item):
            continue
        v = M._version(db, item)
        body = json.loads(v.body_json) if v.body_json else None
        if not body or body.get("component_class") != component_class:
            continue
        if name is not None and body.get("carrier") and body["carrier"] != carrier:
            continue
        evidence = M.evidence_for(db, user, json.loads(v.evidence_json or "[]"))
        source = next((e for e in evidence if e.get("kind") == "project"), None)
        chip = {
            "id": M.public_id(item.id), "version": item.current_version, "scope": item.owner_scope,
            "value": body["value"], "unit": body["unit"], "currency": body.get("currency"),
            "currency_year": body.get("currency_year"), "carrier": body.get("carrier"),
            "saved_at": M._aware(item.updated_at).isoformat(timespec="seconds"),
            "source": None if source is None else {"label": source.get("label"),
                                                   "id": source.get("id"),
                                                   "access": source.get("access", False)},
        }
        rank = (item.apply_count, M._aware(item.last_used_at) or datetime.min.replace(tzinfo=UTC),
                M._aware(item.updated_at))
        out.setdefault(body["field"], []).append((rank, chip))
    return {f: [c for _r, c in sorted(chips, key=lambda rc: rc[0], reverse=True)[:MAX_PER_FIELD]]
            for f, chips in out.items()}


# ── Preview and apply ─────────────────────────────────────────────────────────

def _item_for_apply(db: DBSession, user: User, project: Project, item_id: str, version: int):
    item = M._get(db, user, item_id)
    if item.kind != "parameter":
        raise M._not_found()
    if item.org_id != project.org_id:       # §9 M1: never across orgs
        raise M._not_found()
    if item.status != "confirmed":
        raise _refuse("This saved value was retired or dismissed.", "memory_status", 409)
    if item.current_version != version:
        raise _refuse("This saved value changed since it was shown. Pick it again.", "memory_stale", 409)
    v = M._version(db, item)
    return item, json.loads(v.body_json)


def plan(db: DBSession, user: User, project: Project, item_id: str, version: int,
         component_class: str, name: str) -> dict:
    """What an apply would write, or a typed refusal (§9 M6, GK B P2–P3). Writes nothing."""
    item, body = _item_for_apply(db, user, project, item_id, version)
    if body["component_class"] != component_class:
        raise _refuse(f"This value was saved for a {body['component_class']}, not a {component_class}.",
                      "memory_field_unknown")
    try:
        spec = M.parameter_spec(component_class, body["field"])
    except M.MemoryError as exc:
        raise _refuse(f"{body['component_class']}.{body['field']} is no longer a field of this "
                      "app, so the saved value is not applied.", exc.kind) from None
    if spec.unit_storage != body["unit"]:
        raise _refuse(f"{spec.label}: saved in {body['unit']}, this field is stored in "
                      f"{spec.unit_storage}. Nothing is converted.", "memory_unit_mismatch")
    n = _network()
    current, source, carrier = _current(n, component_class, name, spec.key)
    if body.get("carrier") and body["carrier"] != carrier:
        raise _refuse(f"{spec.label}: saved for {body['carrier']}, this asset is {carrier or 'without a carrier'}.",
                      "memory_carrier_mismatch")
    currency = year = None
    if is_money(spec):
        currency, year = money_stamp(n, component_class, name, spec.key)
        if currency is None or year is None:
            raise _refuse(f"{spec.label}: this project states no currency and money year for it, so a "
                          "saved price is not applied.", "memory_money_basis_missing")
        if body.get("currency") != currency:
            raise _refuse(f"{spec.label}: saved in {body.get('currency')}, this project uses {currency}. "
                          "Nothing is converted.", "currency_mismatch")
        if body.get("currency_year") != year:
            raise _refuse(f"{spec.label}: saved in {body.get('currency_year')} money, this project reads it "
                          f"in {year} money. Nothing is converted or relabelled.", "memory_money_year_mismatch")
    memory = {
        "item": M.public_id(item.id), "version": item.current_version, "field": spec.key,
        "label": spec.label, "value": body["value"], "unit": body["unit"],
        "currency": body.get("currency"), "currency_year": body.get("currency_year"),
        "carrier": body.get("carrier"), "target": {"component_class": component_class, "name": name,
                                                    "value": current, "source": source},
    }
    return {"args": {"component_class": component_class, "name": name,
                     "attrs": {spec.key: body["value"]}},
            "memory": memory, "no_change": current is not None and current == body["value"]}


def digest(card_digest: str, memory: dict) -> str:
    """The approval digest: the edit card's own digest and every memory fact the card
    shows (GK B P4), so a moved item version, value or target refuses the apply."""
    raw = json.dumps({"card": card_digest, "memory": memory}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def preview(db: DBSession, user: User, project: Project, item_id: str, version: int,
            component_class: str, name: str) -> dict:
    """The old → new edit card (`update_component`) with the memory block and its digest."""
    from harness import edit_card

    p = plan(db, user, project, item_id, version, component_class, name)
    if p["no_change"]:
        raise _refuse("This field already has the saved value.", "memory_no_change")
    card = edit_card.preview("update_component", p["args"])
    m = p["memory"]
    note = (f"From memory {m['item']} v{m['version']}: {m['value']:g} {m['unit']}"
            + (f", {m['currency_year']} {m['currency']}" if m["currency"] else "")
            + (f", {m['carrier']}" if m["carrier"] else "")
            + f". Replaces {m['target']['value']} (source: {m['target']['source'] or 'typed'}).")
    return {**card, "memory": m, "memory_note": note, "digest": digest(card["digest"], m),
            "card_digest": card["digest"]}


def apply(db: DBSession, user: User, project: Project, item_id: str, version: int,
          component_class: str, name: str, approved_digest: str) -> dict:
    """Re-plan, refuse a moved digest, write through `update_component` (its validators),
    stamp provenance exactly as §7.1, then bump the item's apply count."""
    from harness import edit_card
    from services import chat_tools
    from services.asset_schema import provenance

    p = plan(db, user, project, item_id, version, component_class, name)
    card = edit_card.preview("update_component", p["args"], with_state=False)
    if digest(card["digest"], p["memory"]) != approved_digest:
        raise _refuse("The card changed since it was shown. Look at it again before applying.",
                      "edit_preview_stale", 409)
    m = p["memory"]
    chat_tools.update_component(component_class, name, p["args"]["attrs"])
    n = _network()
    provenance.record(
        n, component_class, name, m["field"], status="customised",
        source=SOURCE.format(id=m["item"], version=m["version"]),
        currency=m["currency"], currency_year=m["currency_year"],
        approved_by=getattr(user, "email", None),
        approved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        replaces=m["target"]["source"],
    )
    counted = True
    try:
        M.record_apply(db, user, uuid.UUID(m["item"][4:]))
    except Exception:  # noqa: BLE001 - the apply stands; the result says so (GK B P5)
        db.rollback()
        counted = False
    return {"component_class": component_class, "name": name, "written": p["args"]["attrs"],
            "source": SOURCE.format(id=m["item"], version=m["version"]), "memory": m,
            "apply_counted": counted, "receipt": edit_card.receipt(card, p["args"])}
