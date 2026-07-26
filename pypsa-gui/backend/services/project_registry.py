"""
Thin DB-registry adapter for the projects router (multi-user tenancy).

The projects router (`routers/projects.py`) was written for single-user,
flat-filesystem storage: every project lives at ``PROJECTS_DIR / <name>`` and
is identified by its name. When ``settings.pypsa_gui_auth_enabled`` is False
that behaviour is preserved verbatim.

When auth is enabled, projects are rows in the ``projects`` table, scoped to an
organization, identified by a UUID, and stored under an org-scoped path
(``storage_path_for(org_id, project_id)``). This module concentrates the
DB-side logic — resolution (UUID *or* name within the caller's org), tree-aware
ACL gating, row creation for roots and scenarios, and project-membership
management — so the router itself only needs a thin ``if auth_enabled()``
branch per endpoint and can keep reusing its path-based bundle IO helpers
against ``Path(project.storage_path)``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from db.models import Project, ProjectMembership, User
from services import project_acl
from services.storage_paths import storage_path_for
from services.tenancy_service import get_user_membership
from settings import get_settings


def auth_enabled() -> bool:
    return get_settings().pypsa_gui_auth_enabled


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def require_user(user: User | None) -> User:
    """Every DB-backed project route needs an authenticated caller."""
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _org_id_for(db: DBSession, user: User) -> uuid.UUID:
    membership = get_user_membership(db, user.id)
    if membership is None:
        raise HTTPException(
            status_code=403,
            detail="User is not a member of any organization",
        )
    return membership.org_id


def _try_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def find_project(db: DBSession, user: User, id_or_name: str) -> Project | None:
    """
    Resolve ``id_or_name`` to a Project in the caller's org — WITHOUT the
    access check. Accepts a UUID string (exact id match) or a project name
    (unique within an org). Returns None when nothing matches in the org.
    """
    org_id = _org_id_for(db, user)

    as_uuid = _try_uuid(id_or_name)
    if as_uuid is not None:
        project = db.get(Project, as_uuid)
        if project is not None and project.org_id == org_id:
            return project
        # A parseable UUID that matches no row in this org is NOT necessarily a
        # dangling id — a project may legitimately be *named* a UUID-shaped
        # string. Fall through to a name lookup so such projects stay
        # resolvable (rather than 404-ing on a valid name).

    return db.scalar(
        select(Project).where(Project.org_id == org_id, Project.name == id_or_name)
    )


def resolve_project(db: DBSession, user: User, id_or_name: str) -> Project:
    """
    Resolve + ACL-gate. Raises 404 both for "no such project in your org" and
    "exists but you can't see it" — the two are deliberately indistinguishable
    to the caller so project existence isn't leaked across the tree/org
    boundary.
    """
    project = find_project(db, user, id_or_name)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # ensure_project_access raises 404 when access is denied.
    return project_acl.ensure_project_access(db, user, project)


def project_dir(project: Project) -> Path:
    """Materialise the project's org-scoped storage directory."""
    path = Path(project.storage_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_root(
    db: DBSession,
    user: User,
    name: str,
    *,
    scenario_description: str | None = None,
) -> Project:
    """Insert a root (parent-less) project row for the caller's org."""
    org_id = _org_id_for(db, user)
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        org_id=org_id,
        name=name,
        created_by=user.id,
        storage_path=str(storage_path_for(org_id, project_id)),
        parent_project_id=None,
        scenario_description=scenario_description,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(project)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Project '{name}' already exists"
        ) from exc
    db.refresh(project)
    return project


def create_scenario(
    db: DBSession,
    user: User,
    base: Project,
    name: str,
    *,
    scenario_description: str | None = None,
) -> Project:
    """
    Insert a child project row branched off ``base`` — same org, with
    ``parent_project_id`` set so the tree-aware ACL grants inherited access.
    """
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        org_id=base.org_id,
        name=name,
        created_by=user.id,
        storage_path=str(storage_path_for(base.org_id, project_id)),
        parent_project_id=base.id,
        scenario_description=scenario_description,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(project)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Project '{name}' already exists"
        ) from exc
    db.refresh(project)
    return project


def rename_project(db: DBSession, project: Project, new_name: str) -> Project:
    """Update a project's name in place (storage_path is UUID-keyed, so it
    does not move). Raises 409 on an org-level name collision."""
    project.name = new_name
    project.updated_at = _now()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Project '{new_name}' already exists"
        ) from exc
    db.refresh(project)
    return project


def descendants(db: DBSession, project: Project) -> list[Project]:
    """All transitive children of ``project`` (BFS order, leaves last)."""
    out: list[Project] = []
    seen: set[uuid.UUID] = set()
    frontier: list[uuid.UUID] = [project.id]
    while frontier:
        next_frontier: list[uuid.UUID] = []
        for parent_id in frontier:
            children = db.scalars(
                select(Project).where(Project.parent_project_id == parent_id)
            ).all()
            for child in children:
                if child.id in seen:
                    continue
                seen.add(child.id)
                out.append(child)
                next_frontier.append(child.id)
        frontier = next_frontier
    return out


def direct_children(db: DBSession, project: Project) -> list[Project]:
    return list(
        db.scalars(
            select(Project).where(Project.parent_project_id == project.id)
        ).all()
    )


def delete_project_row(db: DBSession, project: Project) -> None:
    db.delete(project)
    db.commit()


# ── Project membership (assignment) ──────────────────────────────────────────
# Assignments always attach to the TREE ROOT: the ACL grants a member access to
# every project in a tree via a single root membership, so managing membership
# on a child transparently manages the root's list.

def list_root_members(db: DBSession, project: Project) -> list[dict[str, str | None]]:
    root = project_acl.resolve_tree_root(db, project)
    rows = db.scalars(
        select(ProjectMembership).where(ProjectMembership.project_id == root.id)
    ).all()
    members: list[dict[str, str | None]] = []
    for pm in rows:
        member_user = db.get(User, pm.user_id)
        members.append(
            {
                "user_id": str(pm.user_id),
                "email": member_user.email if member_user is not None else None,
            }
        )
    return members


def set_root_members(
    db: DBSession,
    actor: User,
    project: Project,
    user_ids: list[uuid.UUID],
) -> list[dict[str, str | None]]:
    root = project_acl.resolve_tree_root(db, project)

    desired: set[uuid.UUID] = set(user_ids)
    for member_id in desired:
        membership = get_user_membership(db, member_id)
        if membership is None or membership.org_id != root.org_id:
            raise HTTPException(
                status_code=400,
                detail=f"User {member_id} is not a member of this organization",
            )

    existing = {
        pm.user_id: pm
        for pm in db.scalars(
            select(ProjectMembership).where(ProjectMembership.project_id == root.id)
        ).all()
    }

    for member_id in desired - set(existing):
        db.add(
            ProjectMembership(
                project_id=root.id,
                user_id=member_id,
                assigned_by=actor.id,
                assigned_at=_now(),
            )
        )
    for member_id in set(existing) - desired:
        db.delete(existing[member_id])

    db.commit()
    return list_root_members(db, root)
