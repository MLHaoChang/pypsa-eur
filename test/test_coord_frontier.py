# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Frontier and stages from issue headers, by ``scripts/coord/frontier.py``."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "frontier.py"

ISSUES = {
    "g2p/issues/14-assistant.md": "# Assistant\n\nStatus: done (#397, f7f92d5e)\n",
    "g2p/issues/15-heat.md": "# Heat bands\n\nStatus: needs-triage\nBlocked by: 14\n",
    "g2p/issues/16-cop.md": (
        "# COP by band\n\nStatus: ready-for-agent (owner 2026-10-23, Q16; "
        "still blocked by 15)\nBlocked by: 15\nGated: no\nModel tier: sonnet\n"
    ),
    "g2p/issues/17-harmonics.md": (
        "# Harmonics\n\nStatus: ready-for-agent\nBlocked by: 14 (done)\n"
        "Gated: **yes** (gridspine)\nModel tier: opus\n\n"
        "Touches `gridspine/solve.py` and `pypsa-gui/backend/api/`.\n"
    ),
    "g2p/issues/18-pending.md": "# Pending\n\nStatus: done (PR pending)\n",
    "g2p/issues/19-after-pending.md": (
        "# After pending\n\nStatus: ready-for-agent\nBlocked by: 18\nModel tier: sonnet\n"
    ),
    "g2p/issues/20-asked.md": "# Asked\n\nStatus: needs-info\n",
    "g2p/issues/21-claimed.md": "# Claimed\n\nStatus: ready-for-agent\nBlocked by: none\n",
    "rp/issues/01-first.md": "# RP-01 — first\n\nStatus: done\n",
    "rp/issues/02-second.md": (
        "# RP-02 — second\n\nStatus: ready-for-agent\nBlocked by: RP-01\n"
    ),
    "rp/issues/03-third.md": (
        "# RP-03 — third\n\nStatus: ready-for-agent\nBlocked by: RP-02\n"
    ),
    "rp/issues/04-wont.md": "# Wont\n\nStatus: wontfix\n",
    "rp/issues/05-bold.md": (
        "# Bold\n\n**Status `ready-for-agent`, gated, size S, sonnet.** Go.\n"
        "Blocked by: 01\n"
    ),
}


def make_root(tmp_path):
    for rel, text in ISSUES.items():
        path = tmp_path / ".scratch" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def run(root, *args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def stages(root, *args):
    out = run(root, "--stages", *args)
    return {
        line.split("\t")[0].split("/issues/")[0].removeprefix(".scratch/")
        + "/"
        + line.split("/issues/")[1].split("\t")[0][:2]: line.split("\t")[1]
        for line in out.splitlines()
    }


def test_issue_16_blocked_by_triage_issue_is_not_on_the_frontier(tmp_path):
    root = make_root(tmp_path)
    out = run(root)
    assert "g2p/issues/16-cop.md" not in out
    assert stages(root)["g2p/16"] == "Blocked by 15"


def test_frontier_lists_unblocked_ready_issues_with_tier_and_gate(tmp_path):
    out = run(make_root(tmp_path))
    assert out.splitlines()[0] == "frontier 4"
    line = next(l for l in out.splitlines() if "17-harmonics" in l)
    assert "opus  gated  Harmonics" in line
    bold = next(l for l in out.splitlines() if "05-bold" in l)
    assert "sonnet  gated" in bold
    assert "19-after-pending" not in out  # blocker's PR not merged yet
    assert "03-third" not in out  # tag blocker RP-02 is not done


def test_stages_from_headers(tmp_path):
    got = stages(make_root(tmp_path))
    assert got["g2p/14"] == "Done"
    assert got["g2p/15"] == "Needs triage"
    assert got["g2p/18"] == "In review"
    assert got["g2p/19"] == "Blocked by 18"
    assert got["g2p/20"] == "Needs owner"
    assert got["g2p/21"] == "Ready"
    assert got["rp/03"] == "Blocked by RP-02"
    assert got["rp/04"] == "Won't do"


def test_sessions_and_open_prs_take_issues_off_the_frontier(tmp_path):
    root = make_root(tmp_path)
    sessions = tmp_path / "sessions.txt"
    sessions.write_text("abc idle Build · g2p 17 — harmonics\n.scratch/rp/issues/02-second.md\n")
    prs = tmp_path / "prs.txt"
    prs.write_text("#12 .scratch/g2p/issues/21-claimed.md\n12 gridspine/other.py\n")
    out = run(root, "--sessions", str(sessions), "--open-prs", str(prs))
    assert "17-harmonics" not in out and "02-second" not in out
    assert "21-claimed" not in out
    got = stages(root, "--sessions", str(sessions), "--open-prs", str(prs))
    assert got["g2p/17"] == "In progress"
    assert got["rp/02"] == "In progress"
    assert got["g2p/21"] == "In review"


def test_overlap_with_open_pr_files_named_in_the_issue(tmp_path):
    root = make_root(tmp_path)
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps({"77": ["gridspine/solve.py", "docs/x.md"]}))
    line = next(
        l for l in run(root, "--open-prs", str(prs)).splitlines() if "17-harmonics" in l
    )
    assert "overlap #77: gridspine/solve.py" in line


def test_json_and_held(tmp_path):
    root = make_root(tmp_path)
    data = json.loads(run(root, "--json"))
    assert {f["path"].split("/")[-1] for f in data["frontier"]} >= {"17-harmonics.md"}
    held = {h["path"].split("/")[-1]: h["reason"] for h in data["held"]}
    assert held["16-cop.md"] == "Blocked by 15"
    assert "held" in run(root, "--held")
