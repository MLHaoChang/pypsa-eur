# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Print the exact ``create_session`` arguments for a brief ``brief.py`` wrote.

    python3 scripts/coord/spawn.py <brief-file>

``brief.py --out brief.md`` also writes ``brief.md.create`` (model, title, tags,
source_url). This prints those, so the coordinator calls ``create_session`` itself
with the brief file's text as ``prompt``: about 1.5k tokens inline, against 50-60k
for a subagent spawn. Exits 2 when the brief or its arguments are missing, or when
model or title was not given to ``brief.py`` (a child without ``model`` inherits the
coordinator's).
"""

import json
import sys
from pathlib import Path


def main(argv):
    if len(argv) != 1:
        sys.exit("usage: spawn.py <brief-file>")
    brief = Path(argv[0])
    side = Path(f"{brief}.create")
    if not brief.is_file() or not side.is_file():
        sys.exit(f"spawn: need {brief} and {side} (written by brief.py --out {brief})")
    args = json.loads(side.read_text())
    missing = [k for k in ("model", "title", "source_url") if not args.get(k)]
    if missing:
        sys.exit(
            f"spawn: {', '.join(missing)} not set; rerun brief.py with --model and --title"
        )
    print(f"model: {args['model']}")
    print(f"title: {args['title']}")
    print(f"tags: {json.dumps(args['tags'])}")
    print(f"source_url: {args['source_url']}")
    print(f"prompt: the text of {brief} ({len(brief.read_text().split())} words)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
