"""
The solver bridge: while one `run_simulation` / `run_ac_pf_stage` tool call
runs, `solver_log_bridge` subscribes to the active solver's BufferedLogQueue
and yields a `tool_progress` payload per log line, tagged by
`_classify_solver_line` (PHASE / VALIDATION / TRACEBACK / ERROR / INFO) so the
panel can style it. The F9 / F10 / M3 invariants it keeps are in its docstring.

Moved from harness/loop.py (the former services/chat_service.py), chat
harness issue 08, by AST selection of whole top-level nodes; the loop
re-imports every function and class, so `chat_service.<name>` is the same
object, and forwards every tunable (PEP 562). A tunable or a patched
function that lives here is patched on THIS module (harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
import time

from harness.session import ChatSession
from services.project_context import ProjectContext


def _classify_solver_line(line: str) -> str:
    """
    Tag a solver log line for the chat `tool_progress` frame so the UI can
    style PHASE / VALIDATION / TRACEBACK distinctly (mirrors
    routers/simulation.py [PHASE] / [VALIDATION] / TRACEBACK markers).
    """
    if line.startswith("[PHASE]"):
        return "PHASE"
    if line.startswith("[VALIDATION]"):
        return "VALIDATION"
    if line.startswith("TRACEBACK"):
        return "TRACEBACK"
    if line.startswith("ERROR"):
        return "ERROR"
    return "INFO"


def solver_log_bridge(
    session: ChatSession,
    ctx: ProjectContext,
    *,
    poll_interval: float = 0.05,
    is_solver_done: Callable[[], bool] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Subscribe to the ACTIVE solver's BufferedLogQueue for the lifetime of one
    `run_simulation` / `run_ac_pf_stage` tool call, yielding `tool_progress`
    payloads dict[{"line": str, "kind": str}] for each new log line.

    F10 invariant — capture `(ctx, log_queue)` ONCE under
    `ctx.solver_state_lock`. If the user switches active project mid-tool,
    the captured queue STILL belongs to the original ctx (the one that
    actually started the solve), so the chat agent observes a consistent
    stream and a quiet end-of-stream when that solver finishes.

    F9 invariant — `try / finally` unsubscribe so a closed browser tab can
    never leak the per-subscriber deque + lock.

    M3 / F8 — the None sentinel is NEVER appended to the subscriber deque
    (Phase 0 BufferedLogQueue.put: the fanout sits INSIDE the
    `if item is not None:` block). The bridge therefore never sees None and
    keeps polling until `is_solver_done()` returns True OR the session
    abort_event fires.
    """
    # F10: snapshot under solver_state_lock so a concurrent project switch
    # cannot swap the queue out from under us.
    with ctx.solver_state_lock:
        log_queue = ctx.solver_state.get("log_queue")
    if log_queue is None:
        return  # no active solver to bridge

    sub_id, dq = log_queue.subscribe()
    try:
        while True:
            if session.abort_event.is_set():
                return
            drained = 0
            while dq:
                line = dq.popleft()
                yield {"line": line, "kind": _classify_solver_line(line)}
                drained += 1
                if drained >= 64:
                    break  # don't starve the abort check on a burst
            if is_solver_done is not None and is_solver_done():
                # Drain any final tail (the solver's last few lines may have
                # landed AFTER our last poll but BEFORE the done flag flipped).
                while dq:
                    line = dq.popleft()
                    yield {"line": line, "kind": _classify_solver_line(line)}
                return
            time.sleep(poll_interval)
    finally:
        # F9: unsubscribe always — closed SSE, abort, exception, normal end.
        log_queue.unsubscribe(sub_id)
