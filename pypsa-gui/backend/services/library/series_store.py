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
import os
import re
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from db.models import LibraryItem
from models.commercial import PriceSeriesRef
from services.storage_paths import library_dir  # noqa: F401  (re-exported for callers)

KIND = "series"
_NAME_MAX = 128


class LibraryRefNotFound(LookupError):
    """No such item/version in this org."""


class LibraryRefStale(ValueError):
    """The ref's hash, or the file's content, no longer matches the row."""


class SeriesMeta(BaseModel):
    """What a caller may attach to a series. Validated BEFORE anything is
    written: a meta that cannot build a ref would otherwise commit a row that
    breaks `list_series` for the whole org (review WP1.1a #1)."""

    model_config = ConfigDict(extra="forbid")
    source: str = "user"
    vintage_year: int | None = None
    provider: str | None = None
    description: str | None = None
    # Meter data (P2 WP2.4b-ii): the settlement its peaks were measured on,
    # the unit, and what the import noted (incomplete months, …).
    settlement: str | None = None
    unit: str | None = None
    source_unit: str | None = None
    label: str | None = None
    notes: list[str] = []


_PUT_RETRIES = 5


def _default_root() -> Path:
    from settings import get_settings

    return Path(get_settings().projects_root)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:48] or "series"


def _canonical(series: pd.Series) -> tuple[bytes, str | None]:
    """Canonical bytes. The first line records the timezone, so the same
    instants in another zone are a different item (resolve returns exactly
    what was put). Values keep 10 significant digits (`%.10g`, the STORED
    precision) and `-0.0` is normalised to `0`."""
    idx = series.index
    tz = str(idx.tz) if idx.tz is not None else None
    ts = idx.tz_convert("UTC") if tz else idx
    stamps = ts.strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if tz else "")
    buf = io.StringIO()
    buf.write(f"# tz={tz or 'naive'}\n")
    buf.write("timestamp_utc,value\n")
    for t, v in zip(stamps, series.to_numpy(dtype=float) + 0.0):
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
    # as_unit("ns"): asi8 is in the index's OWN unit, so a whole-second index
    # stored as datetime64[s|ms|us] must not be read as sub-second.
    if (series.index.as_unit("ns").asi8 % 1_000_000_000 != 0).any():
        raise ValueError("series timestamps must be whole seconds (sub-second steps "
                         "would collapse in the canonical form)")


def _ref(row: LibraryItem) -> PriceSeriesRef:
    meta = json.loads(row.meta_json or "{}")
    return PriceSeriesRef(id=row.name, version=row.version, hash=row.hash,
                          source=meta.get("source") or "user",
                          vintage_year=meta.get("vintage_year"),
                          provider=meta.get("provider"))


def _latest_row(db: DBSession, org_id: UUID, name: str) -> LibraryItem | None:
    return db.scalars(
        select(LibraryItem)
        .where(LibraryItem.org_id == org_id, LibraryItem.kind == KIND, LibraryItem.name == name)
        .order_by(LibraryItem.version.desc())
        .limit(1)
    ).first()


def _write_payload(target: Path, data: bytes, digest: str) -> None:
    """Content-addressed write. A unique temp file per writer + os.replace, so
    concurrent writers of the same content never truncate each other; an
    existing file whose content does not hash to `digest` is rewritten."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            if hashlib.sha256(gzip.decompress(target.read_bytes())).hexdigest() == digest:
                return
        except (OSError, EOFError, gzip.BadGzipFile):
            pass
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(gzip.compress(data, mtime=0))
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def put_series(db: DBSession, org_id: UUID, name: str, series: pd.Series, meta: dict,
               *, created_by: UUID | None = None, root: Path | None = None) -> PriceSeriesRef:
    """Store `series` as `name` in the org's Library; idempotent on content.

    Concurrency: the (org, kind, name, version) unique constraint is the
    arbiter. A writer that loses the race re-reads: identical content returns
    the winner's version, different content retries with the next version.
    The retry ROLLS BACK `db`, so a caller must not hold other unsaved work in
    the same session when calling this.
    """
    _validate(name, series)
    try:
        meta_ok = SeriesMeta.model_validate(meta or {})
    except ValidationError as exc:
        raise ValueError(f"invalid series meta: {exc.errors()[0]['msg']}") from exc
    data, tz = _canonical(series)
    digest = hashlib.sha256(data).hexdigest()
    rel = Path("series") / f"{_slug(name)}-{digest[:16]}.csv.gz"
    _write_payload(library_dir(root or _default_root(), org_id) / rel, data, digest)
    meta_json = json.dumps({**meta_ok.model_dump(), "tz": tz}, sort_keys=True)

    for _ in range(_PUT_RETRIES):
        latest = _latest_row(db, org_id, name)
        if latest is not None and latest.hash == digest:
            return _ref(latest)
        row = LibraryItem(
            org_id=org_id, kind=KIND, name=name,
            version=1 if latest is None else latest.version + 1,
            hash=digest, path=rel.as_posix(), meta_json=meta_json,
            created_by=created_by, created_at=datetime.now(timezone.utc))
        ref = _ref(row)  # built BEFORE the commit: a bad row can never land
        db.add(row)
        try:
            db.commit()
            return ref
        except IntegrityError:
            db.rollback()  # another writer took this version: re-read and retry
            continue
    raise RuntimeError(f"could not store series {name!r} after {_PUT_RETRIES} attempts")


def _row_for(db: DBSession, org_id: UUID, ref: PriceSeriesRef) -> LibraryItem:
    row = db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org_id, LibraryItem.kind == KIND,
        LibraryItem.name == ref.id, LibraryItem.version == ref.version)).first()
    if row is None:
        raise LibraryRefNotFound(f"no series {ref.id!r} v{ref.version} in this org")
    return row


def series_meta(db: DBSession, org_id: UUID, ref: PriceSeriesRef) -> dict:
    """The stored meta of the version `ref` names (the zone key `tz` left out)."""
    row = _row_for(db, org_id, ref)
    meta = json.loads(row.meta_json or "{}")
    meta.pop("tz", None)
    return meta


def latest_ref(db: DBSession, org_id: UUID, name: str) -> PriceSeriesRef | None:
    """The ref of the latest version of `name`, or None."""
    row = _latest_row(db, org_id, name)
    return None if row is None else _ref(row)


def ref_for(db: DBSession, org_id: UUID, name: str, version: int) -> PriceSeriesRef | None:
    """The ref of one specific version, or None."""
    row = db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org_id, LibraryItem.kind == KIND,
        LibraryItem.name == name, LibraryItem.version == version)).first()
    return None if row is None else _ref(row)


def resolve(db: DBSession, org_id: UUID, ref: PriceSeriesRef, *, root: Path | None = None) -> pd.Series:
    """Return exactly the series `ref` names; refuse any hash mismatch."""
    row = _row_for(db, org_id, ref)
    if ref.hash != row.hash:
        raise LibraryRefStale(f"ref hash for {ref.id!r} v{ref.version} does not match the Library")
    base = library_dir(root or _default_root(), org_id).resolve()
    path = (base / row.path).resolve()
    if not path.is_relative_to(base):
        raise LibraryRefStale(f"Library path for {ref.id!r} v{ref.version} points outside "
                              "the org's Library directory")
    try:
        data = gzip.decompress(path.read_bytes())
    except FileNotFoundError as exc:
        raise LibraryRefStale(f"Library file for {ref.id!r} v{ref.version} is missing") from exc
    except (OSError, EOFError, zlib.error) as exc:  # BadGzipFile is an OSError
        raise LibraryRefStale(f"Library file for {ref.id!r} v{ref.version} is unreadable "
                              f"({type(exc).__name__})") from exc
    if hashlib.sha256(data).hexdigest() != row.hash:
        raise LibraryRefStale(f"Library file for {ref.id!r} v{ref.version} was modified")
    meta = json.loads(row.meta_json or "{}")
    tz = meta.get("tz")
    frame = pd.read_csv(io.BytesIO(data), dtype={"value": float}, comment="#")
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
