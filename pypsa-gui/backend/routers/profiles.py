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

import pandas as pd
import xarray as xr
from fastapi import APIRouter, Depends, HTTPException
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
    ProfileApplyError,
    assign_profile,
)
from services.profile_gen.cutout_cache import CdsCredentialError, cds_preflight
from services.profile_gen.engine import ProfileGenError
from services.profile_gen.job import KEEP_PENDING, write_pending

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


# ---------------------------------------------------------------------------
# Global: presets + estimate
# ---------------------------------------------------------------------------


@router.get("/presets")
def list_profile_presets(technology: str | None = None):
    """The preset catalogue (optionally one technology's)."""
    try:
        found = presets.list_presets(technology)
    except ProfileGenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
            detail={"error_kind": _error_kind(exc), "message": str(exc)},
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
            detail={"error_kind": "cds_credential", "message": str(exc)},
        ) from exc
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": _error_kind(exc), "message": str(exc)},
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
    """Write one pending profile onto a generator's `p_max_pu`.

    Undo FIRST: this prefix is outside the undo middleware's coverage, so the
    snapshot is pushed here, before any write. The component is re-resolved at
    apply time — renamed or deleted since generation is a typed refusal; a bus
    that moved since generation comes back as a `warning` on the 200."""
    _check_lock(proj, db, user)

    nc, meta = _pending_artifact(proj, body.job_id)

    point_id = body.point_id or body.component
    try:
        with xr.open_dataset(nc) as ds:
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
            detail=f"pending profile for job {body.job_id!r} is unreadable: {exc}",
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
    if coords is not None and body.component in gen.index:
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
        summary = assign_profile(ctx, body.component, series, meta,
                                 time_basis=body.time_basis)
    except ComponentNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": "component_not_found", "message": str(exc)},
        ) from exc
    except ProfileApplyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": _error_kind(exc), "message": str(exc)},
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
    with xr.open_dataset(nc) as ds:
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
                        "message": f"{point}: {exc}",
                        "point": point,
                        "reachable_max": exc.reachable_max},
            ) from exc
        except ProfileGenError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error_kind": _error_kind(exc), "message": str(exc)},
            ) from exc

    new_id = str(uuid.uuid4())
    record = {
        **parent,
        "job_id": new_id,
        "parent_job": body.job_id,
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
