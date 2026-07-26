from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from db.session import get_db
from db.models import OrgMembership, Organization, User
from deps import require_user
from services import email_service
from services.auth_service import issue_password_token
from services.tenancy_service import (
    ConflictError,
    PermissionDenied,
    ValidationError,
    create_organization,
    create_user,
    get_user_membership,
    list_organizations,
    list_users,
)
from settings import get_settings

router = APIRouter()


class CreateOrganizationRequest(BaseModel):
    name: str


class CreateUserRequest(BaseModel):
    email: str
    role: str
    org_id: uuid.UUID | None = None


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, PermissionDenied):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _set_password_link(raw_token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/set-password?token={raw_token}"


def _send_set_password_email(*, email: str, raw_token: str) -> None:
    email_service.send_email(
        to=email,
        subject="Set your password",
        body=f"Use this link to set your password: {_set_password_link(raw_token)}",
        metadata={"purpose": "set_password"},
    )


def _serialize_organization(organization: Organization) -> dict[str, str]:
    return {
        "id": str(organization.id),
        "name": organization.name,
    }


def _serialize_user(user: User, membership: OrgMembership | None) -> dict[str, str | bool | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "status": user.status,
        "is_super_admin": user.is_super_admin,
        "org_id": str(membership.org_id) if membership is not None else None,
        "role": membership.role if membership is not None else None,
    }


@router.post("/organizations", status_code=201)
def create_organization_endpoint(
    payload: CreateOrganizationRequest,
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    try:
        organization = create_organization(db, payload.name, actor)
    except (PermissionDenied, ConflictError, ValidationError) as exc:
        _raise_http_error(exc)

    return _serialize_organization(organization)


@router.get("/organizations")
def list_organizations_endpoint(
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> list[dict[str, str]]:
    try:
        organizations = list_organizations(db, actor)
    except (PermissionDenied, ConflictError, ValidationError) as exc:
        _raise_http_error(exc)

    return [_serialize_organization(organization) for organization in organizations]


@router.post("/users", status_code=201)
def create_user_endpoint(
    payload: CreateUserRequest,
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, str | bool | None]:
    try:
        user, raw_token = create_user(db, payload.email, payload.org_id, payload.role, actor)
    except (PermissionDenied, ConflictError, ValidationError) as exc:
        _raise_http_error(exc)

    membership = get_user_membership(db, user.id)
    _send_set_password_email(email=user.email, raw_token=raw_token)
    return _serialize_user(user, membership)


@router.get("/users")
def list_users_endpoint(
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> list[dict[str, str | bool | None]]:
    try:
        user_rows = list_users(db, actor)
    except (PermissionDenied, ConflictError, ValidationError) as exc:
        _raise_http_error(exc)

    return [_serialize_user(user, membership) for user, membership in user_rows]


@router.post("/users/{user_id}/resend-set-password")
def resend_set_password_endpoint(
    user_id: uuid.UUID,
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, bool]:
    try:
        user_rows = {user.id: (user, membership) for user, membership in list_users(db, actor)}
    except (PermissionDenied, ConflictError, ValidationError) as exc:
        _raise_http_error(exc)

    row = user_rows.get(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    user, _membership = row
    raw_token = issue_password_token(db, user.id, "set_password")
    _send_set_password_email(email=user.email, raw_token=raw_token)
    return {"ok": True}
