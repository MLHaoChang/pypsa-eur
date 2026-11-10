# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 07e: bundle copies of `reports/` take the per-report lock."""

from __future__ import annotations

import fcntl
import os
import pathlib
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.reports import store

RID_A = "00000000000000aa"
RID_B = "00000000000000bb"


def _seed(project_dir: pathlib.Path, rid: str, body: str = "one") -> pathlib.Path:
    """A report directory with the three files a writer keeps in step."""
    rdir = store.reports_dir(project_dir) / rid
    rdir.mkdir(parents=True)
    for name in ("meta.json", "v1.json", "comments.json"):
        (rdir / name).write_text(f"{name}:{body}", encoding="utf-8")
    return rdir


def _tree(root: pathlib.Path) -> dict[str, bytes]:
    """Every non-lock file under `root`, by relative path."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(".lock")
    }


@contextmanager
def held_elsewhere(project_dir: pathlib.Path, rid: str):
    """Hold the report's file lock on a second open file, as another process
    would (flock is per open file, so this thread's own attempt is refused)."""
    root = store.reports_dir(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / f".{rid}.lock", "a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 0.2)


pytestmark = pytest.mark.skipif(os.name == "nt", reason="flock-based holder")


# ── the lock set ────────────────────────────────────────────────────────────


def test_the_copy_lock_holds_every_report_on_both_sides(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A)
    _seed(dst, RID_B)
    with store.reports_copy_lock(src, dst) as held:
        assert held == {"src": {RID_A}, "dst": {RID_A, RID_B}}
        for project_dir, rid in ((src, RID_A), (dst, RID_A), (dst, RID_B)):
            with pytest.raises(BlockingIOError), held_elsewhere(project_dir, rid):
                pass
    with held_elsewhere(dst, RID_B):  # released
        pass


def test_locks_are_taken_in_sorted_order(tmp_path, monkeypatch):
    src, dst = tmp_path / "b", tmp_path / "a"
    for rid in (RID_B, RID_A):
        _seed(src, rid)
    taken = []
    real = store.report_lock

    @contextmanager
    def spy(project_dir, rid, **kw):
        taken.append((project_dir.name, rid))
        with real(project_dir, rid, **kw):
            yield

    monkeypatch.setattr(store, "report_lock", spy)
    with store.reports_copy_lock(src, dst):
        pass
    assert taken == [("a", RID_A), ("a", RID_B), ("b", RID_A), ("b", RID_B)]


def test_a_busy_report_refuses_and_releases_the_rest(tmp_path, short_timeout):
    _seed(tmp_path, RID_A)
    _seed(tmp_path, RID_B)
    with held_elsewhere(tmp_path, RID_B):
        with pytest.raises(store.ReportBusy):
            with store.reports_copy_lock(tmp_path):
                pytest.fail("entered with a report busy")
        with held_elsewhere(tmp_path, RID_A):  # the one it did get is free again
            pass


def test_the_locks_share_one_deadline(tmp_path, monkeypatch):
    for rid in (RID_A, RID_B):
        _seed(tmp_path, rid)
    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 30)
    clock = iter([100.0, 110.0, 125.0])
    monkeypatch.setattr(
        store,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock, 125.0), time_ns=lambda: 0),
    )
    timeouts = []
    real = store.report_lock

    @contextmanager
    def spy(project_dir, rid, *, timeout):
        timeouts.append(timeout)
        with real(project_dir, rid):
            yield

    monkeypatch.setattr(store, "report_lock", spy)
    with store.reports_copy_lock(tmp_path):
        pass
    assert timeouts == [20.0, 5.0]


def test_a_report_created_while_locking_is_locked_too(tmp_path, monkeypatch):
    _seed(tmp_path, RID_B)
    real = store.report_lock
    order = []

    @contextmanager
    def spy(project_dir, rid, **kw):
        order.append(rid)
        if order == [RID_B]:
            _seed(tmp_path, RID_A)  # appears after the set was read
        with real(project_dir, rid, **kw):
            yield

    monkeypatch.setattr(store, "report_lock", spy)
    with store.reports_copy_lock(tmp_path) as held:
        assert held["src"] == {RID_A, RID_B}
    assert order == [RID_B, RID_A, RID_B]  # released, then retaken in order


def test_a_create_in_progress_is_waited_for(tmp_path, short_timeout):
    """`create_report` holds the lock before its directory exists."""
    with held_elsewhere(tmp_path, RID_A), pytest.raises(store.ReportBusy):
        with store.reports_copy_lock(tmp_path):
            pass


# ── the per-report copy ─────────────────────────────────────────────────────


def test_copy_reports_replaces_each_report_and_drops_the_extra(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A, "new")
    _seed(dst, RID_A, "old")
    (dst / "reports" / RID_A / "v2.json").write_text("later", encoding="utf-8")
    _seed(dst, RID_B, "old")
    store.copy_reports(src, dst)
    assert _tree(dst / "reports") == _tree(src / "reports")
    assert [
        m.name
        for m in (dst / "reports").iterdir()
        if ".tmp-" in m.name or ".old-" in m.name
    ] == []


def test_copy_reports_keeps_held_lock_files_and_copies_none(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A)
    _seed(dst, RID_B)
    with store.report_lock(src, RID_A), store.report_lock(dst, RID_B):
        inode = (dst / "reports" / f".{RID_B}.lock").stat().st_ino
        store.copy_reports(src, dst)
        assert (dst / "reports" / f".{RID_B}.lock").stat().st_ino == inode
    assert not (dst / "reports" / RID_B).exists()
    (src / "reports" / f".{RID_B}.lock").write_bytes(b"")  # a stale source lock
    store.copy_reports(src, dst)
    assert sorted(p.name for p in (dst / "reports").iterdir()) == [
        f".{RID_A}.lock",
        f".{RID_B}.lock",
        RID_A,
    ]


def test_copy_reports_without_source_reports_changes_nothing(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    _seed(dst, RID_A)
    before = _tree(dst)
    store.copy_reports(src, dst)
    assert _tree(dst) == before


def test_copy_reports_refuses_busy_and_writes_nothing(tmp_path, short_timeout):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A, "new")
    _seed(src, RID_B, "new")
    _seed(dst, RID_A, "old")
    _seed(dst, RID_B, "old")
    before = _tree(dst)
    with held_elsewhere(dst, RID_B), pytest.raises(store.ReportBusy):
        store.copy_reports(src, dst)
    assert _tree(dst) == before  # the free sibling was not half-replaced


def test_a_report_created_after_the_lock_set_survives_the_copy(tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A)
    _seed(dst, RID_A)
    real = store.reports_copy_lock

    @contextmanager
    def then_create(*a, **k):
        with real(*a, **k) as held:
            _seed(dst, RID_B, "made during the copy")
            yield held

    monkeypatch.setattr(store, "reports_copy_lock", then_create)
    store.copy_reports(src, dst)
    assert (dst / "reports" / RID_B / "meta.json").exists()


def test_clear_reports_keeps_lock_files_and_later_reports(tmp_path, monkeypatch):
    _seed(tmp_path, RID_A)
    real = store.reports_copy_lock

    @contextmanager
    def then_create(*a, **k):
        with real(*a, **k) as held:
            _seed(tmp_path, RID_B, "made during the restore")
            yield held

    monkeypatch.setattr(store, "reports_copy_lock", then_create)
    with store.report_lock(tmp_path, RID_A):
        store.clear_reports(tmp_path)
    assert sorted(p.name for p in store.reports_dir(tmp_path).iterdir()) == [
        f".{RID_A}.lock",
        RID_B,
    ]


def test_clear_reports_refuses_busy_and_removes_nothing(tmp_path, short_timeout):
    _seed(tmp_path, RID_A)
    _seed(tmp_path, RID_B)
    before = _tree(tmp_path)
    with held_elsewhere(tmp_path, RID_B), pytest.raises(store.ReportBusy):
        store.clear_reports(tmp_path)
    assert _tree(tmp_path) == before


def test_a_failed_report_copy_keeps_the_old_report_and_no_temp_dirs(
    tmp_path, monkeypatch
):
    """Each report is the old one or the new one, never a mix (GK B L6)."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A, "new")
    _seed(src, RID_B, "new")
    _seed(dst, RID_A, "old")
    _seed(dst, RID_B, "old")
    real = store.shutil.copytree

    def fail_on_b(source, target, *a, **k):
        real(source, target, *a, **k)
        if pathlib.Path(source).name == RID_B:
            raise OSError("disk full")

    monkeypatch.setattr(store.shutil, "copytree", fail_on_b)
    with pytest.raises(OSError):
        store.copy_reports(src, dst)
    monkeypatch.undo()
    assert _tree(dst / "reports" / RID_A) == _tree(src / "reports" / RID_A)
    assert (dst / "reports" / RID_B / "meta.json").read_text() == "meta.json:old"
    assert sorted(p.name for p in (dst / "reports").iterdir()) == [
        f".{RID_A}.lock",
        f".{RID_B}.lock",
        RID_A,
        RID_B,
    ]


def test_reports_that_keep_changing_refuse(tmp_path, monkeypatch):
    """The re-list is bounded (GK B L4)."""
    made = iter(f"{n:016x}" for n in range(1, 100))
    real = store._lockable_ids
    calls = []

    def churn(project_dir):
        calls.append(1)
        _seed(tmp_path, next(made))
        return real(project_dir)

    monkeypatch.setattr(store, "_lockable_ids", churn)
    with pytest.raises(store.ReportBusy):
        with store.reports_copy_lock(tmp_path):
            pytest.fail("entered while the reports kept changing")
    assert len(calls) == 2 * store._COPY_LOCK_ROUNDS
    with held_elsewhere(tmp_path, f"{1:016x}"):  # nothing left held
        pass


# ── re-entrancy (GK B L5) ───────────────────────────────────────────────────


def test_a_nested_copy_lock_keeps_the_outer_hold(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A)
    _seed(dst, RID_A)
    with store.reports_copy_lock(src, dst) as outer:
        with store.reports_copy_lock(src, dst) as inner:
            assert inner == outer
        with pytest.raises(BlockingIOError), held_elsewhere(dst, RID_A):
            pass  # the inner release did not drop the outer hold
        store.copy_reports(src, dst)
        with pytest.raises(BlockingIOError), held_elsewhere(src, RID_A):
            pass
    with held_elsewhere(dst, RID_A), held_elsewhere(src, RID_A):
        pass


def test_a_nested_copy_lock_does_not_wait_for_a_later_report(tmp_path, short_timeout):
    """Under a route's hold, the backstop in `_copy_bundle_dirs` keeps the
    outer id set: a report made since, busy or not, is left alone."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A, "new")
    _seed(dst, RID_A, "old")
    with store.reports_copy_lock(src, dst):
        with store.reports_copy_lock(src, dst):
            pass  # an inner release keeps the outer id set too
        _seed(dst, RID_B, "made during the copy")
        with held_elsewhere(dst, RID_B):
            store.copy_reports(src, dst)
    assert (dst / "reports" / RID_A / "meta.json").read_text() == "meta.json:new"
    assert (dst / "reports" / RID_B / "meta.json").exists()


def test_a_thread_of_this_process_is_bounded_too(tmp_path, short_timeout):
    _seed(tmp_path, RID_A)
    entered, leave = threading.Event(), threading.Event()

    def writer():
        with store.report_lock(tmp_path, RID_A):
            entered.set()
            leave.wait(10)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        with pytest.raises(store.ReportBusy):
            with store.reports_copy_lock(tmp_path):
                pass
        assert time.monotonic() - started < 2
    finally:
        leave.set()
        thread.join(5)


# ── lock order against the network lock (GK B L1, L2) ───────────────────────


def _writers():
    from tests import test_report_revisions as T

    def save(pdir):
        store.save_version(pdir, store.load_report(pdir, T.RID))

    def meta(pdir):
        store.update_meta(pdir, T.RID, template_language=None)

    def figure(pdir):
        store.write_figure(pdir, T.RID, "frontier", b"png")

    def comment(pdir):
        T._comment(pdir)

    def delete(pdir):
        store.delete_report(pdir, T.RID)

    return [save, meta, figure, comment, delete]


@pytest.mark.parametrize("write", _writers(), ids=lambda f: f.__name__)
def test_a_writer_finishes_while_a_copy_holds_the_network_lock(
    tmp_path, monkeypatch, write
):
    """Copies take the network lock first and the report locks second. That
    is safe only while no writer takes the network lock inside a report lock:
    such a writer would stall here until the copy gave up, and this goes red."""
    from services.pypsa_service import PyPSAService
    from tests import test_report_revisions as T

    monkeypatch.setattr(store, "COPY_LOCK_TIMEOUT_S", 5)
    store.create_report(tmp_path, T._doc())
    inside, go = threading.Event(), threading.Event()
    failed = []

    def writer():
        try:
            with store.report_lock(tmp_path, T.RID):
                inside.set()
                go.wait(10)
                write(tmp_path)
        except Exception as exc:  # noqa: BLE001 - reported below
            failed.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert inside.wait(5)
        with PyPSAService.get_lock():
            go.set()
            started = time.monotonic()
            with store.reports_copy_lock(tmp_path):
                waited = time.monotonic() - started
    finally:
        go.set()
        thread.join(10)
    assert not failed, failed
    assert waited < 3, f"the copy waited {waited:.1f}s for a writer"


def test_a_source_report_created_after_the_lock_set_is_not_copied(
    tmp_path, monkeypatch
):
    """It is not locked, so it may be half written."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A)
    real = store.reports_copy_lock

    @contextmanager
    def then_create(*a, **k):
        with real(*a, **k) as held:
            _seed(src, RID_B, "made during the copy")
            yield held

    monkeypatch.setattr(store, "reports_copy_lock", then_create)
    store.copy_reports(src, dst)
    assert sorted(p.name for p in (dst / "reports").iterdir() if p.is_dir()) == [RID_A]


def test_a_nested_copy_lock_does_not_wait_for_a_later_source_report(
    tmp_path, short_timeout
):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A, "new")
    with store.reports_copy_lock(src):
        _seed(src, RID_B, "made during the copy")
        with held_elsewhere(src, RID_B):
            store.copy_reports(src, dst)
    assert sorted(p.name for p in (dst / "reports").iterdir() if p.is_dir()) == [RID_A]


def test_a_failed_swap_puts_the_old_report_back(tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A, "new")
    _seed(dst, RID_A, "old")
    before = _tree(dst)
    real = store.os.replace

    def fail_swap_in(source, target):
        if ".tmp-" in pathlib.Path(source).name:
            raise OSError("in use")
        real(source, target)

    monkeypatch.setattr(store.os, "replace", fail_swap_in)
    with pytest.raises(OSError):
        store.copy_reports(src, dst)
    monkeypatch.undo()
    assert _tree(dst) == before
    assert sorted(p.name for p in (dst / "reports").iterdir()) == [
        f".{RID_A}.lock",
        RID_A,
    ]


def test_only_reports_are_copied_or_removed(tmp_path):
    """A stray file in `reports/` is no report: it stays where it is."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, RID_A)
    _seed(dst, RID_B)
    (src / "reports" / "notes.txt").write_text("source stray")
    (dst / "reports" / ".DS_Store").write_text("destination stray")
    store.copy_reports(src, dst)
    assert {p.name for p in (dst / "reports").iterdir()} == {
        ".DS_Store",
        f".{RID_A}.lock",
        f".{RID_B}.lock",
        RID_A,
    }
    store.clear_reports(dst)
    assert {p.name for p in (dst / "reports").iterdir()} == {
        ".DS_Store",
        f".{RID_A}.lock",
        f".{RID_B}.lock",
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (f".{RID_A}.lock", True),
        (f"x{RID_A}.lock", False),
        (f".{RID_A}.tmpX", False),
        (".x.lock", False),
        (RID_A, False),
    ],
)
def test_only_a_reports_own_lock_file_is_a_lock_file(name, expected):
    assert store.is_lock_file(name) is expected


def test_clear_reports_without_a_reports_folder_does_nothing(tmp_path):
    store.clear_reports(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_report_that_is_a_file_or_a_link_is_removed_as_one(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, "00000000000000cc")
    kept = tmp_path / "elsewhere"
    kept.mkdir()
    (kept / "meta.json").write_text("not the project's")
    store.reports_dir(dst).mkdir(parents=True)
    (store.reports_dir(dst) / RID_A).write_text("a file where a report would be")
    (store.reports_dir(dst) / RID_B).symlink_to(kept, target_is_directory=True)
    store.copy_reports(src, dst)
    assert sorted(p.name for p in store.reports_dir(dst).iterdir() if p.is_dir()) == [
        "00000000000000cc"
    ]
    assert (kept / "meta.json").exists()  # the link went, not what it pointed at


def test_a_writer_is_refused_when_a_copy_of_this_process_keeps_its_report(
    tmp_path, monkeypatch
):
    """A copy can hold a report for a whole save or restore: a writer in
    another thread gets `report_busy` after its bound, it does not hang
    (GK B F2)."""
    monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 0.3)
    _seed(tmp_path, RID_A)
    outcome = []

    def writer():
        started = time.monotonic()
        try:
            with store.report_lock(tmp_path, RID_A):
                outcome.append("entered")
        except store.ReportBusy:
            outcome.append(time.monotonic() - started)

    with store.reports_copy_lock(tmp_path):
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(5)
    assert not thread.is_alive()
    assert outcome and outcome[0] != "entered" and 0.25 < outcome[0] < 2
    with store.report_lock(tmp_path, RID_A):  # and it is free once the copy ends
        pass
