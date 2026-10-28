"""
D02: the frozen public contract of the native scene subset.

Error kinds are the categories contracts.md §5 names, each pinned to one HTTP
status. They are scene-route error kinds for SC-01; none is a chat tool error
kind (`pypsa-gui/tool-error-kinds.json` is untouched until SC-07 adds tools).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# kind -> HTTP status. `forbidden`/`not_found` follow native project policy:
# a project the caller cannot see is 404, one it can see but not edit is 403.
ERROR_STATUS: dict[str, int] = {
    "not_found": 404,
    "forbidden": 403,
    "project_locked": 409,
    "invalid_input": 422,
    "invalid_geometry": 422,
    "revision_conflict": 409,
    "scene_managed_site": 409,
    "source_unavailable": 410,
    "worker_unavailable": 503,
    "resource_limit": 413,
    "proposal_expired": 410,
    "proposal_cancelled": 409,
    "integrity_error": 500,
}


@dataclass
class SceneError(Exception):
    """A refused scene operation. `to_body()` is the frozen wire shape."""

    kind: str
    message: str
    detail: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.kind not in ERROR_STATUS:
            raise ValueError(f"unclassified scene error kind {self.kind!r}")

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}"

    @property
    def status(self) -> int:
        return ERROR_STATUS[self.kind]

    def to_body(self) -> dict[str, Any]:
        # The same `{detail: {error_kind, message, ...}}` envelope existing
        # project routes raise through HTTPException(detail=...).
        return {"detail": {"error_kind": self.kind, "message": self.message, **(self.detail or {})}}


# The SC-01 route table. `chat` is each route's chat disposition: none of them
# is a chat tool before SC-07 (harness/README.md), so every entry is "none".
# `role` is the least project role that may call it; `lock` says whether the
# route checks the project edit lock (409 project_locked) before writing.
ROUTES: tuple[dict[str, Any], ...] = (
    {"method": "GET", "path": "/api/projects/{name}/site-anchor", "role": "viewer", "lock": False, "chat": "none"},
    {"method": "PUT", "path": "/api/projects/{name}/site-anchor", "role": "editor", "lock": True, "chat": "none"},
    {"method": "GET", "path": "/api/projects/{name}/sites/{site_id}/scene", "role": "viewer", "lock": False, "chat": "none"},
    {"method": "POST", "path": "/api/projects/{name}/sites/{site_id}/scene/adopt", "role": "editor", "lock": True, "chat": "none"},
    {"method": "POST", "path": "/api/projects/{name}/sites/{site_id}/scene/proposals", "role": "editor", "lock": False, "chat": "none"},
    {"method": "GET", "path": "/api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}", "role": "editor", "lock": False, "chat": "none"},
    {"method": "POST", "path": "/api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}/apply", "role": "editor", "lock": True, "chat": "none"},
    {"method": "POST", "path": "/api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}/cancel", "role": "editor", "lock": False, "chat": "none"},
)

# contracts.md §5 rows that SC-00 N does NOT freeze: they need D05 runtime proof
# (sources) or SC-04 (exports/artifacts). Named here so silence is not taken as
# a frozen contract.
DEFERRED_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST .../scene/sources", "SC-06: upload stays disabled until D05 runtime proof"),
    ("GET/DELETE .../scene/sources/{id}", "SC-06"),
    ("GET .../scene/sources/{id}/original", "SC-06"),
    ("POST .../scene/exports", "SC-04"),
    ("GET .../scene/artifacts/{id}", "SC-04"),
)


def quantity(value: float | None, unit: str, reason: str | None = None) -> dict[str, Any]:
    """
    contracts.md §6: a numeric path is `{value, available, reason, unit}`.

    A missing value is `value=None, available=False` with a named reason; a
    resolved zero stays `value=0, available=True`. Never both, never neither.
    """
    if value is None:
        if not reason:
            raise ValueError("an unavailable quantity needs a reason")
        return {"value": None, "available": False, "reason": reason, "unit": unit}
    if reason is not None:
        raise ValueError("an available quantity carries no reason")
    return {"value": value, "available": True, "reason": None, "unit": unit}
