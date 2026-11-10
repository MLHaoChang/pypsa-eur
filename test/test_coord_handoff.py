# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Message extraction by ``scripts/coord/handoff.py`` from a session transcript."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "handoff.py"

NOTE = "# Hand-off\n    PR #12 is green &amp; ready; a &lt;b&gt; tag.\n    Next: slice 3.\nEND-OF-NOTE"
CLEAN = "# Hand-off\nPR #12 is green & ready; a <b> tag.\nNext: slice 3.\nEND-OF-NOTE"


def handoff(tmp_path, records, start="# Hand-off", end="END-OF-NOTE"):
    (tmp_path / "t.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records) + "not json\n"
    )
    out = tmp_path / "note.md"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), start, end, str(out)],
        capture_output=True,
        text=True,
        env={**os.environ, "HANDOFF_TRANSCRIPTS": str(tmp_path)},
    )
    return r, out


def test_cuts_between_the_markers_unindents_and_unescapes(tmp_path):
    message = {
        "message": {"content": [{"type": "text", "text": f"intro\n{NOTE}\nbye"}]}
    }
    r, out = handoff(tmp_path, [{"type": "user"}, message])
    assert r.returncode == 0, r.stderr
    assert out.read_text() == CLEAN
    assert r.stdout.strip() == str(len(CLEAN))


def test_the_shortest_string_wins_and_a_queue_operation_first_line_is_read(tmp_path):
    queued = {"type": "queue-operation", "content": f"x {NOTE} y"}
    wrapper = {"toolUseResult": {"stdout": f"lots of other output\n{NOTE}\nmore " * 2}}
    r, out = handoff(tmp_path, [queued, wrapper])
    assert r.returncode == 0, r.stderr
    assert out.read_text() == CLEAN


def test_cuts_to_the_last_end_marker(tmp_path):
    text = "# Hand-off\nstep END-OF-NOTE is quoted\nreal END-OF-NOTE tail"
    r, out = handoff(tmp_path, [{"content": text}])
    assert out.read_text() == "# Hand-off\nstep END-OF-NOTE is quoted\nreal END-OF-NOTE"


def test_its_own_source_and_command_line_are_skipped(tmp_path):
    call = {
        "input": {
            "command": "python3 scripts/coord/handoff.py '# Hand-off' 'END-OF-NOTE' n.md"
        }
    }
    source = {"content": "def walk(value):\n    # Hand-off END-OF-NOTE"}
    r, out = handoff(
        tmp_path, [call, source, {"content": f"a much longer wrapper {NOTE}"}]
    )
    assert out.read_text() == CLEAN
    out.unlink()
    r, out = handoff(tmp_path, [call, source])
    assert r.returncode == 1 and not out.exists()


def test_no_match_exits_one_with_a_message(tmp_path):
    r, out = handoff(tmp_path, [{"content": "END-OF-NOTE comes before # Hand-off"}])
    assert r.returncode == 1 and "holds both markers" in r.stderr
    assert not out.exists()
