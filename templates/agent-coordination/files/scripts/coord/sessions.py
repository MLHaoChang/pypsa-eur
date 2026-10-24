# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Summarise a saved ``list_sessions`` result, one line per live session.

Save the Claude Code Remote ``list_sessions`` output to a file (bare JSON, or
the tool-results file as saved, wrapper included), then run::

    python scripts/coord/sessions.py sessions.json

Each line shows the id suffix, status, last update, title, status category,
status detail, context used (``context_usage.used_tokens``) and whether the
session needs action. A session past 200k tokens of context is flagged
``ROTATE``: the coordinator asks it for a hand-off note and a fresh session.
``list_sessions`` does not report context reliably: ``?`` means unknown.
Before deciding on rotation, the coordinator must read ``get_session``'s
``context_usage``; this script reads a saved file and cannot call tools.
Archived sessions are left out. Missing fields print as ``-``, except context,
which prints ``?``.
"""

import argparse
import json
import math
import sys
from pathlib import Path

ARCHIVED = {"SESSION_STATUS_ARCHIVED", "archived"}
ROTATE_TOKENS = 200_000


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


def context_tokens(session):
    """Used context tokens, or None when missing or reported as zero."""
    for holder in (session, session.get("session_context") or {}):
        tokens = (holder.get("context_usage") or {}).get("used_tokens")
        if (isinstance(tokens, (int, float)) and not isinstance(tokens, bool)
                and math.isfinite(tokens) and tokens > 0):
            return int(tokens)
    return None


def fmt_context(tokens):
    if tokens is None:
        return "?"
    flag = " ROTATE" if tokens > ROTATE_TOKENS else ""
    return f"{round(tokens / 1000)}k{flag}"


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
            fmt_context(context_tokens(session)),
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
