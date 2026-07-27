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
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import Project
from services.safe_names import unique_dir_name
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
