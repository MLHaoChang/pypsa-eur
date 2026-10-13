from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import require_user
from services import change_log_service
from services.tenancy_service import get_user_membership

router = APIRouter()


def _caller_org_id(db: DBSession, user: User) -> str | None:
    """
    The caller's organization id, or None when they have no membership row.

    `None` is NOT a synonym for "unscoped" here, and the two routes below must
    each decide what it means for them — `change_log_service` reads `org_id=None`
    as "no filter" on read and "clear EVERYTHING" on delete, so passing this
    through unexamined is how a membership-less caller read every tenant's audit
    trail and destroyed all of it with one DELETE.

    Membership-less callers are not an edge case: `tools/bootstrap_super_admin.py`
    creates the shipped first user of every deployment with `is_super_admin=True`
    and no `OrgMembership`.
    """
    membership = get_user_membership(db, user.id)
    return str(membership.org_id) if membership is not None else None


@router.get("/")
def get_changelog(
    user: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> list[dict]:
    """
    The caller's own organization's audit trail.

    The underlying deque is process-global, so this filter is the only thing
    between one tenant and another's edit history — component names, project
    names, and the timing of their work. `require_user` is declared explicitly
    rather than inherited from the middleware so the dependency is visible at
    the route it protects.

    A caller with NO membership gets nothing, unless they are a super-admin, who
    gets the unscoped view deliberately. "Has no membership" was never the right
    predicate for cross-tenant visibility — `is_super_admin` is — and the two are
    not the same set: the bootstrap first user happens to be both, an invited user
    whose membership was revoked is only the former.
    """
    org_id = _caller_org_id(db, user)
    if org_id is None:
        if not user.is_super_admin:
            return []
        return change_log_service.get_all()  # deliberate cross-tenant read
    return change_log_service.get_all(org_id=org_id)


@router.delete("/", status_code=204)
def clear_changelog(
    user: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    Clear the caller's own organization's entries — never another's.

    This route was previously unauthenticated AND unscoped: a single anonymous
    DELETE erased every tenant's audit trail in the process at once. It then
    stayed unscoped for one narrower case, a caller with no membership row, whose
    `org_id=None` `change_log_service.clear` reads as "clear EVERYTHING" —
    contradicting that function's own docstring, which says an unscoped clear is
    "reachable only from test setup and the in-process reset helpers".

    So this route now NEVER passes None. A membership-less caller has no entries
    of their own to drop, and that includes a super-admin: reading across tenants
    is a defensible product decision, destroying every tenant's audit trail
    through a route documented as org-scoped is not. 204 either way — the
    caller's desired end state ("my history is gone") is satisfied by there being
    none, and telling them apart would leak whether other tenants exist.
    """
    org_id = _caller_org_id(db, user)
    if org_id is None:
        return
    change_log_service.clear(org_id=org_id)
