"""Invariants of `build-macos.sh` that no other test can observe.

The script cannot be executed under pytest — one run provisions a venv, builds
the SPA, freezes a bundle and cuts a DMG, taking minutes and needing
`BUILD_PYTHON`. So these assert on its SOURCE. That is a weak form of test and
it is named as such here, but it is not a vacuous one: both invariants below
were violated by the shipped script, and both failures are silent at build time
and only surface as a wrong artifact or a wrong process much later.

Reading the script text is the only place either can be caught before a human
hits it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "build-macos.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SCRIPT.is_file(), f"build script missing at {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


def test_launch_instruction_uses_an_absolute_path_not_the_app_name(script_text: str) -> None:
    """`open -a 'PyPSA Studio'` starts the WRONG bundle after a build.

    `-a` asks LaunchServices to resolve the name, and building registers the
    copy in `dist-app/`. The most recently registered bundle tends to win, so
    the documented "install then run" sequence has been measured starting
    `.../pypsa-gui/dist-app/PyPSA Studio.app/Contents/MacOS/PyPSA Studio`
    immediately after a verified install into /Applications. Nothing looks
    wrong from the UI, so the tester believes they are exercising the installed
    build while running the build directory's copy.

    CLAUDE.md documents the rule; the script's own completion banner told the
    user to break it.
    """
    offenders = [
        line.strip()
        for line in script_text.splitlines()
        if "open -a" in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        "build-macos.sh tells the user to launch by name; launching by name can "
        f"start the dist-app copy instead of the installed one: {offenders}"
    )


def test_launch_instruction_names_the_installed_bundle(script_text: str) -> None:
    """The banner must point at /Applications, the bundle the install line wrote."""
    assert 'open "/Applications/PyPSA Studio.app"' in script_text, (
        "build-macos.sh should tell the user to launch the INSTALLED bundle by "
        "absolute path"
    )


def test_build_runs_the_test_gate(script_text: str) -> None:
    """A packaging script that gates nothing ships whatever it is pointed at.

    The secret scan is the only gate the script had, and it only inspects the
    bundle's contents. Nothing checked that the source it froze passes its own
    suite, so a red commit packages and installs exactly as quietly as a green
    one.
    """
    assert "gui-tests" in script_text, (
        "build-macos.sh runs no test gate — `pixi run gui-tests` is the "
        "project's canonical suite and belongs in the build"
    )


def test_gate_runs_after_the_spa_build_and_before_the_freeze(script_text: str) -> None:
    """Ordering is the whole point, and it is not arbitrary.

    `test_local_mode_e2e.py` covers the backend serving the built SPA — the
    exact seam a desktop build depends on. Those tests SKIP when
    `frontend/dist/` is absent, so a gate placed before the SPA build reports
    green while silently skipping the only tests that exercise packaging. That
    happened: four tests skipped in the gating run and passed only when re-run
    by hand after the build.

    Gating before the freeze then means a red suite costs seconds, not a full
    PyInstaller run.
    """
    spa = script_text.index("npm run build")
    gate = script_text.index("gui-tests")
    # Anchor on the step banner, not the pyinstaller invocation: the real line
    # reads `"$VENV/bin/pyinstaller" pypsa-gui.spec`, so the obvious substring
    # `pyinstaller pypsa-gui.spec` never matches and .index would raise here
    # rather than assert — a test that dies is not a test that passes.
    freeze = script_text.index('step "Freezing the app"')

    assert spa < gate, (
        "the gate runs before the SPA is built, so the local-mode e2e tests "
        "skip and the suite reads green without covering the packaged seam"
    )
    assert gate < freeze, "the gate runs after the freeze, wasting a build on a red suite"
