"""
Issue 06 slice (a): the `blender_scene` job and its stored artifact.

A job in the existing solve queue (`solve_queue.KIND_BLENDER_SCENE`). It runs
the sandboxed worker (`blender_worker`) on the native export GLB of the site's
pointed generation, and stores the returned `.blend` and GLB as an artifact
under the project directory:

    <project>/scene_artifacts/<job uuid>/{scene.blend, scene.glb, meta.json}

The artifact is a download only. Nothing in this slice reads it back into the
scene or the model: the reviewed return (proposal plus Apply) is slice (b).
`meta.json` records the export base (revision, generation, generation and GLB
sha256) that slice (b) must match.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.site_scene import blender_worker as bw
from services.site_scene import export
from services.site_scene.contract import SceneError

ARTIFACTS = "scene_artifacts"
FILES = ("scene.blend", "scene.glb", "meta.json")
META_CAP = 1 << 20


def spec(site_id: str, project_name: str) -> str:
    """The job's `solver_config_json`."""
    return json.dumps({"site_id": site_id, "project": project_name})


def _parse_spec(raw: str | None) -> tuple[str, str]:
    try:
        data = json.loads(raw or "")
        site_id, project = data["site_id"], data["project"]
    except (ValueError, KeyError, TypeError):
        raise bw.WorkerError(bw.FAILED, "the job's spec is malformed") from None
    if not isinstance(site_id, str) or not isinstance(project, str):
        raise bw.WorkerError(bw.FAILED, "the job's spec is malformed")
    return site_id, project


def artifact_dir(project_dir, artifact_id: str) -> Path:
    """The artifact's directory. The id must be a canonical UUID; anything else is not found."""
    try:
        canonical = str(uuid.UUID(artifact_id))
    except (ValueError, TypeError, AttributeError):
        raise SceneError("not_found", "No such scene artifact") from None
    if canonical != artifact_id:
        raise SceneError("not_found", "No such scene artifact")
    return Path(project_dir) / ARTIFACTS / canonical


def run_queued(
    job,
    stop_event,
    *,
    readiness=bw.readiness,
    caps: bw.Caps = bw.Caps(),
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one queued job. Returns the stored artifact's meta, or raises `WorkerError`/`SceneError`."""
    if not job.storage_dir:
        raise bw.WorkerError(bw.FAILED, "the job has no authorized project directory")
    site_id, project = _parse_spec(job.solver_config_json)
    ready = readiness()
    if not ready.available:
        raise bw.WorkerError(bw.UNAVAILABLE, ready.reason or "Blender is not available")
    project_dir = Path(job.storage_dir)
    snap = {**export.read_snapshot(project_dir, site_id), "project": project}
    glb, _manifest = export.build(snap)
    result = bw.run(ready, glb, stop_event, caps, env)
    if stop_event is not None and stop_event.is_set():
        # Late output: the run finished after a cancel landed. Never stored.
        raise bw.WorkerError(
            bw.CANCELLED, "cancelled after the run finished; its output was discarded"
        )
    meta = {
        "artifact_id": str(job.id),
        "site_id": site_id,
        "revision": snap["revision"],
        "generation": snap["generation"],
        "generation_sha256": snap["generation_sha256"],
        "export_glb_sha256": hashlib.sha256(glb).hexdigest(),
        "blender_version": bw.PIN_VERSION,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _store(project_dir, str(job.id), result, meta)
    if stop_event is not None and stop_event.is_set():
        # A cancel that landed while storing: roll the artifact back.
        shutil.rmtree(artifact_dir(project_dir, str(job.id)), ignore_errors=True)
        raise bw.WorkerError(
            bw.CANCELLED, "cancelled while storing; the artifact was removed"
        )
    return meta


def _store(
    project_dir, artifact_id: str, result: bw.Result, meta: dict[str, Any]
) -> None:
    """Write to a sibling temp dir, then rename, so a reader never sees half an artifact."""
    final = artifact_dir(project_dir, artifact_id)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".tmp-{artifact_id}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        (tmp / "scene.blend").write_bytes(result.blend)
        (tmp / "scene.glb").write_bytes(result.glb)
        (tmp / "meta.json").write_text(
            json.dumps(meta, sort_keys=True), encoding="utf-8"
        )
        os.replace(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _checked(project_dir, site_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
    """The artifact's dir and meta, or not found unless it is this site's."""
    d = artifact_dir(project_dir, artifact_id)
    # No symlink anywhere on the way: neither the store, nor the artifact dir,
    # nor (through O_NOFOLLOW, regular files only, size-capped) any file.
    if d.parent.is_symlink() or d.is_symlink() or not d.is_dir():
        raise SceneError("not_found", "No such scene artifact")
    try:
        meta = json.loads(bw._read_output(d, "meta.json", META_CAP))
    except (OSError, ValueError, bw.WorkerError):
        raise SceneError("not_found", "No such scene artifact") from None
    if not isinstance(meta, dict) or meta.get("site_id") != site_id:
        raise SceneError("not_found", "No such scene artifact")
    return d, meta


def read_artifact(
    project_dir, site_id: str, artifact_id: str
) -> tuple[bytes, dict[str, Any]]:
    """GET .../scene/artifacts/{id}: (zip bytes, meta). Not found unless it is this site's."""
    d, meta = _checked(project_dir, site_id, artifact_id)
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in FILES:
                cap = META_CAP if name == "meta.json" else bw.Caps().output_bytes
                z.writestr(name, bw._read_output(d, name, cap))
    except (OSError, ValueError, bw.WorkerError):
        raise SceneError("not_found", "No such scene artifact") from None
    return buf.getvalue(), meta


def read_return(
    project_dir, site_id: str, artifact_id: str, cap: int
) -> tuple[bytes, dict[str, Any]]:
    """Slice (b): the returned GLB (at most `cap` bytes) and meta. The GLB is untrusted (`glb_read`)."""
    d, meta = _checked(project_dir, site_id, artifact_id)
    try:
        return bw._read_output(d, "scene.glb", cap), meta
    except bw.WorkerError as exc:
        if exc.kind == bw.OUTPUT_LIMIT:
            raise SceneError("resource_limit", f"The returned scene is over {cap >> 20} MiB",
                             {"limit": "return_bytes", "max": cap}) from None
        raise SceneError("not_found", "No such scene artifact") from None
    except OSError:
        raise SceneError("not_found", "No such scene artifact") from None
