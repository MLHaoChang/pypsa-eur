"""
Decision-study routes (guided investment study, MVP-1 S1).

Mounted at ``/api/projects/{name}/studies`` (``main.py``), before the
``projects`` router's ``/{name}`` catch-alls and with the same
``fs_permission.require_file_access`` router guard, because every handler
reads or writes under the projects root.

Authorisation is the PATH project's (review v1 B7):

* every handler takes ``ProjectAccessDep``, which resolves ``{name}`` inside
  the caller's org and ACL and raises **404**, never 403;
* ``{study_id}`` is looked up only inside that project's ``studies/``
  directory (``services/study/store.py``), so a real id addressed through
  another project is 404 — there is no global id index to leak through;
* writes call the check-only ``_check_project_lock`` against the PATH
  project; S4's run and abort will use ``_enforce_project_lock``.

The middleware's solver-in-flight gate exempts these routes by one anchored
pattern (``main._SOLVER_BLOCKING_EXEMPT_PATTERNS``, review v2 BC-3): a study
write never touches the resident network, and the gate reads the ACTIVE
project, which need not be this one. Neither middleware prefix list names
them.

Availability (review v2 BC-6): in auth (multi-user) mode every route refuses
unconditionally until OPEN-ITEMS 1 is closed. ``PYPSAGUI_DECISION_STUDIES=1``
enables them in local mode only. The refusal is a router dependency, so it
runs before the project is resolved and cannot serve as an existence oracle.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, UTC
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import User
from db.session import get_db
from deps import optional_user
from models.study import (
    MVP1_BASIS,
    MVP1_PERSPECTIVES,
    DecisionStudy,
    FinancialBasis,
    Perspective,
)
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.study import FLAG_ENV, flag_set, store

OPEN_ITEMS_1 = (
    "OPEN-ITEMS 1 (docs/superpowers/OPEN-ITEMS.md, item 1: the user-timeseries "
    "store is a process global shared across tenants)"
)


def require_decision_studies_enabled() -> None:
    """
    Router dependency: refuse unless local mode AND the flag.

    404, consistent with the non-member answer and with
    ``local_mode.reject_unless_local_mode``: where the surface is off it does
    not exist. The ``code`` tells the client which of the two reasons applies.
    """
    if not local_mode.is_local_mode():
        raise HTTPException(status_code=404, detail={
            "code": "decision_studies_unavailable",
            "message": (
                "Decision studies are not available in multi-user mode until "
                f"{OPEN_ITEMS_1} is closed. {FLAG_ENV} does not change this."
            ),
        })
    if not flag_set():
        raise HTTPException(status_code=404, detail={
            "code": "decision_studies_disabled",
            "message": (
                f"Decision studies are off. Set {FLAG_ENV}=1 to enable them "
                "in the desktop app."
            ),
        })


router = APIRouter(dependencies=[Depends(require_decision_studies_enabled)])


def _refuse_unless_enabled() -> None:
    """
    The same refusal, repeated inside every handler. House rule (see
    `_enforce_project_lock` and `chat_tools._route`): a handler called as a
    plain function skips its router dependencies, so the dependency alone
    protects HTTP and nothing else. Looked up on the module at call time so a
    test may replace it; `Depends` above captured the original.
    """
    require_decision_studies_enabled()

# Load-modify-save of one sidecar is not atomic across the threadpool; two
# per-step PATCHes to the same study must not drop each other's answers.
_WRITE_LOCK = threading.Lock()


class StudyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    name: str = Field(min_length=1, max_length=120)
    intake: dict[str, Any] = Field(default_factory=dict)


class StudySettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perspective: Perspective | None = None
    basis: FinancialBasis | None = None
    currency_year: int | None = Field(default=None, ge=1900, le=2200)


class StudyPatch(BaseModel):
    """
    Per-step apply (spec decision 18): each PATCH commits what it carries.

    ``intake`` merges key by key into the stored answers. With ``step``, the
    PATCH may only write that step's own key, so one step cannot overwrite
    another's answers.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    step: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    intake: dict[str, Any] | None = None
    settings: StudySettingsPatch | None = None


def _lock_target(project: AuthorizedProject) -> SimpleNamespace | None:
    """
    Adapt an `AuthorizedProject` into the shape `_check_project_lock` needs.

    Fourth copy of this adapter (`routers/adequacy_worksheet.py`,
    `routers/uploads.py`, `routers/snapshots.py` have the others), local for
    the reason they give: a sibling router should not import another's
    private helper. A non-uuid id cannot come from `require_project_access`,
    only from an in-process caller building the dataclass by hand.
    """
    try:
        return SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        return None


def _check_lock(db: DBSession, project: AuthorizedProject, user: User | None) -> None:
    from routers.projects import _check_project_lock

    target = _lock_target(project)
    if target is not None:
        _check_project_lock(db, target, user)


def _project_dir(project: AuthorizedProject):
    if not project.directory.is_dir():
        raise HTTPException(404, f"Project '{project.name}' not found")
    return project.directory


def _load(project: AuthorizedProject, study_id: str) -> DecisionStudy:
    try:
        study = store.load_study(_project_dir(project), study_id)
    except store.StudyNotFound:
        raise HTTPException(404, "Study not found") from None
    except store.StudyUnreadable:
        raise HTTPException(500, detail={
            "code": "study_unreadable",
            "message": "The study record on disk cannot be read.",
        }) from None
    # The containing project is the study's base project: a copy carried by a
    # bundle import, a Save-As, a user scenario or a snapshot must not answer
    # with its origin's uuid — nor with its origin's option forks, which the
    # copy does not own. S4 verifies fork ownership server-side on top of
    # this (review gate S1 SB-4).
    if study.base_project != project.uuid:
        study = study.model_copy(update={"base_project": project.uuid,
                                         "option_projects": []})
    return study


def _save(project: AuthorizedProject, study: DecisionStudy) -> dict:
    try:
        store.save_study(_project_dir(project), study)
    except store.StudyTooLarge as exc:
        raise HTTPException(413, str(exc)) from None
    return _out(study)


def _out(study: DecisionStudy) -> dict:
    return study.model_dump(mode="json", by_alias=True)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _refuse_outside_mvp1(settings: StudySettingsPatch | None) -> None:
    """
    A basis or perspective nothing downstream implements is refused, not
    stored and silently ignored (spec decision 7; plan honest scope).
    """
    if settings is None:
        return
    if (settings.perspective is not None
            and settings.perspective not in MVP1_PERSPECTIVES):
        raise HTTPException(422, (
            f"perspective {settings.perspective.value!r} is not implemented; "
            "MVP-1 models the site owner only."))
    if settings.basis is not None and settings.basis != MVP1_BASIS:
        raise HTTPException(422, (
            "MVP-1 reports on one basis only: real terms, pre-tax, without "
            "subsidy."))


@router.get("/")
def list_studies(project: AuthorizedProject = ProjectAccessDep) -> list[dict]:
    _refuse_unless_enabled()
    return [
        _out(s.model_copy(update={"base_project": project.uuid,
                                  "option_projects": []})
             if s.base_project != project.uuid else s)
        for s in store.list_studies(_project_dir(project))
    ]


@router.post("/", status_code=201)
def create_study(body: StudyCreate,
                 project: AuthorizedProject = ProjectAccessDep,
                 db: DBSession = Depends(get_db),
                 user: User | None = Depends(optional_user)) -> dict:
    """
    Create the study record on the named base project.

    S1 creates the record only. The question-first flow that builds its own
    base project (M0) is S4.
    """
    _refuse_unless_enabled()
    _check_lock(db, project, user)
    now = _now()
    try:
        study = DecisionStudy(
            study_id=store.new_study_id(), name=body.name,
            question_id=body.question_id, base_project=project.uuid,
            intake=body.intake,
            created_by=str(user.id) if user is not None else None,
            created_at=now, updated_at=now,
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False,
                                            include_context=False)) from None
    with _WRITE_LOCK:
        return _save(project, study)


@router.get("/{study_id}")
def get_study(study_id: str,
              project: AuthorizedProject = ProjectAccessDep) -> dict:
    _refuse_unless_enabled()
    return _out(_load(project, study_id))


@router.patch("/{study_id}")
def patch_study(study_id: str, body: StudyPatch,
                project: AuthorizedProject = ProjectAccessDep,
                db: DBSession = Depends(get_db),
                user: User | None = Depends(optional_user)) -> dict:
    _refuse_unless_enabled()
    _check_lock(db, project, user)
    _refuse_outside_mvp1(body.settings)
    if body.step is not None:
        if body.intake is None or set(body.intake) - {body.step}:
            raise HTTPException(422, (
                f"a PATCH for step {body.step!r} may only carry "
                f"intake[{body.step!r}]"))
    with _WRITE_LOCK:
        study = _load(project, study_id)
        data = study.model_dump(mode="json", by_alias=True)
        if body.name is not None:
            data["name"] = body.name
        if body.intake is not None:
            data["intake"] = {**data["intake"], **body.intake}
        if body.settings is not None:
            data.update(body.settings.model_dump(mode="json",
                                                 exclude_unset=True))
        data["updated_at"] = _now().isoformat()
        try:
            updated = DecisionStudy.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False,
                                                include_context=False)) from None
        return _save(project, updated)


@router.delete("/{study_id}", status_code=204)
def delete_study(study_id: str,
                 project: AuthorizedProject = ProjectAccessDep,
                 db: DBSession = Depends(get_db),
                 user: User | None = Depends(optional_user)) -> Response:
    """
    Delete the study record. S4 extends this to cascade to the option forks
    the study OWNS — verified server-side (each fork's metadata names this
    study and this base project as its owner), never by trusting
    ``option_projects`` alone: that list is copied by Save-As, user scenarios,
    snapshots and bundle import, and `_load` drops it on a copied record. In
    S1 no route can set the list, so there is nothing to cascade yet.
    """
    _refuse_unless_enabled()
    _check_lock(db, project, user)
    with _WRITE_LOCK:
        _load(project, study_id)  # 404 before touching anything
        try:
            store.delete_study(_project_dir(project), study_id)
        except store.StudyNotFound:
            raise HTTPException(404, "Study not found") from None
    return Response(status_code=204)
