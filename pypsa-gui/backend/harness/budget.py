"""
The turn budgets: per-turn and per-session output and tool-call caps, the
per-project daily token cap, the stream retry schedule and the per-tool
timeout, and the gate that checks the session and daily caps before a
turn builds a provider.

Moved from harness/loop.py (the former services/chat_service.py), chat
harness issue 08, by AST selection of whole top-level nodes; the loop
re-imports every function and class, so `chat_service.<name>` is the same
object, and forwards every tunable (PEP 562). A tunable or a patched
function that lives here is patched on THIS module (harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

from typing import Any
import os
from harness import history as harness_history
from harness.session import ChatSession


# Hard per-session token caps. The client shows the running token counts
# (M10), but the server enforces a token-count ceiling so a misbehaving
# model + tool-use loop cannot burn unbounded budget. Defaults match the v6
# plan; ops can override via env or a future endpoint.
MAX_OUTPUT_TOKENS_PER_TURN: int = 8192


MAX_TOOL_CALLS_PER_TURN: int = 25


MAX_TURNS_PER_SESSION: int = 100


MAX_OUTPUT_TOKENS_PER_SESSION: int = 200_000


# Transient-SDK-error retry (chat reliability). A rate-limit (429) or an
# Anthropic overload (5xx) that fails the stream BEFORE any token is emitted is
# retried with capped exponential backoff (1s → 2s → 4s, capped at 8s). A
# failure AFTER partial output is surfaced instead — re-streaming would
# duplicate already-yielded tokens. Env-overridable.
MAX_STREAM_RETRIES: int = int(os.environ.get("PYPSA_GUI_CHAT_MAX_RETRIES", "3"))


BASE_STREAM_RETRY_DELAY: float = float(os.environ.get("PYPSA_GUI_CHAT_RETRY_BASE", "1.0"))


MAX_STREAM_RETRY_DELAY: float = float(os.environ.get("PYPSA_GUI_CHAT_RETRY_MAX", "8.0"))


# error_kind values from _map_sdk_exception that are worth retrying.
_RETRYABLE_SDK_KINDS: frozenset[str] = frozenset(["rate_limited", "upstream_error"])


# Cross-session durable per-project/per-day token spend cap (#9). 0 = DISABLED
# (default — ops opts in). When > 0, run_turn sums input+output tokens from
# THIS project's chat.jsonl (+ rotation backup) for records stamped today and
# refuses a NEW turn once the sum reaches the cap. Complements the in-memory
# per-session output ceiling (MAX_OUTPUT_TOKENS_PER_SESSION) — that one resets
# on backend restart / new session; this one is durable on disk. Read at call
# time via the module attribute so a test can monkeypatch it.
PYPSA_GUI_CHAT_DAILY_TOKEN_CAP: int = int(
    os.environ.get("PYPSA_GUI_CHAT_DAILY_TOKEN_CAP", "0")
)


# Per-tool execution deadline (#16). A non-solver tool handler that hangs on a
# blocking read/write would freeze the SSE worker thread indefinitely; we run
# it on a worker thread and abandon it after this many seconds, emitting a
# tool_timeout. Solver tools (run_simulation / run_ac_pf_stage) are EXCLUDED —
# they spawn their own worker + lifecycle poll (solver_log_bridge) and are
# legitimately long-running. Read at call time via the module attribute.
PER_TOOL_TIMEOUT_SECONDS: float = float(
    os.environ.get("PYPSA_GUI_CHAT_TOOL_TIMEOUT", "30.0")
)


def _turn_budget_block(
    session: ChatSession,
    turn_ctx: Any,
) -> tuple[str, dict[str, Any]] | None:
    """
    The frame that refuses this turn on budget grounds, or ``None`` to proceed.

    Both caps are checked here so that both short-circuit in the same place:
    BEFORE `session_init` (the panel treats that frame as "a turn started" and
    would have to tear it down again) and BEFORE the SDK client is built (a
    capped turn must not reach the API). Moving either gate below the client
    build would keep every frame assertion passing while still spending money.

    `turn_ctx` is the P0-pinned context — the project this turn would PERSIST
    to — so a mid-turn project switch cannot move the turn onto another
    project's daily budget.

    Phase B of `docs/superpowers/plans/2026-09-09-chat-turn-loop-decomposition.md`;
    see `tests/test_chat_budget_gates_seam.py`.
    """
    # Cap enforcement — refuse to start a new turn if the session output
    # budget is already exhausted.
    if session.usage_acc["output_tokens"] >= MAX_OUTPUT_TOKENS_PER_SESSION:
        return "session_done", {
            "reason": "budget_exhausted",
            "kind": "output_tokens",
            "limit": MAX_OUTPUT_TOKENS_PER_SESSION,
        }

    # #9 — cross-session durable per-project/per-day token spend cap. Checked
    # against the P0-pinned turn_ctx (the project this turn would persist to),
    # not the live active context. 0 = disabled (default), so zero disk cost
    # unless ops opts in. Sits alongside the session-output ceiling so both
    # budget gates short-circuit BEFORE the SDK client is built (no API call
    # when capped). Reads the module attribute at call time (monkeypatchable).
    daily_cap = PYPSA_GUI_CHAT_DAILY_TOKEN_CAP
    if daily_cap > 0:
        spent = harness_history._today_token_spend(turn_ctx)
        if spent >= daily_cap:
            return "session_done", {
                "reason": "daily_budget_exhausted",
                "kind": "daily_tokens",
                "limit": daily_cap,
                "spent": spent,
            }
    return None
