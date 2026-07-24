"""
Shared FastAPI dependencies for path-scoped per-project routes (B6).

`ProjectDep` resolves a `{project_id}` path parameter to a RESIDENT
`ProjectContext` WITHOUT touching the active foreground slot — so a request
can read a specific project's data while the user keeps editing a different
one. The resolution is "resolve-or-load-resident": an already-resident ctx is
returned as-is; an on-disk-but-not-resident project is hydrated into a fresh
OFF-TO-THE-SIDE context and registered, so a second read of the same project
is a cheap registry hit.

RESIDENCY (resolve-or-load-resident, capped by B9): each distinct project_id
read through this dependency becomes resident in `PyPSAService._contexts`. B9
caps the registry at `PyPSAService.RESIDENT_CAP` — `register` runs the LRU
eviction, so a path-scoped read that pushes the registry over the cap evicts the
least-recently-interacted non-protected project (saving it first). A resident hit
re-stamps the ctx's recency (a read counts as a touch) so frequently-read
projects stay warm. The active project and any project with a queued/running
solve are never evicted.
"""
from __future__ import annotations

import time

from fastapi import Depends, HTTPException

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def resolve_project_context(project_id: str) -> ProjectContext:
    """
    Resolve a path `project_id` to a resident `ProjectContext`, hydrating it
    from disk (off to the side) if it isn't resident yet. NEVER mutates
    `_active`/the foreground — build/hydrate/register all operate on a context
    distinct from the active one.

    Validation + lookup order:
      1. `_safe_project_dir(project_id)` — 400 on a traversing / unsafe id.
      2. 404 if the project dir has no `network.nc` (never saved).
      3. If already resident (`get_context`) → return it.
      4. Else build a fresh ctx, hydrate it from disk, register it, return it.
    """
    # Lazy import: routers.projects pulls in heavy save/load machinery and
    # imports from routers.network / routers.simulation at call time. Importing
    # it here (inside the dependency, not at module load) keeps deps.py free of
    # any import-order coupling to the router package's wiring in main.py.
    from routers.projects import _hydrate_context_from_disk, _safe_project_dir

    src = _safe_project_dir(project_id)  # raises HTTPException(400) on bad id
    if not (src / "network.nc").exists():
        raise HTTPException(404, f"Project '{project_id}' not found")

    resident = PyPSAService.get_context(project_id)
    if resident is not None:
        # A path-scoped read counts as a touch — re-stamp recency so a warm,
        # frequently-read project doesn't get evicted out from under the reader
        # (B9). Stamp under the registry lock so it's atomic w.r.t. eviction's
        # min(last_interacted_at) victim pick.
        with PyPSAService._registry_lock:
            resident.last_interacted_at = time.monotonic()
        return resident

    ctx = PyPSAService.build_context()
    _hydrate_context_from_disk(ctx, src, project_id)
    # register() stamps recency + runs the B9 cap check (evicting the LRU
    # non-protected project, saving it first). The evicted ids aren't surfaced
    # to this path-scoped reader — the activate endpoint is the channel that
    # tells the frontend to drop caches; a path-scoped read is transient.
    PyPSAService.register(project_id, ctx)
    return ctx


# The dependency callable used by path-scoped routes:
#   def endpoint(ctx: ProjectContext = ProjectDep): ...
ProjectDep = Depends(resolve_project_context)
