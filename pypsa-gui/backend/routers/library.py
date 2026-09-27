"""
The org Library over HTTP (Edge Investment Case P1 WP1.1b).

Series only in P1; tariffs, contracts and connection agreements join in P2
WP2.4, which is also where the Library's chat tools land (plan deviation from
"tools ship per phase": nothing in P1 is user-facing yet).

Routes act on the CALLER's org by default. `org_id` names another org and is
honoured only for super-admins (`services/library_acl`). Thin handlers: the
store is `services/library/series_store.py`.
"""
from __future__ import annotations

from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from db.models import Organization, User
from db.session import get_db
from deps import require_user
from models.commercial import PriceSeriesRef
from services import library_acl
from services.library import series_store as S

router = APIRouter()

# A 15-minute year is 35,040 points; this admits ~28 of them. JSON bodies have
# no upload guard in this repo (`upload_guard` covers UploadFile only).
MAX_POINTS = 1_000_000

# A zone marker at the end of an ISO time part: Z, UTC, GMT, +01, +0100,
# +01:00 — case-insensitive (pandas reads `...t00:00z` as UTC too).
_OFFSET_RE = r"(?i)(?:Z|UTC|GMT|[+-]\d{2}(?::?\d{2})?)$"


class SeriesIn(BaseModel):
    """One upload. `timestamps` are ISO strings. Either ALL carry an offset
    (instants; `timezone`, if given, is the zone to keep them in, else UTC) or
    NONE do (naive wall-clock, stored naive; `timezone` is then refused, the
    same rule as the tariff engine's: a naive axis plus a zone is ambiguous
    across DST)."""

    name: str = Field(min_length=1, max_length=128)
    # The cap is checked in `_series_from`, not with `max_length`: a pydantic
    # length error echoes the whole input back (a 29 MB 422 at the cap).
    timestamps: list[str] = Field(min_length=1)
    values: list[float] = Field(min_length=1)
    timezone: str | None = None
    meta: dict = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _fetchable_name(cls, v: str) -> str:
        # `GET /series/{name}` is one path segment: "/", "?" and "#" would
        # split the URL, "." and ".." are collapsed by HTTP clients, and a
        # blank or unprintable name is no name.
        v = v.strip()
        if (not v or v in {".", ".."} or any(ch in "/?#" for ch in v)
                or not v.isprintable()):
            raise ValueError("name must be printable, not '.' or '..', and "
                             "contain no '/', '?' or '#'")
        return v


class SeriesOut(BaseModel):
    ref: PriceSeriesRef
    timestamps: list[str]
    values: list[float]
    timezone: str | None


def _target_org(db: DBSession, user: User, org_id: UUID | None, *, write: bool) -> UUID:
    own = library_acl.org_of(db, user)
    target = org_id or own
    if target is None:
        raise HTTPException(403, "You belong to no organization; the Library is per organization.")
    if org_id is not None and org_id != own and not library_acl.can_read(db, user, org_id):
        # Refuse before the existence check so a non-admin cannot probe org ids.
        raise HTTPException(403, "Not allowed to access that organization's Library.")
    if org_id is not None and db.get(Organization, org_id) is None:
        raise HTTPException(404, "No such organization.")
    allowed = (library_acl.can_write if write else library_acl.can_read)(db, user, target)
    if not allowed:
        raise HTTPException(403, "Not allowed to access that organization's Library.")
    return target


def _series_from(body: SeriesIn) -> pd.Series:
    if max(len(body.timestamps), len(body.values)) > MAX_POINTS:
        raise HTTPException(422, f"at most {MAX_POINTS:,} points per series")
    if len(body.timestamps) != len(body.values):
        raise HTTPException(422, "timestamps and values must have the same length")
    raw = pd.Series(body.timestamps, dtype=str).str.strip()
    time_part = raw.str.extract(r"[Tt ](.*)$")[0].fillna("")
    aware = time_part.str.contains(_OFFSET_RE, regex=True)
    if aware.any() and not aware.all():
        raise HTTPException(422, "timestamps mix offset-carrying and naive values; "
                                 "send every timestamp with an offset, or none")
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(list(raw), utc=bool(aware.all())))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"unparseable timestamp: {exc}") from exc
    # A zone pandas recognised that the regex did not still makes instants.
    if idx.tz is None and body.timezone is not None:
        raise HTTPException(422, "timestamps carry no UTC offset, so `timezone` is ambiguous "
                                 "across DST; send ISO timestamps with an offset")
    if idx.tz is not None:
        try:
            idx = idx.tz_convert(body.timezone or "UTC")
        except Exception as exc:  # noqa: BLE001 — bad zone name
            raise HTTPException(422, f"unknown timezone {body.timezone!r}") from exc
    return pd.Series(np.asarray(body.values, dtype=float), index=idx)


@router.get("/series", response_model=list[PriceSeriesRef])
def list_series(org_id: UUID | None = None, db: DBSession = Depends(get_db),
                user: User = Depends(require_user)):
    return S.list_series(db, _target_org(db, user, org_id, write=False))


@router.post("/series", response_model=PriceSeriesRef)
def put_series(body: SeriesIn, org_id: UUID | None = None, db: DBSession = Depends(get_db),
               user: User = Depends(require_user)):
    org = _target_org(db, user, org_id, write=True)
    series = _series_from(body)
    try:
        return S.put_series(db, org, body.name, series, body.meta, created_by=user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/series/{name}", response_model=SeriesOut)
def get_series(name: str, version: int | None = None, org_id: UUID | None = None,
               db: DBSession = Depends(get_db), user: User = Depends(require_user)):
    org = _target_org(db, user, org_id, write=False)
    ref = S.latest_ref(db, org, name) if version is None else S.ref_for(db, org, name, version)
    if ref is None:
        what = f"series {name!r}" if version is None else f"version {version} of series {name!r}"
        raise HTTPException(404, f"No {what} in this Library")
    try:
        series = S.resolve(db, org, ref)
    except S.LibraryRefNotFound as exc:  # removed between the two reads
        raise HTTPException(404, str(exc)) from exc
    except S.LibraryRefStale as exc:
        raise HTTPException(409, {"code": "library_ref_stale", "message": str(exc)}) from exc
    tz = str(series.index.tz) if series.index.tz is not None else None
    return SeriesOut(ref=ref, timestamps=[t.isoformat() for t in series.index],
                     values=[float(v) for v in series.to_numpy()], timezone=tz)
