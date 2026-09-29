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
