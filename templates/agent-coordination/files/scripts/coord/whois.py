# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Print the current coordinator and reviewer session ids.

Reads ``docs/agents/coordinator.md`` and ``docs/agents/roles.md`` as they are
on ``origin/{{BASE_BRANCH}}`` (after a best-effort ``git fetch``), so a session never addresses an id that a rotation
has replaced::

    python3 scripts/coord/whois.py        # one "role<TAB>session_id" line per role
    python3 scripts/coord/whois.py gk     # just that id: coordinator | gk | gk2 | ux ...

Roles come from the labelled lines of both files, so a new reviewer line needs no
code change. Exits 1 with a message when the file cannot be read or a role is not in it.
"""

import re
import subprocess
import sys

BRANCH = "{{BASE_BRANCH}}"
FILES = ("docs/agents/coordinator.md", "docs/agents/roles.md")
SESSION = re.compile(r"`(session_[A-Za-z0-9]+)`")


def role_of(label):
    """
    Role name from a line's label: ``Current coordinator session`` -> coordinator,
    ``Gatekeeper`` -> gk, ``Gatekeeper 2`` -> gk2, ``UX reviewer`` -> ux; any other
    label becomes its lowercase letters and digits.
    """
    if m := re.match(r"current (\w+)", label, re.I):
        return m.group(1).lower()
    if m := re.match(r"gatekeeper\s*([A-Za-z]?\d*)$", label, re.I):
        return f"gk{m.group(1).lower()}"
    if m := re.match(r"UX reviewer\s*(\d*)$", label, re.I):
        return f"ux{m.group(1)}"
    return re.sub(r"[^a-z0-9]", "", label.lower())


def roles_of(text):
    """
    {role: session id} from the labelled lines (``Label (note): `session_...```
    or a table row ``| Label | `session_...` |``).
    """
    roles = {}
    for line in text.splitlines():
        label = re.split(r"[|(:]", line.lstrip("| ").strip(), maxsplit=1)[0].strip()
        if label and (found := SESSION.search(line)) and (role := role_of(label)):
            roles.setdefault(role, found.group(1))
    return roles


def show(path):
    ref = f"origin/{BRANCH}:{path}"
    result = subprocess.run(["git", "show", ref], capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"whois: cannot read {ref}: {result.stderr.strip()}")
    return result.stdout


def main(argv):
    if len(argv) > 1:
        sys.exit("usage: whois.py [role]")
    try:  # best effort: offline, the last fetched copy still answers
        subprocess.run(
            ["git", "fetch", "-q", "origin", BRANCH], capture_output=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    roles = {}
    for path in FILES:
        for role, sid in roles_of(show(path)).items():
            roles.setdefault(role, sid)
    if argv:
        if argv[0] not in roles:
            sys.exit(
                f"whois: no session id for {argv[0]} in origin/{BRANCH}; roles: {', '.join(roles)}"
            )
        print(roles[argv[0]])
    else:
        for role, sid in roles.items():
            print(f"{role}\t{sid}")


if __name__ == "__main__":
    main(sys.argv[1:])
