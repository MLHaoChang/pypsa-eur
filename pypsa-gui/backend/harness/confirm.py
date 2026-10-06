"""
The confirmation gate: which safety tiers get a card (and the Guided
write-tier rule), the auto-approve exemption, the parallel-destructive
refusal, and the blocking gate itself.

Moved from harness/loop.py (the former services/chat_service.py) on
2026-10-05, chat harness issue 08, by AST selection of whole top-level
nodes; the loop re-imports every function and class, so `chat_service.<name>`
is the same object, and forwards every tunable here (PEP 562), so a read
through the alias is live. A tunable is patched on THIS module (see
harness/README.md, "Splitting the loop").
"""
from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import Any
import os
from harness.session import ChatSession
from harness import session as harness_session


# Safety tier strings recognised by the M7 parallel-destructive pre-scan.
# These match the textual `Safety: <tier>` markers in chat_tools_schema.py
# tool descriptions. Any tool whose tier is in this set requires confirmation
# AND must not appear alongside another such tool in a single turn.
DESTRUCTIVE_TIERS = frozenset(["destructive", "execution", "execution_long_running"])


# Guided mode (G3, P25 gate B1): "the assistant does the steps, you confirm" —
# every CHANGE confirms, so `write` joins the card there. Expert keeps
# DESTRUCTIVE_TIERS exactly. The M7 parallel pre-scan keeps DESTRUCTIVE_TIERS
# in both modes: several Guided writes in one response are not refused, they
# are carded one after the other (dispatch is sequential and blocks on each).
GUIDED_CONFIRM_TIERS = DESTRUCTIVE_TIERS | frozenset(["write"])


def _confirm_tiers(guided: bool) -> frozenset[str]:
    """The tiers that go through the confirmation card this turn."""
    return GUIDED_CONFIRM_TIERS if guided else DESTRUCTIVE_TIERS


def _is_guided(ui_context: Any) -> bool:
    """The turn's mode, from the same allow-listed key `_format_ui_context`
    reads: exactly the string 'guided'; anything else is Expert."""
    return isinstance(ui_context, dict) and ui_context.get("ui_mode") == "guided"


# Per-tier auto-approve policy (#18). A comma-separated list of safety tiers
# (intersected with DESTRUCTIVE_TIERS — only destructive/execution tiers are
# confirmable) that the runtime auto-approves WITHOUT the human round-trip.
# Default empty → every destructive tool still shows a confirmation card (zero
# behavioural change). The M7 parallel-destructive pre-scan is UPSTREAM of this
# and is NOT relaxed — auto-approve drops the human wait, not the serialisation
# invariant. Read at call time via the module attribute so a test can
# monkeypatch AUTO_APPROVE_TIERS directly.
AUTO_APPROVE_TIERS: frozenset[str] = frozenset(
    t.strip().lower()
    for t in os.environ.get("PYPSA_GUI_CHAT_AUTO_APPROVE_TIERS", "").split(",")
    if t.strip()
) & DESTRUCTIVE_TIERS


def find_parallel_destructive(tool_calls: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return the list of tool_use blocks in `tool_calls` whose safety tier is
    destructive / execution / execution_long_running, if there are TWO OR
    MORE. Empty list when the model emitted at most one destructive call —
    serial confirmation flow is OK.

    Each tool_use block is `{tool_use_id, name, args, safety_tier}`. The
    pre-scan looks at `safety_tier`; callers populate it by looking up the
    tool's `Safety: <tier>` marker in chat_tools_schema.TOOLS at request
    time (Phase 3 will derive this from the description; Phase 2 stub
    accepts an explicit `safety_tier` per block).
    """
    destructives = [
        b for b in tool_calls
        if (b.get("safety_tier") or "").lower() in DESTRUCTIVE_TIERS
    ]
    if len(destructives) <= 1:
        return []
    return destructives


def _safety_tier_for(tool_name: str) -> str:
    """
    Resolve a tool's safety tier (read / write / destructive / execution /
    execution_long_running) by grepping the documented `Safety: <tier>`
    marker in its description.

    THIS DEFAULT IS FAIL-**OPEN**, and the docstring used to claim the
    opposite ("fails closed"). An unknown or unmarked tool resolves to
    "read", and "read" is precisely the tier that gets NO confirmation card
    — so a destructive tool whose author forgot the marker would execute
    unconfirmed. The old wording named the behaviour ("no confirmation
    card") while mislabelling its direction, which is how it survived
    review.

    The default is left as-is deliberately: making it confirmable would
    start gating tools that are legitimately unmarked-as-read, changing
    behaviour for the whole registry to defend against a case that does not
    currently exist. What keeps it safe instead is
    `test_every_tool_safety_marker_resolves_to_known_tier`
    (tests/test_chat_tools_dispatch.py), which asserts every TOOLS entry
    carries a marker resolving to its own literal tier — so a missing marker
    is a CI failure rather than a silent runtime fail-open.
    """
    # Lazy import — keeps services.chat_service import-light when only the
    # Phase 0/2 helpers are needed.
    from harness.catalogue import TOOLS
    for tool in TOOLS:
        if tool["name"] == tool_name:
            desc = tool["description"]
            for tier in ("execution_long_running", "execution", "destructive",
                          "write", "read"):
                if f"Safety: {tier}" in desc:
                    return tier
            return "read"
    return "read"


def _confirm_destructive_tool(
    session: ChatSession,
    *,
    tool_use_id: str,
    tool_name: str,
    args: dict[str, Any],
    tier: str,
    tool_results_collector: list[dict[str, Any]],
    guided: bool = False,
) -> Generator[tuple[str, dict[str, Any]], None, bool]:
    """
    Gate a destructive tool on the user's confirmation. Returns whether to
    proceed.

    Yields `tool_pending_confirmation` (carrying the token and TTL), BLOCKS on
    the decision, and on anything but approval emits `tool_error` and pairs an
    `is_error` result for this `tool_use_id`. That pairing is not optional:
    Anthropic requires one result per `tool_use`, and a gap surfaces on the NEXT
    turn as an SDK 400 rather than as a permissions problem.

    Returning False aborts THIS tool, not the turn — the turn loop carries on
    with the remaining tool calls.

    Not every destructive tool is gated: `AUTO_APPROVE_TIERS` exempts some, and
    dropping either half of `tier in DESTRUCTIVE_TIERS and tier not in
    AUTO_APPROVE_TIERS` fails in a different direction — one blocks exempt tools
    on a prompt nobody sent, the other runs destructive tools unprompted.

    In Guided mode (`guided`) the `write` tier is gated too
    (`GUIDED_CONFIRM_TIERS`, P25 gate B1); `AUTO_APPROVE_TIERS` never contains
    `write`, so a Guided write always asks.

    Phase E of `docs/superpowers/plans/2026-09-09-chat-turn-loop-decomposition.md`;
    see `tests/test_chat_confirmation_gate_seam.py`.
    """
    if tier in _confirm_tiers(guided) and tier not in AUTO_APPROVE_TIERS:
        pc = session.issue_confirmation(
            tool_name=tool_name, args=args, safety_tier=tier,
        )
        yield "tool_pending_confirmation", {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "args": args,
            "safety_tier": tier,
            "confirmation_token": pc.token,
            "ttl_seconds": harness_session.CONFIRMATION_TTL_SECONDS,
        }
        decision = session.wait_for_decision(pc.token)
        if decision != "approve":
            error_kind = {
                "deny": "confirmation_denied",
                "expired": "confirmation_expired",
                "aborted": "aborted",
            }.get(decision, "unknown_decision")
            yield "tool_error", {
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "error_kind": error_kind,
                "message": f"{decision} on confirmation for {tool_name!r}",
            }
            tool_results_collector.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": True,
                "content": error_kind,
            })
            return False
    return True
