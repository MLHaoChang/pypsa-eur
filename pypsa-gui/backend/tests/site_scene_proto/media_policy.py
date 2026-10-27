"""
D05: where original photos/layout documents live, who may resolve them, and
what every copy operation does with them (contracts.md §3).

Store: `<app data>/private_media/<org uuid>/<project uuid>/<media uuid>`,
outside every projects root, so none of the recursive project copies
(`_BUNDLE_DIRS`, `_HISTORY_DIRS`, snapshots, Save-As, bundle export) can reach
it. The identities are the native `Organization.id` and `Project.id` UUIDs
(db/models.py), resolved from the registry/ACL, never from a display name or an
uploaded path. A flat project (no registry row, so no Project UUID) has no
owner identity and is refused; there is no shared fallback directory.

A scene stores only an opaque source record: {source_id, kind, state, reason}.
No filename, path, URL or EXIF ever enters scene state.

Upload stays disabled in SC-00 N: no route is mounted. SC-06 enables it only
after these limits are enforced at runtime.
"""
from __future__ import annotations

import pathlib
import uuid
from typing import Any

from tests.site_scene_proto.contract import SceneError

PRIVATE_MEDIA_DIR = "private_media"
RETENTION_DAYS = 90
MAX_MEDIA_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_PROJECT_MEDIA_FILES = 200
MAX_PROJECT_MEDIA_BYTES = 1024 * 1024 * 1024
KINDS = ("photo", "layout")


def _uuid(value: Any, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise SceneError("invalid_input", f"{field} must be a UUID", {"field": field}) from None


def media_path(app_data: pathlib.Path, projects_roots: list[pathlib.Path],
               org_id: Any, project_id: Any, media_id: Any) -> pathlib.Path:
    """The private path of one original. Refuses a missing owner and any path under a projects root."""
    if org_id is None or project_id is None:
        raise SceneError("forbidden", "this project has no owner identity, so it cannot hold private media",
                         {"reason": "no_owner_identity"})
    path = (app_data / PRIVATE_MEDIA_DIR / _uuid(org_id, "org_id") / _uuid(project_id, "project_id")
            / _uuid(media_id, "media_id")).resolve()
    for root in projects_roots:
        if path.is_relative_to(root.resolve()):
            raise SceneError("integrity_error", "the private media store may not sit under a projects root")
    return path


def check_upload(size_bytes: int, pixels: int | None, project_files: int, project_bytes: int) -> None:
    """The quota gate an SC-06 upload must pass; both sides of each limit are tested."""
    for name, limit, actual in (("media_bytes", MAX_MEDIA_BYTES, size_bytes),
                                ("image_pixels", MAX_IMAGE_PIXELS, pixels or 0),
                                ("project_media_files", MAX_PROJECT_MEDIA_FILES, project_files + 1),
                                ("project_media_bytes", MAX_PROJECT_MEDIA_BYTES, project_bytes + size_bytes)):
        if actual > limit:
            raise SceneError("resource_limit", f"{name} {actual} exceeds the limit {limit}",
                             {"limit": name, "max": limit, "actual": actual})


def source_record(media_id: Any, kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise SceneError("invalid_input", f"kind must be one of {KINDS}", {"field": "kind"})
    return {"source_id": _uuid(media_id, "media_id"), "kind": kind, "state": "available", "reason": None}


def resolve(record: dict[str, Any], *, owner_project_id: Any, caller_project_id: Any,
            caller_can_read: bool, age_days: float, deleted: bool) -> dict[str, Any]:
    """Reauthorize a handle on every read: foreign project, revoked access, deletion and expiry all refuse."""
    if not caller_can_read or _uuid(owner_project_id, "owner") != _uuid(caller_project_id, "caller"):
        raise SceneError("not_found", "no such source")
    if deleted:
        raise SceneError("source_unavailable", "Original unavailable: it was deleted", {"reason": "deleted"})
    if age_days > RETENTION_DAYS:
        raise SceneError("source_unavailable", f"Original unavailable: kept for {RETENTION_DAYS} days",
                         {"reason": "retention_expired"})
    return record


def for_copy(sources: list[dict[str, Any]], operation: str) -> list[dict[str, Any]]:
    """
    Source records in the destination of a copy. Same-project snapshot/restore
    keeps the handle (reauthorized on read); scenario, Save-As, bundle export
    and import keep the provenance but mark the original unavailable.
    """
    if operation in ("snapshot", "restore"):
        return [dict(s) for s in sources]
    if operation in ("scenario", "save_as", "bundle_export", "bundle_import"):
        return [{**s, "state": "unavailable", "reason": "original_not_copied"} for s in sources]
    raise SceneError("invalid_input", f"unknown copy operation {operation!r}")
