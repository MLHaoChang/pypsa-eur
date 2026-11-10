# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 07e: bundle copies of `reports/` take the per-report lock."""

from __future__ import annotations

import fcntl
import os
import pathlib
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
    monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 0.2)


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
    monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 30)
    clock = iter([100.0, 110.0, 125.0])
    monkeypatch.setattr(store, "time", SimpleNamespace(monotonic=lambda: next(clock)))
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
