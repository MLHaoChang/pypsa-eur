# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Print the current coordinator and reviewer session ids.

Reads ``docs/agents/coordinator.md`` and ``docs/agents/roles.md`` as they are
on ``origin/{{BASE_BRANCH}}`` (after a best-effort ``git fetch``), so a session never addresses an id that a rotation
has replaced::

    python3 scripts/coord/whois.py        # one "role<TAB>session_id" line per role
    python3 scripts/coord/whois.py gk     # just that id: coordinator | gk | ux

Exits 1 with a message when the file cannot be read or a role is not in it.
"""

import re
import subprocess
import sys

BRANCH = "{{BASE_BRANCH}}"
# role -> (file on origin/BRANCH, start of the line that carries its id)
ROLES = {
    "coordinator": ("docs/agents/coordinator.md", "Current coordinator session"),
    "gk": ("docs/agents/roles.md", "Gatekeeper"),
    "ux": ("docs/agents/roles.md", "UX reviewer"),
}
SESSION = re.compile(r"`(session_[A-Za-z0-9]+)`")


def show(path):
    ref = f"origin/{BRANCH}:{path}"
    result = subprocess.run(["git", "show", ref], capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"whois: cannot read {ref}: {result.stderr.strip()}")
    return result.stdout


def session_id(role, texts):
    path, start = ROLES[role]
    if path not in texts:
        texts[path] = show(path)
    for line in texts[path].splitlines():
        if line.lstrip("| ").startswith(start) and (found := SESSION.search(line)):
            return found.group(1)
    sys.exit(f"whois: no session id for {role} ('{start}') in origin/{BRANCH}:{path}")


def main(argv):
    if len(argv) > 1 or (argv and argv[0] not in ROLES):
        sys.exit(f"usage: whois.py [{'|'.join(ROLES)}]")
    try:  # best effort: offline, the last fetched copy still answers
        subprocess.run(
            ["git", "fetch", "-q", "origin", BRANCH], capture_output=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    texts = {}
    if argv:
        print(session_id(argv[0], texts))
    else:
        for role in ROLES:
            print(f"{role}\t{session_id(role, texts)}")


if __name__ == "__main__":
    main(sys.argv[1:])
