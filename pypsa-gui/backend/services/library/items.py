"""
Library items: tariffs, contracts, connection agreements (Edge Investment Case
P2 WP2.4a).

An item is a JSON payload validated against its kind's model and stored as a
content-hashed file under the org's Library directory:
`<projects_root>/.library/<org_id>/items/<kind>/<name-slug>-<sha16>.json`
(`LibraryItem.path` is NOT NULL). The canonical bytes are
`hashing.library_item_canonical(model)` — sorted-key JSON of the model's
non-default fields — so a model that later grows an optional field keeps every
stored hash, and preflight can check an inline copy against its ref without a
database (`hashing.library_item_digest`). Rows share `library_items` with the
series (`kind` is part of the unique key); versions, idempotent PUT and the
race rule are the series store's.

Kinds:
  * `tariff` — a `Tariff`;
  * `connection_agreement` — a `ConnectionAgreement` (its `library_ref`, if any,
    is dropped: an item never names itself);
  * `contract` — `{"type": ppa|cfd|dr|lease|eaas|retail, …}` (the WP2.2a
    discriminator) validated against that contract model, unknown keys
    refused, a `library_ref` dropped.

Access control is the caller's (router / `library_acl`); every function is
scoped by an explicit `org_id`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from db.models import LibraryItem
from models import commercial as M
from services.commercial import hashing as H
from services.library.series_store import LibraryRefNotFound, LibraryRefStale
from services.storage_paths import library_dir

KINDS = ("tariff", "contract", "connection_agreement")
CONTRACT_TYPES: dict[str, type[BaseModel]] = {
    "ppa": M.PpaContract, "cfd": M.CfdContract, "dr": M.DrContract,
    "lease": M.LeaseContract, "eaas": M.EaasContract, "retail": M.RetailContract}
_NAME_MAX = 128
_PUT_RETRIES = 5


class ItemMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = "user"
    provider: str | None = None
    description: str | None = None


def _default_root() -> Path:
    from settings import get_settings

    return Path(get_settings().projects_root)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:48] or "item"


def validate_payload(kind: str, payload: Any) -> BaseModel:
    """The payload as its kind's model, or ValueError with a readable reason."""
    if kind not in KINDS:
        raise ValueError(f"unknown Library item kind {kind!r}; one of {', '.join(KINDS)}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    try:
        if kind == "tariff":
            return M.Tariff.model_validate(payload)
        if kind == "connection_agreement":
            body = {k: v for k, v in payload.items() if k != "library_ref"}
            return M.ConnectionAgreement.model_validate(body)
        ctype = payload.get("type")
        model = CONTRACT_TYPES.get(ctype) if isinstance(ctype, str) else None
        if model is None:
            raise ValueError(f"contract payload needs type, one of "
                             f"{', '.join(CONTRACT_TYPES)}")
        body = {k: v for k, v in payload.items() if k not in ("type", "library_ref")}
        unknown = sorted(set(body) - set(model.model_fields))
        if unknown:
            raise ValueError(f"unknown {ctype} contract field(s): {', '.join(unknown)}")
        return model.model_validate(body)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(x) for x in err.get("loc", ()))
        raise ValueError(f"invalid {kind}: {where + ': ' if where else ''}{err['msg']}") from exc


def _payload_dict(kind: str, model: BaseModel, raw: dict) -> dict:
    """The stored JSON object: the canonical dump (+ the contract type)."""
    body = json.loads(H.library_item_canonical(model))
    if kind == "contract":
        body = {"type": raw["type"], **body}
    return body


def _canonical_bytes(kind: str, model: BaseModel, raw: dict) -> bytes:
    if kind == "contract":
        # The contract type is part of the content (two types can share fields).
        return json.dumps(_payload_dict(kind, model, raw), sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    return H.library_item_canonical(model).encode("utf-8")


def _ref(row: LibraryItem) -> M.LibraryItemRef:
    return M.LibraryItemRef(kind=row.kind, id=row.name, version=row.version, hash=row.hash)


def _latest_row(db: DBSession, org_id: UUID, kind: str, name: str) -> LibraryItem | None:
    return db.scalars(
        select(LibraryItem)
        .where(LibraryItem.org_id == org_id, LibraryItem.kind == kind, LibraryItem.name == name)
        .order_by(LibraryItem.version.desc()).limit(1)).first()


def _write(target: Path, data: bytes, digest: str) -> None:
    """Content-addressed write (unique temp file + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                return
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def put_item(db: DBSession, org_id: UUID, kind: str, name: str, payload: dict,
             meta: dict | None = None, *, created_by: UUID | None = None,
             root: Path | None = None) -> M.LibraryItemRef:
    """Store `payload` as item `name` of `kind`; idempotent on content (the
    series store's race rule: the unique key arbitrates, the loser re-reads)."""
    name = (name or "").strip()
    if not name or len(name) > _NAME_MAX:
        raise ValueError(f"item name must be 1..{_NAME_MAX} characters")
    model = validate_payload(kind, payload)
    try:
        meta_ok = ItemMeta.model_validate(meta or {})
    except ValidationError as exc:
        raise ValueError(f"invalid item meta: {exc.errors()[0]['msg']}") from exc
    data = _canonical_bytes(kind, model, payload)
    digest = hashlib.sha256(data).hexdigest()
    rel = Path("items") / kind / f"{_slug(name)}-{digest[:16]}.json"
    _write(library_dir(root or _default_root(), org_id) / rel, data, digest)
    meta_json = json.dumps(meta_ok.model_dump(), sort_keys=True)
    for _ in range(_PUT_RETRIES):
        latest = _latest_row(db, org_id, kind, name)
        if latest is not None and latest.hash == digest:
            return _ref(latest)
        row = LibraryItem(org_id=org_id, kind=kind, name=name,
                          version=1 if latest is None else latest.version + 1,
                          hash=digest, path=rel.as_posix(), meta_json=meta_json,
                          created_by=created_by, created_at=datetime.now(timezone.utc))
        ref = _ref(row)
        db.add(row)
        try:
            db.commit()
            return ref
        except IntegrityError:
            db.rollback()
            continue
    raise RuntimeError(f"could not store {kind} {name!r} after {_PUT_RETRIES} attempts")


def latest_ref(db: DBSession, org_id: UUID, kind: str, name: str) -> M.LibraryItemRef | None:
    row = _latest_row(db, org_id, kind, name)
    return None if row is None else _ref(row)


def ref_for(db: DBSession, org_id: UUID, kind: str, name: str,
            version: int) -> M.LibraryItemRef | None:
    row = db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org_id, LibraryItem.kind == kind,
        LibraryItem.name == name, LibraryItem.version == version)).first()
    return None if row is None else _ref(row)


def resolve(db: DBSession, org_id: UUID, ref: M.LibraryItemRef, *,
            root: Path | None = None) -> dict:
    """Exactly the payload `ref` names (a JSON object); refuse any mismatch."""
    row = db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org_id, LibraryItem.kind == ref.kind,
        LibraryItem.name == ref.id, LibraryItem.version == ref.version)).first()
    label = f"{ref.kind} {ref.id!r} v{ref.version}"
    if row is None:
        raise LibraryRefNotFound(f"no {label} in this org")
    if ref.hash != row.hash:
        raise LibraryRefStale(f"ref hash for {label} does not match the Library")
    base = library_dir(root or _default_root(), org_id).resolve()
    path = (base / row.path).resolve()
    if not path.is_relative_to(base):
        raise LibraryRefStale(f"Library path for {label} points outside the org's Library")
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise LibraryRefStale(f"Library file for {label} is missing") from exc
    except OSError as exc:
        raise LibraryRefStale(f"Library file for {label} is unreadable "
                              f"({type(exc).__name__})") from exc
    if hashlib.sha256(data).hexdigest() != row.hash:
        raise LibraryRefStale(f"Library file for {label} was modified")
    return json.loads(data)


def list_items(db: DBSession, org_id: UUID, kind: str) -> list[M.LibraryItemRef]:
    """Latest version of every item of `kind` in the org."""
    latest = (select(LibraryItem.name, func.max(LibraryItem.version).label("v"))
              .where(LibraryItem.org_id == org_id, LibraryItem.kind == kind)
              .group_by(LibraryItem.name).subquery())
    rows = db.scalars(select(LibraryItem).join(
        latest, (LibraryItem.name == latest.c.name) & (LibraryItem.version == latest.c.v))
        .where(LibraryItem.org_id == org_id, LibraryItem.kind == kind)
        .order_by(LibraryItem.name)).all()
    return [_ref(r) for r in rows]
