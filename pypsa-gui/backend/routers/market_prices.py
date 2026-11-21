"""
ENTSO-E day-ahead prices (SCU06 S1): the token, and a project's fetch.

`/api/market-prices/token` stores the user's ENTSO-E token in `user.env`
(`app_secrets`). Like the Anthropic key it is one install-wide value, so only a
super-admin may set or clear it; the status says only whether it is set.

`/api/projects/{name}/market-prices` reads the zone from the site pin (or the
zone the user picked) and `…/day-ahead` fetches one complete year into the
organisation's Library (`services/market_prices.py`). Not via chat: it spends a
stored credential on an outbound call.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from models.commercial import PriceSeriesRef
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import app_secrets, library_acl, market_prices, project_write_gate
from services.library import series_store

router = APIRouter()
project_router = APIRouter()

_STATUS = {
    "no_token": 409,
    "bad_token": 422,
    "invalid_zone": 422,
    "invalid_year": 422,
    "no_data": 404,
    "incomplete": 422,
    "bad_document": 502,
    "unavailable": 503,
}


def _refusal(exc: market_prices.MarketPriceError) -> HTTPException:
    return HTTPException(
        _STATUS.get(exc.kind, 502), {"kind": exc.kind, "message": exc.message}
    )


def _require_super_admin(user: User | None) -> User:
    if user is None:
        raise HTTPException(401, "Authentication required")
    if not user.is_super_admin:
        raise HTTPException(
            403,
            "The ENTSO-E access token is shared by every organization on "
            "this instance, so only super-admins can change it.",
        )
    return user


def _token_status(user: User | None) -> dict:
    return {
        "token_set": market_prices.token() is not None,
        "can_set_token": bool(user is not None and user.is_super_admin),
    }


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


@router.get("/status")
def get_status(user: User | None = Depends(optional_user)) -> dict:
    """Set or not set, never a mask or a prefix of the token."""
    if user is None:
        raise HTTPException(401, "Authentication required")
    return _token_status(user)


@router.put("/token")
def put_token(body: TokenIn, user: User | None = Depends(optional_user)) -> dict:
    _require_super_admin(user)
    try:
        app_secrets.set_secret(market_prices.TOKEN_NAME, body.value)
    except app_secrets.SecretValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _token_status(user)


@router.delete("/token")
def delete_token(user: User | None = Depends(optional_user)) -> dict:
    _require_super_admin(user)
    app_secrets.clear_secret(market_prices.TOKEN_NAME)
    return _token_status(user)


def _pin_zone(project: AuthorizedProject) -> str | None:
    from services.site_scene import store as scene_store

    try:
        pin = market_prices.pin_of(scene_store.project_sites(project.directory))
    except (OSError, ValueError):
        return None
    return market_prices.zone_for_pin(*pin) if pin else None


def _can_fetch(db: DBSession, user: User | None, project: AuthorizedProject) -> bool:
    """A project editor who may write the Library; a viewer gets no Fetch."""
    if user is None:
        return False
    try:
        if project_write_gate.is_viewer(db, user, project.uuid):
            return False
    except ValueError:  # not a registry project (a uuid-less legacy folder)
        pass
    try:
        org = _project_org(db, user, project)
    except ValueError:
        return False
    return org is not None and library_acl.can_write(db, user, org)


def _project_org(db: DBSession, user: User, project: AuthorizedProject) -> UUID | None:
    """The project's org; the user's own only when the project names none. A
    present but malformed `org_id` raises ValueError and never falls back."""
    if not project.org_id:
        return library_acl.org_of(db, user)
    try:
        return UUID(str(project.org_id))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("malformed project org_id") from None


@project_router.get("/{name}/market-prices")
def get_project_market_prices(
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """What the fetch offers this project: the zones, the zone from the pin,
    the zone the user picked, the last complete year, and whether this user
    may fetch at all."""
    pin_zone = _pin_zone(project)
    chosen = market_prices.chosen_zone(project.directory)
    return {
        **_token_status(user),
        "zones": [
            {"code": z.code, "label": z.label, "first_year": z.first_year}
            for z in market_prices.BIDDING_ZONES.values()
        ],
        "pin_zone": pin_zone,
        "chosen_zone": chosen,
        "zone": chosen or pin_zone,
        "last_year": market_prices.last_complete_year(),
        "can_fetch": _can_fetch(db, user, project),
    }


class DayAheadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zone: str
    year: StrictInt


class DayAheadOut(BaseModel):
    ref: PriceSeriesRef
    zone: str
    year: int
    fetched: str
    source: str = market_prices.SOURCE


def _lock_target(project: AuthorizedProject) -> SimpleNamespace | None:
    # The `routers/adequacy_worksheet.py` adapter (that file says why it is copied).
    try:
        return SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        return None


def _library_org(db: DBSession, user: User | None, project: AuthorizedProject) -> UUID:
    if user is None:
        raise HTTPException(401, "Authentication required")
    try:
        org = _project_org(db, user, project)
    except ValueError:
        raise HTTPException(
            403, "This project's organization could not be read, so nothing was added."
        ) from None
    if org is None or not library_acl.can_write(db, user, org):
        raise HTTPException(
            403, "Not allowed to add series to this organization's Library."
        )
    return org


def _require_project_writer(
    db: DBSession, user: User | None, project: AuthorizedProject
) -> None:
    try:
        viewer = project_write_gate.is_viewer(db, user, project.uuid)
    except ValueError:  # not a registry project (a uuid-less legacy folder)
        viewer = False
    if viewer:
        raise project_write_gate.refusal()


@project_router.post("/{name}/market-prices/day-ahead", response_model=DayAheadOut)
def post_day_ahead(
    body: DayAheadIn,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> DayAheadOut:
    """Fetch one complete year of `zone`'s day-ahead prices into the Library,
    and remember the zone for this project. Check-only lock, like the sidecars."""
    from routers.projects import _check_project_lock

    lock = _lock_target(project)
    if lock is not None:
        _check_project_lock(db, lock, user)
    org = _library_org(db, user, project)
    _require_project_writer(db, user, project)
    try:
        market_prices.check_request(body.zone, body.year)
        # A sync route: FastAPI runs it in the threadpool, so the fetch blocks no loop.
        series, notes, fetched = market_prices.day_ahead(body.zone, body.year)
    except market_prices.MarketPriceError as exc:
        raise _refusal(exc) from exc
    # The fetch can take a minute: a role or lock may have changed meanwhile, so
    # access is checked again right before each write (the fetched year stays
    # cached either way; it is public data and carries no token).
    org = _library_org(db, user, project)
    ref = series_store.put_series(
        db,
        org,
        market_prices.series_name(body.zone, body.year),
        series,
        market_prices.series_meta(body.zone, body.year, notes, fetched),
        created_by=user.id,
    )
    if lock is not None:
        _check_project_lock(db, lock, user)
    _require_project_writer(db, user, project)
    market_prices.store_zone(project.directory, body.zone)
    return DayAheadOut(ref=ref, zone=body.zone, year=body.year, fetched=fetched)
