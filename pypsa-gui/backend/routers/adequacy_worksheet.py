"""
Per-project FMEA worksheet routes (Phase 3 Task 1).

Mounted under /api/projects (before the `/{name}` catch-all) with the same
ProjectAccessDep authorization as compare-state: the handler never derives a
path from client input. GET returns the manual state; PUT replaces it whole
(payloads are small — a couple hundred rows at most by the sidecar's caps)
and echoes the bumped version for the UI's last-write-wins awareness.
Computed rows are NOT served here — they come from /results/copt and are
merged client-side (plan: the server never mixes foreground network state
with on-disk project state).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from routers.deps import AuthorizedProject, ProjectAccessDep
from services.adequacy.worksheet import (
    WorksheetValidationError,
    load_worksheet,
    save_worksheet,
)

router = APIRouter()


class WorksheetPut(BaseModel):
    manual_rows: list[dict] = Field(default_factory=list)
    overlays: dict[str, dict] = Field(default_factory=dict)


@router.get("/{name}/worksheet")
def get_worksheet(project: AuthorizedProject = ProjectAccessDep) -> dict:
    return load_worksheet(project.directory)


@router.put("/{name}/worksheet")
def put_worksheet(body: WorksheetPut,
                  project: AuthorizedProject = ProjectAccessDep) -> dict:
    try:
        return save_worksheet(project.directory,
                              manual_rows=body.manual_rows,
                              overlays=body.overlays)
    except WorksheetValidationError as exc:
        raise HTTPException(422, str(exc))
