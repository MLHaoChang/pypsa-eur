# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""``scripts/coord/inputs.sh`` builds prs.txt and sessions-issues.txt from the board."""

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "inputs.sh"


def git(cwd, *a):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_prs_and_sessions_come_from_the_board_rows(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "master")
    (origin / "a.py").write_text("1")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "base")
    git(origin, "checkout", "-qb", "pr12")
    (origin / "b.py").write_text("2")
    (origin / "a.py").write_text("3")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "pr")
    git(origin, "update-ref", "refs/pull/12/head", "HEAD")
    git(origin, "checkout", "-q", "master")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    board = tmp_path / "board.json"
    board.write_text(
        json.dumps(
            {
                "rows": [
                    {"board": "prs", "pr": 12, "title": "t"},
                    {"board": "prs", "pr": 99, "title": "never opened"},
                    {"board": "merged", "pr": 5},
                    {"board": "sessions", "session": "Pkg", "doing": "g2p 17 in CI"},
                ]
            }
        )
    )
    r = subprocess.run(
        [str(SCRIPT), str(board), str(tmp_path / "out")],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert sorted((tmp_path / "out/prs.txt").read_text().split("\n")[:-1]) == [
        "12 a.py",
        "12 b.py",
    ]
    assert (tmp_path / "out/sessions-issues.txt").read_text() == "Pkg g2p 17 in CI\n"
    assert "cannot fetch refs/pull/99/head" in r.stderr
