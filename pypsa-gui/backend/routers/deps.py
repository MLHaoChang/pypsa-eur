"""
Shared FastAPI dependencies for path-scoped per-project routes (B6).

`ProjectDep` resolves a `{project_id}` path parameter to a RESIDENT
`ProjectContext` WITHOUT touching the active foreground slot — so a request
can read a specific project's data while the user keeps editing a different
one. The resolution is "resolve-or-load-resident": an already-resident ctx is
returned as-is; an on-disk-but-not-resident project is hydrated into a fresh
OFF-TO-THE-SIDE context and registered, so a second read of the same project
is a cheap registry hit.

AUTHORIZATION (Step 0a): resolution goes through `project_registry.resolve_project`,
which resolves the id-or-name **within the caller's org** and ACL-gates the
result, raising 404 for both "no such project" and "exists but you may not see
it". Before Step 0a this dependency called `_safe_project_dir(project_id)` — a
pure path join under a shared root — so any authenticated user could read any
org's project by naming it. The registry lookup is not an optimisation here; it
is the authorization boundary.

RESIDENCY (resolve-or-load-resident, capped by B9): each distinct project read
through this dependency becomes resident in `PyPSAService._contexts`, keyed by
`(org_id, project_uuid)` rather than by name — two orgs' `Baseline` are two
slots, not one. B9 caps the registry at `PyPSAService.RESIDENT_CAP`; `register`
runs the LRU eviction, so a path-scoped read that pushes the registry over the
cap evicts the least-recently-interacted non-protected project (saving it
first). A resident hit re-stamps the ctx's recency (a read counts as a touch) so
frequently-read projects stay warm. The active project and any project with a
queued/running solve are never evicted.
"""
from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


@dataclass(frozen=True)
class AuthorizedProject:
    """
    A `{name}` path parameter that has been resolved AND authorized.

    Carries the canonical display `name` (the caller may have addressed the
    project by uuid) and the org-scoped storage `directory`, so a handler never
    re-derives a path from client input. `uuid` and `org_id` are the identity a
    handler needs for registry keys and lock rows.
    """

    name: str
    directory: pathlib.Path
    uuid: str
    org_id: str
    registry_key: str


def require_project_access(
    name: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> AuthorizedProject:
    """
    Authorize a `{name}` path parameter against the caller's org and ACL.

    This is the dependency the plan's Step 0a mandates on every route that
    carries a real project path parameter — `uploads` (6), `snapshots` (4),
    `compare` (2). Those handlers previously called `_safe_project_dir(name)`,
    which is a *path-traversal* guard, not an *authorization* check: it happily
    resolved another org's project because the projects root is shared. Any
    authenticated user could therefore read, snapshot, or overwrite any org's
    project by naming it.

    Raises 404 — never 403 — for both "no such project" and "exists but you may
    not see it", matching `project_acl.ensure_project_access`. A 403 here would
    be an existence oracle: it tells the caller the name is taken in some other
    org, which is exactly the cross-tenant leak being closed.
    """
    from services import project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, name)
    return AuthorizedProject(
        name=project.name,
        directory=project_registry.project_dir(project),
        uuid=str(project.id),
        org_id=str(project.org_id),
        registry_key=project_registry.registry_key(project),
    )


# The dependency callable used by project-scoped routes:
#   def endpoint(proj: AuthorizedProject = ProjectAccessDep): ...
ProjectAccessDep = Depends(require_project_access)


def resolve_project_context(
    project_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> ProjectContext:
    """
    Resolve a path `project_id` to a resident `ProjectContext`, hydrating it
    from disk (off to the side) if it isn't resident yet. NEVER mutates
    `_active`/the foreground — build/hydrate/register all operate on a context
    distinct from the active one.

    Validation + lookup order:
      1. `project_registry.resolve_project` — 401 unauthenticated, 404 for
         "not in your org" AND "exists but not accessible" (indistinguishable
         by design, so project existence isn't leaked across the boundary).
      2. 404 if the project's storage has no `network.nc` (never saved).
      3. If already resident (`get_context`) → return it.
      4. Else build a fresh ctx, hydrate it from disk, register it, return it.
    """
    # Lazy import: routers.projects pulls in heavy save/load machinery and
    # imports from routers.network / routers.simulation at call time. Importing
    # it here (inside the dependency, not at module load) keeps deps.py free of
    # any import-order coupling to the router package's wiring in main.py.
    from routers.projects import _hydrate_context_from_disk
    from services import project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, project_id)
    src = project_registry.project_dir(project)
    if not (src / "network.nc").exists():
        raise HTTPException(404, f"Project '{project_id}' not found")

    key = project_registry.registry_key(project)
    resident = PyPSAService.get_context(key)
    if resident is not None:
        # A path-scoped read counts as a touch — re-stamp recency so a warm,
        # frequently-read project doesn't get evicted out from under the reader
        # (B9). Stamp under the registry lock so it's atomic w.r.t. eviction's
        # min(last_interacted_at) victim pick.
        with PyPSAService._registry_lock:
            resident.last_interacted_at = time.monotonic()
        return resident

    ctx = PyPSAService.build_context()
    _hydrate_context_from_disk(ctx, src, project.name)
    project_registry.bind_context(ctx, project)
    # register() stamps recency + runs the B9 cap check (evicting the LRU
    # non-protected project, saving it first). The evicted ids aren't surfaced
    # to this path-scoped reader — the activate endpoint is the channel that
    # tells the frontend to drop caches; a path-scoped read is transient.
    PyPSAService.register(key, ctx)
    return ctx


# The dependency callable used by path-scoped routes:
#   def endpoint(ctx: ProjectContext = ProjectDep): ...
ProjectDep = Depends(resolve_project_context)
