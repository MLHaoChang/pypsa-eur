"""
The Site 3D native scene routes (SC-01a; D01/D02 in
`.scratch/site-scene-generation/sc-00-n/decisions.md`, contracts.md §5).

  * ``GET/PUT /{name}/site-anchor``: the confirmed site anchor (revisioned sidecar).
  * ``GET  /{name}/sites/{site_id}/scene``: the canonical scene, or ``unadopted``.
  * ``POST /{name}/sites/{site_id}/scene/adopt``: explicit, digest-checked adoption.
  * ``POST /{name}/sites/{site_id}/scene/proposals``: an in-request native preview.
  * ``GET  …/proposals/{proposal_id}``, ``POST …/apply``, ``POST …/cancel``.

Every route authorizes ``{name}`` with ``ProjectAccessDep`` (404 for a project
the caller may not see). Every write checks the native edit lock first
(``_check_project_lock``: 409 ``project_locked``, never acquiring), then the
scene service takes its per-project mutex across check and write. Request
bodies are size-checked on their raw bytes before JSON parsing (413
``resource_limit``). None of these routes is a chat tool before SC-07
(``services/not_via_chat.py``). Errors are ``{detail: {error_kind, message, …}}``
with one status per kind (``services/site_scene/contract.py``).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import site_service
from services.site_scene import anchor, proposals, store
from services.site_scene.contract import SceneError

router = APIRouter()

# Raw request-body caps, checked before JSON parsing. A proposal carries at
# most `native.MAX_BUILDINGS` footprints; its preview is capped at 4 MiB, and
# the request that produces it gets the same cap.
MAX_ANCHOR_BODY = 4 * 1024
MAX_SMALL_BODY = 4 * 1024
MAX_PROPOSAL_BODY = 4 * 1024 * 1024


def _raise(exc: SceneError) -> HTTPException:
    return HTTPException(exc.status, exc.to_body()["detail"])


async def _call(fn, *args) -> Any:
    """Run a blocking service call (file I/O, the scene mutex) off the event loop."""
    try:
        return await run_in_threadpool(fn, *args)
    except SceneError as exc:
        raise _raise(exc) from exc


async def _json_body(request: Request, cap: int) -> Any:
    raw = await request.body()
    if len(raw) > cap:
        raise _raise(SceneError("resource_limit", f"Too large: the request is {len(raw)} bytes, the limit is {cap}",
                                {"limit": "request_bytes", "max": cap, "actual": len(raw)}))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise _raise(SceneError("invalid_input", "The request is not valid JSON")) from None


def _check_lock(project: AuthorizedProject, db: DBSession, user: User | None) -> None:
    from routers.projects import _check_project_lock
    try:
        target = SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        return          # no registry row, no lock (as routers/flow.py's ack)
    _check_project_lock(db, target, user)


def _owner(user: User | None) -> str | None:
    return str(user.id) if user is not None else None


def _site_known(project: AuthorizedProject, site_id: str) -> None:
    """404 for a malformed or unknown Site id, before any scene I/O."""
    try:
        site_service.site_dir(project.directory, site_id)
        store.legacy_site_digest(site_service.read_sites(project.directory), site_id)
    except site_service.SitesInvalid:
        raise _raise(SceneError("not_found", "Site not found")) from None
    except SceneError as exc:
        raise _raise(exc) from exc


@router.get("/{name}/site-anchor")
def get_site_anchor(project: AuthorizedProject = ProjectAccessDep) -> dict:
    try:
        return anchor.read_anchor(project.directory)
    except SceneError as exc:
        raise _raise(exc) from exc


@router.put("/{name}/site-anchor")
async def put_site_anchor(request: Request, project: AuthorizedProject = ProjectAccessDep,
                          db: DBSession = Depends(get_db),
                          user: User | None = Depends(optional_user)) -> dict:
    await run_in_threadpool(_check_lock, project, db, user)
    body = await _json_body(request, MAX_ANCHOR_BODY)
    if not isinstance(body, dict):
        raise _raise(SceneError("invalid_input", "Site coordinates: send anchor and expected_revision"))
    return await _call(anchor.write_anchor, project.directory, body.get("anchor"), body.get("expected_revision"))


@router.get("/{name}/sites/{site_id}/scene")
def get_scene(site_id: str, project: AuthorizedProject = ProjectAccessDep) -> dict:
    _site_known(project, site_id)
    try:
        return store.read_scene(project.directory, site_id)
    except SceneError as exc:
        raise _raise(exc) from exc


@router.post("/{name}/sites/{site_id}/scene/adopt")
async def adopt_scene(site_id: str, request: Request, project: AuthorizedProject = ProjectAccessDep,
                      db: DBSession = Depends(get_db),
                      user: User | None = Depends(optional_user)) -> dict:
    await run_in_threadpool(_check_lock, project, db, user)
    body = await _json_body(request, MAX_SMALL_BODY)
    await run_in_threadpool(_site_known, project, site_id)
    digest = body.get("expected_digest") if isinstance(body, dict) else None
    if not isinstance(digest, str):
        raise _raise(SceneError("invalid_input", "Adopt: expected_digest is required", {"field": "expected_digest"}))
    await _call(store.adopt, project.directory, site_id, digest)
    return await _call(store.read_scene, project.directory, site_id)


@router.post("/{name}/sites/{site_id}/scene/proposals")
async def create_proposal(site_id: str, request: Request, project: AuthorizedProject = ProjectAccessDep,
                          db: DBSession = Depends(get_db),
                          user: User | None = Depends(optional_user)) -> dict:
    await run_in_threadpool(_check_lock, project, db, user)
    body = await _json_body(request, MAX_PROPOSAL_BODY)
    await run_in_threadpool(_site_known, project, site_id)
    return await _call(proposals.create, project.directory, project.registry_key, _owner(user), site_id, body)


@router.get("/{name}/sites/{site_id}/scene/proposals/{proposal_id}")
def get_proposal(site_id: str, proposal_id: str, project: AuthorizedProject = ProjectAccessDep,
                 user: User | None = Depends(optional_user)) -> dict:
    try:
        return proposals.view(proposals.get(project.registry_key, _owner(user), site_id, proposal_id))
    except SceneError as exc:
        raise _raise(exc) from exc


@router.post("/{name}/sites/{site_id}/scene/proposals/{proposal_id}/apply")
async def apply_proposal(site_id: str, proposal_id: str, request: Request,
                         project: AuthorizedProject = ProjectAccessDep,
                         db: DBSession = Depends(get_db),
                         user: User | None = Depends(optional_user)) -> dict:
    await run_in_threadpool(_check_lock, project, db, user)
    body = await _json_body(request, MAX_SMALL_BODY)
    return await _call(proposals.apply, project.directory, project.registry_key, _owner(user), site_id,
                       proposal_id, body)


@router.post("/{name}/sites/{site_id}/scene/proposals/{proposal_id}/cancel")
def cancel_proposal(site_id: str, proposal_id: str, project: AuthorizedProject = ProjectAccessDep,
                    db: DBSession = Depends(get_db),
                    user: User | None = Depends(optional_user)) -> dict:
    _check_lock(project, db, user)
    try:
        return proposals.cancel(project.registry_key, _owner(user), site_id, proposal_id)
    except SceneError as exc:
        raise _raise(exc) from exc
