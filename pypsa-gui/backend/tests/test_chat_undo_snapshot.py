"""
CH-3 — a chat edit is undoable, and one undo reverts the assistant's turn.

Undo snapshots are pushed by `main.undo_snapshot_middleware`. A chat tool calls
its handler in-process and never passes through it, so a chat edit pushed
nothing: `undo_last` then refused, or reverted an OLDER canvas edit while
reporting `{"undone": true}`. Texts the model reads promised otherwise —
"Writes participate in the turn-level undo"; an image reconstruction "created
inside one undo snapshot so a misread can be reverted in one click".

The dispatcher now pushes ONE snapshot per turn, per project, before the
turn's first network-changing tool. These tests pin the unit (one step per
turn, not per call), the reset after an in-turn undo, the tools it applies to,
and that its constants agree with the middleware's.
"""
from __future__ import annotations

import pypsa
import pytest

import main
from services import chat_service, chat_tools, undo_service
# AUTO_APPROVE_TIERS lives in harness.confirm; patch it there, not via the
# chat_service alias (tests/test_harness_layout.py's tripwire).
from harness import confirm as harness_confirm
from services.pypsa_service import PyPSAService
from tests.test_chat_e2e import (
    FakeAnthropicClient, _FakeFinalMessage, _FakeUsage, _text_block,
    _text_event, _tool_use_block, _tool_use_event,
)


@pytest.fixture
def g1_at_100(tmp_projects_dir, install_network, monkeypatch):
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Generator", "G1", bus="B1", p_nom=100.0)
    install_network(n)
    undo_service.clear()
    chat_service._reset_sessions_for_tests()
    monkeypatch.setattr(harness_confirm, "AUTO_APPROVE_TIERS",
                        frozenset({"write", "destructive"}))


def _p_nom() -> float:
    return float(PyPSAService.get_network().generators.at["G1", "p_nom"])


def _set(p_nom: float) -> dict:
    return {"component_class": "Generator", "name": "G1", "attrs": {"p_nom": p_nom}}


def _dispatch(session, name: str, args: dict) -> list[str]:
    return [ev for ev, _p in chat_service._dispatch_real_tool_call(
        session, {"id": f"tu-{name}", "name": name, "input": args}, [])]


def _new_turn(session) -> None:
    """What `run_turn` does at a turn boundary."""
    session._undo_snapshotted = []


def test_the_undo_constants_match_the_middleware():
    assert tuple(chat_tools._UNDO_PREFIXES) == tuple(main._UNDO_PREFIXES)
    assert set(chat_tools._UNDO_EXCLUDE) == set(main._UNDO_EXCLUDE)


def test_a_turn_of_edits_is_one_undo_step_through_run_turn(g1_at_100):
    """End to end through `run_turn`: two edits in one turn, one undo step, and
    that step returns the network to where the turn began."""
    calls = [("tu-a", "update_component", _set(150.0)),
             ("tu-b", "update_component", _set(175.0))]
    client = FakeAnthropicClient([
        ([_tool_use_event(*c) for c in calls],
         _FakeFinalMessage(content=[_tool_use_block(*c) for c in calls],
                           usage=_FakeUsage())),
        ([_text_event("done.")],
         _FakeFinalMessage(content=[_text_block("done.")], usage=_FakeUsage())),
    ])
    events = list(chat_service.run_turn(
        chat_service.ChatSession(), "raise G1 twice", client=client))
    assert not [p for ev, p in events if ev in ("error", "tool_error")], events
    assert _p_nom() == 175.0

    assert undo_service.depth() == 1, (
        f"expected ONE undo step for the turn, found {undo_service.depth()}: "
        "zero means chat edits are still invisible to undo; two means the unit "
        "is the call, not the turn"
    )
    chat_tools.undo_last()
    assert _p_nom() == 100.0


def test_each_turn_gets_its_own_step(g1_at_100):
    session = chat_service.ChatSession()
    _dispatch(session, "update_component", _set(150.0))
    _new_turn(session)
    _dispatch(session, "update_component", _set(175.0))

    assert undo_service.depth() == 2
    chat_tools.undo_last()
    assert _p_nom() == 150.0


def test_an_undo_inside_the_turn_returns_to_its_start_and_rearms(g1_at_100):
    """`undo_last` called by the model in the same turn pops the turn's own
    snapshot. The edits it makes after that need a step of their own, or a
    later undo would skip straight past them to an older one."""
    session = chat_service.ChatSession()
    _dispatch(session, "update_component", _set(150.0))
    _dispatch(session, "undo_last", {})
    assert _p_nom() == 100.0
    assert undo_service.depth() == 0

    _dispatch(session, "update_component", _set(125.0))
    assert undo_service.depth() == 1, (
        "the edit after an in-turn undo pushed no snapshot, so it cannot be "
        "undone on its own"
    )
    chat_tools.undo_last()
    assert _p_nom() == 100.0


def test_a_read_tool_pushes_no_snapshot(g1_at_100):
    _dispatch(chat_service.ChatSession(), "undo_status", {})
    assert undo_service.depth() == 0


def test_an_unconfirmed_destructive_tool_pushes_no_snapshot(g1_at_100, monkeypatch):
    """Placement: after the confirmation gate, so a card nobody answers costs
    nothing — the same rule the dirty mark beside it follows."""
    monkeypatch.setattr(harness_confirm, "AUTO_APPROVE_TIERS", frozenset())
    gen = chat_service._dispatch_real_tool_call(
        chat_service.ChatSession(),
        {"id": "tu-del", "name": "delete_component",
         "input": {"component_class": "Generator", "name": "G1"}}, [])
    for event, _p in gen:
        if event == "tool_pending_confirmation":
            gen.close()
            break
    assert undo_service.depth() == 0


def test_the_captured_set_is_network_edits_only():
    """Spot checks on the derivation: the network writers are in, the undo
    endpoints and project-folder writes are out (a snapshot before them would
    be an undo step that changes nothing)."""
    captured = chat_tools.UNDO_CAPTURED_TOOLS
    for name in ("update_component", "create_component", "delete_component",
                 "bulk_update_components", "batch_create_components",
                 "import_network_nc", "reconstruct_network_from_image"):
        assert name in captured, name
    for name in ("undo_last", "undo_status", "delete_upload",
                 "clear_chat_history", "list_components"):
        assert name not in captured, name


def test_two_turns_on_one_session_are_two_steps(g1_at_100):
    """The per-turn record lives on the session, so `run_turn` must clear it at
    the turn boundary — or every turn after the first pushes nothing."""
    session = chat_service.ChatSession()
    for p_nom in (150.0, 175.0):
        call = ("tu-x", "update_component", _set(p_nom))
        client = FakeAnthropicClient([
            ([_tool_use_event(*call)],
             _FakeFinalMessage(content=[_tool_use_block(*call)], usage=_FakeUsage())),
            ([_text_event("ok.")],
             _FakeFinalMessage(content=[_text_block("ok.")], usage=_FakeUsage())),
        ])
        list(chat_service.run_turn(session, "edit", client=client))

    assert undo_service.depth() == 2
    chat_tools.undo_last()
    assert _p_nom() == 150.0
