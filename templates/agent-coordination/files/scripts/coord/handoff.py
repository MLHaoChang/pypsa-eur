# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Copy one message's text from this session's transcript into a file.

A hand-off note that arrives as a message is already in the transcript; this
writes it to a file without the session typing it out again::

    python3 scripts/coord/handoff.py '<start-marker>' '<end-marker>' note.md

Of every string in the transcripts (``~/.claude/projects/<repo path>/*.jsonl``,
or the folder in ``HANDOFF_TRANSCRIPTS``) that holds both markers, the shortest
one is taken, which is the message itself rather than a wrapper around it. It is
cut from the start marker to the end of the last end marker, a 4-space indent is
removed from each line and HTML entities are unescaped. Prints the character
count; exits 1 when no string holds both markers. Pick markers that occur only
in the note, such as its first heading and its last line.
"""

import html
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def walk(value):
    """Every string inside a decoded JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def extract(lines, start, end):
    """The shortest string holding both markers, cut and cleaned; None when there is none."""
    # This script's own source and its own command line hold the markers too.
    own_call = re.compile(r"handoff\.py\b[^\n]*" + re.escape(start))
    best = None
    for raw in lines:
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        for text in walk(record):
            if start not in text or end not in text:
                continue
            if "def walk" in text or own_call.search(text):
                continue
            if text.rfind(end) < text.find(start):
                continue
            if best is None or len(text) < len(best):
                best = text
    if best is None:
        return None
    best = best[best.find(start) : best.rfind(end) + len(end)]
    return html.unescape(re.sub(r"^ {4}", "", best, flags=re.M))


def main(argv):
    if len(argv) != 3 or not argv[0] or not argv[1]:
        sys.exit("usage: handoff.py <start-marker> <end-marker> <out-file>")
    start, end, out = argv
    folder = os.environ.get("HANDOFF_TRANSCRIPTS") or (
        Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    )
    lines = []
    for transcript in sorted(Path(folder).glob("*.jsonl")):
        with transcript.open(encoding="utf-8", errors="replace") as handle:
            lines.extend(handle)
    text = extract(lines, start, end)
    if text is None:
        sys.exit(f"handoff: no transcript string in {folder} holds both markers")
    Path(out).write_text(text, encoding="utf-8")
    print(len(text))


if __name__ == "__main__":
    main(sys.argv[1:])
