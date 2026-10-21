"""Renewable profile generator routes (Task 7).

Two surfaces in one module:

  * global, no project — `GET /api/profiles/presets` (the Task 4 catalogue)
    and `POST /api/profiles/estimate` (a SYNTHETIC, downsampled, labelled
    preview for the map UI; it never enqueues a job and never touches a
    project);
  * project-scoped — generate / pending / apply under
    `/api/projects/{name}/profiles/…`, authorized by `ProjectAccessDep` and
    lock-gated like every other project write edge.

Guards this module is responsible for itself (the middleware does NOT cover
this prefix — `_UNDO_PREFIXES` is `/api/network/` + `/api/io/` only):

  * the apply edge pushes an undo snapshot BEFORE `assign_profile` writes;
  * `_check_lock` (the campus module's wrapper over `_check_project_lock`)
    runs on both write edges;
  * CDS credentials are preflighted IN THE REQUEST for the atlite source, so
    a missing key is a 422 with setup instructions now rather than a failed
    job in ten minutes;
  * apply re-resolves the component at apply time: renamed/deleted since
    generation is a typed 422; a bus that MOVED since generation is a
    warning field on the 200, not a refusal (re-siting an asset is
    legitimate).
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import xarray as xr
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from db.models import Project, User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import solve_queue as sq
from services.profile_gen import engine, presets, shape
from services.profile_gen.apply import (
    ComponentNotFoundError,
    CurrentChangedError,
    CurrentSeriesError,
    DcLoadOwnedError,
    FamilyTargetMismatchError,
    InvalidSeriesUnitError,
    ProfileApplyError,
    ProfileBoundConflictError,
    UnknownFamilyError,
    UnsupportedTargetError,
    WindowSourceGapError,
    assign_profile,
)
from services.profile_gen.cutout_cache import CdsCredentialError, cds_preflight
from services.profile_gen.engine import ProfileGenError
from services.profile_gen.refusals import public_message
from services.profile_gen.families import default_target
from services.profile_gen.job import KEEP_PENDING, pending_io_lock, write_pending
from services.profile_gen.availability import (
    ForcedOutagesNotWrittenError,
    InvalidAvailabilityParamsError,
    MaintenanceYearError,
    PocBoundError,
    TwoWayFloorError,
    UnknownAvailabilityKindError,
)
from services.profile_gen.refine import (
    AnnualEnergyOnWindowSourceError,
    InvalidRefineOpError,
    RefineReplayError,
    WindowClockUnknownError,
    WindowSourceOpError,
)
from services.profile_gen.series import (
    CopBusError,
    InvalidSeriesParamsError,
    MarketBundlesUnavailableError,
    PartLoadNotASeriesError,
    PocTariffError,
    UnknownSeriesKindError,
)
from services.profile_gen.load_upload import (
    UploadCoverageError,
    UploadFormatError,
    UploadNoLoadError,
    UploadUnitError,
    read_load_upload,
)
from services.profile_gen.load_archetypes import (
    ARCHETYPES,
    InvalidArchetypeParamsError,
    UnknownArchetypeError,
    list_presets as list_load_presets,
    resolve_defaults as load_archetype_defaults,
)

router = APIRouter()          # global: /api/profiles/…
project_router = APIRouter()  # scoped: /api/projects/{name}/profiles/…


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ProfilePointIn(BaseModel):
    id: str
    lon: float = Field(ge=-180.0, le=180.0)
    lat: float = Field(ge=-90.0, le=90.0)


class EstimateRequest(BaseModel):
    # Sites come from explicit `points` and/or `from_generators` (generator
    # names resolved against the FOREGROUND network's bus coordinates — the
    # same semantics the chat tools expose; at least one of the two is
    # required). Point ids from generator names ARE the generator names, so
    # apply needs no mapping; an explicit point with a generator's name is
    # that generator's coordinate override.
    points: list[ProfilePointIn] = []
    from_generators: list[str] | None = None
    technology: str
    preset: str | None = None
    params: dict[str, Any] | None = None
    year: int | None = None
    # C2: aggregation happens at generation (D1=A); hourly is the source's
    # native axis. Annual was dropped by ruling D4=B — an annual mean is a
    # scalar, not a profile.
    resolution: Literal["hourly", "daily", "weekly"] = "hourly"


class GenerateRequest(EstimateRequest):
    source: str = "synthetic"


class ApplyRequest(BaseModel):
    job_id: str
    component: str
    # Which generated point feeds the component. Defaults to the component
    # name itself — the generate UI seeds point ids from the selected
    # generators, so the common case needs no mapping at all.
    point_id: str | None = None
    # What the network's naive snapshots mean: "UTC", or an IANA zone whose
    # wall clock they are (the UTC weather series is then shifted by that
    # zone's offset, DST included). Unset: UTC when the project's commercial
    # timezone is set, else applied unshifted with a `warnings` entry.
    time_basis: str | None = None
    # The target. Unset: the artifact's family default (a renewable capacity
    # factor onto generators/p_max_pu, a load shape onto loads/p_set).
    component_type: str | None = None
    attribute: str | None = None


class LoadGenerateRequest(BaseModel):
    # A load archetype (`services/profile_gen/load_archetypes.py`) for the
    # named loads of the open network; one column per load, ids = names.
    archetype: str
    loads: list[str] = Field(min_length=1)
    params: dict[str, Any] | None = None
    year: int | None = None
    # Archetypes are hourly shapes; the field stays for the shared engine call.
    resolution: Literal["hourly"] = "hourly"
    # A previous artifact's `refinements`, replayed onto the new series
    # (`profile_gen/refine.replay`).
    refinements: list[dict[str, Any]] | None = None


class AvailabilityGenerateRequest(BaseModel):
    # An availability kind (`services/profile_gen/availability.py`) for the
    # named generators (or, with component_type "links", links) of the open
    # network; one column per component. `components` names either kind;
    # `generators` is kept for the generator case.
    kind: str
    generators: list[str] | None = None
    components: list[str] | None = None
    component_type: Literal["generators", "links"] = "generators"
    params: dict[str, Any] | None = None
    # The source year. Unset: the network's (first period's) year, so dated
    # maintenance windows land where the user means them.
    year: int | None = None
    refinements: list[dict[str, Any]] | None = None


class SeriesGenerateRequest(BaseModel):
    # A series kind (`services/profile_gen/series.py`) for named components;
    # the kind decides their type (tou: params.component_type, default
    # generators; inflow: storage units; cop: links).
    kind: str
    components: list[str] = Field(min_length=1)
    params: dict[str, Any] | None = None
    year: int | None = None
    refinements: list[dict[str, Any]] | None = None


class CurrentSeriesRequest(BaseModel):
    # The series a component carries now, made into a pending artifact the
    # refinement ops act on (services/profile_gen/current.py).
    component_type: str
    attribute: str
    component: str


class DefaultsPreviewRequest(BaseModel):
    # "Generate defaults for all" (services/profile_gen/defaults.py).
    replace_generated: bool = False
    # Components sized by their peak (= static p_set) instead of their mean.
    peak_sized: list[str] = Field(default_factory=list)


class DefaultsApplyRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1)


class RefineRequest(BaseModel):
    # A pending artifact to refine by deterministic ops
    # (services/profile_gen/refine.py); the result is a new pending artifact.
    job_id: str
    ops: list[dict[str, Any]] = Field(min_length=1)


class ShapeRequest(BaseModel):
    # A pending artifact to calibrate, the ordered ops (services/profile_gen/
    # shape.py), and optionally which points (default: every point).
    job_id: str
    ops: list[dict[str, Any]]
    points: list[str] | None = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def points_from_generators(names: list[str]) -> list[dict[str, Any]]:
    """Resolve generator names to profile points (id/lon/lat) from the
    FOREGROUND network's bus coordinates — the one code path behind the chat
    tools' `from_generators` and the GUI's default generation flow. PyPSA
    generators carry no coordinates of their own: a generator sits at its
    bus. The point id IS the generator name, which is what apply defaults to.

    Unknown names are a 422 `unknown_generator`. A bus without usable
    coordinates (NaN, out of range, or PyPSA's 0/0 default — the rule
    `network_geometry._bus_coord` applies to the map and line lengths) is a
    422 `missing_coordinates` listing the generators: never a profile
    generated for 0°E 0°N."""
    from services.network_geometry import _bus_coord
    from services.pypsa_service import PyPSAService

    n = PyPSAService.get_active_context().network
    points: list[dict[str, Any]] = []
    missing: list[str] = []
    no_coords: list[tuple[str, str]] = []
    for raw in names:
        g = str(raw)
        if g not in n.generators.index:
            missing.append(g)
            continue
        bus = str(n.generators.at[g, "bus"] or "")
        if bus not in n.buses.index:
            missing.append(f"{g} (its bus {bus!r} is not on the network)")
            continue
        coord = _bus_coord(n, bus)
        if coord is None:
            no_coords.append((g, bus))
            continue
        points.append({"id": g, "lon": coord[0], "lat": coord[1]})
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error_kind": "unknown_generator",
                "message": "unknown generator(s): "
                           + ", ".join(missing)
                           + f"; the network holds {len(n.generators.index)} "
                           "generators — check the name(s)",
            },
        )
    if no_coords:
        raise HTTPException(
            status_code=422,
            detail={
                "error_kind": "missing_coordinates",
                "generators": [g for g, _ in no_coords],
                "message": "no coordinates for "
                           + ", ".join(f"{g} (bus {b})" for g, b in no_coords)
                           + ": set coordinates on the bus (x = longitude, "
                           "y = latitude, WGS-84 degrees) or pass an explicit "
                           "point for the generator, or upload a profile for "
                           "it instead",
            },
        )
    return points


def _require_generators(names: list[str]) -> None:
    """422 `unknown_generator` for names the foreground network lacks — the
    check an explicit coordinate override must not skip."""
    if not names:
        return
    from services.pypsa_service import PyPSAService

    gens = PyPSAService.get_active_context().network.generators.index
    unknown = [str(g) for g in names if str(g) not in gens]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "error_kind": "unknown_generator",
                "message": "unknown generator(s): " + ", ".join(unknown)
                           + f"; the network holds {len(gens)} generators "
                           "— check the name(s)",
            },
        )


def _merged_points(body: EstimateRequest) -> list[dict[str, Any]]:
    """Explicit points plus generator-resolved points; at least one source is
    required. Duplicated ids are the engine's typed refusal downstream."""
    merged = [p.model_dump() for p in body.points]
    if body.from_generators:
        # An explicit point for a generator is its coordinate override.
        # Names it shadows must still exist on the network.
        explicit = {p["id"] for p in merged}
        resolved = points_from_generators(
            [g for g in body.from_generators if str(g) not in explicit]
        )
        _require_generators(
            [g for g in body.from_generators if str(g) in explicit]
        )
        merged.extend(resolved)
    if not merged:
        raise HTTPException(
            status_code=422,
            detail={
                "error_kind": "no_points",
                "message": "give either `points` (id/lon/lat) or "
                           "`from_generators` (generator names whose bus "
                           "coordinates seed the sites)",
            },
        )
    return merged


def _validate_technology(technology: str) -> None:
    """The engine's typed refusal, raised BEFORE preset resolution so an
    unsupported carrier never surfaces as a confusing 'no such preset'."""
    tech = str(technology).strip().lower()
    if tech not in engine.SUPPORTED_TECHNOLOGIES:
        raise engine.UnsupportedTechnologyError(
            f"cannot generate a profile for technology {technology!r}; "
            f"supported: {', '.join(engine.SUPPORTED_TECHNOLOGIES)}"
        )


def _preset_params(body: EstimateRequest) -> dict[str, Any]:
    name = body.preset or "default"
    return presets.resolve_params(name, body.technology, body.params)


def _pending_dir(proj: AuthorizedProject) -> Path:
    return proj.directory / "profiles_pending"


def _pending_records(proj: AuthorizedProject) -> list[dict[str, Any]]:
    """Every pending artifact's meta record, newest first. A `.nc` without a
    readable `.json` is skipped — the record IS the artifact's identity."""
    pending = _pending_dir(proj)
    if not pending.exists():
        return []
    records = []
    for meta_path in pending.glob("*.json"):
        try:
            record = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (pending / f"{meta_path.stem}.nc").exists():
            records.append(record)
    records.sort(key=lambda r: r.get("generated_at", ""), reverse=True)
    return records


def _pending_artifact(proj: AuthorizedProject,
                      job_id: str) -> tuple[Path, dict[str, Any]]:
    """The pending `.nc` path and meta record for `job_id`; 422 for an id
    that escapes the pending folder, 404 for one with no artifact."""
    pending = _pending_dir(proj).resolve()
    nc = (pending / f"{job_id}.nc").resolve()
    meta_path = pending / f"{job_id}.json"
    if nc.parent != pending:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "malformed_job_id",
                    "message": f"malformed job id {job_id!r}"},
        )
    if not nc.exists() or not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no pending profile for job {job_id!r}",
        )
    return nc, json.loads(meta_path.read_text())


def _error_kind(exc: Exception) -> str:
    name = type(exc).__name__
    return "".join(
        f"_{c.lower()}" if c.isupper() else c for c in name
    ).lstrip("_").removesuffix("_error")


# Apply refusals whose error_kind is written out as a literal, so
# tests/test_tool_error_kind_manifest.py's scan sees each one; the rest fall
# back to the class-name kind. Subclasses before their bases.
_APPLY_REFUSALS: tuple[tuple[type[Exception], dict[str, str]], ...] = (
    (ComponentNotFoundError, {"error_kind": "component_not_found"}),
    (ProfileBoundConflictError, {"error_kind": "profile_bound_conflict"}),
    (UnsupportedTargetError, {"error_kind": "unsupported_target"}),
    (UnknownFamilyError, {"error_kind": "unknown_family"}),
    (FamilyTargetMismatchError, {"error_kind": "family_target_mismatch"}),
    (InvalidSeriesUnitError, {"error_kind": "invalid_series_unit"}),
    (DcLoadOwnedError, {"error_kind": "dc_load_owned"}),
    (PocTariffError, {"error_kind": "poc_tariff"}),
    (CopBusError, {"error_kind": "cop_bus"}),
    (TwoWayFloorError, {"error_kind": "two_way_floor"}),
    (PocBoundError, {"error_kind": "poc_bound"}),
    (CurrentChangedError, {"error_kind": "current_changed_since_refinement"}),
    (WindowSourceGapError, {"error_kind": "window_source_gap"}),
)

# Generation and upload refusals (load, availability), literal for the same
# reason.
_GENERATE_REFUSALS: tuple[tuple[type[Exception], dict[str, str]], ...] = (
    (UnknownArchetypeError, {"error_kind": "unknown_archetype"}),
    (InvalidArchetypeParamsError, {"error_kind": "invalid_archetype_params"}),
    (UploadFormatError, {"error_kind": "load_upload_format"}),
    (UploadUnitError, {"error_kind": "load_upload_unit"}),
    (UploadCoverageError, {"error_kind": "load_upload_coverage"}),
    (UploadNoLoadError, {"error_kind": "unknown_load"}),
    (ForcedOutagesNotWrittenError, {"error_kind": "forced_outages_not_written"}),
    (UnknownAvailabilityKindError, {"error_kind": "unknown_availability_kind"}),
    (InvalidAvailabilityParamsError, {"error_kind": "invalid_availability_params"}),
    (MaintenanceYearError, {"error_kind": "maintenance_year"}),
    (PartLoadNotASeriesError, {"error_kind": "part_load_not_a_series"}),
    (UnknownSeriesKindError, {"error_kind": "unknown_series_kind"}),
    (MarketBundlesUnavailableError, {"error_kind": "market_bundles_unavailable"}),
    (InvalidSeriesParamsError, {"error_kind": "invalid_series_params"}),
    (InvalidRefineOpError, {"error_kind": "invalid_refine_op"}),
    (WindowClockUnknownError, {"error_kind": "window_clock_unknown"}),
    (RefineReplayError, {"error_kind": "refine_replay"}),
    (WindowSourceOpError, {"error_kind": "window_source_op"}),
    (AnnualEnergyOnWindowSourceError, {"error_kind": "annual_energy_on_window_source"}),
    (CurrentSeriesError, {"error_kind": "no_current_series"}),
)


def _apply_refusal_kind(exc: Exception) -> dict[str, str]:
    for cls, kind in (*_APPLY_REFUSALS, *_GENERATE_REFUSALS):
        if isinstance(exc, cls):
            return dict(kind)
    return {"error_kind": _error_kind(exc)}


# ---------------------------------------------------------------------------
# Global: presets + estimate
# ---------------------------------------------------------------------------


@router.get("/presets")
def list_profile_presets(technology: str | None = None):
    """The preset catalogue (optionally one technology's)."""
    try:
        found = presets.list_presets(technology)
    except ProfileGenError as exc:
        raise HTTPException(status_code=422,
                            detail=public_message(exc, route="presets")) from exc
    return {
        "presets": [
            {
                "name": p.name,
                "technology": p.technology,
                "description": getattr(p, "description", ""),
                "params": dict(p.params),
            }
            for p in found
        ]
    }


@router.get("/load-archetypes")
def list_load_archetypes():
    """The load archetypes with their parameter defaults, and the presets."""
    return {
        "archetypes": [{"name": a, "params": load_archetype_defaults(a)}
                       for a in ARCHETYPES],
        "presets": list_load_presets(),
    }


@router.post("/estimate")
def estimate_profile(body: EstimateRequest):
    """A quick SYNTHETIC preview, downsampled to daily means and labelled an
    estimate. This is what the map shows while the user drags a pin — the
    real artifact comes from the queued generate."""
    try:
        _validate_technology(body.technology)
        params = _preset_params(body)
        result = engine.generate_profile(
            _merged_points(body),
            body.technology,
            params=params,
            source="synthetic",
            year=body.year,
            resolution=body.resolution,
        )
    except HTTPException:
        raise
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": _error_kind(exc),
                    "message": public_message(exc, route="estimate")},
        ) from exc
    # Hourly previews are downsampled to daily means for the map; coarser
    # resolutions are already compact enough to return as-is.
    frame = (
        result.series.resample("1D").mean()
        if body.resolution == "hourly"
        else result.series
    )
    return {
        "estimate": True,
        "source": "synthetic",
        "technology": result.meta["technology"],
        "weather_year": result.meta["weather_year"],
        "resolution": result.meta["resolution"],
        "timestamps": [t.isoformat() for t in frame.index],
        "series": {c: [round(float(v), 6) for v in frame[c]] for c in frame.columns},
    }


# ---------------------------------------------------------------------------
# Project-scoped: generate / pending / apply
# ---------------------------------------------------------------------------


def _row(proj: AuthorizedProject, db: DBSession) -> Project:
    project = db.get(Project, uuid.UUID(proj.uuid))
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{proj.name}' not found")
    return project


def _check_lock(proj: AuthorizedProject, db: DBSession, user: User | None) -> None:
    from types import SimpleNamespace

    from routers.projects import _check_project_lock

    try:
        target = SimpleNamespace(id=uuid.UUID(proj.uuid), name=proj.name)
    except (TypeError, ValueError):
        return
    _check_project_lock(db, target, user)


@project_router.post("/{name}/profiles/generate", status_code=202)
def generate_profiles(
    body: GenerateRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Queue the generation as a `profile_gen` solve-queue job and answer
    202 with the job. One FIFO serves every kind: the job starts after any
    running solve — follow it in the solve queue, abort it there.

    The spec is validated IN THE REQUEST (points, technology, preset, and a
    CDS preflight for the atlite source): a job that cannot start is a 422
    now, not a failed job later."""
    _check_lock(proj, db, user)
    spec = body.model_dump()
    spec.pop("from_generators", None)  # resolved into concrete points below
    try:
        # Validate the way the runner will, minus the work: points, technology,
        # preset name (resolve_params raises UnknownPresetError), and a CDS
        # preflight for the atlite source. The runner re-resolves params from
        # the raw preset+overrides; nothing computed here is passed down.
        spec["points"] = _merged_points(body)
        engine._normalize_points(spec["points"])
        _validate_technology(body.technology)
        _preset_params(body)
        if body.source == "atlite":
            cds_preflight()
    except HTTPException:
        raise
    except CdsCredentialError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "cds_credential",
                    "message": public_message(exc, route="generate")},
        ) from exc
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": _error_kind(exc),
                    "message": public_message(exc, route="generate")},
        ) from exc

    project = _row(proj, db)
    job, created = sq.solve_queue.enqueue_unique(
        project.name,
        project_key=proj.registry_key,
        storage_dir=str(proj.directory),
        solver_config_json=json.dumps(spec),
        enqueued_by_user_id=getattr(user, "id", None),
        kind=sq.KIND_PROFILE_GEN,
    )
    if not created:
        raise HTTPException(
            status_code=409,
            detail=f"project '{project.name}' already has a {job.kind} job "
                   f"{job.status}; wait for it or abort it",
        )
    return sq.solve_queue.get_job(job.id) or job.to_public(None)


def _refine_frame(ctx, frame: pd.DataFrame, meta: dict, ops: list, *, replaying: bool):
    """Refine (or replay a recorded list over) every column of an artifact;
    one meta (the ops are the same for every column), stats per column, and
    the warnings."""
    from services.profile_gen import refine
    from services.profile_gen.families import default_target

    ct, attr = default_target(meta, None, None)
    run = refine.replay if replaying else refine.refine_series
    cols, stats, warnings, new_meta = {}, {}, [], meta
    for col in frame.columns:
        out = run(ctx, frame[col], meta, ops, component_type=ct, attribute=attr)
        cols[col], stats[col], new_meta = out.series, out.stats, out.meta
        warnings += [w for w in out.warnings if w not in warnings]
    return pd.DataFrame(cols, index=frame.index), new_meta, stats, warnings


def _replay_refinements(ctx, result, refinements):
    """A regenerated series replays the refinements it is given."""
    if not refinements:
        return result
    from types import SimpleNamespace

    try:
        frame, meta, _stats, warnings = _refine_frame(ctx, result.series, dict(result.meta),
                                                      refinements, replaying=True)
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="refine_replay")},
        ) from exc
    meta["replay_warnings"] = warnings
    return SimpleNamespace(series=frame, meta=meta)


def _foreground_ctx(proj: AuthorizedProject, verb: str):
    """The active context, refused (409) unless it holds this project."""
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    if ctx.loaded_project not in (None, proj.name):
        raise HTTPException(
            status_code=409,
            detail={"error_kind": "project_not_open",
                    "message": f"'{proj.name}' is not the open project "
                               f"(the foreground holds {ctx.loaded_project!r}); "
                               f"open it first, then {verb}"},
        )
    return ctx


def _preview_load_energy(ctx, frame: pd.DataFrame, record: dict,
                         stats: dict[str, dict]) -> list[str]:
    """What apply will record, shown before it: each load's modelled energy
    on this network's snapshots (a sampled template sees less than the year)
    into ``stats``, and the warnings (time basis, energy mismatch). A preview
    that cannot align (a partial-year network) says why instead; apply
    refuses the same way."""
    from services.profile_gen.apply import ProfileApplyError, preview_energy

    warnings: set[str] = set()
    for col in frame.columns:
        try:
            pv = preview_energy(ctx, frame[col], record,
                                component_type="loads", attribute="p_set")
        except ProfileApplyError as exc:
            stats[col]["modelled_mwh"] = None
            warnings.add(f"not_applicable: {public_message(exc, route='load_preview')}")
            continue
        stats[col]["modelled_mwh"] = pv["modelled_mwh"]
        warnings.update(pv["warnings"])
    return sorted(warnings)


def _dry_run(ctx, frame: pd.DataFrame, record: dict) -> tuple[list[str], dict[str, dict]]:
    """Apply's own decision for each column, written nowhere
    (`prepare_profile`): its warnings show before Apply, and a refusal it
    would give (a partial-year network, a family guard) shows as
    ``not_applicable: <why>`` so the editor can hold the button. Also each
    column's preview week (`_preview_week`) of what apply would write."""
    from services.profile_gen.apply import ProfileApplyError, prepare_profile
    from services.profile_gen.families import default_target

    component_type, attribute = default_target(record, None, None)
    warnings: set[str] = set(record.get("warnings") or [])
    weeks: dict[str, dict] = {}
    for col in frame.columns:
        try:
            prepared = prepare_profile(ctx, col, frame[col], record,
                                       component_type=component_type,
                                       attribute=attribute)
        except ProfileApplyError as exc:
            warnings.add(f"not_applicable: {public_message(exc, route='dry_run')}")
            continue
        warnings.update(prepared.warnings)
        weeks[col] = _preview_week(ctx.network, prepared)
    return sorted(warnings), weeks


def _preview_week(n, prepared) -> dict:
    """The calendar week (Monday 00:00 on) of the network's snapshots that
    holds the series' most telling hour — its lowest for an availability
    cap, its highest otherwise — as apply would write it, beside the
    component's current series for the same snapshots when it has one."""
    aligned = prepared.aligned
    values = aligned.to_numpy(dtype=float)
    low = prepared.attribute == "p_max_pu"
    i = int(np.nanargmin(values) if low else np.nanargmax(values))
    idx = aligned.index
    stamps = pd.DatetimeIndex(idx.get_level_values(-1) if isinstance(idx, pd.MultiIndex) else idx)
    monday = stamps[i].normalize() - pd.Timedelta(days=stamps[i].weekday())
    mask = (stamps >= monday) & (stamps < monday + pd.Timedelta(days=7))
    if isinstance(idx, pd.MultiIndex):
        mask &= np.asarray(idx.get_level_values(0) == idx[i][0])
    table = getattr(n, f"{prepared.component_type}_t")[prepared.attribute]
    current = None
    if prepared.component in table.columns:
        current = [round(float(v), 4) for v in
                   table[prepared.component].reindex(idx).to_numpy(dtype=float)[mask]]
    shown = stamps[mask]
    return {
        "label": "lowest week" if low else "peak week",
        "period": int(idx[i][0]) if isinstance(idx, pd.MultiIndex) else None,
        "snapshots": [t.isoformat() for t in shown],
        "new": [round(float(v), 4) for v in values[mask]],
        "current": current,
    }


def _load_stats(frame: pd.DataFrame, scale: float, year: int) -> dict[str, dict]:
    hours = 8784 if pd.Timestamp(year=year, month=1, day=1).is_leap_year else 8760
    return {col: {"peak_mw": float(frame[col].max()) * scale,
                  "annual_mwh": float(frame[col].mean()) * scale * hours}
            for col in frame.columns}


@project_router.post("/{name}/profiles/loads/generate")
def generate_load_profiles(
    body: LoadGenerateRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Generate a load archetype for named loads into the pending store.

    Synchronous: an archetype is closed-form and takes milliseconds, so it
    skips the solve queue the weather sources need. The artifact is the same
    pending pair the renewable jobs write (`job.write_pending`) and is applied
    through the same `/profiles/apply`. Nothing touches the network here."""
    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "generate")
    missing = [ld for ld in body.loads if ld not in ctx.network.loads.index]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "unknown_load",
                    "message": f"no load named {', '.join(map(repr, missing))} "
                               f"on the open network; it has "
                               f"{sorted(ctx.network.loads.index)[:20]}"},
        )
    params = dict(body.params or {})
    # Working hours are on the site's wall clock: with the project's
    # commercial timezone set, its snapshots are UTC (`resolve_time_basis`),
    # so the archetype maps on that zone unless the caller names one.
    if "timezone" not in params:
        from services.profile_gen.apply import commercial_timezone

        tz = commercial_timezone(ctx)
        if tz:
            params["timezone"] = tz
    try:
        result = engine.generate_profile(
            body.loads, body.archetype, params=params,
            year=body.year, resolution=body.resolution, family="load",
        )
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="load_generate")},
        ) from exc

    result = _replay_refinements(ctx, result, body.refinements)
    job_id = str(uuid.uuid4())
    record = dict(result.meta)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = list(result.series.columns)
    write_pending(str(proj.directory), job_id, result.series, record)

    stats = _load_stats(result.series, float(record.get("scale_mw") or 1.0),
                        int(record["weather_year"]))
    warnings = _preview_load_energy(ctx, result.series, record, stats)
    dry, weeks = _dry_run(ctx, result.series, record)
    return {**record, "stats": stats, "warnings": sorted({*warnings, *dry}), "weeks": weeks}


@project_router.post("/{name}/profiles/loads/upload")
async def upload_load_profiles(
    file: UploadFile = File(...),
    unit: str | None = Form(None),
    timezone: str | None = Form(None),
    year: int | None = Form(None),
    annual_mwh: float | None = Form(None),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Read a measured load export (`load_upload.read_load_upload`) into the
    pending store, one column per load the network has; apply it through
    `/profiles/apply` like a generated profile. Columns that name no load
    come back as `unmatched` and are not read. Naive timestamps default to
    the project's commercial timezone."""
    from fastapi.concurrency import run_in_threadpool

    from services.profile_gen.apply import commercial_timezone
    from services.upload_guard import read_capped

    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "upload")
    blob = await read_capped(file)
    try:
        # A large export is seconds of pandas: off the event loop.
        up = await run_in_threadpool(
            read_load_upload, blob, file.filename or "upload.csv", unit=unit,
            timezone=timezone, default_timezone=commercial_timezone(ctx),
            year=year, annual_mwh=annual_mwh, only=set(ctx.network.loads.index),
        )
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="load_upload")},
        ) from exc
    matched = list(up.frame.columns)
    unmatched = up.unmatched
    frame = up.frame
    job_id = str(uuid.uuid4())
    record = dict(up.meta)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = matched
    record["notes"] = up.notes
    record["qa_warnings"] = up.warnings  # timeseries_qa findings, per column
    write_pending(str(proj.directory), job_id, frame, record)
    stats = _load_stats(frame, 1.0, int(record["weather_year"]))
    preview = _preview_load_energy(ctx, frame, record, stats)
    dry, weeks = _dry_run(ctx, frame, record)
    return {**record, "unmatched": unmatched, "stats": stats,
            "warnings": sorted({*preview, *dry}), "weeks": weeks}


@project_router.post("/{name}/profiles/availability/generate")
def generate_availability_profiles(
    body: AvailabilityGenerateRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Generate an availability bound (maintenance, must_run, derating) for
    named generators into the pending store; `/profiles/apply` writes it to
    the target it states (p_max_pu or p_min_pu). Synchronous, like the load
    archetypes; nothing touches the network here."""
    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "generate")
    names = body.components or body.generators or []
    if not names:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "unknown_component",
                    "message": "name the components (or generators) to generate for"})
    table = getattr(ctx.network, body.component_type)
    missing = [g for g in names if g not in table.index]
    if missing and body.component_type == "generators":
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "unknown_generator",
                    "message": f"no generator named {', '.join(map(repr, missing))} "
                               f"on the open network"},
        )
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "unknown_component",
                    "message": f"no {body.component_type[:-1]} named "
                               f"{', '.join(map(repr, missing))} on the open network"},
        )
    from services.profile_gen.families import is_weather_carrier

    weather = ([g for g in names if is_weather_carrier(table.at[g, "carrier"])]
               if body.component_type == "generators" else [])
    if weather:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "carrier_mismatch",
                    "message": f"{', '.join(map(repr, weather))} carry a weather "
                               "profile; an availability series would overwrite it"},
        )
    snaps = ctx.network.snapshots
    first = (snaps.get_level_values(-1)[0] if isinstance(snaps, pd.MultiIndex)
             else pd.DatetimeIndex(snaps)[0])
    params = dict(body.params or {})
    if "timezone" not in params:
        from services.profile_gen.apply import commercial_timezone

        tz = commercial_timezone(ctx)
        if tz:
            params["timezone"] = tz
    try:
        result = engine.generate_profile(
            names, body.kind, params=params,
            year=body.year or int(first.year), family="availability",
        )
        result.meta["target"] = [body.component_type, result.meta["target"][1]]
        # A dated window that apply would refuse is refused before anything
        # reaches the pending store.
        from services.profile_gen.availability import check_dated_windows

        check_dated_windows(ctx, result.meta)
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="availability_generate")},
        ) from exc

    result = _replay_refinements(ctx, result, body.refinements)
    job_id = str(uuid.uuid4())
    record = dict(result.meta)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = list(result.series.columns)
    write_pending(str(proj.directory), job_id, result.series, record)
    is_cap = record["target"][1] == "p_max_pu"
    stats = {}
    for col in result.series.columns:
        v = result.series[col]
        stats[col] = {"min": float(v.min()), "mean": float(v.mean()),
                      "max": float(v.max())}
        if is_cap:
            stats[col]["hours_reduced"] = int((v < 1.0 - 1e-9).sum())
        else:
            stats[col]["hours_floored"] = int((v > 1e-9).sum())
    warnings, weeks = _dry_run(ctx, result.series, record)
    return {**record, "stats": stats, "warnings": warnings, "weeks": weeks}


_SERIES_TYPES = {"inflow": "storage_units", "cop": "links"}


@project_router.post("/{name}/profiles/series/generate")
def generate_series_profiles(
    body: SeriesGenerateRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Generate another model series (a time-of-use price, hydro inflow, a
    heat-pump COP) for named components into the pending store;
    `/profiles/apply` writes it to the target it states. Synchronous."""
    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "generate")
    params = dict(body.params or {})
    ctype = _SERIES_TYPES.get(body.kind) or str(params.get("component_type") or "generators")
    table = getattr(ctx.network, ctype, None)
    missing = [c for c in body.components if table is None or c not in table.index]
    if missing and body.kind in ("tou", *_SERIES_TYPES):
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "unknown_component",
                    "message": f"no {ctype} named {', '.join(map(repr, missing))} "
                               "on the open network"},
        )
    if body.kind == "tou" and ctype == "links":
        # A price on a point-of-connection link is refused before anything
        # reaches the pending store, as apply would refuse it.
        from services.profile_gen import series as series_mod

        try:
            for comp in body.components:
                series_mod.guard(ctx, "links", comp,
                                 {"kind": "tou", "target": ["links", "marginal_cost"]})
        except ProfileGenError as exc:
            raise HTTPException(
                status_code=422,
                detail={**_apply_refusal_kind(exc),
                        "message": public_message(exc, route="series_generate")},
            ) from exc
    if "timezone" not in params and body.kind in ("tou", "cop"):
        from services.profile_gen.apply import _commercial_timezone

        tz = _commercial_timezone(ctx)
        if tz:
            params["timezone"] = tz
    snaps = ctx.network.snapshots
    first = (snaps.get_level_values(-1)[0] if isinstance(snaps, pd.MultiIndex)
             else pd.DatetimeIndex(snaps)[0])
    try:
        result = engine.generate_profile(
            body.components, body.kind, params=params,
            year=body.year or int(first.year), family="series",
        )
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="series_generate")},
        ) from exc

    result = _replay_refinements(ctx, result, body.refinements)
    job_id = str(uuid.uuid4())
    record = dict(result.meta)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = list(result.series.columns)
    write_pending(str(proj.directory), job_id, result.series, record)
    scale = float(record.get("scale_mw") or 1.0)
    stats = {}
    for col in result.series.columns:
        v = result.series[col] * scale
        stats[col] = {"min": float(v.min()), "mean": float(v.mean()), "max": float(v.max())}
        if body.kind == "inflow":
            stats[col]["annual_mwh"] = float(v.sum())
    warnings, weeks = _dry_run(ctx, result.series, record)
    return {**record, "stats": stats, "warnings": warnings, "weeks": weeks}


@project_router.get("/{name}/profiles/current")
def get_current_series(
    component_type: str,
    attribute: str,
    component: str,
    proj: AuthorizedProject = ProjectAccessDep,
):
    """What the component carries now for that attribute: nothing, a
    generated series (its family and provenance), or an upload."""
    from services.profile_gen.current import describe_current

    ctx = _foreground_ctx(proj, "read its series")
    return describe_current(ctx, component_type, attribute, component)


@project_router.post("/{name}/profiles/from-current")
def pending_from_current(
    body: CurrentSeriesRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """The component's current series as a pending artifact, on the
    snapshot clock it was stored in, so the refinement ops act on a measured
    upload the same way as on a generated series. Nothing touches the
    network here; apply refuses the artifact once the stored series changes."""
    from services.profile_gen.current import series_from_current

    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "refine its series")
    try:
        series, meta = series_from_current(ctx, body.component_type, body.attribute,
                                           body.component)
    except ProfileApplyError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="from_current")},
        ) from exc
    frame = series.to_frame(body.component)
    job_id = str(uuid.uuid4())
    record = dict(meta)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = [body.component]
    write_pending(str(proj.directory), job_id, frame, record)
    v = series.dropna()
    stats = {body.component: {"min": float(v.min()), "mean": float(v.mean()),
                              "max": float(v.max())}}
    if meta.get("unit") == "MW":
        hours = 24 * (366 if pd.Timestamp(year=int(meta["weather_year"]), month=1,
                                          day=1).is_leap_year else 365)
        stats[body.component].update(peak_mw=float(v.max()),
                                     annual_mwh=float(v.mean()) * hours)
        if body.component_type == "loads":
            _preview_load_energy(ctx, frame, record, stats)  # modelled_mwh, as generated loads show
    warnings, weeks = _dry_run(ctx, frame, record)
    return {**record, "stats": stats, "warnings": warnings, "weeks": weeks}


@project_router.post("/{name}/profiles/defaults/preview")
def preview_default_series(
    body: DefaultsPreviewRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Every family's default series, one row per component: a generated
    pending series (its reason, sizing, stats, warnings and preview week),
    or why it is skipped. Nothing touches the network here."""
    from services.profile_gen.defaults import preview_defaults

    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "preview defaults")
    rows = preview_defaults(ctx, str(proj.directory), proj.name,
                            replace_generated=body.replace_generated,
                            peak_sized=frozenset(body.peak_sized))
    out = []
    for row in rows:
        record = row.pop("record", None)
        if record is not None:
            nc, _meta = _pending_artifact(proj, row["job_id"])
            with pending_io_lock(), xr.open_dataset(nc) as ds:
                frame = ds.to_dataframe()
            frame.index = pd.DatetimeIndex(frame.index)
            dry, weeks = _dry_run(ctx, frame[[row["component"]]], record)
            row["warnings"] = sorted({*row["warnings"], *dry})
            row["week"] = weeks.get(row["component"])
        out.append(row)
    return {"rows": out}


@project_router.post("/{name}/profiles/defaults/apply")
def apply_default_series(
    body: DefaultsApplyRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Apply the chosen default series under ONE undo step. Each row is
    re-checked (a measured series is never replaced) and reported on its
    own: a refusal does not roll the others back; undo reverts the batch."""
    from services.network_undo import push_undo_snapshot

    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "apply defaults")
    push_undo_snapshot()
    results = []
    for job_id in body.job_ids:
        try:
            nc, meta = _pending_artifact(proj, job_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            results.append({"job_id": job_id, "component": None, "applied": False, **detail})
            continue
        component = (meta.get("points") or [None])[0]
        try:
            component_type, attribute = default_target(meta, None, None)
            key = (component_type, attribute, component)
            if key in ctx.user_ts and not ctx.user_ts_meta.get(key):
                results.append({"job_id": job_id, "component": component, "applied": False,
                                "error_kind": "measured_series_kept",
                                "message": f"{component!r} carries a measured upload "
                                           "now; a default never replaces it"})
                continue
            with pending_io_lock(), xr.open_dataset(nc) as ds:
                frame = ds.to_dataframe()
            frame.index = pd.DatetimeIndex(frame.index)
            summary = assign_profile(ctx, component, frame[component], meta,
                                     component_type=component_type, attribute=attribute)
        except ProfileApplyError as exc:
            results.append({"job_id": job_id, "component": component, "applied": False,
                            **_apply_refusal_kind(exc),
                            "message": public_message(exc, route="defaults_apply",
                                                      job_id=job_id)})
            continue
        results.append({"job_id": job_id, "component": component, "applied": True,
                        "warnings": summary.get("warnings", []),
                        "peak": summary.get("peak")})
    return {"results": results,
            "applied": sum(r["applied"] for r in results),
            "undo": "one undo reverts every series applied here"}


@project_router.post("/{name}/profiles/refine")
def refine_profile(
    body: RefineRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Refine a pending artifact by deterministic ops into a NEW pending
    artifact (its meta records the ops, `based_on` and the user-refined
    provenance); `/profiles/apply` writes it, behind the old → new card in
    chat. Nothing touches the network here."""
    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "refine")
    nc, meta = _pending_artifact(proj, body.job_id)
    with pending_io_lock(), xr.open_dataset(nc) as ds:
        frame = ds.to_dataframe()
    frame.index = pd.DatetimeIndex(frame.index)
    frame = frame[[c for c in meta.get("points", frame.columns) if c in frame.columns]]
    try:
        new, new_meta, stats, warnings = _refine_frame(ctx, frame, meta, body.ops,
                                                       replaying=False)
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="refine",
                                              job_id=body.job_id)},
        ) from exc
    job_id = str(uuid.uuid4())
    record = {k: v for k, v in new_meta.items() if k != "replay_warnings"}
    record["job_id"] = job_id
    record["based_on"] = body.job_id
    record["project_id"] = proj.name
    record["points"] = list(new.columns)
    write_pending(str(proj.directory), job_id, new, record)
    dry, weeks = _dry_run(ctx, new, record)
    return {**record, "stats": stats, "warnings": sorted({*warnings, *dry}), "weeks": weeks}


@project_router.get("/{name}/profiles/pending")
def list_pending_profiles(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
):
    """The pending generated profiles (newest first), plus any profile_gen
    job of this project still in the queue."""
    records = _pending_records(proj)
    live = [
        j for j in (sq.solve_queue.get_job(jid) for jid in
                    list(sq.solve_queue._jobs))
        if j and j.get("kind") == sq.KIND_PROFILE_GEN
        and j.get("project_id") == proj.name
        and j.get("status") in ("queued", "running")
    ]
    return {"pending": records, "jobs": live, "keep": KEEP_PENDING}


@project_router.post("/{name}/profiles/apply")
def apply_profile(
    body: ApplyRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Write one pending profile onto its target: a generator's `p_max_pu`
    for a renewable profile, a load's `p_set` for a load shape, or the
    `component_type`/`attribute` the body names.

    Undo FIRST: this prefix is outside the undo middleware's coverage, so the
    snapshot is pushed here, before any write. The component is re-resolved at
    apply time — renamed or deleted since generation is a typed refusal; a bus
    that moved since generation comes back as a `warning` on the 200."""
    _check_lock(proj, db, user)

    nc, meta = _pending_artifact(proj, body.job_id)

    point_id = body.point_id or body.component
    try:
        with pending_io_lock(), xr.open_dataset(nc) as ds:
            if point_id not in ds.data_vars:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error_kind": "point_not_in_profile",
                        "message": f"the pending profile has no column "
                                   f"{point_id!r}; it holds "
                                   f"{sorted(ds.data_vars)}",
                    },
                )
            series = ds[point_id].to_series()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — a corrupt artifact is a 422, not a 500
        raise HTTPException(
            status_code=422,
            detail=(f"pending profile for job {body.job_id!r} is unreadable: "
                    f"{public_message(exc, route='apply', job_id=body.job_id)}"),
        ) from exc
    series = pd.Series(series.to_numpy(), index=pd.DatetimeIndex(series.index),
                       name=body.component, dtype=float)

    # Apply writes the FOREGROUND context — the project the caller is looking
    # at. Refuse to write a background project the user may not even have open.
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    if ctx.loaded_project not in (None, proj.name):
        raise HTTPException(
            status_code=409,
            detail=f"'{proj.name}' is not the open project "
                   f"(the foreground holds {ctx.loaded_project!r}); "
                   "open it first, then apply",
        )

    warning = None
    coords = (meta.get("point_coords") or {}).get(point_id)
    gen = ctx.network.generators
    if (coords is not None and body.component in gen.index
            and body.component_type in (None, "generators")):
        bus = str(gen.at[body.component, "bus"])
        if bus in ctx.network.buses.index:
            from services.network_geometry import _bus_coord

            now = _bus_coord(ctx.network, bus)
            if now is None:
                warning = (
                    f"bus '{bus}' has no coordinates now: profile was made "
                    f"for ({coords[0]:.3f}, {coords[1]:.3f})"
                )
            elif (abs(now[0] - coords[0]) > 0.5
                  or abs(now[1] - coords[1]) > 0.5):
                warning = (
                    f"bus '{bus}' moved since generation: profile was made for "
                    f"({coords[0]:.3f}, {coords[1]:.3f}), the bus now sits at "
                    f"({now[0]:.3f}, {now[1]:.3f})"
                )

    # Undo snapshot BEFORE the write — the middleware only covers
    # /api/network/ and /api/io/, and apply is neither.
    from services.network_undo import push_undo_snapshot

    push_undo_snapshot()

    try:
        component_type, attribute = default_target(
            meta, body.component_type, body.attribute)
        summary = assign_profile(ctx, body.component, series, meta,
                                 component_type=component_type,
                                 attribute=attribute,
                                 time_basis=body.time_basis)
    except ProfileApplyError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="apply", job_id=body.job_id)},
        ) from exc

    from routers.simulation import _state_update

    _state_update(status="idle")
    if warning:
        summary["warning"] = warning
    return summary


@project_router.post("/{name}/profiles/shape")
def shape_profile(
    body: ShapeRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Calibrate a pending profile: run the ops on its weather-year series
    and write the result as a NEW pending artifact (`parent_job`, op chain,
    parent/result stats, `calibrated: true`). The network is never written —
    applying the shaped artifact goes through the same apply route and its
    refusals."""
    _check_lock(proj, db, user)
    nc, parent = _pending_artifact(proj, body.job_id)
    with pending_io_lock(), xr.open_dataset(nc) as ds:
        frame = ds.to_dataframe()
    frame.index = pd.DatetimeIndex(frame.index)
    if body.points is not None and not body.points:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "no_points",
                    "message": "`points` is empty; omit it to shape every point"},
        )
    wanted = body.points if body.points is not None else list(frame.columns)
    unknown = [p for p in wanted if p not in frame.columns]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "point_not_in_profile",
                    "message": f"the pending profile has no column(s) "
                               f"{unknown}; it holds {list(frame.columns)}"},
        )
    shaped: dict[str, pd.Series] = {}
    per_point: dict[str, dict[str, Any]] = {}
    for point in wanted:
        try:
            shaped[point], per_point[point] = shape.apply_chain(
                frame[point].astype(float), body.ops)
        except shape.UnreachableTargetError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error_kind": "unreachable_target",
                        "message": f"{point}: "
                                   f"{public_message(exc, route='reshape', job_id=body.job_id)}",
                        "point": point,
                        "reachable_max": exc.reachable_max},
            ) from exc
        except ProfileGenError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error_kind": _error_kind(exc),
                        "message": public_message(exc, route="reshape", job_id=body.job_id)},
            ) from exc

    # Reshaping a shaped artifact extends its chain: the ops accumulate in
    # order and the stats stay anchored on the generated, uncalibrated root
    # — the provenance must not depend on an intermediate artifact that
    # eviction may delete (gatekeeper, #432).
    if parent.get("calibrated"):
        earlier = parent.get("shaping") or {}
        for point, chain in per_point.items():
            prior = earlier.get(point)
            if prior:
                chain["ops"] = list(prior.get("ops", [])) + chain["ops"]
                chain["parent_stats"] = prior.get("parent_stats",
                                                  chain["parent_stats"])
    origin_job = parent.get("origin_job") or body.job_id

    new_id = str(uuid.uuid4())
    record = {
        **parent,
        "job_id": new_id,
        "parent_job": body.job_id,
        "origin_job": origin_job,
        "parent_generated_at": parent.get("generated_at"),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "points": list(shaped),
        "point_coords": {p: c for p, c in
                         (parent.get("point_coords") or {}).items()
                         if p in shaped},
        "calibrated": True,
        "shaping": per_point,
    }
    write_pending(proj.directory, new_id, pd.DataFrame(shaped), record)
    return {"job_id": new_id, "parent_job": body.job_id,
            "points": list(shaped), "stats": per_point}
