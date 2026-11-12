"""A project's template marker, ``eh_template.json`` (ui-modern issue 07,
owner decision UM07-KIND-1).

A project made from an Energy Hub template carries the template's metadata in
its folder; a project without it is a plain network. A sensitivity case made
by ``run_sensitivity_sweep`` has no marker of its own: it takes its base's,
read-side. :func:`resolve` follows the case's ``sensitivity_case.json`` to the
base project's folder (and on, for a case of a case); the sweep copies
nothing, so existing cases need no migration.

Every reader of the marker goes through this module;
``tests/test_template_marker.py`` pins that no module names the file
directly. A write goes to :func:`own_path`, so an edit on a case writes the
case's own copy and never the base's; from then on the case reads its copy
and a later change to the base no longer reaches it.

Outcomes (:class:`Marker`):

* ``own``: the project's own file.
* ``base``: a case's base's file (the nearest one up a chain of cases). The
  case row must be a direct child of the base row and in the same
  organisation (GK B U1), so a case never surfaces a base its reader could
  not open.
* ``none``: a plain network; also a case whose base row is gone, moved out
  from under it or in another organisation, or whose base has no marker, or
  whose chain loops: the case reads as a plain network, as every case did
  before this module.
* ``unreadable``: a case record (``sensitivity_case.json``) that exists but
  cannot be read. Each reader treats it as it treats a marker of its own
  that cannot be parsed (GK B U2): :func:`read_marker` gives None,
  :func:`present` gives True. A base marker that cannot be parsed is ``base``
  and parses to None, likewise.
"""

from __future__ import annotations

import json
import logging
import pathlib
import uuid
from dataclasses import dataclass

from services import project_registry

_log = logging.getLogger(__name__)

MARKER_FILE = "eh_template.json"
#: A case's own record (`workflow_tools.SENSITIVITY_CASE_FILE`).
CASE_FILE = "sensitivity_case.json"
#: How many bases a chain of cases may pass through.
MAX_DEPTH = 8

OWN, BASE, NONE, UNREADABLE = "own", "base", "none", "unreadable"


@dataclass(frozen=True)
class Marker:
    state: str
    #: The file to read: set for ``own`` and ``base`` only.
    path: pathlib.Path | None = None


class _Unreadable(Exception):
    pass


def own_path(project_dir) -> pathlib.Path:
    """Where the project's own marker is, and where a write to it goes."""
    return pathlib.Path(project_dir) / MARKER_FILE


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _same_dir(a: pathlib.Path, b: pathlib.Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _base_dir(here: pathlib.Path) -> pathlib.Path | None:
    """The folder of the base ``here``'s case record names; None when
    ``here`` is not a case or its base cannot be reached (see the module
    docstring). Raises _Unreadable for a record that cannot be read."""
    record_path = here / CASE_FILE
    if not record_path.is_file():
        return None
    record = _read_json(record_path)
    baseline_id = record.get("baseline_id") if isinstance(record, dict) else None
    try:
        key = uuid.UUID(baseline_id) if isinstance(baseline_id, str) else None
    except ValueError:
        key = None
    if key is None:
        raise _Unreadable(record_path)
    from db import session as _db
    from db.models import Project

    try:
        with _db.SessionLocal() as db:
            base = db.get(Project, key)
            if base is None:
                return None
            base_dir = pathlib.Path(project_registry.project_dir(base))
            # U1: the case's own row is a direct child of the base, in its
            # organisation; anything else is no base.
            for child in project_registry.direct_children(db, base):
                if child.org_id == base.org_id and _same_dir(
                    pathlib.Path(project_registry.project_dir(child)), here
                ):
                    return base_dir
            return None
    except Exception as exc:  # noqa: BLE001 - a base that cannot be looked up
        _log.warning("template marker: base of %s not read: %s", here, exc)
        raise _Unreadable(record_path) from None


def resolve(project_dir) -> Marker:
    """Which marker ``project_dir`` reads (see the module docstring)."""
    here = pathlib.Path(project_dir)
    seen: list[pathlib.Path] = []
    for depth in range(MAX_DEPTH + 1):
        path = own_path(here)
        if path.is_file():
            return Marker(OWN if depth == 0 else BASE, path)
        seen.append(here)
        try:
            base = _base_dir(here)
        except _Unreadable as exc:
            _log.warning("template marker: case record %s cannot be read", exc)
            return Marker(UNREADABLE)
        if base is None or any(_same_dir(base, s) for s in seen):
            return Marker(NONE)
        here = base
    return Marker(NONE)


def source_path(project_dir) -> pathlib.Path | None:
    """The marker file the project reads: its own, else its base's when it is
    a sensitivity case; None otherwise."""
    return resolve(project_dir).path


def present(project_dir) -> bool:
    """Whether the project has a marker, as a reader that branches on the
    file being there sees it: a case record that cannot be read counts, as
    an own marker that cannot be parsed does."""
    return resolve(project_dir).state != NONE


def read_marker(project_dir) -> dict | None:
    """The project's template metadata (its base's for a case), or None."""
    path = source_path(project_dir)
    raw = _read_json(path) if path is not None else None
    return raw if isinstance(raw, dict) else None
