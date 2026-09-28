"""
`_force_rmtree` must refuse a symbolic link, not chmod what it points at.

THE DEFECT. `shutil.rmtree` already refuses a symlink root — but it refuses it
by routing `Cannot call rmtree on a symbolic link` to the error callback, with
`func=os.path.islink`. `_rm_onexc` was written for the Windows read-only case:
it did `os.chmod(path, stat.S_IWRITE)` and then called `func(path)` to re-raise
if the path was still locked. For a symlink, `os.chmod` FOLLOWS the link and
`os.path.islink(path)` returns True without raising — so the callback silently
absorbed the refusal, having just set the link's TARGET to 0o200.

MEASURED, before the fix: `_force_rmtree(snapshots/<id> -> /some/dir)` returned
normally, the target still existed with mode 0o40200 (no read, no traverse, so
unusable by a non-root server process), the link was still there, and the
DELETE route answered 204 "deleted" with a change-log entry.

WHY IT LIVES HERE AND NOT ONLY IN THE CALLER. `snapshots._list_snapshot_dirs`
and `_existing_snapshot_dir` now filter escaping entries, so the snapshot
routes cannot reach this any more. `_force_rmtree` has other callers — project
delete, `_copy_bundle_dirs`, the legacy importer — and the trap belongs to the
function, not to the one caller that happened to spring it.
"""
from __future__ import annotations

import pathlib

import pytest

from routers.projects import _force_rmtree


def test_it_refuses_a_symlink_instead_of_chmod_ing_the_target(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "data.txt").write_text("precious")
    before = victim.stat().st_mode

    link = tmp_path / "link"
    link.symlink_to(victim, target_is_directory=True)

    with pytest.raises(OSError):
        _force_rmtree(link)

    # The assertion that actually distinguishes fixed from broken: the old code
    # did not raise AND left the target at 0o200.
    assert victim.stat().st_mode == before
    assert (victim / "data.txt").read_text() == "precious"
    assert link.is_symlink()


def test_a_symlink_to_a_file_is_refused_too(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("keep")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(OSError):
        _force_rmtree(link)
    assert target.read_text() == "keep"


def test_an_ordinary_tree_is_still_removed(tmp_path):
    """The guard must not make the function stop doing its job — without this
    a `raise` at the top of `_force_rmtree` would pass both cases above."""
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f.txt").write_text("gone")

    _force_rmtree(tree)
    assert not tree.exists()


def test_a_symlink_INSIDE_the_tree_is_removed_without_following_it(tmp_path):
    """
    Only the ROOT is refused. `shutil.rmtree` unlinks a nested symlink rather
    than descending it, which is the behaviour every caller relies on — a
    project directory may legitimately contain one.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")

    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "inner").symlink_to(outside, target_is_directory=True)

    _force_rmtree(tree)
    assert not tree.exists()
    assert (outside / "keep.txt").read_text() == "keep"
