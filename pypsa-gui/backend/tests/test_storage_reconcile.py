"""
Reconcile storage against the database (phase 1b, Task 6 — spec E3/E4).

Read-only by construction. `scan` never creates, moves or deletes anything;
`sweep_tmp` is the only function that removes, it removes only `*.tmp`, and
only when explicitly asked.

**Declared deviation from E3.** The spec says "on startup, import orphan
directories". This is a manual, opt-in CLI instead. Auto-importing a directory
at boot is the same class of action as overwriting a live project: it fires on
exactly the ambiguous state where the app cannot know the user's intent, and
the cost of guessing wrong is somebody's data. Task 9 must not mark E3
delivered as written.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from db.models import Organization, Project, User
from services.storage_reconcile import ReconcileReport, scan, sweep_tmp
from settings import get_settings

_ORG = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def root(tmp_path, monkeypatch):
    projects = tmp_path / "Projects"
    projects.mkdir()
    monkeypatch.setenv("PROJECTS_ROOT", str(projects))
    get_settings.cache_clear()
    yield projects
    get_settings.cache_clear()


def _seed_row(db, *, name: str, storage_path: str) -> Project:
    if db.get(Organization, _ORG) is None:
        db.add(Organization(id=_ORG, name="Org", created_at=_now()))
        db.flush()
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com",
                password_hash=None, status="active", created_at=_now())
    db.add(user)
    db.flush()
    project = Project(
        id=uuid.uuid4(), org_id=_ORG, name=name, created_by=user.id,
        storage_path=storage_path, created_at=_now(), updated_at=_now(),
    )
    db.add(project)
    db.commit()
    return project


def _project_dir(root, name: str, *, network=True):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    if network:
        (directory / "network.nc").write_text("network", encoding="utf-8")
    return directory


# ── Orphans ──────────────────────────────────────────────────────────────────

def test_a_directory_with_a_network_and_no_row_is_an_orphan(db_session, root):
    _project_dir(root, "Abandoned Study")
    report = scan(db_session, root, org_segment=False)
    assert [p.name for p in report.orphan_dirs] == ["Abandoned Study"]


def test_a_directory_without_a_network_is_not_an_orphan(db_session, root):
    """
    E3's premise is that an orphan holding a `network.nc` is a project worth
    adopting. A bare directory is just a directory — reporting it invites the
    user to "recover" something that was never a project.
    """
    _project_dir(root, "Just A Folder", network=False)
    report = scan(db_session, root, org_segment=False)
    assert report.orphan_dirs == []


def test_a_directory_with_a_row_is_not_an_orphan(db_session, root):
    _seed_row(db_session, name="Belgium Grid", storage_path="Belgium Grid")
    _project_dir(root, "Belgium Grid")
    report = scan(db_session, root, org_segment=False)
    assert report.orphan_dirs == []


def test_staging_directories_are_skipped(db_session, root):
    """
    Task 7 stages an import into `<dest>.importing/`, which lives under the
    projects root and contains a `network.nc`. Classifying it as an orphan
    invites adopting a copy that has not been verified yet — a partial one, if
    the copy is still running.
    """
    _project_dir(root, "Belgium Grid.importing")
    report = scan(db_session, root, org_segment=False)
    assert report.orphan_dirs == []


def test_orphans_are_found_one_level_down_when_the_org_segment_is_on(db_session, root):
    """
    With `org_segment=True` the projects are at depth 2. A depth-1 scan finds
    the org UUID directories, decides they hold no `network.nc`, and reports a
    clean tree that is not clean.
    """
    _project_dir(root / str(_ORG), "Abandoned Study")
    assert scan(db_session, root, org_segment=False).orphan_dirs == []
    assert [p.name for p in scan(db_session, root, org_segment=True).orphan_dirs] == [
        "Abandoned Study"
    ]


# ── Missing directories ──────────────────────────────────────────────────────

def test_a_row_whose_directory_is_gone_is_reported(db_session, root):
    """
    Only reachable because Task 3 stopped `project_dir` from mkdir-ing. While
    it created on read, listing projects resurrected the directory and this
    state could not be observed.
    """
    project = _seed_row(db_session, name="Deleted In Finder", storage_path="Deleted In Finder")
    report = scan(db_session, root, org_segment=False)
    assert [row.id for row in report.missing_dirs] == [project.id]


def test_a_row_whose_directory_exists_is_not_reported(db_session, root):
    _seed_row(db_session, name="Belgium Grid", storage_path="Belgium Grid")
    _project_dir(root, "Belgium Grid")
    assert scan(db_session, root, org_segment=False).missing_dirs == []


# ── Stale .tmp ───────────────────────────────────────────────────────────────

def test_a_leftover_tmp_is_listed_but_not_touched(db_session, root):
    """
    `atomic_io` unlinks its tmp on an exception, so a surviving one means the
    process was KILLED mid-write. `scan` is read-only — it says so and leaves
    the evidence in place.
    """
    directory = _project_dir(root, "Belgium Grid")
    stale = directory / "network.nc.tmp"
    stale.write_text("half a network", encoding="utf-8")

    report = scan(db_session, root, org_segment=False)

    assert report.stale_tmp == [stale]
    assert stale.exists()
    assert stale.read_text(encoding="utf-8") == "half a network"


def test_a_clean_tree_reports_nothing(db_session, root):
    _seed_row(db_session, name="Belgium Grid", storage_path="Belgium Grid")
    _project_dir(root, "Belgium Grid")
    report = scan(db_session, root, org_segment=False)
    assert report == ReconcileReport(orphan_dirs=[], missing_dirs=[], stale_tmp=[])
    assert not report.has_findings


def test_a_missing_root_is_not_an_error(db_session, tmp_path):
    """First run, before anything has been saved."""
    report = scan(db_session, tmp_path / "not-yet", org_segment=False)
    assert report == ReconcileReport(orphan_dirs=[], missing_dirs=[], stale_tmp=[])


# ── sweep_tmp ────────────────────────────────────────────────────────────────

def test_sweep_removes_only_the_tmp_files(db_session, root):
    directory = _project_dir(root, "Belgium Grid")
    stale = directory / "network.nc.tmp"
    stale.write_text("half", encoding="utf-8")

    removed = sweep_tmp(scan(db_session, root, org_segment=False))

    assert removed == [stale]
    assert not stale.exists()
    assert (directory / "network.nc").read_text(encoding="utf-8") == "network"


def test_sweep_never_runs_as_part_of_scan(db_session, root):
    """Two functions on purpose. A scan that deletes cannot be run to find out
    what is wrong before deciding what to do about it."""
    directory = _project_dir(root, "Belgium Grid")
    (directory / "network.nc.tmp").write_text("half", encoding="utf-8")

    scan(db_session, root, org_segment=False)
    scan(db_session, root, org_segment=False)

    assert (directory / "network.nc.tmp").exists()


def test_sweep_leaves_orphans_and_missing_rows_alone(db_session, root):
    """`sweep_tmp` takes the whole report but must act on ONE field of it.
    Deleting an orphan directory is the outcome Task 0's backup exists for."""
    orphan = _project_dir(root, "Abandoned Study")
    _seed_row(db_session, name="Gone", storage_path="Gone")

    report = scan(db_session, root, org_segment=False)
    assert report.orphan_dirs and report.missing_dirs
    assert sweep_tmp(report) == []

    assert (orphan / "network.nc").exists()
