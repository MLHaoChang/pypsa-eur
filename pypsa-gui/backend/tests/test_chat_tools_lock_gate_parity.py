"""
The chat surface's lock-gate prefixes must match the HTTP middleware's.

`main._FOREIGN_LOCK_GATE_PREFIXES` gates HTTP writes; `chat_tools._LOCK_GATE_PREFIXES`
gates the same writes when they arrive as TOOL CALLS, because a chat tool calls its
route handler as a plain function inside the SSE generator, long after any
middleware ran. They are two copies of one decision.

They drifted: the HTTP gate gained `/api/results/` on 2026-09-12 (the five
adequacy-study POSTs re-solve the shared network) and this copy did not. The drift
was latent rather than exploitable — `TOOL_ROUTES` maps no tool to those POSTs
today — but `_lock_gated_tool_names`' docstring promises that "a tool added later
against a new route is gated the day it lands, with nothing to remember", and that
promise was false for the new prefix. Found by an independent QA review.

A comment asking two constants to stay in step is not a mechanism. This is.
"""
import main
from services import chat_tools


def test_the_two_lock_gate_prefix_sets_agree():
    http_gate = set(main._FOREIGN_LOCK_GATE_PREFIXES)
    chat_gate = set(chat_tools._LOCK_GATE_PREFIXES)
    assert chat_gate == http_gate, (
        "chat_tools._LOCK_GATE_PREFIXES has drifted from "
        "main._FOREIGN_LOCK_GATE_PREFIXES.\n"
        f"  only in the HTTP gate: {sorted(http_gate - chat_gate)}\n"
        f"  only in the chat gate: {sorted(chat_gate - http_gate)}\n"
        "A write reachable as a tool call must be gated the same way as the same "
        "write over HTTP; whichever set is missing an entry is the hole."
    )



def _concrete(path: str) -> str:
    """A route TEMPLATE as the middleware would see it on the wire.

    `TOOL_ROUTES` stores templates (`/queue/{job_id}/abort`) while main.py
    matches concrete paths against UUID-anchored patterns, so a template has to
    be filled in before main's predicate can be asked about it.
    """
    return path.replace("{job_id}", "00000000-0000-4000-8000-000000000000")


def test_every_route_a_tool_reaches_is_exempted_identically_on_both_surfaces():
    """
    The prefix test above was not enough, for the reason its own docstring
    gives: "A comment asking two constants to stay in step is not a mechanism."
    The EXEMPTIONS were the second pair of constants, and they drifted too:

      * chat's exempt set had only the three queue paths, so
        `abort_adequacy_study` was refused under a foreign lock in chat while
        the same POST succeeded over HTTP — the "trapped study" main.py's
        comment calls "actively harmful";
      * main's own set said "the five adequacy-study ABORTS" and never gained
        the Energy Hub one, which calls the same `_abort_study` helper. So an
        EH study could be trapped over plain HTTP as well. Both surfaces agreed
        on that one, which is how a pairwise check would have missed it — the
        test below asks the question per ROUTE, against the real predicate.

    Equality of the two sets would be the wrong invariant: main exempts routes
    no tool reaches (queue pause/resume, preflight). What must hold is that
    for every route a tool can actually call, both surfaces give the same
    answer.
    """
    from services.chat_tools_schema import TOOL_ROUTES

    mismatched = []
    for tool, routes in sorted(TOOL_ROUTES.items()):
        for route in routes:
            if not isinstance(route, tuple):
                continue
            _method, path = route
            http = main._foreign_lock_gate_exempt(_concrete(path))
            chat = path in chat_tools._LOCK_GATE_EXEMPT_PATHS
            if http != chat:
                mismatched.append((tool, path, f"http={http}", f"chat={chat}"))
    assert not mismatched, (
        "a route is exempted from the foreign-lock gate on one surface and not "
        "the other:\n  " + "\n  ".join(map(str, mismatched))
    )


def test_every_study_abort_is_exempt_over_http():
    """Pinned by name, because the per-route test above cannot see an abort
    that NO tool routes to — and this list went stale once already."""
    from services.chat_tools_schema import TOOL_ROUTES

    aborts = {
        path for routes in TOOL_ROUTES.values() for route in routes
        if isinstance(route, tuple)
        for path in [route[1]]
        if path.startswith("/api/results/") and path.endswith("/abort")
    }
    assert aborts, "no study-abort routes found; this test's premise is gone"
    trapped = sorted(p for p in aborts if not main._foreign_lock_gate_exempt(p))
    assert not trapped, (
        f"these study aborts are gated over HTTP, so a foreign lock would trap "
        f"the study with no way to stop it: {trapped}"
    )


def test_the_abort_tool_is_not_lock_gated_in_chat():
    """The user-visible consequence, stated directly."""
    assert "abort_adequacy_study" not in chat_tools._lock_gated_tool_names()
