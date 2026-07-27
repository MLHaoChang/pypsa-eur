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


# ── The CALL SITES (review finding: reverting them was invisible) ────────────
#
# `test_atomic_io.py` tested the helper in isolation and nothing asserted any
# caller used it, so reverting `atomic_write_bytes` to `write_bytes` at the
# bundle-import, from-template and scenario-create sites passed the whole
# suite. Those three sites are the entire point of the task.

def _sites():
    """The production call sites, as (file, needle) pairs."""
    return [
        ("routers/projects.py", "atomic_write_bytes(dest / fname, zf.read(fname))"),
        ("routers/projects.py", "atomic_write_bytes(target_path, zf.read(member))"),
        ("routers/projects.py", 'atomic_copy(src_nc, dest / "network.nc")'),
        ("routers/projects.py", "atomic_write_bytes(child_dir / fname, src_file.read_bytes())"),
    ]


@pytest.mark.parametrize(("relative", "needle"), _sites())
def test_the_destructive_write_sites_go_through_atomic_io(relative, needle):
    """
    A source check, deliberately, and it is worth saying why rather than
    apologising for it. The behavioural difference between `write_bytes` and
    `atomic_write_bytes` is observable only when the write FAILS PART-WAY —
    which at these sites means a corrupt zip member or an unreadable template
    mid-stream, and neither is reachable through the public API without
    monkeypatching the thing under test. `test_a_failed_copy_leaves_the_
    destination_intact` above proves the mechanism; this proves the sites use
    it, which is the half that silently regressed.
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent / relative).read_text(
        encoding="utf-8"
    )
    assert needle in source, f"{relative} no longer routes this write through atomic_io"


def test_no_bare_write_bytes_survives_in_the_bundle_paths():
    """The inverse of the above: catch a NEW unguarded write appearing beside
    the guarded ones."""
    import pathlib
    import re

    source = (
        pathlib.Path(__file__).resolve().parent.parent / "routers" / "projects.py"
    ).read_text(encoding="utf-8")
    # `.write_bytes(` on a path built from a project directory.
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"\b(dest|child_dir|target_path)\s*/.*\.write_bytes\(", line)
    ]
    assert offenders == [], offenders
