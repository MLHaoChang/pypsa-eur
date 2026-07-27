from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from db.models import Session as SessionRow, User
from db.session import get_db
from services.auth_service import resolve_session, resolve_session_row
from settings import get_settings

_MISSING = object()


def resolve_request_user(request: Request, db: DBSession) -> User | None:
    raw_token = request.cookies.get(get_settings().session_cookie_name)
    if not raw_token:
        return None
    return resolve_session(db, raw_token)


def resolve_request_session(request: Request, db: DBSession) -> SessionRow | None:
    """
    The caller's SESSION row, not just the user behind it.

    Step 0b puts `active_project_id` on the session, so the request path needs
    the row. Sessions are per sign-in, so two browsers logged in as the same
    user each keep their own active project — which is the behaviour a user
    expects from two windows, and the reason this keys on the session rather
    than the user.
    """
    raw_token = request.cookies.get(get_settings().session_cookie_name)
    if not raw_token:
        return None
    return resolve_session_row(db, raw_token)


async def bind_active_project(
    request: Request,
    db: DBSession = Depends(get_db),
) -> None:
    """
    Bind this request to its SESSION's active project (Step 0b).

    Registered as an app-level dependency so it runs for every route without
    any of the ~110 project-less handlers changing. `PyPSAService._ensure_active`
    prefers the binding, so all 133 `get_network()` call sites follow it.

    WHY A DEPENDENCY AND NOT MIDDLEWARE. The obvious home is the auth middleware
    — it already resolves the session. It does not work: `BaseHTTPMiddleware`
    runs the downstream app in a task spawned by its own task group, and that
    task does not inherit contextvars `dispatch` sets, so the endpoint saw a
    pristine context and every route silently fell back to the process global.
    ASYNC is load-bearing for the same reason. FastAPI runs a SYNC dependency in
    a threadpool, so a `ContextVar.set()` inside one mutates a COPY of the
    context and is discarded when the worker returns — the second failure mode
    this hit. An async dependency runs in the request's own task, and the sync
    endpoint's own threadpool call then copies THAT context, so the binding
    reaches the handler. Verified by S0.10, which failed loudly for both the
    middleware and the sync-dependency versions.

    The DB work inside is blocking, which is why this would normally be sync;
    the queries are two indexed lookups on SQLite/Postgres and the correctness
    requirement wins. Step 2's async rework is where that trade-off is revisited.

    Never raises: authorization is the middleware's job, and a route that has
    no session (the public paths) simply gets no binding and falls through to
    the process foreground.
    """
    try:
        session = resolve_request_session(request, db)
        if session is None:
            return
        from services import active_project
        from services.pypsa_service import PyPSAService

        ctx, slot = active_project.resolve_for_session(db, session)
        PyPSAService.bind_request_context(ctx)
        PyPSAService.bind_request_slot(slot)
        PyPSAService.bind_request_scratch(active_project.scratch_key(session))
    except Exception:  # noqa: BLE001
        # A corrupt or vanished project must not 500 every route — the resolver
        # already falls back to the session's scratch context, and this is the
        # last-resort net for anything it did not anticipate.
        logging.getLogger(__name__).exception(
            "could not bind the session's active project"
        )


def current_session(
    request: Request,
    db: DBSession = Depends(get_db),
) -> SessionRow | None:
    """
    The caller's session row, attached to the REQUEST's DB session.

    Deliberately re-resolved rather than reusing the instance the middleware
    put on `request.state`: that one belongs to a `Session()` the middleware
    has already closed, so writing `active_project_id` through it would either
    raise DetachedInstanceError or commit on a connection the handler does not
    own. One indexed lookup on a hashed token is cheap; a half-attached ORM
    object is not.
    """
    return resolve_request_session(request, db)


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
