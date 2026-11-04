"""Immutable queued solves (study harness issue 05, HH-10; GK B's 3 Nov
ruling and conditions C6a-g).

An envelope-admitted `solve_queue_enqueue` captures, under the locks its
admission holds (`run_envelope.admitted_write`), the exact input the gate
checked: the network as it is in memory, its time series, the solver config
the job snapshots, the edit counter, and every project file the solve reads
or the template carries, byte for byte. The job solves that capture on a
private context and applies the result to the project only when the
project's identity still equals the captured one.

Layout: `<project>/.harness/captures/<capture id>/` holding the files and
`manifest.json` (each file's sha256, the exact input fingerprint and the
edit counter). The capture's identity is the manifest's sha256 and is kept on
the job row. The directory is written under a temporary name, the manifest
last, then renamed. Pickup verifies the manifest against the job's identity
and every file against the manifest; anything else is `capture_mismatch`.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import pathlib
import shutil
import uuid

logger = logging.getLogger(__name__)

CAPTURES = pathlib.PurePosixPath(".harness") / "captures"
MANIFEST = "manifest.json"
VERSION = 1
MISMATCH = "capture_mismatch"
ALREADY_QUEUED = "solve_already_queued"
# The boot sweep leaves a capture younger than this: the harness runner's
# lease (`harness.runner.LEASE_SECONDS`, pinned equal by a test).
SWEEP_MIN_AGE_S = 90.0

# What `_hydrate_context_from_disk` reads to build the solving context.
HYDRATE_FILES = ("network.nc", "user_ts.json", "solver_config.json", "metadata.json")
# Written by the capture from memory (the rest are copied byte for byte).
_WRITTEN = frozenset(HYDRATE_FILES)

# Set by `run_envelope.admitted_write` around an admitted queued solve: the
# enqueue route captures the input of the context it names.
_REQUEST: contextvars.ContextVar = contextvars.ContextVar("solve_capture_request", default=None)


class CaptureMismatch(RuntimeError):
    """The capture on disk is not the one the job recorded."""


def sidecars() -> tuple[str, ...]:
    """The project files a queued solve reads besides the hydrate set, plus
    the template sidecars (GK B, 3 Nov: captured byte for byte, their sha256
    in the identity). Named from the readers' own constants."""
    from services.adequacy import grid_limits, island_config
    from services.campus_electrical_service import CAMPUS_FILE, SUBDIR
    from services.flow_service import TEMPLATE_FILE
    from services.solver import island
    return (island_config.SIDECAR_NAME, island.CUTS_FILE, f"{SUBDIR}/{CAMPUS_FILE}",
            TEMPLATE_FILE, grid_limits.SIDECAR_NAME)


def solve_inputs() -> tuple[str, ...]:
    """Every file a capture holds (C6b): the hydrate set and the sidecars."""
    return HYDRATE_FILES + sidecars()


@contextlib.contextmanager
def requested(ctx):
    """Within this block, a queued solve of `ctx`'s project is captured."""
    token = _REQUEST.set({"ctx": ctx})
    try:
        yield
    finally:
        _REQUEST.reset(token)


def request():
    return _REQUEST.get()


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def root(project_dir) -> pathlib.Path:
    return pathlib.Path(project_dir) / CAPTURES


def sidecar_hashes(project_dir) -> dict:
    """sha256 of each sidecar present in `project_dir` (absent ones omitted)."""
    base = pathlib.Path(project_dir)
    return {name: _sha256(base / name) for name in sidecars() if (base / name).is_file()}


def current(ctx, project_dir) -> dict:
    """The project's identity fields, read under the caller's locks: the
    exact input fingerprint, the edit counter and the sidecars' sha256."""
    from services import run_envelope
    found = run_envelope.identity(ctx)
    return {"exact": found.get("exact"), "network_revision": found.get("network_revision"),
            "sidecars": sidecar_hashes(project_dir)}


def capture(ctx, project_dir, solver_config_json: str | None) -> dict:
    """Write the capture of `ctx` (the caller holds its locks). Returns the
    job's record: `{id, dir, identity, files}`."""
    from services.user_timeseries import _serialize_user_ts
    from services.pypsa_service import PyPSAService
    base = root(project_dir)
    base.mkdir(parents=True, exist_ok=True)
    cid = uuid.uuid4().hex
    tmp = base / f".{cid}.tmp"
    tmp.mkdir()
    try:
        with PyPSAService.get_netcdf_io_lock():
            PyPSAService.export_network_to_netcdf(ctx.network, str(tmp / "network.nc"))
        (tmp / "user_ts.json").write_text(json.dumps(_serialize_user_ts(store=ctx.user_ts)))
        config = solver_config_json
        if config is None:
            from dataclasses import asdict
            config = json.dumps(asdict(ctx.solver_state["solver_config"]))
        (tmp / "solver_config.json").write_text(config)
        identity = current(ctx, project_dir)
        (tmp / "metadata.json").write_text(json.dumps({"network_revision": identity["network_revision"]}))
        src = pathlib.Path(project_dir)
        for name in sidecars():
            if (src / name).is_file():
                (tmp / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / name, tmp / name)  # byte for byte
        files = {name: _sha256(tmp / name) for name in solve_inputs() if (tmp / name).is_file()}
        manifest = {"version": VERSION, "files": files, "exact": identity["exact"],
                    "network_revision": identity["network_revision"]}
        body = json.dumps(manifest, sort_keys=True).encode()
        (tmp / MANIFEST).write_bytes(body)  # last: a capture without it is no capture
        final = base / cid
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    record = {"id": cid, "dir": str(final), "identity": hashlib.sha256(body).hexdigest(), "files": files,
              "exact": manifest["exact"], "network_revision": manifest["network_revision"]}
    if _user_code(config):
        # GK B rider 2: the code text travels in the captured config; what it
        # reads while it runs is not captured, and the evidence says so.
        record["not_captured"] = ["user_code_reads"]
    return record


def _user_code(config_json: str) -> bool:
    try:
        return bool(str(json.loads(config_json).get("extra_functionality_code") or "").strip())
    except (ValueError, AttributeError):
        return True  # unreadable: never claim it is fully captured


def verify(record: dict) -> dict:
    """The capture's manifest, checked against the job's identity and every
    file against the manifest; CaptureMismatch otherwise (C6a)."""
    path = pathlib.Path(record.get("dir") or "")
    try:
        body = (path / MANIFEST).read_bytes()
    except OSError as exc:
        raise CaptureMismatch(f"no capture at {path.name or '?'}") from exc
    if hashlib.sha256(body).hexdigest() != record.get("identity"):
        raise CaptureMismatch("the manifest is not the one the job recorded")
    manifest = json.loads(body)
    for name, digest in manifest["files"].items():
        if not (path / name).is_file() or _sha256(path / name) != digest:
            raise CaptureMismatch(f"{name} differs from the manifest")
    return manifest


def unchanged(manifest: dict, now: dict) -> bool:
    """C6e: the full identity, never the fingerprint alone."""
    captured = {name: manifest["files"][name] for name in sidecars() if name in manifest["files"]}
    return (manifest["exact"] is not None and now["exact"] == manifest["exact"]
            and now["network_revision"] == manifest["network_revision"] and now["sidecars"] == captured)


def public(record: dict) -> dict:
    """The record as the job listing and the run evidence show it (no path)."""
    return {k: v for k, v in record.items() if k != "dir"}


def remove(record: dict | None) -> None:
    """Drop a settled job's capture (C6g); its record stays on the job."""
    if record and record.get("dir"):
        shutil.rmtree(record["dir"], ignore_errors=True)


def sweep(live: set[str], *, min_age_s: float, roots=None) -> int:
    """Boot (C6g): remove every capture no queued or running job names.
    `live` comes from the shared job table, so a job another replica holds
    keeps its capture; a directory younger than `min_age_s` (the runner's
    lease) is left too, since another replica may be writing it before its
    job row exists. Scans `<root>/<project>` and `<root>/<org>/<project>`."""
    import time
    if roots is None:
        from settings import get_settings
        settings = get_settings()
        roots = {settings.projects_root, settings.flat_projects_root}
    now, removed = time.time(), 0
    for top in {pathlib.Path(r) for r in roots if r}:
        for base in [*top.glob(f"*/{CAPTURES}"), *top.glob(f"*/*/{CAPTURES}")]:
            for child in base.iterdir():
                cid = child.name.strip(".").split(".", 1)[0]
                try:
                    young = now - child.stat().st_mtime < min_age_s
                except OSError:
                    continue
                if cid in live or young:
                    continue
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
    return removed
