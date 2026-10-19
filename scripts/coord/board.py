# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

r"""
Edit the dashboard's ``board.json`` from flags and log the change, in one call.

Run it from the coordinator's scratchpad (or pass ``--dir``), with any mix of::

    python scripts/coord/board.py \
        --session "Package" --doing "#12 in CI" --waiting-on CI --needs-you no \
        --remove-session "Old package" \
        --merged 11 --merged-title "Short title" --merged-stream "Package" \
        --pr 12 --title "Short title" --stream "Package" --state Testing \
        --remove-pr 10 \
        --log "gatekeeper OK #12 at 1a2b3c4d"

- ``--session NAME`` adds or updates the sessions row (matched by name); give
  any of ``--doing``, ``--waiting-on`` and ``--needs-you``.
- ``--remove-session NAME`` drops it.
- ``--merged N`` appends a merged row and drops PR N from the prs board. Its
  title and stream come from ``--merged-title`` and ``--merged-stream``, else
  from the dropped prs row; the time is ``--at`` or now in the owner's zone.
- ``--pr N`` adds or updates the prs row; give any of ``--title``, ``--stream``,
  ``--state``, ``--eta``, ``--head``, ``--unblocks`` and ``--note``.
- ``--remove-pr N`` drops it.

Every call appends one ``- HH:MMZ ...`` line to ``coordinator-state.md``
(your ``--log`` text, else a summary of the edits) and prints the board's path,
ready for the Artifact upload. Files live under ``--dir`` (default
``$COORD_DIR``, else the current directory): ``board/board.json`` and
``coordinator-state.md``.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OWNER_TZ = os.environ.get("COORD_TZ", "Europe/Berlin")
PR_FIELDS = ("title", "stream", "state", "eta", "head", "unblocks", "note")


def upsert(rows, board, key, value, fields):
    """Update the ``board`` row whose ``key`` is ``value``, or append one."""
    for row in rows:
        if row.get("board") == board and str(row.get(key)) == str(value):
            row.update(fields)
            return "updated"
    rows.append({"board": board, key: value, **fields})
    return "added"


def drop(rows, board, key, value):
    kept = [
        r
        for r in rows
        if not (r.get("board") == board and str(r.get(key)) == str(value))
    ]
    removed = len(rows) - len(kept)
    rows[:] = kept
    return removed


def apply(rows, args, now):
    """Edit ``rows`` in place; return a short note for each edit made."""
    notes = []
    given = {k: v for k, v in vars(args).items() if v is not None}

    if args.session:
        fields = {
            col: given[opt]
            for opt, col in (
                ("doing", "doing"),
                ("waiting_on", "waiting_on"),
                ("needs_you", "needs_you"),
            )
            if opt in given
        }
        verb = upsert(rows, "sessions", "session", args.session, fields)
        notes.append(f"session {args.session!r} {verb}")
    if args.remove_session:
        n = drop(rows, "sessions", "session", args.remove_session)
        notes.append(f"session {args.remove_session!r} removed ({n})")

    if args.pr is not None:
        fields = {k: given[k] for k in PR_FIELDS if k in given}
        verb = upsert(rows, "prs", "pr", args.pr, fields)
        notes.append(f"pr #{args.pr} {verb}")
    if args.remove_pr is not None:
        n = drop(rows, "prs", "pr", args.remove_pr)
        notes.append(f"pr #{args.remove_pr} removed ({n})")

    if args.merged is not None:
        old = next(
            (
                r
                for r in rows
                if r.get("board") == "prs" and str(r.get("pr")) == str(args.merged)
            ),
            {},
        )
        drop(rows, "prs", "pr", args.merged)
        at = args.at or now.astimezone(ZoneInfo(OWNER_TZ)).strftime("%H:%M")
        rows.append(
            {
                "board": "merged",
                "pr": args.merged,
                "title": args.merged_title or old.get("title", ""),
                "stream": args.merged_stream or old.get("stream", ""),
                "merged": at,
            }
        )
        notes.append(f"pr #{args.merged} merged {at}")
    return notes


def parser():
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="See the module docstring for the full flag list.",
    )
    p.add_argument("--dir", type=Path, default=Path(os.environ.get("COORD_DIR", ".")))
    p.add_argument("--log", help="state-log text (default: a summary of the edits)")
    p.add_argument("--session")
    p.add_argument("--remove-session")
    p.add_argument("--doing")
    p.add_argument("--waiting-on")
    p.add_argument("--needs-you", choices=("yes", "no"))
    p.add_argument("--pr", type=int)
    p.add_argument("--remove-pr", type=int)
    p.add_argument("--merged", type=int)
    p.add_argument("--merged-title")
    p.add_argument("--merged-stream")
    p.add_argument("--at", help="merged time HH:MM (default: now)")
    for field in PR_FIELDS:
        p.add_argument(f"--{field}")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    board = args.dir / "board" / "board.json"
    state = args.dir / "coordinator-state.md"
    data = json.loads(board.read_text()) if board.exists() else {"rows": []}
    now = datetime.now(UTC)

    notes = apply(data["rows"], args, now)
    if not notes and not args.log:
        print("nothing to do: give a session, pr, merged or log flag", file=sys.stderr)
        return 2

    board.parent.mkdir(parents=True, exist_ok=True)
    rows = ",\n".join("  " + json.dumps(r) for r in data["rows"])
    board.write_text('{"rows": [\n' + rows + "\n]}\n")
    line = f"- {now.strftime('%H:%MZ')} {args.log or 'board: ' + '; '.join(notes)}\n"
    with state.open("a") as fh:
        fh.write(line)
    print(board.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
