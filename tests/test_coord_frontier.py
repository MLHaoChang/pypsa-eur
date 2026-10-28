# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""frontier.py: lettered issue files (04b-) load and can be blocked on."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "frontier", Path(__file__).parents[1] / "scripts" / "coord" / "frontier.py"
)
frontier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(frontier)


def write(root, name, status, blocked="none"):
    d = root / ".scratch" / "eff" / "issues"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"# Issue {name}\n\nStatus: {status}\nBlocked by: {blocked}\n", encoding="utf-8"
    )


def test_blockers_keep_letter():
    assert frontier.blockers_of(["Blocked by: 04b"], "")[0] == ["04b"]
    assert frontier.blockers_of(["Blocked by: 04, 06a"], "")[0] == ["04", "06a"]
    assert frontier.blockers_of(["Blocked by: 2-4"], "")[0] == ["02", "03", "04"]
    assert frontier.blockers_of([], "ready; still blocked by 5a")[0] == ["05a"]


def test_lettered_issues_load_and_stage(tmp_path):
    write(tmp_path, "04-base.md", "done")
    write(tmp_path, "04b-extra.md", "ready-for-agent", "04")
    write(tmp_path, "05-next.md", "ready-for-agent", "04b")
    write(tmp_path, "06a-later.md", "ready-for-agent", "04, 06")
    issues = frontier.load_issues(tmp_path)
    frontier.evaluate(issues, {}, "")
    stage = {i["num"]: i["stage"] for i in issues.values()}
    assert stage == {
        "04": "Done",
        "04b": "Ready",
        "05": "Blocked by 04b",
        "06a": "Blocked by 06",
    }
