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
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import require_user
from models.commercial import PriceSeriesRef
from services import library_acl
from services.library import series_store as S

router = APIRouter()


class SeriesIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    timestamps: list[str] = Field(min_length=1)
    values: list[float] = Field(min_length=1)
    timezone: str | None = None
    meta: dict = Field(default_factory=dict)


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
    allowed = (library_acl.can_write if write else library_acl.can_read)(db, user, target)
    if not allowed:
        raise HTTPException(403, "Not allowed to access that organization's Library.")
    return target


def _series_from(body: SeriesIn) -> pd.Series:
    if len(body.timestamps) != len(body.values):
        raise HTTPException(422, "timestamps and values must have the same length")
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(body.timestamps, utc=body.timezone is not None))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"unparseable timestamp: {exc}") from exc
    if body.timezone is not None:
        try:
            idx = idx.tz_convert(body.timezone)
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
    refs = {r.id: r for r in S.list_series(db, org)}
    if name not in refs:
        raise HTTPException(404, f"No series {name!r} in this Library")
    ref = refs[name]
    if version is not None and version != ref.version:
        ref = S.ref_for(db, org, name, version)
        if ref is None:
            raise HTTPException(404, f"No version {version} of series {name!r}")
    try:
        series = S.resolve(db, org, ref)
    except S.LibraryRefStale as exc:
        raise HTTPException(409, str(exc)) from exc
    tz = str(series.index.tz) if series.index.tz is not None else None
    return SeriesOut(ref=ref, timestamps=[t.isoformat() for t in series.index],
                     values=[float(v) for v in series.to_numpy()], timezone=tz)
