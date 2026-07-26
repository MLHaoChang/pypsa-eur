from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

import security
from db.models import User
from db.session import get_db
from deps import require_user
from services import email_service
from services.auth_service import (
    create_session,
    issue_password_token,
    revoke_session,
    set_password_from_token,
    verify_password,
)
from services.tenancy_service import get_user_membership, list_org_members
from settings import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordTokenRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    email: str


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _is_local_origin(base_url: str) -> bool:
    hostname = urlparse(base_url).hostname
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _cookie_flags(request: Request | None = None) -> tuple[str, bool]:
    """Return (samesite, secure) for the session cookie."""
    settings = get_settings()
    host = (request.headers.get("host") if request is not None else "") or ""
    hostname = host.split(":")[0].lower()
    is_local_host = hostname in {"localhost", "127.0.0.1", "::1", "", "testserver"}
    # Cursor cloud/mobile HTTPS tunnel hosts need SameSite=None; Secure so the
    # session cookie is accepted inside the preview iframe.
    if hostname.endswith(".cursorusercontent.com"):
        return "none", True
    if (
        request is not None
        and request.url.scheme == "https"
        and not is_local_host
    ):
        return "none", True
    return "lax", not _is_local_origin(settings.public_base_url)


def _set_session_cookie(response: Response, raw_token: str, request: Request | None = None) -> None:
    settings = get_settings()
    samesite, secure = _cookie_flags(request)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite=samesite,
        secure=secure,
        max_age=settings.session_ttl_hours * 60 * 60,
        path="/",
    )


def issue_csrf_cookie(response: Response, request: Request | None = None) -> str:
    """
    Mint a double-submit CSRF token and return it.

    Deliberately **not** httponly — the SPA has to read it to echo it back in
    the `X-CSRF-Token` header, which is the entire mechanism: an attacker page
    can make the browser *send* a `SameSite=None` cookie but cannot *read* it
    across origins. `httponly=True` here would break the client without adding
    protection, since the threat is cross-origin reads, not script access on
    our own origin (a same-origin XSS can call the API directly regardless).
    """
    settings = get_settings()
    samesite, secure = _cookie_flags(request)
    token = security.new_csrf_token()
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        samesite=samesite,
        secure=secure,
        max_age=settings.session_ttl_hours * 60 * 60,
        path="/",
    )
    return token


def _clear_session_cookie(response: Response, request: Request | None = None) -> None:
    settings = get_settings()
    samesite, secure = _cookie_flags(request)
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        samesite=samesite,
        secure=secure,
        path="/",
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        httponly=False,
        samesite=samesite,
        secure=secure,
        path="/",
    )


def _serialize_user(user: User, *, org_id: str | None = None, role: str | None = None) -> dict[str, str | bool | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "status": user.status,
        "is_super_admin": user.is_super_admin,
        "org_id": org_id,
        "role": role,
    }


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    # Throttle BEFORE the password check. `/api/auth/login` is in
    # `_AUTH_PUBLIC_PATHS`, so nothing upstream limits it and credential
    # stuffing was previously unimpeded — the only rate limiter in the codebase
    # (`chat_service._RATE_BUCKETS`) covers chat.
    ip = security.client_ip(request)
    retry_after = security.login_retry_after(ip, payload.email)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.scalar(select(User).where(User.email == _normalize_email(payload.email)))
    if (
        user is None
        or user.password_hash is None
        or user.status != "active"
        or not verify_password(payload.password, user.password_hash)
    ):
        security.record_failed_login(ip, payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    security.clear_login_attempts(ip, payload.email)
    raw_token, _session = create_session(db, user.id)
    _set_session_cookie(response, raw_token, request)
    csrf_token = issue_csrf_cookie(response, request)
    membership = get_user_membership(db, user.id)
    return {
        "ok": True,
        # Returned in the body as well as the cookie so a client that cannot
        # read cookies (native app, test harness) can still satisfy the
        # double-submit check without a second round-trip.
        "csrf_token": csrf_token,
        "user": _serialize_user(
            user,
            org_id=str(membership.org_id) if membership is not None else None,
            role=membership.role if membership is not None else None,
        ),
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    _user: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, bool]:
    raw_token = request.cookies.get(get_settings().session_cookie_name)
    if raw_token:
        revoke_session(db, raw_token)
    try:
        from services import project_locks

        project_locks.release_all_for_user(db, _user.id)
    except Exception:
        db.rollback()
        logger.warning("best-effort project lock release failed on logout", exc_info=True)
    _clear_session_cookie(response, request)
    return {"ok": True}


@router.get("/me")
def me(
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, str | bool | None]:
    # Top up the CSRF cookie when it is missing but the session is still valid
    # — the session cookie outlives the tab, and a session that predates the
    # CSRF change (or whose token cookie was cleared) would otherwise have
    # every write rejected with no way to recover short of signing out.
    # Only when ABSENT: re-minting on every `/me` would race a concurrent
    # mutation holding the previous value in its header.
    settings = get_settings()
    csrf_token = request.cookies.get(settings.csrf_cookie_name)
    if not csrf_token:
        csrf_token = issue_csrf_cookie(response, request)

    membership = get_user_membership(db, user.id)
    return {
        **_serialize_user(
            user,
            org_id=str(membership.org_id) if membership is not None else None,
            role=membership.role if membership is not None else None,
        ),
        "csrf_token": csrf_token,
    }


@router.get("/org-members")
def org_members(
    user: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> list[dict[str, str | None]]:
    """
    Member directory for the current user's organization.

    Available to ANY authenticated org member (not just admins) so a project
    creator can pick the colleagues to assign to their project. Returns only
    users within the caller's own org — id, email, and role.
    """
    return [
        {
            "id": str(member.id),
            "email": member.email,
            "role": membership.role,
        }
        for member, membership in list_org_members(db, user)
    ]


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest,
    db: DBSession = Depends(get_db),
) -> dict[str, bool]:
    user = db.scalar(select(User).where(User.email == _normalize_email(payload.email)))
    if user is not None and user.status != "disabled":
        raw_token = issue_password_token(db, user.id, "reset_password")
        reset_link = (
            f"{get_settings().public_base_url.rstrip('/')}/reset-password?token={raw_token}"
        )
        try:
            email_service.send_email(
                to=user.email,
                subject="Reset your password",
                body_text=f"Use this link to reset your password: {reset_link}",
                metadata={"purpose": "reset_password"},
            )
        except email_service.EmailServiceError:
            logger.warning("forgot-password email delivery failed", exc_info=True)
    return {"ok": True}


def _consume_password_token(
    *,
    db: DBSession,
    payload: PasswordTokenRequest,
    purpose: str,
) -> dict[str, bool]:
    user = set_password_from_token(db, payload.token, purpose, payload.password)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"ok": True}


@router.post("/set-password")
def set_password(
    payload: PasswordTokenRequest,
    db: DBSession = Depends(get_db),
) -> dict[str, bool]:
    return _consume_password_token(db=db, payload=payload, purpose="set_password")


@router.post("/reset-password")
def reset_password(
    payload: PasswordTokenRequest,
    db: DBSession = Depends(get_db),
) -> dict[str, bool]:
    return _consume_password_token(db=db, payload=payload, purpose="reset_password")
