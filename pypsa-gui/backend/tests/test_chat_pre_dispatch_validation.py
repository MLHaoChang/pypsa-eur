"""
Improvement #19 — don't ask a user to approve a call that cannot work.

`_dispatch_real_tool_call` issues the confirmation token and emits
`tool_pending_confirmation` BEFORE the dispatcher ever runs. So a destructive
call with bad arguments — delete a generator that isn't there — showed the
user a card saying "permanently delete Solar_typo?", waited for them to
decide, and only then answered 404.

That is worse than a wasted round-trip. `cascade_delete_bus` and
`delete_project` carry a TYPED confirmation: the user has to retype the
target name before Approve unlocks. Making someone type a name to authorise
an operation that was never going to happen teaches them that confirming is
harmless, which is the one habit a destructive prompt must not build.

The fix is a per-tool validator consulted before the gate. Scope is
deliberately network-local (see PRE_DISPATCH_VALIDATORS): a project- or
snapshot-level existence check would have to re-implement the tenancy
resolution those routes do, and CLAUDE.md's 403→404 rule makes a
second, sloppier existence oracle exactly the wrong thing to add.
"""
from __future__ import annotations

import pypsa
import pytest

from services import chat_service, chat_tools


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


@pytest.fixture
def net(install_network):
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Generator", "Solar", bus="B1")
    install_network(n, name="PreValidate")
    return n


def _dispatch(tool_name: str, args: dict):
    """Drive one tool_use and return (frames, tool_results)."""
    session = chat_service.ChatSession()
    collected: list[dict] = []
    frames = list(chat_service._dispatch_real_tool_call(
        session,
        {"id": "tu-1", "name": tool_name, "input": args},
        collected,
    ))
    return frames, collected


def test_deleting_a_component_that_is_not_there_never_reaches_the_user(net):
    """
    The defect, stated as the user sees it: no card, no typed confirmation,
    no approval — just an error the agent can act on.
    """
    frames, results = _dispatch(
        "delete_component", {"component_class": "Generator", "name": "Solar_typo"},
    )

    events = [e for e, _ in frames]
    assert "tool_pending_confirmation" not in events, (
        "the user was asked to approve a delete that could not succeed"
    )
    assert "tool_running" not in events
    kinds = [p.get("error_kind") for e, p in frames if e == "tool_error"]
    assert kinds == ["invalid_tool_args"]


def test_the_rejection_is_reported_back_to_the_model(net):
    """
    Anthropic's API requires a tool_result for every tool_use. A validator
    that returned early without appending one would break the NEXT request in
    the turn, not this one — a failure that surfaces far from its cause.
    """
    _frames, results = _dispatch(
        "delete_component", {"component_class": "Generator", "name": "Solar_typo"},
    )

    assert len(results) == 1
    assert results[0]["tool_use_id"] == "tu-1"
    assert results[0]["is_error"] is True
    # The message has to name the problem — the agent's only route to a
    # correct retry is what this string says.
    assert "Solar_typo" in str(results[0]["content"])


def test_a_real_component_still_gets_its_confirmation_card(net):
    """
    The guard must not swallow legitimate destructive calls. This is the
    test that fails if a validator is too strict.
    """
    session = chat_service.ChatSession()
    collected: list[dict] = []
    gen = chat_service._dispatch_real_tool_call(
        session,
        {"id": "tu-2", "name": "delete_component",
         "input": {"component_class": "Generator", "name": "Solar"}},
        collected,
    )
    events = []
    for event, _payload in gen:
        events.append(event)
        if event == "tool_pending_confirmation":
            gen.close()  # don't block on a decision nobody will make
            break

    assert "tool_pending_confirmation" in events
    assert "tool_error" not in events


def test_an_unknown_component_class_is_rejected_before_the_prompt(net):
    """
    `delete_component` raises HTTPException(400) on an unknown class from
    inside the dispatcher — after the card. Same class of waste.
    """
    frames, _results = _dispatch(
        "delete_component", {"component_class": "Reactor", "name": "R1"},
    )

    events = [e for e, _ in frames]
    assert "tool_pending_confirmation" not in events
    assert [p.get("error_kind") for e, p in frames if e == "tool_error"] == [
        "invalid_tool_args"
    ]


def test_cascade_delete_bus_checks_the_bus_before_asking_for_typed_consent(net):
    """
    The most expensive prompt in the product to waste: the user must type the
    bus name verbatim before Approve unlocks.
    """
    frames, _results = _dispatch("cascade_delete_bus", {"name": "B_nope"})

    events = [e for e, _ in frames]
    assert "tool_pending_confirmation" not in events
    assert [p.get("error_kind") for e, p in frames if e == "tool_error"] == [
        "invalid_tool_args"
    ]


def test_a_destructive_tool_without_a_validator_is_untouched(net):
    """
    The hook is opt-in per tool. A tool with no entry must behave exactly as
    it did — reaching the confirmation gate unchanged.
    """
    assert "save_project" not in chat_tools.PRE_DISPATCH_VALIDATORS

    session = chat_service.ChatSession()
    collected: list[dict] = []
    gen = chat_service._dispatch_real_tool_call(
        session,
        {"id": "tu-3", "name": "save_project", "input": {"name": "Whatever"}},
        collected,
    )
    events = []
    for event, _payload in gen:
        events.append(event)
        if event == "tool_pending_confirmation":
            gen.close()
            break

    assert "tool_pending_confirmation" in events


def test_the_validators_class_table_matches_the_dispatchers():
    """
    Drift guard. `_COMPONENT_CLASS_TO_ATTR` decides what the validator will
    ACCEPT; `_delete_component_handlers()` decides what the tool can actually
    delete. A class added to the second and missed in the first becomes
    undeletable via chat — refused by the validator before the handler that
    supports it is ever reached, and refused with a message claiming the
    class does not exist.
    """
    assert set(chat_tools._COMPONENT_CLASS_TO_ATTR) == set(
        chat_tools._delete_component_handlers()
    )


def test_every_validated_class_names_a_real_pypsa_frame(net):
    """
    The attribute names are typed by hand. One typo and the validator reads
    None for that class and rejects every delete of it as non-existent.
    """
    from services.pypsa_service import PyPSAService
    n = PyPSAService.get_network()
    missing = [
        f"{cls} -> n.{attr}"
        for cls, attr in chat_tools._COMPONENT_CLASS_TO_ATTR.items()
        if getattr(n, attr, None) is None
    ]
    assert not missing, f"validator points at frames PyPSA does not have: {missing}"


def test_a_validator_that_raises_does_not_block_the_tool(net, monkeypatch):
    """
    A validator is a convenience, not a gate. If one breaks, the correct
    outcome is the old behaviour (prompt, dispatch, let the real handler
    answer) — never a destructive tool that has become uncallable.
    """
    def _boom(_args):
        raise RuntimeError("validator is broken")

    monkeypatch.setitem(
        chat_tools.PRE_DISPATCH_VALIDATORS, "cascade_delete_bus", _boom,
    )

    session = chat_service.ChatSession()
    collected: list[dict] = []
    gen = chat_service._dispatch_real_tool_call(
        session,
        {"id": "tu-4", "name": "cascade_delete_bus", "input": {"name": "B1"}},
        collected,
    )
    events = []
    for event, _payload in gen:
        events.append(event)
        if event == "tool_pending_confirmation":
            gen.close()
            break

    assert "tool_pending_confirmation" in events
