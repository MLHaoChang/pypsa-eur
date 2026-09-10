"""
Phase C tripwire — `_dispatch_tool_uses` lifted out of `_run_turn_body`.

This is the first cut with real loop state, and the state is what makes it
worth guarding. The extracted loop reads and writes four things the turn loop
needs afterwards:

  * `tool_call_count` — accumulates across EVERY assistant step of the turn, so
    the cap is per-TURN. Reset it per step and a long agent loop dispatches
    unboundedly while the cap still appears to be enforced.
  * `turn_project_holder[0]` — refreshed when the agent calls a legitimately
    rebinding tool, so the mid-turn-switch guard does not fire on the agent's
    own rebind. A list, deliberately: mutation is the mechanism.
  * `tool_results_for_next_turn` — one `tool_result` per `tool_use_id`, without
    exception. Anthropic requires the pairing; a missing one makes the resumed
    conversation invalid.
  * `switched_mid_turn` — read by the block after the loop.

So the seam is a generator (it yields frames) that RETURNS its state through
`yield from`, in a small dataclass rather than a tuple — a tuple invites a
later field addition to reorder silently at the call site.

What is deliberately NOT extracted: the parallel-destructive `offenders` block
just above. It ends in `continue`, i.e. it drives the OUTER `while` loop, and an
extracted generator cannot continue its caller's loop. Folding it in would need
a third outcome and blur what the seam means; it stays in `_run_turn_body`.
"""
from __future__ import annotations

import inspect

import pytest

from services import chat_service


def _seam():
    fn = getattr(chat_service, "_dispatch_tool_uses", None)
    assert fn is not None, "chat_service._dispatch_tool_uses does not exist yet"
    return fn


def _tu(tid, name="list_components", args=None):
    return {"id": tid, "name": name, "input": args or {"component_class": "Bus"}}


def _drive(session, tool_uses, *, tool_call_count=0, holder=None, results=None,
           turn_ctx=None, switched=lambda: False):
    """Run the seam to completion, returning (frames, outcome)."""
    holder = holder if holder is not None else ["P"]
    results = results if results is not None else []
    frames = []
    gen = _seam()(
        session, tool_uses,
        tool_call_count=tool_call_count,
        turn_ctx=turn_ctx,
        turn_project_holder=holder,
        project_switched=switched,
        tool_results_for_next_turn=results,
        char_budget={"used": 0},
    )
    try:
        while True:
            frames.append(next(gen))
    except StopIteration as stop:
        return frames, stop.value


def test_the_seam_is_a_generator_that_returns_its_state():
    assert inspect.isgeneratorfunction(_seam()), (
        "_dispatch_tool_uses must be a generator — it yields SSE frames, which "
        "the caller forwards with `yield from`"
    )


def test_the_outcome_is_a_named_structure_not_a_tuple(tmp_projects_dir, install_network):
    """
    Fields by name. A tuple would let a later addition reorder at the call site
    with nothing to notice it.
    """
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    _frames, outcome = _drive(chat_service.ChatSession(), [_tu("t1")])
    assert not isinstance(outcome, tuple)
    for field in ("tool_call_count", "stop_turn", "switched_mid_turn"):
        assert hasattr(outcome, field), f"outcome has no {field!r}"


def test_the_call_count_carries_in_and_out(tmp_projects_dir, install_network):
    """
    The cap is per TURN, so the count arrives from the previous assistant step
    and leaves incremented. Resetting it per step would silently unbound the cap.
    """
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    _frames, outcome = _drive(chat_service.ChatSession(), [_tu("t1"), _tu("t2")],
                              tool_call_count=5)
    assert outcome.tool_call_count == 7, (
        f"expected 5 + 2 dispatches, got {outcome.tool_call_count}"
    )


def test_the_cap_ends_the_turn_with_both_frames(monkeypatch, tmp_projects_dir,
                                                install_network):
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    monkeypatch.setattr(chat_service, "MAX_TOOL_CALLS_PER_TURN", 1)
    frames, outcome = _drive(chat_service.ChatSession(), [_tu("t1"), _tu("t2")])
    names = [n_ for n_, _ in frames]
    assert "tool_error" in names and names[-1] == "session_done"
    kinds = [p.get("error_kind") for n_, p in frames if n_ == "tool_error"]
    assert "tool_call_cap_exceeded" in kinds
    assert outcome.stop_turn is True, "the cap must end the whole turn"


def test_a_mid_turn_switch_synthesises_a_result_for_every_remaining_tool(
    tmp_projects_dir, install_network
):
    """
    Anthropic requires one `tool_result` per `tool_use_id`. On a switch the loop
    stops dispatching but must still pair off the current tool AND every tool
    after it, or the resumed conversation is invalid.
    """
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    results: list[dict] = []
    frames, outcome = _drive(
        chat_service.ChatSession(), [_tu("t1"), _tu("t2"), _tu("t3")],
        results=results, switched=lambda: True,
    )
    assert outcome.switched_mid_turn is True
    paired = {r["tool_use_id"] for r in results}
    assert paired == {"t1", "t2", "t3"}, (
        f"only {sorted(paired)} got a tool_result; every tool_use_id needs one"
    )
    assert all(r["is_error"] for r in results)
    assert [p["error_kind"] for n_, p in frames if n_ == "tool_error"] == \
        ["project_switched_mid_turn"] * 3


def test_a_rebinding_tool_refreshes_the_holder_and_announces_it(
    monkeypatch, tmp_projects_dir, install_network
):
    """
    The agent's own rebind is legitimate, so the guard's snapshot must follow it
    — and the frontend must be told, or its autosave keeps sending the old name
    and the identity guard 409s (incident 2026-06-08).
    """
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    rebinding = sorted(chat_service.PROJECT_REBINDING_TOOLS)[0]

    # The dispatch itself is irrelevant here; what matters is what the loop does
    # after it, so stub the dispatcher out and move the live binding instead.
    def _fake_dispatch(session, tu, collector, **kw):
        collector.append({"type": "tool_result", "tool_use_id": tu["id"],
                          "content": "ok"})
        return iter(())

    monkeypatch.setattr(chat_service, "_dispatch_real_tool_call", _fake_dispatch)

    from services.pypsa_service import PyPSAService

    class _Ctx:
        loaded_project = "the-new-one"

    monkeypatch.setattr(PyPSAService, "get_active_context",
                        staticmethod(lambda: _Ctx()))

    holder = ["the-old-one"]
    frames, _outcome = _drive(chat_service.ChatSession(),
                              [_tu("t1", name=rebinding)], holder=holder)
    assert holder[0] == "the-new-one", "the guard's snapshot did not follow the rebind"
    rebound = [p for n_, p in frames if n_ == "project_rebound"]
    assert rebound and rebound[0] == {
        "from": "the-old-one", "to": "the-new-one", "via_tool": rebinding,
    }


def test_the_char_budget_is_shared_across_the_step(monkeypatch, tmp_projects_dir,
                                                   install_network):
    """
    One budget for the whole step, not one per tool — otherwise the per-turn
    result cap multiplies by the number of tools.
    """
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    seen = []

    def _fake_dispatch(session, tu, collector, **kw):
        seen.append(id(kw.get("result_char_budget")))
        return iter(())

    monkeypatch.setattr(chat_service, "_dispatch_real_tool_call", _fake_dispatch)
    _drive(chat_service.ChatSession(), [_tu("t1"), _tu("t2"), _tu("t3")])
    assert len(set(seen)) == 1, "each tool got its own char budget"
