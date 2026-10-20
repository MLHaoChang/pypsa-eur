"""
The project flow routes (project-workflow B1, design §3.1).

  * ``GET  /api/projects/{name}/flow``: one row per flow section with its state
    and items, plus ``run_ready`` / ``run_blocker`` (``services/flow_service``).
  * ``POST /api/projects/{name}/flow/ack``: acknowledge a standing warning.

Read-only over the existing checks: ``/run`` keeps its own ``validate_for_run``.
The flow reads the FOREGROUND network, so a project that is not the open one
answers 409, as the map-layout import does. Mounted under /api/projects before
the ``/{name}`` catch-all, with ``ProjectAccessDep`` like its siblings.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import flow_service

router = APIRouter()


class FlowAck(BaseModel):
    code: str
    entity: str | None = None


def _require_open(project: AuthorizedProject) -> None:
    from services import network_lengths

    active = network_lengths.active_project_dir()
    if active is None or os.path.realpath(active) != os.path.realpath(project.directory):
        raise HTTPException(
            409, f"Open project '{project.name}' first: the flow is read from the loaded network.")


def _org(project: AuthorizedProject):
    try:
        return uuid.UUID(str(project.org_id))
    except (TypeError, ValueError):
        return None


@router.get("/{name}/flow")
def get_project_flow(project: AuthorizedProject = ProjectAccessDep,
                     db: DBSession = Depends(get_db)) -> dict:
    """``{sections, run_ready, run_blocker, deferred, deferred_reason}``; see
    ``services/flow_service.py`` for the states and the sources."""
    _require_open(project)
    return flow_service.project_flow(project.directory, cache_key=project.registry_key,
                                     db=db, org_id=_org(project))


@router.post("/{name}/flow/ack")
def ack_flow_item(body: FlowAck,
                  project: AuthorizedProject = ProjectAccessDep,
                  db: DBSession = Depends(get_db),
                  user: User | None = Depends(optional_user)) -> dict:
    """Store ``{code, entity, value_hash, at}`` for a standing warning in
    ``flow_acks.json``. 404: no such item; 422: an error (not acknowledgeable);
    409: another user's edit lock, or the preflight is deferred."""
    from routers.projects import _check_project_lock

    _require_open(project)
    try:
        lock_target = SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        lock_target = None          # no registry row, no lock (see adequacy_worksheet._lock_target)
    if lock_target is not None:
        _check_project_lock(db, lock_target, user)
    try:
        ack = flow_service.acknowledge_item(project.directory, body.code, body.entity,
                                            cache_key=project.registry_key, db=db,
                                            org_id=_org(project))
    except flow_service.FlowAckError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"ack": ack}
