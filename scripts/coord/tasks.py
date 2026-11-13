# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

r"""
Apply issue stages and merge notes to the Feature-progress dashboard's ``tasks.json``.

::

    python3 scripts/coord/frontier.py --stages --open-prs prs.txt --sessions s.txt > stages.tsv
    python3 scripts/coord/tasks.py tasks.json --stages stages.tsv \
        --merged 1457=.scratch/g2p/issues/16-x.md --note ".scratch/g2p/issues/17-y.md=waits on owner"

- ``--stages FILE``: ``frontier.py --stages`` output (``<issue path><TAB><stage>`` per
  line). Every row of ``tasks.json`` whose ``path``, ``issue``, ``id`` or ``file`` is
  that issue path gets its ``stage``.
- ``--merged PR=<issue path>`` (repeatable): the issue's PR merged; it is Done with
  note ``merged #PR`` even when its file still lacks ``Status: done``. Applied after
  ``--stages``.
- ``--note <issue path>=TEXT`` (repeatable): sets the row's ``note``.

``tasks.json`` is a list of rows or ``{"rows": [...]}``; the file is rewritten in
place and a one-line summary printed. A path that matches no row is reported, so a
renamed issue is not silently dropped.
"""

import argparse
import json
import sys
from pathlib import Path

KEYS = ("path", "issue", "id", "file")


def rows_of(data):
    return data["rows"] if isinstance(data, dict) else data


def find(rows, path):
    return [r for r in rows if any(r.get(k) == path for k in KEYS)]


def pair(text, what):
    left, sep, right = text.partition("=")
    if not sep or not left or not right:
        sys.exit(f"tasks: {what} wants KEY=VALUE, got {text!r}")
    return left, right


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tasks", type=Path)
    ap.add_argument("--stages", type=Path)
    ap.add_argument("--merged", action="append", default=[], metavar="PR=PATH")
    ap.add_argument("--note", action="append", default=[], metavar="PATH=TEXT")
    args = ap.parse_args(argv)

    data = json.loads(args.tasks.read_text(encoding="utf-8"))
    rows = rows_of(data)
    changed, missing = 0, []

    def put(path, **fields):
        nonlocal changed
        hit = find(rows, path)
        if not hit:
            missing.append(path)
        for r in hit:
            if any(r.get(k) != v for k, v in fields.items()):
                changed += 1
            r.update(fields)

    if args.stages:
        for line in args.stages.read_text(encoding="utf-8").splitlines():
            path, sep, stage = line.partition("\t")
            if sep and stage.strip():
                put(path, stage=stage.strip())
    for text in args.merged:
        pr, path = pair(text, "--merged")
        put(path, stage="Done", note=f"merged #{pr.lstrip('#')}")
    for text in args.note:
        path, note = pair(text, "--note")
        put(path, note=note)

    args.tasks.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"tasks: {changed} field change(s) in {len(rows)} rows")
    for path in sorted(set(missing)):
        print(f"tasks: no row for {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
