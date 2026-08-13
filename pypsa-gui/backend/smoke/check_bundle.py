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

import plistlib
import sys
from pathlib import Path

# Exact basenames that must never appear.
FORBIDDEN_FILES = {
    "auth_dev.db",       # a password hash plus absolute developer paths
    "auth_dev.db-wal",
    "auth_dev.db-shm",
    "pypsa-gui.db",      # a real user's database, if a build ran from app-data
    # Written by `local_settings.py` into app-data and holds a live Anthropic
    # key. On the forbidden list for the same reason `.env` is: a build that
    # ever ran from an app-data directory would otherwise bundle it.
    "local-settings.json",
    # `local_settings.write_api_key`'s own atomic-write temp file
    # (`path.with_name(path.name + ".tmp")`, `local_settings.py`). It is
    # written with the SAME live key before `os.replace` renames it onto
    # `local-settings.json` — so a crash (or a build run) between the
    # `os.open` and the `os.replace` leaves this file behind holding the
    # same secret. Listed separately, as its own exact basename, rather than
    # relying on a `.tmp` suffix rule: the rest of this module matches by
    # exact basename on purpose, and a generic `.tmp` rule would be a much
    # broader (and untested) change for one file.
    "local-settings.json.tmp",
}

# Anything starting `.env`, not an enumeration of the two that exist today.
# The rest of this file matches by NAME ANYWHERE IN THE TREE precisely so it
# survives layout changes — but the secret list itself was a list of current
# filenames, which is the same brittleness one level down. `.env.local` and
# `.env.production` are gitignored by convention, so they are exactly the ones
# a reviewer would never see in a diff.
FORBIDDEN_PREFIXES = (".env",)

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
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keychain", ".db"}

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
    # PROJ's coordinate-reference database, 9 MB, shipped by pyproj and
    # REQUIRED. Measured the hard way: when a running app lost access to this
    # file, saving a project failed with
    #   CRSError: Invalid projection: EPSG:4326 (... SQLite ... disk I/O error)
    # so a `.db` rule without this entry would fail every build, and "nothing
    # legitimate ships a .db" is not true of this bundle.
    "proj.db",
}

# What SHOULD be there. Absence is not fatal here — a missing template breaks
# the app loudly on first use, which is a different (and recoverable) problem —
# but reporting it turns one manual check into zero.
EXPECTED = ("project_templates", "matpower.jinja2", "alembic", "spa.html")

# Info.plist usage-description keys macOS TCC requires before the app may
# touch speech recognition / the microphone — see `pypsa-gui.spec`'s
# `info_plist` block for why each one is there. Missing either is not a
# broken build, it is a build that LAUNCHES FINE and is hard-killed by the OS
# (TCC namespace, not a Python exception, nothing in pypsa-gui.log) the
# instant the chat panel's mic button is used. A future spec edit that drops
# one of these — e.g. someone "tidying" the info_plist dict — must fail this
# gate rather than ship an app that dies on first mic use.
REQUIRED_PLIST_KEYS = (
    "NSSpeechRecognitionUsageDescription",
    "NSMicrophoneUsageDescription",
)


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

        if name in FORBIDDEN_FILES or name.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"FORBIDDEN FILE       {rel}")
        elif path.suffix.lower() in FORBIDDEN_SUFFIXES and name not in ALLOWED_DESPITE_SUFFIX:
            problems.append(f"SECRET-SHAPED FILE   {rel}")

    missing = [want for want in EXPECTED if want not in seen]
    return problems, missing


def check_info_plist(root: Path) -> list[str]:
    """Assert the TCC usage-description keys are present and non-empty.

    Checked against the BUILT `Contents/Info.plist`, not the `.spec` source —
    a key can be in the spec dict and still be dropped by PyInstaller, so
    only the artifact is proof.
    """
    plist_path = root / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return [f"MISSING Info.plist        {plist_path}"]

    with plist_path.open("rb") as fh:
        try:
            data = plistlib.load(fh)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the gate
            return [f"UNREADABLE Info.plist      {plist_path}: {exc}"]

    problems: list[str] = []
    for key in REQUIRED_PLIST_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(
                f"MISSING USAGE DESCRIPTION  {key} not present/non-empty in "
                f"Contents/Info.plist — the app will be killed by TCC the "
                f"instant it is exercised"
            )
    return problems


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
    problems += check_info_plist(root)

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
