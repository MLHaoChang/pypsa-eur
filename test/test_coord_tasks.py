# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Stages and merge notes applied to tasks.json by ``scripts/coord/tasks.py``."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "tasks.py"
A, B, C = (f".scratch/f/issues/{n}.md" for n in ("01-a", "02-b", "03-c"))


def run(tmp_path, rows, *args, wrap=True):
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"rows": rows} if wrap else rows))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(tasks), *args], capture_output=True, text=True
    )
    data = json.loads(tasks.read_text())
    return r, (data["rows"] if wrap else data)


def test_stages_merge_notes_and_notes_are_applied(tmp_path):
    stages = tmp_path / "stages.tsv"
    stages.write_text(f"{A}\tIn review\n{B}\tReady\n")
    rows = [
        {"path": A, "stage": "In progress"},
        {"id": B},
        {"path": C, "stage": "Ready"},
    ]
    r, out = run(
        tmp_path,
        rows,
        "--stages",
        str(stages),
        "--merged",
        f"#9={C}",
        "--note",
        f"{B}=waits",
    )
    assert r.returncode == 0, r.stderr
    assert out[0]["stage"] == "In review"
    assert out[1] == {"id": B, "stage": "Ready", "note": "waits"}
    assert out[2] == {"path": C, "stage": "Done", "note": "merged #9"}
    assert "tasks:" in r.stdout


def test_a_path_with_no_row_is_reported_and_a_bare_list_works(tmp_path):
    r, out = run(tmp_path, [{"path": A}], "--merged", f"5={B}", wrap=False)
    assert f"no row for {B}" in r.stdout
    assert out == [{"path": A}]


def test_a_malformed_pair_is_refused(tmp_path):
    r, _ = run(tmp_path, [], "--note", "nothing")
    assert r.returncode != 0 and "KEY=VALUE" in r.stderr
