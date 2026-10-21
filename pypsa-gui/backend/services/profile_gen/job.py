"""The solve-queue runner for `profile_gen` jobs (Task 6).

A queued profile generation carries its whole spec as JSON in
`job.solver_config_json` (resolved by the route, which has the request and
the authorized storage_dir — the dispatcher thread has neither):

    {
      "points":     [{"id": "…", "lon": 8.0, "lat": 50.0}, …],   # required
      "technology": "wind" | "solar",                            # required
      "preset":     "default",                                   # optional
      "params":     {…},                                         # optional overrides
      "source":     "synthetic" | "atlite",                      # default synthetic
      "year":       2023,                                        # default: engine's
      "resolution": "hourly" | "daily" | "weekly"                # default hourly
    }

The runner NEVER touches a network. It writes a PENDING artifact pair under
the job's authorized storage_dir:

    <storage_dir>/profiles_pending/<job_id>.nc    — the CF frame at the
                                                    spec's resolution
    <storage_dir>/profiles_pending/<job_id>.json  — the spec-D6 meta record

Applying one to a component is a separate, undoable, ACL-checked step
(`assign_profile` via the apply endpoint); a queue job is only ever a
producer of candidates.

Abort contract: the queue's stop_event is honoured at every stage boundary
and is passed into `ensure_cutout` (whose downloader raises
`CutoutDownloadAborted`). Either way the job ends `aborted` and NO artifact
is left behind — the `.nc` is staged as `.part` and renamed only on success.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

from services.profile_gen import engine, presets
from services.profile_gen.cutout_cache import (
    CutoutDownloadAborted,
    ensure_cutout,
)
from services.profile_gen.engine import (
    ProfileGenError,
    generate_profile,
)

logger = logging.getLogger(__name__)

# How many pending artifacts one project keeps. Older pairs (`.nc` + `.json`,
# by `.nc` mtime) are swept after each successful run — pending profiles are
# candidates, not an archive, and a kept-forever rule would grow unbounded on
# a shared disk.
KEEP_PENDING = 10


class ProfileJobError(ProfileGenError):
    """The job's spec or its artifact destination is malformed or unsafe."""


class ProfileGenAborted(ProfileGenError):
    """The queue's stop_event fired between stages (or inside the download,
    re-raised here as the runner's own abort marker)."""


# ---------------------------------------------------------------------------
# Artifact paths — the traversal guard
# ---------------------------------------------------------------------------


def _artifact_paths(storage_dir: str | Path, job_id: str) -> tuple[Path, Path]:
    """The pending `.nc`/`.json` pair for `job_id`, pinned INSIDE
    `<storage_dir>/profiles_pending`. The queue mints uuids, but the id still
    crosses a filesystem boundary — a `..` in it must be a typed refusal, not
    a write outside the project's authorized directory."""
    pending = (Path(storage_dir) / "profiles_pending").resolve()
    nc = (pending / f"{job_id}.nc").resolve()
    if nc.parent != pending:
        raise ProfileJobError(
            f"job id {job_id!r} escapes the pending directory — refused"
        )
    return nc, pending / f"{job_id}.json"


def _evict_pending(storage_dir: str | Path, keep: int = KEEP_PENDING) -> None:
    """Keep the newest `keep` pending artifacts; sweep the rest (both files of
    each pair). Age is the `.nc` mtime — the `.json` is written second but
    belongs to the same run."""
    pending = Path(storage_dir) / "profiles_pending"
    if not pending.exists():
        return
    ncs = sorted(pending.glob("*.nc"), key=lambda p: p.stat().st_mtime)
    for old in ncs[:-keep] if keep else ncs:
        for f in (old, old.with_suffix(".json")):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                logger.warning("profile_gen: could not evict %s", f)


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def pending_io_lock():
    """The process-wide netCDF lock (`PyPSAService.get_netcdf_io_lock`):
    netCDF4/HDF5 is not thread-safe per process, so a pending write or read
    must not run beside another one, a project save/load, an export or a
    campus read (concurrent pending writes crashed the API).

    Non-re-entrant: never call under the lock. It is a plain Lock, and every
    pending access takes it on its own, so a nested call would hang."""
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_netcdf_io_lock()


def write_pending(storage_dir: str | Path, job_id: str, frame: pd.DataFrame,
                  record: dict[str, Any]) -> tuple[Path, Path]:
    """Write one pending artifact pair and evict the oldest beyond the
    keep. Both files are staged then renamed (`.nc` first, `.json` last —
    the record IS the artifact's identity, so a crash leaves at most an
    orphan `.nc` the listing skips, never a truncated record)."""
    nc, meta_path = _artifact_paths(storage_dir, job_id)
    nc.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(nc) + ".part")
    meta_part = Path(str(meta_path) + ".part")
    try:
        with pending_io_lock():
            frame.rename_axis("time").to_xarray().to_netcdf(str(part))
        os.replace(part, nc)
        meta_part.write_text(json.dumps(record, indent=2, default=str))
        os.replace(meta_part, meta_path)
    finally:
        part.unlink(missing_ok=True)
        meta_part.unlink(missing_ok=True)
    _evict_pending(storage_dir)
    return nc, meta_path


def _parse_spec(job: Any) -> dict[str, Any]:
    raw = job.solver_config_json
    if not raw:
        raise ProfileJobError("profile_gen job carries no spec")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileJobError(f"profile_gen spec is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise ProfileJobError("profile_gen spec must be a JSON object")
    if "points" not in spec or "technology" not in spec:
        raise ProfileJobError("profile_gen spec needs 'points' and 'technology'")
    return spec


def _resolve_params(spec: dict[str, Any]) -> dict[str, Any]:
    """Preset params + user overrides; an explicit `params` dict without a
    preset starts from the technology's default preset so required keys
    (turbine / panel+orientation) are always present."""
    technology = str(spec["technology"]).strip().lower()
    preset_name = str(spec.get("preset") or "default")
    return presets.resolve_params(preset_name, technology, spec.get("params"))


# ---------------------------------------------------------------------------
# Entry point (called by the queue's `_run_profile_gen_job`)
# ---------------------------------------------------------------------------


def run_queued(job: Any, progress: Any, stop_event: Any) -> tuple[Path, Path]:
    """Run one profile_gen job to its pending artifact. Returns the
    (nc, json) pair. Raises ProfileGenAborted when the stop_event fired,
    ProfileJobError / engine errors for typed refusals."""

    def _check_stop() -> None:
        if stop_event is not None and stop_event.is_set():
            raise ProfileGenAborted("profile generation aborted")

    if not job.storage_dir:
        raise ProfileJobError(
            "profile_gen job has no authorized storage directory"
        )

    spec = _parse_spec(job)
    points = spec["points"]
    technology = spec["technology"]
    source = str(spec.get("source") or "synthetic")
    year = spec.get("year")

    # Validate the technology BEFORE resolving the preset, so an unsupported
    # carrier is the engine's typed refusal, not a confusing "no such preset
    # for nuclear" from the catalogue.
    tech = str(technology).strip().lower()
    if tech not in engine.SUPPORTED_TECHNOLOGIES:
        raise engine.UnsupportedTechnologyError(
            f"cannot generate a profile for technology {technology!r}; "
            f"supported: {', '.join(engine.SUPPORTED_TECHNOLOGIES)}"
        )
    params = _resolve_params(spec)

    _check_stop()

    if source == "atlite":
        pts = [engine._as_point(p) for p in points]
        bbox = (
            min(p.lon for p in pts),
            min(p.lat for p in pts),
            max(p.lon for p in pts),
            max(p.lat for p in pts),
        )
        weather_year = int(year) if year is not None else (
            pd.Timestamp.now(tz="UTC").year - 1
        )
        if progress:
            progress("cutout", 0, 1)
        params["cutout_path"] = str(ensure_cutout(
            bbox, weather_year,
            stop_event=stop_event,
        ))
        _check_stop()

    if progress:
        progress("generate", 0, 1)
    result = generate_profile(
        points, technology, params=params, source=source, year=year,
        resolution=str(spec.get("resolution") or "hourly"),
    )
    _check_stop()

    record = dict(result.meta)
    record["job_id"] = str(job.id)
    record["project_id"] = job.project_id
    record["points"] = list(result.series.columns)
    # Per-point coordinates, so a later apply can warn when an asset's bus
    # has moved since its profile was generated.
    record["point_coords"] = {
        p.id: [p.lon, p.lat] for p in engine._normalize_points(points)
    }
    return write_pending(job.storage_dir, str(job.id), result.series, record)
