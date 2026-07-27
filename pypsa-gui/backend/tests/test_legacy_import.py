"""
Copying the legacy tree into the desktop store (phase 1b, Task 7 — F1/F3/F4).

Every rule tested here came from a confirmed finding, not from imagination:

  **Stage -> receipt -> rename -> row.** A crash between the rename and the row
  insert used to leave a populated destination with no row and no manifest
  entry, which the next run classified as a foreign collision and skipped
  forever. The receipt is written INSIDE the staging directory, so it moves
  atomically with the data.

  **Idempotence keys on (source root, destination root, install id).** Source
  root alone breaks on a synced checkout: import on machine A writes a manifest
  into the synced tree, machine B sees a match and imports nothing, silently.

  **Size is never an "already imported" signal.** In the real tree `KeepA` and
  `KeepB` are both 39,716 bytes and three `chatbot_validation_*` projects are
  all 115,099 — a size check would call them identical.

  **Copy, never move.** The source is authoritative until the user runs
  `--forget-legacy`, which is a separate action this module never takes.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from db.models import Organization, Project, User
from services import project_registry
from services.legacy_import import (
    RECEIPT_NAME,
    STAGING_SUFFIX,
    import_all,
    install_id,
    rebase_row,
    rollback,
    write_manifest,
)
from settings import get_settings

_ORG = uuid.UUID("33333333-3333-4333-8333-333333333333")
_USER = uuid.UUID("33333333-3333-4333-8333-333333333334")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Local-mode layout: no org segment, isolated roots, isolated app data."""
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "Projects"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def dest_root(env):
    root = get_settings().projects_root
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def source(tmp_path):
    return tmp_path / "legacy"


@pytest.fixture
def identity(db_session):
    db_session.add(Organization(id=_ORG, name="Local", created_at=_now()))
    db_session.add(User(id=_USER, email="local@example.com", password_hash=None,
                        status="active", created_at=_now()))
    db_session.commit()
    return _ORG, _USER


def _write_project(root, name, *, parent=None, payload="network", extras=()):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.json").write_text(
        json.dumps({"name": name, "parent_project": parent}), encoding="utf-8"
    )
    (directory / "network.nc").write_text(payload, encoding="utf-8")
    for relative in extras:
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"content:{relative}", encoding="utf-8")
    return directory


def _run(db, source, *, apply=True):
    return import_all(db, source, _ORG, _USER, apply=apply)


def _rows(db) -> dict[str, Project]:
    from sqlalchemy import select

    return {row.name: row for row in db.scalars(select(Project)).all()}


# ── The happy path ───────────────────────────────────────────────────────────

def test_a_clean_import_copies_every_file_and_inserts_rows(db_session, identity, source, dest_root):
    _write_project(source, "Belgium Grid", extras=(
        "user_ts.json", "layout.json", "solver_config.json", "chat.jsonl",
        "results_state.pkl", "snapshots/2026-01-01-a/network.nc",
        "uploads/file-1/ref.csv",
    ))

    report = _run(db_session, source)

    assert report.imported == ["Belgium Grid"]
    row = _rows(db_session)["Belgium Grid"]
    directory = project_registry.project_dir(row)
    assert directory.name == "Belgium Grid"
    for relative in ("network.nc", "metadata.json", "user_ts.json", "layout.json",
                     "solver_config.json", "chat.jsonl", "results_state.pkl",
                     "snapshots/2026-01-01-a/network.nc", "uploads/file-1/ref.csv"):
        assert (directory / relative).exists(), relative


def test_the_source_is_never_moved(db_session, identity, source, dest_root):
    """`--forget-legacy` is a separate, user-initiated action. Until then the
    source is the authoritative copy and this module does not touch it."""
    original = _write_project(source, "Belgium Grid")

    _run(db_session, source)

    assert (original / "network.nc").read_text(encoding="utf-8") == "network"


def test_dry_run_changes_nothing(db_session, identity, source, dest_root):
    _write_project(source, "Belgium Grid")

    report = _run(db_session, source, apply=False)

    assert report.would_import == ["Belgium Grid"]
    assert report.imported == []
    assert list(dest_root.iterdir()) == []
    assert _rows(db_session) == {}


def test_names_that_sanitise_alike_get_distinct_directories(db_session, identity, source, dest_root):
    """
    `taken` accumulates ACROSS the loop. Seeded once and never updated, both of
    these target `Study_1`; the second is then caught by the exists-check and
    silently skipped. No data is lost — the source is untouched — but a project
    goes un-imported, which is what lookup-before-allocate exists to prevent.
    """
    _write_project(source, "Study:1", payload="first")
    _write_project(source, "Study_1", payload="second")

    report = _run(db_session, source)

    assert sorted(report.imported) == ["Study:1", "Study_1"]
    directories = sorted(p.name for p in dest_root.iterdir())
    assert directories == ["Study_1", "Study_1 (2)"]
    payloads = {(d / "network.nc").read_text(encoding="utf-8") for d in dest_root.iterdir()}
    assert payloads == {"first", "second"}


# ── Parents ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("order", [("Base", "Variant"), ("Variant", "Base")])
def test_parents_resolve_in_either_inventory_order(db_session, identity, source, dest_root, order):
    """Two-pass: the child can be inventoried before its parent exists as a
    row, so the links are made after every row is in."""
    for name in order:
        _write_project(source, name, parent="Base" if name == "Variant" else None)

    _run(db_session, source)

    rows = _rows(db_session)
    assert rows["Variant"].parent_project_id == rows["Base"].id


def test_a_dangling_parent_is_reported_and_the_child_still_imports(db_session, identity, source, dest_root):
    _write_project(source, "Orphan Child", parent="Never Existed")

    report = _run(db_session, source)

    assert report.imported == ["Orphan Child"]
    assert _rows(db_session)["Orphan Child"].parent_project_id is None
    assert any("Never Existed" in warning for warning in report.warnings)


# ── Idempotence ──────────────────────────────────────────────────────────────

def test_a_second_run_imports_nothing_new(db_session, identity, source, dest_root):
    _write_project(source, "Belgium Grid")
    _run(db_session, source)

    second = _run(db_session, source)

    assert second.imported == []
    assert second.already_imported == ["Belgium Grid"]
    assert len(_rows(db_session)) == 1


def test_identical_sizes_are_not_treated_as_already_imported(db_session, identity, source, dest_root):
    """
    In the real tree `KeepA` and `KeepB` are both 39,716 bytes. A size-based
    check calls the second one already-imported and drops it.
    """
    _write_project(source, "KeepA", payload="x" * 100)
    _write_project(source, "KeepB", payload="y" * 100)

    report = _run(db_session, source)

    assert sorted(report.imported) == ["KeepA", "KeepB"]
    assert len(_rows(db_session)) == 2


def test_a_different_destination_root_is_a_different_import(db_session, identity, source, tmp_path, monkeypatch):
    """
    The synced-checkout case. Machine A's receipt names A's destination; on
    machine B the destination differs, so the project must still be imported
    rather than skipped because a marker happens to be sitting in the shared
    tree. A dry run is the right shape here — machine B has its own database,
    so re-inserting into A's would trip `UniqueConstraint(org_id, name)` for a
    reason that has nothing to do with the property under test.
    """
    _write_project(source, "Belgium Grid")
    _run(db_session, source)
    assert _run(db_session, source, apply=False).already_imported == ["Belgium Grid"]

    other = tmp_path / "OtherProjects"
    other.mkdir()
    monkeypatch.setenv("PROJECTS_ROOT", str(other))
    get_settings.cache_clear()

    report = _run(db_session, source, apply=False)

    assert report.would_import == ["Belgium Grid"]
    assert report.already_imported == []


# ── Crash recovery ───────────────────────────────────────────────────────────

def test_a_crash_before_the_rename_leaves_staging_the_next_run_discards(
    db_session, identity, source, dest_root
):
    """A staging directory is an unverified partial copy. The next run throws
    it away and starts again rather than adopting it."""
    _write_project(source, "Belgium Grid")
    abandoned = dest_root / f"Belgium Grid{STAGING_SUFFIX}"
    abandoned.mkdir()
    (abandoned / "network.nc").write_text("half a copy", encoding="utf-8")

    report = _run(db_session, source)

    assert report.imported == ["Belgium Grid"]
    assert not abandoned.exists()
    row = _rows(db_session)["Belgium Grid"]
    assert (project_registry.project_dir(row) / "network.nc").read_text(
        encoding="utf-8"
    ) == "network"


def test_a_crash_after_the_rename_is_completed_not_refused(db_session, identity, source, dest_root):
    """
    The destination is populated and carries a matching receipt, but no row
    exists. That is "copied, needs row" — not the foreign collision the naive
    exists-check would report and skip forever.
    """
    _write_project(source, "Belgium Grid")
    _run(db_session, source, apply=False)          # nothing yet

    # Simulate the crash: run the import, then delete the row it inserted.
    _run(db_session, source)
    row = _rows(db_session)["Belgium Grid"]
    directory = project_registry.project_dir(row)
    db_session.delete(row)
    db_session.commit()
    assert (directory / RECEIPT_NAME).exists()

    report = _run(db_session, source)

    assert report.imported == ["Belgium Grid"]
    assert _rows(db_session)["Belgium Grid"] is not None
    # Completed in place — not copied a second time alongside.
    assert sorted(p.name for p in dest_root.iterdir()) == ["Belgium Grid"]


def test_a_foreign_directory_is_never_written_into(db_session, identity, source, dest_root):
    """
    Never write into a destination it cannot prove it created.

    `taken_names` unions the filesystem, so the allocator steps around the
    foreign directory rather than refusing outright — the user's project still
    arrives, beside the thing that was already there. This mirrors what rename
    does with an occupied directory, for the same reason: refusing costs a
    project, and overwriting costs somebody's data.
    """
    _write_project(source, "Belgium Grid")
    squatter = dest_root / "Belgium Grid"
    squatter.mkdir()
    (squatter / "network.nc").write_text("somebody else's work", encoding="utf-8")

    report = _run(db_session, source)

    assert report.imported == ["Belgium Grid"]
    assert (squatter / "network.nc").read_text(encoding="utf-8") == "somebody else's work"
    row = _rows(db_session)["Belgium Grid"]
    assert project_registry.project_dir(row).name == "Belgium Grid (2)"


def test_a_destination_that_appears_after_allocation_is_refused(
    db_session, identity, source, dest_root, monkeypatch
):
    """
    The explicit `destination.exists()` check, which is the race guard behind
    the allocator. `os.rename` silently succeeds onto an existing EMPTY
    directory on POSIX and raises on Windows (D1 targets both), so it is
    checked here rather than left to the syscall. Reached with a stale `taken`
    set — the same way Task 4 reaches rename's equivalent.
    """
    _write_project(source, "Belgium Grid")
    squatter = dest_root / "Belgium Grid"
    squatter.mkdir()
    (squatter / "network.nc").write_text("somebody else's work", encoding="utf-8")

    from services import legacy_import

    monkeypatch.setattr(legacy_import, "taken_names", lambda *a, **k: set())

    report = _run(db_session, source)

    assert report.imported == []
    assert any("Belgium Grid" in c for c in report.collisions)
    assert (squatter / "network.nc").read_text(encoding="utf-8") == "somebody else's work"
    assert _rows(db_session) == {}


def test_a_verification_failure_leaves_no_partial_destination(
    db_session, identity, source, dest_root, monkeypatch
):
    """Verify BEFORE the rename, so a failed copy never becomes a project."""
    _write_project(source, "Belgium Grid")

    from services import legacy_import

    monkeypatch.setattr(legacy_import, "_verify_copy", lambda *a, **k: "mismatched size")

    report = _run(db_session, source)

    assert report.imported == []
    assert any("Belgium Grid" in f for f in report.failed)
    assert list(dest_root.iterdir()) == []
    assert _rows(db_session) == {}


# ── The row ──────────────────────────────────────────────────────────────────

def test_the_row_name_is_truncated_but_the_directory_is_the_cap(db_session, identity, source, dest_root):
    long_name = "L" * 200
    _write_project(source, long_name)

    _run(db_session, source)

    row = next(iter(_rows(db_session).values()))
    assert len(row.name) <= 64
    assert len(project_registry.project_dir(row).name) <= 96


def test_world_writable_source_modes_are_not_carried_over(db_session, identity, source, dest_root):
    """Three legacy directories are `drwxrwxrwx`. Under `~/Documents` that is
    a permission grant the user never made."""
    directory = _write_project(source, "Wide Open")
    directory.chmod(0o777)

    _run(db_session, source)

    row = _rows(db_session)["Wide Open"]
    mode = project_registry.project_dir(row).stat().st_mode & 0o777
    assert not mode & 0o002, oct(mode)


def test_the_receipt_records_what_was_copied(db_session, identity, source, dest_root):
    _write_project(source, "Belgium Grid")

    _run(db_session, source)

    row = _rows(db_session)["Belgium Grid"]
    receipt = json.loads(
        (project_registry.project_dir(row) / RECEIPT_NAME).read_text(encoding="utf-8")
    )
    assert receipt["source_dir_name"] == "Belgium Grid"
    assert receipt["install_id"] == install_id()
    assert receipt["dest_root"] == str(dest_root)
    names = {entry["path"] for entry in receipt["files"]}
    assert "network.nc" in names
    assert all("sha256" in entry for entry in receipt["files"] if entry["path"] == "network.nc")


# ── Manifest + rollback ──────────────────────────────────────────────────────

def test_rollback_removes_exactly_what_the_run_created(db_session, identity, source, dest_root, tmp_path):
    """
    Rows are identifiable ONLY because the manifest carries their ids.
    `parent_project_id` is `ondelete="SET NULL"`, so cleaning up by hand
    silently flattens the scenario tree instead of failing loudly.
    """
    _write_project(source, "Base")
    _write_project(source, "Variant", parent="Base")
    report = _run(db_session, source)
    manifest = write_manifest(
        report, source_root=source, dest_root=dest_root,
        path=tmp_path / "manifest.json",
    )
    destinations = [project_registry.project_dir(r) for r in _rows(db_session).values()]
    assert all(d.is_dir() for d in destinations)

    removed = rollback(db_session, manifest)

    assert sorted(removed) == ["Base", "Variant"]
    assert _rows(db_session) == {}
    assert not any(d.exists() for d in destinations)
    # The source is untouched — rollback undoes the import, not the projects.
    assert (source / "Base" / "network.nc").exists()


def test_rollback_will_not_delete_a_directory_it_did_not_create(db_session, identity, source, dest_root, tmp_path):
    """Proved by the receipt, not by the path in the manifest — a manifest can
    be edited, and `rmtree` is not something to run on a name alone."""
    _write_project(source, "Belgium Grid")
    report = _run(db_session, source)
    foreign = dest_root / "Not Ours"
    foreign.mkdir()
    (foreign / "network.nc").write_text("somebody else's", encoding="utf-8")
    report.records[0]["destination"] = str(foreign)
    manifest = write_manifest(
        report, source_root=source, dest_root=dest_root,
        path=tmp_path / "manifest.json",
    )

    rollback(db_session, manifest)

    assert (foreign / "network.nc").read_text(encoding="utf-8") == "somebody else's"


# ── --rebase-db ──────────────────────────────────────────────────────────────

def test_rebase_copies_verifies_and_repoints_the_row(db_session, identity, tmp_path, dest_root):
    """
    The only thing that delivers E2 for a row pointing OUTSIDE `projects_root`
    — which 0003 leaves alone by design, and which is the single most valuable
    object in the phase on the machine this was written for.
    """
    outside = tmp_path / "checkout" / "projects" / "860edcb4" / "e8645aba"
    outside.mkdir(parents=True)
    (outside / "network.nc").write_text("irreplaceable", encoding="utf-8")
    row = Project(id=uuid.uuid4(), org_id=_ORG, name="3_nodes_system", created_by=_USER,
                  storage_path=str(outside), created_at=_now(), updated_at=_now())
    db_session.add(row)
    db_session.commit()

    destination = rebase_row(db_session, row)

    assert destination == dest_root / "3_nodes_system"
    assert (destination / "network.nc").read_text(encoding="utf-8") == "irreplaceable"
    assert row.storage_path == "3_nodes_system"
    assert project_registry.project_dir(row) == destination
    # Copied, never moved. `--forget-legacy` is the user's separate decision.
    assert (outside / "network.nc").read_text(encoding="utf-8") == "irreplaceable"


def test_rebase_removes_the_copy_when_verification_fails(db_session, identity, tmp_path, dest_root, monkeypatch):
    outside = tmp_path / "checkout" / "projects" / "old"
    outside.mkdir(parents=True)
    (outside / "network.nc").write_text("irreplaceable", encoding="utf-8")
    row = Project(id=uuid.uuid4(), org_id=_ORG, name="3_nodes_system", created_by=_USER,
                  storage_path=str(outside), created_at=_now(), updated_at=_now())
    db_session.add(row)
    db_session.commit()

    from services import legacy_import

    monkeypatch.setattr(legacy_import, "_verify_copy", lambda *a, **k: "content differs")

    with pytest.raises(OSError):
        rebase_row(db_session, row)

    db_session.refresh(row)
    assert row.storage_path == str(outside)
    assert list(dest_root.iterdir()) == []
    assert (outside / "network.nc").read_text(encoding="utf-8") == "irreplaceable"


def test_rebase_removes_the_copy_when_the_commit_fails(db_session, identity, tmp_path, dest_root, monkeypatch):
    """The source is still authoritative and untouched, so the failure costs
    the copy and nothing else."""
    outside = tmp_path / "checkout" / "projects" / "old"
    outside.mkdir(parents=True)
    (outside / "network.nc").write_text("irreplaceable", encoding="utf-8")
    row = Project(id=uuid.uuid4(), org_id=_ORG, name="3_nodes_system", created_by=_USER,
                  storage_path=str(outside), created_at=_now(), updated_at=_now())
    db_session.add(row)
    db_session.commit()

    real_commit = db_session.commit
    calls = {"n": 0}

    def _explode():
        calls["n"] += 1
        raise RuntimeError("database went away")

    monkeypatch.setattr(db_session, "commit", _explode)

    with pytest.raises(RuntimeError):
        rebase_row(db_session, row)

    monkeypatch.setattr(db_session, "commit", real_commit)
    assert list(dest_root.iterdir()) == []
    assert (outside / "network.nc").read_text(encoding="utf-8") == "irreplaceable"
