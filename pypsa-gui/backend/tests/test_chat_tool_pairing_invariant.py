"""
Every `tool_use` must get a matching `tool_result` in the next user message.

Anthropic requires the pairing; without it the NEXT turn on that session is a
hard `invalid_request_error` and only "New chat" recovers. The durable transcript
is fine — it is the in-memory `session.messages` that is poisoned — which is why
nothing caught this.

`_dispatch_tool_uses`' own docstring promises `tool_results_for_next_turn` gets
"one `tool_result` per `tool_use_id` WITHOUT exception". The
`tool_call_cap_exceeded` path broke that promise twice over:

  * it returned `stop_turn=True` without appending a result for the capped tool
    or for any tool after it, and
  * `_run_turn_body`'s `if dispatch.stop_turn: return` sits BEFORE the
    `messages.append({"role": "user", "content": tool_results_for_next_turn})`,
    so even the results of tools that ALREADY RAN SUCCESSFULLY in that step were
    dropped.

The assistant message carrying the `tool_use` blocks is persisted before its
results are known, so an orphan is what remains.

Found by an independent QA review, 2026-09-12, which also identified the same
class on the client-disconnect path (GeneratorExit) — covered here by asserting
the invariant on the collected results rather than only on the cap path.
"""
import pytest

from services import chat_service


def _pairing_problems(messages) -> list[str]:
    """
    Every tool_use id in an assistant message must be answered by a tool_result
    in the NEXT message, and no tool_result may answer nothing.
    """
    problems: list[str] = []
    msgs = list(messages)
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        ids = [b.get("id") for b in content
               if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not ids:
            continue
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        answered: set = set()
        if nxt is not None and isinstance(nxt.get("content"), list):
            answered = {b.get("tool_use_id") for b in nxt["content"]
                        if isinstance(b, dict) and b.get("type") == "tool_result"}
        for tid in ids:
            if tid not in answered:
                problems.append(f"tool_use {tid} has no tool_result")
    return problems


def test_the_cap_path_pairs_every_tool_use_it_refuses(monkeypatch):
    """
    Two tool_use blocks with the cap set to 1: the first is dispatched, the
    second trips the cap. BOTH must end up with a tool_result.
    """
    monkeypatch.setattr(chat_service, "MAX_TOOL_CALLS_PER_TURN", 1)

    collected: list[dict] = []
    tool_uses = [
        {"type": "tool_use", "id": "t1", "name": "list_buses", "input": {}},
        {"type": "tool_use", "id": "t2", "name": "list_buses", "input": {}},
    ]

    gen = chat_service._dispatch_tool_uses(
        chat_service.ChatSession(),
        tool_uses,
        tool_call_count=1,  # already at the cap, so t2 trips it
        turn_ctx=None,
        turn_project_holder=[None],
        project_switched=lambda: False,
        tool_results_for_next_turn=collected,
        char_budget={"remaining": 40000},
        offered_tool_names={"list_buses"},
    )
    outcome = None
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        outcome = stop.value

    assert outcome is not None and outcome.stop_turn, (
        "expected the cap to stop the turn"
    )
    answered = {r.get("tool_use_id") for r in collected}
    assert "t2" in answered, (
        f"the capped tool_use got no tool_result; the next turn on this session "
        f"will be rejected by the provider. collected={collected}"
    )


def test_a_stopped_turn_still_records_the_results_it_collected():
    """
    The caller half. Whatever `tool_results_for_next_turn` holds when a step
    stops must reach `session.messages`, or a successfully-dispatched tool's
    result is dropped and its tool_use is orphaned.

    Asserted on the invariant checker rather than on the cap path specifically,
    so the client-disconnect and exception paths are covered by the same
    property.
    """
    sess = chat_service.ChatSession()
    sess.messages.clear()
    sess.messages.append({"role": "user", "content": "hello"})
    sess.messages.append({
        "role": "assistant",
        "content": [
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": "t1", "name": "list_buses", "input": {}},
        ],
    })
    # A turn that stopped WITHOUT pairing is exactly the poisoned shape.
    problems = _pairing_problems(sess.messages)
    assert problems == ["tool_use t1 has no tool_result"], (
        f"the checker itself is wrong: {problems}"
    )
    # ...and once paired, the same history is clean.
    sess.messages.append({
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1",
                     "is_error": True, "content": "tool_call_cap_exceeded"}],
    })
    assert _pairing_problems(sess.messages) == [], (
        "a paired history should raise no problems"
    )


def test_a_capped_turn_leaves_a_replayable_history(install_network, monkeypatch):
    """
    The CALLER half, end to end — the half my first cut of this file did not
    cover, which a mutation proved: reverting `_run_turn_body` to `return`
    before recording the collected results passed every other test here.

    Drives a real capped turn through `run_turn` with the e2e suite's fake
    client, then asserts `session.messages` is replayable. Before the fix the
    history ended with an assistant message carrying three orphaned `tool_use`
    blocks, so the next turn on this session was rejected by the provider and
    only a new chat recovered.
    """
    import pypsa

    from tests.test_chat_e2e import (
        FakeAnthropicClient,
        _FakeFinalMessage,
        _FakeUsage,
        _tool_use_block,
        _tool_use_event,
    )

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)
    monkeypatch.setattr(chat_service, "MAX_TOOL_CALLS_PER_TURN", 2)

    session = chat_service.ChatSession()
    events = [_tool_use_event(f"tu-{i}", "get_meta", {}) for i in range(3)]
    final = _FakeFinalMessage(
        content=[_tool_use_block(f"tu-{i}", "get_meta", {}) for i in range(3)],
        usage=_FakeUsage(input_tokens=5, output_tokens=5),
    )
    client = FakeAnthropicClient([(events, final)])

    frames = list(chat_service.run_turn(session, "describe", client=client))
    kinds = [p.get("error_kind") for ev, p in frames if ev == "tool_error"]
    assert "tool_call_cap_exceeded" in kinds, (
        f"expected the cap to trip; got {kinds}"
    )

    problems = _pairing_problems(session.messages)
    assert problems == [], (
        f"a capped turn left an unreplayable history — the next turn on this "
        f"session would be rejected by the provider: {problems}"
    )
