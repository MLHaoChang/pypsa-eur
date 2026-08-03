from __future__ import annotations

import uuid
from collections.abc import Iterable

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


def accessible_project_ids(
    db: DBSession, user: User, project_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """
    Of ``project_ids``, the ones ``can_access_project`` would admit — in a
    FIXED number of queries however many ids are passed.

    ``can_access_project`` costs several queries per project (a membership
    lookup, a lineage walk, a root-membership probe). That is fine for one
    project and wrong for a caller holding a LIST: the solve-queue listing is
    polled every 1.5s while a job is active, so a per-project call there is an
    N+1 on a hot path. This resolves the same predicate from three queries —
    the caller's org membership, the org's project graph, and the caller's
    project memberships — and answers the rest in memory.

    Cost is bounded by the ORG's project count, not by the number of ids asked
    about. That is deliberate: the lineage walk needs ancestors, which may not
    be in ``project_ids``, and ``GET /api/projects/`` already loads every row in
    the org (in full, plus a per-project ACL call), so this is the lighter of
    the two.

    ``tests/test_project_acl.py`` asserts this agrees with
    ``can_access_project`` project-for-project; the two must not drift.

    One deliberate divergence: on a cyclic parent chain ``resolve_tree_root``
    raises ``ValueError``, which here would 500 a polled endpoint. This stops
    at the repeat and lets the entry fail closed instead.
    """
    wanted = {pid for pid in project_ids if pid is not None}
    if not wanted:
        return set()

    membership = get_user_membership(db, user.id)
    if membership is None:
        return set()

    rows = db.execute(
        select(Project.id, Project.parent_project_id, Project.created_by).where(
            Project.org_id == membership.org_id
        )
    ).all()
    candidates = wanted & {row.id for row in rows}
    if not candidates or membership.role == "admin":
        # Org admin is `can_access_project`'s see-everything short-circuit.
        return candidates

    parent = {row.id: row.parent_project_id for row in rows}
    creator = {row.id: row.created_by for row in rows}
    member_of = set(
        db.scalars(
            select(ProjectMembership.project_id).where(
                ProjectMembership.user_id == user.id
            )
        ).all()
    )

    allowed: set[uuid.UUID] = set()
    for project_id in candidates:
        node = project_id
        seen: set[uuid.UUID] = set()
        created = False
        while True:
            if creator.get(node) == user.id:
                created = True  # `_user_created_in_lineage`, self included
                break
            seen.add(node)
            nxt = parent.get(node)
            # A parent that is absent from the org graph ends the walk exactly
            # as `resolve_tree_root` does when `db.get` returns None.
            if nxt is None or nxt not in parent or nxt in seen:
                break
            node = nxt
        # Not created anywhere in the lineage, so `node` is the tree root and
        # the remaining grant is a membership on it.
        if created or node in member_of:
            allowed.add(project_id)
    return allowed


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
