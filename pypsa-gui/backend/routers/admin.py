from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.session import get_db
from db.models import OrgMembership, Organization, Project, User
from deps import require_user
from services import email_service
from services.auth_service import issue_password_token
from services.legacy_migrate import claim_legacy_project, list_legacy_projects
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


class ClaimLegacyProjectRequest(BaseModel):
    org_id: uuid.UUID | None = None
    owner_id: uuid.UUID
    member_ids: list[uuid.UUID] = Field(default_factory=list)
    include_descendants: bool = True


class TestEmailRequest(BaseModel):
    to: str | None = None


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
        body_text=f"Use this link to set your password: {_set_password_link(raw_token)}",
        metadata={"purpose": "set_password"},
    )


def _raise_email_http_error(exc: email_service.EmailServiceError) -> None:
    raise HTTPException(status_code=503, detail=str(exc)) from exc


def _require_admin_actor(db: DBSession, actor: User) -> OrgMembership | None:
    membership = get_user_membership(db, actor.id)
    if actor.is_super_admin:
        return membership
    if membership is None or membership.role != "admin":
        raise HTTPException(status_code=403, detail="Only org admins or super-admins can access admin routes")
    return membership


def _resolve_claim_org_id(
    db: DBSession,
    *,
    actor: User,
    requested_org_id: uuid.UUID | None,
) -> uuid.UUID:
    membership = _require_admin_actor(db, actor)
    if actor.is_super_admin:
        if requested_org_id is not None:
            return requested_org_id
        if membership is not None:
            return membership.org_id
        raise HTTPException(status_code=400, detail="org_id is required for a super-admin without an organization")
    if requested_org_id is not None and membership is not None and requested_org_id != membership.org_id:
        raise HTTPException(status_code=403, detail="Org admins can only claim into their own organization")
    assert membership is not None
    return membership.org_id


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


def _serialize_project(project: Project) -> dict[str, str | None]:
    return {
        "id": str(project.id),
        "name": project.name,
        "org_id": str(project.org_id),
        "parent_project_id": str(project.parent_project_id) if project.parent_project_id is not None else None,
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

    # The user (and its set-password token) is already committed at this point.
    # A failed/unconfigured email must not dead-end the admin with a 503: return
    # 201 with a clear warning so the admin can use the resend endpoint.
    membership = get_user_membership(db, user.id)
    response = _serialize_user(user, membership)
    try:
        _send_set_password_email(email=user.email, raw_token=raw_token)
        response["email_sent"] = True
    except email_service.EmailServiceError as exc:
        response["email_sent"] = False
        response["warning"] = (
            f"User created, but the set-password email was not sent: {exc}. "
            "Use the resend set-password action once email is configured."
        )
    return response


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
    try:
        _send_set_password_email(email=user.email, raw_token=raw_token)
    except email_service.EmailServiceError as exc:
        _raise_email_http_error(exc)
    return {"ok": True}


# The same two `legacy_migrate` functions `routers/projects.py` exposes under
# `/unclaimed`, behind a different door. `main.py` already 404s this whole
# router in local mode; these two carry their own guard as well, so the
# protection survives someone re-opening the admin surface. See Task 1's tests.
@router.get("/legacy-projects", dependencies=[Depends(local_mode.reject_in_local_mode)])
def list_legacy_projects_endpoint(
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> list[dict[str, object]]:
    _require_admin_actor(db, actor)
    return [
        {
            "name": project.name,
            "parent_project": project.parent_project,
            "scenario_description": project.scenario_description,
            # Same payload as /api/projects/unclaimed — this route is the same
            # `legacy_migrate` data behind an admin door, and a field present
            # on one and absent on the other is a trap for whoever consumes
            # both.
            "scenario_type": project.scenario_type,
            "has_network": project.has_network,
            "descendant_names": project.descendant_names,
        }
        for project in list_legacy_projects()
    ]


@router.post(
    "/legacy-projects/{legacy_name}/claim",
    status_code=201,
    dependencies=[Depends(local_mode.reject_in_local_mode)],
)
def claim_legacy_project_endpoint(
    legacy_name: str,
    payload: ClaimLegacyProjectRequest,
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    target_org_id = _resolve_claim_org_id(db, actor=actor, requested_org_id=payload.org_id)
    try:
        result = claim_legacy_project(
            db,
            legacy_name,
            target_org_id,
            payload.owner_id,
            payload.member_ids,
            payload.include_descendants,
        )
    except (PermissionDenied, ConflictError, ValidationError) as exc:
        _raise_http_error(exc)

    return {
        "root": _serialize_project(result.root),
        "claimed": [_serialize_project(project) for project in result.claimed],
        "warnings": result.warnings,
    }


@router.get("/email/status")
def email_status_endpoint(
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    _require_admin_actor(db, actor)
    return email_service.get_status()


@router.post("/email/test")
def email_test_endpoint(
    payload: TestEmailRequest,
    actor: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    _require_admin_actor(db, actor)
    target_email = payload.to or actor.email
    try:
        email_service.send_email(
            to=target_email,
            subject="PyPSA Studio email test",
            body_text="PyPSA Studio SMTP delivery is working.",
            metadata={"purpose": "admin_test"},
        )
    except email_service.EmailServiceError as exc:
        _raise_email_http_error(exc)
    return {"ok": True, "to": target_email}
