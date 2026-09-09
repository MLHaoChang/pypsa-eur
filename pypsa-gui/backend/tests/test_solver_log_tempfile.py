"""
The solver's live log file must not be created by guessing a free name.

`run_simulation` streams HiGHS/pypsa output to the UI by handing the solver a
PATH to write and tailing that file from a thread. The path used to come from
`tempfile.mktemp`, which is deprecated precisely because of what it does: it
returns a name that is unused *at that moment* and leaves the caller to create
the file later. Anything else that can write to the shared temp directory owns
that gap (CWE-377) — it can pre-create the path and read a tenant's solve log,
or point it at a file it wants the solver to overwrite.

On a single-user desktop install that is close to theoretical. On the shared
web deployment the tenancy migration was built for, `/tmp` is shared by every
tenant's process, which is where it stops being theoretical.

The fix cannot be `mkstemp`: pypsa passes `log_fn` (a NAME) down to the solver,
which opens it itself, so there is no file descriptor to hand anyone. What
closes the race instead is a private DIRECTORY — `mkdtemp` creates it 0700
before anything can be in it, so no other user can pre-create, symlink or read
the log inside it, whatever the log file's own mode is.

These tests pin the property, not the call: what matters is that the directory
holding the log is private and that it is cleaned up.
"""
from __future__ import annotations

import ast
import pathlib
import stat

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SOLVER = _BACKEND / "services" / "solver_service.py"


def test_the_solver_service_does_not_call_mktemp():
    """
    Asserted on the AST rather than on the text, so a `mktemp` inside a comment
    or a docstring explaining why it is not used does not fail the test — and a
    real call cannot hide behind one.
    """
    tree = ast.parse(_SOLVER.read_text())
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mktemp"
    ]
    assert not offenders, (
        f"services/solver_service.py calls tempfile.mktemp at line(s) {offenders}. "
        f"It hands the solver a path it creates later, which is the CWE-377 race; "
        f"use a private mkdtemp directory instead."
    )


def test_the_solve_log_lives_in_a_private_directory():
    """
    The behavioural half. Runs the module's own temp-log factory and checks the
    DIRECTORY it hands back, because that is what the protection rests on: a
    0700 directory means the mode of the log file inside it does not matter.
    """
    from services.solver_service import _make_solve_log_path

    path, cleanup = _make_solve_log_path()
    try:
        assert not path.exists(), (
            "the factory pre-created the log file; the solver opens it by name "
            "and `tmp_log.touch()` creates it, so handing back an existing file "
            "hides whether the directory is the thing protecting it"
        )
        mode = stat.S_IMODE(path.parent.stat().st_mode)
        assert mode == 0o700, (
            f"the solve log's directory is {oct(mode)}, not 0o700 — another user "
            f"on the host can reach into it"
        )
        path.touch()
        assert path.is_file()
    finally:
        cleanup()
    assert not path.parent.exists(), (
        "cleanup left the temp directory behind; one directory per solve would "
        "accumulate for the life of the process"
    )


def test_the_factory_hands_out_a_fresh_directory_each_time():
    """
    Two concurrent solves must not share a log. `run_simulation` is called per
    solve and the tail thread reads to EOF, so a shared path would interleave
    two solvers' output into both UIs.
    """
    from services.solver_service import _make_solve_log_path

    first, first_cleanup = _make_solve_log_path()
    second, second_cleanup = _make_solve_log_path()
    try:
        assert first != second
        assert first.parent != second.parent
    finally:
        first_cleanup()
        second_cleanup()
