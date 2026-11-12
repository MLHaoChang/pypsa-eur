"""A project's template marker, ``eh_template.json`` (ui-modern issue 07,
owner decision UM07-KIND-1).

A project made from an Energy Hub template carries the template's metadata in
its folder; a project without it is a plain network. A sensitivity case made
by ``run_sensitivity_sweep`` has no marker of its own: it takes its base's,
read-side. :func:`source_path` follows the case's ``sensitivity_case.json`` to
the base project's folder (and on, for a case of a case); the sweep copies
nothing, so existing cases need no migration.

Every reader of the marker goes through :func:`read_marker` or
:func:`source_path`; ``tests/test_template_marker.py`` pins that no module
names the file directly. A write goes to :func:`own_path`, so an edit on a
case writes the case's own copy and never the base's.

Fallback: a case whose base row is gone, whose base has no marker, whose
record cannot be read, or whose chain loops reads as a plain network (no
marker), as every case did before this module.
"""
from __future__ import annotations

import json
import logging
import pathlib
import uuid

from services import project_registry

_log = logging.getLogger(__name__)

MARKER_FILE = "eh_template.json"
#: A case's own record (`workflow_tools.SENSITIVITY_CASE_FILE`).
CASE_FILE = "sensitivity_case.json"
#: How many bases a chain of cases may pass through.
MAX_DEPTH = 8


def own_path(project_dir) -> pathlib.Path:
    """Where the project's own marker is, and where a write to it goes."""
    return pathlib.Path(project_dir) / MARKER_FILE


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _base_dir(project_dir: pathlib.Path) -> pathlib.Path | None:
    """The folder of the base a case's record names, or None (not a case, an
    unreadable record, a base row that is gone)."""
    record = _read_json(project_dir / CASE_FILE)
    baseline_id = record.get("baseline_id") if isinstance(record, dict) else None
    if not isinstance(baseline_id, str):
        return None
    try:
        key = uuid.UUID(baseline_id)
    except ValueError:
        return None
    from db import session as _db
    from db.models import Project

    try:
        with _db.SessionLocal() as db:
            row = db.get(Project, key)
            return pathlib.Path(project_registry.project_dir(row)) if row is not None else None
    except Exception as exc:  # noqa: BLE001 - an unreadable base is no base
        _log.info("template marker: base %s not read: %s", baseline_id, exc)
        return None


def source_path(project_dir) -> pathlib.Path | None:
    """The marker file the project reads: its own, else its base's when it is
    a sensitivity case; None for a plain network."""
    here = pathlib.Path(project_dir)
    seen: set[pathlib.Path] = set()
    for _ in range(MAX_DEPTH + 1):
        path = own_path(here)
        if path.is_file():
            return path
        seen.add(here.resolve())
        base = _base_dir(here)
        if base is None or base.resolve() in seen:
            return None
        here = base
    return None


def read_marker(project_dir) -> dict | None:
    """The project's template metadata (its base's for a case), or None."""
    path = source_path(project_dir)
    raw = _read_json(path) if path is not None else None
    return raw if isinstance(raw, dict) else None
