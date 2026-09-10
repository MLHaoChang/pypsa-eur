"""
Write `tests/golden/chat_turn_frames.json` from the scenarios in
`tests/test_chat_turn_frame_contract.py`.

Deliberately a separate entry point rather than a `--update` flag on the test:
re-recording is a decision, and a flag on the test invites making it by reflex
when the gate goes red. Run this, read `git diff` on the JSON, and only then
commit it.

    python -m tests.record_chat_turn_frames        # from pypsa-gui/backend
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> int:
    import pytest

    # The scenarios need the suite's fixtures (tmp_projects_dir, install_network)
    # and its environment pinning, so recording runs THROUGH pytest rather than
    # standing the harness up a second time — a second copy would drift, which
    # is the failure `tests/qa_support.py` was written to avoid elsewhere.
    code = pytest.main([
        "-q", "-p", "no:warnings", "--no-header",
        "tests/test_chat_turn_frame_contract.py::test_record",
        "--record-chat-frames",
        "-o", "python_files=test_*.py",
    ])
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
