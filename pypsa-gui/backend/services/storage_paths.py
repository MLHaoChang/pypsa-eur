"""
Where a project's files live (phase 1b, spec E1/E2).

Two changes from the original `<root>/<org_uuid>/<project_uuid>/`:

  **Readable.** The directory carries the project's own name, so
  `~/Documents/PyPSA GUI/Projects/Belgium Grid/` is something a user can find
  in Finder. `safe_names` does the portability work.

  **Relative.** The row stores `Belgium Grid`, not an absolute path, and
  `project_registry.project_dir` rejoins it with the configured root. An
  absolute path bakes one machine's home directory into the database, so a
  restored backup, a moved app-data directory, or the same checkout on the
  other platform this project targets all point at a directory that is not
  there.

The org segment is a **parameter**, not a constant — see `use_org_segment`.
"""
from __future__ import annotations

import uuid
from pathlib import Path, PurePosixPath
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import Project
from services.safe_names import fold, unique_dir_name
from settings import get_settings


def use_org_segment() -> bool:
    """
    True when project directories are nested under their org's UUID.

    Defined ONCE, here, and deliberately the negation of the predicate Task 1
    gates `/unclaimed` on. Locally the segment carries no information — one
    org, one fixed id — and `Projects/<uuid>/Belgium Grid` only moves the UUID
    up a level.

    Dropping it is not cosmetic. With projects at the top of `projects_root`,
    `legacy_migrate._scan_root(..., pre_auth_layout=True)` treats every
    non-UUID-named directory as a claimable leftover and
    `POST /unclaimed/{name}/import` `shutil.move`s it. Task 1 closing that door
    is the SOLE guard, so if these two predicates ever diverge a live project
    can be moved out from under its own row. `test_storage_layout.py` asserts
    they agree.

    (The name is `use_org_segment`, not `org_segment`, because every function
    below takes an `org_segment` keyword and a module-level function of the
    same name would read as if the parameter defaulted to it. It does not —
    the caller decides, so a test can exercise both layouts without touching
    the environment.)
    """
    return not local_mode.is_local_mode()


def storage_path_for(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    taken: Iterable[str],
    *,
    org_segment: bool,
) -> Path:
    """
    A project's storage directory, **relative to `projects_root`**.

    `project_id` is accepted and deliberately not used in the path. It stays in
    the signature because every caller has it to hand and because the
    local->web conversion recorded in the plan's Q1 needs a rebase keyed on the
    row, not on a display name that may since have been renamed.
    """
    rel = Path(str(org_id)) if org_segment else Path()
    return rel / unique_dir_name(name, taken)


def storage_value(relative: Path) -> str:
    """
    The string form to persist in ``Project.storage_path``. **Always POSIX.**

    ``str(Path("<org>") / "Belgium Grid")`` is ``<org>\\Belgium Grid`` on
    Windows, and a database written there and read on macOS resolves to a
    single directory whose name contains a backslash — while
    ``Path(row).name`` returns the whole string instead of the leaf, so
    ``taken_names`` stops detecting collisions too. Portability across exactly
    these two platforms is the reason E2 exists, so the separator cannot be
    the host's.

    Reading needs no counterpart: ``Path`` accepts ``/`` on Windows.
    """
    return PurePosixPath(*Path(relative).parts).as_posix()


def allocate_storage_path(
    db: DBSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    *,
    org_segment: bool,
    exclude: Iterable[str] = (),
) -> Path:
    """
    A free storage path for a new project, checked against the filesystem.

    `taken_names` already unions the directory listing, so the `exists()` loop
    below is a backstop rather than the mechanism — but it is the difference
    between "the allocator has a bug" and "the user's project was overwritten",
    and `_stage_and_rename` has had the same check since it was written.

    `exclude` drops names the caller knows are its own (a rename excludes the
    project's current directory, or a no-op rename suffixes itself).
    """
    root = Path(get_settings().projects_root)
    mine = {fold(entry) for entry in exclude}
    taken = {entry for entry in taken_names(db, org_id, org_segment) if fold(entry) not in mine}
    for _ in range(8):
        relative = storage_path_for(
            org_id, project_id, name, taken, org_segment=org_segment
        )
        # `exclude` names are the CALLER'S OWN directory, so of course it
        # exists — a rename to a name that sanitises back to the current
        # directory must land on it, not allocate ` (2)` and move the data for
        # nothing.
        if fold(relative.name) in mine or not (root / relative).exists():
            return relative
        taken.add(relative.name)
    raise HTTPException(
        status_code=409,
        detail=f"Could not find a free directory name for '{name}'",
    )


def taken_names(db: DBSession, org_id: uuid.UUID, org_segment: bool) -> set[str]:
    """
    Sibling DIRECTORY names — `Path(row.storage_path).name`, never `row.name`.

    Reading display names lets a project whose name sanitises onto another
    project's directory be assigned that same directory: "Study:1" lives in
    "Study_1", so a later project literally named "Study_1" would find no
    collision and both rows would resolve to one path.

    The database **union the filesystem**. Rows alone are not enough: a
    directory with no row is an orphan (`storage_reconcile.orphan_dirs`), and
    handing its name to a new project makes the first save adopt and then
    overwrite it — the save mkdirs with `exist_ok=True` and writes over its
    `network.nc`. That was impossible while paths were `<org>/<uuid>` and is
    introduced by this phase. Reserving an orphan's name costs one suffix;
    adopting it costs the user's data.

    No leading underscore: this is called from `project_registry`,
    `legacy_migrate` and the importer, not just from here.
    """
    rows = db.scalars(
        select(Project.storage_path).where(Project.org_id == org_id)
    ).all()
    taken = {Path(row).name for row in rows}

    root = Path(get_settings().projects_root)
    if org_segment:
        root = root / str(org_id)
    if root.is_dir():
        taken |= {entry.name for entry in root.iterdir() if entry.is_dir()}
    return taken
