"""
A chat-driven edit is unsaved work, and it must be visible as such.

`services/chat_tools.py`'s module docstring claims tools "inherit the existing
lock policy, audit log, undo registration ... because they go through the same
_create/_update/_delete_component generic helpers". The undo half of that claim
is what these tests check, because the only `undo_service.push` statement in the
codebase is reached from `undo_snapshot_middleware`, and chat tools call handlers
DIRECTLY rather than over HTTP.

If the claim is stale, a chat edit is neither undoable nor counted by `depth`,
and — since `unsaved` marks only the solve sink (d78d9e29) — it is invisible to
every destructive-action guard, exactly as solver results were.

The HTTP test beside it is the control. Without it, a failure here could equally
mean the fixture never captures undo at all, which would make the chat assertion
prove nothing.
"""
from __future__ import annotations

import pypsa
import pytest

from services import chat_tools, undo_service


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Generator", "G1", bus="B1", p_nom=100.0)
    return n


def test_http_edit_is_captured_control(client, install_network):
    """Control: the middleware path DOES capture. If this fails, the fixture is wrong."""
    install_network(_net())
    # Read depth through the CLIENT, not `undo_service` from the test thread:
    # undo state is per-context and the client's session context is not the
    # test thread's foreground one (the trap documented in
    # tests/test_solver_run_api.py). Measuring across contexts reports 0 for
    # reasons that have nothing to do with the behaviour under test.
    before = client.get("/api/network/undo/info").json()["depth"]

    r = client.put("/api/network/generators/G1", json={"name": "G1", "bus": "B1", "p_nom": 150.0})
    assert r.status_code == 200, r.text

    after = client.get("/api/network/undo/info").json()["depth"]
    assert after == before + 1, (
        "the HTTP path did not capture an undo entry — this control must pass "
        "for the chat assertion below to mean anything"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "REPRODUCED 2026-08-27, not yet fixed. A chat-driven edit leaves no "
        "dirty signal: depth=0 and unsaved=False, while the control above "
        "proves the HTTP path captures normally. Every destructive guard reads "
        "`unsaved`, so a chat edit can be destroyed with no prompt.\n\n"
        "FIX AT THE DISPATCHER, NOT THE CRUD HELPER. The tempting fix is "
        "routers.network._update_component, which this test's Generator edit "
        "goes through — but Transformer, GlobalConstraint and Bus-rename "
        "dispatch to DEDICATED handlers (chat_tools.py:730-740) and would stay "
        "invisible. That fix flips this test GREEN while leaving those paths "
        "open, which is worse than the present bug because it reads as closed. "
        "The real chokepoint is chat_service.py:2948 where every tool is "
        "dispatched and `tier` is in scope; the mark must sit AFTER the "
        "confirmation gate, since marking before it would mark even when the "
        "user DECLINES.\n\n"
        "NOTE FOR WHOEVER TAKES IT: this test calls chat_tools directly and so "
        "does NOT exercise the dispatcher. It must be re-pointed through "
        "chat_service dispatch as part of the fix, or it will keep xfailing "
        "against a correct fix. Kept as-is deliberately: it documents the "
        "defect, and moving it is the fixer's first step rather than a change "
        "made blind now.\n\n"
        "strict=True so this fails loudly the moment it is fixed, rather than "
        "lingering as a stale exemption."
    ),
)
def test_chat_edit_is_visible_as_unsaved_work(client, install_network):
    """
    The claim under test. A chat edit must leave SOME signal that unsaved work
    exists — otherwise every destructive guard treats the project as clean.

    Asserted against the field the guards actually read (`unsaved`), not against
    `depth`, deliberately: whether a chat edit is UNDOABLE is a separate product
    question from whether it is DIRTY, and only the second one is a data-loss
    bug. A fix that makes chat edits dirty without making them undoable still
    closes the hole this test guards.
    """
    install_network(_net())
    undo_service.clear()
    from services import dirty_state
    dirty_state.clear()

    chat_tools.update_component(
        component_class="Generator", name="G1", attrs={"p_nom": 150.0},
    )

    info = client.get("/api/network/undo/info").json()
    assert info["unsaved"] is True, (
        "a chat-driven edit left no trace of unsaved work: depth="
        f"{info['depth']}, unsaved={info['unsaved']}. Every destructive guard "
        "reads `unsaved`, so the edit can be destroyed without a prompt."
    )
