# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Summarise a saved ``list_sessions`` result, one line per live session.

Save the Claude Code Remote ``list_sessions`` output to a file (bare JSON, or
the tool-results file as saved, wrapper included), then run::

    python scripts/coord/sessions.py sessions.json

Each line shows the id suffix, status, last update, title, status category,
status detail and whether the session needs action. Archived sessions are
left out. Fields a session lacks print as ``-``.
"""

import argparse
import json
import sys
from pathlib import Path

ARCHIVED = {"SESSION_STATUS_ARCHIVED", "archived"}


def strip_prefix(value, prefix):
    if isinstance(value, str) and value.startswith(prefix):
        return value[len(prefix) :].lower()
    return value


def sessions_from(payload):
    """Return the session list from the raw tool output, wrapped or not."""
    if isinstance(payload, list):
        return payload
    for key in ("ccr", "result"):
        if isinstance(payload.get(key), dict):
            return sessions_from(payload[key])
    return payload.get("data") or payload.get("sessions") or []


def fmt(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("\n", " ")


def load(text):
    """Parse a saved result: bare JSON, or JSON inside the tool's text wrapper."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.index('{"ccr"') if '{"ccr"' in text else text.index("{")
        return json.JSONDecoder().raw_decode(text, start)[0]
    # A result saved as the tool's content blocks: [{"type": "text", "text": ...}].
    if (
        isinstance(payload, list)
        and payload
        and all(isinstance(b, dict) and b.get("type") == "text" for b in payload)
    ):
        return load("".join(b.get("text", "") for b in payload))
    return payload


def row(session):
    summary = session.get("post_turn_summary") or {}
    status = session.get("session_status") or session.get("status")
    category = (
        summary.get("status_category")
        or session.get("status_category")
        or session.get("status_bucket")
    )
    updated = session.get("updated_at") or ""
    return "  ".join(
        [
            fmt(session.get("id", "")[-6:]),
            fmt(strip_prefix(status, "SESSION_STATUS_")),
            fmt(updated[:16].replace("T", " ")),
            fmt(session.get("title")),
            fmt(strip_prefix(category, "SESSION_STATUS_BUCKET_")),
            fmt(
                summary.get("status_detail")
                or session.get("status_detail")
                or (session.get("external_metadata") or {}).get("task_summary")
            ),
            fmt(summary.get("needs_action", session.get("needs_action"))),
        ]
    )


def live(sessions):
    return [
        s
        for s in sessions
        if s.get("session_status") not in ARCHIVED and s.get("status") not in ARCHIVED
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="saved list_sessions JSON")
    args = parser.parse_args(argv)
    for session in live(sessions_from(load(args.file.read_text()))):
        print(row(session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
