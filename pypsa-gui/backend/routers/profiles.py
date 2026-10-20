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

import json
import uuid
from pathlib import Path
from typing import Any

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
from services.profile_gen import engine, presets
from services.profile_gen.apply import (
    ComponentNotFoundError,
    ProfileApplyError,
    assign_profile,
)
from services.profile_gen.cutout_cache import CdsCredentialError, cds_preflight
from services.profile_gen.engine import ProfileGenError
from services.profile_gen.job import KEEP_PENDING

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
    points: list[ProfilePointIn]
    technology: str
    preset: str | None = None
    params: dict[str, Any] | None = None
    year: int | None = None


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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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
            [p.model_dump() for p in body.points],
            body.technology,
            params=params,
            source="synthetic",
            year=body.year,
        )
    except ProfileGenError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_kind": _error_kind(exc), "message": str(exc)},
        ) from exc
    daily = result.series.resample("1D").mean()
    return {
        "estimate": True,
        "source": "synthetic",
        "technology": result.meta["technology"],
        "weather_year": result.meta["weather_year"],
        "timestamps": [t.isoformat() for t in daily.index],
        "series": {c: [round(float(v), 6) for v in daily[c]] for c in daily.columns},
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
    spec["points"] = [p.model_dump() for p in body.points]
    try:
        # Validate the way the runner will, minus the work: points, technology,
        # preset name (resolve_params raises UnknownPresetError), and a CDS
        # preflight for the atlite source. The runner re-resolves params from
        # the raw preset+overrides; nothing computed here is passed down.
        engine._normalize_points(spec["points"])
        _validate_technology(body.technology)
        _preset_params(body)
        if body.source == "atlite":
            cds_preflight()
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

    pending = _pending_dir(proj).resolve()
    nc = (pending / f"{body.job_id}.nc").resolve()
    meta_path = pending / f"{body.job_id}.json"
    if nc.parent != pending:
        raise HTTPException(status_code=422, detail="malformed job id")
    if not nc.exists() or not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no pending profile for job {body.job_id!r}",
        )
    meta = json.loads(meta_path.read_text())

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
            bx = float(ctx.network.buses.at[bus, "x"] or 0.0)
            by = float(ctx.network.buses.at[bus, "y"] or 0.0)
            if abs(bx - coords[0]) > 0.5 or abs(by - coords[1]) > 0.5:
                warning = (
                    f"bus '{bus}' moved since generation: profile was made for "
                    f"({coords[0]:.3f}, {coords[1]:.3f}), the bus now sits at "
                    f"({bx:.3f}, {by:.3f})"
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
