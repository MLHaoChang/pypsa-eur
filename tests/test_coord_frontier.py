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


def issue(root, effort, name, title, status="ready-for-agent"):
    d = root / ".scratch" / effort / "issues"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"# {title}\n\nStatus: {status}\nBlocked by: none\n", encoding="utf-8"
    )


def test_session_naming_issue_id_holds_it(tmp_path):
    issue(tmp_path, "lc", "48f-x.md", "LC-48f: thing")
    issue(tmp_path, "sh", "35-a.md", "A")
    issue(tmp_path, "sh", "36-b.md", "B")
    issue(tmp_path, "sh", "37-c.md", "C")
    issues = frontier.load_issues(tmp_path)
    frontier.evaluate(
        issues, {}, "session_1 building LC-48f\nsession_2 SH 35/36 together"
    )
    stage = {i["path"].split("/")[-1]: i["stage"] for i in issues.values()}
    assert stage == {
        "48f-x.md": "In progress",
        "35-a.md": "In progress",
        "36-b.md": "In progress",
        "37-c.md": "Ready",
    }


def test_owner_hold_row_lists_issue_under_held(tmp_path, capsys):
    issue(tmp_path, "rp", "12-w.md", "RP-12: weeks")
    issue(tmp_path, "sc", "05-x.md", "SC-05: x")
    issue(tmp_path, "sc", "07-y.md", "SC-07: y")
    dec = tmp_path / "docs" / "agents" / "owner-decisions.md"
    dec.parent.mkdir(parents=True)
    dec.write_text(
        "| Date | Id | Question | Decision | By |\n|---|---|---|---|---|\n"
        "| 2026-11-11 | HOLD-1111 | Keep RP-12 and SC-04/05/06 held? | Owner: keep them held. | c |\n"
        "| 2026-11-12 | OTHER-1 | SC-07 question | yes | c |\n",
        encoding="utf-8",
    )
    frontier.main(["--root", str(tmp_path), "--held"])
    out = capsys.readouterr().out
    assert out.startswith("frontier 1\n") and "07-y.md" in out.split("held")[0]
    assert "12-w.md  Held by owner decision HOLD-1111" in out
    assert "05-x.md  Held by owner decision HOLD-1111" in out


def test_soft_and_hard_overlap_lines(tmp_path):
    issue(tmp_path, "e", "01-a.md", "A")
    p = tmp_path / ".scratch/e/issues/01-a.md"
    p.write_text(
        p.read_text() + "\n`pypsa-gui/backend/harness/` and `pypsa-gui/backend/x/`\n"
    )
    issues = frontier.load_issues(tmp_path)
    prs = {
        "9": {
            "pypsa-gui/backend/harness/README.md",
            "pypsa-gui/backend/harness/catalogue.py",
        },
        "10": {"pypsa-gui/backend/x/a.py"},
    }
    frontier.evaluate(issues, prs, "")
    text = frontier.line(next(iter(issues.values())))
    assert "  soft #9: " in text and "  overlap #10: pypsa-gui/backend/x/a.py" in text
    assert "overlap #9" not in text


def test_waivers_conf_matches_premerge_regexes():
    w = frontier.load_waivers()
    assert set(w) == {"docs_only_re", "catalogue_re", "inventory_overlap_re"}
    assert frontier.is_soft("doc/release-notes.d/a.md", w)
    assert not frontier.is_soft("pypsa-gui/backend/harness/catalogue_x.py", w)
    assert not frontier.is_soft("pypsa-gui/backend/services/a.py", w)
