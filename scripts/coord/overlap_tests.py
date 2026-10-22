# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Map overlapping source files to the tests that exercise them.

``premerge.sh`` calls this to decide whether a code overlap can be waived: the
overlap does not block when every overlapping file has tests and they pass on
the trial merge. Run it against the trial-merge worktree::

    python scripts/coord/overlap_tests.py <worktree> <file>...

One tab-separated line per file and test: ``pytest <file> <test>`` (test path
relative to ``pypsa-gui/backend``), ``vitest <file> <test>`` (relative to
``pypsa-gui/frontend``), or ``none <file>`` when no test matches.

- Backend ``pypsa-gui/backend/<path>/<mod>.py`` maps to
  ``pypsa-gui/backend/tests/test_<mod>*.py``.
- Frontend ``pypsa-gui/frontend/src/**/<Name>.tsx|ts`` maps to every
  ``<Name>*.test.*`` under ``pypsa-gui/frontend/src``.
"""

import sys
from pathlib import Path, PurePosixPath

BACKEND = PurePosixPath("pypsa-gui/backend")
FRONTEND = PurePosixPath("pypsa-gui/frontend")


def tests_for(root, path):
    """Return ``(runner, [test paths])`` for ``path``; the list is empty if none."""
    root = Path(root)
    p = PurePosixPath(path)
    if p.suffix == ".py" and p.is_relative_to(BACKEND):
        if p.is_relative_to(BACKEND / "tests") or p.stem.startswith("__"):
            return "pytest", []
        tests = sorted((root / BACKEND / "tests").glob(f"test_{p.stem}*.py"))
        return "pytest", [str(PurePosixPath(t.relative_to(root / BACKEND))) for t in tests]
    if p.suffix in (".ts", ".tsx") and p.is_relative_to(FRONTEND / "src"):
        name = p.name.split(".")[0]
        if p.name.endswith((".test.ts", ".test.tsx")):
            return "vitest", []
        tests = sorted((root / FRONTEND / "src").rglob(f"{name}*.test.*"))
        return "vitest", [str(PurePosixPath(t.relative_to(root / FRONTEND))) for t in tests]
    return "none", []


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    root, files = argv[0], argv[1:]
    for f in files:
        runner, tests = tests_for(root, f)
        if not tests:
            print(f"none\t{f}")
        for t in tests:
            print(f"{runner}\t{f}\t{t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
