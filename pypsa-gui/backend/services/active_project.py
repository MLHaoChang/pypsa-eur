"""
Session-bound active project (Step 0b).

WHAT THIS SOLVES. Roughly 110 routes name no project at all —
``GET /api/network/buses``, ``GET /api/results/cost_breakdown``,
``POST /api/simulation/run`` — because they resolved through a PROCESS-GLOBAL
active project. Two consequences, and the second is the reason this module
exists rather than a path-scoping refactor:

  1. There is nothing for an ACL to bind to. ``require_project_access(project_id)``
     needs a project id, and these routes have none.
  2. On a shared process, two signed-in users fight over one slot: whoever
     loaded a project last decides what BOTH of them read next.

POINTER, NOT PAYLOAD. This module moves the *pointer* — which project a session
is looking at — into `sessions.active_project_id`. Step 3 separately moves the
*payload* (the resident ``pypsa.Network``) out of process memory. That split is
what makes 0b a precondition for Step 3 rather than a duplicate of it: on one
machine the payload can stay in ``PyPSAService._contexts`` exactly as it is.

THE UNBOUND CASE IS THE DEFAULT, NOT AN EDGE CASE. A fresh network — first load,
"New Project", or ``POST /api/network/reset`` — has no project row to point at.
``active_project_id`` is NULL there, and this module gives the session its own
SCRATCH context keyed ``scratch:<session-id>``. Without that, every unbound user
on the process would share one draft network, which is precisely the bug being
fixed. The alternative (create a real project row per session) was rejected: it
would litter every user's project list with drafts they never asked to save.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session as DBSession

from db.models import Project, Session as SessionRow
from services import project_acl, project_registry
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService

logger = logging.getLogger(__name__)

SCRATCH_PREFIX = "scratch:"


def scratch_key(session: SessionRow) -> str:
    """Registry key for a session's unbound draft network."""
    return f"{SCRATCH_PREFIX}{session.id}"


def is_scratch_key(key: str | None) -> bool:
    return bool(key) and key.startswith(SCRATCH_PREFIX)


def _authorized_active_project(db: DBSession, session: SessionRow) -> Project | None:
    """
    The session's active project, re-checked against the ACL on every request.

    Re-checking is not paranoia. The pointer is written once at activate time
    but read on every subsequent request, and access can be revoked in between
    — a project membership removed, a user moved between orgs. A pointer is not
    a capability. On denial the pointer is CLEARED rather than 403'd, so the
    user lands on the unbound state instead of a session that fails every
    request until they notice.
    """
    if session.active_project_id is None:
        return None
    project = db.get(Project, session.active_project_id)
    if project is None:
        session.active_project_id = None
        db.commit()
        return None

    from db.models import User

    user = db.get(User, session.user_id)
    if user is None or not project_acl.can_access_project(db, user, project):
        logger.info(
            "active-project pointer cleared: session %s may no longer access %s",
            session.id, project.id,
        )
        session.active_project_id = None
        db.commit()
        return None
    return project


def resolve_for_session(db: DBSession, session: SessionRow) -> tuple[ProjectContext, str]:
    """
    Return ``(context, registry_key)`` for this session's active project.

    Resolution order:
      1. Pointer set and still accessible → the project's resident context,
         hydrated from its org-scoped storage if it is not resident yet.
      2. Otherwise → the session's own scratch context (created on demand).

    Never touches ``PyPSAService._active``: the process foreground belongs to
    the callers that have no session (the solve dispatcher, eviction) and must
    not move because a user opened a page.
    """
    project = _authorized_active_project(db, session)
    if project is not None:
        key = project_registry.registry_key(project)
        resident = PyPSAService.get_context(key)
        if resident is not None:
            return resident, key

        src = project_registry.project_dir(project)
        if (src / "network.nc").exists():
            from routers.projects import _hydrate_context_from_disk

            ctx = PyPSAService.build_context()
            try:
                _hydrate_context_from_disk(ctx, src, project.name)
                project_registry.bind_context(ctx, project)
                PyPSAService.register(key, ctx)
                return ctx, key
            except Exception:  # noqa: BLE001 — a corrupt project must not 500 every route
                logger.exception(
                    "active-project hydrate failed for %s; falling back to scratch",
                    project.id,
                )
        else:
            # Pointed at a project whose blob is gone (deleted behind the app's
            # back). Drop the pointer so the next request starts clean instead
            # of re-failing the same hydrate forever.
            session.active_project_id = None
            db.commit()

    key = scratch_key(session)
    resident = PyPSAService.get_context(key)
    if resident is not None:
        return resident, key
    ctx = PyPSAService.adopt_process_foreground() or PyPSAService.build_context()
    PyPSAService.register(key, ctx)
    return ctx, key


def set_active_project(db: DBSession, session: SessionRow, project: Project | None) -> None:
    """Point the session at `project` (or clear it). Caller must have authorized it."""
    from services.auth_service import set_session_active_project

    set_session_active_project(db, session, project.id if project is not None else None)
