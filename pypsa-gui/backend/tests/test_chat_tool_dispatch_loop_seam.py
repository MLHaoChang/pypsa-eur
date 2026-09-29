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
           turn_ctx=None, switched=lambda: False, offered=None):
    """
    Run the seam to completion, returning (frames, outcome).

    `offered` is the C-1 allowlist the seam refuses unoffered `tool_use` blocks
    against. It defaults to "everything this call passes in", because every
    case in this file is about dispatch MECHANICS — the call count, the holder,
    the char budget — and would otherwise be testing the capability guard by
    accident. The guard has its own cases in `test_chat_profile_binding.py`;
    pass `offered=` explicitly to exercise it here.
    """
    holder = holder if holder is not None else ["P"]
    results = results if results is not None else []
    if offered is None:
        offered = {tu.get("name") for tu in tool_uses}
    frames = []
    gen = _seam()(
        session, tool_uses,
        tool_call_count=tool_call_count,
        turn_ctx=turn_ctx,
        turn_project_holder=holder,
        project_switched=switched,
        tool_results_for_next_turn=results,
        char_budget={"used": 0},
        offered_tool_names=offered,
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


# ── P27a (deferred spec 2026-09-28 §1.1, §1.2) ─────────────────────────────


def test_a_refused_edit_reaches_the_model_as_study_in_flight(
    monkeypatch, tmp_projects_dir, install_network
):
    """A1: a live-network study refuses an edit with the `study_in_flight`
    dict; the model must receive that KIND and the sentence, through the
    existing tool_error path (kills a mutant that stringifies the dict)."""
    import pypsa
    from fastapi import HTTPException

    from services import chat_tools

    n = pypsa.Network(); n.add("Bus", "B1"); install_network(n, name=None)
    sentence = ("Cannot edit the network while an FMEA sweep is running — it "
                "re-solves the in-memory network between its own iterates.")
    detail = {"error_kind": "study_in_flight", "study": "fmea_sweep",
              "message": sentence}

    def _refuse(**_kw):
        raise HTTPException(status_code=409, detail=detail)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", _refuse)
    results: list[dict] = []
    frames, _outcome = _drive(
        chat_service.ChatSession(),
        [_tu("t1", name="update_component",
             args={"component_class": "Bus", "name": "B1",
                   "attrs": {"v_nom": 220.0}})],
        results=results,
    )
    errors = [p for n_, p in frames if n_ == "tool_error"]
    assert len(errors) == 1, frames
    assert errors[0]["error_kind"] == "study_in_flight", errors[0]
    assert sentence in errors[0]["message"], errors[0]
    # What the MODEL gets: the typed kind, then the sentence.
    assert len(results) == 1 and results[0]["is_error"] is True
    content = str(results[0]["content"])
    assert content.startswith("study_in_flight"), content
    assert sentence in content, content


class _MovableCtx:
    def __init__(self, loaded):
        self.loaded_project = loaded


def _rebinding_harness(monkeypatch, *, start, moves_to, moving_tool):
    """Fake dispatcher that records what ran and moves the binding DURING the
    `moving_tool` call; `get_active_context` reads the moved value."""
    from services.pypsa_service import PyPSAService

    ctx = _MovableCtx(start)
    ran: list[str] = []

    def _fake_dispatch(session, tu, collector, **kw):
        ran.append(tu["id"])
        if tu["name"] == moving_tool:
            ctx.loaded_project = moves_to
        collector.append({"type": "tool_result", "tool_use_id": tu["id"],
                          "content": "ok"})
        return iter(())

    monkeypatch.setattr(chat_service, "_dispatch_real_tool_call", _fake_dispatch)
    monkeypatch.setattr(PyPSAService, "get_active_context",
                        staticmethod(lambda: ctx))
    return ctx, ran


def _assert_announced(monkeypatch, tool, start, to):
    _ctx, _ran = _rebinding_harness(monkeypatch, start=start, moves_to=to,
                                    moving_tool=tool)
    holder = [start]
    frames, _outcome = _drive(chat_service.ChatSession(),
                              [_tu("t1", name=tool, args={})], holder=holder)
    rebound = [p for n_, p in frames if n_ == "project_rebound"]
    assert rebound == [{"from": start, "to": to, "via_tool": tool}], frames
    assert holder[0] == to, "the guard's snapshot did not follow the rebind"


def test_create_project_from_template_announces_the_rebind(monkeypatch):
    _assert_announced(monkeypatch, "create_project_from_template",
                      "the-old-one", "probe-a8")


def test_import_project_bundle_announces_the_rebind(monkeypatch):
    _assert_announced(monkeypatch, "import_project_bundle",
                      "the-old-one", "probe-a8")


def test_save_project_of_an_unbound_draft_announces_the_rebind(monkeypatch):
    _assert_announced(monkeypatch, "save_project", None, "draft-1")


def test_a_second_tool_in_the_same_turn_still_dispatches_after_a_template_create(
    monkeypatch
):
    """The REAL mid-turn-switch closure (never `_drive`'s `lambda: False`,
    which makes this green whatever the set says), and the binding moves
    DURING t1 — as the template route does."""
    from services.pypsa_service import PyPSAService

    _ctx, ran = _rebinding_harness(
        monkeypatch, start="the-old-one", moves_to="probe-a8",
        moving_tool="create_project_from_template")
    holder = ["the-old-one"]
    frames, outcome = _drive(
        chat_service.ChatSession(),
        [_tu("t1", name="create_project_from_template",
             args={"template_id": "eh_datacenter", "new_name": "probe-a8"}),
         _tu("t2")],
        holder=holder,
        switched=lambda: PyPSAService.get_active_context().loaded_project != holder[0],
    )
    assert ran == ["t1", "t2"], f"only {ran} dispatched"
    assert outcome.switched_mid_turn is False
    kinds = [p.get("error_kind") for n_, p in frames if n_ == "tool_error"]
    assert "project_switched_mid_turn" not in kinds, frames


def test_save_a_copy_emits_no_rebind(monkeypatch):
    """Save-a-Copy (`rebind=False`) and a save of the already-bound project
    leave the binding where it was — no frame (the "only on a move" rule)."""
    _ctx, _ran = _rebinding_harness(monkeypatch, start="the-old-one",
                                    moves_to="the-old-one",
                                    moving_tool="save_project")
    holder = ["the-old-one"]
    frames, _outcome = _drive(chat_service.ChatSession(),
                              [_tu("t1", name="save_project",
                                   args={"name": "a-copy"})], holder=holder)
    assert [p for n_, p in frames if n_ == "project_rebound"] == []
    assert holder[0] == "the-old-one"
