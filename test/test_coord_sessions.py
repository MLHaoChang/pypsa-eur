# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Parsing of saved ``list_sessions`` results by ``scripts/coord/sessions.py``."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "sessions.py"

PAYLOAD = {
    "ccr": {
        "data": [
            {
                "id": "session_01AAAAAAAAidle01",
                "session_status": "SESSION_STATUS_IDLE",
                "updated_at": "2026-10-14T19:14:00Z",
                "title": "Done work",
                "post_turn_summary": {
                    "status_category": "completed",
                    "status_detail": "PR #1 merged",
                    "needs_action": False,
                },
            },
            {
                "id": "session_01AAAAAAAArun002",
                "session_status": "SESSION_STATUS_RUNNING",
                "updated_at": "2026-10-14T19:15:00Z",
                "title": "Busy work",
                "status_bucket": "SESSION_STATUS_BUCKET_WORKING",
                "external_metadata": {"task_summary": "writing the plan"},
            },
            {
                "id": "session_01AAAAAAAAarch03",
                "session_status": "SESSION_STATUS_ARCHIVED",
                "title": "Old work",
            },
        ]
    }
}


def run(tmp_path, text):
    path = tmp_path / "sessions.txt"
    path.write_text(text)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.splitlines()


def test_bare_json(tmp_path):
    lines = run(tmp_path, json.dumps(PAYLOAD))
    assert len(lines) == 2
    assert (
        "PR #1 merged" in lines[0]
        and "completed" in lines[0]
        and lines[0].endswith("no")
    )
    assert "writing the plan" in lines[1] and "working" in lines[1]


def test_saved_tool_output_wrapper(tmp_path):
    text = (
        '<other-session nonce="x" untrusted="true">\nAnother session\'s record.\n'
        + json.dumps(PAYLOAD)
        + "\n</other-session>\n"
    )
    lines = run(tmp_path, text)
    assert len(lines) == 2
    assert "Old work" not in "\n".join(lines)


def test_saved_content_blocks(tmp_path):
    wrapped = (
        '<other-session nonce="x">\nRecord:\n    '
        + json.dumps(PAYLOAD)
        + "\n</other-session>"
    )
    lines = run(tmp_path, json.dumps([{"type": "text", "text": wrapped}]))
    assert len(lines) == 2
    assert "PR #1 merged" in lines[0]
