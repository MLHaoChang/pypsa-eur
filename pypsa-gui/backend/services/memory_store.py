"""Assistant memory: the store (assistant-memory plan §1–§5, §9; slice 02a).

One module owns every rule about a memory item: the body schema per kind, the
concept-key grammar, the status machine, who may read or change an item,
recall under the org rule (§9 M1), the revision counters the standing prompt
block is keyed on (M5), nomination and promotion (§5, M2), and the sweeper
that drops a dismissed proposal's body after 30 days (M7).

Nothing here is learned silently (MEMORY-1): every write is the user's click
or card decision, made by the route or tool that calls in.

Every function takes the caller's SQLAlchemy session and commits its own
write. Access failures raise `MemoryError` with ``status=404`` whatever the
reason, so an id cannot be probed (plan §4).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DBSession

from db.models import MemoryItem, MemoryItemVersion, MemoryRevision, OrgMembership, User

KINDS = ("preference", "chart_spec", "parameter", "lesson")
# Kinds this slice writes; `parameter` arrives with slice 03 (its unit check
# on apply belongs there, §9 M6).
WRITABLE_KINDS = ("preference", "chart_spec", "lesson")
# Kinds that hold no project data and so may be recalled in another org's
# project (§9 M1). Everything else needs the bound project's org.
CROSS_ORG_KINDS = frozenset({"preference", "chart_spec"})
STATUSES = ("proposed", "confirmed", "retired", "dismissed")
ORIGINS = ("button", "chat", "repeat", "run_end", "dream", "promoted")
SCOPES = ("user", "org")
PROJECT_KINDS = ("capacity_expansion", "planning_dynamics", "connection")

MAX_BODY_BYTES = 4096
MAX_SUMMARY = 160
MAX_TEXT = 280
MAX_KEYS = 8
MAX_EVIDENCE = 8
DISMISS_DAYS = 30

PREFERENCE_KEYS: dict[str, Any] = {
    "units.power": ("kW", "MW", "GW"),
    "units.energy": ("kWh", "MWh", "GWh", "TWh"),
    "currency": "currency",
    "report.sections": "report_sections",
    "sensitivities.default": "sensitivities",
    "answer.style": ("brief", "detailed"),
}
TOPICS = ("units", "currency", "style", "sensitivity")
NAMESPACES = ("view", "card", "param", "carrier", "step", "report", "topic")
EVIDENCE_KINDS = ("project", "run", "chat", "answer")
LESSON_SITUATION_KEYS = ("step", "asset_class", "carrier", "tool")

# Status actions: action -> (allowed from, to).
TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "confirm": (("proposed",), "confirmed"),
    "dismiss": (("proposed",), "dismissed"),
    "retire": (("confirmed",), "retired"),
    "restore": (("retired",), "confirmed"),
}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_PARAM = re.compile(r"^[A-Z][A-Za-z]{0,47}\.[a-z][a-z0-9_]{0,63}$")


class MemoryError(ValueError):  # noqa: A001 - the module's own error, imported qualified
    """A refused memory operation. ``kind`` is the typed error kind;
    ``status`` is the HTTP status a route returns (404 for every access miss)."""

    def __init__(self, message: str, kind: str = "memory_invalid", status: int = 400):
        super().__init__(message)
        self.kind = kind
        self.status = status


def _not_found() -> MemoryError:
    return MemoryError("memory item not found", "memory_not_found", 404)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes for timezone=True columns.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def public_id(item_id: uuid.UUID) -> str:
    return f"mem_{item_id.hex}"


def parse_id(value: str) -> uuid.UUID:
    """`mem_<hex>` or a bare uuid; anything else reads as not found."""
    raw = value[4:] if isinstance(value, str) and value.startswith("mem_") else value
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        raise _not_found() from None


# --- validation -------------------------------------------------------------


def _currencies() -> frozenset[str]:
    from services.document_values import _CODES

    return _CODES


def _report_sections() -> frozenset[str]:
    from models.energy_hub import REPORT_SECTIONS

    return frozenset(REPORT_SECTIONS)


def _step_ids() -> frozenset[str]:
    from harness.workflows.registry import registry

    return frozenset(s.id for w in registry().values() for s in w.steps)


def _text(value: Any, where: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryError(f"{where} must be non-empty text")
    value = " ".join(value.split())
    if len(value) > limit:
        raise MemoryError(f"{where} is longer than {limit} characters")
    return value


def _closed(
    obj: Any, where: str, allowed: Iterable[str], required: Iterable[str] = ()
) -> dict:
    if not isinstance(obj, dict):
        raise MemoryError(f"{where} must be an object")
    extra = set(obj) - set(allowed)
    if extra:
        raise MemoryError(f"{where} has unknown keys: {', '.join(sorted(extra))}")
    missing = set(required) - set(obj)
    if missing:
        raise MemoryError(f"{where} is missing: {', '.join(sorted(missing))}")
    return obj


def validate_concept_key(key: Any) -> str:
    """One `<namespace>:<id>` key (plan §1.2), checked against its registry."""
    if not isinstance(key, str) or ":" not in key:
        raise MemoryError(f"concept key {key} is not <namespace>:<id>")
    ns, ident = key.split(":", 1)
    ok = False
    if ns == "view":
        from services.result_views import RESULT_VIEWS

        ok = ident in {v.view_id for v in RESULT_VIEWS}
    elif ns == "card":
        from services.result_views import CARDS

        ok = ident in CARDS
    elif ns == "param":
        ok = bool(_PARAM.match(ident))
        if ok:
            from services.chart_spec import _known_classes

            ok = ident.split(".", 1)[0] in _known_classes()
    elif ns == "carrier":
        ok = bool(_ID.match(ident))
    elif ns == "step":
        ok = ident in _step_ids()
    elif ns == "report":
        ok = ident in _report_sections()
    elif ns == "topic":
        ok = ident in TOPICS
    if not ok:
        raise MemoryError(f"concept key {key} is not a known {ns or 'namespace'} id")
    return key


def validate_concept_keys(keys: Any) -> list[str]:
    if not isinstance(keys, list) or not keys:
        raise MemoryError("an item needs 1 to 8 concept keys")
    out = sorted({validate_concept_key(k) for k in keys})
    if len(out) > MAX_KEYS:
        raise MemoryError("an item needs 1 to 8 concept keys")
    return out


def _preference(body: Any) -> dict:
    b = _closed(body, "preference", {"key", "value", "note"}, {"key", "value"})
    key, value = b["key"], b["value"]
    rule = PREFERENCE_KEYS.get(key)
    if rule is None:
        raise MemoryError(f"preference key {key} is not in the registry")
    if isinstance(rule, tuple):
        if value not in rule:
            raise MemoryError(f"{key} must be one of {', '.join(rule)}")
    elif rule == "currency":
        if value not in _currencies():
            raise MemoryError(f"currency {value} is not a supported ISO 4217 code")
    elif rule == "report_sections":
        known = _report_sections()
        if (
            not isinstance(value, list)
            or not value
            or len(value) > len(known)
            or any(v not in known for v in value)
            or len(set(value)) != len(value)
        ):
            raise MemoryError("report.sections must list report section ids, each once")
    elif rule == "sensitivities":
        if not isinstance(value, list) or not 1 <= len(value) <= 8:
            raise MemoryError("sensitivities.default must list 1 to 8 entries")
        clean = []
        for i, e in enumerate(value):
            e = _closed(
                e,
                f"sensitivities.default[{i}]",
                {"parameter", "pct"},
                {"parameter", "pct"},
            )
            if not str(e["parameter"]).startswith("param:"):
                raise MemoryError("a sensitivity names a param: concept key")
            validate_concept_key(e["parameter"])
            pct = e["pct"]
            if (
                isinstance(pct, bool)
                or not isinstance(pct, (int, float))
                or not 0 < pct <= 100
            ):
                raise MemoryError("a sensitivity's pct is a number in (0, 100]")
            clean.append({"parameter": e["parameter"], "pct": pct})
        value = clean
    out = {"key": key, "value": value}
    if "note" in b:
        out["note"] = _text(b["note"], "note")
    return out


def _chart_spec(body: Any) -> dict:
    from services import chart_spec

    b = _closed(body, "chart_spec", {"spec"}, {"spec"})
    try:
        return {"spec": chart_spec.validate(b["spec"])}
    except chart_spec.ChartSpecError as exc:
        raise MemoryError(str(exc), exc.kind) from None


def _lesson(body: Any) -> dict:
    b = _closed(body, "lesson", {"text", "situation"}, {"text"})
    out: dict[str, Any] = {"text": _text(b["text"], "lesson text")}
    sit = _closed(b.get("situation", {}), "situation", LESSON_SITUATION_KEYS)
    clean = {}
    for k, v in sit.items():
        if not isinstance(v, str) or not _ID.match(v):
            raise MemoryError(f"situation.{k} must be an id")
        clean[k] = v
    if clean:
        out["situation"] = clean
    return out


_BODIES = {"preference": _preference, "chart_spec": _chart_spec, "lesson": _lesson}


def validate_body(kind: str, body: Any) -> dict:
    """The normalised body for `kind`, or MemoryError. ≤ 4 KB serialised."""
    if kind not in KINDS:
        raise MemoryError(f"kind must be one of {', '.join(KINDS)}")
    if kind not in WRITABLE_KINDS:
        raise MemoryError(
            f"{kind} items arrive in a later slice", "memory_kind_unavailable"
        )
    out = _BODIES[kind](body)
    if len(json.dumps(out, sort_keys=True).encode()) > MAX_BODY_BYTES:
        raise MemoryError("the body is larger than 4 KB")
    return out


def default_summary(kind: str, body: dict) -> str:
    if kind == "preference":
        v = body["value"]
        shown = (
            ", ".join(
                (
                    f"{e['parameter'][6:]} ±{e['pct']}%"
                    if isinstance(e, dict)
                    else str(e)
                )
                for e in v
            )
            if isinstance(v, list)
            else str(v)
        )
        line = f"{body['key']}: {shown}"
    elif kind == "chart_spec":
        from services import chart_spec

        line = chart_spec.summary(body["spec"])
    else:
        line = body["text"]
    return line if len(line) <= MAX_SUMMARY else line[: MAX_SUMMARY - 1] + "…"


def validate_evidence(evidence: Any) -> list[dict]:
    if evidence is None:
        return []
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        raise MemoryError("evidence is a list of at most 8 links")
    out = []
    for i, e in enumerate(evidence):
        e = _closed(e, f"evidence[{i}]", {"kind", "id", "label", "at"}, {"kind", "id"})
        if e["kind"] not in EVIDENCE_KINDS:
            raise MemoryError(
                f"evidence[{i}].kind must be one of {', '.join(EVIDENCE_KINDS)}"
            )
        link = {"kind": e["kind"], "id": _text(str(e["id"]), f"evidence[{i}].id", 128)}
        if e.get("label") is not None:
            link["label"] = _text(e["label"], f"evidence[{i}].label", 120)
        if e.get("at") is not None:
            link["at"] = _text(str(e["at"]), f"evidence[{i}].at", 40)
        out.append(link)
    return out


def proposal_hash(kind: str, concept_keys: list[str], body: dict) -> str:
    """sha256 of (kind, sorted keys, normalised body): a dismissed proposal's
    fingerprint, kept after its body is dropped (§1, §2.4)."""
    raw = json.dumps(
        [kind, sorted(concept_keys), body], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# --- concepts from the screen ------------------------------------------------


def concepts_for(ui_context: Any) -> set[str]:
    """The concept keys the screen is about (§1.2), from the same allow-listed
    ui-context fields `_format_ui_context` reads. Never reads free text, and
    returns only keys that pass `validate_concept_key`."""
    if not isinstance(ui_context, dict):
        return set()
    raw: list[str] = []
    tab = ui_context.get("results_tab")
    if isinstance(tab, str):
        from services.result_views import for_results_tab

        view = for_results_tab(tab)
        if view is not None:
            raw.append(f"view:{view.view_id}")
    card = ui_context.get("results_card")
    if isinstance(card, str):
        raw.append(f"card:{card}")
    if ui_context.get("ui_mode") == "guided" and isinstance(
        ui_context.get("guided_step"), str
    ):
        raw.append(f"step:{ui_context['guided_step']}")
    selected = ui_context.get("selected_component")
    if isinstance(selected, dict):
        carrier = selected.get("carrier")
        if isinstance(carrier, str):
            raw.append(f"carrier:{carrier}")
        klass, field = selected.get("class"), selected.get("field")
        if isinstance(klass, str) and isinstance(field, str):
            raw.append(f"param:{klass}.{field}")
    out = set()
    for key in raw:
        try:
            out.add(validate_concept_key(key))
        except Exception:  # noqa: BLE001 - an unknown screen value is just no key
            continue
    return out


# --- access -------------------------------------------------------------------


def _membership(db: DBSession, user: User) -> OrgMembership | None:
    return db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id))


def org_of(db: DBSession, user: User) -> uuid.UUID | None:
    m = _membership(db, user)
    return m.org_id if m is not None else None


def is_admin_of(db: DBSession, user: User, org_id: uuid.UUID) -> bool:
    """An `OrgMembership` admin of exactly `org_id` (§9 M2), never "any admin"."""
    m = _membership(db, user)
    return m is not None and m.org_id == org_id and m.role == "admin"


def _can_read(db: DBSession, user: User, item: MemoryItem) -> bool:
    if item.owner_scope == "user":
        if item.user_id == user.id:
            return True
        # An org admin sees a member's item only while it is nominated to that
        # org; a declined one is the member's alone again (§9 M2).
        return item.nomination == "pending" and is_admin_of(db, user, item.org_id)
    return org_of(db, user) == item.org_id


def _can_write(db: DBSession, user: User, item: MemoryItem) -> bool:
    if item.owner_scope == "user":
        return item.user_id == user.id
    return is_admin_of(db, user, item.org_id)


def _get(
    db: DBSession, user: User, item_id: str | uuid.UUID, *, write: bool = False
) -> MemoryItem:
    iid = item_id if isinstance(item_id, uuid.UUID) else parse_id(item_id)
    item = db.get(MemoryItem, iid)
    if (
        item is None
        or not _can_read(db, user, item)
        or (write and not _can_write(db, user, item))
    ):
        raise _not_found()
    return item


# --- revisions ----------------------------------------------------------------


def _scope_key(item: MemoryItem) -> str:
    return (
        f"user:{item.user_id}" if item.owner_scope == "user" else f"org:{item.org_id}"
    )


def _bump(db: DBSession, scope_key: str) -> None:
    row = db.get(MemoryRevision, scope_key)
    if row is None:
        db.add(MemoryRevision(scope_key=scope_key, revision=1))
    else:
        row.revision += 1


def revisions(db: DBSession, user: User, org_id: uuid.UUID | None) -> tuple[int, int]:
    """(user revision, org revision): the standing block's cache key parts (M5)."""
    u = db.get(MemoryRevision, f"user:{user.id}")
    o = db.get(MemoryRevision, f"org:{org_id}") if org_id is not None else None
    return (u.revision if u else 0, o.revision if o else 0)


# --- reads --------------------------------------------------------------------


@dataclass(frozen=True)
class ItemView:
    """One item with its live version, as routes, tools and blocks read it."""

    id: uuid.UUID
    kind: str
    owner_scope: str
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    project_kind: str | None
    concept_keys: tuple[str, ...]
    status: str
    origin: str
    version: int
    summary: str
    body: dict | None
    evidence: tuple[dict, ...]
    nomination: str | None
    promoted_from: uuid.UUID | None
    recall_count: int
    apply_count: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def public_id(self) -> str:
        return public_id(self.id)


def _version(
    db: DBSession, item: MemoryItem, version: int | None = None
) -> MemoryItemVersion:
    return db.scalar(
        select(MemoryItemVersion).where(
            MemoryItemVersion.item_id == item.id,
            MemoryItemVersion.version == (version or item.current_version),
        )
    )


def _view(db: DBSession, item: MemoryItem) -> ItemView:
    v = _version(db, item)
    return ItemView(
        id=item.id,
        kind=item.kind,
        owner_scope=item.owner_scope,
        org_id=item.org_id,
        user_id=item.user_id,
        project_kind=item.project_kind,
        concept_keys=tuple(json.loads(item.concept_keys)),
        status=item.status,
        origin=item.origin,
        version=item.current_version,
        summary=v.summary,
        body=json.loads(v.body_json) if v.body_json is not None else None,
        evidence=tuple(json.loads(v.evidence_json)),
        nomination=item.nomination,
        promoted_from=item.promoted_from,
        recall_count=item.recall_count,
        apply_count=item.apply_count,
        last_used_at=_aware(item.last_used_at),
        created_at=_aware(item.created_at),
        updated_at=_aware(item.updated_at),
    )


def get_item(db: DBSession, user: User, item_id: str | uuid.UUID) -> ItemView:
    return _view(db, _get(db, user, item_id))


def list_items(
    db: DBSession,
    user: User,
    *,
    kind: str | None = None,
    status: str | None = "confirmed",
    scope: str | None = None,
    nominated: bool = False,
) -> list[ItemView]:
    """The items `user` may see: their own, their org's, and, for an org admin
    with ``nominated``, the members' items nominated to that org (§5)."""
    org_id = org_of(db, user)
    if nominated:
        if org_id is None or not is_admin_of(db, user, org_id):
            return []
        cond = (
            (MemoryItem.owner_scope == "user")
            & (MemoryItem.org_id == org_id)
            & (MemoryItem.nomination == "pending")
        )
    else:
        own = (MemoryItem.owner_scope == "user") & (MemoryItem.user_id == user.id)
        org = (MemoryItem.owner_scope == "org") & (MemoryItem.org_id == org_id)
        if scope == "user":
            cond = own
        elif scope == "org":
            cond = org
        else:
            cond = or_(own, org) if org_id is not None else own
    q = select(MemoryItem).where(cond)
    if kind:
        q = q.where(MemoryItem.kind == kind)
    if status:
        q = q.where(MemoryItem.status == status)
    q = q.order_by(MemoryItem.updated_at.desc(), MemoryItem.id)
    return [_view(db, i) for i in db.scalars(q)]


def versions(db: DBSession, user: User, item_id: str | uuid.UUID) -> list[dict]:
    item = _get(db, user, item_id)
    rows = db.scalars(
        select(MemoryItemVersion)
        .where(MemoryItemVersion.item_id == item.id)
        .order_by(MemoryItemVersion.version)
    )
    return [
        {
            "version": r.version,
            "summary": r.summary,
            "body": json.loads(r.body_json) if r.body_json is not None else None,
            "evidence": json.loads(r.evidence_json),
            "edited_by": str(r.edited_by) if r.edited_by else None,
            "edited_at": _aware(r.edited_at).isoformat(),
        }
        for r in rows
    ]


# --- writes -------------------------------------------------------------------


def _write_version(db, item, *, version, body, summary, evidence, actor, now) -> None:
    db.add(
        MemoryItemVersion(
            item_id=item.id,
            version=version,
            body_json=json.dumps(body, sort_keys=True),
            summary=summary,
            evidence_json=json.dumps(evidence),
            edited_by=actor.id,
            edited_at=now,
        )
    )


def _checked(kind, body, concept_keys, summary, evidence, project_kind):
    body = validate_body(kind, body)
    keys = validate_concept_keys(concept_keys)
    summary = (
        _text(summary, "summary", MAX_SUMMARY)
        if summary
        else default_summary(kind, body)
    )
    if project_kind is not None and project_kind not in PROJECT_KINDS:
        raise MemoryError(
            f"project_kind must be one of {', '.join(PROJECT_KINDS)} or null"
        )
    return body, keys, summary, validate_evidence(evidence)


def create_item(
    db: DBSession,
    user: User,
    *,
    kind: str,
    body: Any,
    concept_keys: Any,
    project_kind: str | None = None,
    summary: str | None = None,
    evidence: Any = None,
    status: str = "confirmed",
    origin: str = "button",
    org_id: uuid.UUID | None = None,
) -> ItemView:
    """Write a user's own item. A confirmed `button` save on the same
    (owner, org, kind, concept keys, project kind) is a new version of the
    existing item, not a second item (§2.1).

    `org_id` is the org the item recalls in (§9 M1); it defaults to the user's
    membership org. A caller saving from a bound project passes that project's
    org for kinds outside `CROSS_ORG_KINDS`."""
    if status not in ("proposed", "confirmed"):
        raise MemoryError("a new item is proposed or confirmed")
    if origin not in ORIGINS or origin == "promoted":
        raise MemoryError("unknown origin")
    home_org = org_of(db, user)
    if home_org is None:
        raise MemoryError(
            "memory needs an organisation membership", "memory_no_org", 403
        )
    if org_id is None or kind in CROSS_ORG_KINDS:
        org_id = home_org
    body, keys, summary, evidence = _checked(
        kind, body, concept_keys, summary, evidence, project_kind
    )
    if origin == "button" and status == "confirmed":
        existing = db.scalar(
            select(MemoryItem).where(
                MemoryItem.owner_scope == "user",
                MemoryItem.user_id == user.id,
                MemoryItem.org_id == org_id,
                MemoryItem.kind == kind,
                MemoryItem.concept_keys == json.dumps(keys),
                MemoryItem.status == "confirmed",
                MemoryItem.project_kind.is_(None)
                if project_kind is None
                else MemoryItem.project_kind == project_kind,
            )
        )
        if existing is not None:
            return edit_item(
                db, user, existing.id, body=body, summary=summary, evidence=evidence
            )
    now = _now()
    item = MemoryItem(
        id=uuid.uuid4(),
        kind=kind,
        owner_scope="user",
        user_id=user.id,
        org_id=org_id,
        project_kind=project_kind,
        concept_keys=json.dumps(keys),
        status=status,
        current_version=1,
        origin=origin,
        recall_count=0,
        apply_count=0,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    _write_version(
        db,
        item,
        version=1,
        body=body,
        summary=summary,
        evidence=evidence,
        actor=user,
        now=now,
    )
    _bump(db, _scope_key(item))
    db.commit()
    return _view(db, item)


def edit_item(
    db: DBSession,
    user: User,
    item_id: str | uuid.UUID,
    *,
    body: Any = None,
    summary: str | None = None,
    evidence: Any = None,
    concept_keys: Any = None,
    project_kind: Any = ...,
) -> ItemView:
    """Write version n+1 and move `current_version`; old versions stay (§1).
    A dismissed item is not edited."""
    item = _get(db, user, item_id, write=True)
    if item.status == "dismissed":
        raise MemoryError("a dismissed proposal is not edited", "memory_status", 409)
    cur = _version(db, item)
    keys = json.loads(item.concept_keys) if concept_keys is None else concept_keys
    pk = item.project_kind if project_kind is ... else project_kind
    new_body = json.loads(cur.body_json) if body is None else body
    new_summary = (
        summary if summary is not None else (cur.summary if body is None else None)
    )
    new_evidence = json.loads(cur.evidence_json) if evidence is None else evidence
    new_body, keys, new_summary, new_evidence = _checked(
        item.kind, new_body, keys, new_summary, new_evidence, pk
    )
    now = _now()
    item.current_version += 1
    item.concept_keys = json.dumps(keys)
    item.project_kind = pk
    item.updated_at = now
    _write_version(
        db,
        item,
        version=item.current_version,
        body=new_body,
        summary=new_summary,
        evidence=new_evidence,
        actor=user,
        now=now,
    )
    _bump(db, _scope_key(item))
    db.commit()
    return _view(db, item)


def transition(
    db: DBSession, user: User, item_id: str | uuid.UUID, action: str
) -> ItemView:
    """Confirm | dismiss | retire | restore (§1.3)."""
    if action not in TRANSITIONS:
        raise MemoryError(f"action must be one of {', '.join(TRANSITIONS)}")
    item = _get(db, user, item_id, write=True)
    allowed, to = TRANSITIONS[action]
    if item.status not in allowed:
        raise MemoryError(
            f"a {item.status} item cannot be {action}ed", "memory_status", 409
        )
    if action == "dismiss":
        v = _version(db, item)
        item.dismissed_hash = proposal_hash(
            item.kind, json.loads(item.concept_keys), json.loads(v.body_json)
        )
    if action == "retire" and item.nomination == "pending":
        item.nomination = None
    item.status = to
    item.updated_at = _now()
    _bump(db, _scope_key(item))
    db.commit()
    return _view(db, item)


def dismiss_proposal(
    db: DBSession,
    user: User,
    *,
    kind: str,
    body: Any,
    concept_keys: Any,
    project_kind: str | None = None,
    summary: str | None = None,
    evidence: Any = None,
) -> tuple[ItemView, bool]:
    """Record "Not now" on a memory card (§2.2, §2.4) in one commit: the item
    is written already `dismissed` with its `dismissed_hash`, so a failure
    leaves no stray `proposed` row. The same proposal dismissed again by the
    same user refreshes that row's 30-day window instead of adding a second
    one. Returns the item and whether it was new."""
    home_org = org_of(db, user)
    if home_org is None:
        raise MemoryError(
            "memory needs an organisation membership", "memory_no_org", 403
        )
    body, keys, summary, evidence = _checked(
        kind, body, concept_keys, summary, evidence, project_kind
    )
    h = proposal_hash(kind, sorted(keys), body)
    now = _now()
    existing = db.scalar(
        select(MemoryItem).where(
            MemoryItem.user_id == user.id,
            MemoryItem.status == "dismissed",
            MemoryItem.dismissed_hash == h,
        )
    )
    if existing is not None:
        existing.updated_at = now
        db.commit()
        return _view(db, existing), False
    item = MemoryItem(
        id=uuid.uuid4(),
        kind=kind,
        owner_scope="user",
        user_id=user.id,
        org_id=home_org,
        project_kind=project_kind,
        concept_keys=json.dumps(keys),
        status="dismissed",
        dismissed_hash=h,
        current_version=1,
        origin="chat",
        recall_count=0,
        apply_count=0,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    _write_version(
        db,
        item,
        version=1,
        body=body,
        summary=summary,
        evidence=evidence,
        actor=user,
        now=now,
    )
    _bump(db, _scope_key(item))
    db.commit()
    return _view(db, item), True


def recently_dismissed(
    db: DBSession,
    user: User,
    kind: str,
    concept_keys: list[str],
    body: dict,
    now: datetime | None = None,
) -> bool:
    """True when `user` dismissed this exact proposal less than 30 days ago."""
    h = proposal_hash(kind, sorted(concept_keys), body)
    cutoff = (now or _now()) - timedelta(days=DISMISS_DAYS)
    for row in db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user.id,
            MemoryItem.status == "dismissed",
            MemoryItem.dismissed_hash == h,
        )
    ):
        if _aware(row.updated_at) > cutoff:
            return True
    return False


def sweep_dismissed(db: DBSession, now: datetime | None = None) -> int:
    """Drop the body of every proposal dismissed 30 days ago or more, keeping
    its `dismissed_hash` and summary-free row (§1.3, §9 M7). Returns how many
    items it cleared; running it twice clears nothing more."""
    cutoff = (now or _now()) - timedelta(days=DISMISS_DAYS)
    cleared = 0
    for item in db.scalars(select(MemoryItem).where(MemoryItem.status == "dismissed")):
        if _aware(item.updated_at) > cutoff:
            continue
        rows = list(
            db.scalars(
                select(MemoryItemVersion).where(
                    MemoryItemVersion.item_id == item.id,
                    MemoryItemVersion.body_json.is_not(None),
                )
            )
        )
        if not rows:
            continue
        for r in rows:
            r.body_json = None
            r.summary = "(dismissed proposal)"
            r.evidence_json = "[]"
        cleared += 1
    db.commit()
    return cleared


def record_apply(db: DBSession, user: User, item_id: str | uuid.UUID) -> None:
    """The user applied the item: counters only, no revision bump (M5)."""
    item = _get(db, user, item_id)
    item.apply_count += 1
    item.last_used_at = _now()
    db.commit()


def record_recall(db: DBSession, item_ids: Iterable[uuid.UUID]) -> None:
    """Bump `recall_count` (the caller sends each item once per session, §3.3).
    Counters only, no revision bump (M5)."""
    ids = list(item_ids)
    if not ids:
        return
    for item in db.scalars(select(MemoryItem).where(MemoryItem.id.in_(ids))):
        item.recall_count += 1
    db.commit()


# --- org promotion (§5, §9 M2) --------------------------------------------------


def nominate(db: DBSession, user: User, item_id: str | uuid.UUID) -> ItemView:
    item = _get(db, user, item_id, write=True)
    if item.owner_scope != "user" or item.status != "confirmed":
        raise MemoryError(
            "only your own confirmed item can be suggested", "memory_status", 409
        )
    if item.nomination == "pending":
        raise MemoryError(
            "already suggested to your organisation", "memory_status", 409
        )
    if item.org_id != org_of(db, user):
        # Saved in another org's project (§9 M1): your own org's admins must not
        # receive another org's lesson.
        raise MemoryError(
            "this item belongs to another organisation's project", "memory_status", 409
        )
    item.nomination = "pending"
    item.updated_at = _now()
    db.commit()
    return _view(db, item)


def _nominated_for_admin(db: DBSession, admin: User, item_id) -> MemoryItem:
    iid = item_id if isinstance(item_id, uuid.UUID) else parse_id(item_id)
    item = db.get(MemoryItem, iid)
    if (
        item is None
        or item.owner_scope != "user"
        or item.nomination != "pending"
        or not is_admin_of(db, admin, item.org_id)
    ):
        raise _not_found()
    return item


def promote(
    db: DBSession,
    admin: User,
    item_id: str | uuid.UUID,
    *,
    body: Any = None,
    summary: str | None = None,
) -> ItemView:
    """Copy a nominated item to a new org item of the item's own org; the
    member's item stays theirs. Checked against `item.org_id` only (M2)."""
    src = _nominated_for_admin(db, admin, item_id)
    v = _version(db, src)
    keys = json.loads(src.concept_keys)
    new_body = json.loads(v.body_json) if body is None else body
    new_summary = (
        summary if summary is not None else (v.summary if body is None else None)
    )
    new_body, keys, new_summary, evidence = _checked(
        src.kind,
        new_body,
        keys,
        new_summary,
        json.loads(v.evidence_json),
        src.project_kind,
    )
    now = _now()
    item = MemoryItem(
        id=uuid.uuid4(),
        kind=src.kind,
        owner_scope="org",
        user_id=None,
        org_id=src.org_id,
        project_kind=src.project_kind,
        concept_keys=json.dumps(keys),
        status="confirmed",
        current_version=1,
        origin="promoted",
        promoted_from=src.id,
        recall_count=0,
        apply_count=0,
        created_by=admin.id,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    _write_version(
        db,
        item,
        version=1,
        body=new_body,
        summary=new_summary,
        evidence=evidence,
        actor=admin,
        now=now,
    )
    src.nomination = None
    _bump(db, _scope_key(item))
    db.commit()
    return _view(db, item)


def decline(db: DBSession, admin: User, item_id: str | uuid.UUID) -> ItemView:
    src = _nominated_for_admin(db, admin, item_id)
    src.nomination = "declined"
    db.commit()
    return _view(db, src)


# --- recall (§3.3, §9 M1) --------------------------------------------------------


def recall(
    db: DBSession,
    user: User,
    *,
    project_org_id: uuid.UUID | None,
    project_kind: str | None,
    kinds: Iterable[str] | None = None,
    concept_keys: Iterable[str] | None = None,
    query: str | None = None,
    screen: Iterable[str] = (),
    require_concept: bool = False,
    snapshot: dict | None = None,
) -> list[ItemView]:
    """Confirmed items for a turn bound to a project of `project_org_id` and
    `project_kind` (None, None when unbound), ranked (§3.3).

    M1: only `preference` and `chart_spec` items of the user's own may cross
    into another org's project; every org item and every other kind needs the
    project's org. Unbound, only the user's own cross-org kinds with no
    project kind are eligible (§3.4).

    `snapshot` maps item id → (apply_count, last_used_at, updated_at): when
    given, ranking reads it instead of the live counters (M5); items it lacks
    are added to it."""
    org_id = org_of(db, user)
    own = (MemoryItem.owner_scope == "user") & (MemoryItem.user_id == user.id)
    cond = own
    if org_id is not None and project_org_id == org_id:
        cond = or_(
            own, (MemoryItem.owner_scope == "org") & (MemoryItem.org_id == org_id)
        )
    q = select(MemoryItem).where(cond, MemoryItem.status == "confirmed")
    if project_org_id is None:
        q = q.where(MemoryItem.project_kind.is_(None))
    else:
        pk = project_kind or "capacity_expansion"
        q = q.where(
            or_(MemoryItem.project_kind.is_(None), MemoryItem.project_kind == pk)
        )
    if kinds is not None:
        q = q.where(MemoryItem.kind.in_(list(kinds)))
    want = set(concept_keys or ())
    screen = set(screen)
    folded = query.casefold() if query else None
    rows: list[ItemView] = []
    for item in db.scalars(q):
        if item.org_id != project_org_id and item.kind not in CROSS_ORG_KINDS:
            continue
        if item.org_id != project_org_id and item.owner_scope != "user":
            continue
        view = _view(db, item)
        if want and not want & set(view.concept_keys):
            continue
        if require_concept and not screen & set(view.concept_keys):
            continue
        if folded and folded not in view.summary.casefold():
            continue
        rows.append(view)

    def stats(v: ItemView):
        if snapshot is not None:
            if v.id not in snapshot:
                snapshot[v.id] = (v.apply_count, v.last_used_at, v.updated_at)
            return snapshot[v.id]
        return (v.apply_count, v.last_used_at, v.updated_at)

    epoch = datetime(1970, 1, 1, tzinfo=UTC)

    def rank(v: ItemView):
        applies, last_used, updated = stats(v)
        return (
            -len(screen & set(v.concept_keys)),
            0 if v.owner_scope == "user" else 1,
            0 if v.project_kind is not None else 1,
            -applies,
            -(last_used or epoch).timestamp(),
            -(updated or epoch).timestamp(),
            v.id.hex,
        )

    rows.sort(key=rank)
    # Rule 2: a user's own item wins its concept keys; the org item for the
    # same kind and key is dropped, not shown twice.
    taken: set[tuple[str, str]] = set()
    for v in rows:
        if v.owner_scope == "user":
            taken |= {(v.kind, k) for k in v.concept_keys}
    return [
        v
        for v in rows
        if v.owner_scope == "user"
        or not any((v.kind, k) in taken for k in v.concept_keys)
    ]


def evidence_for(db: DBSession, user: User, evidence: Iterable[dict]) -> list[dict]:
    """Evidence links as `user` may see them (§1.4, §9 M1): a project link whose
    project the user cannot open keeps no label and reads "source project
    (no access)". Evidence never grants access."""
    from db.models import Project
    from services.project_acl import can_access_project

    out = []
    for e in evidence:
        e = dict(e)
        if e.get("kind") == "project":
            try:
                project = db.get(Project, uuid.UUID(str(e.get("id"))))
            except (ValueError, TypeError):
                project = None
            if project is None or not can_access_project(db, user, project):
                e = {
                    "kind": "project",
                    "id": None,
                    "label": "source project (no access)",
                    "at": e.get("at"),
                    "access": False,
                }
            else:
                e["access"] = True
        out.append(e)
    return out
