"""
Atomic writes (phase 1b, Task 5 — spec E5).

The contract worth testing is narrow and the temptation is to test something
adjacent instead. Two traps this file deliberately avoids:

  * asserting the `.tmp` SURVIVES an exception. It does not — the `except`
    branch unlinks it, and that is correct: the detector exists to catch a
    KILLED PROCESS, and a surviving tmp after an ordinary exception would make
    the corruption banner fire on every failed save.
  * writing `network.nc.tmp` by hand and asserting it exists. That exercises
    no production code at all; the naming test below drives the real writer
    and captures the path it was handed.
"""
from __future__ import annotations

import pathlib

import pytest

from routers.projects import _BUNDLE_FILES
from services.atomic_io import (
    atomic_copy,
    atomic_write_bytes,
    atomic_write_text,
    atomic_write_with,
)


def _boom(_path):
    raise RuntimeError("writer failed")


def test_content_is_replaced(tmp_path):
    target = tmp_path / "network.nc"
    target.write_text("old", encoding="utf-8")

    atomic_write_with(target, lambda p: p.write_text("new", encoding="utf-8"))

    assert target.read_text(encoding="utf-8") == "new"


def test_a_failed_write_leaves_the_original_intact(tmp_path):
    """The whole point. `write_bytes` straight onto the target truncates it
    first, so a failure partway leaves neither version."""
    target = tmp_path / "network.nc"
    target.write_text("irreplaceable", encoding="utf-8")

    with pytest.raises(RuntimeError):
        atomic_write_with(target, _boom)

    assert target.read_text(encoding="utf-8") == "irreplaceable"


def test_the_tmp_is_cleaned_up_on_exception(tmp_path):
    target = tmp_path / "network.nc"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(RuntimeError):
        atomic_write_with(target, lambda p: (p.write_text("half"), _boom(p)))

    assert list(tmp_path.iterdir()) == [target]


def test_a_new_file_is_created_without_leaving_a_tmp(tmp_path):
    target = tmp_path / "layout.json"
    atomic_write_text(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("fname", _BUNDLE_FILES)
def test_tmp_name_matches_what_the_crash_detector_looks_for(tmp_path, fname):
    """
    `routers/projects.py` scans for `f"{fname}.tmp"` per bundle file to raise
    the corruption banner. If the suffix logic here drifts — `with_suffix`
    replaces the extension rather than appending, which is exactly the kind of
    thing that changes under a refactor — the banner silently stops firing and
    nothing goes red.
    """
    captured: dict[str, pathlib.Path] = {}

    def _capture_then_fail(path):
        captured["path"] = path
        raise RuntimeError("stop here")

    with pytest.raises(RuntimeError):
        atomic_write_with(tmp_path / fname, _capture_then_fail)

    assert captured["path"].name == f"{fname}.tmp"


def test_atomic_write_bytes_replaces_content(tmp_path):
    target = tmp_path / "network.nc"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_copy_replaces_content(tmp_path):
    src = tmp_path / "src.nc"
    src.write_bytes(b"fresh")
    dest = tmp_path / "network.nc"
    dest.write_bytes(b"stale")

    atomic_copy(src, dest)

    assert dest.read_bytes() == b"fresh"


def test_a_failed_copy_leaves_the_destination_intact(tmp_path):
    """
    `shutil.copy2(src, dest)` opens `dest` for writing — truncating it — before
    reading `src`. A source that cannot be read therefore destroys a working
    destination. This is the bundle-import case: the archive member is
    attacker- or accident-supplied and the destination is a live project.
    """
    unreadable = tmp_path / "a-directory"
    unreadable.mkdir()
    dest = tmp_path / "network.nc"
    dest.write_bytes(b"irreplaceable")

    with pytest.raises(OSError):
        atomic_copy(unreadable, dest)

    assert dest.read_bytes() == b"irreplaceable"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a-directory", "network.nc"]


def test_routers_still_expose_the_historical_names():
    """`routers/projects.py` has fourteen call sites under the old private
    names and `routers/snapshots.py` imported them from there. Both keep
    working, or this task's move becomes a rewrite of two routers."""
    from routers import projects as projects_router
    from services import atomic_io

    assert projects_router._atomic_write_with is atomic_io.atomic_write_with
    assert projects_router._atomic_write_text is atomic_io.atomic_write_text
