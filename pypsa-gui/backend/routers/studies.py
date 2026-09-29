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

import re
import uuid
from datetime import datetime, UTC
from types import SimpleNamespace
from typing import Any, Literal

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
    Fidelity,
    FinancialBasis,
    Perspective,
    StudyBudget,
)
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.http_filenames import content_disposition
from services.study import FLAG_ENV, flag_set, store
from services.study import ledger as study_ledger
from services.study import library as study_library
from services.study import forks as study_forks
from services.study import packs
from services.study import questions as study_questions
from services.study import runner as study_runner

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
# per-step PATCHes to the same study must not drop each other's answers. The
# S4 runner's worker writes the same sidecar, so both take the store's lock.
_WRITE_LOCK = store.WRITE_LOCK


class StudyCreate(BaseModel):
    """
    ``question_id`` naming a question TEMPLATE (``services/study/questions``)
    creates the study's own base project (S4 M0), named ``project_name``
    (default: the study's name); any other id attaches a record to the path
    project and runs no pack.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    name: str = Field(min_length=1, max_length=120)
    intake: dict[str, Any] = Field(default_factory=dict)
    project_name: str | None = Field(default=None, min_length=1, max_length=64)


class RunRequest(BaseModel):
    """
    ``fidelity`` is recorded on every figure; both are 8760 h of one year in
    MVP-1 (spec decision 14 amended), the field exists for MVP-2.
    ``budget_solves`` caps the run's own campaign when none is open.
    """

    model_config = ConfigDict(extra="forbid")

    fidelity: Fidelity = Fidelity.quick_screen
    budget_solves: int | None = Field(default=None, ge=1, le=120)


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
    return _adopt(study, project)


_COPIED_FINDINGS = "copied_record_findings_computed_on_the_origin_forks"


def _adopt(study: DecisionStudy, project: AuthorizedProject) -> DecisionStudy:
    """
    The containing project is the study's base project: a copy carried by a
    bundle import, a Save-As, a user scenario or a snapshot must not answer
    with its origin's uuid — nor with its origin's option forks, which the
    copy does not own (fork ownership is verified server-side too,
    `services/study/forks.py`; gate S1 SB-4). A copy's findings and report
    were computed on the origin's forks, so they are marked stale (gate S1
    re-gate carry); the first write persists the adoption. `pack_project`
    is left as it was, so a copy never runs the origin's pack.
    """
    if study.base_project == project.uuid:
        return study
    update: dict[str, Any] = {"base_project": project.uuid, "option_projects": []}
    if study.findings_ref or study.report_ref:
        update["stale"] = True
        update["stale_reasons"] = sorted(set(study.stale_reasons) | {_COPIED_FINDINGS})
    return study.model_copy(update=update)


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
    return [_out(_adopt(s, project)) for s in store.list_studies(_project_dir(project))]


@router.post("/", status_code=201)
def create_study(body: StudyCreate,
                 project: AuthorizedProject = ProjectAccessDep,
                 db: DBSession = Depends(get_db),
                 user: User | None = Depends(optional_user)) -> dict:
    """
    Create a study.

    * A question TEMPLATE id (S4 M0): a NEW base project is created for the
      study (`_create_pack_study`), holding the question pack's baseline
      network and the sidecar; the path project — the caller's current one —
      is only the authorisation and is never written.
    * Any other id: the record is attached to the path project (S1). Such a
      record runs no pack and cannot be run.
    """
    _refuse_unless_enabled()
    if study_questions.get_question(body.question_id) is not None:
        return _create_pack_study(body, project, db, user)
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
        if body.intake is not None and "tariff" in body.intake:
            _refuse_bad_tariff(updated.intake)
            if updated.ledger is not None:
                # Gate S2 [S9]: a tariff change re-seeds the stored ledger, so
                # its tariff rows (the demand-charge price, the energy level)
                # follow the new tariff; a user value that no longer applies
                # is flagged `needs_attention`, and the run refuses it.
                try:
                    updated = updated.model_copy(update={
                        "ledger": study_ledger.reseed_ledger(
                            updated.ledger, _key_drivers(updated), updated.intake,
                            _library_or_500())})
                except study_library.LibraryError as exc:
                    raise HTTPException(422, str(exc)) from None
        if updated.ledger is not None:
            # S2: the badge reads the intake's load too, so an intake step
            # (e.g. a meter upload) moves it without waiting for a ledger PUT.
            updated = updated.model_copy(update={
                "maturity": study_ledger.maturity_from_ledger(
                    updated.ledger, study_ledger.load_provenance(updated.intake))})
        return _save(project, updated)


@router.delete("/{study_id}", status_code=204)
def delete_study(study_id: str,
                 project: AuthorizedProject = ProjectAccessDep,
                 db: DBSession = Depends(get_db),
                 user: User | None = Depends(optional_user)) -> Response:
    """
    Delete the study record and cascade to the option forks the study OWNS
    (S4) — verified server-side for every candidate (`forks.is_study_owned`:
    the fork's metadata names this study and this base project AND its row's
    parent is this base project), never by trusting ``option_projects``: that
    list is copied by Save-As, user scenarios, snapshots and bundle import.
    Refused (409) while the study is running or while an owned fork cannot
    be removed (an active queue job, or a project branched from it); nothing
    is deleted then.
    """
    _refuse_unless_enabled()
    _check_lock(db, project, user)
    with _WRITE_LOCK:
        study = _load(project, study_id)  # 404 before touching anything
        forks = _owned_fork_rows(study, project, db, user)
        _refuse_if_running(project, db, user, study_id)
        for row in forks:
            _refuse_undeletable_fork(row, db)
        for row in forks:
            try:
                study_forks.delete_fork(db, row, study_id=study_id,
                                        base_uuid=project.uuid)
            except study_forks.ForkError as exc:
                raise HTTPException(exc.status, detail={
                    "error_kind": exc.code, "message": exc.message}) from None
        try:
            store.delete_study(_project_dir(project), study_id)
        except store.StudyNotFound:
            raise HTTPException(404, "Study not found") from None
    return Response(status_code=204)


def _base_row(project: AuthorizedProject, db: DBSession, user: User | None):
    from services import project_registry

    if user is None:
        return None
    return project_registry.find_project(db, user, project.uuid)


def _owned_fork_rows(study: DecisionStudy, project: AuthorizedProject,
                     db: DBSession, user: User | None) -> list:
    """
    Candidates from the study's list AND from the base's children — every
    one verified by `forks.is_study_owned`, so the list alone never deletes.
    """
    from db.models import Project

    base = _base_row(project, db, user)
    if base is None:
        return []
    rows = {str(r.id): r for r in study_forks.owned_forks(db, base, study.study_id)}
    for ref in study.option_projects:
        try:
            row = db.get(Project, uuid.UUID(str(ref)))
        except (TypeError, ValueError):
            continue
        if row is not None and study_forks.is_study_owned(
                row, study_id=study.study_id, base_uuid=project.uuid):
            rows[str(row.id)] = row
    return list(rows.values())


def _refuse_undeletable_fork(row, db: DBSession) -> None:
    from services import project_registry
    from services.solve_queue import solve_queue

    key = project_registry.registry_key(row)
    if any(j.get("project_key") == key and j.get("status") in ("queued", "running")
           for j in solve_queue.list_jobs()):
        raise HTTPException(409, detail={
            "error_kind": "fork_solving",
            "message": f"the study fork '{row.name}' has an active solve-queue job"})
    if project_registry.direct_children(db, row):
        raise HTTPException(409, detail={
            "error_kind": "fork_has_children",
            "message": (f"a project was branched from the study fork '{row.name}'; "
                        "delete or move it first")})


def _refuse_if_running(project: AuthorizedProject, db: DBSession,
                       user: User | None, study_id: str) -> None:
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_context(project.registry_key)
    if ctx is None:
        return
    with ctx.solver_state_lock:
        rec = ctx.solver_state.get(study_runner.STUDY_KEY)
        if rec and rec.get("study_id") == study_id and rec.get("status") == "running":
            raise HTTPException(409, detail={
                "error_kind": "study_running",
                "message": "the study is running; abort it before deleting it"})


# ── S4 M0: a question-first study creates its own base project ────────────

_BASE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]{1,48}$")


def _refuse_bad_tariff(intake: dict[str, Any]) -> None:
    """Gate S3 N7 / N-v2-2: the tariff the engine would refuse, refused now."""
    try:
        packs.intake_tariff(intake, _library_or_500())
    except packs.PackError as exc:
        raise HTTPException(422, detail={"error_kind": exc.code,
                                         "message": exc.message}) from None


def _create_pack_study(body: StudyCreate, project: AuthorizedProject,
                       db: DBSession, user: User | None) -> dict:
    """
    M0 (plan S4, review v2 BC-5): ``project_registry.create_root`` →
    ``ensure_project_dir`` → ``PyPSAService.build_context()`` →
    ``_save_context(ctx, name, project_row=row, storage_dir=dir,
    persist_user_ts=False, db, user)``, the first-save claim made inside
    ``PyPSAService.hydrate_or_adopt(key)``. The request's slot, the path
    project and every other project are untouched: the pack is built off to
    the side, the new directory is the only one written, and the process
    `_user_ts` is never read or written (OPEN-ITEMS 1).
    """
    from routers.projects import _save_context
    from services import project_registry
    from services.adequacy import campaign
    from services.pypsa_service import PyPSAService

    project_registry.require_user(user)
    question = study_questions.get_question(body.question_id)
    base_name = (body.project_name or body.name).strip()
    if not _BASE_NAME_RE.fullmatch(base_name):
        raise HTTPException(422, (
            "the study's base project name must be 1-48 letters, digits, spaces, "
            "'_', '-' or '.' (its option forks append '-opt-<option>')"))
    if project_registry.find_project(db, user, base_name) is not None:
        raise HTTPException(409, f"Project '{base_name}' already exists")
    library = _library_or_500()
    _refuse_bad_tariff(body.intake)

    def resolve_upload(file_id: str) -> bytes:
        from services import upload_service
        return upload_service.get_upload_bytes(project.name, file_id,
                                               project_dir=project.directory)

    try:
        ledger = study_library.seed_ledger(question, body.intake, library)
        network = packs.build_site_network(body.intake, ledger, "none",
                                           library=library, question=question,
                                           resolve_upload=resolve_upload)
        tariff = packs.effective_tariff(body.intake, ledger, library, network.snapshots)
        cfg = packs.option_solver_config(ledger, tariff)
    except (packs.PackError, study_library.LibraryError) as exc:
        code = getattr(exc, "code", "intake_invalid")
        raise HTTPException(422, detail={"error_kind": code, "message": str(exc)}) from None
    options = study_questions.options_for(question, body.intake)
    solves = campaign.estimate_solves(None, study_runner.STUDY_KEY,
                                      options=[o.option_id for o in options])

    row = project_registry.create_root(db, user, base_name)
    base_dir = project_registry.ensure_project_dir(row)
    key = project_registry.registry_key(row)
    try:
        with PyPSAService.hydrate_or_adopt(key) as resident:
            if resident is not None:  # a fresh uuid cannot be resident
                raise HTTPException(409, f"Project '{base_name}' is already open")
            ctx = PyPSAService.build_context()
            ctx.network = network
            ctx.solver_state["solver_config"] = cfg
            _save_context(ctx, row.name, project_row=row, storage_dir=base_dir,
                          persist_user_ts=False, db=db, user=user)
            # The first-save claim re-keys the build context INTO the registry
            # (`rekey_context`); it is a throw-away here, and a resident base
            # would count against the user's resident cap (gate S4 BC-S4-1).
            PyPSAService.drop(key)
        load = body.intake.get("load") if isinstance(body.intake, dict) else None
        if isinstance(load, dict) and load.get("upload_id"):
            _copy_upload(project, base_dir, str(load["upload_id"]))
        now = _now()
        study = DecisionStudy(
            study_id=store.new_study_id(), name=body.name,
            question_id=body.question_id, base_project=str(row.id),
            pack_project=str(row.id), intake=body.intake,
            ledger=ledger, ledger_version=ledger.ledger_version,
            maturity=study_ledger.maturity_from_ledger(
                ledger, study_ledger.load_provenance(body.intake)),
            budget=StudyBudget(solves_max=solves),
            currency_year=int(library.finance["currency_year"]),
            created_by=str(user.id), created_at=now, updated_at=now,
        )
        with _WRITE_LOCK:
            store.save_study(base_dir, study)
    except BaseException:
        from routers.projects import _force_rmtree
        try:
            PyPSAService.drop(key)
            project_registry.delete_project_row(db, row)
            _force_rmtree(base_dir)
        except Exception:  # noqa: BLE001
            pass
        raise
    return {**_out(study), "base_project_name": row.name}


def _copy_upload(project: AuthorizedProject, base_dir, file_id: str) -> None:
    """Carry the intake's load upload into the new base project's uploads/."""
    import shutil

    src = project.directory / "uploads" / file_id
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", file_id) or not src.is_dir():
        return
    shutil.copytree(str(src), str(base_dir / "uploads" / file_id), dirs_exist_ok=True)


# ── S4 M1: run, status, abort ─────────────────────────────────────────────

def _run_error(exc: study_runner.RunRefused) -> HTTPException:
    return HTTPException(exc.status, detail=exc.detail)


@router.post("/{study_id}/run", status_code=202)
def run_study(study_id: str, body: RunRequest | None = None,
              project: AuthorizedProject = ProjectAccessDep,
              db: DBSession = Depends(get_db),
              user: User | None = Depends(optional_user)) -> dict:
    """
    Solve every option of a question-pack study on study-owned forks.

    The middleware's solver-in-flight exemption covers this route (BC-3), so
    the handler takes its own, context-parameterised check: the study mesh
    and the in-flight test run against the STUDY'S base context, claimed
    under its locks (`runner.start_study_run`). 409 while any study or a
    solve holds the base project, and for a record that must not run a
    pack; 409 naming the shortfall when the campaign budget is too small.
    """
    from routers.projects import _enforce_project_lock

    _refuse_unless_enabled()
    body = body or RunRequest()
    base = _base_row(project, db, user)
    if base is None:
        raise HTTPException(404, f"Project '{project.name}' not found")
    _enforce_project_lock(db, base, user)
    _load(project, study_id)
    try:
        return study_runner.start_study_run(
            study_id, body.fidelity, base_row=base, user_id=user.id,
            budget_solves=body.budget_solves)
    except study_runner.RunRefused as exc:
        raise _run_error(exc) from None


@router.get("/{study_id}/run")
def get_study_run(study_id: str,
                  project: AuthorizedProject = ProjectAccessDep,
                  db: DBSession = Depends(get_db),
                  user: User | None = Depends(optional_user)) -> dict:
    _refuse_unless_enabled()
    _load(project, study_id)
    base = _base_row(project, db, user)
    if base is None:
        raise HTTPException(404, f"Project '{project.name}' not found")
    rec = study_runner.get_study_run(study_id, base_row=base,
                                     base_dir=_project_dir(project))
    if rec is None:
        raise HTTPException(404, detail={"error_kind": "study_never_run",
                                         "message": "this study has not been run"})
    return rec


@router.post("/{study_id}/run/abort")
def abort_study_run(study_id: str,
                    project: AuthorizedProject = ProjectAccessDep,
                    db: DBSession = Depends(get_db),
                    user: User | None = Depends(optional_user)) -> dict:
    """
    Ask a running study to stop: the running option's queue job is aborted,
    no further option starts, unsolved forks are removed and the options
    never reached are named (`pending_options`). Idempotent, 200 on a
    finished run; 404 when the study was never run.
    """
    from routers.projects import _enforce_project_lock

    _refuse_unless_enabled()
    base = _base_row(project, db, user)
    if base is None:
        raise HTTPException(404, f"Project '{project.name}' not found")
    _enforce_project_lock(db, base, user)
    _load(project, study_id)
    try:
        return study_runner.abort_study_run(study_id, base_row=base,
                                            base_dir=_project_dir(project))
    except study_runner.RunRefused as exc:
        raise _run_error(exc) from None


# ── S2: the assumptions ledger ────────────────────────────────────────────
#
# The ledger lives on the study record (`DecisionStudy.ledger`). Until the
# first PUT it is None and GET answers with a seed computed in memory from the
# library and the intake (`stored: false`), so a read never writes. Every PUT
# stores the ledger and recomputes `maturity` from it and the intake's load.

class LedgerEdit(BaseModel):
    """One user row. The unit is required and must be the row's own."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    value: float
    unit: str = Field(min_length=1, max_length=64)
    provenance: Literal["user", "measured"] = "user"
    source: str | None = Field(default=None, max_length=500)
    source_year: int | None = Field(default=None, ge=1900, le=2200)
    source_url: str | None = Field(default=None, max_length=2000)
    currency_year: int | None = Field(default=None, ge=1900, le=2200)


class LedgerPut(BaseModel):
    """
    ``rows`` are applied in order over the stored ledger (seeded first when
    there is none). ``reseed`` first re-seeds: library rows refreshed from
    the library and the current intake (e.g. a newly chosen tariff), user
    rows kept, and a user row that no longer applies flagged
    ``needs_attention``. ``reset`` (after the re-seed, before the edits)
    puts the named rows back to their seed, which clears that flag.
    """

    model_config = ConfigDict(extra="forbid")

    rows: list[LedgerEdit] = Field(default_factory=list, max_length=200)
    reseed: bool = False
    reset: list[str] = Field(default_factory=list, max_length=200)


def _key_drivers(study: DecisionStudy) -> tuple[str, ...]:
    """
    The question template's key drivers (gate S2 carry: the template's list,
    not the library stand-in). A custom question has no template; MVP-1 has
    one, the BESS one, and seeds such a record with its drivers.
    """
    question = study_questions.get_question(study.question_id) or study_questions.BESS_AT_SITE
    return tuple(question.key_drivers)


def _library_or_500() -> study_library.Library:
    try:
        return study_library.load_library()
    except (study_library.LibraryError, OSError) as exc:
        raise HTTPException(500, detail={
            "code": "study_library_unreadable", "message": str(exc)}) from None


def _current_ledger(study: DecisionStudy):
    """
    (ledger, stored): the stored ledger, or a fresh in-memory seed; either
    names a study currency year that differs from the ledger's (BC-S2-4).
    """
    if study.ledger is not None:
        return study_ledger.with_study_currency_year(study.ledger, study.currency_year), True
    try:
        seeded = study_library.seed_ledger(_key_drivers(study), study.intake,
                                           _library_or_500())
    except study_library.LibraryError as exc:
        raise HTTPException(422, str(exc)) from None
    return study_ledger.with_study_currency_year(seeded, study.currency_year), False


def _ledger_out(study: DecisionStudy, ledger, stored: bool, maturity=None) -> dict:
    maturity = maturity or study_ledger.maturity_from_ledger(
        ledger, study_ledger.load_provenance(study.intake))
    return {
        "study_id": study.study_id,
        "stored": stored,
        "ledger": ledger.model_dump(mode="json", by_alias=True),
        "maturity": maturity.model_dump(mode="json", by_alias=True),
    }


@router.get("/{study_id}/ledger")
def get_ledger(study_id: str,
               project: AuthorizedProject = ProjectAccessDep) -> dict:
    _refuse_unless_enabled()
    study = _load(project, study_id)
    ledger, stored = _current_ledger(study)
    return _ledger_out(study, ledger, stored)


@router.put("/{study_id}/ledger")
def put_ledger(study_id: str, body: LedgerPut,
               project: AuthorizedProject = ProjectAccessDep,
               db: DBSession = Depends(get_db),
               user: User | None = Depends(optional_user)) -> dict:
    _refuse_unless_enabled()
    _check_lock(db, project, user)
    with _WRITE_LOCK:
        study = _load(project, study_id)
        ledger, _stored = _current_ledger(study)
        try:
            if body.reseed:
                ledger = study_ledger.reseed_ledger(
                    ledger, _key_drivers(study), study.intake, _library_or_500())
            if body.reset:
                ledger = study_ledger.reset_rows(
                    ledger, body.reset, _key_drivers(study), study.intake,
                    _library_or_500())
            ledger = study_ledger.with_study_currency_year(ledger, study.currency_year)
            now = _now()
            changed_by = str(user.id) if user is not None else None
            for edit in body.rows:
                ledger = study_ledger.apply_user_row(
                    ledger, edit.key, edit.value, unit=edit.unit,
                    changed_by=changed_by, changed_at=now,
                    provenance=edit.provenance, source=edit.source,
                    source_year=edit.source_year, source_url=edit.source_url,
                    currency_year=edit.currency_year)
        except (study_ledger.LedgerEditError, study_library.LibraryError) as exc:
            raise HTTPException(422, str(exc)) from None
        maturity = study_ledger.maturity_from_ledger(
            ledger, study_ledger.load_provenance(study.intake))
        updated = study.model_copy(update={
            "ledger": ledger, "ledger_version": ledger.ledger_version,
            "maturity": maturity, "updated_at": _now()})
        _save(project, updated)
        return _ledger_out(updated, ledger, True, maturity)


@router.get("/{study_id}/ledger.csv")
def get_ledger_csv(study_id: str,
                   project: AuthorizedProject = ProjectAccessDep) -> Response:
    """
    The ledger as CSV, export only (import is MVP-2). Text cells a
    spreadsheet would evaluate as a formula are neutralised
    (`services/study/ledger.py::ledger_to_csv`).
    """
    _refuse_unless_enabled()
    study = _load(project, study_id)
    ledger, _stored = _current_ledger(study)
    return Response(
        content=study_ledger.ledger_to_csv(ledger),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(
            f"{study.name} - ledger.csv")},
    )


# ── S5: the investment case (pro forma), JSON and XLSX ────────────────────

def _case_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"error_kind": code, "message": message})


def _option_case(study_id: str, option_id: str, project: AuthorizedProject,
                 db: DBSession, user: User | None):
    """
    (study, ledger, case) for one solved option of a finished run.

    Built from the option fork's saved network (its own frames), the run
    record's bills and asset economics, and the ledger the run was built
    from — a ledger edited since is refused (409), never mixed with results
    it did not produce. 404 for a study never run, an option the question
    does not have, and an option the run did not solve or whose fork the
    study does not own; 422 for the baseline (every case is measured
    against it) and for a case the engine refuses (a second currency year).
    """
    from db.models import Project
    from services.study import proforma

    study = _load(project, study_id)
    base_dir = _project_dir(project)
    try:
        run = store.load_aux(base_dir, study_id, "run")
        findings = store.load_aux(base_dir, study_id, "findings")
    except store.StudyUnreadable:
        raise HTTPException(500, detail={"code": "study_unreadable",
                                         "message": "The run record cannot be read."}) from None
    question = study_questions.get_question(study.question_id)
    if run is None or findings is None or question is None:
        raise _case_error(404, "study_never_run", "this study has not been run")
    if option_id not in {o.option_id for o in question.options}:
        raise _case_error(404, "option_unknown", f"the question has no option {option_id!r}")
    if not any(o.free_assets for o in question.options if o.option_id == option_id):
        raise _case_error(422, "baseline_has_no_case",
                          f"{option_id!r} is the baseline every case is measured against")
    _refuse_if_running(project, db, user, study_id)
    result = next((o for o in findings.get("options") or []
                   if o.get("option_id") == option_id), None)
    if result is None or result.get("solve_status") != "ok" or not result.get("project_ref"):
        raise _case_error(404, "option_not_solved",
                          f"the last run did not solve option {option_id!r}")
    if _base_row(project, db, user) is None:
        raise HTTPException(404, f"Project '{project.name}' not found")
    try:
        fork = db.get(Project, uuid.UUID(str(result["project_ref"])))
    except (TypeError, ValueError):
        fork = None
    if fork is None or not study_forks.is_study_owned(
            fork, study_id=study_id, base_uuid=project.uuid):
        raise _case_error(404, "option_not_solved",
                          f"option {option_id!r} has no fork this study owns")
    ledger, _stored = _current_ledger(study)
    if packs.ledger_hash(ledger) != (findings.get("hashes") or {}).get("ledger_hash"):
        raise _case_error(409, "ledger_changed_since_run", (
            "the assumptions ledger changed after the run; re-run the study "
            "before reading its cases (the LP sized the options on the old one)"))
    library = _library_or_500()
    network = study_runner._solved_network(fork)
    details = (run.get("details") or {})
    bills = {"baseline": (details.get("none") or {}).get("bill"),
             "option": (details.get(option_id) or {}).get("bill")}
    try:
        tariff = packs.effective_tariff(study.intake, ledger, library, network.snapshots)
        cfg = packs.option_solver_config(ledger, tariff)
        case = proforma.build_investment_case(
            network, cfg, None, ledger, bills, option_id, study_id=study_id,
            tariff=tariff, fidelity=result.get("fidelity"),
            asset_economics=(details.get(option_id) or {}).get("asset_economics"),
            study_currency_year=study.currency_year, question=question,
            project_ref=str(fork.id),
            model_hash=((findings.get("hashes") or {}).get("option_network_hashes")
                        or {}).get(str(fork.id)))
    except (proforma.ProformaError, packs.PackError) as exc:
        raise _case_error(422, exc.code, exc.message) from None
    return study, ledger, case


@router.get("/{study_id}/options/{option_id}/case")
def get_option_case(study_id: str, option_id: str,
                    project: AuthorizedProject = ProjectAccessDep,
                    db: DBSession = Depends(get_db),
                    user: User | None = Depends(optional_user)) -> dict:
    """
    The option's investment case (plan S5): a year-by-year cash flow on one
    basis, its KPIs, the bill's value streams and the market revenue at
    duals (reported, excluded from the cash flow). ``available`` is false
    when the case is not established (a bill or the asset economics missing).
    """
    _refuse_unless_enabled()
    _study, _ledger, case = _option_case(study_id, option_id, project, db, user)
    return {"available": case.status == "ok",
            **case.model_dump(mode="json", by_alias=True)}


@router.get("/{study_id}/options/{option_id}/case.xlsx")
def get_option_case_xlsx(study_id: str, option_id: str,
                         project: AuthorizedProject = ProjectAccessDep,
                         db: DBSession = Depends(get_db),
                         user: User | None = Depends(optional_user)) -> Response:
    """
    The same case as a workbook: cash flows with live discounting formulas
    and the NPV pinned as ``=CF0 + NPV(rate, CF1:CFn)``, the KPIs, the
    ledger and the provenance (`services/study/proforma_xlsx.py`).
    """
    _refuse_unless_enabled()
    from services.study.proforma_xlsx import write_proforma_xlsx

    study, ledger, case = _option_case(study_id, option_id, project, db, user)
    return Response(
        content=write_proforma_xlsx(case, ledger),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(
            f"{study.name} - {option_id} - pro forma.xlsx")},
    )
