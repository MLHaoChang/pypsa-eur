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
from services.profile_gen import engine, presets, shape, tuning, weather_prefs
from services.profile_gen.tuning import InvalidTuningError
from services.profile_gen.apply import (
    ComponentNotFoundError,
    CurrentChangedError,
    CurrentSeriesError,
    DcLoadOwnedError,
    FamilyTargetMismatchError,
    InvalidSeriesUnitError,
    ProfileApplyError,
    ProfileBoundConflictError,
    StepAggregationUnsupportedError,
    StepIrregularError,
    StepNotWholeHoursError,
    StepOverDstGapError,
    UnknownFamilyError,
    UnsupportedTargetError,
    WindowSourceGapError,
    assign_profile,
)
from services.profile_gen.cutout_cache import CdsCredentialError, cds_preflight
from services.profile_gen.current import SeriesUploadError
from services.profile_gen.defaults import is_measured
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
    connection_share,
    InvalidRefineOpError,
    RefineError,
    RefineReplayError,
    WindowClockUnknownError,
    WindowSourceOpError,
)
from services.profile_gen.series import (
    CopAmbientUnknownError,
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


class BatchMember(BaseModel):
    component: str
    point_id: str | None = None


class BatchApplyRequest(BaseModel):
    # Several points of one pending job, written in one validated, single-undo
    # step. `all_from_job` takes every point named after a wind/solar generator.
    job_id: str
    members: list[BatchMember] = Field(default_factory=list)
    all_from_job: bool = False
    time_basis: str | None = None


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
    # (`profile_gen/refine.replay`). Without them, the ops the loads' applied
    # series record replay unless `replay_refinements` is false (TSG 06).
    refinements: list[dict[str, Any]] | None = None
    replay_refinements: bool = True


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
    replay_refinements: bool = True


class SeriesGenerateRequest(BaseModel):
    # A series kind (`services/profile_gen/series.py`) for named components;
    # the kind decides their type (tou: params.component_type, default
    # generators; inflow: storage units; cop: links).
    kind: str
    components: list[str] = Field(min_length=1)
    params: dict[str, Any] | None = None
    year: int | None = None
    refinements: list[dict[str, Any]] | None = None
    replay_refinements: bool = True


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
    # Or, with ``undo_index``, the artifact re-refined without that recorded
    # op (TSG 05). Exactly one of the two (`refine_profile` refuses both or
    # neither; `refine.undo_base` an index out of range).
    job_id: str
    ops: list[dict[str, Any]] = Field(default_factory=list)
    undo_index: int | None = Field(default=None, strict=True)


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
    (CopAmbientUnknownError, {"error_kind": "cop_ambient_unknown"}),
    (TwoWayFloorError, {"error_kind": "two_way_floor"}),
    (PocBoundError, {"error_kind": "poc_bound"}),
    (CurrentChangedError, {"error_kind": "current_changed_since_refinement"}),
    (WindowSourceGapError, {"error_kind": "window_source_gap"}),
    (SeriesUploadError, {"error_kind": "series_upload"}),
    (StepNotWholeHoursError, {"error_kind": "step_not_whole_hours"}),
    (StepIrregularError, {"error_kind": "step_irregular"}),
    (StepOverDstGapError, {"error_kind": "step_over_dst_gap"}),
    (StepAggregationUnsupportedError, {"error_kind": "step_aggregation_unsupported"}),
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
                "source_type": p.source_type,
                "description": getattr(p, "description", ""),
                "params": dict(p.params),
            }
            for p in found
        ]
    }


@router.get("/schema")
def profile_tuning_schema(technology: str, preset: str = "default"):
    """Preset values, units, bounds and provenance for the Tune dialog."""
    try:
        _validate_technology(technology)
        selected = presets.get_preset(preset, technology)
        return {"technology": technology, "preset": preset,
                "fields": tuning.schema(technology, selected)}
    except ProfileGenError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": _error_kind(exc),
            "message": public_message(exc, route="schema"),
        }) from exc


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
    except InvalidTuningError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "invalid_params",
            "message": public_message(exc, route="estimate"),
        }) from exc
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
    # The weather year, decided here so the job and the answer agree: an
    # explicit year wins; else the OPEN project's snapshots; a project that is
    # not the open one never has its snapshots read implicitly (RP-03).
    # The weather bar's saved choice (RP-04b) is the default of a request that
    # names no source or no year, so the chat and Profile Studio agree with it.
    saved = weather_prefs.saved_choice(proj.directory)
    if saved and "source" not in body.model_fields_set:
        spec["source"] = saved["source"]
    if body.year is not None:
        spec["year_source"] = "explicit"
    elif saved and saved["year"] is not None:
        spec["year"], spec["year_source"] = saved["year"], "saved"
    else:
        from services.profile_gen.horizon import default_weather_year
        from services.pypsa_service import PyPSAService

        ctx = PyPSAService.get_active_context()
        open_here = ctx.loaded_project in (None, proj.name)
        spec["year"], spec["year_source"] = default_weather_year(
            ctx.network.snapshots if open_here else None)
    try:
        # Validate the way the runner will, minus the work: points, technology,
        # preset name (resolve_params raises UnknownPresetError), and a CDS
        # preflight for the atlite source. The runner re-resolves params from
        # the raw preset+overrides; nothing computed here is passed down.
        spec["points"] = _merged_points(body)
        engine._normalize_points(spec["points"])
        _validate_technology(body.technology)
        _preset_params(body)
        if spec["source"] == "atlite":
            cds_preflight()
    except HTTPException:
        raise
    except CdsCredentialError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "cds_credential",
                    "message": public_message(exc, route="generate")},
        ) from exc
    except InvalidTuningError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "invalid_params",
            "message": public_message(exc, route="generate"),
        }) from exc
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


def _refine_frame(ctx, frame: pd.DataFrame, meta: dict, ops: list, *, replaying: bool,
                  **undo):
    """Refine (or replay a recorded list over) every column of an artifact;
    one meta (the ops are the same for every column), stats per column, and
    the warnings."""
    from services.profile_gen import refine
    from services.profile_gen.families import default_target

    ct, attr = default_target(meta, None, None)
    run = refine.replay if replaying else refine.refine_series
    cols, stats, warnings, new_meta = {}, {}, [], meta
    for col in frame.columns:
        out = run(ctx, frame[col], meta, ops, component_type=ct, attribute=attr, **undo)
        cols[col], stats[col], new_meta = out.series, out.stats, out.meta
        warnings += [w for w in out.warnings if w not in warnings]
    return pd.DataFrame(cols, index=frame.index), new_meta, stats, warnings


# Opting out of a stored replay: what a refusal offers the caller instead.
_REPLAY_RETRY = {"replay_refinements": False}
# An apply records a must-run floor that means a min stable level under that
# name (`apply._prepare`); it is still the generator that made it.
_KIND_ALIASES = {"min_stable_level": "must_run"}


def _generator_of(meta: dict) -> tuple:
    """Which generator made a series: its family and archetype or kind. An
    upload has neither, so it matches no generate."""
    from services.profile_gen.families import family_of

    kind = meta.get("kind")
    return (family_of(meta), meta.get("archetype"), _KIND_ALIASES.get(kind, kind))


def _stored_refinements(ctx, result) -> list[dict[str, Any]] | None:
    """The op list the targets' applied series record, when the same generator
    made them (TSG 06): replayed by default on a re-generate. Targets holding
    different lists (or some none) are refused together, since one artifact
    records one list."""
    import copy

    from services.profile_gen.families import default_target
    from services.user_timeseries import _user_ts_lock

    ct, attr = default_target(result.meta, None, None)
    made_by = _generator_of(result.meta)
    if made_by[1] is None and made_by[2] is None:
        return None
    # One read under the store's lock, so an apply or upload running beside
    # this generate never shows half of its write.
    with _user_ts_lock:
        held = {comp: copy.deepcopy(ctx.user_ts_meta.get((ct, attr, comp)))
                if (ct, attr, comp) in ctx.user_ts else None
                for comp in result.series.columns}
    lists: dict[str, list] = {}
    for comp, stored in held.items():
        same = stored is not None and _generator_of(stored) == made_by
        lists[comp] = list(stored.get("refinements") or []) if same else []
    distinct = {json.dumps(ops, sort_keys=True, default=str) for ops in lists.values()}
    if len(distinct) > 1:
        refined = sorted(c for c, ops in lists.items() if ops)
        plain = sorted(c for c, ops in lists.items() if not ops)
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "replay_mixed", "retry": _REPLAY_RETRY,
                    "message": f"{', '.join(refined)} carry refinements that a "
                               f"re-generate replays"
                               + (f" and {', '.join(plain)} carry none" if plain else
                                  ", and the lists differ")
                               + "; generate them one at a time, or set "
                               "replay_refinements false to drop the ops"},
        )
    return next(iter(lists.values()), None) or None


def _replay_refinements(ctx, result, refinements, *, replay_stored: bool = False):
    """A regenerated series replays the refinements it is given (an empty list
    is a choice too); without a list, and unless the caller opted out, those its targets' applied series record
    (`_stored_refinements`). A stored list's refusal names the op and its
    index and offers the retry without them."""
    stored = False
    if refinements is None and replay_stored:
        refinements, stored = _stored_refinements(ctx, result), True
    if not refinements:
        return result
    from types import SimpleNamespace

    try:
        frame, meta, _stats, warnings = _refine_frame(ctx, result.series, dict(result.meta),
                                                      refinements, replaying=True)
    except ProfileGenError as exc:
        detail = {**_apply_refusal_kind(exc),
                  "message": public_message(exc, route="refine_replay")}
        if stored and isinstance(exc, RefineError):
            # A refusal of the whole list (an hourly-only op on a coarser
            # series) names no op; the retry still drops them.
            detail.update({"error_kind": "replay_refused", "op": getattr(exc, "op", None),
                           "index": getattr(exc, "index", None), "retry": _REPLAY_RETRY})
        raise HTTPException(status_code=422, detail=detail) from exc
    meta["replay_warnings"] = warnings
    meta["replay"] = {"from": "stored" if stored else "request", "ops": len(refinements)}
    return SimpleNamespace(series=frame, meta=meta)


def _take_replay(record: dict) -> dict:
    """What a generate says of its replay: the ops it replayed, kept out of
    the pending record so an apply does not store it."""
    replay = record.pop("replay", None)
    return {"replay": replay} if replay else {}


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


def _with_connection_share(ctx, stats: dict[str, dict]) -> None:
    """The peak's share of the grid connection beside each peak_mw (omitted
    with no connection link)."""
    for st in stats.values():
        st.update(connection_share(ctx, st["peak_mw"]))


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

    result = _replay_refinements(ctx, result, body.refinements,
                                 replay_stored=body.replay_refinements)
    job_id = str(uuid.uuid4())
    record = dict(result.meta)
    replay = _take_replay(record)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = list(result.series.columns)
    write_pending(str(proj.directory), job_id, result.series, record)

    stats = _load_stats(result.series, float(record.get("scale_mw") or 1.0),
                        int(record["weather_year"]))
    _with_connection_share(ctx, stats)
    warnings = _preview_load_energy(ctx, result.series, record, stats)
    dry, weeks = _dry_run(ctx, result.series, record)
    replayed = record.get("replay_warnings") or []
    return {**record, **replay, "stats": stats,
            "warnings": sorted({*warnings, *dry, *replayed}), "weeks": weeks}


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
    _with_connection_share(ctx, stats)
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

    result = _replay_refinements(ctx, result, body.refinements,
                                 replay_stored=body.replay_refinements)
    job_id = str(uuid.uuid4())
    record = dict(result.meta)
    replay = _take_replay(record)
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
    warnings += [w for w in record.get("replay_warnings") or [] if w not in warnings]
    return {**record, **replay, "stats": stats, "warnings": warnings, "weeks": weeks}


_SERIES_TYPES = {"inflow": "storage_units", "cop": "links"}


#: The component tables a series may target (`series` family targets).
_SERIES_COMPONENT_TABLES = ("generators", "links", "storage_units")


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
    if ctype not in _SERIES_COMPONENT_TABLES:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "invalid_series_params",
                    "message": f"a series goes on {', '.join(_SERIES_COMPONENT_TABLES)}, "
                               f"not {ctype!r}"},
        )
    table = getattr(ctx.network, ctype)
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
        from services.profile_gen.apply import commercial_timezone

        tz = commercial_timezone(ctx)
        if tz:
            params["timezone"] = tz
    # An air-source COP draws the project's own ambient (its data-centre
    # load spec) unless the ambient is stated; with neither it is refused.
    from services.dc_load_profile import AMBIENT_FIELDS, read_ambient

    ambient_from_spec = (
        body.kind == "cop" and params.get("source", "air") == "air"
        and not any(k in params for k in AMBIENT_FIELDS)
        and read_ambient(proj.directory))
    if ambient_from_spec:
        params.update(ambient_from_spec)
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

    result = _replay_refinements(ctx, result, body.refinements,
                                 replay_stored=body.replay_refinements)
    job_id = str(uuid.uuid4())
    record = dict(result.meta)
    replay = _take_replay(record)
    if ambient_from_spec:
        record["ambient_from"] = "dc_load_profile"
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
    warnings += [w for w in record.get("replay_warnings") or [] if w not in warnings]
    return {**record, **replay, "stats": stats, "warnings": warnings, "weeks": weeks}


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


@project_router.post("/{name}/profiles/series/upload")
async def upload_series(
    file: UploadFile = File(...),
    component_type: str = Form(...),
    attribute: str = Form(...),
    component: str = Form(...),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """A measured file for one field (the editors' "Measured file" tab) as a
    pending artifact on the snapshot clock, like `from-current`: previewed,
    then applied through `/profiles/apply` with the same old → new receipt.
    A file that does not fit (its column, the model's hours, the field's
    units) is a `series_upload` refusal saying what to change."""
    from fastapi.concurrency import run_in_threadpool

    from services.profile_gen.current import series_from_upload
    from services.upload_guard import read_capped
    from services.user_timeseries import _parse_upload

    _check_lock(proj, db, user)
    ctx = _foreground_ctx(proj, "upload")
    blob = await read_capped(file)
    frame = await run_in_threadpool(_parse_upload, blob, file.filename or "upload.csv")
    try:
        series, meta = series_from_upload(ctx, component_type, attribute, component, frame)
    except ProfileApplyError as exc:
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc),
                    "message": public_message(exc, route="series_upload")},
        ) from exc
    return _pending_series_response(ctx, proj, component_type, component, series, meta)


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
    return _pending_series_response(ctx, proj, body.component_type, body.component,
                                    series, meta)


def _pending_series_response(ctx, proj, component_type: str, component: str,
                             series: pd.Series, meta: dict) -> dict:
    """Write a snapshot-clock series as a pending artifact and answer with
    its stats, the apply's dry-run warnings and the preview week."""
    frame = series.to_frame(component)
    job_id = str(uuid.uuid4())
    record = dict(meta)
    record["job_id"] = job_id
    record["project_id"] = proj.name
    record["points"] = [component]
    write_pending(str(proj.directory), job_id, frame, record)
    v = series.dropna()
    stats = {component: {"min": float(v.min()), "mean": float(v.mean()),
                         "max": float(v.max())}}
    if meta.get("unit") == "MW":
        hours = 24 * (366 if pd.Timestamp(year=int(meta["weather_year"]), month=1,
                                          day=1).is_leap_year else 365)
        stats[component].update(peak_mw=float(v.max()),
                                annual_mwh=float(v.mean()) * hours)
        stats[component].update(connection_share(ctx, stats[component]["peak_mw"]))
        if component_type == "loads":
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
            if key in ctx.user_ts and is_measured(ctx.user_ts_meta.get(key)):
                results.append({"job_id": job_id, "component": component, "applied": False,
                                "error_kind": "measured_series_kept",
                                "message": f"{component!r} carries a measured upload "
                                           "now; a default never replaces it"})
                continue
            with pending_io_lock(), xr.open_dataset(nc) as ds:
                frame = ds.to_dataframe()
            frame.index = pd.DatetimeIndex(frame.index)
            summary = assign_profile(ctx, component, frame[component],
                                     {**meta, "point_id": component},
                                     component_type=component_type, attribute=attribute)
        except ProfileApplyError as exc:
            results.append({"job_id": job_id, "component": component, "applied": False,
                            **_apply_refusal_kind(exc),
                            "message": public_message(exc, route="defaults_apply",
                                                      job_id=job_id)})
            continue
        results.append({"job_id": job_id, "component": component, "applied": True,
                        "warnings": summary.get("warnings", []),
                        "peak": summary.get("peak"), "before": summary.get("before")})
    return {"results": results,
            "applied": sum(r["applied"] for r in results),
            "undo": "one undo reverts every series applied here"}


def _pending_frame(nc: Path, meta: dict) -> pd.DataFrame:
    with pending_io_lock(), xr.open_dataset(nc) as ds:
        frame = ds.to_dataframe()
    frame.index = pd.DatetimeIndex(frame.index)
    return frame[[c for c in meta.get("points", frame.columns) if c in frame.columns]]


def _undo_frame(ctx, proj: AuthorizedProject, meta: dict, k: int):
    """Undo op ``k`` (TSG 05): replay the later recorded ops but ``k`` on the
    base (`refine.undo_base`, read from this project's pending store only).
    Returns (base job id, frame, meta, stats, warnings)."""
    from services.profile_gen import refine

    def load(job_id: str):
        try:
            nc, m = _pending_artifact(proj, job_id)
        except HTTPException:  # malformed, another project's, or pruned
            return None
        return _pending_frame(nc, m), m

    base_id, base, base_meta = refine.undo_base(meta, k, load)
    carried = list(base_meta.get("refinements") or [])
    ops, numbers = refine.undo_plan(list(meta["refinements"]), k, carried)
    if not ops:  # undoing the last op: the base, as it is
        return base_id, base, dict(base_meta), {}, []
    new, new_meta, stats, warnings = _refine_frame(ctx, base, base_meta, ops, replaying=True,
                                                   numbers=numbers, after=carried)
    return base_id, new, new_meta, stats, warnings


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
    frame = _pending_frame(nc, meta)
    based_on = body.job_id
    try:
        if bool(body.ops) == (body.undo_index is not None):
            raise InvalidRefineOpError("send either a non-empty ops list or undo_index")
        if body.undo_index is None:
            new, new_meta, stats, warnings = _refine_frame(ctx, frame, meta, body.ops,
                                                           replaying=False)
        else:
            based_on, new, new_meta, stats, warnings = _undo_frame(ctx, proj, meta,
                                                                   body.undo_index)
    except ProfileGenError as exc:
        from services.profile_gen.refine import _echo

        named = ({"op": _echo(exc.op, 20), "index": exc.index}
                 if isinstance(exc, RefineReplayError) else {})
        raise HTTPException(
            status_code=422,
            detail={**_apply_refusal_kind(exc), **named,
                    "message": public_message(exc, route="refine",
                                              job_id=body.job_id)},
        ) from exc
    job_id = str(uuid.uuid4())
    record = {k: v for k, v in new_meta.items() if k not in ("replay_warnings", "undone")}
    if body.undo_index is not None:
        record["undone"] = {"index": body.undo_index,
                            "op": meta["refinements"][body.undo_index].get("op")}
    record["job_id"] = job_id
    record["based_on"] = based_on
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


def _read_point_series(nc, job_id: str, point_id: str, component: str) -> pd.Series:
    """One point's weather-year series from a pending artifact, named after
    the component. A missing column is 422 `point_not_in_profile`, an
    unreadable artifact a 422, never a 500."""
    try:
        with pending_io_lock(), xr.open_dataset(nc) as ds:
            return _series_of(ds, point_id, component)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — a corrupt artifact is a 422, not a 500
        raise HTTPException(
            status_code=422,
            detail=(f"pending profile for job {job_id!r} is unreadable: "
                    f"{public_message(exc, route='apply', job_id=job_id)}"),
        ) from exc


def _series_of(ds, point_id: str, component: str) -> pd.Series:
    """`_read_point_series` on an artifact already open (the batch opens it once)."""
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
    return pd.Series(series.to_numpy(), index=pd.DatetimeIndex(series.index),
                     name=component, dtype=float)


def _moved_bus_warning(ctx, meta: dict, point_id: str, component: str,
                       component_type: str | None) -> str | None:
    """A generator's bus that moved (or lost its coordinates) since the point
    was generated, as the warning apply returns; None when it still fits."""
    warning = None
    coords = (meta.get("point_coords") or {}).get(point_id)
    gen = ctx.network.generators
    if (coords is not None and component in gen.index
            and component_type in (None, "generators")):
        bus = str(gen.at[component, "bus"])
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
    return warning


class WeatherChoice(BaseModel):
    source: Literal["synthetic", "atlite"]
    year: int


def _weather_answer(proj: AuthorizedProject) -> dict[str, Any]:
    from services.profile_gen.apply import commercial_timezone, resolve_time_basis

    ctx = _foreground_ctx(proj, "read the weather choice")
    _mapping, recorded, _warnings = resolve_time_basis(None, commercial_timezone(ctx))
    from services.profile_gen.apply import _hourly_window

    snapshots = ctx.network.snapshots
    stamps = snapshots.get_level_values(-1)
    if isinstance(snapshots, pd.MultiIndex) and len(snapshots):
        stamps = stamps[snapshots.get_level_values(0) == snapshots.get_level_values(0)[0]]
    try:
        window = _hourly_window(stamps, what="the model's snapshots")
    except ProfileGenError as exc:
        window = {"unsupported": public_message(exc, route="weather")}
    return {**weather_prefs.resolved(proj.directory, snapshots), "window": window,
            "time_basis": {"value": recorded, "assumed": "assumed" in recorded}}


@project_router.get("/{name}/profiles/weather")
def get_weather_choice(proj: AuthorizedProject = ProjectAccessDep):
    """The weather bar's source and year: the saved choice, else the snapshots'
    year (`year_source` says which), with the time basis it is read in
    (read-only here; the project's site connection holds it)."""
    return _weather_answer(proj)


@project_router.put("/{name}/profiles/weather")
def put_weather_choice(
    body: WeatherChoice,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Save the source and year every generate without its own takes."""
    _check_lock(proj, db, user)
    try:
        weather_prefs.save_choice(proj.directory, body.source, body.year)
    except weather_prefs.WeatherChoiceError as exc:
        raise HTTPException(status_code=422, detail={"error_kind": "weather_choice",
                                                     "message": str(exc)}) from exc
    return _weather_answer(proj)


@project_router.delete("/{name}/profiles/weather")
def clear_weather_choice(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Forget the saved choice: back to the snapshots' year."""
    _check_lock(proj, db, user)
    weather_prefs.clear_choice(proj.directory)
    return _weather_answer(proj)


@project_router.get("/{name}/profiles/compare")
def compare_profiles(b: str, a: str | None = None, component: str | None = None,
                     proj: AuthorizedProject = ProjectAccessDep):
    """Read before/after statistics on the model clock without changing it."""
    from services.profile_gen import compare

    ctx = _foreground_ctx(proj, "compare profiles")
    try:
        left, right = compare.resolve_sides(ctx, proj.directory, a, b, component)
        return compare.compare(left, right, ctx.network.snapshot_weightings.generators)
    except compare.InvalidCompareSideError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "invalid_compare_side", "message": public_message(exc, route="compare"),
        }) from exc
    except compare.NotAShapedProfileError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "not_a_shaped_profile", "message": public_message(exc, route="compare"),
        }) from exc
    except ProfileGenError as exc:
        raise HTTPException(status_code=422, detail={
            **_apply_refusal_kind(exc), "message": public_message(exc, route="compare"),
        }) from exc


@project_router.get("/{name}/profiles/readiness")
def profile_readiness(proj: AuthorizedProject = ProjectAccessDep):
    """One row per wind/solar generator: its coordinate state, the profile it
    carries now and whether a generated one is stale (RP-02, read-only)."""
    from services.profile_gen.readiness import readiness_rows

    return {"generators": readiness_rows(_foreground_ctx(proj, "read readiness"))}


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
    series = _read_point_series(nc, body.job_id, point_id, body.component)

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

    warning = _moved_bus_warning(ctx, meta, point_id, body.component,
                                 body.component_type)

    # Undo snapshot BEFORE the write — the middleware only covers
    # /api/network/ and /api/io/, and apply is neither.
    from services.network_undo import push_undo_snapshot

    push_undo_snapshot()

    try:
        component_type, attribute = default_target(
            meta, body.component_type, body.attribute)
        # The point applied is recorded, so readiness can tell which of the
        # job's point_coords the series came from (RP-02).
        summary = assign_profile(ctx, body.component, series,
                                 {**meta, "point_id": point_id},
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


@project_router.post("/{name}/profiles/apply-batch")
def apply_profile_batch(
    body: BatchApplyRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Write several points of one pending job under ONE undo step (RP-09).

    Every member is validated first through `prepare_profile` (the pure half
    of `assign_profile`: carrier guard, alignment, time basis); like a single
    apply, an explicit batch replaces what a component carries (the receipt
    shows `before`, one Undo restores it); any refusal is a 422 `batch_refused`
    naming every bad member, and nothing is written. Only then is the undo
    snapshot pushed and each member written. A write that fails midway is a
    409 `batch_partially_applied` (applied / not_applied); one Undo reverts
    the batch. No automatic undo: it re-imports the whole network."""
    from services.network_undo import push_undo_snapshot
    from services.profile_gen.apply import prepare_profile
    from services.profile_gen.batch import batch_members

    _check_lock(proj, db, user)
    nc, meta = _pending_artifact(proj, body.job_id)
    ctx = _foreground_ctx(proj, "apply")

    with pending_io_lock(), xr.open_dataset(nc) as ds:
        columns = [str(c) for c in meta.get("points") or []]
        chosen, skipped = batch_members(
            columns, ctx.network.generators,
            [(m.component, m.point_id) for m in body.members], body.all_from_job)
        members = [BatchMember(component=c, point_id=pid) for c, pid in chosen]
        if not members:
            raise HTTPException(status_code=422, detail={
                "error_kind": "no_members",
                "message": "nothing to apply: name `members` (component, "
                           "optional point_id) or use all_from_job with points "
                           "named after wind/solar generators"
                           + (f"; the job's points {columns} are none" if body.all_from_job else ""),
                "skipped": skipped})
        refused: list[dict[str, Any]] = []
        seen: set[str] = set()
        prepared: list[tuple[str, pd.Series, str, str, dict]] = []
        for m in members:
            if m.component in seen:
                refused.append({"component": m.component, "error_kind": "duplicate_member",
                                "message": f"{m.component!r} is listed twice"})
                continue
            seen.add(m.component)
            point_id = m.point_id or m.component
            try:
                series = _series_of(ds, point_id, m.component)
                member_meta = {**meta, "point_id": point_id}
                component_type, attribute = default_target(member_meta, None, None)
                prepare_profile(ctx, m.component, series, member_meta,
                                component_type=component_type, attribute=attribute,
                                time_basis=body.time_basis)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                refused.append({"component": m.component, **detail})
                continue
            except ProfileApplyError as exc:
                refused.append({"component": m.component, **_apply_refusal_kind(exc),
                                "message": public_message(exc, route="apply", job_id=body.job_id)})
                continue
            prepared.append((m.component, series, component_type, attribute, member_meta))
    if refused:
        raise HTTPException(status_code=422, detail={
            "error_kind": "batch_refused",
            "message": f"{len(refused)} of {len(members)} members cannot be applied; "
                       "nothing was written",
            "members": refused})

    push_undo_snapshot()
    applied: list[dict[str, Any]] = []
    for i, (component, series, component_type, attribute, member_meta) in enumerate(prepared):
        try:
            summary = assign_profile(ctx, component, series, member_meta,
                                     component_type=component_type, attribute=attribute,
                                     time_basis=body.time_basis)
        except Exception as exc:  # noqa: BLE001 — typed below, the raw text never leaves
            kind = (_apply_refusal_kind(exc)["error_kind"]
                    if isinstance(exc, ProfileApplyError) else "write_failed")
            message = public_message(exc, route="apply", job_id=body.job_id)
            raise HTTPException(status_code=409, detail={
                "error_kind": "batch_partially_applied",
                "message": f"{len(applied)} of {len(prepared)} written before "
                           f"{component!r} failed ({message}); one Undo reverts "
                           "the whole batch",
                "applied": [a["component"] for a in applied],
                "not_applied": [{"component": c, "error_kind": kind if c == component
                                 else "not_attempted"}
                                for c, *_ in prepared[i:]],
            }) from exc
        warning = _moved_bus_warning(ctx, meta, member_meta["point_id"], component,
                                     component_type)
        if warning:
            summary["warning"] = warning
        applied.append(summary)

    from routers.simulation import _state_update

    _state_update(status="idle")
    return {"applied": applied, "count": len(applied), "skipped": skipped}


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
