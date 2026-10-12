"""
The rename route must decide "was this the active project?" BEFORE it renames.

`_rename_project_db` updates the in-memory `n.name` only when the project being
renamed is the active one. That test used to read the binding AFTER calling
`project_registry.rename_project` — and the QA-P2 fix made that call rebind
resident contexts (it must: otherwise every rename leaves the context on the
old name and the next save 409s). `ctx.loaded_project` was therefore already
the NEW name, `== old_name` was always False, and the update silently stopped
happening.

`n.name` is not cosmetic: `export_to_netcdf` writes it, so a stale one ships
inside the saved file and inside every bundle made from it.

WHERE THE COVERAGE LIVES. The end-to-end behaviour is asserted by
`tests/qa_rename_project.py` step [2] ("in-memory n.name updates when renaming
the active project"), which is a CI gate (`pixi run gui-qa-drivers`) and is what
caught this — 6093 unit tests passed with the regression in place. A behavioural
unit test is awkward here because the project binding lives in a request-scoped
context and `db_session` is a different database from the signed-in client's, so
this file pins the one thing the driver cannot: the ORDERING, which is what
broke and what a future edit could quietly undo again.
"""
from __future__ import annotations

import inspect

from routers.projects import _rename_project_db


def _source() -> str:
    return inspect.getsource(_rename_project_db)


def _code_only() -> str:
    """The function's source with comment lines dropped.

    The explanation of this very regression is written in a comment inside the
    function, so a naive substring search finds the broken expression in the
    prose describing it.
    """
    return "\n".join(
        line for line in _source().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_active_check_is_captured_before_the_rename():
    src = _source()
    assert "was_active" in src, (
        "_rename_project_db no longer captures the active-project decision; if "
        "it reads the binding after rename_project, the rebind has already "
        "moved it and the n.name update is dead code"
    )
    capture = src.index("was_active =")
    rename = src.index("project_registry.rename_project(")
    assert capture < rename, (
        "the active-project decision is made AFTER rename_project, which "
        "rebinds the resident context — so it can never be True"
    )


def test_the_network_name_update_is_gated_on_that_capture():
    src = _source()
    assert "if was_active:" in src, (
        "the n.name update is gated on something other than the pre-rename "
        "capture; re-reading the live binding here is the regression"
    )
    gate = src.index("if was_active:")
    assert "n.name = new_name" in src[gate:gate + 400], (
        "the n.name update no longer sits under the was_active gate"
    )


def test_the_stale_binding_comparison_is_gone():
    # The exact expression that broke. It must not come back.
    assert "get_loaded_project() == old_name" not in _code_only()
