"""
The scripted stub loop behind `StreamRequest.script`: drives SSE frames and
the confirmation card without a model, for the SSE and card tests.

Moved from harness/loop.py (the former services/chat_service.py), chat
harness issue 08, by AST selection of whole top-level nodes; the loop
re-imports every function and class, so `chat_service.<name>` is the same
object, and forwards every tunable (PEP 562). A tunable or a patched
function that lives here is patched on THIS module (harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from harness import session as harness_session
from typing import Any
import uuid
from harness.catalogue import TOOLS
from harness.confirm import DESTRUCTIVE_TIERS, find_parallel_destructive
from harness.providers.wiring import _resolve_turn_profile
from harness.session import ChatSession


def agent_loop_stub(
    session: ChatSession,
    script: list[dict[str, Any]],
    *,
    confirmation_wait_seconds: float | None = None,
    is_disconnected: Callable[[], bool] | None = None,
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """
    Drive a scripted sequence of SSE frames. The stub stands in for the
    Phase 3 Anthropic agent loop so Phase 2 can exercise:

      * session_init frame
      * token / thinking frames
      * tool_request → tool_pending_confirmation → wait → tool_result loop
      * M7 parallel-destructive pre-scan (>1 destructive in a `tool_batch`
        emits TWO `tool_error` frames AND no confirmation card)
      * M8 abort-on-disconnect (polls `is_disconnected` between steps)

    Each `script` entry is a dict with a `type` key:
      * `{"type": "token", "text": ...}` → yields ("token", {"delta": ...}).
      * `{"type": "thinking", "text": ...}` → yields ("thinking", ...).
      * `{"type": "tool_call", "tool_use_id", "name", "args", "safety_tier",
         "result"}` → for read/write tier, immediately emit tool_request +
         tool_result. For destructive/execution tier, emit tool_request +
         tool_pending_confirmation, BLOCK on wait_for_decision; emit
         tool_running + tool_result on approve, tool_error on
         deny/expired/aborted.
      * `{"type": "tool_batch", "calls": [...]}` → M7 pre-scan. If >1
         destructive, emit a tool_error per call. Otherwise dispatch each
         in sequence as a single `tool_call`.
      * `{"type": "turn_done"}` → final frame with usage rollup.
      * `{"type": "session_done"}` → final session_done frame.
      * `{"type": "error", "error_kind", "message"}` → emit an error frame.

    Yields `(event_name, payload)` tuples; the SSE writer turns them into
    `sse_frame(...)` bytes.
    """
    # Phase 4 QA fix: clear any abort state from a previous turn so /abort
    # is one-shot. Mirrors run_turn (E2E QA: INT-004).
    session.abort_event.clear()

    # session_init: tools + replay (Phase 4 polish) + model identity
    # Task 7 — the stub is driven by `routers/chat.py`'s script path, which
    # binds `session.profile_id`/`bound_wire` the SAME way the real run_turn
    # path does, before branching on `has_explicit_script`. Reported here too
    # so a script-driven SSE test can assert the binding without needing a
    # live/fake provider at all.
    stub_profile = _resolve_turn_profile(session)
    yield "session_init", {
        "session_id": session.session_id,
        "session6": session.session6(),
        "model": session.model,
        "tool_count": len(TOOLS),
        "profile_id": stub_profile.id,
        "profile_label": stub_profile.label,
    }

    for step in script:
        if session.abort_event.is_set():
            yield "session_done", {"reason": "aborted"}
            return
        if is_disconnected is not None and is_disconnected():
            # M8: client closed mid-stream. Set abort and exit cleanly.
            session.abort_event.set()
            yield "session_done", {"reason": "disconnected"}
            return

        kind = step.get("type")

        if kind == "token":
            yield "token", {"delta": step.get("text", "")}
            continue

        if kind == "thinking":
            yield "thinking", {"delta": step.get("text", "")}
            continue

        if kind == "tool_batch":
            calls = step.get("calls") or []
            offenders = find_parallel_destructive(calls)
            if offenders:
                # M7: emit one tool_error per destructive call, NO
                # confirmation cards. The agent must re-issue these one at
                # a time in a future turn.
                for call in offenders:
                    yield "tool_error", {
                        "tool_use_id": call.get("tool_use_id"),
                        "tool_name": call.get("name"),
                        "error_kind": "parallel_destructive_not_allowed",
                        "message": (
                            "two or more destructive / execution tool calls "
                            "were issued in a single turn — confirmation "
                            "cards are only available one at a time. "
                            "Re-issue each tool in its own turn."
                        ),
                    }
                continue
            # No parallel-destructive — dispatch each call in sequence.
            for call in calls:
                yield from _dispatch_stub_call(
                    session, call,
                    confirmation_wait_seconds=confirmation_wait_seconds,
                )
            continue

        if kind == "tool_call":
            yield from _dispatch_stub_call(
                session, step,
                confirmation_wait_seconds=confirmation_wait_seconds,
            )
            continue

        if kind == "turn_done":
            with session._lock:
                usage_snapshot = dict(session.usage_acc)
                # W-3 — ships alongside the totals so the client can
                # tell "nothing used" from "never reported".
                usage_snapshot["reported"] = session.usage_reported
            yield "turn_done", {
                "turn_id": step.get("turn_id"),
                "usage": usage_snapshot,
            }
            continue

        if kind == "session_done":
            yield "session_done", {"reason": step.get("reason", "complete")}
            return

        if kind == "error":
            yield "error", {
                "error_kind": step.get("error_kind", "unspecified"),
                "message": step.get("message", ""),
            }
            continue

        # Unknown step kind — emit a clear error so tests catch typos.
        yield "error", {
            "error_kind": "unknown_step_kind",
            "message": f"agent_loop_stub: unknown step kind {kind!r}",
        }


def _dispatch_stub_call(
    session: ChatSession,
    call: dict[str, Any],
    *,
    confirmation_wait_seconds: float | None,
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """
    Phase 2 stub: handle one tool_call entry from the script. Read/write
    tiers run immediately; destructive/execution tiers go through the
    confirmation card lifecycle.
    """
    tool_use_id = call.get("tool_use_id") or uuid.uuid4().hex
    tool_name = call.get("name") or "<missing-name>"
    args = call.get("args") or {}
    tier = (call.get("safety_tier") or "read").lower()
    stub_result = call.get("result")

    # Always tell the client the agent wants to call this tool.
    yield "tool_request", {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "args": args,
        "safety_tier": tier,
    }

    if tier in DESTRUCTIVE_TIERS:
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

        decision = session.wait_for_decision(
            pc.token, timeout=confirmation_wait_seconds,
        )

        if decision == "approve":
            yield "tool_running", {"tool_use_id": tool_use_id, "tool_name": tool_name}
            ref = {"tool_use_id": tool_use_id, "tool_name": tool_name,
                   "summary": stub_result}
            session.push_result_ref(ref)
            yield "tool_result", {
                "tool_use_id": tool_use_id, "tool_name": tool_name,
                "result": stub_result,
            }
            return
        if decision == "deny":
            yield "tool_error", {
                "tool_use_id": tool_use_id, "tool_name": tool_name,
                "error_kind": "confirmation_denied",
                "message": f"user denied confirmation for {tool_name!r}",
            }
            return
        if decision == "expired":
            yield "tool_error", {
                "tool_use_id": tool_use_id, "tool_name": tool_name,
                "error_kind": "confirmation_expired",
                "message": (
                    f"confirmation TTL elapsed without user action for "
                    f"{tool_name!r}"
                ),
            }
            return
        # aborted
        yield "tool_error", {
            "tool_use_id": tool_use_id, "tool_name": tool_name,
            "error_kind": "aborted",
            "message": "session aborted before confirmation",
        }
        return

    # Non-destructive tier — execute immediately (stubbed).
    yield "tool_running", {"tool_use_id": tool_use_id, "tool_name": tool_name}
    ref = {"tool_use_id": tool_use_id, "tool_name": tool_name,
           "summary": stub_result}
    session.push_result_ref(ref)
    yield "tool_result", {
        "tool_use_id": tool_use_id, "tool_name": tool_name,
        "result": stub_result,
    }
