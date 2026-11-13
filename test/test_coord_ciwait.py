# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""``scripts/coord/ciwait.sh --board``: one process, one line per PR, keeps watching."""

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "ciwait.sh"

FAKE_GIT = """#!/bin/sh
[ "$1" = ls-remote ] || exit 1
n=${3#refs/pull/}; n=${n%/head}
printf 'sha%s\\trefs/pull/%s/head\\n' "$n" "$n"
"""
FAKE_GH = """#!/bin/sh
case "$2" in
  *commits/sha1/*) printf 'completed\\tsuccess\\tlint\\t\\t1\\n' ;;
  *commits/sha2/*) printf 'completed\\tfailure\\ttests\\t\\t2\\ncompleted\\tsuccess\\tlint\\t\\t3\\n' ;;
  *commits/sha3/*) printf 'in_progress\\t-\\ttests\\t\\t4\\n' ;;
esac
"""


def watch(tmp_path, rows, seconds=6):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, text in (("git", FAKE_GIT), ("gh", FAKE_GH)):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    board = tmp_path / "board.json"
    board.write_text(json.dumps({"rows": rows}))
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "CIWAIT_INTERVAL": "1"}
    try:
        subprocess.run(
            [str(SCRIPT), "--board", str(board), "1"],
            env=env,
            capture_output=True,
            text=True,
            timeout=seconds,
        )
    except subprocess.TimeoutExpired as e:  # it keeps watching: stopped by the test
        return (e.stdout or b"").decode().splitlines()
    raise AssertionError("board mode must keep watching until the limit")


def test_each_pr_is_reported_once_and_the_pending_one_keeps_being_watched(tmp_path):
    rows = [{"board": "prs", "pr": n} for n in (1, 2, 3)] + [
        {"board": "merged", "pr": 9}
    ]
    lines = watch(tmp_path, rows)
    assert lines == ["READY 1 sha1", "FAIL 2 tests failure"], lines


def test_board_mode_refuses_a_missing_board(tmp_path):
    r = subprocess.run(
        [str(SCRIPT), "--board", str(tmp_path / "none.json")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2 and "no such board" in r.stderr


def test_list_mode_still_exits_on_the_first_hit(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, text in (("git", FAKE_GIT), ("gh", FAKE_GH)):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "CIWAIT_INTERVAL": "1"}
    r = subprocess.run(
        [str(SCRIPT), "5", "3", "2", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.stdout.strip() == "FAIL 2 tests failure", r.stdout
