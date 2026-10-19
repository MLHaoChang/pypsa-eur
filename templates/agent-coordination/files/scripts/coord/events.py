# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Summarise a saved ``list_events`` result: what a session said and did.

Save the Claude Code Remote ``list_events`` output to a file (bare JSON, or the
tool-results file as saved, wrapper included), then run::

    python scripts/coord/events.py events.json --chars 200

Each assistant event prints its time, its text cut to ``--chars`` characters
(default 200) and the name of its last tool call. Events without assistant
text or a tool call are left out. Use it on a ``list_events`` call made with
``kinds=["assistant"]``; it is the twin of ``sessions.py``.
"""

import argparse
import sys
from pathlib import Path

from sessions import load


def events_from(payload):
    """Return the event list from the raw tool output, wrapped or not."""
    if isinstance(payload, list):
        return payload
    for key in ("ccr", "result"):
        if isinstance(payload.get(key), dict):
            return events_from(payload[key])
    return payload.get("data") or payload.get("events") or []


def content_of(event):
    """The content blocks of an assistant event, whichever way it is nested."""
    body = event.get("data") if isinstance(event.get("data"), dict) else event
    body = body.get("assistant") if isinstance(body.get("assistant"), dict) else body
    if body.get("type", "assistant") != "assistant":
        return []
    message = body.get("message") if isinstance(body.get("message"), dict) else body
    content = message.get("content") or []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)]


def when(event):
    body = event.get("data") if isinstance(event.get("data"), dict) else {}
    stamp = (
        event.get("created_at")
        or event.get("timestamp")
        or body.get("timestamp")
        or body.get("created_at")
        or ""
    )
    return str(stamp)[:19].replace("T", " ") or "-"


def row(event, chars):
    blocks = content_of(event)
    text = " ".join(
        b.get("text", "").strip() for b in blocks if b.get("type") == "text"
    )
    text = " ".join(text.split())
    if len(text) > chars:
        text = text[:chars].rstrip() + "…"
    tools = [b.get("name", "?") for b in blocks if b.get("type") == "tool_use"]
    if not text and not tools:
        return None
    return "  ".join([when(event), text or "-", tools[-1] if tools else "-"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="saved list_events JSON")
    parser.add_argument("--chars", type=int, default=200, help="text length cap")
    args = parser.parse_args(argv)
    for event in events_from(load(args.file.read_text())):
        line = row(event, args.chars)
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
