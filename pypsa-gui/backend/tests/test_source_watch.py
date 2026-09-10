"""
Improvement #10, re-diagnosed.

The backlog asked for a per-worker PROJECTS_DIR fixture to stop parallel
pytest workers contaminating each other. Three findings kill that as
written:

  * `pytest-xdist` is not installed and `addopts` carries no `-n`. There
    are no workers to isolate — the described failure cannot occur.
  * PROJECTS_DIR resolves to `~/Library/Application Support/PyPSA GUI/
    flat_projects`, and a suite run leaves it untouched. It is not the
    channel.
  * The tests that actually flake — observed twice in one session — are
    the ones calling `inspect.getsource`.

`getsource` reads the file FROM DISK at the line numbers recorded in the
code object at import time. Edit the file after import and it returns text
that was never in that function: demonstrated in isolation by inserting
three comment lines above a function and watching them appear inside its
"source". `linecache.checkcache()` does not help — the cache is not the
problem, the stored line numbers are.

So the real cause is a pytest process racing an EDITOR, not another pytest
process. Two concurrent readers are harmless; it takes a writer, which in
this repo means a second agent session editing mid-run — exactly what
CLAUDE.md warns about.

Nothing here can prevent that. What it can do is stop the failure lying:
`assert "with ctx.chat_state.lock:" in src` reads as a locking regression
and sends the next person hunting a bug that does not exist.
"""
from __future__ import annotations

import time
from pathlib import Path

from tests import _source_watch as sw


def test_an_untouched_tree_reports_no_changes(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")

    snap = sw.snapshot_mtimes([tmp_path])

    assert sw.changed_since(snap, [tmp_path]) == []


def test_a_file_edited_after_the_snapshot_is_named(tmp_path):
    victim = tmp_path / "victim.py"
    victim.write_text("x = 1\n")
    snap = sw.snapshot_mtimes([tmp_path])

    time.sleep(0.01)
    victim.write_text("x = 2\n")

    changed = sw.changed_since(snap, [tmp_path])
    assert [Path(p).name for p in changed] == ["victim.py"]


def test_a_file_created_after_the_snapshot_is_named(tmp_path):
    """
    A new module is as capable of shifting an import as an edited one —
    and it is what a second session adding a file looks like.
    """
    snap = sw.snapshot_mtimes([tmp_path])
    time.sleep(0.01)
    (tmp_path / "newcomer.py").write_text("z = 3\n")

    changed = sw.changed_since(snap, [tmp_path])
    assert [Path(p).name for p in changed] == ["newcomer.py"]


def test_a_deleted_file_is_named(tmp_path):
    victim = tmp_path / "gone.py"
    victim.write_text("x = 1\n")
    snap = sw.snapshot_mtimes([tmp_path])

    victim.unlink()

    assert [Path(p).name for p in sw.changed_since(snap, [tmp_path])] == ["gone.py"]


def test_non_python_files_are_ignored(tmp_path):
    """
    Logs, sqlite journals and __pycache__ churn constantly during a run.
    Reporting those would make the warning noise, and a warning that cries
    wolf is worse than none.
    """
    (tmp_path / "keep.py").write_text("x = 1\n")
    snap = sw.snapshot_mtimes([tmp_path])
    time.sleep(0.01)
    (tmp_path / "app.log").write_text("noise\n")
    (tmp_path / "db.sqlite-journal").write_text("noise\n")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "keep.cpython-313.pyc").write_bytes(b"\x00")

    assert sw.changed_since(snap, [tmp_path]) == []


def test_a_missing_root_is_not_an_error(tmp_path):
    """The watcher must never be the reason a suite fails."""
    absent = tmp_path / "nope"
    snap = sw.snapshot_mtimes([absent])

    assert snap == {}
    assert sw.changed_since(snap, [absent]) == []


def test_the_warning_names_the_files_and_says_what_to_do(tmp_path):
    victim = tmp_path / "chat_service.py"
    victim.write_text("x = 1\n")
    snap = sw.snapshot_mtimes([tmp_path])
    time.sleep(0.01)
    victim.write_text("x = 2\n")

    msg = sw.format_warning(sw.changed_since(snap, [tmp_path]))

    assert msg is not None
    assert "chat_service.py" in msg
    # The two things the reader needs: that a failure may be spurious, and
    # that re-running serially is how to tell.
    assert "inspect.getsource" in msg
    assert "re-run" in msg.lower()


def test_no_warning_when_nothing_changed():
    assert sw.format_warning([]) is None
