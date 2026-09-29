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

from datetime import date
from enum import Enum
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from db.models import Organization, User
from db.session import get_db
from deps import require_user
from models.commercial import LibraryItemRef, PriceSeriesRef
from services import library_acl
from services.library import items as I
from services.library import series_io
from services.library import series_store as S
from services.library import urdb as U
from services.upload_guard import read_capped

router = APIRouter()

# The upload rules live in `services/library/series_io.py` (P2 WP2.4b-0).
MAX_POINTS = series_io.MAX_POINTS


class SeriesIn(BaseModel):
    """One upload. `timestamps` are ISO strings. Either ALL carry an offset
    (instants; `timezone`, if given, is the zone to keep them in, else UTC) or
    NONE do (naive wall-clock, stored naive; `timezone` is then refused, the
    same rule as the tariff engine's: a naive axis plus a zone is ambiguous
    across DST)."""

    name: str = Field(min_length=1, max_length=128)
    # The cap is checked in `series_io`, not with `max_length`: a pydantic
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


class ItemKind(str, Enum):
    tariff = "tariff"
    contract = "contract"
    connection_agreement = "connection_agreement"


class ItemIn(BaseModel):
    payload: dict
    meta: dict = Field(default_factory=dict)


class ItemOut(BaseModel):
    ref: LibraryItemRef
    payload: dict
    meta: dict = Field(default_factory=dict)


class UrdbImportIn(BaseModel):
    """A URDB rate (the `items[0]` object of an OpenEI response, or a REopt
    `urdb_response`) to store as a Library tariff (P2 WP2.4b-i)."""

    urdb_response: dict
    name: str = Field(min_length=1, max_length=128)
    cyclic_year: bool = False
    accept_partial: bool = False
    valid_from: date | None = None
    tariff_id: str | None = None
    jurisdiction: str | None = Field(default=None, min_length=2)


class UrdbImportOut(BaseModel):
    ref: LibraryItemRef
    notes: list[str]
    refusals: list[dict]
    unsupported_fields: list[str]


class MeterDataOut(BaseModel):
    ref: PriceSeriesRef
    meter_history_peaks_kw: dict[str, float]
    meter_history_energy_kwh: dict[str, float]
    settlement: str
    notes: list[str]
    help: str


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
    try:
        return series_io.series_from(body.timestamps, body.values, body.timezone,
                                     max_points=MAX_POINTS)
    except series_io.SeriesInputError as exc:
        raise HTTPException(422, str(exc)) from exc


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


def _upload_name(name: str) -> str:
    try:
        return SeriesIn.model_validate({"name": name, "timestamps": ["x"], "values": [0.0]}).name
    except ValueError as exc:
        raise HTTPException(422, "name must be printable, not '.' or '..', and contain no "
                                 "'/', '?' or '#'") from exc


@router.post("/series/upload", response_model=PriceSeriesRef)
async def upload_series(file: UploadFile = File(...), name: str = Form(...),
                        timezone: str | None = Form(None), source: str = Form("upload"),
                        org_id: UUID | None = None, db: DBSession = Depends(get_db),
                        user: User = Depends(require_user)):
    """A `timestamp,value` CSV or xlsx as a Library series (P2 WP2.4b-ii)."""
    org = _target_org(db, user, org_id, write=True)
    name = _upload_name(name)
    data = await read_capped(file)
    try:
        series = series_io.parse_upload(data, file.filename or "", timezone,
                                        max_points=MAX_POINTS)
        return S.put_series(db, org, name, series, {"source": source}, created_by=user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/meter_data", response_model=MeterDataOut)
async def upload_meter_data(file: UploadFile = File(...), name: str = Form(...),
                            settlement: str = Form("15min"), timezone: str | None = Form(None),
                            org_id: UUID | None = None, db: DBSession = Depends(get_db),
                            user: User = Depends(require_user)):
    """A kW meter file → a Library series (its settlement and notes in the
    meta) and the monthly history for `meter_history_peaks_kw` /
    `meter_history_energy_kwh` (P2 WP2.4b-ii)."""
    org = _target_org(db, user, org_id, write=True)
    name = _upload_name(name)
    data = await read_capped(file)
    try:
        series = series_io.parse_upload(data, file.filename or "", timezone,
                                        max_points=MAX_POINTS)
        hist = series_io.meter_history(series, settlement=settlement, timezone=timezone)
        ref = S.put_series(db, org, name, series,
                           {"source": "meter_data", "unit": "kW", "settlement": settlement,
                            "notes": hist["notes"]}, created_by=user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return MeterDataOut(ref=ref, help=series_io.METER_HISTORY_HELP, **hist)


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


# ── items: tariffs, contracts, connection agreements (P2 WP2.4a) ───────────


def _item_name(name: str) -> str:
    name = name.strip()
    if (not name or name in {".", ".."} or any(ch in "/?#" for ch in name)
            or not name.isprintable()):
        raise HTTPException(422, "item name must be printable, not '.' or '..', and "
                                 "contain no '/', '?' or '#'")
    return name


@router.get("/items/{kind}", response_model=list[LibraryItemRef])
def list_items(kind: ItemKind, org_id: UUID | None = None, db: DBSession = Depends(get_db),
               user: User = Depends(require_user)):
    return I.list_items(db, _target_org(db, user, org_id, write=False), kind.value)


@router.put("/items/{kind}/{name}", response_model=LibraryItemRef)
def put_item(kind: ItemKind, name: str, body: ItemIn, org_id: UUID | None = None,
             db: DBSession = Depends(get_db), user: User = Depends(require_user)):
    org = _target_org(db, user, org_id, write=True)
    try:
        return I.put_item(db, org, kind.value, _item_name(name), body.payload, body.meta,
                          created_by=user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/items/{kind}/{name}", response_model=ItemOut)
def get_item(kind: ItemKind, name: str, version: int | None = None,
             org_id: UUID | None = None, db: DBSession = Depends(get_db),
             user: User = Depends(require_user)):
    org = _target_org(db, user, org_id, write=False)
    name = _item_name(name)   # the PUT's normalisation (WP2.4a review #4)
    ref = (I.latest_ref(db, org, kind.value, name) if version is None
           else I.ref_for(db, org, kind.value, name, version))
    if ref is None:
        what = f"{kind.value} {name!r}" if version is None else \
            f"version {version} of {kind.value} {name!r}"
        raise HTTPException(404, f"No {what} in this Library")
    try:
        payload = I.resolve(db, org, ref)
    except S.LibraryRefNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except S.LibraryRefStale as exc:
        raise HTTPException(409, {"code": "library_ref_stale", "message": str(exc)}) from exc
    return ItemOut(ref=ref, payload=payload, meta=I.item_meta(db, org, ref))


@router.post("/items/tariff/import_urdb", response_model=UrdbImportOut)
def import_urdb(body: UrdbImportIn, org_id: UUID | None = None,
                db: DBSession = Depends(get_db), user: User = Depends(require_user)):
    """Refusals ⇒ 422 listing them, unless `accept_partial` (then stored with
    `unsupported_fields`; the engine flags `tariff_incomplete`)."""
    org = _target_org(db, user, org_id, write=True)
    name = _item_name(body.name)
    try:
        tariff, refusals, notes = U.urdb_to_tariff(
            body.urdb_response, name=name, cyclic_year=body.cyclic_year,
            accept_partial=body.accept_partial, tariff_id=body.tariff_id,
            jurisdiction=body.jurisdiction, valid_from=body.valid_from)
    except U.UrdbRefused as exc:
        raise HTTPException(422, {"code": "urdb_refused", "message": str(exc),
                                  "refusals": exc.refusals}) from exc
    except ValueError as exc:
        raise HTTPException(422, {"code": "urdb_invalid", "message": str(exc)}) from exc
    meta = {"source": "urdb", "provider": "OpenEI URDB", "notes": notes,
            "description": str(body.urdb_response.get("label") or "")[:500] or None}
    try:
        ref = I.put_item(db, org, "tariff", name, tariff.model_dump(mode="json"), meta,
                         created_by=user.id)
    except ValueError as exc:
        raise HTTPException(422, {"code": "urdb_invalid", "message": str(exc)}) from exc
    return UrdbImportOut(ref=ref, notes=notes, refusals=refusals,
                         unsupported_fields=tariff.unsupported_fields)
