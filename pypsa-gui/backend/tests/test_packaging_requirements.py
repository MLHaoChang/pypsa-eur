"""
`gui-requirements.txt` must pin every third-party module the shipped backend
imports WITHOUT a guard.

**The bug this exists to prevent, measured in a shipped build.** A user clicked
"download template" in the packaged macOS app and got a 500. From
`~/Library/Application Support/PyPSA GUI/pypsa-gui.log`:

    File "routers/network.py", line 3568, in download_load_profile_template
    File "routers/network.py", line 441, in _xlsx_response
    File "pandas/io/excel/_openpyxl.py", line 57, in __init__
    ModuleNotFoundError: No module named 'openpyxl'

`openpyxl` is in `pixi.toml`, so every test and every dev run had it. The app
is built from a pip venv driven by `gui-requirements.txt` (D14), which did not
list it. Six modules were in that state; the build venv was the only place it
was observable and nothing was looking there.

**Why "unguarded" is the rule and not "imported".** A guarded import is a
deliberate optional — `time_aggregation_service.py` catches ImportError and
falls back to the full period, and pinning `tsam` would pull pyomo and
scikit-learn into the bundle to service a path that already works. An
UNGUARDED import is a promise that the module is there; when the promise is
broken the user gets a 500. Those are the ones this file makes non-negotiable.

**Why a static check rather than an endpoint test.** `test_desktop_downloads`
and friends run in the pixi environment, where all six modules are present. No
test executing against pixi can observe this defect — the difference lives in
the packaging manifest, so the check has to read the manifest.
"""
from __future__ import annotations

import ast
import importlib.metadata as md
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND.parent / "gui-requirements.txt"

# `tests/` and `smoke/` are development tooling. The frozen app's entry point
# is `desktop/gui.py` and neither is reachable from it, so `pytest` and
# `requests` are not shipped dependencies.
NOT_SHIPPED = {"tests", "smoke"}

# Third-party modules the backend imports INSIDE a try/except that catches the
# import failure. Each is allowed to be absent from the bundle, and the value
# records what the user actually gets when it is — which is the thing worth
# reviewing. A guarded import missing from this map fails the test: adding an
# optional dependency should be a decision someone wrote down, not a default.
OPTIONAL_AT_RUNTIME = {
    "tsam": (
        "time_aggregation_service catches ImportError and falls back to the "
        "full period. Correct result, slower solve — but the fallback is "
        "SILENT (log line only), so a user who asked for representative "
        "periods is never told they did not get them."
    ),
    "magic": (
        "uploads.py catches (ImportError, OSError) and trusts the client's "
        "DECLARED content-type. The wheel is ctypes bindings over a system "
        "libmagic this bundle does not ship, so pinning it alone would not "
        "change behaviour on a clean Mac."
    ),
    "anthropic": (
        "chat_service returns the typed error `sdk_not_installed` and the "
        "panel renders disabled. Pinned anyway — the panel is meant to work — "
        "but note the packaged app has no ANTHROPIC_API_KEY (check_bundle.py "
        "keeps .env out of the bundle), so the key gate fails first."
    ),
    "pypdf": (
        "upload_service returns (None, False) — page count unknown. A "
        ">100-page PDF then reaches Anthropic's cap as a 415 instead of a "
        "clean local truncation banner. Pinned to keep the banner working."
    ),
}


def _normalise(name: str) -> str:
    """PEP 503: `python-magic`, `Python_Magic` and `python.magic` are one
    distribution."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _pinned_distributions() -> set[str]:
    pinned: set[str] = set()
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[;]", line, maxsplit=1)[0]
        if name:
            pinned.add(_normalise(name))
    return pinned


def _first_party() -> set[str]:
    names = {p.name for p in BACKEND.iterdir() if p.is_dir()}
    names |= {p.stem for p in BACKEND.glob("*.py")}
    return names


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    """Does this `except` clause swallow a failed import?"""
    caught = handler.type
    if caught is None:  # bare `except:`
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    for node in names:
        if isinstance(node, ast.Name) and node.id in {
            "ImportError", "ModuleNotFoundError", "Exception", "BaseException",
        }:
            return True
    return False


def _collect(node: ast.AST, guarded: bool, out: dict[str, set[tuple[str, bool]]],
             where: str) -> None:
    """
    Walk `node`, recording every import as (file, guarded).

    Hand-rolled rather than `ast.walk` because guardedness depends on ancestry:
    only the `body` of a try whose handler catches ImportError is protected.
    An import in the HANDLER of that try is not — `io.py:95` imports openpyxl
    inside `except AttributeError`, which catches nothing about openpyxl.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Try):
            protected = guarded or any(
                _catches_import_error(h) for h in child.handlers
            )
            for stmt in child.body:
                _collect(stmt, protected, out, where)
            for section in (child.handlers, child.orelse, child.finalbody):
                for stmt in section:
                    _collect(stmt, guarded, out, where)
            continue

        if isinstance(child, ast.Import):
            modules = [alias.name for alias in child.names]
        elif isinstance(child, ast.ImportFrom):
            # `level > 0` is relative — first-party by definition.
            modules = [child.module] if child.level == 0 and child.module else []
        else:
            modules = []

        for module in modules:
            out.setdefault(module.split(".")[0], set()).add((where, guarded))

        _collect(child, guarded, out, where)


def _shipped_imports() -> dict[str, set[tuple[str, bool]]]:
    """Third-party top-level imports in shipped backend code, each tagged with
    the file that imports it and whether that import is guarded."""
    first_party = _first_party()
    raw: dict[str, set[tuple[str, bool]]] = {}

    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if NOT_SHIPPED & set(rel.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _collect(tree, False, raw, str(rel))

    return {
        module: sites
        for module, sites in raw.items()
        if module not in sys.stdlib_module_names
        and module not in first_party
        and not module.startswith("_")
    }


def _distribution_for(module: str) -> list[str] | None:
    return md.packages_distributions().get(module)


def test_every_unguarded_third_party_import_is_pinned_for_the_build():
    """
    An unguarded import is a promise the module is there. Break it and the
    user gets a 500 — which is exactly what shipped.
    """
    pinned = _pinned_distributions()
    problems: list[str] = []

    for module, sites in sorted(_shipped_imports().items()):
        unguarded = sorted(where for where, guarded in sites if not guarded)
        if not unguarded:
            continue
        providers = _distribution_for(module)
        if not providers:
            problems.append(
                f"{module!r} (imported unguarded by {', '.join(unguarded)}) is "
                "not installed here, so its distribution cannot be resolved"
            )
        elif not any(_normalise(p) in pinned for p in providers):
            problems.append(
                f"{module!r} (provided by {'/'.join(providers)}, imported "
                f"unguarded by {', '.join(unguarded)}) is missing from "
                "gui-requirements.txt"
            )

    assert not problems, (
        "These modules will NOT be in the frozen app, and nothing catches "
        "their absence — the feature 500s at runtime:\n  "
        + "\n  ".join(problems)
    )


def test_every_optional_dependency_has_a_recorded_consequence():
    """
    A guarded import may legitimately be left out of the bundle — but somebody
    has to have decided that, and written down what the user gets instead.
    A new guarded import fails here until that decision is recorded.
    """
    undocumented = sorted(
        module
        for module, sites in _shipped_imports().items()
        if all(guarded for _, guarded in sites)
        and module not in OPTIONAL_AT_RUNTIME
    )

    assert not undocumented, (
        "These imports are guarded, so the app survives without them — but "
        "the fallback behaviour is unrecorded. Add each to "
        "OPTIONAL_AT_RUNTIME describing what the user actually gets, then "
        "decide whether to pin it:\n  " + "\n  ".join(undocumented)
    )


def test_openpyxl_is_required_because_nothing_catches_its_absence():
    """
    The regression, named. `io.py:95` imports openpyxl inside an
    `except AttributeError` handler — which catches nothing about openpyxl —
    so the import is unguarded and the module must ship.

    This asserts the CLASSIFIER, not just the pin: a guard-detector that
    wrongly treated any try/except as protection would demote openpyxl to
    optional and silently retire the test above.
    """
    sites = _shipped_imports()["openpyxl"]

    assert any(not guarded for _, guarded in sites), (
        "openpyxl is now classified as guarded everywhere it is imported — if "
        "a real guard was added, confirm the fallback works before relaxing "
        "the pin, because 'download template' has no fallback"
    )
    assert "openpyxl" in _pinned_distributions()


def test_a_guard_that_does_not_catch_importerror_is_not_a_guard():
    """
    Mutation check on `_catches_import_error`. `except ValueError` must not
    count as protection, or every unguarded import inside an unrelated
    try/except would be waved through.
    """
    protects = ast.parse("try:\n import x\nexcept ImportError:\n pass").body[0]
    does_not = ast.parse("try:\n import x\nexcept ValueError:\n pass").body[0]
    bare = ast.parse("try:\n import x\nexcept:\n pass").body[0]
    tupled = ast.parse(
        "try:\n import x\nexcept (OSError, ImportError):\n pass"
    ).body[0]

    assert _catches_import_error(protects.handlers[0])
    assert not _catches_import_error(does_not.handlers[0])
    assert _catches_import_error(bare.handlers[0])
    assert _catches_import_error(tupled.handlers[0])


def test_requirements_parsing_ignores_comments_and_blank_lines():
    """
    `gui-requirements.txt` is mostly prose. A parser that treated comment lines
    as requirements would "pin" everything and this file would assert nothing.
    """
    pinned = _pinned_distributions()

    assert "pandas" in pinned
    assert "pypsa" in pinned
    assert not any(p.startswith("#") for p in pinned)
    assert "" not in pinned
    # The prose names modules it deliberately does NOT pin; they must not be
    # parsed as pins.
    assert "tsam" not in pinned
