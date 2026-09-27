"""
Library series store (Edge Investment Case P1 WP1.1a; spec decision 17).

Payload: `<projects_root>/.library/<org_id>/series/<name-slug>-<sha16>.csv.gz`,
a HIDDEN directory — `legacy_migrate._scan_root` skips hidden directories but
offers any other non-UUID directory under the projects root as a claimable
leftover it may MOVE. The org id is always in the path.

Canonical bytes (what the hash covers): a CSV with header
`timestamp_utc,value`, timestamps in ISO-8601 UTC with a `Z` suffix (naive
indexes are written without one and flagged naive in the row's meta), values
formatted `%.10g`. gzip is written with `mtime=0`, so identical content gives
an identical file. The row's `hash` is the sha256 of the UNCOMPRESSED
canonical bytes; `resolve` re-hashes the file and refuses a mismatch.

Access control is the caller's (router / `library_acl`) job; every function
here is scoped by an explicit `org_id`.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from db.models import LibraryItem
from models.commercial import PriceSeriesRef

KIND = "series"
_NAME_MAX = 128


class LibraryRefNotFound(LookupError):
    """No such item/version in this org."""


class LibraryRefStale(ValueError):
    """The ref's hash, or the file's content, no longer matches the row."""


def library_dir(root: Path, org_id: UUID) -> Path:
    return Path(root) / ".library" / str(org_id)


def _default_root() -> Path:
    from settings import get_settings

    return Path(get_settings().projects_root)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:48] or "series"


def _canonical(series: pd.Series) -> tuple[bytes, str | None]:
    idx = series.index
    tz = str(idx.tz) if idx.tz is not None else None
    ts = idx.tz_convert("UTC") if tz else idx
    stamps = ts.strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if tz else "")
    buf = io.StringIO()
    buf.write("timestamp_utc,value\n")
    for t, v in zip(stamps, series.to_numpy(dtype=float)):
        buf.write(f"{t},{v:.10g}\n")
    return buf.getvalue().encode("utf-8"), tz


def _validate(name: str, series: pd.Series) -> None:
    if not name or len(name) > _NAME_MAX:
        raise ValueError(f"series name must be 1..{_NAME_MAX} characters")
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError("series must have a DatetimeIndex")
    if len(series) == 0:
        raise ValueError("series is empty")
    vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(vals).all():
        raise ValueError("series values must all be finite numbers")
    if not series.index.is_monotonic_increasing or series.index.has_duplicates:
        raise ValueError("series index must be strictly increasing")


def _ref(row: LibraryItem) -> PriceSeriesRef:
    meta = json.loads(row.meta_json or "{}")
    return PriceSeriesRef(id=row.name, version=row.version, hash=row.hash,
                          source=meta.get("source") or "user",
                          vintage_year=meta.get("vintage_year"),
                          provider=meta.get("provider"))


def put_series(db: DBSession, org_id: UUID, name: str, series: pd.Series, meta: dict,
               *, created_by: UUID | None = None, root: Path | None = None) -> PriceSeriesRef:
    """Store `series` as `name` in the org's Library; idempotent on content."""
    _validate(name, series)
    data, tz = _canonical(series)
    digest = hashlib.sha256(data).hexdigest()
    latest = db.scalars(
        select(LibraryItem)
        .where(LibraryItem.org_id == org_id, LibraryItem.kind == KIND, LibraryItem.name == name)
        .order_by(LibraryItem.version.desc())
        .limit(1)
    ).first()
    if latest is not None and latest.hash == digest:
        return _ref(latest)
    version = 1 if latest is None else latest.version + 1
    rel = Path("series") / f"{_slug(name)}-{digest[:16]}.csv.gz"
    target = library_dir(root or _default_root(), org_id) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(data, mtime=0))
        tmp.replace(target)
    row = LibraryItem(
        org_id=org_id, kind=KIND, name=name, version=version, hash=digest,
        path=rel.as_posix(),
        meta_json=json.dumps({**{k: v for k, v in meta.items() if k != "tz"}, "tz": tz},
                             sort_keys=True, default=str),
        created_by=created_by, created_at=datetime.now(timezone.utc))
    db.add(row)
    db.commit()
    return _ref(row)


def _row_for(db: DBSession, org_id: UUID, ref: PriceSeriesRef) -> LibraryItem:
    row = db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org_id, LibraryItem.kind == KIND,
        LibraryItem.name == ref.id, LibraryItem.version == ref.version)).first()
    if row is None:
        raise LibraryRefNotFound(f"no series {ref.id!r} v{ref.version} in this org")
    return row


def resolve(db: DBSession, org_id: UUID, ref: PriceSeriesRef, *, root: Path | None = None) -> pd.Series:
    """Return exactly the series `ref` names; refuse any hash mismatch."""
    row = _row_for(db, org_id, ref)
    if ref.hash != row.hash:
        raise LibraryRefStale(f"ref hash for {ref.id!r} v{ref.version} does not match the Library")
    path = library_dir(root or _default_root(), org_id) / row.path
    try:
        data = gzip.decompress(path.read_bytes())
    except FileNotFoundError as exc:
        raise LibraryRefStale(f"Library file for {ref.id!r} v{ref.version} is missing") from exc
    if hashlib.sha256(data).hexdigest() != row.hash:
        raise LibraryRefStale(f"Library file for {ref.id!r} v{ref.version} was modified")
    meta = json.loads(row.meta_json or "{}")
    tz = meta.get("tz")
    frame = pd.read_csv(io.BytesIO(data), dtype={"value": float})
    if tz:
        idx = pd.DatetimeIndex(pd.to_datetime(frame["timestamp_utc"], utc=True)).tz_convert(tz)
    else:
        idx = pd.DatetimeIndex(pd.to_datetime(frame["timestamp_utc"]))
    return pd.Series(frame["value"].to_numpy(dtype=float), index=idx, name=ref.id)


def list_series(db: DBSession, org_id: UUID) -> list[PriceSeriesRef]:
    """Latest version of every series in the org."""
    latest = (select(LibraryItem.name, func.max(LibraryItem.version).label("v"))
              .where(LibraryItem.org_id == org_id, LibraryItem.kind == KIND)
              .group_by(LibraryItem.name).subquery())
    rows = db.scalars(select(LibraryItem).join(
        latest, (LibraryItem.name == latest.c.name) & (LibraryItem.version == latest.c.v))
        .where(LibraryItem.org_id == org_id, LibraryItem.kind == KIND)
        .order_by(LibraryItem.name)).all()
    return [_ref(r) for r in rows]
