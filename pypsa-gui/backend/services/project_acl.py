from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import OrgMembership, Project, ProjectMembership, User
from services.tenancy_service import get_user_membership


def resolve_tree_root(db: DBSession, project: Project) -> Project:
    current = project
    seen: set[uuid.UUID] = set()

    while current.parent_project_id is not None:
        if current.id in seen:
            raise ValueError("project tree contains a cycle")
        seen.add(current.id)

        parent = db.get(Project, current.parent_project_id)
        if parent is None:
            break
        current = parent

    return current


def _get_org_membership(db: DBSession, user: User, project: Project) -> OrgMembership | None:
    membership = get_user_membership(db, user.id)
    if membership is None or membership.org_id != project.org_id:
        return None
    return membership


def _is_org_admin(db: DBSession, user: User, project: Project) -> bool:
    membership = _get_org_membership(db, user, project)
    return membership is not None and membership.role == "admin"


def _user_created_in_lineage(db: DBSession, user: User, project: Project) -> bool:
    current = project
    seen: set[uuid.UUID] = set()

    while True:
        if current.created_by == user.id:
            return True

        if current.parent_project_id is None:
            return False
        if current.id in seen:
            raise ValueError("project tree contains a cycle")
        seen.add(current.id)

        parent = db.get(Project, current.parent_project_id)
        if parent is None:
            return False
        current = parent


def _has_root_membership(db: DBSession, user: User, root_project: Project) -> bool:
    membership = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == root_project.id,
            ProjectMembership.user_id == user.id,
        )
    )
    return membership is not None


def can_access_project(db: DBSession, user: User, project: Project) -> bool:
    membership = _get_org_membership(db, user, project)
    if membership is None:
        return False
    if membership.role == "admin":
        return True
    if _user_created_in_lineage(db, user, project):
        return True

    root_project = resolve_tree_root(db, project)
    return _has_root_membership(db, user, root_project)


def can_manage_membership(db: DBSession, user: User, project: Project) -> bool:
    if _is_org_admin(db, user, project):
        return True

    root_project = resolve_tree_root(db, project)
    return root_project.created_by == user.id and _get_org_membership(db, user, project) is not None


def can_delete_project(db: DBSession, user: User, project: Project) -> bool:
    if not can_access_project(db, user, project):
        return False
    if _is_org_admin(db, user, project):
        return True

    root_project = resolve_tree_root(db, project)
    if root_project.created_by == user.id:
        return True
    if project.parent_project_id is None:
        return False

    return project.created_by == user.id


def list_accessible_projects(db: DBSession, user: User, roots_only: bool = False) -> list[Project]:
    membership = get_user_membership(db, user.id)
    if membership is None:
        return []

    stmt = select(Project).where(Project.org_id == membership.org_id)
    if roots_only:
        stmt = stmt.where(Project.parent_project_id.is_(None))
    stmt = stmt.order_by(Project.name)

    projects = db.scalars(stmt).all()
    return [project for project in projects if can_access_project(db, user, project)]


def ensure_project_access(db: DBSession, user: User, project: Project) -> Project:
    if not can_access_project(db, user, project):
        raise HTTPException(status_code=404, detail="Project not found")
    return project
