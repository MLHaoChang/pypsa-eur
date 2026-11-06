# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Repr diff guard (``scripts/coord/repr_guard.py``): new ``!r}`` in user-visible text."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "coord"))

import repr_guard  # noqa: E402

SRC = """\
import logging
log = logging.getLogger(__name__)

def f(x):
    msg = f"bad {x!r}"
    log.warning(
        "multi %s",
        f"{x!r}",
    )
    log.info(f"ok {x!r}")
    dbg = f"{x!r}"  # repr-ok: debug text
    return msg, dbg
"""


def test_flags_only_non_log_lines_without_escape():
    bad = repr_guard.check_file("m.py", range(1, 13), SRC)
    assert [ln for ln, _ in bad] == [5]


def test_only_added_lines_are_checked():
    assert repr_guard.check_file("m.py", [4, 6], SRC) == []


def test_added_lines_parse_and_test_paths():
    diff = (
        "+++ b/pypsa-gui/backend/a.py\n@@ -1,0 +5,2 @@\n+x\n+y\n"
        "@@ -9 +20 @@\n-old\n+new\n"
    )
    assert list(repr_guard.added_lines(diff)) == [
        ("pypsa-gui/backend/a.py", 5),
        ("pypsa-gui/backend/a.py", 6),
        ("pypsa-gui/backend/a.py", 20),
    ]
    assert repr_guard.is_test_path("pypsa-gui/backend/tests/test_a.py")
    assert repr_guard.is_test_path("pypsa-gui/backend/services/test_x.py")
    assert not repr_guard.is_test_path("pypsa-gui/backend/services/x.py")


def test_main_ratchet_in_a_repo(tmp_path, monkeypatch, capsys):
    def git(*a):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    f = tmp_path / "pypsa-gui" / "backend" / "svc.py"
    f.parent.mkdir(parents=True)
    f.write_text('old = f"{1!r}"\n')  # existing !r} stays
    git("init", "-q", "-b", "master")
    git("add", ".")
    git("commit", "-qm", "base")
    monkeypatch.chdir(tmp_path)
    assert repr_guard.main(["master"]) == 0
    f.write_text('old = f"{1!r}"\nnew = f"{2!r}"\n')
    assert repr_guard.main(["master"]) == 1
    assert "svc.py:2" in capsys.readouterr().out


def test_substring_names_are_not_exempt():
    src = 'def f(x):\n    catalog.add(f"{x!r}")\n    changelog.add(f"{x!r}")\n'
    assert [ln for ln, _ in repr_guard.check_file("m.py", [2, 3], src)] == [2, 3]


def test_escape_hatch_needs_a_reason():
    src = 'a = f"{1!r}"  # repr-ok:\nb = f"{1!r}"  # repr-ok: debug\n'
    assert [ln for ln, _ in repr_guard.check_file("m.py", [1, 2], src)] == [1]


def test_test_files_are_ignored_by_main(tmp_path, monkeypatch, capsys):
    def git(*a):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "master")
    (tmp_path / "README").write_text("x")
    git("add", ".")
    git("commit", "-qm", "base")
    t = tmp_path / "pypsa-gui" / "backend" / "tests" / "test_a.py"
    t.parent.mkdir(parents=True)
    t.write_text('x = f"{1!r}"\n')
    git("add", "-N", ".")
    monkeypatch.chdir(tmp_path)
    assert repr_guard.main(["master"]) == 0
