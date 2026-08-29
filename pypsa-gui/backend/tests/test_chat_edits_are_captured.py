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


def _dispatch(tool_name: str, args: dict):
    """Drive one tool_use through the REAL dispatcher, as a chat turn does."""
    from services import chat_service
    session = chat_service.ChatSession()
    collected: list[dict] = []
    frames = list(chat_service._dispatch_real_tool_call(
        session, {"id": "tu-1", "name": tool_name, "input": args}, collected,
    ))
    return frames, collected


def test_chat_edit_is_visible_as_unsaved_work(client, install_network):
    """
    A chat edit must leave a signal that unsaved work exists, or every
    destructive guard treats the project as clean and can discard it.

    Driven through `_dispatch_real_tool_call`, NOT by calling `chat_tools`
    directly. That distinction is the whole point: the fix lives at the
    dispatcher, because the tempting fix — marking in
    `routers.network._update_component` — misses Transformer, GlobalConstraint
    and Bus-rename, which dispatch to dedicated handlers. A test that called
    chat_tools directly would pass against that broken fix.
    """
    install_network(_net())
    from services import chat_service, dirty_state
    chat_service._reset_sessions_for_tests()
    dirty_state.clear()
    undo_service.clear()

    _dispatch("update_component", {
        "component_class": "Generator", "name": "G1", "attrs": {"p_nom": 150.0},
    })

    assert dirty_state.is_dirty() is True, (
        "a chat-driven edit left no trace of unsaved work; every destructive "
        "guard reads this, so the edit can be destroyed without a prompt"
    )


def test_a_read_tier_chat_tool_does_not_mark_the_project_dirty(client, install_network):
    """
    The sibling assertion. The fix must not mark on EVERY tool — a read tool
    that dirties the project would prompt the user about work that does not
    exist, and a guard that always fires is one people learn to click through.
    """
    install_network(_net())
    from services import chat_service, dirty_state
    chat_service._reset_sessions_for_tests()
    dirty_state.clear()

    _dispatch("undo_status", {})

    assert dirty_state.is_dirty() is False, "a read-tier tool must not mark dirty"


def test_an_unconfirmed_destructive_tool_does_not_mark_the_project_dirty(client, install_network):
    """
    This is the assertion that pins the mark's PLACEMENT, and without it the
    mark could drift above the confirmation gate and nothing would fail.

    A destructive tool stops at `tool_pending_confirmation` and executes
    nothing until a human decides. Marking dirty before that point would claim
    the project holds unsaved work the user has not agreed to create — and on a
    denial, that claim would outlive the refusal and prompt them about it later.
    """
    install_network(_net())
    from services import chat_service, dirty_state
    chat_service._reset_sessions_for_tests()
    dirty_state.clear()

    session = chat_service.ChatSession()
    collected: list[dict] = []
    gen = chat_service._dispatch_real_tool_call(
        session,
        {"id": "tu-deny", "name": "delete_component",
         "input": {"component_class": "Generator", "name": "G1"}},
        collected,
    )
    events = []
    for event, _payload in gen:
        events.append(event)
        if event == "tool_pending_confirmation":
            gen.close()  # nobody will decide; the tool never runs
            break

    assert "tool_pending_confirmation" in events, events
    assert dirty_state.is_dirty() is False, (
        "the project was marked dirty by a destructive tool that was never "
        "confirmed and never ran — the mark is above the confirmation gate"
    )
