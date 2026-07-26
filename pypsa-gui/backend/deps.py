from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from services.auth_service import resolve_session
from settings import get_settings

_MISSING = object()


def resolve_request_user(request: Request, db: DBSession) -> User | None:
    raw_token = request.cookies.get(get_settings().session_cookie_name)
    if not raw_token:
        return None
    return resolve_session(db, raw_token)


def optional_user(
    request: Request,
    db: DBSession = Depends(get_db),
) -> User | None:
    cached_user = getattr(request.state, "auth_user", _MISSING)
    if cached_user is not _MISSING:
        return cached_user

    user = resolve_request_user(request, db)
    request.state.auth_user = user
    return user


def require_user(
    request: Request,
    db: DBSession = Depends(get_db),
) -> User:
    user = optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
