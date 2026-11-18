# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""whois.py builds roles from labelled lines; tasks.py adds rows for new issues."""

import importlib.util
import json
import subprocess
from pathlib import Path

COORD = Path(__file__).parents[1] / "scripts" / "coord"


def load(name):
    spec = importlib.util.spec_from_file_location(name, COORD / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


whois, tasks = load("whois"), load("tasks")


def test_roles_come_from_labelled_lines():
    text = (
        "# Coordinator\n\nCurrent coordinator session: `session_C1`\n\n"
        "Engine gatekeeper A (gridspine): `session_A1`\n\n"
        "Engine gatekeeper A2 (overflow): `session_A2`\n\n"
        "Engine gatekeeper B2: `session_B2`\n\n"
        "UX reviewer (gate): `session_U1`\n\nSend every message to this id.\n"
    )
    assert whois.roles_of(text) == {
        "coordinator": "session_C1",
        "gka": "session_A1",
        "gka2": "session_A2",
        "gkb2": "session_B2",
        "ux": "session_U1",
    }


def test_whois_reads_repo_file():
    roles = whois.roles_of(
        (COORD.parents[1] / "docs/agents/coordinator.md").read_text()
    )
    assert {"coordinator", "gka", "gkb", "ux"} <= set(roles)


def test_tasks_adds_row_for_issue_without_one(tmp_path, capsys):
    f = tmp_path / "tasks.json"
    f.write_text(
        json.dumps({"rows": [{"path": ".scratch/a/issues/01-x.md", "stage": "Ready"}]})
    )
    st = tmp_path / "stages.tsv"
    st.write_text(
        ".scratch/a/issues/01-x.md\tDone\n.scratch/a/issues/02-new.md\tReady\n"
    )
    assert tasks.main([str(f), "--stages", str(st), "--note", "other.md=n"]) == 0
    rows = json.loads(f.read_text())["rows"]
    assert rows[0]["stage"] == "Done"
    assert rows[1] == {
        "path": ".scratch/a/issues/02-new.md",
        "feature": "a",
        "title": "02-new",
        "stage": "Ready",
    }
    out = capsys.readouterr().out
    assert "added row for .scratch/a/issues/02-new.md" in out
    assert "no row for other.md" in out and "no row for .scratch" not in out


def test_inputs_survives_missing_merge_base(tmp_path):
    def git(*a, cwd):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)

    up, work = tmp_path / "up", tmp_path / "work"
    up.mkdir()
    git("init", "-q", "-b", "master", cwd=up)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "m",
        cwd=up,
    )
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "checkout",
        "-q",
        "--orphan",
        "pr",
        cwd=up,
    )
    (up / "f").write_text("x")
    git("add", "f", cwd=up)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "p", cwd=up)
    git("update-ref", "refs/pull/7/head", "HEAD", cwd=up)
    git("clone", "-q", str(up), str(work), cwd=tmp_path)
    board = tmp_path / "board.json"
    board.write_text(json.dumps({"rows": [{"board": "prs", "pr": 7}]}))
    r = subprocess.run(
        ["bash", str(COORD / "inputs.sh"), str(board), str(tmp_path / "o")],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "no merge base for #7" in r.stderr
