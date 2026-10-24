# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Read JSONL comments; emit first-line review markers as TSV, not identities."""
import json
import os
import re
import sys

VERDICT = re.compile(r"^(GK|UX) (NOT OK|OK) @([0-9a-f]{7,40})")
LOOKS_LIKE_VERDICT = re.compile(r"(?:GK|UX) (?:NOT OK|OK) @[0-9a-f]{7,40}")


def verdicts(comments, allowed_logins=None):
    # This filters comment shape, not reviewer identity. Sessions currently
    # share one GitHub login and footer; a forged first-line marker still counts.
    for number, raw in enumerate(comments, 1):
        if not raw.strip():
            continue
        try:
            comment = json.loads(raw)
            if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
                raise ValueError("expected an object with a string body")
        except (ValueError, TypeError) as exc:
            print(f"comment line {number}: invalid JSON comment: {exc}", file=sys.stderr)
            continue
        login = comment.get("login", "unknown")
        lines = [line for line in comment["body"].splitlines() if line.strip()]
        for index, line in enumerate(lines):
            match = VERDICT.match(line) if index == 0 else None
            if match and (allowed_logins is None or login in allowed_logins):
                yield match.groups()
            elif LOOKS_LIKE_VERDICT.search(line):
                print(f"skipped verdict by {login}: {line[:60]}", file=sys.stderr)


def main():
    configured = os.environ.get("PREMERGE_REVIEWER_LOGINS")
    allowed = set(configured.split()) if configured is not None else None
    for marker in verdicts(sys.stdin, allowed):
        print("\t".join(marker))
    return 0


if __name__ == "__main__":
    sys.exit(main())
