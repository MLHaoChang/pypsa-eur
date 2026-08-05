"""
A multiprocessing helper must not become a second copy of the application.

A frozen app is its own `sys.executable`, so `multiprocessing` starts its
resource tracker by re-executing the bundle with a `-c` command. OBSERVED on
2026-08-03: that child ran the entry point instead, opened a window, started
uvicorn and took the single-instance lock — and while the real parent was
still alive it produced the "PyPSA GUI is already running" dialog on every
solve. See `desktop/gui.py::multiprocessing_helper_command`.
"""
from __future__ import annotations

import pytest

webview = pytest.importorskip(
    "webview",
    reason=(
        "pywebview is missing from this environment, so desktop/gui.py cannot "
        "be imported — run `pixi run gui-tests`, which resolves the `test` env"
    ),
)

from desktop import gui  # noqa: E402


# The exact argv observed on the orphaned process, in order.
REAL_TRACKER_ARGV = [
    "/Applications/PyPSA Studio.app/Contents/MacOS/PyPSA Studio",
    "-B", "-S", "-I", "-c",
    "from multiprocessing.resource_tracker import main;main(4)",
]


def test_the_real_resource_tracker_argv_is_recognised():
    assert gui.multiprocessing_helper_command(REAL_TRACKER_ARGV) == \
        "from multiprocessing.resource_tracker import main;main(4)"


@pytest.mark.parametrize("command", [
    "from multiprocessing.resource_tracker import main;main(7)",
    "from multiprocessing.semaphore_tracker import main;main(7)",
    "from multiprocessing.forkserver import main(); main(3, 4, None)",
    "import sys; from multiprocessing.forkserver import main; main(3, 4)",
])
def test_every_known_helper_bootstrap_is_recognised(command):
    assert gui.multiprocessing_helper_command(["app", "-c", command]) == command


def test_a_normal_launch_is_not_treated_as_a_helper():
    assert gui.multiprocessing_helper_command(["/path/to/PyPSA Studio"]) is None


def test_an_unrelated_dash_c_command_is_refused():
    """
    The allowlist is the point: this must never become a way to run arbitrary
    code by passing `-c` to a shipped binary.
    """
    assert gui.multiprocessing_helper_command(
        ["app", "-c", "import shutil; shutil.rmtree('/')"]) is None


def test_a_command_merely_mentioning_multiprocessing_is_refused():
    """Matches on the bootstrap PREFIX, not on the substring appearing anywhere."""
    assert gui.multiprocessing_helper_command(
        ["app", "-c", "print('from multiprocessing.resource_tracker import main')"]
    ) is None


def test_a_trailing_dash_c_with_no_command_does_not_crash():
    assert gui.multiprocessing_helper_command(["app", "-c"]) is None


def test_the_flags_between_argv0_and_dash_c_are_not_required_to_match():
    """
    The regression that made PyInstaller's own hook useless here. Its
    `pyi_rth_multiprocessing` diverts only when the flags before `-c` equal
    `_args_from_interpreter_flags()` recomputed in the CHILD — and a frozen
    bootloader configures its own interpreter, so that equality fails and the
    hook silently returns. This helper must not depend on those flags at all.
    """
    for flags in ([], ["-B"], ["-B", "-S", "-I"], ["-E", "-s", "-S", "-I", "-O"]):
        argv = ["app", *flags, "-c", "from multiprocessing.resource_tracker import main;main(4)"]
        assert gui.multiprocessing_helper_command(argv) is not None, flags
