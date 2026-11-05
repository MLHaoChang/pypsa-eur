# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""``scripts/coord/board.py --list``: the rows a coordinator brief pastes."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "board.py"

ROWS = [
    {"board": "merged", "pr": 1, "title": "Old", "stream": "s", "merged": "01:00"},
    {
        "board": "prs",
        "pr": 2,
        "title": "Open PR",
        "stream": "s",
        "state": "CI",
        "head": "abc12345",
    },
    {
        "board": "sessions",
        "session": "session_01X",
        "doing": "Build · X",
        "waiting_on": "#2",
        "needs_you": "no",
    },
]


def test_list_prints_open_rows_and_writes_nothing(tmp_path):
    board = tmp_path / "board" / "board.json"
    board.parent.mkdir()
    board.write_text(json.dumps({"rows": ROWS}))
    before = board.read_text()

    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--dir", str(tmp_path), "--list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert out == [
        "pr #2 @abc12345 [s] Open PR: CI",
        "session session_01X: Build · X | waiting on #2 | needs you no",
    ]
    assert board.read_text() == before
    assert not (tmp_path / "coordinator-state.md").exists()
