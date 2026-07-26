from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

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
from settings import get_settings

router = APIRouter()


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


def _set_session_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=not _is_local_origin(settings.public_base_url),
        max_age=settings.session_ttl_hours * 60 * 60,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        samesite="lax",
        secure=not _is_local_origin(settings.public_base_url),
        path="/",
    )


def _serialize_user(user: User) -> dict[str, str | bool]:
    return {
        "id": str(user.id),
        "email": user.email,
        "status": user.status,
        "is_super_admin": user.is_super_admin,
    }


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    user = db.scalar(select(User).where(User.email == _normalize_email(payload.email)))
    if (
        user is None
        or user.password_hash is None
        or user.status != "active"
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    raw_token, _session = create_session(db, user.id)
    _set_session_cookie(response, raw_token)
    return {"ok": True, "user": _serialize_user(user)}


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
    # Task 8's lock service handles releasing project locks on logout; keep the
    # auth endpoint focused on revoking the session and clearing the cookie.
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(require_user)) -> dict[str, str | bool]:
    return _serialize_user(user)


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
        email_service.send_email(
            to=user.email,
            subject="Reset your password",
            body=f"Use this link to reset your password: {reset_link}",
            metadata={"purpose": "reset_password"},
        )
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
