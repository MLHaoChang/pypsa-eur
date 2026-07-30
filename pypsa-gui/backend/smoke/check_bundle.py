"""
Refuse to ship a bundle containing anything secret.

Run against a PyInstaller output tree BEFORE it is signed, notarised, zipped or
handed to anyone:

    python pypsa-gui/backend/smoke/check_bundle.py dist/PyPSA\\ GUI.app

Exit 0 = clean. Exit 1 = do not distribute this build.

**Why this is a hard gate and not a review checklist.** Every other packaging
mistake is a broken build you fix and rebuild. This one is a published API key:
`pypsa-gui/backend/.env` carries a real `ANTHROPIC_API_KEY` and the `SECRET_KEY`
that signs session cookies, and once a build is distributed there is no recall.
A `SECRET_KEY` identical across every install is the same class of problem.

The bait is that the obvious `.spec` line is a directory sweep —
`datas=[('pypsa-gui/backend', '.')]` — which picks all of this up in one go and
looks tidy in a diff. `.env`, `auth_dev.db` and `projects/` are all gitignored,
so none of them appears in a code review either.

Two of these were, until recently, invisible in another way: a bundled `.env`
also carries a cwd-relative `DATABASE_URL`, which used to CRASH the frozen app
at startup. That crash was, accidentally, the only thing standing between a
directory sweep and a shipped credential. `build_environment` now pins
`DATABASE_URL`, so a bundled `.env` produces an app that works perfectly and
leaks. Removing the crash is what makes this check load-bearing.

Checked by NAME anywhere in the tree, not by path, because the layout inside a
`.app` differs from a onedir build and both differ on Windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Exact basenames that must never appear.
FORBIDDEN_FILES = {
    ".env",              # ANTHROPIC_API_KEY, SECRET_KEY
    ".env.example",      # a template, but it teaches the layout and has a DATABASE_URL
    "auth_dev.db",       # a password hash plus absolute developer paths
    "auth_dev.db-wal",
    "auth_dev.db-shm",
    "pypsa-gui.db",      # a real user's database, if a build ran from app-data
}

# Directory names that must never appear. `projects` is 113 MB of real user
# work; the others are development state that would ship absolute local paths.
FORBIDDEN_DIRS = {
    "projects",
    "flat_projects",
    ".pixi",
    "node_modules",
    ".git",
}

# Suffixes that are secret-shaped wherever they turn up.
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keychain"}

# PUBLIC certificate material that legitimately ships. The suffix rule above is
# about PRIVATE keys; a CA bundle is the opposite — it is public by design and
# TLS breaks without it. Found by running this gate on the first real build,
# where `certifi/cacert.pem` was its only complaint: a false positive on the
# one file the app cannot work without.
#
# Kept as an exact-basename allowlist rather than a path or a directory rule,
# because the layout differs between a `.app`, a onedir build and Windows.
ALLOWED_DESPITE_SUFFIX = {
    "cacert.pem",       # certifi
}

# What SHOULD be there. Absence is not fatal here — a missing template breaks
# the app loudly on first use, which is a different (and recoverable) problem —
# but reporting it turns one manual check into zero.
EXPECTED = ("project_templates", "matpower.jinja2", "alembic", "spa.html")


def scan(root: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    seen: set[str] = set()

    for path in root.rglob("*"):
        name = path.name
        seen.add(name)
        try:
            rel = path.relative_to(root)
        except ValueError:  # pragma: no cover - rglob yields descendants
            rel = path

        if path.is_dir():
            if name in FORBIDDEN_DIRS:
                problems.append(f"FORBIDDEN DIRECTORY  {rel}")
            continue

        if name in FORBIDDEN_FILES:
            problems.append(f"FORBIDDEN FILE       {rel}")
        elif path.suffix.lower() in FORBIDDEN_SUFFIXES and name not in ALLOWED_DESPITE_SUFFIX:
            problems.append(f"SECRET-SHAPED FILE   {rel}")

    missing = [want for want in EXPECTED if want not in seen]
    return problems, missing


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print(f"usage: {Path(argv[0]).name} <bundle-directory>")
        return 2

    root = Path(argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    problems, missing = scan(root)

    if missing:
        print(f"WARNING: expected content not found in the bundle: {', '.join(missing)}")

    if problems:
        print(f"\nREFUSING THIS BUILD — {len(problems)} problem(s) in {root}:\n")
        for line in problems:
            print("   " + line)
        print(
            "\nFix the `datas` allowlist in the .spec. Do NOT delete the files "
            "from the bundle and ship it: the next build reproduces them."
        )
        return 1

    print(f"clean: no secrets or user data found in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
