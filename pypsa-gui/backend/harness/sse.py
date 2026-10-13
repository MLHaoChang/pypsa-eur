"""
SSE framing for the turn loop (chat harness issue 08, first extraction).

`run_turn` yields `(event, payload)` tuples; this turns one into the bytes
the route writes. Moved verbatim from the loop (AST-selected, not a line
range); `harness.loop.sse_frame` is the same object.
"""
from __future__ import annotations

import json
from typing import Any


def sse_frame(event: str, data: dict[str, Any]) -> bytes:
    """
    Render one SSE frame in the `event:`/`data:`/blank-line convention.

    Belt-and-suspenders defence: `default=str` ensures a Pydantic model that
    leaks past `_truncate_result` (or any other future tool result path) is
    stringified rather than crashing the entire SSE stream with
    ``TypeError: Object of type X is not JSON serializable``. The
    ``_truncate_result.``→``_coerce_jsonable`` pipeline is the *primary* fix
    — it produces proper dict shapes for the LLM. This fallback exists so a
    bug-by-omission elsewhere in the pipeline can't take the chat panel down.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()
