# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Brief assembly by ``scripts/coord/brief.py`` from ``docs/agents/session-brief.md``."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "brief.py"


def brief(tmp_path, *args):
    out = tmp_path / "brief.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.read_text(), result.stdout


def test_fields_filled_and_standard_paragraphs_kept(tmp_path):
    text, stdout = brief(
        tmp_path,
        "--task", "Build X. Done when Y.",
        "--decisions", "USE-2,OWN-PR",
        "--read", "docs/a.md: why",
        "--model", "claude-sonnet-5-5",
        "--title", "Build · X",
    )
    assert "**Task.** Build X. Done when Y." in text
    assert "**Owner decisions.** `USE-2`, `OWN-PR`" in text
    assert "**Read first.**\n- docs/a.md: why" in text
    assert "**Dependencies.** none" in text
    assert "**Context budget.** Hand off at 300k" in text
    assert "RETRO-QUIET-1" in text
    assert "<" not in text.replace("<full head SHA>", "")
    assert "CONFIDENTIALITY" not in text
    assert "model=claude-sonnet-5-5" in stdout and "team:build" in stdout


def test_dc_stream_appends_sagc_text_verbatim(tmp_path):
    text, _ = brief(tmp_path, "--task", "t", "--stream", "dc")
    assert (
        "CONFIDENTIALITY (owner decision SAGC-1).** DC issues 09–19 derive from a "
        "client's confidential documents. In code, tests, PR text, commits and "
        "messages cite only the public SAGC (May 2026) and generic engineering; "
        "never a client name, network, MW figure or SOW text."
    ) in text


def test_stay_out_from_open_prs_and_sessions(tmp_path):
    prs = tmp_path / "prs.txt"
    prs.write_text(
        "5 pypsa-gui/backend/harness/a.py\n5 pypsa-gui/backend/harness/b.py\n"
        "6 gridspine/solve.py\n7 docs/own.md\n"
    )
    sessions = tmp_path / "s.txt"
    sessions.write_text("abc running Build · RP-04 — tab\n")
    text, _ = brief(
        tmp_path, "--task", "t", "--open-prs", str(prs), "--sessions", str(sessions),
        "--exclude-pr", "7",
    )
    assert "open PR #5: `pypsa-gui/backend/harness/` (2)" in text
    assert "open PR #6: `gridspine/` (1)" in text
    assert "#7" not in text
    assert "live session: abc running Build · RP-04 — tab" in text
