"""Create at grant: the user's one approval creates the project from the
template with the confirmed intake inputs, and the run starts on the new
project (study harness issue 21 §2; owner decision SH21-ACCEPT-1 Q1; GK B
conditions A1-A3 and R1-R3).

`hand_over` runs only after the grant is on record (`approvals.decided(
by="user")`) and the intake digest is checked (`run_intake.check_grant`).
The model plays no part.

* **Once, crash-safe (A1).** A cross-process `durable.file_lock` on a sidecar
  of the host task file (`<task>.handover.lock`) is held across the intent
  record, the creation, the upload copies and the task start. The intent
  (`<task>.handover.json`, written by atomic replace) names the host
  (`origin`), the planned project uuid and name, the grant's digest and
  decision id. A retried or concurrent grant finds it and never creates
  twice. After a crash it goes on from the state it names: `created` starts
  the run, `started` returns it. A row that exists while the intent still
  says `creating` was cut off mid-create. It stays (deleting is never
  automatic) and the host names it. A lock that cannot be taken refuses the
  grant.
* **Same checks as the route (R1).** The project is made by
  `routers.projects._materialise_from_template`, the route's own first half:
  template and params, the owner's org and name rules, the Library series.
* **The foreground never moves (A3, R2).** There is no context swap. The new
  project is opened as `services.study.runner.base_context` opens a run's
  project, and its setup (`_finish_created_project`) runs with that context
  request-scoped. No session's active-project pointer is written.
* **Lock order (R3).** This file lock first, then the new project's
  `mutation_lock`, held across setup, copies and start. The host's
  `mutation_lock` is never taken here. `assistant_tasks._LOCK` is taken last,
  inside `start`.
* **Uploads (A4).** The cited uploads are read from the host's own directory
  and added to the new project. Uploads are content-addressed, so each keeps
  its id and sha256.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from pathlib import Path


VERSION = 1
LOCK_TIMEOUT = 5.0
STATES = ("creating", "created", "started")


class HandoverError(ValueError):
    def __init__(self, kind: str, message: str, **extra):
        super().__init__(message)
        self.kind = kind
        self.extra = extra


def lock_path(host_task_path: Path) -> Path:
    return Path(host_task_path).with_name(Path(host_task_path).stem + ".handover.lock")


def intent_path(host_task_path: Path) -> Path:
    return Path(host_task_path).with_name(Path(host_task_path).stem + ".handover.json")


def read_intent(host_task_path: Path) -> dict | None:
    path = intent_path(host_task_path)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _write(host_task_path: Path, intent: dict) -> None:
    from services.atomic_io import atomic_write_text

    atomic_write_text(
        intent_path(host_task_path), json.dumps(intent, indent=2, sort_keys=True) + "\n"
    )


def _owner(db, owner_id: str):
    from db.models import User

    user = db.get(User, uuid.UUID(owner_id))
    if user is None or user.status != "active":
        raise HandoverError("access", "The run's owner cannot create projects now.")
    return user


def _bound(ctx, row, scratch):
    """The new project's context request-scoped for the block (the runner's
    binding), never the process-wide active one. The grant runs inside the
    host's chat turn, whose turn cell outranks a request binding: a fresh
    cell holds the new project's context, as `retained_results` does, so
    nothing in the block reads or publishes into the host's."""
    import contextlib

    from services import project_registry
    from services.pypsa_service import PyPSAService

    @contextlib.contextmanager
    def scope():
        tokens = PyPSAService.bind_request_scope(
            ctx, project_registry.registry_key(row), scratch
        )
        cell = PyPSAService.bind_turn_cell()
        try:
            yield
        finally:
            if cell is not None:
                PyPSAService._turn_cell.reset(cell)
            PyPSAService.reset_request_scope(tokens)

    return scope()


def _copy_uploads(host_name: str, host_dir: Path, row, upload_ids) -> list[dict]:
    from services import project_registry, upload_service

    new_dir = project_registry.project_dir(row)
    copied = []
    for file_id in upload_ids:
        meta = upload_service.get_upload_meta(host_name, file_id, project_dir=host_dir)
        blob = upload_service.get_upload_bytes(host_name, file_id, project_dir=host_dir)
        added = upload_service.add_upload(
            row.name, blob, meta.filename, meta.mime, project_dir=new_dir
        )
        sha = hashlib.sha256(blob).hexdigest()
        if added.file_id != file_id or (added.sha256 and added.sha256 != sha):
            raise HandoverError(
                "upload_copy_mismatch", "A cited file did not copy byte for byte."
            )
        copied.append({"upload_id": file_id, "sha256": sha})
    return copied


def hand_over(
    host_task_path: Path,
    *,
    owner_id: str,
    origin: dict,
    name: str | None,
    params: dict,
    digest: str,
    decision_id: str,
    host_name: str,
    host_dir: Path,
    uploads=(),
    start,
    template: str = "eh_site",
    lock_timeout: float = LOCK_TIMEOUT,
    precheck=None,
    goal: float | None = None,
) -> dict:
    """Create the project and start the run there, once. `start(row, ctx)`
    starts the run's task on the new project and returns its task id.
    `precheck()` runs under the lock before anything is written: it raises to
    refuse the grant (the host's requirements moved since the brief, GK B (20)
    Q1). Returns the intent record (`state == "started"`)."""
    from harness import durable

    lock = durable.file_lock(
        lock_path(host_task_path),
        timeout=lock_timeout,
        busy="another grant of this plan is creating its project",
    )
    try:
        lock.__enter__()
    except TimeoutError as exc:
        raise HandoverError(
            "handover_busy",
            "Another grant of this plan is creating its project; nothing was granted.",
        ) from exc
    try:
        return _under_lock(
            host_task_path,
            owner_id=owner_id,
            origin=origin,
            name=name,
            params=params,
            digest=digest,
            decision_id=decision_id,
            host_name=host_name,
            host_dir=host_dir,
            uploads=list(uploads),
            start=start,
            template=template,
            precheck=precheck,
            goal=goal,
        )
    finally:
        lock.__exit__(None, None, None)


def _under_lock(
    host_task_path,
    *,
    owner_id,
    origin,
    name,
    params,
    digest,
    decision_id,
    host_name,
    host_dir,
    uploads,
    start,
    template,
    precheck=None,
    goal=None,
):
    from db.models import Project
    from db.session import SessionLocal
    from routers import projects as project_routes
    from services import project_registry
    from services.study.runner import base_context

    intent = read_intent(host_task_path)
    if precheck is not None and (intent is None or intent["state"] == "creating"):
        precheck()  # refuses before any project or intent exists for a fresh grant
    if intent is not None and (intent["digest"], intent["decision_id"]) != (
        digest,
        decision_id,
    ):
        raise HandoverError(
            "handover_conflict",
            "This plan already handed over with other inputs.",
            project_id=intent.get("project_id"),
        )
    if intent is not None and intent["state"] == "started":
        return intent
    if intent is None:
        intent = {
            "version": VERSION,
            "origin": dict(origin),
            "digest": digest,
            "decision_id": decision_id,
            "template": template,
            "name": name,
            "planned_project_id": str(uuid.uuid4()),
            "project_id": None,
            "project_name": None,
            "task_id": None,
            "uploads": [],
            "state": "creating",
        }
        _write(host_task_path, intent)
    planned = uuid.UUID(intent["planned_project_id"])
    with SessionLocal() as db:
        user = _owner(db, owner_id)
        row = db.get(Project, planned)
        if intent["state"] == "creating":
            if row is not None:
                # Cut off mid-create: the project stays and the host names it.
                raise HandoverError(
                    "handover_incomplete",
                    "The project was created but its setup did not "
                    "finish; open it, or start the run there.",
                    project_id=str(row.id),
                    project_name=row.name,
                )
            # The route's own refusal (R1) raises here, before anything exists.
            made = project_routes._materialise_from_template(
                template, name, params, db, user, project_id=planned
            )
            row = made.project
        if row is None:
            raise HandoverError(
                "handover_incomplete",
                "The created project is gone; nothing was started.",
                project_id=intent["project_id"],
            )
        ctx = base_context(row)
        # One hold of the new project's lock across setup, copies and start (A3, R3).
        with ctx.mutation_lock, _bound(ctx, row, f"handover:{origin.get('task_id')}"):
            if intent["state"] == "creating":
                project_routes._finish_created_project(made, db, user, None)
                if goal is not None:
                    # Issue 07 B3b (P5): the user's goal (h/yr) from the grant card,
                    # stored before the save and the run's freeze; a goal field is
                    # outside every input fingerprint (`reliability_goal.GOAL_FIELDS`).
                    with ctx.solver_state_lock:
                        cfg = ctx.solver_state["solver_config"]
                        ctx.solver_state["solver_config"] = dataclasses.replace(
                            cfg, target_lole_h=float(goal)
                        )
                # Saved once, as the user's first save would: a template ships no
                # user_ts, and the run's first solve write-back would otherwise
                # back the network's series into it, so that solve's inputs read
                # as changed and its result as unverified (issue 07 B3b).
                project_routes._save_context(
                    ctx,
                    row.name,
                    expect=row.name,
                    persist_user_ts=True,
                    storage_dir=project_registry.project_dir(row),
                    db=db,
                    user=user,
                    project_row=row,
                )
                intent.update(
                    state="created", project_id=str(row.id), project_name=row.name
                )
                _write(host_task_path, intent)
            intent["uploads"] = _copy_uploads(host_name, Path(host_dir), row, uploads)
            intent["task_id"] = start(row, ctx)
        intent["state"] = "started"
        _write(host_task_path, intent)
    return intent
