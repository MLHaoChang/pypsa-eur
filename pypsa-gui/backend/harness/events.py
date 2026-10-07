"""
The closed vocabulary of frames the turn loop yields to the UI.

`run_turn` and `agent_loop_stub` are generators of `(event, payload)` tuples;
`routers/chat.py` frames each one as an SSE event of that name and the React
panel switches on it. Until now the set was implicit in the `yield` sites of
`services/chat_service.py`. It is written down here so that

  * a provider swap cannot change what the UI receives — every provider's
    output is translated into these frames and nothing else;
  * a new frame cannot reach the UI unnamed: `tests/test_harness_layout.py`
    scans the loop with `ast` and fails on a name outside `FRAMES`;
  * the frontend contract has one place to read.

Adding a frame: add it here with its payload keys, teach the panel, then
yield it. The tool-error `error_kind` values are a separate contract
(`pypsa-gui/tool-error-kinds.json`), not repeated here.
"""
from __future__ import annotations

# event name -> the payload keys a consumer may rely on (informational; the
# tripwire checks names only).
FRAME_PAYLOADS: dict[str, tuple[str, ...]] = {
    # Turn lifecycle
    "session_init": ("session_id", "model", "profile_id"),
    "turn_done": ("usage",),
    "session_done": ("reason",),
    "error": ("error_kind", "message"),
    "model_fallback": ("from_model", "to_model"),
    "project_rebound": ("project",),
    # Model output
    "token": ("delta",),
    "thinking": ("delta",),
    # Tool lifecycle, in order
    "tool_request": ("tool_use_id", "tool_name", "args", "safety_tier"),
    "tool_pending_confirmation": (
        "tool_use_id", "tool_name", "args", "safety_tier",
        "confirmation_token", "ttl_seconds",
    ),
    "tool_preparing": ("tool_use_id", "tool_name"),
    "tool_running": ("tool_use_id", "tool_name"),
    "tool_progress": ("tool_use_id", "line"),
    "tool_result": ("tool_use_id", "tool_name", "result"),
    "tool_error": ("tool_use_id", "tool_name", "error_kind", "message"),
    # UI control (a `_ui_event` tool result becomes a frame)
    "ui_event": ("kind",),
    # `ask_user` (issue 04): the panel renders a Choice card; the pick is
    # the next user message, so the turn does not block on it.
    # Issue 15: `multi_select`, a Markdown `detail` (or null) and the
    # presentation `intent` ("choice" | "plan_review").
    "choice_request": ("tool_use_id", "title", "question", "options", "allow_free_text",
                       "multi_select", "detail", "intent"),
    # After start_workflow / advance_workflow / end_workflow: the session's
    # workflow step for the panel's strip, or `workflow: null`.
    "workflow_state": ("workflow",),
}

FRAMES: frozenset[str] = frozenset(FRAME_PAYLOADS)
