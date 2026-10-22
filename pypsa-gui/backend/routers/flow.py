"""
The project flow routes (project-workflow B1, design §3.1).

  * ``GET  /api/projects/{name}/flow``: one row per flow section with its state
    and items, plus ``run_ready`` / ``run_blocker`` (``services/flow_service``).
  * ``POST /api/projects/{name}/flow/ack``: acknowledge a standing warning.
  * ``GET  /api/projects/{name}/flow/scope``: the work cards, this project's
    availability and preset, and its scope (W4a, ``services/flow_scope``).
  * ``PUT  /api/projects/{name}/flow/scope``: store the scope.

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
        raise HTTPException(409, {
            "error_kind": "flow_project_not_open",
            "message": f"Open project '{project.name}' first: the flow is read from the "
                       "loaded network."})


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
        raise HTTPException(exc.status, {"error_kind": exc.error_kind,
                                          "message": exc.message}) from exc
    return {"ack": ack}


def _solver_settings(project: AuthorizedProject) -> dict | None:
    """The project's solver config, for the sub-options it decides (load flow,
    N-1 security): the loaded one when the project is open, else its saved
    ``solver_config.json``. None when neither can be read."""
    import json
    from dataclasses import asdict

    from services import network_lengths
    from services.pypsa_service import PyPSAService

    active = network_lengths.active_project_dir()
    if active is not None and os.path.realpath(active) == os.path.realpath(project.directory):
        try:
            return asdict(PyPSAService.get_solver_state()["solver_config"])
        except Exception:                                # noqa: BLE001 — read the file
            pass
    try:
        raw = json.loads((project.directory / "solver_config.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _no_load(project: AuthorizedProject, template: dict | None) -> bool:
    """#485's generation-only rule, as the flow applies it: the template only
    generates and the network has no Load. The loaded network when the project
    is open, else the template alone (a created plant has no Load)."""
    from services import flow_scope, network_lengths
    from services.pypsa_service import PyPSAService

    if not (template and template.get("generation_only")):
        return False
    loads = None
    active = network_lengths.active_project_dir()
    if active is not None and os.path.realpath(active) == os.path.realpath(project.directory):
        try:
            loads = len(PyPSAService.get_network().loads)
        except Exception:                                # noqa: BLE001 — the template decides
            loads = None
    return flow_scope.no_load_plant(template, loads)


def _store_fixed_sizes(project: AuthorizedProject, sizing: bool, db, user) -> str:
    """Apply the scope's sizing transition to ``fixed_sizes`` and nothing else,
    on the LIVE value, and return the value in force. The open project goes
    through ``update_solver_config`` (the one writer) inside the solver-state
    lock, so a concurrent Solver-settings save is never overwritten; a closed
    project's saved ``solver_config.json`` is edited the same way."""
    import json
    from dataclasses import asdict

    from models.schemas import SolverConfigSchema
    from routers.simulation import _state, update_solver_config
    from services import flow_scope, network_lengths
    from services.atomic_io import atomic_write_text
    from services.pypsa_service import PyPSAService

    active = network_lengths.active_project_dir()
    if active is not None and os.path.realpath(active) == os.path.realpath(project.directory):
        with PyPSAService.get_solver_state_lock():
            current = asdict(_state["solver_config"]).get("fixed_sizes", "off")
            new = flow_scope.fixed_sizes_transition(sizing, current)
            if new is None:
                return current
            update_solver_config(SolverConfigSchema(fixed_sizes=new), db, user)
            return new
    path = project.directory / "solver_config.json"
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        # No saved config: the default is "off". Only a change needs a file.
        if flow_scope.fixed_sizes_transition(sizing, "off") is None:
            return "off"
        raise flow_scope.ScopeError(
            "solver_config_unavailable",
            "Open the project first: Asset sizing is stored in its solver settings, "
            "which are not saved yet.")
    current = raw.get("fixed_sizes", "off")
    new = flow_scope.fixed_sizes_transition(sizing, current)
    if new is None:
        return current
    raw["fixed_sizes"] = new
    atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
    return new


@router.get("/{name}/flow/scope")
def get_flow_scope(project: AuthorizedProject = ProjectAccessDep) -> dict:
    """``{scope, cards, preset_id, preset}``: the project's scope (stored, or its
    template's preset) and the cards the scope component draws. Reads only the
    project's sidecars, so the project need not be open."""
    from services import flow_scope

    template = flow_service.read_template(project.directory)
    settings = _solver_settings(project)
    no_load = _no_load(project, template)
    return {"scope": flow_scope.read_scope(project.directory, template, settings,
                                           no_load=no_load),
            # The size source Asset sizing off dispatches on (read-only here).
            "fixed_sizes": (settings or {}).get("fixed_sizes", "off"),
            **flow_scope.registry(template, settings, no_load=no_load)}


@router.put("/{name}/flow/scope")
def put_flow_scope(body: dict,
                   project: AuthorizedProject = ProjectAccessDep,
                   db: DBSession = Depends(get_db),
                   user: User | None = Depends(optional_user)) -> dict:
    """Store ``{version, groups, options}`` in ``flow_scope.json``. 422 with a
    typed kind: ``flow_scope_invalid`` (shape), ``flow_scope_unknown`` (an id),
    ``flow_scope_unavailable`` (a sub-option not yet available),
    ``flow_scope_required`` (a locked card or sub-option switched off). 409:
    another user's edit lock."""
    from routers.projects import _check_project_lock
    from services import flow_scope

    try:
        lock_target = SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        lock_target = None
    if lock_target is not None:
        _check_project_lock(db, lock_target, user)
    template = flow_service.read_template(project.directory)
    try:
        scope = flow_scope.write_scope(project.directory, body, template,
                                       _solver_settings(project),
                                       no_load=_no_load(project, template),
                                       store_fixed_sizes=lambda w: _store_fixed_sizes(project, w, db, user))
    except flow_scope.ScopeError as exc:
        raise HTTPException(422, {"error_kind": exc.error_kind,
                                  "message": exc.message}) from exc
    return {"scope": scope}
