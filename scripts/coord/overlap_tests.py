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
- A backend ``tests/test_*.py`` or frontend ``*.test.ts|tsx`` file maps to
  itself.
- Frontend ``pypsa-gui/frontend/src/**/<Name>.tsx|ts`` maps to every
  ``<Name>*.test.*`` under ``pypsa-gui/frontend/src``.
- The shared JSON files in ``SHARED_JSON`` (and their ``.d/`` fragments) map to
  the backend tests that read them.
- A chat tool fragment (``harness/catalogue_tools/<stem>.py``,
  ``services/chat_dispatchers/<stem>.py``) and ``harness/catalogue_base.py``
  map to ``TOOL_REGISTRY_TESTS`` plus any ``tests/test_<stem>*.py``.
"""

import sys
from pathlib import Path, PurePosixPath

BACKEND = PurePosixPath("pypsa-gui/backend")
FRONTEND = PurePosixPath("pypsa-gui/frontend")
# Hand-written shared JSON: base file stem -> the backend tests that read it.
SHARED_JSON = {
    "pypsa-gui/tool-error-kinds": ["tests/test_tool_error_kind_manifest.py", "tests/test_json_fragments.py"],
    "pypsa-gui/backend/smoke/assistant_route_parity_declarations": [
        "tests/test_assistant_route_parity.py", "tests/test_json_fragments.py"],
    "pypsa-gui/backend/tests/write_surface_lock_policy": [
        "tests/test_write_surface_lock_policy.py", "tests/test_registry_fragments.py"],
    "pypsa-gui/backend/tests/route_chat_disposition": [
        "tests/test_route_chat_disposition.py", "tests/test_registry_fragments.py"],
    "pypsa-gui/backend/services/not_via_chat": [
        "tests/test_not_via_chat.py", "tests/test_registry_fragments.py"],
    "pypsa-gui/backend/smoke/family_manifest": [
        "tests/test_family_manifest.py", "tests/test_registry_fragments.py"],
    "pypsa-gui/backend/tests/golden_route_surfaces": ["tests/test_golden_coverage.py"],
}
# The chat tool registry fragments (RETRO-FRAG-1): a fragment is held by the
# tests that read the merged TOOLS / TOOL_ROUTES / DISPATCHERS.
TOOL_FRAGMENT_DIRS = (BACKEND / "harness/catalogue_tools", BACKEND / "services/chat_dispatchers")
TOOL_REGISTRY_TESTS = [
    "tests/test_tool_fragments.py",
    "tests/test_chat_tools_endpoint_map.py",
    "tests/test_chat_tools_schema_match.py",
    "tests/test_chat_tools_lock_gate_parity.py",
    "tests/test_openai_tool_selection.py",
]


def tests_for(root, path):
    """Return ``(runner, [test paths])`` for ``path``; the list is empty if none."""
    root = Path(root)
    p = PurePosixPath(path)
    if p.suffix == ".json":
        stem = str(p.parent.with_suffix("") if p.parent.suffix == ".d" else p.with_suffix(""))
        if stem in SHARED_JSON:
            return "pytest", SHARED_JSON[stem]
    if p.suffix == ".py" and p.is_relative_to(BACKEND):
        if p.is_relative_to(BACKEND / "tests"):
            # A changed test module is its own test; conftest and helpers have none.
            if p.name.startswith("test_") and (root / p).is_file():
                return "pytest", [str(p.relative_to(BACKEND))]
            return "pytest", []
        registry = (p.parent in TOOL_FRAGMENT_DIRS
                    or p == BACKEND / "harness/catalogue_base.py")
        if p.stem.startswith("__") and not registry:
            return "pytest", []
        tests = sorted((root / BACKEND / "tests").glob(f"test_{p.stem}*.py"))
        own = [str(PurePosixPath(t.relative_to(root / BACKEND))) for t in tests]
        if registry:
            return "pytest", TOOL_REGISTRY_TESTS + [t for t in own if t not in TOOL_REGISTRY_TESTS]
        return "pytest", own
    if p.suffix in (".ts", ".tsx") and p.is_relative_to(FRONTEND / "src"):
        name = p.name.split(".")[0]
        if p.name.endswith((".test.ts", ".test.tsx")):
            # A changed test file is its own test.
            return "vitest", [str(p.relative_to(FRONTEND))] if (root / p).is_file() else []
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
