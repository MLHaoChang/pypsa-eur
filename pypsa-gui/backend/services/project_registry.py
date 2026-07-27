"""
Thin DB-registry adapter for the projects router (multi-user tenancy).

The projects router (`routers/projects.py`) was written for single-user,
flat-filesystem storage: every project lives at ``PROJECTS_DIR / <name>`` and
is identified by its name. When ``settings.pypsa_gui_auth_enabled`` is False
that behaviour is preserved verbatim.

When auth is enabled, projects are rows in the ``projects`` table, scoped to an
organization and identified by a UUID. This module concentrates the DB-side
logic — resolution (UUID *or* name within the caller's org), tree-aware ACL
gating, row creation for roots and scenarios, and project-membership
management — so the router needs only a thin ``if auth_enabled()`` branch per
endpoint.

**Storage paths changed in phase 1b (E1/E2), and this docstring used to
describe the shape they replaced.** A project's directory now carries its
NAME, and ``storage_path`` is stored RELATIVE to ``projects_root`` —
``Belgium Grid`` locally, ``<org_uuid>/Belgium Grid`` on the web. Nothing may
read the raw column: ``project_dir`` rejoins it with the configured root,
``ensure_project_dir`` adds the mkdir, and
``tests/test_project_dir_resolver.py`` fails the build for any other access.
"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import Project, ProjectMembership, User
from services import project_acl
from services.storage_paths import (
    allocate_storage_path,
    storage_path_for,
    storage_value,
    taken_names,
    use_org_segment,
)
from services.tenancy_service import get_user_membership
from settings import get_settings

logger = logging.getLogger(__name__)


def auth_enabled() -> bool:
    return get_settings().pypsa_gui_auth_enabled


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def require_user(user: User | None) -> User:
    """Every DB-backed project route needs an authenticated caller."""
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _org_id_or_none(db: DBSession, user: User) -> uuid.UUID | None:
    membership = get_user_membership(db, user.id)
    return membership.org_id if membership is not None else None


def _org_id_for(db: DBSession, user: User) -> uuid.UUID:
    """
    The caller's org, or 403.

    403 is correct HERE because this is the CREATE path: the caller is trying
    to make a new project and genuinely lacks the capability, and no existing
    resource is implied by the answer. LOOKUP paths must not use it — see
    `find_project`, which degrades a missing membership to "no match" so it
    ends in the same 404 as a project that does not exist.
    """
    org_id = _org_id_or_none(db, user)
    if org_id is None:
        raise HTTPException(
            status_code=403,
            detail="User is not a member of any organization",
        )
    return org_id


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

    A caller with no org membership resolves to None rather than 403. Every
    lookup outcome the caller is not entitled to must be INDISTINGUISHABLE:
    "no such project", "exists in another org", and "you belong to no org" all
    end as the same 404 with the same body. A 403 on any of them is an
    existence oracle — it tells the caller their probe was well-formed enough
    to reach an authorization decision, which is information about another
    tenant's data.
    """
    org_id = _org_id_or_none(db, user)
    if org_id is None:
        return None

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


def registry_key(project: Project) -> str:
    """
    Key for the process-global resident-context registry (Step 0a).

    Must match `ProjectContext.registry_key`, which is the accessor the service
    layer uses; this function is the DB-side producer of the same string. The
    old key was the project name — unique per org, but the registry is per
    process, so two orgs' `Baseline` shared one slot.
    """
    return f"{project.org_id}:{project.id}"


def bind_context(ctx, project: Project) -> None:
    """Stamp a `ProjectContext` with this project's name + tenant identity."""
    ctx.loaded_project = project.name
    ctx.org_id = str(project.org_id)
    ctx.project_uuid = str(project.id)
    # Resolved, not raw. `ProjectContext.storage_dir` is turned back into a
    # `Path` by four other modules (`chat_service`, `upload_service`,
    # `pypsa_service`, `solve_queue`), and once `storage_path` can be relative
    # the raw column would resolve there against the process CWD.
    ctx.storage_dir = str(project_dir(project))


def project_dir(project: Project) -> Path:
    """
    Resolve a row to its storage directory. **Creates nothing.**

    `routers/projects.py` calls this for every row of `GET /api/projects/`; a
    mkdir here resurrects the directory of a project the user deleted in
    Finder and defeats `storage_reconcile`'s missing-dir detection. Callers
    that are about to write use `ensure_project_dir`.

    Both path formats are accepted permanently, not as a migration window:
    rows created before migration 0003 are absolute, rows outside the projects
    root stay absolute by design, and a restored backup can carry either.
    """
    path = Path(project.storage_path)
    if path.is_absolute():
        return path
    return Path(get_settings().projects_root) / path


def ensure_project_dir(project: Project) -> Path:
    """`project_dir` plus the mkdir. For callers about to WRITE."""
    path = project_dir(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def stores_absolute_path(project: Project) -> bool:
    """
    True for a row migration 0003 could not convert.

    Either it pointed outside `projects_root` — left alone by design, because
    a rewrite that invalidates a working path is worse than one that does
    nothing — or 0003 never ran against this database. `tools/import_legacy
    --rebase-db` is what resolves either case.

    Lives here, next to the resolver, because `project_dir` deliberately
    erases the distinction and this is the one place that needs it back.
    """
    return Path(project.storage_path).is_absolute()


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
    segment = use_org_segment()
    project = Project(
        id=project_id,
        org_id=org_id,
        name=name,
        created_by=user.id,
        storage_path=storage_value(
            allocate_storage_path(db, org_id, project_id, name, org_segment=segment)
        ),
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
    segment = use_org_segment()
    project = Project(
        id=project_id,
        org_id=base.org_id,
        name=name,
        created_by=user.id,
        storage_path=storage_value(
            allocate_storage_path(
                db, base.org_id, project_id, name, org_segment=segment
            )
        ),
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
    """
    Rename a project — and MOVE its directory, since E1 puts the name on disk.

    The row and the filesystem cannot be changed in one transaction, so the
    order is chosen to make every failure recoverable:

      1. resolve the new directory and refuse a collision **before** the
         commit. Doing it after leaves a committed row pointing at a directory
         that never moved, with nothing to put it back.
      2. commit the row, keeping the existing `IntegrityError` -> 409 handler.
         Its old comment ("storage_path is UUID-keyed, so it does not move")
         is what E1 invalidates; the handler itself is still right, and the
         unique index on `(org_id, storage_path)` now backs it up.
      3. move the directory, compensating the row if the move fails.

    `taken` excludes this project's own directory, or a rename to a name that
    sanitises back to the current directory would allocate ` (2)` and move the
    data for nothing.
    """
    old_dir = project_dir(project)
    old_name, old_rel = project.name, project.storage_path

    if not _may_move_directory(project, old_dir):
        # Rename the ROW only. The directory keeps the name it was created
        # with, which is stale but readable, resolvable and safe.
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

    segment = use_org_segment()
    new_rel = allocate_storage_path(
        db, project.org_id, project.id, new_name,
        org_segment=segment, exclude={old_dir.name},
    )
    new_dir = Path(get_settings().projects_root) / new_rel
    if new_dir.exists() and new_dir != old_dir:
        raise HTTPException(
            status_code=409,
            detail=f"A directory named '{new_dir.name}' already exists",
        )

    project.name = new_name
    project.storage_path = storage_value(new_rel)
    project.updated_at = _now()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Project '{new_name}' already exists"
        ) from exc

    if old_dir.exists() and old_dir != new_dir:
        try:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old_dir, new_dir)
        except OSError:
            # The row is already committed — there is no transaction spanning
            # the database and the filesystem — so put BOTH columns back or the
            # project points at a directory that does not exist and vanishes
            # from the UI. `name` too: restoring only `storage_path` leaves the
            # project renamed in the database and not on disk.
            _compensate(db, project, old_name, old_rel)
            raise

    _rebind_resident_contexts(project)
    db.refresh(project)
    return project


def _may_move_directory(project: Project, old_dir: Path) -> bool:
    """
    Whether renaming this project may also move its directory.

    **Local mode only, and only for a row inside the projects root.** Two
    independent reasons, both found by review:

      * Four subsystems cache the resolved directory on the resident
        `ProjectContext` (`bind_context` -> `ctx.storage_dir`, read back by
        `upload_service`, `chat_service`, `solve_queue` and eviction). Eviction
        writes through it with `mkdir(parents=True, exist_ok=True)`, so a moved
        directory is silently RECREATED at the old path and the user's work
        lands in an orphan no row references. In local mode there is one
        process (D11's single-instance lock) and `_rebind_resident_contexts`
        can fix every cached copy; across replicas it cannot, and rename takes
        no project lock.
      * A row whose path is absolute lives outside `projects_root` by design
        (`stores_absolute_path`). Moving it into the root on a rename
        contradicts that, and on a hosted deployment with projects on a
        separate mount it turns a rename into a multi-gigabyte cross-volume
        copy inside one HTTP request.

    Web mode therefore renames the row and leaves the directory — which is
    exactly what it did before this phase, when `storage_path` was UUID-keyed.
    """
    if not local_mode.is_local_mode():
        return False
    if stores_absolute_path(project):
        return False
    return old_dir.is_relative_to(Path(get_settings().projects_root))


def _compensate(db: DBSession, project: Project, name: str, storage_path: str) -> None:
    """Put a committed row back after the filesystem half failed."""
    project.name = name
    project.storage_path = storage_path
    try:
        db.commit()
    except Exception:  # noqa: BLE001 — the original error is the one to report
        db.rollback()
        logger.exception(
            "rename compensation failed for %s; row and disk now disagree",
            project.id,
        )


def _rebind_resident_contexts(project: Project) -> None:
    """
    Point any resident `ProjectContext` at the directory's new location.

    `rekey_context` keys on `org:uuid`, which a rename does not change, so
    without this a context that is resident-but-not-active keeps the old path
    and the next save or eviction writes there.
    """
    try:
        from services.pypsa_service import PyPSAService

        resolved = str(project_dir(project))
        for ctx in (
            PyPSAService.get_context(registry_key(project)),
            PyPSAService.get_active_context(),
        ):
            if ctx is not None and getattr(ctx, "project_uuid", None) == str(project.id):
                ctx.storage_dir = resolved
                ctx.loaded_project = project.name
    except Exception:  # noqa: BLE001 — a rename must not fail on bookkeeping
        logger.exception("could not rebind resident contexts after rename")


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
