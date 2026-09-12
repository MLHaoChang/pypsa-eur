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

import uuid
from types import SimpleNamespace

from fastapi import Depends
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.adequacy.stress import (
    StressValidationError,
    load_scenarios,
    save_scenarios,
)
from services.adequacy.worksheet import (
    WorksheetValidationError,
    load_worksheet,
    save_worksheet,
)

router = APIRouter()


class WorksheetPut(BaseModel):
    manual_rows: list[dict] = Field(default_factory=list)
    overlays: dict[str, dict] = Field(default_factory=dict)


def _lock_target(project: AuthorizedProject) -> SimpleNamespace | None:
    """
    Adapt an `AuthorizedProject` into the shape `_check_project_lock` needs.

    Third copy of this adapter (`routers/uploads.py`, `routers/snapshots.py` have
    the other two), kept local for the same reason they are: a sibling router
    should not import a private helper from another. That there are now three is
    a signal about `ProjectAccessDep`-only routers, tracked separately — not a
    reason to hold up a cross-user write fix.
    """
    try:
        return SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        # Not a real project uuid, so there is no row in the lock table to check
        # and nothing to enforce. `require_project_access` always resolves a real
        # DB project, whose id IS a uuid, so this can only be an in-process or
        # unit-test caller constructing an AuthorizedProject by hand (three tests
        # in tests/test_adequacy_worksheet.py and tests/test_adequacy_stress.py
        # pass uuid="u-1" and call the handler directly with no db/user, which is
        # how the first cut of this guard broke them with a ValueError).
        #
        # Same reasoning `routers/projects.put_layout` documents for a legacy
        # flat-storage project with no DB row: no registry entry, no lock to
        # speak of. NOT a bypass -- an attacker cannot choose this value.
        return None


@router.get("/{name}/worksheet")
def get_worksheet(project: AuthorizedProject = ProjectAccessDep) -> dict:
    return load_worksheet(project.directory)


@router.put("/{name}/worksheet")
def put_worksheet(body: WorksheetPut,
                  project: AuthorizedProject = ProjectAccessDep,
                  db: DBSession = Depends(get_db),
                  user: User | None = Depends(optional_user)) -> dict:
    # A live FOREIGN edit lock refuses this write. `ProjectAccessDep` answers
    # "may this caller SEE this project", which is not "may they write it while
    # someone else holds the edit lock" — and `save_worksheet` REPLACES the
    # sidecar wholesale and bumps `version`, which the holder's client reads as
    # last-write-wins, so a non-holder's write silently became the holder's
    # authoritative copy. The file is also in `projects._BUNDLE_FILES`, so it
    # propagated into later bundles and snapshots.
    #
    # `/api/projects/` is deliberately outside the middleware's gated prefixes
    # because this family enforces in-handler; these two PUTs never got it.
    # Check-only, not acquire: editing a sidecar is not claiming the project.
    from routers.projects import _check_project_lock

    _lock = _lock_target(project)
    if _lock is not None:
        _check_project_lock(db, _lock, user)
    try:
        return save_worksheet(project.directory,
                              manual_rows=body.manual_rows,
                              overlays=body.overlays)
    except WorksheetValidationError as exc:
        raise HTTPException(422, str(exc))


class StressScenariosPut(BaseModel):
    scenarios: list[dict] = Field(default_factory=list)


@router.get("/{name}/stress_scenarios")
def get_stress_scenarios(project: AuthorizedProject = ProjectAccessDep) -> dict:
    """Class-C stress-scenario registry (adequacy Phase 4 Task 3) — same
    sidecar pattern and authorization as the worksheet."""
    return {"scenarios": load_scenarios(project.directory)}


@router.put("/{name}/stress_scenarios")
def put_stress_scenarios(body: StressScenariosPut,
                         project: AuthorizedProject = ProjectAccessDep,
                         db: DBSession = Depends(get_db),
                         user: User | None = Depends(optional_user)) -> dict:
    # Same reasoning as `put_worksheet` above: whole-document replace of a
    # bundled sidecar, so a foreign lock must refuse it. Check-only.
    from routers.projects import _check_project_lock

    _lock = _lock_target(project)
    if _lock is not None:
        _check_project_lock(db, _lock, user)
    try:
        return {"scenarios": save_scenarios(project.directory, body.scenarios)}
    except StressValidationError as exc:
        raise HTTPException(422, str(exc))
