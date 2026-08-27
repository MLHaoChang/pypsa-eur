"""
Solver results are unsaved work, and the destructive-action guards must see them.

Three frontend guards (import replace, palette snapshot restore, Sidebar
destructive re-load) ask "is there unsaved work?" and all three answered it with
`undo_info()["depth"] > 0`. Undo depth is a PROXY: the stack is cleared on save,
so depth > 0 does imply unsaved edits — but the converse fails for any work that
never enters the stack, and solver results are exactly that. They are written
straight into the in-memory network and never pushed.

So solve → import silently destroyed the solve, with no prompt from any guard.

These tests assert the BEHAVIOUR (`unsaved` is true after a real solve) rather
than the mechanism, deliberately: the first design set the flag in the HTTP
middleware, which is wrong — the queue solves on a background thread with no
request at all, and even POST /run returns before the solve finishes. A test
written against the mechanism would have passed on that broken design.

See docs/superpowers/specs/2026-08-27-unsaved-results-visibility-design.md.
"""
from __future__ import annotations

from tests.conftest import build_network
from tests.test_solver_run_api import _run_and_join


def _undo_info(client) -> dict:
    r = client.get("/api/network/undo/info")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_completed_solve_marks_the_project_unsaved(client, install_network, session_state):
    """The defect: results exist, nothing is saved, and depth cannot see it."""
    install_network(build_network())

    before = _undo_info(client)
    assert before["unsaved"] is False, "precondition: freshly installed network is clean"

    s = _run_and_join(client, session_state)
    assert s["status"] == "completed", f"status={s['status']} condition={s['condition']}"

    after = _undo_info(client)
    assert after["unsaved"] is True, (
        "a completed solve left results in memory that are not on disk, and "
        "`unsaved` did not report it — every destructive guard reads this field"
    )


def test_undo_depth_still_cannot_see_the_solve(client, install_network, session_state):
    """
    The sibling assertion, and the reason `unsaved` had to be a NEW field rather
    than a redefinition of `depth`.

    `depth` answers "how many undoable edits since the last save" and StatusBar
    renders it as "N unsaved edits". A solve is not an undoable edit — pushing
    one would mean netcdf blobs against a 500 MB cap, evicting real history — so
    depth is RIGHT to stay 0 here. Two questions, two fields.
    """
    install_network(build_network())
    _run_and_join(client, session_state)

    info = _undo_info(client)
    assert info["depth"] == 0, "a solve must not push an undo entry"
    assert info["unsaved"] is True


def test_saving_clears_the_unsaved_flag(client, install_network, session_state, tmp_path):
    """Clear iff the operation leaves memory equal to disk. Save does."""
    install_network(build_network())
    _run_and_join(client, session_state)
    assert _undo_info(client)["unsaved"] is True

    r = client.post("/api/projects/solve-dirty-probe")
    assert r.status_code in (200, 201), r.text

    assert _undo_info(client)["unsaved"] is False, (
        "save wrote memory to disk, so nothing is unsaved any more"
    )
