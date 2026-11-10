# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 07e, at the routes: every bundle copy of `reports/` holds
the report locks, and one that cannot get them refuses 409 `report_busy`
having written nothing."""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from services import project_registry
from services.reports import store
from tests.test_report_copy_lock import RID_A, RID_B, _seed, _tree, held_elsewhere

pytestmark = pytest.mark.skipif(os.name == "nt", reason="flock-based holder")


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 0.2)


def _busy(resp) -> None:
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error_kind"] == "report_busy"


def _leftovers(project_dir) -> list[str]:
    return [
        p.name for p in project_dir.rglob("*") if ".tmp-" in p.name or ".old-" in p.name
    ]


@pytest.fixture
def project(client, api_project, project_storage_dir):
    """A saved project with two reports; returns (name, directory)."""
    name = api_project("lockme")
    pdir = project_storage_dir(name)
    _seed(pdir, RID_A)
    _seed(pdir, RID_B)
    return name, pdir


def _snapshot(client, name) -> str:
    resp = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _snapshot_ids(pdir) -> list[str]:
    snaps = pdir / "snapshots"
    return sorted(p.name for p in snaps.iterdir()) if snaps.exists() else []


# ── snapshot restore (criterion 1) ──────────────────────────────────────────


def test_restore_refuses_while_a_report_is_being_written(client, project):
    name, pdir = project
    snap_id = _snapshot(client, name)
    (pdir / "reports" / RID_A / "v2.json").write_text("an address in flight")
    (pdir / "layout.json").write_text('{"edited": "after the snapshot"}')
    before, snaps = _tree(pdir), _snapshot_ids(pdir)
    with held_elsewhere(pdir, RID_A):
        _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))
        assert _tree(pdir) == before  # reports, bundle files and uploads alike
        assert _snapshot_ids(pdir) == snaps  # not even the safety snapshot
    assert _leftovers(pdir) == []


def test_restore_runs_once_the_report_is_free_and_keeps_the_lock_files(client, project):
    name, pdir = project
    snap_id = _snapshot(client, name)
    (pdir / "reports" / RID_A / "v2.json").write_text("written after the snapshot")
    _seed(pdir, "00000000000000cc", "made after the snapshot")
    with held_elsewhere(pdir, RID_A):
        pass
    inode = (pdir / "reports" / f".{RID_A}.lock").stat().st_ino
    resp = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert resp.status_code == 200, resp.text
    reports = pdir / "reports"
    assert sorted(p.name for p in reports.iterdir() if p.is_dir()) == [RID_A, RID_B]
    assert not (reports / RID_A / "v2.json").exists()
    assert (reports / f".{RID_A}.lock").stat().st_ino == inode
    assert _leftovers(pdir) == []


def test_restore_to_a_state_without_reports_removes_them_report_by_report(
    client, api_project, project_storage_dir
):
    name = api_project("noreports")
    pdir = project_storage_dir(name)
    snap_id = _snapshot(client, name)
    _seed(pdir, RID_A)
    with held_elsewhere(pdir, RID_A):
        _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))
        assert (pdir / "reports" / RID_A / "meta.json").exists()
    resp = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert resp.status_code == 200, resp.text
    assert [p.name for p in (pdir / "reports").iterdir()] == [f".{RID_A}.lock"]


# ── snapshot create (criterion 3) ───────────────────────────────────────────


def test_snapshot_create_refuses_and_leaves_no_snapshot(client, project):
    name, pdir = project
    with held_elsewhere(pdir, RID_B):
        _busy(client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"}))
    assert _snapshot_ids(pdir) == []


def test_a_snapshot_holds_each_report_while_it_copies_it(client, project, monkeypatch):
    """The copy of a report's files runs with that report's lock held."""
    name, pdir = project
    seen = []
    real = store.shutil.copytree

    def spy(source, target, *a, **k):
        with (
            pytest.raises(BlockingIOError),
            held_elsewhere(pdir, os.path.basename(source)),
        ):
            pass
        seen.append(os.path.basename(source))
        return real(source, target, *a, **k)

    monkeypatch.setattr(store.shutil, "copytree", spy)
    snap_id = _snapshot(client, name)
    monkeypatch.undo()
    assert seen == [RID_A, RID_B]
    snap_reports = pdir / "snapshots" / snap_id / "reports"
    assert _tree(snap_reports) == _tree(pdir / "reports")
    assert sorted(p.name for p in snap_reports.iterdir() if p.is_dir()) == [
        RID_A,
        RID_B,
    ]


# ── save-as, save-a-copy, scenario (criterion 2) ────────────────────────────


@pytest.mark.parametrize("rebind", [True, False], ids=["save-as", "save-a-copy"])
def test_a_save_to_another_name_refuses_and_writes_nothing(
    client, project, project_row, rebind
):
    name, pdir = project
    with held_elsewhere(pdir, RID_A):
        _busy(client.post("/api/projects/lockme_copy", params={"rebind": rebind}))
    assert project_row("lockme_copy") is None  # no row for the retry to trip on


@pytest.mark.parametrize("rebind", [True, False], ids=["save-as", "save-a-copy"])
def test_a_save_to_another_name_carries_the_reports(
    client, project, project_storage_dir, rebind
):
    name, pdir = project
    resp = client.post("/api/projects/lockme_copy", params={"rebind": rebind})
    assert resp.status_code == 200, resp.text
    copy = project_storage_dir("lockme_copy")
    assert _tree(copy / "reports") == _tree(pdir / "reports")
    assert [
        p.name
        for p in (copy / "reports").iterdir()
        if p.is_file() and p.name not in (f".{RID_A}.lock", f".{RID_B}.lock")
    ] == []


def test_a_scenario_refuses_and_creates_nothing(client, project, project_row):
    name, pdir = project
    with held_elsewhere(pdir, RID_A):
        _busy(
            client.post(
                f"/api/projects/{name}/scenarios",
                json={"name": "lockme_child", "description": "x"},
            )
        )
    assert project_row("lockme_child") is None


def test_a_scenario_carries_the_reports(client, project, project_storage_dir):
    name, pdir = project
    resp = client.post(
        f"/api/projects/{name}/scenarios",
        json={"name": "lockme_child", "description": "x"},
    )
    assert resp.status_code in (200, 201), resp.text
    assert _tree(project_storage_dir("lockme_child") / "reports") == _tree(
        pdir / "reports"
    )


# ── bundle export and import (criterion 2) ──────────────────────────────────


def _bundle(client, name) -> bytes:
    resp = client.get(f"/api/projects/{name}/bundle")
    assert resp.status_code == 200, resp.text
    return resp.content


def _import(client, data: bytes, name="lockme_imported"):
    return client.post(
        f"/api/projects/import_bundle?name={name}",
        files={"file": ("b.pypsaproj.zip", data, "application/zip")},
    )


def _with_member(data: bytes, member: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.namelist():
            dst.writestr(item, src.read(item))
        dst.writestr(member, b"x")
    return out.getvalue()


def test_export_refuses_while_a_report_is_being_written(client, project):
    name, pdir = project
    with held_elsewhere(pdir, RID_A):
        _busy(client.get(f"/api/projects/{name}/bundle"))


def test_export_carries_the_reports_and_no_lock_file(client, project):
    name, pdir = project
    with held_elsewhere(pdir, RID_A):
        pass  # leaves `.<id>.lock` behind
    members = zipfile.ZipFile(io.BytesIO(_bundle(client, name))).namelist()
    reports = sorted(m for m in members if m.startswith("reports/"))
    assert reports == sorted(f"reports/{k}" for k in _tree(pdir / "reports"))


def test_import_refuses_while_a_report_lock_is_held(
    client, project, project_row, monkeypatch
):
    name, _pdir = project
    data = _bundle(client, name)
    real = project_registry.ensure_project_dir
    made, holds = [], []

    def ensure_then_hold(row):
        dest = real(row)
        made.append(dest)
        hold = held_elsewhere(dest, RID_A)
        hold.__enter__()
        holds.append(hold)
        return dest

    monkeypatch.setattr(project_registry, "ensure_project_dir", ensure_then_hold)
    try:
        _busy(_import(client, data))
    finally:
        for hold in holds:
            hold.__exit__(None, None, None)
    assert _tree(made[0]) == {}  # no bundle file, upload or report was written
    assert _leftovers(made[0]) == []


def test_import_restores_the_reports_and_skips_a_bundled_lock_file(
    client, project, project_storage_dir
):
    name, pdir = project
    data = _with_member(_bundle(client, name), f"reports/.{RID_A}.lock")
    resp = _import(client, data)
    assert resp.status_code in (200, 201), resp.text
    imported = project_storage_dir(resp.json().get("name") or "lockme_imported")
    assert _tree(imported / "reports") == _tree(pdir / "reports")
    assert (imported / "reports" / f".{RID_A}.lock").read_bytes() == b""


@pytest.mark.parametrize(
    "member",
    [
        "reports/../x/meta.json",
        "reports/.x/meta.json",
        "reports/.x",
        "reports/0123/meta.json",
        f"reports/{RID_A.upper()}/meta.json",
    ],
)
def test_import_refuses_a_bundle_whose_reports_name_no_report(
    client, project, project_row, member
):
    """The ids name lock files and directories, and the archive is untrusted
    (GK B L3)."""
    name, _pdir = project
    resp = _import(client, _with_member(_bundle(client, name), member))
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_report_id"
    assert project_row("lockme_imported") is None


# ── the backstop ────────────────────────────────────────────────────────────


def test_copy_bundle_dirs_takes_the_locks_before_it_touches_any_folder(tmp_path):
    """A caller without a route's hold still cannot interleave: busy is
    raised, not swallowed, before `uploads/` or any other folder is replaced."""
    from routers.projects import _copy_bundle_dirs

    src, dst = tmp_path / "src", tmp_path / "dst"
    for project_dir, body in ((src, "new"), (dst, "old")):
        _seed(project_dir, RID_A, body)
        (project_dir / "uploads").mkdir()
        (project_dir / "uploads" / "note.txt").write_text(body)
    before = _tree(dst)
    with held_elsewhere(dst, RID_A), pytest.raises(store.ReportBusy):
        _copy_bundle_dirs(src, dst)
    assert _tree(dst) == before
    _copy_bundle_dirs(src, dst)
    assert _tree(dst) == _tree(src)


def test_restore_itself_takes_the_locks_before_its_first_write(
    client, project, monkeypatch
):
    """Without the safety snapshot's own refusal in front of it."""
    from routers import snapshots

    name, pdir = project
    snap_id = _snapshot(client, name)
    (pdir / "reports" / RID_A / "v2.json").write_text("an address in flight")
    (pdir / "layout.json").write_text('{"edited": "after the snapshot"}')
    before = _tree(pdir)
    monkeypatch.setattr(snapshots, "_create_snapshot_internal", lambda *a, **k: None)
    with held_elsewhere(pdir, RID_A):
        _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))
        assert _tree(pdir) == before
    assert _leftovers(pdir) == []


def test_an_export_with_a_stray_reports_file_still_imports(
    client, project, project_storage_dir
):
    name, pdir = project
    (pdir / "reports" / ".DS_Store").write_text("stray")
    data = _bundle(client, name)
    assert "reports/.DS_Store" not in zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert _import(client, data).status_code in (200, 201)


def _raise_busy(*_a, **_k):
    raise store.ReportBusy("a report the route's hold did not cover")


def test_a_busy_backstop_is_never_a_silent_skip_in_a_snapshot(
    client, project, monkeypatch
):
    from routers import projects

    name, _pdir = project
    monkeypatch.setattr(projects, "_copy_bundle_dirs", _raise_busy)
    _busy(client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"}))


def test_a_busy_backstop_is_never_a_silent_skip_in_a_restore(
    client, project, monkeypatch
):
    from routers import projects, snapshots

    name, _pdir = project
    snap_id = _snapshot(client, name)
    monkeypatch.setattr(snapshots, "_create_snapshot_internal", lambda *a, **k: None)
    monkeypatch.setattr(projects, "_copy_bundle_dirs", _raise_busy)
    _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))


def test_the_save_itself_takes_the_locks_before_it_writes(
    client, project, project_row, monkeypatch
):
    """Without the route's early refusal in front of it."""
    from routers import projects

    name, pdir = project
    monkeypatch.setattr(projects, "_refuse_copy_of_busy_reports", lambda *a, **k: None)
    with held_elsewhere(pdir, RID_A):
        _busy(client.post("/api/projects/lockme_copy", params={"rebind": True}))
    row = project_row("lockme_copy")
    assert not (project_registry.project_dir(row) / "network.nc").exists()


def test_a_busy_backstop_is_never_a_silent_skip_in_a_save_as(
    client, project, monkeypatch
):
    from routers import projects

    monkeypatch.setattr(projects, "_copy_bundle_dirs", _raise_busy)
    _busy(client.post("/api/projects/lockme_copy", params={"rebind": True}))


def test_a_busy_safety_snapshot_refuses_the_restore_as_report_busy(
    client, project, monkeypatch
):
    """A writer that arrives after the restore's early check: still 409, not
    the 500 other safety-snapshot failures become."""
    from routers import reports, snapshots

    name, _pdir = project
    snap_id = _snapshot(client, name)

    def busy(*_a, **_k):
        raise reports._http(store.ReportBusy("busy"))

    monkeypatch.setattr(snapshots, "_create_snapshot_internal", busy)
    _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))


def test_restore_refuses_before_it_saves_unsaved_edits(client, project, monkeypatch):
    from routers import snapshots

    name, pdir = project
    snap_id = _snapshot(client, name)
    saved = []
    monkeypatch.setattr(
        snapshots, "_save_unsaved_live_state", lambda *a, **k: saved.append(1)
    )
    with held_elsewhere(pdir, RID_A):
        _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))
    assert saved == []
