# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 07f: the operations that move, copy or remove a whole
directory hold the report locks, or refuse 409 `report_busy` having changed
nothing. Project delete and rename, snapshot delete and prune, the study
option forks, the legacy import and the legacy claim."""

# ruff: noqa: F811 -- fixtures imported from other test modules are parameters here
from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

from services import project_registry
from services.reports import report_job, store
from tests.test_legacy_import import (  # noqa: F401 -- fixtures
    _run,
    _write_project,
    dest_root,
    env,
    identity,
    source,
)
from tests.test_legacy_migrate import (  # noqa: F401 -- fixtures
    _write_legacy_project,
    admin,
    legacy_env,
    legacy_root_dir,
    org,
    session_local,
)
from tests.test_report_copy_lock import RID_A, RID_B, _seed, _tree, held_elsewhere
from tests.test_report_copy_lock_routes import (  # noqa: F401 -- fixture
    _busy,
    _snapshot,
    _with_the_sites_network_lock_held_elsewhere,
    project,
)
from tests.test_storage_layout import (  # noqa: F401 -- fixtures
    _seed_project,
    desktop_layout,
    projects_root,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="flock-based holder")


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 0.2)


@pytest.fixture
def running_job(monkeypatch):
    """Make this process run a report job for the directory given."""

    def start(project_dir):
        monkeypatch.setattr(
            report_job, "_ACTIVE", {"status": "running", "thread": None}
        )
        monkeypatch.setattr(report_job, "_ACTIVE_DIR", project_dir)

    return start


def _lock_files(root) -> list[str]:
    return sorted(
        p.name
        for p in root.rglob("*")
        if p.name.endswith(".lock") or p.name == store.CLOSING_MARKER
    )


def _retired(parent) -> list[str]:
    return [p.name for p in parent.iterdir() if p.name.startswith(".pypsa-deleting-")]


# ── the store: closing a directory (GK B C1) ────────────────────────────────


def test_a_closing_marker_refuses_a_writer_that_shares_no_state_with_the_closer(
    tmp_path,
):
    """The marker is a file, checked under the writer's own file lock, so a
    writer in another process is refused too: nothing in memory says closing."""
    _seed(tmp_path, RID_A)
    (store.reports_dir(tmp_path) / store.CLOSING_MARKER).write_text("1\n")
    with pytest.raises(store.ReportBusy):
        with store.report_lock(tmp_path, RID_A):
            pytest.fail("a writer ran while its project was closing")


def test_a_closing_marker_a_crash_left_goes_stale(tmp_path):
    _seed(tmp_path, RID_A)
    marker = store.reports_dir(tmp_path) / store.CLOSING_MARKER
    marker.write_text("1\n")
    old = time.time() - store.CLOSING_STALE_S - 1
    os.utime(marker, (old, old))
    with store.report_lock(tmp_path, RID_A):
        pass


def test_reports_closed_refuses_every_writer_until_it_ends(tmp_path):
    _seed(tmp_path, RID_A)
    refused = []

    def writer():
        try:
            with store.report_lock(tmp_path, RID_A, timeout=1):
                pass
        except store.ReportBusy:
            refused.append(True)

    with store.reports_closed(tmp_path):
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(5)
        assert refused == [True]
        # No lock file is held while the caller moves the directory.
        with held_elsewhere(tmp_path, RID_A):
            pass
    assert not (store.reports_dir(tmp_path) / store.CLOSING_MARKER).exists()
    with store.report_lock(tmp_path, RID_A):
        pass


def test_reports_closed_waits_for_a_write_in_progress_and_then_refuses(tmp_path):
    _seed(tmp_path, RID_A)
    with held_elsewhere(tmp_path, RID_A):
        with pytest.raises(store.ReportBusy):
            with store.reports_closed(tmp_path):
                pytest.fail("closed while a report was being written")
    assert not (store.reports_dir(tmp_path) / store.CLOSING_MARKER).exists()


def test_a_writer_never_makes_a_project_directory_again(tmp_path):
    gone = tmp_path / "deleted"
    with pytest.raises(store.ReportProjectGone):
        with store.report_lock(gone, RID_A):
            pytest.fail("wrote into a project that is gone")
    assert not gone.exists()


def test_a_job_cannot_start_for_a_directory_that_is_closing(tmp_path):
    """GK B C2: `_launch` asks under the slot lock, so a job either started
    before the close (and `running_for` sees it) or is refused."""
    _seed(tmp_path, RID_A)
    job = SimpleNamespace(
        project_dir=tmp_path, record={"status": "running"}, report_id=RID_A
    )
    with store.reports_closed(tmp_path):
        with pytest.raises(store.ReportBusy):
            report_job._launch(
                prepare=lambda: job,
                run=lambda _job: None,
                lock=threading.Lock(),
                state={},
            )
    assert report_job._ACTIVE is None or report_job._ACTIVE is not job.record
    assert not report_job.running_for(tmp_path)


def test_running_for_names_only_the_jobs_own_project(tmp_path, running_job):
    running_job(tmp_path / "a")
    assert report_job.running_for(tmp_path / "a")
    assert not report_job.running_for(tmp_path / "b")


# ── project delete (criterion 1) ────────────────────────────────────────────


def test_delete_refuses_while_a_report_is_being_written(client, project):
    name, pdir = project
    with held_elsewhere(pdir, RID_A):
        _busy(client.delete(f"/api/projects/{name}"))
    assert (pdir / "network.nc").exists()
    assert sorted(p.name for p in store.reports_dir(pdir).iterdir() if p.is_dir()) == [
        RID_A,
        RID_B,
    ]
    assert not (store.reports_dir(pdir) / store.CLOSING_MARKER).exists()


def test_delete_refuses_while_a_report_job_runs_for_it(client, project, running_job):
    name, pdir = project
    running_job(pdir)
    resp = client.delete(f"/api/projects/{name}")
    _busy(resp)
    assert "Abort it in Reports" in resp.json()["detail"]["message"]
    assert (pdir / "network.nc").exists()
    assert not (store.reports_dir(pdir) / store.CLOSING_MARKER).exists()


def test_after_a_delete_no_report_comes_back(client, project):
    name, pdir = project
    resp = client.delete(f"/api/projects/{name}")
    assert resp.status_code == 200, resp.text
    assert not pdir.exists()
    assert _retired(pdir.parent) == []
    # A writer that outlived the delete (another process, or a job that
    # missed it) cannot make `reports/<id>/` again.
    with pytest.raises(store.ReportProjectGone):
        with store.report_lock(pdir, RID_A):
            pass
    assert not pdir.exists()


def test_a_writer_arriving_while_the_tree_is_removed_finds_no_project(
    client, project, monkeypatch
):
    """The directory is moved aside before anything in it is removed, so a
    writer that arrives mid-removal cannot rebuild `reports/<id>/` under a
    half-removed tree."""
    from routers import projects

    name, pdir = project
    seen = []
    real = projects._force_rmtree

    def removing(target):
        try:
            with store.report_lock(pdir, RID_A, timeout=1):
                seen.append("wrote")
        except store.ReportStoreError as exc:
            seen.append(type(exc).__name__)
        real(target)

    monkeypatch.setattr(projects, "_force_rmtree", removing)
    assert client.delete(f"/api/projects/{name}").status_code == 200
    assert seen == ["ReportProjectGone"]
    assert not pdir.exists()


def _network_lock_free_elsewhere() -> bool:
    """Whether another thread can take the network lock right now."""
    from services.pypsa_service import PyPSAService

    lock, got = PyPSAService.get_lock(), []

    def probe():
        if lock.acquire(timeout=0.5):
            got.append(True)
            lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(5)
    return got == [True]


@pytest.mark.parametrize("what", ["project", "snapshot"])
def test_the_moved_tree_is_removed_outside_the_network_lock(
    client, project, monkeypatch, what
):
    """GK B C4: only the move holds the network lock; removing a large tree
    after it must not keep the lock's 5 s waiters out."""
    from routers import projects

    name, pdir = project
    url = f"/api/projects/{name}"
    if what == "snapshot":
        url += f"/snapshots/{_snapshot(client, name)}"
    free = []
    real = projects._force_rmtree

    def removing(target):
        free.append(_network_lock_free_elsewhere())
        real(target)

    monkeypatch.setattr(projects, "_force_rmtree", removing)
    assert client.delete(url).status_code in (200, 204)
    assert free == [True]


def test_delete_takes_the_network_lock_before_any_report_lock(
    client, project, monkeypatch
):
    """GK B C3: while another thread holds the network lock, the delete waits
    for it holding no report lock, so a report writer still gets its lock."""
    from services.solve_queue import solve_queue

    name, pdir = project
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    resp = _with_the_sites_network_lock_held_elsewhere(
        monkeypatch,
        solve_queue,
        "list_jobs",
        lambda: client.delete(f"/api/projects/{name}"),
        pdir,
    )
    assert resp.status_code == 200, resp.text
    assert not pdir.exists()


# ── project rename (criterion 1) ────────────────────────────────────────────


@pytest.fixture
def movable(db_session, projects_root, desktop_layout):
    project_row = _seed_project(db_session, name="Old Name", storage_path="Old Name")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("payload", encoding="utf-8")
    _seed(old_dir, RID_A)
    return db_session, project_row, old_dir, projects_root / "New Name"


def test_rename_refuses_while_a_report_is_being_written(movable):
    from fastapi import HTTPException

    db, row, old_dir, new_dir = movable
    with held_elsewhere(old_dir, RID_A):
        with pytest.raises(HTTPException) as caught:
            project_registry.rename_project(db, row, "New Name")
    assert caught.value.status_code == 409
    assert caught.value.detail["error_kind"] == "report_busy"
    db.refresh(row)
    assert (row.name, row.storage_path) == ("Old Name", "Old Name")
    assert old_dir.exists() and not new_dir.exists()
    assert not (store.reports_dir(old_dir) / store.CLOSING_MARKER).exists()


def test_rename_refuses_while_a_report_job_runs_for_it(movable, running_job):
    from fastapi import HTTPException

    db, row, old_dir, new_dir = movable
    running_job(old_dir)
    with pytest.raises(HTTPException) as caught:
        project_registry.rename_project(db, row, "New Name")
    assert caught.value.detail["error_kind"] == "report_busy"
    db.refresh(row)
    assert row.name == "Old Name"
    assert old_dir.exists() and not new_dir.exists()


def test_rename_moves_the_reports_and_leaves_no_marker(movable):
    db, row, old_dir, new_dir = movable
    project_registry.rename_project(db, row, "New Name")
    assert not old_dir.exists()
    assert (store.reports_dir(new_dir) / RID_A / "v1.json").exists()
    assert not (store.reports_dir(new_dir) / store.CLOSING_MARKER).exists()
    with store.report_lock(new_dir, RID_A):
        pass
    with pytest.raises(store.ReportProjectGone):
        with store.report_lock(old_dir, RID_A):
            pass
    assert not old_dir.exists()


def test_rename_takes_the_network_lock_before_any_report_lock(movable, monkeypatch):
    db, row, old_dir, new_dir = movable
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    _with_the_sites_network_lock_held_elsewhere(
        monkeypatch,
        project_registry,
        "_queued_job_blocks_rename",
        lambda: project_registry.rename_project(db, row, "New Name"),
        old_dir,
    )
    assert new_dir.exists() and not old_dir.exists()


# ── snapshot delete and prune (criterion 2) ─────────────────────────────────


def test_snapshot_delete_refuses_while_a_restore_reads_it(client, project):
    name, pdir = project
    sid = _snapshot(client, name)
    snap_dir = pdir / "snapshots" / sid
    with held_elsewhere(snap_dir, RID_A):
        _busy(client.delete(f"/api/projects/{name}/snapshots/{sid}"))
    assert (store.reports_dir(snap_dir) / RID_A / "v1.json").exists()
    assert client.delete(f"/api/projects/{name}/snapshots/{sid}").status_code == 204
    assert not snap_dir.exists()
    assert _retired(pdir / "snapshots") == []


def _fake_snapshots(pdir, count: int) -> list:
    dirs = []
    for i in range(count):
        d = pdir / "snapshots" / f"2026-01-01T00-00-{i:02d}_s"
        d.mkdir(parents=True)
        _seed(d, RID_A)
        dirs.append(d)
    return dirs


def test_prune_skips_a_snapshot_a_restore_reads_and_takes_the_next(
    tmp_path, monkeypatch
):
    from routers import snapshots

    monkeypatch.setattr(snapshots, "_MAX_SNAPSHOTS_PER_PROJECT", 3)
    oldest, second, third = _fake_snapshots(tmp_path, 3)
    with held_elsewhere(oldest, RID_A):
        assert snapshots._prune_oldest(tmp_path) == 1
    assert oldest.exists() and not second.exists() and third.exists()


def test_a_busy_snapshot_never_lets_the_count_grow(tmp_path, monkeypatch):
    """GK B C5: one prune per create, with the oldest snapshot busy the whole
    time; the count stays at the cap minus one."""
    from routers import snapshots

    monkeypatch.setattr(snapshots, "_MAX_SNAPSHOTS_PER_PROJECT", 3)
    oldest, *_ = _fake_snapshots(tmp_path, 3)
    with held_elsewhere(oldest, RID_A):
        for i in range(5):
            snapshots._prune_oldest(tmp_path)
            assert len(snapshots._list_snapshot_dirs(tmp_path)) == 2
            new = tmp_path / "snapshots" / f"2026-01-02T00-00-{i:02d}_s"
            new.mkdir()
    assert oldest.exists()


def test_a_retired_directory_is_never_listed_as_a_snapshot(tmp_path):
    from routers import snapshots

    (tmp_path / "snapshots" / ".pypsa-deleting-abc").mkdir(parents=True)
    assert snapshots._list_snapshot_dirs(tmp_path) == []


def test_snapshot_delete_takes_the_network_lock_before_any_report_lock(
    client, project, monkeypatch
):
    from routers import snapshots

    name, pdir = project
    sid = _snapshot(client, name)
    snap_dir = pdir / "snapshots" / sid
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    resp = _with_the_sites_network_lock_held_elsewhere(
        monkeypatch,
        snapshots,
        "_read_snapshot_meta",
        lambda: client.delete(f"/api/projects/{name}/snapshots/{sid}"),
        snap_dir,
    )
    assert resp.status_code == 204, resp.text


# ── study option forks (criterion 3) ────────────────────────────────────────


def test_a_fork_carries_the_reports_and_no_lock_file(tmp_path):
    from services.study import forks

    src, dst = tmp_path / "base", tmp_path / "fork"
    _seed(src, RID_A)
    _seed(src, RID_B)
    for rid in (RID_A, RID_B):
        # A base's lock file, marked so a copy of it shows.
        (store.reports_dir(src) / f".{rid}.lock").write_bytes(b"base")
    dst.mkdir()
    forks._copy_walk(src, dst)
    assert _tree(store.reports_dir(dst)) == _tree(store.reports_dir(src))
    # Taking the fork's own locks leaves it empty lock files, as a snapshot
    # gets (07e); none of the base's travels.
    copied = [
        p
        for p in store.reports_dir(dst).iterdir()
        if p.name.endswith(".lock") and p.read_bytes() == b"base"
    ]
    assert copied == []


def test_a_fork_refuses_while_a_base_report_is_being_written(tmp_path):
    from services.study import forks

    src, dst = tmp_path / "base", tmp_path / "fork"
    _seed(src, RID_A)
    dst.mkdir()
    with held_elsewhere(src, RID_A):
        with pytest.raises(forks.ForkError) as caught:
            forks._copy_walk(src, dst)
    assert caught.value.code == "report_busy"
    assert not store.reports_dir(dst).exists()


# ── the legacy import and claim ─────────────────────────────────────────────


def test_a_legacy_import_copies_the_reports_and_no_lock_file(
    db_session, identity, source, dest_root
):
    pdir = _write_project(source, "Old Grid")
    _seed(pdir, RID_A)
    reports = store.reports_dir(pdir)
    (reports / f".{RID_A}.lock").write_bytes(b"")
    (reports / store.CLOSING_MARKER).write_text("1\n")

    report = _run(db_session, source)

    assert report.imported == ["Old Grid"], report.failed
    copied = store.reports_dir(dest_root / "Old Grid")
    assert (copied / RID_A / "v1.json").exists()
    assert _lock_files(copied) == []
    assert (reports / f".{RID_A}.lock").exists()  # the original is untouched


def test_a_legacy_claim_refuses_while_a_report_is_being_written(
    session_local, org, admin, legacy_root_dir
):
    from services.legacy_migrate import claim_legacy_project

    _write_legacy_project(legacy_root_dir, "Root")
    _seed(legacy_root_dir / "Root", RID_A)
    with session_local() as db:
        with held_elsewhere(legacy_root_dir / "Root", RID_A):
            with pytest.raises(store.ReportBusy):
                claim_legacy_project(
                    db, "Root", org.id, admin.id, [], include_descendants=True
                )
    assert (legacy_root_dir / "Root" / "network.nc").exists()
    assert not (
        store.reports_dir(legacy_root_dir / "Root") / store.CLOSING_MARKER
    ).exists()


def test_a_legacy_claim_moves_the_reports_and_leaves_no_marker(
    session_local, org, admin, legacy_root_dir
):
    from services.legacy_migrate import claim_legacy_project

    _write_legacy_project(legacy_root_dir, "Root")
    _seed(legacy_root_dir / "Root", RID_A)
    with session_local() as db:
        result = claim_legacy_project(
            db, "Root", org.id, admin.id, [], include_descendants=True
        )
        moved = project_registry.project_dir(result.root)
    assert not (legacy_root_dir / "Root").exists()
    assert (store.reports_dir(moved) / RID_A / "v1.json").exists()
    assert not (store.reports_dir(moved) / store.CLOSING_MARKER).exists()


def _outside_row(db_session, tmp_path):
    import uuid

    from db.models import Project
    from tests.test_legacy_import import _ORG, _USER, _now

    outside = tmp_path / "checkout" / "e8645aba"
    outside.mkdir(parents=True)
    (outside / "network.nc").write_text("irreplaceable", encoding="utf-8")
    _seed(outside, RID_A)
    row = Project(
        id=uuid.uuid4(),
        org_id=_ORG,
        name="3_nodes_system",
        created_by=_USER,
        storage_path=str(outside),
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(row)
    db_session.commit()
    return row, outside


def test_a_rebase_refuses_while_a_report_of_the_row_is_being_written(
    db_session, identity, tmp_path, dest_root
):
    """`rebase_row` copies a registry project, which a running app can write."""
    from services.legacy_import import rebase_row

    row, outside = _outside_row(db_session, tmp_path)
    with held_elsewhere(outside, RID_A):
        with pytest.raises(store.ReportBusy):
            rebase_row(db_session, row)
    assert not (dest_root / "3_nodes_system").exists()
    assert row.storage_path == str(outside)
    assert not (store.reports_dir(outside) / store.CLOSING_MARKER).exists()


def test_a_rebase_copies_the_reports_and_no_lock_file(
    db_session, identity, tmp_path, dest_root
):
    from services.legacy_import import rebase_row

    row, outside = _outside_row(db_session, tmp_path)
    destination = rebase_row(db_session, row)
    assert (store.reports_dir(destination) / RID_A / "v1.json").exists()
    assert _lock_files(destination) == []
    assert not (store.reports_dir(outside) / store.CLOSING_MARKER).exists()
