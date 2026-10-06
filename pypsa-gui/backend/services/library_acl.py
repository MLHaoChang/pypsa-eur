"""
Access rule for the org-scoped Library (Edge Investment Case P1 WP1.1b).

Distinct from `services/project_acl.py`, which scopes access by PROJECT
scenario tree: the Library belongs to an ORGANIZATION. The rule is short on
purpose:

  * a member of the org (any role) reads and writes its Library;
  * a super-admin may address any org;
  * nobody else.

Membership is one org per user (`org_memberships.user_id` is unique), so
`org_of(user)` is the org a request defaults to.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import OrgMembership, User


def org_of(db: DBSession, user: User) -> UUID | None:
    return db.scalar(select(OrgMembership.org_id).where(OrgMembership.user_id == user.id))


def can_read(db: DBSession, user: User, org_id: UUID) -> bool:
    if getattr(user, "is_super_admin", False):
        return True
    return org_id is not None and org_of(db, user) == org_id


def can_write(db: DBSession, user: User, org_id: UUID) -> bool:
    # Same rule today; kept separate so a read-only role can arrive later
    # without touching every call site.
    return can_read(db, user, org_id)
