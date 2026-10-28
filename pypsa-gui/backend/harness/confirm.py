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
import copy
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

# Write-tier tools Guided gates as Expert does (LC-16 OJ-21, "one card, one
# approval in Expert or Guided"): each stores a record and changes nothing in
# the project, and the tool that applies it carries the approval card.
GUIDED_UNCARDED = frozenset(["preview_project_changes"])

# A tool name absent from the catalogue gets the card in every mode.
UNKNOWN_TOOL_TIER = "destructive"


def _confirm_tiers(guided: bool) -> frozenset[str]:
    """The tiers that go through the confirmation card this turn."""
    return GUIDED_CONFIRM_TIERS if guided else DESTRUCTIVE_TIERS


def confirmation_policy(tier: str, *, guided: bool = False, external: bool = False,
                        restriction_mode: str = 'standard') -> str:
    """
    Current gate disposition, also used by the read-only access view.

    ``policy`` still needs a durable approval record; it is not an unconditional
    execution grant. External non-read calls never inherit native exemptions.
    """
    if restriction_mode not in {'standard', 'confirm_changes', 'read_only'}:
        raise ValueError('unknown restriction mode')
    if tier != 'read' and restriction_mode == 'read_only':
        return 'blocked'
    if tier != 'read' and restriction_mode == 'confirm_changes':
        return 'user'
    if tier not in _confirm_tiers(guided) and not (external and tier != 'read'):
        return 'none'
    if tier in AUTO_APPROVE_TIERS and not external:
        return 'policy'
    return 'user'


def confirmation_revision() -> tuple:
    """Server-derived effective rules; a changed set invalidates open decisions."""
    return (DESTRUCTIVE_TIERS, GUIDED_CONFIRM_TIERS, GUIDED_UNCARDED, AUTO_APPROVE_TIERS,
            harness_session.CONFIRMATION_TTL_SECONDS)


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

    A name that is NOT in `TOOLS` resolves to "destructive": the card in
    Expert and Guided, and the parallel-destructive refusal. It used to
    resolve to "read" (no card), so a handler registered without a
    declaration, or a tool that arrives at runtime, would have run
    unconfirmed (tests/test_uncatalogued_tool_tier.py).

    A tool that IS in `TOOLS` but carries no marker still resolves to
    "read". That default is fail-open and left as-is deliberately: what
    keeps it safe is `test_every_tool_safety_marker_resolves_to_known_tier`
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
    return UNKNOWN_TOOL_TIER


def _confirm_destructive_tool(
    session: ChatSession,
    *,
    tool_use_id: str,
    tool_name: str,
    args: dict[str, Any],
    tier: str,
    tool_results_collector: list[dict[str, Any]],
    guided: bool = False,
    external: str | None = None,
    restriction_mode: str = 'standard',
    restriction_snapshot: dict | None = None,
    edit_card: dict | None = None,
    sweep_card: dict | None = None,
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

    An external MCP tool (`external` is its server id; issue 19, owner
    decision Q20) is gated whenever its tier is not `read`, in Expert as in
    Guided, is never auto-approved, and its card says which server it is.

    Phase E of `docs/superpowers/plans/2026-09-09-chat-turn-loop-decomposition.md`;
    see `tests/test_chat_confirmation_gate_seam.py`.
    """
    revision = confirmation_revision()
    policy = confirmation_policy(tier, guided=guided, external=bool(external),
                                 restriction_mode=restriction_mode)
    if policy == 'blocked':
        yield from _deny(tool_use_id, tool_name, 'user_restriction',
                         'Your saved project rule blocks non-read tools.', tool_results_collector)
        return False
    if policy == 'none':
        return True
    from harness import approvals

    link = approvals.links(session, args)
    if policy == 'policy':
        # Issue 18: the log says why no card appeared, and an approval that
        # cannot be put on record does not run (fail closed).
        if approvals.decided(session, call_id=tool_use_id, tool=tool_name, tier=tier,
                             outcome="approved", by="policy", link=link):
            return True
        yield from _deny(tool_use_id, tool_name, _UNAVAILABLE["error_kind"],
                         f"approval for {tool_name!r} could not be recorded",
                         tool_results_collector)
        return False

    pc = session.issue_confirmation(
        tool_name=tool_name, args=args, safety_tier=tier,
    )
    if not approvals.asked(session, call_id=tool_use_id, tool=tool_name, tier=tier,
                           token=pc.token):
        # A card nobody can audit is never shown.
        session.withdraw_confirmation(pc.token)
        yield from _deny(tool_use_id, tool_name, _UNAVAILABLE["error_kind"],
                         f"approval for {tool_name!r} could not be recorded",
                         tool_results_collector)
        return False
    from harness import attention
    attention.confirmation(session, pc, tool_use_id)
    card = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "args": copy.deepcopy(args),
        "safety_tier": tier,
        "confirmation_token": pc.token,
        "ttl_seconds": harness_session.CONFIRMATION_TTL_SECONDS,
    }
    if external:
        card["external"] = {"server": external}
    if edit_card is not None:
        card["edit_card"] = copy.deepcopy(edit_card)
    if sweep_card is not None:
        card["sweep_card"] = copy.deepcopy(sweep_card)  # LC-17, harness/sweep_card.py
    yield "tool_pending_confirmation", card
    decision = session.wait_for_decision(pc.token)
    if decision == 'approve' and confirmation_revision() != revision:
        decision = 'revoked'
    if decision == 'approve' and restriction_snapshot is not None:
        from harness import restrictions
        try:
            restrictions.require_current(restriction_snapshot, session)
        except Exception:  # A failed required policy read cannot approve work.
            decision = 'revoked'
    guard = getattr(session, 'decision_guard', None)
    if decision == 'approve' and guard is not None and not guard(tool_name, args):
        # The harness runner's session re-checks its project, owner and locks
        # after the decision, under the project's locks (harness/runner.py).
        decision = 'revoked'
    outcome, by = _DECISION_OUTCOMES.get(decision, ("unavailable", "system"))
    recorded = approvals.decided(session, call_id=tool_use_id, tool=tool_name, tier=tier,
                                 outcome=outcome, by=by, link=link)
    attention.confirmation(session, pc, tool_use_id, outcome if recorded else 'unavailable')
    if decision == "approve" and recorded:
        return True
    if decision == "approve":
        error_kind = _UNAVAILABLE["error_kind"]
        message = f"approval for {tool_name!r} could not be recorded"
    else:
        error_kind = _REFUSALS.get(decision, _UNKNOWN)["error_kind"]
        message = f"{decision} on confirmation for {tool_name!r}"
    yield from _deny(tool_use_id, tool_name, error_kind, message, tool_results_collector)
    return False


# The refusal each decision ends in. Written as `{"error_kind": ...}` literals
# so tests/test_tool_error_kind_manifest.py sees every kind this gate emits.
_REFUSALS: dict[str, dict[str, str]] = {
    "deny": {"error_kind": "confirmation_denied"},
    "expired": {"error_kind": "confirmation_expired"},
    "aborted": {"error_kind": "aborted"},
    "revoked": {"error_kind": "confirmation_revoked"},      # issue 18
}
_UNKNOWN: dict[str, str] = {"error_kind": "unknown_decision"}
# Issue 18: an approval that could not be put on record does not run.
_UNAVAILABLE: dict[str, str] = {"error_kind": "approval_unavailable"}

# Issue 18: what `wait_for_decision` returned, as (outcome, by) for the log.
_DECISION_OUTCOMES: dict[str, tuple[str, str]] = {
    "approve": ("approved", "user"),
    "deny": ("rejected", "user"),
    "expired": ("expired", "ttl"),
    "aborted": ("aborted", "session"),
    "revoked": ("revoked", "system"),
}


def _deny(tool_use_id: str, tool_name: str, error_kind: str, message: str,
          tool_results_collector: list[dict[str, Any]],
          ) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """The refusal: a `tool_error` frame and the paired `is_error` result
    (Anthropic requires one result per `tool_use`)."""
    yield "tool_error", {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "error_kind": error_kind,
        "message": message,
    }
    tool_results_collector.append({
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "is_error": True,
        "content": error_kind,
    })
