"""
Import projects from a pre-desktop install (spec F1-F4).

The one rule everything else follows from: **the source is authoritative until
the user says otherwise.** This module copies, never moves; a failure at any
point costs the destination, never the original. Retiring the old tree is
`tools/import_legacy --forget-legacy`, a separate action the user takes after
checking the result.

Sequence per project — **stage, receipt, rename, row**:

    .pypsa-importing-<hash>/   copy the tree here (hidden, and a name no
                               project directory can ever equal)
    verify                     every source file present at the same size,
                               plus a SHA-256 of network.nc
    .../.pypsa-import-receipt.json
                               written INSIDE the staging directory, so it
                               moves atomically with the data
    os.rename -> <dest>        one operation
    INSERT + COMMIT            the row, on its own

A crash between the rename and the insert therefore leaves a destination
carrying a matching receipt, which the next run recognises as "copied, needs
row" and completes. Without the receipt it would look like a foreign directory
the importer must refuse — and would be skipped forever.

**Idempotence has TWO signals, and both are needed.**

  *The receipt* inside each destination, keyed on (source root, source
  directory, destination root). It recognises an already-imported tree on a
  machine with no manifests at all — a restored backup, a reinstall — and it is
  what keeps the synced-checkout case working: machine B has its own
  destination root, so machine A's receipts do not match and its projects still
  import.

  *The run manifests* in app-data, read back by `_ledger`. The receipt lives
  INSIDE the destination, so deleting the project through the UI deletes the
  marker with it — and a review found the next launch then re-imported a
  project the user had just removed, every launch, for as long as the legacy
  root stayed configured. The manifests survive that. They are the same files
  `--rollback` consumes, so rolling a run back also makes those projects
  importable again.

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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

import app_paths
from db.models import Project
from services import project_registry
from services.atomic_io import atomic_write_text
from services.safe_names import unique_dir_name
from services.storage_paths import (
    allocate_storage_path,
    storage_path_for,
    storage_value,
    taken_names,
    use_org_segment,
)
from settings import get_settings

# Staging directories are named `<PREFIX><12 hex>` — deliberately NOT derived
# from the project's own name.
#
# The first version used `<destination>.importing`, and a review showed that
# `safe_dir_name` happily produces `Belgium Grid.importing`, so a live project
# could occupy the name and `_stage_and_rename`'s "discard the abandoned
# staging directory" step would `rmtree` it. The leading dot is the fix that
# makes the collision impossible rather than unlikely: `safe_dir_name` strips
# leading dots, so no allocated directory can ever begin with one. The hash
# keeps the name short — the full name would blow the 96-character per
# component budget that exists for Windows' 260-character path limit.
STAGING_PREFIX = ".pypsa-importing-"

# Kept for `storage_reconcile`, which must not classify either shape as an
# orphan project to adopt.
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


def _scenario_fields(metadata: dict) -> dict[str, str | None]:
    """
    `scenario_type` + `scenario_description` out of a legacy metadata.json.

    Thin re-export of the router's decoder so both importers and the project
    endpoints agree on what an old bundle means. Imported lazily: this module
    is reachable from `routers.projects`, and a module-level import back the
    other way is a cycle.
    """
    from routers.projects import _scenario_fields_from_meta

    return _scenario_fields_from_meta(metadata)


# ── Inventory ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LegacyProject:
    path: Path
    dir_name: str
    name: str
    has_network: bool
    parent_name: str | None
    scenario_description: str | None
    # Split out of `scenario_description` when the on-disk project predates
    # migration 0004 and still carries the retired `[type]` prefix inline.
    # Passing the description through raw — which this importer did, while its
    # sibling `legacy_migrate` was updated — recreated exactly the state 0004
    # exists to delete: an uncategorised row whose description renders the
    # marker as prose. This is the ONLY import path the desktop app offers.
    scenario_type: str | None
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
        # An org-scoped tree, `<org>/<project>/network.nc`. Not a project
        # itself — its network is one level down — but the projects INSIDE it
        # are real and `_org_tree_children` imports them.
        return "org-scoped tree"
    if not (entry / "network.nc").exists():
        return "no network.nc"
    return None


def _org_tree_children(entry: Path) -> list[Path]:
    """
    The real projects inside an org-scoped tree.

    A review found the tree this phase was written against holds a solved
    3-bus, 8760-snapshot network with its chat, layout and time series — and
    NOTHING shipped in phase 1b imported it. It was classified `org-scoped
    tree`, dropped, and not even reported: `--rebase-db` only walks database
    ROWS, and a fresh desktop install has no row for it.

    So descend exactly one level. Not `rglob`: one level is the org layout, and
    anything deeper is a project's own subtree (`snapshots/`, `uploads/`) that
    must never be mistaken for a sibling project.
    """
    try:
        return sorted(
            child
            for child in entry.iterdir()
            if child.is_dir() and (child / "network.nc").exists()
        )
    except OSError:
        return []


def _org_tree_display_name(child: Path, metadata: dict) -> str:
    """
    A findable name for a project that only ever had a UUID.

    The org layout stores no display name — the name lived in the database this
    tree was detached from. A UUID directory would satisfy the importer and
    defeat E1, so fall back to something the user can recognise and rename.
    """
    name = metadata.get("name")
    if isinstance(name, str) and name.strip():
        return name[:_NAME_CAP]
    return f"Imported project {child.name[:8]}"


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
        if _is_staging(entry.name) or entry.name.startswith("."):
            continue
        skip_reason = _classify(entry)

        if skip_reason == "org-scoped tree":
            # Report the container as skipped — it is not a project — and then
            # import each project inside it.
            found.append(
                LegacyProject(
                    path=entry, dir_name=entry.name, name=entry.name,
                    has_network=False, parent_name=None,
                    scenario_description=None, scenario_type=None,
                    skip_reason=skip_reason,
                )
            )
            for child in _org_tree_children(entry):
                child_meta = _read_metadata(child)
                parent = child_meta.get("parent_project")
                found.append(
                    LegacyProject(
                        path=child,
                        dir_name=f"{entry.name}/{child.name}",
                        name=_org_tree_display_name(child, child_meta),
                        has_network=True,
                        parent_name=parent if isinstance(parent, str) else None,
                        **_scenario_fields(child_meta),
                        skip_reason=None,
                    )
                )
            continue

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
                **_scenario_fields(metadata),
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

    **Symlinks are skipped.** `Path.chmod` FOLLOWS them, so an earlier version
    of this loop wrote permission changes into the user's original legacy tree
    — directly contradicting this module's own guarantee that a failure costs
    the destination and never the original. `os.chmod(follow_symlinks=False)`
    is not portable (it raises `NotImplementedError` on Linux), so the entries
    are skipped instead; a symlink's own mode bits are not consulted by any
    platform this targets.
    """
    for path in [root, *root.rglob("*")]:
        try:
            if path.is_symlink():
                continue
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

    `install_id` is deliberately NOT among them. It is recorded in the receipt
    for provenance, but matching on it would mean a reinstall — new app-data,
    same projects folder — fails to recognise its own destinations and imports
    everything a second time alongside. `dest_root` already carries the
    distinction `install_id` was reaching for.
    """
    if receipt is None:
        return False
    return (
        receipt.get("source_root") == str(source_root)
        and receipt.get("source_dir_name") == source_dir_name
        and receipt.get("dest_root") == str(dest_root)
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
        if not entry.is_dir() or _is_staging(entry.name):
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
    manifest_path=None,
) -> ImportReport:
    """
    Copy every importable project under `root` into the configured store.

    `apply=False` (the default) reports what would happen and touches nothing.

    `manifest_path` is where this run's record goes. It is written EMPTY before
    the first byte is copied and rewritten after every successful project, so
    an import that cannot be rolled back is refused up front rather than
    discovered afterwards — and a crash midway still leaves a manifest covering
    everything that did land.
    """
    source_root = Path(root)
    report = ImportReport()
    entries = inventory(source_root)
    candidates = [p for p in entries if p.importable]
    # `skipped` was declared and never written to, so an entry the importer
    # decided was not a project vanished from every surface: the CLI report,
    # `last-import-report.json`, and the log. The user was then told to "check
    # the import" against a report that could not show them what was left out.
    report.skipped = [
        f"{p.dir_name}: {p.skip_reason}" for p in entries if not p.importable
    ]
    if not candidates:
        return report

    segment = use_org_segment()
    dest_root = Path(get_settings().projects_root)
    if segment:
        dest_root = dest_root / str(org_id)

    done = _ledger(dest_root)

    def _already(candidate) -> bool:
        """
        Two signals, and both are needed.

        The receipt lives inside the destination, so deleting the project
        through the UI deletes it — and the next launch would re-import a
        project the user just removed, every time. The ledger is every previous
        run's manifest, which survives that. Conversely the receipt is what
        recognises a destination on a machine with no manifests at all (a
        restored backup, a reinstall).
        """
        if (source_root_str(source_root), candidate.dir_name) in done:
            return True
        return _existing_destination(dest_root, source_root, candidate) is not None

    if not apply:
        # A dry run consults both signals too. Listing every candidate as
        # "would import" regardless would make the rehearsal useless exactly
        # where it matters — on the second run.
        for candidate in candidates:
            if _already(candidate):
                report.already_imported.append(candidate.dir_name)
            else:
                report.would_import.append(candidate.dir_name)
        return report

    dest_root.mkdir(parents=True, exist_ok=True)
    if manifest_path is not None:
        # Refuse BEFORE copying anything. An import with no manifest is an
        # import with no way back.
        write_manifest(report, source_root=source_root, dest_root=dest_root,
                       path=manifest_path)

    # Seeded once and added to after each rename. Without the accumulation two
    # names that sanitise alike both target one directory, and the second is
    # caught by the exists-check and silently skipped.
    taken = taken_names(db, org_id, segment)
    inserted: dict[str, Project] = {}

    for candidate in candidates:
        ledger_hit = (source_root_str(source_root), candidate.dir_name) in done
        existing = _existing_destination(dest_root, source_root, candidate)

        if ledger_hit:
            # Already imported — but it must STILL enter `inserted`, or a child
            # imported in a later run than its parent is reparented to root and
            # warned about as "parent not found in the legacy tree" while the
            # parent sits in the same database. Returning early here was a
            # regression the ledger introduced.
            row = _row_for_directory(db, org_id, existing) if existing else None
            if row is not None:
                inserted[candidate.dir_name] = row
            report.already_imported.append(candidate.dir_name)
            continue

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

        row = _insert_row_committing(
            db, candidate, destination, org_id, user_id, segment, report
        )
        if row is None:
            continue
        inserted[candidate.dir_name] = row
        report.imported.append(candidate.dir_name)
        report.records.append(
            {
                "dir_name": candidate.dir_name,
                "source_root": source_root_str(source_root),
                "project_id": str(row.id),
                "destination": str(destination),
            }
        )
        if manifest_path is not None:
            write_manifest(report, source_root=source_root, dest_root=dest_root,
                           path=manifest_path)

    _link_parents(db, candidates, inserted, report)
    return report


def source_root_str(source_root) -> str:
    """
    ONE spelling of the source root, shared by every entry point.

    The ledger and the receipts are both keyed on this string, so two spellings
    of the same tree are two different imports. `main.py` used
    `Path(x).expanduser()` while the CLI used `.expanduser().resolve()`, and on
    a symlinked path — `/tmp`, an iCloud- or OneDrive-backed `~/Documents` —
    those differ: a CLI `--apply` after a first-run import copied all 113 MB a
    second time as `Name (2)`. Both go through here now.
    """
    return str(Path(source_root).expanduser().resolve())


def _ledger(dest_root: Path) -> set[tuple[str, str]]:
    """
    Every `(source root, source directory)` a previous run recorded for THIS
    destination, read back from the manifests in app-data.

    This is what makes deletion stick. Idempotence keyed only on a receipt
    inside the destination means removing the project removes the marker, and
    the importer resurrects it on the next launch — forever, while the legacy
    root stays configured.

    Scoped by destination root, so the synced-checkout case still imports: a
    second machine has its own app-data and its own destination.
    """
    done: set[tuple[str, str]] = set()
    try:
        manifests = sorted(app_paths.app_data_dir().glob("import-manifest-*.json"))
    except OSError:
        return done
    for path in manifests:
        # Every isinstance below is a guard against a file killing every future
        # import. `_read_receipt` and `_read_metadata` have had this check
        # since they were written; this function shipped without it, and a
        # manifest containing `[]` or `null` — shape-valid JSON — raised
        # `AttributeError` out of `import_all` BEFORE the dry-run branch. On
        # the first-run path that surfaces as "first-run import failed" on
        # every launch, forever, with the projects never importable and nothing
        # saying why.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("dest_root") != str(dest_root):
            continue
        records = data.get("records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            source = record.get("source_root")
            name = record.get("dir_name")
            if isinstance(source, str) and isinstance(name, str):
                done.add((source, name))
    return done


def _insert_row_committing(db, candidate, destination, org_id, user_id, segment, report):
    """
    Insert one row and commit it, on its own.

    Per candidate, NOT one commit for the whole run. `Project` carries
    `UniqueConstraint("org_id", "name")` from migration 0001, so a legacy
    project whose name is already taken — the user deleted it and made a new
    one, or two legacy names collide after the 64-character truncation — raises
    `IntegrityError`. With a single commit at the end that exception aborts the
    entire loop, and every candidate after it is never imported again on any
    launch, swallowed into a log line nobody reads.

    The name is suffixed and retried once, because the DIRECTORY has already
    been allocated a free name and refusing here would strand it.
    """
    for attempt in range(2):
        name = candidate.name if attempt == 0 else _suffixed(candidate.name, attempt + 1)
        try:
            row = _insert_row(db, candidate, destination, org_id, user_id, segment, name)
            db.commit()
            db.refresh(row)
            if name != candidate.name:
                report.warnings.append(
                    f"'{candidate.dir_name}': the name '{candidate.name}' was "
                    f"already taken; imported as '{name}'"
                )
            return row
        except IntegrityError:
            db.rollback()
    report.failed.append(
        f"{candidate.dir_name}: could not insert a row (name '{candidate.name}' "
        "is taken and the suffixed form is too)"
    )
    return None


def _suffixed(name: str, n: int) -> str:
    suffix = f" ({n})"
    return f"{name[: _NAME_CAP - len(suffix)]}{suffix}"


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
    # ATOMIC. This is rewritten after every project so that a crash midway
    # still leaves a manifest covering what landed — a promise a bare
    # `write_text` cannot keep, because it truncates first. A torn manifest
    # loses both the rollback record and the ledger for the whole run, and this
    # phase shipped `atomic_io` precisely so nothing that matters is written
    # any other way.
    atomic_write_text(
        path,
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
    )
    return path


def rollback(db: DBSession, manifest_path) -> list[str]:
    """
    Delete exactly the destinations and rows a manifest records.

    Rows are identifiable ONLY because the manifest carries their ids.
    `parent_project_id` is `ondelete="SET NULL"`, so cleaning up by hand
    silently flattens the scenario tree rather than failing.
    """
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed: list[str] = []
    for record in manifest.get("records", []):
        destination = Path(record["destination"])
        # Only remove a directory THIS RUN created. A receipt alone proves
        # "some import made this" — after a rename frees the name and a later
        # import takes it, that is a different project, also receipt-bearing.
        # The receipt names its own source directory, so cross-check it.
        row = db.get(Project, uuid.UUID(record["project_id"]))
        receipt = _read_receipt(destination) if destination.is_dir() else None
        # Three conditions, and the third is the one a review added: the
        # recorded directory must still be THIS row's. After a rename the row
        # has moved on, and if the freed name is later retaken by a re-import
        # of the same source directory the receipt matches a DIFFERENT
        # project — which rollback would then delete.
        ours = (
            receipt is not None
            and receipt.get("source_dir_name") == record.get("dir_name")
            and receipt.get("dest_root") == manifest.get("dest_root")
            and (row is None or project_registry.project_dir(row) == destination)
        )
        if ours:
            shutil.rmtree(destination, ignore_errors=True)
        if row is not None:
            db.delete(row)
        removed.append(record["dir_name"])
    db.commit()
    # Remove the manifest itself, or the ledger keeps reporting these projects
    # as already-imported and the user can never bring them back.
    manifest_path.unlink(missing_ok=True)
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

    # `exclude` the row's OWN directory name. `taken_names` includes every row
    # in the org — including this one — so without it every rebase allocates
    # ` (2)` and commits that permanently. `rename_project` has had this since
    # it was written; this function did not.
    relative = allocate_storage_path(
        db, row.org_id, row.id, row.name,
        org_segment=segment, exclude={source.name},
    )
    destination = root / relative
    staged = _staging_dir(destination.parent, relative.name)

    # `dirs_exist_ok=False` is the default and is the point: fail safe if the
    # allocator and the filesystem disagree.
    # STAGE, verify, then publish with one rename — the same shape as
    # `_stage_and_rename`, and for the same reason. Copying straight to the
    # final name leaves a fully materialised but UNVERIFIED tree there when
    # `copytree` fails (it accumulates per-entry errors and raises only at the
    # end) or when the process is killed — and `storage_reconcile` then offers
    # that partial copy to the operator as "an orphan directory that may be a
    # project worth keeping". The name is also burnt: `taken_names` reserves
    # it, so the retry lands on ` (3)`.
    #
    # `symlinks=False`: a preserved link would make `_verify_copy` hash the
    # same inode twice — a tautology — and `--forget-legacy`, which the CLI
    # recommends immediately afterwards, would destroy the "imported" project.
    if destination.exists():
        raise FileExistsError(f"{row.name}: {destination} already exists")
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)

    try:
        shutil.copytree(source, staged, symlinks=False, ignore_dangling_symlinks=True)
        _normalise_modes(staged)
        problem = _verify_copy(source, staged)
        if problem is not None:
            raise OSError(f"{row.name}: copy verification failed — {problem}")
        os.rename(staged, destination)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise

    previous = row.storage_path
    row.storage_path = storage_value(relative)
    row.updated_at = datetime.now(tz=timezone.utc)
    try:
        db.commit()
    except Exception:
        row.storage_path = previous
        db.rollback()
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def _is_staging(name: str) -> bool:
    """Both shapes: the current hidden prefix and the historical suffix."""
    return name.startswith(STAGING_PREFIX) or name.endswith(STAGING_SUFFIX)


def _staging_dir(dest_root: Path, directory_name: str) -> Path:
    """
    A staging path that no project directory can ever equal.

    Hashed and dot-prefixed rather than `<name>.importing` — see
    `STAGING_PREFIX`. The digest is of the allocated name, so a resumed run
    lands on the same staging directory and discards it deterministically.
    """
    digest = hashlib.sha256(directory_name.encode("utf-8")).hexdigest()[:12]
    return dest_root / f"{STAGING_PREFIX}{digest}"


def _stage_and_rename(source_root, candidate, dest_root, taken, report) -> Path | None:
    """Copy into a hidden staging directory, verify, receipt, then one rename."""
    directory_name = unique_dir_name(candidate.name, taken)
    destination = dest_root / directory_name
    staged = _staging_dir(dest_root, directory_name)

    if destination.exists():
        # Never write into a destination it cannot prove it created. Checked
        # explicitly rather than left to `os.rename`, which silently succeeds
        # onto an existing EMPTY directory on POSIX and raises on Windows.
        report.collisions.append(
            f"{candidate.dir_name}: '{destination.name}' already exists and is not ours"
        )
        return None

    # An abandoned staging directory is an unverified partial copy. Safe to
    # discard ONLY because the name is one no project can hold.
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)

    try:
        # `symlinks=False`: DEREFERENCE. Copying a symlink as a symlink makes
        # the destination depend on the source — `_verify_copy` then hashes the
        # same file on both sides, so the check is a tautology, and the
        # `--forget-legacy` step this CLI actively recommends destroys the
        # imported project. A copy has to be a copy.
        shutil.copytree(
            candidate.path, staged, symlinks=False, ignore_dangling_symlinks=True
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


def _insert_row(db, candidate, destination, org_id, user_id, segment, name=None) -> Project:
    project_id = uuid.uuid4()
    # Built directly rather than through `storage_path_for`: the directory is
    # already allocated and on disk, and re-running the sanitiser over its name
    # could only ever disagree with it.
    relative = Path(str(org_id)) / destination.name if segment else Path(destination.name)
    project = Project(
        id=project_id,
        org_id=org_id,
        name=name if name is not None else candidate.name,
        created_by=user_id,
        storage_path=storage_value(relative),
        parent_project_id=None,
        scenario_description=candidate.scenario_description,
        scenario_type=candidate.scenario_type,
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
