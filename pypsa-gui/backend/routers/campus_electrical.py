"""`/api/campus-electrical`: the campus electrical study of a hub project
(plan C6).

A wrapper layer like ``/api/gridspine``. Each handler resolves and
authorizes the project, checks the edit lock on a write, and calls one
function in ``services/campus_electrical_service.py``, the same functions
the copilot's tools call. Drafting and running parse a network and solve
load flows, which takes seconds of CPU, so they run off the event loop.

The asset library the investment step buys from (plan C9) is under
``/{name}/library``: ``GET``, ``PUT`` (validated by the engine's loader; 422
names the entry and field, 413 over 1 MB) and ``POST /library/reset``.

The joint optimisation (plan C12, the MILP of C11) is a background job:
``POST /{name}/milp`` starts it with the run settings (202 with its record;
409 while a job runs for any project; the least-cost run is made first, in
the request, unless a fresh one with these settings exists), ``GET
/{name}/milp`` is its record (``null`` when none has run since the backend
started) and ``POST /{name}/milp/cancel`` asks it to stop, keeping its best
point (404 when none has run). Starting and cancelling check the edit lock.

``GET /{name}/owner-assets`` (part three D2) lists what the investment run
bought as the extra owner assets of the investment case, with the study's
``source_hash``, a ``stale`` flag and the run's ``study_method`` (the joint
optimisation when it is fresh with a choice of its own, else least cost; owner
decision 2026-10-12), and the needs that run could not meet (owner decision
2026-10-13): ``{assets, source_hash, stale, study_method, unresolved}``. Read only.

``GET /{name}/grid-readiness?profile=`` (DC grid readiness 02) is the
advisory grid-readiness scorecard (``services/adequacy/grid_readiness.py``).
``PUT /{name}/grid-readiness/limits/{item}`` sets the user's own limit (DC 03
slice 6, ``services/adequacy/grid_limits.py``): ``{value, source, note}`` for
``connection_ramp`` and ``oscillation``, ``{source, note}`` for
``fault_level``; 422 with an ``error_kind`` for a refused one. ``DELETE`` the
same path resets it to the template's value or the placeholder. Both check the
edit lock; neither writes ``eh_template.json``.

The project's own grid codes (plan C10) are under ``/{name}/grid-codes``,
wrapping ``services/campus_grid_code_service.py``:

* ``GET /grid-codes``: the shipped, published and draft profiles, the
  uploaded documents, and whether extraction is available;
* ``POST /grid-codes/documents`` (multipart ``file``, optional ``page_ranges``) and
  ``DELETE /grid-codes/documents/{document_id}``;
* ``POST /grid-codes/documents/{document_id}/extract``: the copilot's draft;
* ``POST /grid-codes/drafts``: a blank draft to fill in by hand;
* ``GET``, ``PUT`` and ``DELETE /grid-codes/drafts/{profile_id}``;
* ``POST /grid-codes/drafts/{profile_id}/confirm`` and ``.../publish``;
* ``GET`` and ``DELETE /grid-codes/published/{profile_id}``.

The upload is read under one ``UploadBudget`` of the PDF cap; parsing it and
the extraction (one API call, up to minutes) run off the event loop.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool

from db.models import Project, User
from db.session import get_db
from models.flex_archetypes import BackupMode
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import campus_electrical_service as ce
from services import campus_grid_code_service as gc
from services import campus_judgment
from services import equipment_schedule
from services.http_filenames import content_disposition
from services.upload_guard import UploadBudget, read_capped

router = APIRouter()


class DraftRequest(BaseModel):
    overwrite: bool = False


class CampusText(BaseModel):
    yaml: str = Field(max_length=ce.MAX_CAMPUS_BYTES)


class LibraryText(BaseModel):
    #: No ``max_length``: the service answers 413 over ``MAX_LIBRARY_BYTES``,
    #: where a model constraint would answer 422.
    yaml: str


class IslandPresetBody(BaseModel):
    #: A control preset name (gridspine's ``PRESETS``), or None to clear it;
    #: gridspine refuses an unknown name (422).
    preset: str | None = None


class IslandPatchBody(BaseModel):
    #: The fields to change, by name: ``{units?: {unit: {field: value}},
    #: requirements?: {field: value}}``; the service merges, checks and saves.
    patch: dict
    #: The ``revision`` the editor read (GET); a changed sidecar is a 409.
    base: str | None = None


class IslandConfigBody(BaseModel):
    #: The raw sidecar; gridspine's schema validates it in the service (422
    #: naming the field) and the service caps its size (413).
    config: dict
    #: The ``revision`` the editor read (GET); a changed sidecar is a 409, so a
    #: whole-config save cannot drop a change made since (optional, as on PATCH).
    base: str | None = None


class IslandStudyBody(BaseModel):
    scenarios: list[Literal["ups_bridged", "seamless", "planned"]] | None = Field(
        None, min_length=1, max_length=3)


class RunSettings(BaseModel):
    k: int = Field(ce.DEFAULTS["k"], ge=1, le=50)
    pf: float | None = Field(None, gt=0, le=1)
    profile: str = Field(ce.DEFAULTS["profile"], min_length=1, max_length=64)
    margin: float = Field(ce.DEFAULTS["margin"], ge=0, le=2)
    n_minus_1: bool = True
    #: Buy the electrical assets from the library after sizing (plan C9).
    invest: bool = True
    #: The grid operator owns the PCC switchgear, so it is not bought or costed.
    pcc_switchgear_by_operator: bool = False
    #: Who moves the PCC transformer's on-load taps (plan C15): the campus, or
    #: the grid operator (they then stay at the campus file's position).
    pcc_tap_control: Literal["campus", "grid_operator"] = "campus"


class ExtractRequest(BaseModel):
    #: The draft's id; omitted, one is derived from the document's hash.
    profile_id: str | None = Field(None, min_length=1, max_length=64)
    overwrite: bool = False


class NewDraft(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(None, max_length=200)
    overwrite: bool = False


class ProfileText(BaseModel):
    yaml: str = Field(max_length=gc.MAX_PROFILE_BYTES)


class ConfirmLimit(BaseModel):
    #: ``voltage_bands[i]``, ``q_range_demand``, ``rvc_limit_pct`` or ``campus_voltage``.
    limit: str = Field(min_length=1, max_length=64)


class Publish(BaseModel):
    allow_unconfirmed: bool = False


def _row(proj: AuthorizedProject, db: DBSession) -> Project:
    project = db.get(Project, uuid.UUID(proj.uuid))
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{proj.name}' not found")
    return project


def _check_lock(proj: AuthorizedProject, db: DBSession, user: User | None) -> None:
    from routers.projects import _check_project_lock

    try:
        target = SimpleNamespace(id=uuid.UUID(proj.uuid), name=proj.name)
    except (TypeError, ValueError):
        return
    _check_project_lock(db, target, user)


@router.get("/{name}")
def get_state(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The campus file, the grid-code profiles, the last settings and results."""
    return ce.get_state(_row(proj, db))


@router.post("/{name}/draft")
async def draft(
    body: DraftRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Draft the campus file from the project's solved network."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.draft, _row(proj, db), body.overwrite)


@router.put("/{name}/campus")
def save_campus(
    body: CampusText,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Save the user's campus file, validated by building it."""
    _check_lock(proj, db, user)
    return ce.save_campus(_row(proj, db), body.yaml)


@router.post("/{name}/run", status_code=202)
async def run(
    body: RunSettings,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Queue the study (prepare, rank and size the campus at its critical
    hours, then buy the electrical assets from the library unless ``invest``
    is false) as a solve-queue job of kind ``campus``, and answer the job. It
    runs for minutes: follow it in ``GET /{name}`` (``run_job``, with its
    stage) or the solve queue, and abort it there."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.enqueue_run, _row(proj, db), body.model_dump(), user)


# ── the joint optimisation (MILP) as a background job (plan C12) ────────────

@router.post("/{name}/milp", status_code=202)
async def start_milp(
    body: RunSettings,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Start the joint optimisation (MILP, minutes) with these run settings;
    poll ``GET /{name}/milp``."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.start_milp_job, _row(proj, db), body.model_dump())


@router.get("/{name}/milp")
def milp_status(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The job's record: state, iteration, best cost against the least-cost one."""
    return ce.milp_job_status(_row(proj, db))


@router.post("/{name}/milp/cancel")
def cancel_milp(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Ask the running job to stop; it keeps the best point found so far."""
    _check_lock(proj, db, user)
    return ce.cancel_milp_job(_row(proj, db))


# ── the grid-readiness scorecard (DC grid readiness 02) ─────────────────────

@router.get("/{name}/grid-readiness")
async def get_grid_readiness(profile: str | None = None, proj: AuthorizedProject = ProjectAccessDep,
                             db: DBSession = Depends(get_db)):
    """The project's grid-readiness scorecard against grid-code ``profile``
    (default: the campus study's last-run profile, else ``eu_rfg_dcc_ce``):
    rows ``{id, requirement, limit, unit, source, project_value, verdict,
    assumed, note, section, target}``, and ``network`` (``live``, ``saved`` or
    ``out_of_date``). Advisory and read only: it scores a copy of the loaded
    network (unsaved edits included), or the saved netCDF when the project is
    not loaded or a run holds it (DC walkthrough 04), off the event loop. 422
    for an unknown profile."""
    from services.adequacy.grid_readiness import grid_readiness_for

    return await run_in_threadpool(grid_readiness_for, _row(proj, db), profile)


class LimitBody(BaseModel):
    model_config = {"extra": "forbid"}

    value: float | None = None
    #: Optional here so a missing source is the service's 422 with its error_kind.
    source: str | None = None
    note: str = ""


def _limit_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail={"error_kind": exc.kind, "message": exc.message})


@router.put("/{name}/grid-readiness/limits/{item}")
def put_grid_readiness_limit(
    item: str,
    body: LimitBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Set the user's own grid-readiness limit ``item``; returns the stored
    entry ``{value, source, note, set_by, set_at, default}``."""
    from services import project_registry
    from services.adequacy import grid_limits

    _check_lock(proj, db, user)
    try:
        return grid_limits.set_limit(project_registry.project_dir(_row(proj, db)), item,
                                     value=body.value, source=body.source, note=body.note,
                                     set_by=getattr(user, "email", None))
    except grid_limits.LimitError as exc:
        raise _limit_error(exc) from exc


@router.delete("/{name}/grid-readiness/limits/{item}")
def delete_grid_readiness_limit(
    item: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Reset ``item`` to the template's value or the generic placeholder."""
    from services import project_registry
    from services.adequacy import grid_limits

    _check_lock(proj, db, user)
    try:
        cleared = grid_limits.clear_limit(project_registry.project_dir(_row(proj, db)), item)
    except grid_limits.LimitError as exc:
        raise _limit_error(exc) from exc
    return {"item": item, "cleared": cleared}


# ── the data-centre backup (DC grid readiness 03) ───────────────────────────

class DcBackupAssessBody(BaseModel):
    model_config = {"extra": "forbid"}

    #: None assesses the stored mode; a mode here is a what-if, never stored.
    mode: BackupMode | None = None


def _dc_backup_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail={
        "error_kind": "dc_backup_invalid", "message": str(exc), "field": exc.field})


def _eh_templates():
    from routers.projects import _eh_templates_module

    tpl = _eh_templates_module()
    if tpl is None:
        raise HTTPException(status_code=503, detail="the Energy Hub templates are not shipped")
    return tpl


@router.put("/{name}/dc-backup")
def save_dc_backup(
    body: dict = Body(...),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Store the data-centre project's backup setting (``mode`` genset, bess
    or hybrid, and the BESS assumptions) after the create body's checks;
    returns the stored backup. Writes ``eh_template.json`` only: the saved
    network and the investment case are untouched. 422 ``dc_backup_invalid``
    for a refused setting or a project that is not a data centre."""
    from services import project_registry
    from services.adequacy import dc_backup

    _check_lock(proj, db, user)
    try:
        return dc_backup.save_backup(project_registry.project_dir(_row(proj, db)),
                                     _eh_templates(), body)
    except dc_backup.DcBackupError as exc:
        raise _dc_backup_error(exc) from exc
    except dc_backup.DcBackupBusy as exc:
        # Retryable: another marker write or an undo holds the lock
        # (UM10-UNDO-1). Nothing was stored.
        raise HTTPException(status_code=409, detail={
            "error_kind": "dc_backup_busy", "message": str(exc)}) from exc


@router.put("/{name}/dc-load")
def save_dc_load(
    body: dict = Body(...),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Change a data-centre project's PUE at design, critical share, backup
    mode (``backup.mode``) or power ``architecture`` after creation (te 04).
    The merged spec passes the create body's checks; a PUE or critical-share
    change rewrites the IT and cooling series on the open project's network
    (one undo step), and the stored spec, envelope and ledger follow, all or
    nothing. ``overwrite_series`` replaces a series the user edited (else 409
    ``dc_series_edited``). 422 ``dc_load_invalid`` names the field; 409
    ``project_not_open`` / ``study_running`` / ``dc_load_busy``."""
    from services import dirty_state, project_registry
    from services.dc_load_update import DcLoadUpdateError, update_dc_load
    from services.dispatch_status import clear_dispatch
    from services.network_undo import push_undo_snapshot
    from services.pypsa_service import PyPSAService
    from services.undo_service import pop as pop_undo

    _check_lock(proj, db, user)
    patch = dict(body)
    overwrite = bool(patch.pop("overwrite_series", False))
    # Before any write: a live study reads the network this edits.
    row = _row(proj, db)
    own = PyPSAService.get_context(project_registry.registry_key(row))
    if own is not None:
        PyPSAService.refuse_if_study_running("change the data-centre load", ctx=own)
    PyPSAService.refuse_if_study_running("change the data-centre load")
    ctx = PyPSAService.get_active_context()
    # Only the project open in the foreground: an unsaved draft or another
    # project's network is never rewritten by a PUT naming this one.
    resident = ctx.loaded_project == proj.name and ctx.network is not None
    project_dir = project_registry.project_dir(row)
    pushed = False
    try:
        with PyPSAService.get_lock():
            if resident and any(
                k in patch for k in ("pue_at_design", "critical_share")
            ):
                # The series are rewritten: one undo step. A marker-only edit
                # (backup mode, architecture) is not on the stack, as dc-backup.
                push_undo_snapshot()
                pushed = True
            try:
                out = update_dc_load(
                    project_dir,
                    _eh_templates(),
                    patch,
                    ctx.network if resident else None,
                    overwrite_series=overwrite,
                )
            except DcLoadUpdateError:
                if pushed:
                    pop_undo()
                raise
            if out["series_changed"]:
                clear_dispatch(ctx.network)
                dirty_state.bump_inputs(ctx)
                dirty_state.bump_revision(ctx)
                dirty_state.mark_dirty(ctx)
            elif pushed:
                pop_undo()  # same values: nothing changed, no step
            return out
    except DcLoadUpdateError as exc:
        status = 409 if exc.kind in ("project_not_open", "dc_series_edited") else 422
        raise HTTPException(
            status_code=status,
            detail={"error_kind": exc.kind, "message": str(exc), "field": exc.field},
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "dc_load_busy",
                "message": "the project's template metadata is busy; try again",
            },
        ) from exc


@router.post("/{name}/dc-backup/assess")
async def assess_dc_backup(
    body: DcBackupAssessBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Size the backup in ``mode`` (default: the stored one), prove it over
    the outage window and price it against the genset fleet: ``{sizing,
    window, proof, cost_delta, ...}``. Read only: solves run on a private
    copy of the saved network. Lock-checked as the island study is."""
    from services import project_registry
    from services.adequacy import dc_backup

    _check_lock(proj, db, user)
    project_dir = project_registry.project_dir(_row(proj, db))
    tpl = _eh_templates()
    try:
        return await run_in_threadpool(dc_backup.assess_project, project_dir, tpl, body.mode)
    except dc_backup.DcBackupError as exc:
        raise _dc_backup_error(exc) from exc


class DcArchitectureCompareBody(BaseModel):
    """The power-architecture screen's one input (DC 05): the price per MWh of
    facility energy. None reads the project's tariff energy component."""

    model_config = ConfigDict(extra="forbid")
    energy_price_per_mwh: float | None = None


@router.post("/{name}/dc-architecture/compare")
async def compare_dc_architecture(
    body: DcArchitectureCompareBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Screen the four power architectures (AC, MV UPS, 800 VDC, SST) against
    the project's own on the saved network: energy, effective PUE, peak within
    the import limit, capex and the NPV delta at the range ends, and the grid
    services each can offer with the scorecard's verdicts. No solve; writes
    nothing. 422 with a ``dc_architecture_*`` error kind for each refusal."""
    from services import dc_architecture, project_registry

    _check_lock(proj, db, user)
    project_dir = project_registry.project_dir(_row(proj, db))
    tpl = _eh_templates()
    try:
        return await run_in_threadpool(dc_architecture.compare_project, project_dir, tpl,
                                       energy_price_per_mwh=body.energy_price_per_mwh)
    except dc_architecture.DcArchitectureError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": exc.kind, "message": str(exc), "field": exc.field}) from exc


# ── the PSS/E hand-off bundles of the latest run (grid-compliance 23) ───────

@router.get("/{name}/psse")
def list_psse_bundles(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The latest campus run's PSS/E bundles, and whether the run is stale."""
    return ce.psse_bundles(_row(proj, db))


@router.get("/{name}/psse/{bundle}/download")
def download_psse_bundle(bundle: str, proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """One bundle as a zip. The name must be in the listing, so no path
    assembled from client input is opened; a bundle of a stale run is served
    with ``X-Run-Stale: true``."""
    data, stale = ce.psse_bundle_zip(_row(proj, db), bundle)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": content_disposition(f"{bundle}.zip"),
                             "X-Run-Stale": "true" if stale else "false"})


# ── the pack read-back judgment of the latest run (grid-compliance 22) ──────

@router.post("/{name}/psse/{bundle}/emt/results")
async def upload_pack_results(
    bundle: str,
    files: list[UploadFile] = File(...),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """The engineer's EMT results for one bundle's pack (``<case id>.csv``,
    ``.inf``, ``.out`` or ``_NN.out``, and a case's ``.status.txt`` poll log): saved into the pack, then the pack is
    judged against the run (GC-07 read-back). All files share one size cap."""
    _check_lock(proj, db, user)
    if len(files) > campus_judgment.MAX_RESULT_FILES:
        raise HTTPException(status_code=413, detail={
            "error_kind": "campus_results_too_many",
            "message": f"at most {campus_judgment.MAX_RESULT_FILES} result files per upload"})
    got, used = [], 0
    for f in files:  # each file under its own cap and all of them under the total, refused while reading
        per_file = campus_judgment.MAX_RESULT_FILE_BYTES
        left = campus_judgment.MAX_RESULT_BYTES - used
        try:
            data = await read_capped(f, min(per_file, left))
        except HTTPException as exc:
            if exc.status_code != 413:
                raise
            if per_file <= left:
                raise HTTPException(status_code=413, detail={
                    "error_kind": "campus_result_too_large",
                    "message": f"{f.filename} exceeds {per_file // (1024 * 1024)} MB"}) from exc
            raise HTTPException(status_code=413, detail={
                "error_kind": "campus_results_too_large",
                "message": f"the result files exceed {campus_judgment.MAX_RESULT_BYTES // (1024 * 1024)} MB together",
            }) from exc
        used += len(data)
        got.append((f.filename or "", data))
    return await run_in_threadpool(campus_judgment.add_results, _row(proj, db), bundle, got)


@router.post("/{name}/psse/{bundle}/emt/judge")
async def judge_pack(bundle: str, proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db),
                     user: User | None = Depends(optional_user)):
    """Judge one bundle's pack again on the results it holds; replaces the stored judgment."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(campus_judgment.judge, _row(proj, db), bundle)


@router.get("/{name}/judgment")
def get_pack_judgment(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The latest run's stored pack judgment, as the read-back wrote it, with its status counts."""
    return campus_judgment.get_judgment(_row(proj, db))


# ── the PowerFactory (DGS) hand-off (grid-compliance 09, item 6) ──

@router.get("/{name}/powerfactory")
def list_powerfactory_bundles(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The latest campus run's PowerFactory (DGS) bundles, and whether the run is stale."""
    return ce.powerfactory_bundles(_row(proj, db))


@router.get("/{name}/powerfactory/{bundle}/download")
def download_powerfactory_bundle(bundle: str, proj: AuthorizedProject = ProjectAccessDep,
                                 db: DBSession = Depends(get_db)):
    """One PowerFactory bundle as a zip, picked from the listing as the PSS/E download is."""
    data, stale = ce.powerfactory_bundle_zip(_row(proj, db), bundle)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": content_disposition(f"{bundle}.zip"),
                             "X-Run-Stale": "true" if stale else "false"})


# ── the background voltage harmonics at the PCC (grid-compliance 29) ──

@router.get("/{name}/background-harmonics")
def get_background_harmonics(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The stored field measurement of the background harmonics at the PCC, if any."""
    return ce.get_background(_row(proj, db))


@router.put("/{name}/background-harmonics")
async def put_background_harmonics(
    file: UploadFile = File(...),
    source: str = Form(""),
    measured_on: str = Form(""),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """A field measurement (CSV of harmonic order and % of the nominal fundamental) with who measured it and
    when; checked now against the campus PCC, taken by the next campus run. At most the cap plus one byte is
    read into memory, so the service refuses anything longer by its length (the server has already spooled the
    body by then)."""
    _check_lock(proj, db, user)
    data = await file.read(ce.MAX_BACKGROUND_BYTES + 1)
    return await run_in_threadpool(ce.put_background, _row(proj, db), data, source, measured_on, file.filename)


@router.delete("/{name}/background-harmonics")
def delete_background_harmonics(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db),
                                user: User | None = Depends(optional_user)):
    """Remove the stored measurement; the next campus run has none."""
    _check_lock(proj, db, user)
    return ce.delete_background(_row(proj, db))


# ── the island sidecar and study (campus island plan, I9a) ──────────────────

@router.get("/{name}/island")
def get_island(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The island sidecar as written, and the last island study."""
    return ce.get_island(_row(proj, db))


@router.get("/{name}/island/report")
def island_report(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The island report: its tables, each with the model behind it, the
    screening caveat and the rows that rest on assumed values."""
    from services.campus_island_report import island_report as _report
    return _report(_row(proj, db))


@router.put("/{name}/island")
def save_island(
    body: IslandConfigBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Save the island sidecar, validated by gridspine's schema. A stale
    ``base`` is a 409 ``island_config_changed``."""
    _check_lock(proj, db, user)
    return ce.save_island(_row(proj, db), body.config, body.base)


@router.patch("/{name}/island")
def patch_island(
    body: IslandPatchBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Change some island settings (a unit's controls, a requirement) without
    resending the rest: merged into the stored sidecar, validated, saved. A
    stale ``base`` is a 409 ``island_config_changed``."""
    _check_lock(proj, db, user)
    return ce.patch_island(_row(proj, db), body.patch, body.base)


@router.post("/{name}/island/preset")
def select_island_preset(
    body: IslandPresetBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Select the project's ESS/UPS/converter control preset in its island
    sidecar (data-centre grid readiness, ticket 04)."""
    _check_lock(proj, db, user)
    return ce.select_island_preset(_row(proj, db), body.preset)


@router.post("/{name}/island/study")
async def run_island_study(
    body: IslandStudyBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Solve the islanding variants (a baseline and one solve per scenario)
    and report the cost of island capability and of seamless islanding."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.run_island_study, _row(proj, db), body.scenarios)


# ── the frequency cuts (campus island plan, I7; owner Q6: manual) ──────────

CutKind = Literal["inertia", "headroom_up", "headroom_down", "droop_gain"]


@router.get("/{name}/island/cuts")
def get_island_cuts(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The cuts the last campus run proposes, each with its primal estimate;
    the applied cuts and the trials; the limit (bridge energy gets no cut)."""
    from services import campus_island_cuts as cic
    return cic.get_cuts(_row(proj, db))


@router.post("/{name}/island/cuts/apply", status_code=202)
def apply_island_cuts(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Apply the proposed cuts, re-solve the hub and rerun the campus study,
    up to three trials (a background job); poll ``GET .../apply``."""
    from services import campus_island_cuts as cic
    _check_lock(proj, db, user)
    return cic.start_apply_job(_row(proj, db), user_id=user.id if user else None)


@router.get("/{name}/island/cuts/apply")
def island_cuts_status(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The apply job's record: state, trials so far, why it stopped."""
    from services import campus_island_cuts as cic
    return cic.apply_status(_row(proj, db))


@router.post("/{name}/island/cuts/apply/cancel")
def cancel_island_cuts(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Stop the apply job after the trial in hand."""
    from services import campus_island_cuts as cic
    _check_lock(proj, db, user)
    return cic.cancel_apply_job(_row(proj, db))


@router.delete("/{name}/island/cuts/{period}/{hour}/{kind}")
def remove_island_cut(
    period: int,
    hour: int,
    kind: CutKind,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Take one applied cut back; the next solve no longer has it."""
    from services import campus_island_cuts as cic
    _check_lock(proj, db, user)
    return cic.remove_cut(_row(proj, db), period=period, hour=hour, kind=kind)


@router.delete("/{name}/island/cuts")
def clear_island_cuts(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Take every cut back, and the trials with them."""
    from services import campus_island_cuts as cic
    _check_lock(proj, db, user)
    return cic.clear_cuts(_row(proj, db))


# ── recommended GFM settings (E2E demo plan §7.2, GFM-b) ───────────────────


class GfmSettingsBody(BaseModel):
    allow_i_max: bool = False


@router.get("/{name}/island/gfm-settings")
def get_gfm_settings(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The last recommendation (heuristic screening) and whether it is still
    the campus run's: ``{state: not_run|ready|stale, reason, recommendation}``."""
    from services import campus_island_tuning as cit
    return cit.get_settings(_row(proj, db))


@router.post("/{name}/island/gfm-settings", status_code=202)
def recommend_gfm_settings(
    body: GfmSettingsBody | None = None,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Recommend GFM settings from the last campus run's island hours, as a
    campus job of the solve queue; follow it on the campus state's run job."""
    from services import campus_island_tuning as cit
    _check_lock(proj, db, user)
    return cit.enqueue_settings(_row(proj, db), user,
                                allow_i_max=bool(body.allow_i_max) if body else False)


GFM_APPLY_TOOL = "campus_apply_gfm_settings"


class GfmApplyIn(BaseModel):
    digest: str  # the card the user approved; required (ticket 29)


@router.get("/{name}/island/gfm-settings/card")
def gfm_settings_card(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
):
    """The edit card for applying the recommendation (old → new per unit, the consequences):
    the one the copilot shows for ``campus_apply_gfm_settings``. Writes nothing."""
    from harness import edit_card
    with edit_card.for_project(_row(proj, db)):
        return edit_card.preview(GFM_APPLY_TOOL, {}, with_state=False)


@router.post("/{name}/island/gfm-settings/apply")
def apply_gfm_settings(
    body: GfmApplyIn,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Write the recommended settings into the island sidecar and the campus
    file (tagged assumed); the island study goes, and the hub must be
    re-solved: ``{changes, hub_resolve_needed, receipt}``. The body carries the
    digest of the card the user approved (``…/card``), which must still be current
    (a 422 without it); the ``receipt`` holds the values read back from both files."""
    from harness import edit_card
    from services import campus_island_tuning as cit
    _check_lock(proj, db, user)
    project = _row(proj, db)
    with edit_card.for_project(project):
        card = edit_card.preview(GFM_APPLY_TOOL, {}, with_state=False)
        edit_card.require_current(GFM_APPLY_TOOL, {}, {"digest": body.digest})
        out = cit.apply_settings(project)
        return {**out, "receipt": edit_card.receipt(card, {})}


# ── the asset library (plan C9) ────────────────────────────────────────────

@router.get("/{name}/library")
def get_library(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The asset library a study buys from: the project's copy, else the shipped default."""
    return ce.get_library(_row(proj, db))


@router.put("/{name}/library")
def save_library(
    body: LibraryText,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Keep the user's library as the project's copy, validated by the engine's loader."""
    _check_lock(proj, db, user)
    return ce.save_library(_row(proj, db), body.yaml)


@router.post("/{name}/library/reset")
def reset_library(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Delete the project's copy; the shipped default is used again."""
    _check_lock(proj, db, user)
    return ce.reset_library(_row(proj, db))


# ── the equipment schedule (DC review 09) ─────────────────────────────────

@router.get("/{name}/equipment-schedule")
def get_equipment_schedule(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """Every electrical asset of the last DC Grid study: rating, quantity,
    governing criterion, source (computed / declared / assumed / not sized)
    and capex; declared ratings below the chosen ones flagged."""
    return equipment_schedule.for_project(_row(proj, db))


@router.get("/{name}/equipment-schedule.xlsx")
def get_equipment_schedule_xlsx(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The equipment schedule as a workbook: a Schedule and an Assumptions sheet."""
    row = _row(proj, db)
    blob = equipment_schedule.to_xlsx(equipment_schedule.for_project(row))
    return Response(content=blob,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": content_disposition(f"{row.name}_equipment_schedule.xlsx")})


# ── the chosen equipment as owner assets (plan, part three D2) ──────────────

@router.get("/{name}/owner-assets")
def owner_assets(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """What the investment run bought, as the extra owner assets of the
    investment case: ``{"assets": [...], "source_hash": str | None, "stale": bool,
    "study_method": "milp" | "least_cost" | None, "unresolved": [{"need", "reason"}]}``
    (``owner_assets_response``); no assets, no hash, no method and no unresolved
    need before an investment run. Read only."""
    return ce.owner_assets_response(_row(proj, db))


# ── the project's grid codes (plan C10) ────────────────────────────────────

@router.get("/{name}/grid-codes")
def list_grid_codes(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The shipped, published and draft profiles and the uploaded documents."""
    return gc.list_grid_codes(_row(proj, db))


@router.post("/{name}/grid-codes/documents")
async def upload_grid_code_document(
    file: UploadFile = File(...),
    page_ranges: str | None = Form(None, max_length=200),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Upload a grid code as a PDF; it is kept in the project by its hash.
    ``page_ranges`` (``"1-40,120-180"``) selects the pages to read."""
    _check_lock(proj, db, user)
    data = await UploadBudget(gc.MAX_PDF_BYTES).read(file)
    return await run_in_threadpool(gc.upload_document, _row(proj, db), data, file.filename, file.content_type,
                                   page_ranges)


@router.delete("/{name}/grid-codes/documents/{document_id}")
def delete_grid_code_document(
    document_id: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    _check_lock(proj, db, user)
    return gc.delete_document(_row(proj, db), document_id)


@router.post("/{name}/grid-codes/documents/{document_id}/extract")
async def extract_grid_code(
    document_id: str,
    body: ExtractRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Draft a profile from the document (one API call); every limit is
    extracted until a person confirms it."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(gc.extract, _row(proj, db), document_id, body.profile_id, body.overwrite)


@router.post("/{name}/grid-codes/drafts")
def new_grid_code_draft(
    body: NewDraft,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """A blank draft, every limit assumed, to fill in by hand."""
    _check_lock(proj, db, user)
    return gc.new_draft(_row(proj, db), body.profile_id, body.title, body.overwrite)


@router.get("/{name}/grid-codes/drafts/{profile_id}")
def get_grid_code_draft(profile_id: str, proj: AuthorizedProject = ProjectAccessDep,
                        db: DBSession = Depends(get_db)):
    return gc.get_draft(_row(proj, db), profile_id)


@router.put("/{name}/grid-codes/drafts/{profile_id}")
def save_grid_code_draft(
    profile_id: str,
    body: ProfileText,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Save the user's edit of a draft, validated by the profile loader."""
    _check_lock(proj, db, user)
    return gc.save_draft(_row(proj, db), profile_id, body.yaml)


@router.delete("/{name}/grid-codes/drafts/{profile_id}")
def delete_grid_code_draft(
    profile_id: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    _check_lock(proj, db, user)
    return gc.delete_draft(_row(proj, db), profile_id)


@router.post("/{name}/grid-codes/drafts/{profile_id}/confirm")
def confirm_grid_code_limit(
    profile_id: str,
    body: ConfirmLimit,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Confirm one extracted limit against its quote: it becomes code."""
    _check_lock(proj, db, user)
    return gc.confirm(_row(proj, db), profile_id, body.limit)


@router.post("/{name}/grid-codes/drafts/{profile_id}/publish")
def publish_grid_code(
    profile_id: str,
    body: Publish,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Publish a draft for the study; 409 while a limit is unconfirmed unless
    allow_unconfirmed."""
    _check_lock(proj, db, user)
    return gc.publish(_row(proj, db), profile_id, body.allow_unconfirmed)


@router.get("/{name}/grid-codes/published/{profile_id}")
def get_published_grid_code(profile_id: str, proj: AuthorizedProject = ProjectAccessDep,
                            db: DBSession = Depends(get_db)):
    return gc.get_published(_row(proj, db), profile_id)


@router.delete("/{name}/grid-codes/published/{profile_id}")
def delete_published_grid_code(
    profile_id: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    _check_lock(proj, db, user)
    return gc.delete_published(_row(proj, db), profile_id)
