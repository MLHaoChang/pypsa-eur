"""
Import projects from a pre-desktop install (spec F1-F4).

The one rule everything else follows from: **the source is authoritative until
the user says otherwise.** This module copies, never moves; a failure at any
point costs the destination, never the original. Retiring the old tree is
`tools/import_legacy --forget-legacy`, a separate action the user takes after
checking the result.

Sequence per project — **stage, receipt, rename, row**:

    <dest>.importing/          copy the tree here
    verify                     every source file present at the same size,
                               plus a SHA-256 of network.nc
    <dest>.importing/.pypsa-import-receipt.json
                               written INSIDE the staging directory, so it
                               moves atomically with the data
    os.rename -> <dest>        one operation
    INSERT                     the row

A crash between the rename and the insert therefore leaves a destination
carrying a matching receipt, which the next run recognises as "copied, needs
row" and completes. Without the receipt it would look like a foreign directory
the importer must refuse — and would be skipped forever.

**Idempotence keys on (source root, destination root, install id).** Source
root alone breaks on a synced checkout: importing on machine A writes a marker
into the shared tree, and machine B then sees a match and imports nothing,
silently. The install id is persisted in app-data, never per-process — an
ephemeral id makes every receipt unrecognisable to the next run.

**Size is never an "already imported" signal.** In the tree this was written
against, `KeepA` and `KeepB` are both 39,716 bytes and three
`chatbot_validation_*` projects are all 115,099. Size is a cheap first check
inside verification, and nothing more.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

import app_paths
from db.models import Project
from services import project_registry
from services.safe_names import unique_dir_name
from services.storage_paths import storage_path_for, taken_names, use_org_segment
from settings import get_settings

# Matches `storage_reconcile.STAGING_SUFFIX`, which skips these so a partial
# copy is never classified as an orphan project to adopt.
STAGING_SUFFIX = ".importing"

RECEIPT_NAME = ".pypsa-import-receipt.json"
MANIFEST_NAME = "import-manifest.json"
INSTALL_FILE = "install.json"

# `Project.name` is String(64) and SQLite does not enforce it, so a long name
# persists silently until something else trips over it.
_NAME_CAP = 64

# Not `network.nc` alone: a project is its whole bundle. But `network.nc` is
# the one file whose corruption is silent, so it gets a content hash while the
# rest get a presence-and-size check.
_HASHED = ("network.nc",)


# ── Install identity ─────────────────────────────────────────────────────────

def install_id() -> str:
    """
    A UUID identifying THIS installation, persisted in app-data.

    Not `LOCAL_ORG_ID`/`LOCAL_USER_ID`: those are fixed constants shared by
    every install, which would make the whole term a no-op. Not per-process
    either — an ephemeral id means every receipt carries a different one, the
    next run reads its own destinations as non-matching, refuses them as
    foreign collisions, and skips them permanently.
    """
    directory = app_paths.app_data_dir()
    # `O_EXCL` under a missing parent raises FileNotFoundError. The rehearsal
    # never hits that (bootstrap runs first); a bare CLI invocation on a fresh
    # machine would.
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / INSTALL_FILE

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"install_id": str(uuid.uuid4())}, handle)

    try:
        return json.loads(path.read_text(encoding="utf-8"))["install_id"]
    except (OSError, ValueError, KeyError):
        # A truncated file from a killed first run. Rewrite it rather than
        # failing the import; the cost is one spurious re-import.
        fresh = str(uuid.uuid4())
        path.write_text(json.dumps({"install_id": fresh}), encoding="utf-8")
        return fresh


# ── Inventory ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LegacyProject:
    path: Path
    dir_name: str
    name: str
    has_network: bool
    parent_name: str | None
    scenario_description: str | None
    skip_reason: str | None

    @property
    def importable(self) -> bool:
        return self.skip_reason is None


def _is_uuid_named(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


def _read_metadata(directory: Path) -> dict:
    try:
        data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt metadata.json costs the parent pointer, not the project.
        return {}
    return data if isinstance(data, dict) else {}


def _classify(entry: Path) -> str | None:
    if not entry.is_dir():
        return "not a directory"
    if _is_uuid_named(entry.name):
        # The org-scoped tree: `<org>/<project>/network.nc`. Its network is one
        # level down, so a recursive content check would import the whole org
        # root as a single project. `--rebase-db` handles it separately.
        return "org-scoped tree"
    if not (entry / "network.nc").exists():
        return "no network.nc"
    return None


def inventory(root) -> list[LegacyProject]:
    """
    Everything in `root`, classified. Reads only.

    **By content, never by suffix.** `new_project_test.pypsaproj` is a
    directory holding a 7 MB `network.nc` — 22 MB and the root of a three-level
    scenario chain behind a name that reads as an archive.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    found: list[LegacyProject] = []
    for entry in sorted(root.iterdir()):
        if entry.name.endswith(STAGING_SUFFIX) or entry.name.startswith("."):
            continue
        skip_reason = _classify(entry)
        metadata = _read_metadata(entry) if entry.is_dir() else {}
        display = metadata.get("name")
        if not isinstance(display, str) or not display.strip():
            display = entry.name
        parent = metadata.get("parent_project")
        found.append(
            LegacyProject(
                path=entry,
                dir_name=entry.name,
                name=display[:_NAME_CAP],
                has_network=entry.is_dir() and (entry / "network.nc").exists(),
                parent_name=parent if isinstance(parent, str) else None,
                scenario_description=metadata.get("scenario_description"),
                skip_reason=skip_reason,
            )
        )
    return found


# ── Copy + verify ────────────────────────────────────────────────────────────

def _relative_files(root: Path) -> list[Path]:
    return sorted(
        path.relative_to(root) for path in root.rglob("*") if path.is_file()
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_copy(source: Path, staged: Path) -> str | None:
    """
    Return a human-readable reason the copy is not trustworthy, or None.

    Presence and size for everything, plus a content hash for `network.nc` —
    the one file whose corruption is silent. Hashing 22 MB of `user_ts.json`
    on every import would buy very little for the time it costs.
    """
    for relative in _relative_files(source):
        src_file, dest_file = source / relative, staged / relative
        if not dest_file.is_file():
            return f"missing from the copy: {relative}"
        if src_file.stat().st_size != dest_file.stat().st_size:
            return f"size differs: {relative}"
        if relative.name in _HASHED and _sha256(src_file) != _sha256(dest_file):
            return f"content differs: {relative}"
    return None


def _normalise_modes(root: Path) -> None:
    """
    Strip group/other write bits from the copy.

    Three directories in the real tree are `drwxrwxrwx`. Under `~/Documents`
    that is a permission grant the user never made, and `copytree` preserves it.
    """
    for path in [root, *root.rglob("*")]:
        try:
            mode = path.stat().st_mode
            path.chmod(stat.S_IMODE(mode) & ~0o022)
        except OSError:
            continue


def _write_receipt(staged: Path, *, source: Path, source_root: Path, dest_root: Path) -> None:
    files = []
    for relative in _relative_files(source):
        entry = {"path": str(relative), "size": (source / relative).stat().st_size}
        if relative.name in _HASHED:
            entry["sha256"] = _sha256(source / relative)
        files.append(entry)
    receipt = {
        "source_root": str(source_root),
        "source_dir_name": source.name,
        "dest_root": str(dest_root),
        "install_id": install_id(),
        "imported_at": datetime.now(tz=timezone.utc).isoformat(),
        "files": files,
    }
    (staged / RECEIPT_NAME).write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def _read_receipt(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _receipt_matches(receipt: dict | None, *, source_root, source_dir_name, dest_root) -> bool:
    """
    Three keys, not one. Source root alone means a synced checkout carrying
    machine A's marker suppresses the import on machine B, silently.
    """
    if receipt is None:
        return False
    return (
        receipt.get("source_root") == str(source_root)
        and receipt.get("source_dir_name") == source_dir_name
        and receipt.get("dest_root") == str(dest_root)
        and receipt.get("install_id") == install_id()
    )


# ── Import ───────────────────────────────────────────────────────────────────

@dataclass
class ImportReport:
    would_import: list[str] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)
    already_imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # One entry per row this run inserted: `{"dir_name", "project_id",
    # "destination"}`. Rollback needs the ids — `parent_project_id` is
    # `ondelete="SET NULL"`, so a partial manual cleanup silently flattens the
    # scenario tree instead of failing.
    records: list[dict] = field(default_factory=list)


def _existing_destination(dest_root: Path, source_root: Path, candidate: LegacyProject):
    """
    **Lookup before allocate.**

    Find a destination already carrying a receipt for THIS source directory
    before allocating a fresh name. Skipping this means a crash-resumed run
    whose inventory order differs allocates a different directory and reports
    the half-imported project as a foreign collision it skips forever.
    """
    if not dest_root.is_dir():
        return None
    for entry in sorted(dest_root.iterdir()):
        if not entry.is_dir() or entry.name.endswith(STAGING_SUFFIX):
            continue
        if _receipt_matches(
            _read_receipt(entry),
            source_root=source_root,
            source_dir_name=candidate.dir_name,
            dest_root=dest_root,
        ):
            return entry
    return None


def import_all(
    db: DBSession,
    root,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    apply: bool = False,
) -> ImportReport:
    """
    Copy every importable project under `root` into the configured store.

    `apply=False` (the default) reports what would happen and touches nothing.
    """
    source_root = Path(root)
    report = ImportReport()
    candidates = [p for p in inventory(source_root) if p.importable]
    if not candidates:
        return report

    segment = use_org_segment()
    dest_root = Path(get_settings().projects_root)
    if segment:
        dest_root = dest_root / str(org_id)

    if not apply:
        # A dry run consults the receipts too. Listing every candidate as
        # "would import" regardless would make the rehearsal step useless
        # exactly where it matters — on the second run, where the question is
        # whether this install already has these projects.
        for candidate in candidates:
            if _existing_destination(dest_root, source_root, candidate) is None:
                report.would_import.append(candidate.dir_name)
            else:
                report.already_imported.append(candidate.dir_name)
        return report

    dest_root.mkdir(parents=True, exist_ok=True)
    # Seeded once and added to after each rename. Without the accumulation two
    # names that sanitise alike both target one directory, and the second is
    # caught by the exists-check and silently skipped.
    taken = taken_names(db, org_id, segment)
    inserted: dict[str, Project] = {}

    for candidate in candidates:
        existing = _existing_destination(dest_root, source_root, candidate)
        if existing is not None:
            row = _row_for_directory(db, org_id, existing)
            if row is not None:
                report.already_imported.append(candidate.dir_name)
                inserted[candidate.dir_name] = row
                continue
            # Copied, needs row — a crash between the rename and the insert.
            destination = existing
        else:
            destination = _stage_and_rename(
                source_root, candidate, dest_root, taken, report
            )
            if destination is None:
                continue
            taken.add(destination.name)

        row = _insert_row(db, candidate, destination, org_id, user_id, segment)
        inserted[candidate.dir_name] = row
        report.imported.append(candidate.dir_name)
        report.records.append(
            {
                "dir_name": candidate.dir_name,
                "project_id": str(row.id),
                "destination": str(destination),
            }
        )

    db.commit()
    _link_parents(db, candidates, inserted, report)
    return report


# ── Run manifest + rollback ──────────────────────────────────────────────────

def write_manifest(report: ImportReport, *, source_root, dest_root, path) -> Path:
    """
    Record what this run did, so `--rollback` can undo exactly that.

    `--apply` refuses when this cannot be written: an import with no manifest
    is an import with no way back, and the rollback procedure is the reason it
    is safe to run at all.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source_root": str(source_root),
                "dest_root": str(dest_root),
                "install_id": install_id(),
                "imported_at": datetime.now(tz=timezone.utc).isoformat(),
                "records": report.records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def rollback(db: DBSession, manifest_path) -> list[str]:
    """
    Delete exactly the destinations and rows a manifest records.

    Rows are identifiable ONLY because the manifest carries their ids.
    `parent_project_id` is `ondelete="SET NULL"`, so cleaning up by hand
    silently flattens the scenario tree rather than failing.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    removed: list[str] = []
    for record in manifest.get("records", []):
        destination = Path(record["destination"])
        # Only remove a directory this import created — proved by its receipt,
        # not by its name.
        if destination.is_dir() and _read_receipt(destination) is not None:
            shutil.rmtree(destination, ignore_errors=True)
        row = db.get(Project, uuid.UUID(record["project_id"]))
        if row is not None:
            db.delete(row)
        removed.append(record["dir_name"])
    db.commit()
    return removed


# ── Rebase an existing row onto the readable layout ──────────────────────────

def rebase_row(db: DBSession, row: Project) -> Path:
    """
    Copy one project's directory into the readable layout and repoint its row.

    The only thing that delivers E2 for a row whose `storage_path` points
    OUTSIDE `projects_root` — which migration 0003 leaves alone by design, and
    which on the machine this was written for is the only row there is.

    It copies, like everything else here. A filesystem move is not transactional
    with a database commit, so the order is explicit and the source stays
    authoritative throughout:

        1. copy to `projects_root/<sanitised name>` — the same layout every
           other project gets, not `<org>/<uuid>` verbatim, which would
           reintroduce the segment the desktop layout removes.
        2. verify with the same manifest + SHA-256 as an import. Abort and
           remove the copy if it fails.
        3. rewrite the row and commit.
        4. if the commit fails, remove the copy and re-raise.
        5. leave the source for `--forget-legacy`.
    """
    source = project_registry.project_dir(row)
    if not source.is_dir():
        raise FileNotFoundError(f"{row.name}: {source} does not exist")

    segment = use_org_segment()
    root = Path(get_settings().projects_root)
    dest_root = root / str(row.org_id) if segment else root
    dest_root.mkdir(parents=True, exist_ok=True)

    relative = storage_path_for(
        row.org_id, row.id, row.name,
        taken=taken_names(db, row.org_id, segment),
        org_segment=segment,
    )
    destination = root / relative

    # `dirs_exist_ok=False` is the default and is the point: fail safe if the
    # allocator and the filesystem disagree.
    shutil.copytree(source, destination, symlinks=True, ignore_dangling_symlinks=True)

    # Verify BEFORE touching the session at all. A failure here has not
    # modified the row, so rolling back would only discard whatever else the
    # caller had in flight.
    try:
        _normalise_modes(destination)
        problem = _verify_copy(source, destination)
        if problem is not None:
            raise OSError(f"{row.name}: copy verification failed — {problem}")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    previous = row.storage_path
    row.storage_path = str(relative)
    row.updated_at = datetime.now(tz=timezone.utc)
    try:
        db.commit()
    except Exception:
        row.storage_path = previous
        db.rollback()
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def _stage_and_rename(source_root, candidate, dest_root, taken, report) -> Path | None:
    """Copy into `<dest>.importing/`, verify, receipt, then one rename."""
    directory_name = unique_dir_name(candidate.name, taken)
    destination = dest_root / directory_name
    staged = dest_root / f"{directory_name}{STAGING_SUFFIX}"

    if destination.exists():
        # Never write into a destination it cannot prove it created. Checked
        # explicitly rather than left to `os.rename`, which silently succeeds
        # onto an existing EMPTY directory on POSIX and raises on Windows.
        report.collisions.append(
            f"{candidate.dir_name}: '{destination.name}' already exists and is not ours"
        )
        return None

    # An abandoned staging directory is an unverified partial copy. Discard it.
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)

    try:
        shutil.copytree(
            candidate.path, staged, symlinks=True, ignore_dangling_symlinks=True
        )
        _normalise_modes(staged)
        problem = _verify_copy(candidate.path, staged)
        if problem is not None:
            raise OSError(problem)
        _write_receipt(
            staged, source=candidate.path, source_root=source_root, dest_root=dest_root
        )
        os.rename(staged, destination)
    except OSError as exc:
        shutil.rmtree(staged, ignore_errors=True)
        report.failed.append(f"{candidate.dir_name}: {exc}")
        return None
    return destination


def _row_for_directory(db: DBSession, org_id: uuid.UUID, destination: Path):
    from sqlalchemy import select

    for row in db.scalars(select(Project).where(Project.org_id == org_id)).all():
        if project_registry.project_dir(row) == destination:
            return row
    return None


def _insert_row(db, candidate, destination, org_id, user_id, segment) -> Project:
    project_id = uuid.uuid4()
    # Built directly rather than through `storage_path_for`: the directory is
    # already allocated and on disk, and re-running the sanitiser over its name
    # could only ever disagree with it.
    relative = Path(str(org_id)) / destination.name if segment else Path(destination.name)
    project = Project(
        id=project_id,
        org_id=org_id,
        name=candidate.name,
        created_by=user_id,
        storage_path=str(relative),
        parent_project_id=None,
        scenario_description=candidate.scenario_description,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    db.add(project)
    db.flush()
    return project


def _link_parents(db, candidates, inserted, report) -> None:
    """
    Second pass. `parent_project` is a NAME, and a child can be inventoried
    before its parent, so the links are made once every row exists. A dangling
    parent is normal — the real tree has one — and reported rather than fixed.
    """
    by_name = {c.name: inserted.get(c.dir_name) for c in candidates}
    for candidate in candidates:
        child = inserted.get(candidate.dir_name)
        if child is None or candidate.parent_name is None:
            continue
        parent = by_name.get(candidate.parent_name)
        if parent is None:
            report.warnings.append(
                f"'{candidate.name}': parent '{candidate.parent_name}' was not "
                "found in the legacy tree; imported as a root"
            )
            continue
        child.parent_project_id = parent.id
    db.commit()
