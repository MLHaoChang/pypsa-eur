"""
Reconcile what is on disk against what is in the database (spec E3/E4).

Three kinds of drift, each with a different remedy and none of them automatic:

  **orphan_dirs**   a directory holding a `network.nc` with no row pointing at
                    it. Left behind by a failed import, a restored backup, or a
                    user copying a project into the folder by hand.
  **missing_dirs**  a row whose directory is gone — deleted in Finder. Only
                    observable since `project_dir` stopped creating on read;
                    while it mkdir'd, merely listing projects resurrected the
                    directory and this state could not exist.
  **stale_tmp**     a `*.tmp` left by `atomic_io`. The `except` branch unlinks
                    it on an ordinary failure, so a survivor means the process
                    was KILLED mid-write and the sibling file may be a version
                    behind.

**`scan` is read-only.** It creates nothing, moves nothing and deletes nothing.
`sweep_tmp` is the only function here that removes, it removes only `*.tmp`,
and only when a caller passes it a report.

**Declared deviation from E3.** The spec says "on startup, import orphan
directories". This is manual and opt-in — `tools/reconcile_storage.py` — for
the same reason the importer never writes into a destination it cannot prove it
created: adopting a directory automatically fires on exactly the ambiguous
state where the app cannot know what the user meant, and the cost of guessing
wrong is somebody's project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import Project
from services import project_registry

# Task 7 stages an import into `<destination>.importing/`. It lives under the
# projects root and holds a `network.nc`, so without this it reads as an orphan
# — and adopting one means adopting a copy that has not been verified, or one
# that is still being written.
STAGING_SUFFIX = ".importing"

TMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class ReconcileReport:
    orphan_dirs: list[Path] = field(default_factory=list)
    missing_dirs: list[Project] = field(default_factory=list)
    stale_tmp: list[Path] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.orphan_dirs or self.missing_dirs or self.stale_tmp)


def _candidate_dirs(root: Path, *, org_segment: bool) -> list[Path]:
    """
    Every directory that could BE a project, at the depth the layout puts them.

    Depth follows `org_segment`. With the segment on — web mode, and any local
    install whose pre-0003 rows are still `<org>/<uuid>` — projects are at
    depth 2, and a depth-1 scan finds the org UUID directories, sees no
    `network.nc` in them, and reports a clean tree that is not clean.
    """
    if not root.is_dir():
        return []
    level_one = sorted(entry for entry in root.iterdir() if entry.is_dir())
    if not org_segment:
        return level_one
    return sorted(
        child
        for parent in level_one
        for child in parent.iterdir()
        if child.is_dir()
    )


def scan(db: DBSession, root, *, org_segment: bool) -> ReconcileReport:
    """Compare `root` with the `projects` table. Changes nothing."""
    root = Path(root)
    rows = db.scalars(select(Project)).all()
    known = {project_registry.project_dir(row).resolve() for row in rows}

    orphan_dirs: list[Path] = []
    stale_tmp: list[Path] = []
    for directory in _candidate_dirs(root, org_segment=org_segment):
        if directory.name.endswith(STAGING_SUFFIX):
            continue
        stale_tmp.extend(sorted(directory.rglob(f"*{TMP_SUFFIX}")))
        if not (directory / "network.nc").exists():
            # Not a project. Reporting bare directories invites the user to
            # "recover" something that never was one.
            continue
        if directory.resolve() not in known:
            orphan_dirs.append(directory)

    missing_dirs = [
        row for row in rows if not project_registry.project_dir(row).is_dir()
    ]

    return ReconcileReport(
        orphan_dirs=orphan_dirs, missing_dirs=missing_dirs, stale_tmp=stale_tmp
    )


def sweep_tmp(report: ReconcileReport) -> list[Path]:
    """
    Delete the `*.tmp` files a report lists. Nothing else.

    Takes the whole report and acts on one field of it, deliberately: the call
    site reads as "sweep the tmp files out of THIS scan", and there is no
    signature here that could ever be handed an orphan directory to remove.
    """
    removed: list[Path] = []
    for path in report.stale_tmp:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed
