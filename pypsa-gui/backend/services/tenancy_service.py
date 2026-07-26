from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from db.models import Organization, OrgMembership, User
from services.auth_service import issue_password_token

VALID_ORG_ROLES = {"admin", "member"}


class TenancyError(Exception):
    """Base tenancy service error."""


class PermissionDenied(TenancyError):
    """Raised when the current actor lacks the required tenancy permission."""


class ValidationError(TenancyError):
    """Raised when tenancy input is malformed or missing."""


class ConflictError(TenancyError):
    """Raised when tenancy data would violate a uniqueness constraint."""


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def normalize_email(value: str) -> str:
    return value.strip().lower()


def get_user_membership(db: DBSession, user_id: uuid.UUID) -> OrgMembership | None:
    return db.scalar(select(OrgMembership).where(OrgMembership.user_id == user_id))


def _normalize_role(role: str) -> str:
    normalized_role = role.strip().lower()
    if normalized_role not in VALID_ORG_ROLES:
        raise ValidationError("role must be one of: admin, member")
    return normalized_role


def _resolve_target_org_id(
    db: DBSession,
    *,
    actor: User,
    org_id: uuid.UUID | None,
) -> uuid.UUID:
    actor_membership = get_user_membership(db, actor.id)

    if actor.is_super_admin:
        if org_id is not None:
            return org_id
        if actor_membership is not None:
            return actor_membership.org_id
        raise ValidationError("org_id is required for a super-admin without an organization")

    if actor_membership is None or actor_membership.role != "admin":
        raise PermissionDenied("Only org admins or super-admins can create users")

    if org_id is not None and org_id != actor_membership.org_id:
        raise PermissionDenied("Org admins can only create users in their own organization")

    return actor_membership.org_id


def create_organization(db: DBSession, name: str, actor: User) -> Organization:
    if not actor.is_super_admin:
        raise PermissionDenied("Only super-admins can create organizations")

    normalized_name = name.strip()
    if not normalized_name:
        raise ValidationError("name is required")

    organization = Organization(name=normalized_name, created_at=_now_utc())
    db.add(organization)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Organization already exists") from exc

    db.refresh(organization)
    return organization


def create_user(
    db: DBSession,
    email: str,
    org_id: uuid.UUID | None,
    role: str,
    actor: User,
) -> tuple[User, str]:
    normalized_email = normalize_email(email)
    if not normalized_email:
        raise ValidationError("email is required")

    normalized_role = _normalize_role(role)
    target_org_id = _resolve_target_org_id(db, actor=actor, org_id=org_id)
    organization = db.get(Organization, target_org_id)
    if organization is None:
        raise ValidationError("Organization not found")

    user = User(
        email=normalized_email,
        password_hash=None,
        status="invited",
        is_super_admin=False,
        created_at=_now_utc(),
    )
    membership = OrgMembership(user_id=user.id, org_id=organization.id, role=normalized_role)

    try:
        db.add(user)
        db.flush()
        membership.user_id = user.id
        db.add(membership)
        raw_token = issue_password_token(db, user.id, "set_password")
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A user with that email already exists") from exc

    db.refresh(user)
    return user, raw_token


def list_organizations(db: DBSession, actor: User) -> list[Organization]:
    if actor.is_super_admin:
        return list(db.scalars(select(Organization).order_by(Organization.name)))

    membership = get_user_membership(db, actor.id)
    if membership is None or membership.role != "admin":
        raise PermissionDenied("Only org admins or super-admins can list organizations")

    organization = db.get(Organization, membership.org_id)
    return [organization] if organization is not None else []


def list_users(db: DBSession, actor: User) -> list[tuple[User, OrgMembership]]:
    if actor.is_super_admin:
        memberships = db.scalars(
            select(OrgMembership).order_by(OrgMembership.org_id, OrgMembership.user_id)
        ).all()
    else:
        actor_membership = get_user_membership(db, actor.id)
        if actor_membership is None or actor_membership.role != "admin":
            raise PermissionDenied("Only org admins or super-admins can list users")

        memberships = db.scalars(
            select(OrgMembership)
            .where(OrgMembership.org_id == actor_membership.org_id)
            .order_by(OrgMembership.user_id)
        ).all()

    users: list[tuple[User, OrgMembership]] = []
    for membership in memberships:
        user = db.get(User, membership.user_id)
        if user is not None:
            users.append((user, membership))
    return users
