# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 07e, at the routes: every bundle copy of `reports/` holds
the report locks, and one that cannot get them refuses 409 `report_busy`
having written nothing."""

from __future__ import annotations

import io
import os
import threading
import time
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


def test_restore_refuses_a_writer_that_arrived_after_its_early_check(
    client, project, monkeypatch
):
    """The restore's own hold covers the live project's reports, not only the
    snapshot's: the writer takes its lock where the safety snapshot runs."""
    import contextlib

    from routers import snapshots

    name, pdir = project
    snap_id = _snapshot(client, name)
    (pdir / "layout.json").write_text('{"edited": "after the snapshot"}')
    seen = {}

    with contextlib.ExitStack() as writer:
        def arrive(*_a, **_k):
            seen["before"] = _tree(pdir)
            writer.enter_context(held_elsewhere(pdir, RID_A))

        monkeypatch.setattr(snapshots, "_create_snapshot_internal", arrive)
        _busy(client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"))
        assert _tree(pdir) == seen["before"]
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


def test_a_save_over_another_project_locks_that_projects_reports_first(
    client, project, project_storage_dir
):
    """The destination's reports are held before the save writes to it."""
    resp = client.post("/api/projects/lockme_copy", params={"rebind": False})
    assert resp.status_code == 200, resp.text
    copy = project_storage_dir("lockme_copy")
    (copy / "network.nc").write_bytes(b"the copy as it was")
    before = _tree(copy)
    with held_elsewhere(copy, RID_A):
        _busy(client.post("/api/projects/lockme_copy", params={"rebind": False}))
        assert _tree(copy) == before
    assert _leftovers(copy) == []


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


# ── lock order at the sites that hold the network lock (GK B F1) ────────────


def _with_the_sites_network_lock_held_elsewhere(
    monkeypatch, module, site, call, pdir, lock_of=None
):
    """Run `call`; when it reaches `module.site`, another thread takes the
    network lock that request uses and keeps it. A report writer must then
    still get its lock at once: the site waits for the network lock FIRST,
    holding no report lock. Returns what `call` returned once the lock is
    freed."""
    from services.pypsa_service import PyPSAService

    lock_of = lock_of or (lambda *a, **k: PyPSAService.get_lock())
    locks, result = [], []
    reached, holding, release = threading.Event(), threading.Event(), threading.Event()
    real = getattr(module, site)

    def at_site(*a, **k):
        if not reached.is_set():
            locks.append(lock_of(*a, **k))
            reached.set()
            assert holding.wait(10)
        return real(*a, **k)

    def hold():
        assert reached.wait(10)
        with locks[0]:
            holding.set()
            release.wait(20)

    monkeypatch.setattr(module, site, at_site)
    holder = threading.Thread(target=hold)
    caller = threading.Thread(target=lambda: result.append(call()))
    holder.start()
    caller.start()
    try:
        assert holding.wait(10)
        time.sleep(0.5)  # long enough for an inverted site to take the report locks
        with store.report_lock(pdir, RID_A, timeout=1):
            pass
    finally:
        release.set()
        holder.join(10)
        caller.join(30)
    return result[0]


def test_snapshot_create_waits_for_the_network_lock_before_any_report_lock(
    client, project, monkeypatch
):
    from routers import snapshots

    name, pdir = project
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    resp = _with_the_sites_network_lock_held_elsewhere(
        monkeypatch,
        snapshots,
        "_create_snapshot_internal",
        lambda: client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"}),
        pdir,
    )
    assert resp.status_code in (200, 201), resp.text


@pytest.mark.parametrize("site", ["_absent_at_capture", "_refuse_if_changed_since"])
def test_restore_waits_for_the_network_lock_before_any_report_lock(
    client, project, monkeypatch, site
):
    """At its early check and again at its own writes."""
    from routers import snapshots

    name, pdir = project
    snap_id = _snapshot(client, name)
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    if site == "_refuse_if_changed_since":
        # Reached with the lock held by the request itself, so stop one step
        # earlier: the last call before the restore's `with` statement.
        site = "_save_unsaved_live_state"
        monkeypatch.setattr(
            snapshots, "_create_snapshot_internal", lambda *a, **k: None
        )
    resp = _with_the_sites_network_lock_held_elsewhere(
        monkeypatch,
        snapshots,
        site,
        lambda: client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore"),
        pdir,
    )
    assert resp.status_code == 200, resp.text


def test_a_save_as_waits_for_the_network_lock_before_any_report_lock(
    client, project, project_storage_dir, monkeypatch
):
    """`_save_context` keeps the context's lock for the whole save: without
    it, the save would wait for that lock inside `_write_context` with the
    report locks already held."""
    from routers import projects

    name, pdir = project
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    monkeypatch.setattr(projects, "_refuse_copy_of_busy_reports", lambda *a, **k: None)
    resp = _with_the_sites_network_lock_held_elsewhere(
        monkeypatch,
        projects,
        "_save_context",
        lambda: client.post("/api/projects/lockme_copy", params={"rebind": True}),
        pdir,
        lock_of=lambda ctx, *a, **k: ctx.mutation_lock,
    )
    assert resp.status_code == 200, resp.text
    assert _tree(project_storage_dir("lockme_copy") / "reports") == _tree(
        pdir / "reports"
    )


# ── GK B F4, F5, F6 ─────────────────────────────────────────────────────────


def test_the_chat_export_tool_refuses_typed_report_busy(client, session_ctx, project):
    from fastapi import HTTPException

    from services import chat_tools

    name, pdir = project
    with held_elsewhere(pdir, RID_A), pytest.raises(HTTPException) as exc:
        chat_tools.download_project_bundle(name)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "report_busy"


def test_the_manifest_text_for_report_busy_covers_copies():
    import json
    import pathlib

    fragment = (
        pathlib.Path(__file__).parents[2]
        / "tool-error-kinds.d"
        / "claude-sh07c-report-comment-revisions.json"
    )
    why = json.loads(fragment.read_text())["kinds"]["report_busy"]["why"]
    assert "3 s" in why and "30 s" in why and "another request of this server" in why


def test_a_restore_leaves_only_lock_files_behind_in_the_snapshot(client, project):
    """Locking the snapshot's reports writes `.<id>.lock` beside them. Nothing
    else in the snapshot changes, and it restores and deletes as before."""
    name, pdir = project
    snap_id = _snapshot(client, name)
    snap = pdir / "snapshots" / snap_id
    before = _tree(snap)
    for _ in range(2):
        resp = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
        assert resp.status_code == 200, resp.text
    assert _tree(snap) == before  # `_tree` leaves the lock files out
    locks = sorted(p.name for p in (snap / "reports").iterdir() if p.is_file())
    assert locks == [f".{RID_A}.lock", f".{RID_B}.lock"]
    assert _tree(pdir / "reports") == _tree(snap / "reports")
    resp = client.delete(f"/api/projects/{name}/snapshots/{snap_id}")
    assert resp.status_code in (200, 204), resp.text
    assert not snap.exists()


def test_a_save_of_the_bound_project_by_its_id_copies_no_reports(
    client, project, project_row
):
    """The early check compares the project's own name, as the save does: a
    save addressed by id is still a save to the bound project (GK B F6)."""
    name, pdir = project
    row = project_row(name)
    with held_elsewhere(pdir, RID_A):
        resp = client.post(f"/api/projects/{row.id}", params={"force": True})
    assert resp.status_code == 200, resp.text
