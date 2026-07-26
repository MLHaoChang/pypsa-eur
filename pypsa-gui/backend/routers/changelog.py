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
    """
    return change_log_service.get_all(org_id=_caller_org_id(db, user))


@router.delete("/", status_code=204)
def clear_changelog(
    user: User = Depends(require_user),
    db: DBSession = Depends(get_db),
) -> None:
    """
    Clear the caller's own organization's entries — never another's.

    This route was previously unauthenticated AND unscoped: a single anonymous
    DELETE erased every tenant's audit trail in the process at once.
    """
    change_log_service.clear(org_id=_caller_org_id(db, user))
