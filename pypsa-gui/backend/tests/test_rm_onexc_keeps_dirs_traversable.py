"""
`_rm_onexc` must not leave a directory it failed to remove unreadable.

THE DEFECT. The callback clears a Windows read-only attribute with
`os.chmod(path, stat.S_IWRITE)`. On POSIX that call SETS the mode to 0o200:
it adds owner write and drops read and traverse. When `os.rmdir` fails on a
directory that is not empty (a file written into it while the tree was being
removed), the callback chmods it to 0o200 and re-raises. `_force_rmtree`'s
retries can then no longer list it, and the directory stays behind,
untraversable by a non-root server process.

SEEN IN CI (#99, 13 Oct): `qa_decision_study`'s abort left the fork
`qa-ds-abort-opt-bess_1h` in place (`fork_removal_failed`), and the next study
delete crashed with `PermissionError` on `stat(<fork>/metadata.json)`.

The check is on the mode bits, so it holds when the suite runs as root too,
where the missing read bit would not show as an error.
"""
from __future__ import annotations

import errno
import os
import stat

import pytest

from routers.projects import _rm_onexc


def _refuse_not_empty(_path):
    raise OSError(errno.ENOTEMPTY, "Directory not empty")


def test_a_directory_that_will_not_go_stays_readable_and_traversable(tmp_path):
    d = tmp_path / "fork"
    d.mkdir()
    (d / "metadata.json").write_text("{}")

    with pytest.raises(OSError):
        _rm_onexc(_refuse_not_empty, str(d), None)

    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode & stat.S_IRWXU == stat.S_IRWXU, oct(mode)


def test_a_read_only_file_is_removed(tmp_path):
    f = tmp_path / "network.nc"
    f.write_text("x")
    os.chmod(f, 0o444)

    _rm_onexc(os.unlink, str(f), None)

    assert not f.exists()


def test_a_file_that_will_not_go_gets_owner_write_and_keeps_its_read_bits(tmp_path):
    f = tmp_path / "network.nc"
    f.write_text("x")
    os.chmod(f, 0o444)

    def _locked(_path):
        raise PermissionError(errno.EACCES, "locked")

    with pytest.raises(OSError):
        _rm_onexc(_locked, str(f), None)

    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_a_directory_an_earlier_pass_left_at_0o200_is_made_traversable_again(tmp_path):
    d = tmp_path / "fork"
    d.mkdir()
    (d / "metadata.json").write_text("{}")
    os.chmod(d, 0o200)
    try:
        with pytest.raises(OSError):
            _rm_onexc(_refuse_not_empty, str(d), None)
        mode = stat.S_IMODE(d.stat().st_mode)
        assert mode & stat.S_IRWXU == stat.S_IRWXU, oct(mode)
    finally:
        os.chmod(d, 0o700)
