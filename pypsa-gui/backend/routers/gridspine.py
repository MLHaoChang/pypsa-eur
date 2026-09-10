"""HTTP for the planning → dynamics pipeline. Wrappers, and nothing else.

Increment 4, task 5. Every handler here does two things: resolve and authorize
the project, then call ONE function in `services/gridspine_service.py`. The
copilot's tools call those same functions directly (task 6), so parity between
the UI and the chatbot is structural — a handler that grew logic of its own
would immediately be a second implementation the chatbot does not have.

Authorization is `ProjectAccessDep`, the same dependency every other
project-scoped router uses: 404 (never 403) for both "no such project" and
"not yours", because a 403 would be an existence oracle across orgs. The row
is fetched after that check, never from client input.

    POST   /api/gridspine/projects                       create a study
    POST   /api/gridspine/{name}/dispatch-source         generate | a finished run | a solved project
    GET    /api/gridspine/{name}/config                  the study config as the next run uses it
    PUT    /api/gridspine/{name}/config                  change some of it (refused while a job is active)
    POST   /api/gridspine/{name}/run                     enqueue (returns the job)
    GET    /api/gridspine/{name}/status                  per-stage state
    GET    /api/gridspine/{name}/snapshots               ranked selection
    GET    /api/gridspine/{name}/ledger                  assumptions, as data
    PUT    /api/gridspine/{name}/templates/{unit}/{param} edit one value
    GET    /api/gridspine/{name}/bundles/{hour}          download a handoff bundle
    POST   /api/gridspine/{name}/readback/{hour}         upload PowerFactory results for that bundle
    GET    /api/gridspine/{name}/readback                what has been read back, per hour
    GET    /api/gridspine/{name}/figures/{hour}/{figure} one comparison as data

The run endpoint returns a solve-queue job: watching and aborting it are the
EXISTING `/api/simulation/queue` endpoints, which is the point of giving the
queue a job kind rather than inventing a second one here.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from db.models import Project, User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import gridspine_service as gs
from services import project_registry
from services.upload_guard import read_capped

router = APIRouter()


class CreateStudy(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    config: dict | None = None


class DispatchSource(BaseModel):
    #: "generate" to solve the unit commitment, "from_dispatch" for a finished
    #: study directory, or "from_project" for the solved network of one of the
    #: caller's capacity-expansion projects (increment 5, D3).
    source: str = "generate"
    from_dispatch: str | None = None
    from_project: str | None = None

    def as_source(self):
        if self.from_project is not None or self.source == "from_project":
            return {"from_project": self.from_project}
        if self.from_dispatch is not None or self.source == "from_dispatch":
            return {"from_dispatch": self.from_dispatch}
        return "generate"


class ConfigPatch(BaseModel):
    """Any subset of the editable study config. `None` means "leave as is" for
    every field but `from_dispatch`, where it means "generate" — so that one
    is carried explicitly by `set_from_dispatch`."""
    hours: int | None = None
    k: int | None = None
    window: int | None = None
    overlap: int | None = None
    screen: bool | None = None
    n2_prune_threshold_pct: float | None = None
    from_dispatch: str | None = None
    set_from_dispatch: bool = False

    def as_patch(self) -> dict:
        patch = {
            key: getattr(self, key)
            for key in ("hours", "k", "window", "overlap", "screen", "n2_prune_threshold_pct")
            if getattr(self, key) is not None
        }
        if self.set_from_dispatch:
            patch["from_dispatch"] = self.from_dispatch
        return patch


class TemplateEdit(BaseModel):
    value: float
    source: str
    #: The spec's ledger provenance. A UI edit is `user`; the chat tool passes
    #: `chat` itself, and never reaches this endpoint.
    edited_by: str = "user"


def _row(proj: AuthorizedProject, db: DBSession) -> Project:
    """The authorized project's row. `proj` has already been ACL-checked."""
    project = db.get(Project, uuid.UUID(proj.uuid))
    if project is None:                      # deleted between the check and here
        raise HTTPException(status_code=404, detail=f"Project '{proj.name}' not found")
    return project


@router.post("/projects")
def create_study(
    body: CreateStudy,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    project_registry.require_user(user)
    return gs.create_study(db, user, body.name, config=body.config)


@router.post("/{name}/dispatch-source")
def set_dispatch_source(
    body: DispatchSource,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    return gs.set_dispatch_source(db, _row(proj, db), body.as_source(), user=user)


@router.get("/{name}/config")
def get_config(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    return gs.get_config(_row(proj, db), db=db)


@router.put("/{name}/config")
def update_config(
    body: ConfigPatch,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    # `db`/`user` are the service's authorization context for `from_dispatch`,
    # which names a directory; every other field in the patch is a scalar.
    return gs.update_config(_row(proj, db), body.as_patch(), db=db, user=user)


@router.post("/{name}/run")
def run_pipeline(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Enqueue the study. Watch or abort it at `/api/simulation/queue`."""
    return gs.run_pipeline(db, _row(proj, db), user=user)


@router.get("/{name}/status")
def get_stage_status(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    return gs.get_stage_status(_row(proj, db))


@router.get("/{name}/snapshots")
def list_ranked_snapshots(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    return gs.list_ranked_snapshots(_row(proj, db))


@router.get("/{name}/ledger")
def get_assumption_ledger(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    return gs.get_assumption_ledger(_row(proj, db))


@router.put("/{name}/templates/{unit_id}/{param}")
def edit_template_param(
    unit_id: str,
    param: str,
    body: TemplateEdit,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
):
    return gs.edit_template_param(
        _row(proj, db), unit_id, param, body.value, body.source, body.edited_by
    )


@router.get("/{name}/bundles/{hour}")
def export_handoff_bundle(
    hour: int,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
):
    """The bundle as a zip. The service writes it inside the project directory,
    so the response never streams a path assembled from client input."""
    path = gs.export_handoff_bundle(_row(proj, db), hour)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/{name}/readback/{hour}")
async def upload_readback(
    hour: int,
    bus: UploadFile = File(...),
    branches: UploadFile | None = File(None),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
):
    """The engineer's PowerFactory export for hour `hour`'s bundle: the bus
    CSV is required, the branch CSV optional (spec stage 6). Both are read
    under the same size cap as every other upload."""
    bus_bytes = await read_capped(bus)
    branch_bytes = await read_capped(branches) if branches is not None else None
    return gs.upload_readback(
        _row(proj, db), hour, bus_bytes, bus.filename,
        branch_bytes, branches.filename if branches is not None else None,
    )


@router.get("/{name}/readback")
def get_readback(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    return gs.get_readback(_row(proj, db))


@router.get("/{name}/figures/{hour}/{figure}")
def fetch_result_figure(
    hour: int,
    figure: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
):
    return gs.fetch_result_figure(_row(proj, db), figure, hour)
