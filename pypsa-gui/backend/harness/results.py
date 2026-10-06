"""
Tool-result shaping: JSON coercion, truncation, the per-turn result
budget, error content, and the fenced block the model receives.

Moved from harness/loop.py (the former services/chat_service.py) on
2026-10-05, chat harness issue 08, by AST selection of whole top-level
nodes; the loop re-imports every name, so `chat_service.<name>` is the same
object. A tunable here is patched on THIS module (see harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

from services.redaction import (  # moved 2026-08-13 (provider seam, Task 1)
    redact_secrets_in_str as _redact_secrets_in_str,
)
from typing import Any
from harness import fence as harness_fence
from harness.fence import _UNTRUSTED_CLOSE, _UNTRUSTED_OPEN


# Cap on a single tool result's serialized size handed to the model. Oversized
# A7 — aggregate cap across all tool_result payloads in one turn (after the
# per-result `_RESULT_CONTENT_CAP` cut). Further results become an omitted stub.
MAX_TOOL_RESULT_CHARS_PER_TURN: int = 40_000


# results are cut WITH an explicit marker (see _result_to_anthropic_content) so
# the model knows data was elided rather than treating a partial blob as whole.
_RESULT_CONTENT_CAP: int = 4000


def _coerce_jsonable(value: Any) -> Any:
    """
    Recursively coerce Pydantic models (and lists/dicts containing them)
    into plain JSON-serialisable structures.

    Why this exists: some chat tools call FastAPI route handlers directly
    (in-process), and those handlers return Pydantic models (e.g.
    `create_scenario` → `ProjectInfo`, `load_project` → `ImportSummary`).
    When the dispatcher emits the tool_result SSE frame, `json.dumps`
    raises ``TypeError: Object of type ProjectInfo is not JSON serializable``
    because Pydantic models aren't natively JSON-serialisable — the SSE
    stream stalls and the chat panel hangs on the "running" indicator.

    Coercion happens here (one place) instead of per-tool so a future
    tool can return a model without remembering to call ``.model_dump()``
    manually. ``BaseModel.model_dump()`` returns plain Python primitives,
    so the result is safe for both Anthropic's tool_result content
    contract AND the SSE frame writer.
    """
    # Pydantic v2 BaseModel — duck-typed on `model_dump` to avoid an
    # import-time dependency in this module.
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return value.model_dump()
        except Exception:  # noqa: BLE001 — fall through to str repr
            return str(value)
    if isinstance(value, list):
        return [_coerce_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _coerce_jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_coerce_jsonable(v) for v in value]
    return value


def _truncate_result(result: Any, limit: int = 4000) -> Any:
    """
    Cap large results so a single tool call doesn't blow the chat panel +
    Anthropic context. List/dict get truncated structurally; scalars
    pass through.

    Pre-step: route ``result`` through ``_coerce_jsonable`` so any Pydantic
    leak from a tool that wraps a FastAPI route handler is normalised to
    plain dicts before the truncation + serialisation logic runs.
    """
    result = _coerce_jsonable(result)
    if isinstance(result, list):
        if len(result) > 200:
            return {
                "_truncated": True,
                "total": len(result),
                "sample": result[:200],
            }
        return result
    if isinstance(result, dict):
        # If the dict is huge when serialised, fall back to a string repr.
        try:
            import json
            s = json.dumps(result, default=str)
            if len(s) > limit:
                return {"_truncated": True, "length": len(s),
                         "preview": s[:limit] + "..."}
        except Exception:  # noqa: BLE001
            pass
        return result
    return result


def _truncation_marker(total: int, shown: int) -> str:
    """The explicit sentinel appended when a tool result is cut for the model."""
    return (
        f" …[RESULT TRUNCATED: showed {shown} of {total} chars — "
        "call a narrower / paginated query for the rest]"
    )


def _apply_turn_tool_result_budget(
    content: Any,
    budget: dict[str, int],
) -> Any:
    """
    A7 — enforce MAX_TOOL_RESULT_CHARS_PER_TURN across tool_result bodies.

    Once the running total is exhausted, replace further full payloads with a
    small omitted stub so the model knows to narrow queries.
    """
    text = content if isinstance(content, str) else str(content)
    length = len(text)
    if budget.get("used", 0) >= MAX_TOOL_RESULT_CHARS_PER_TURN:
        return _result_to_anthropic_content({
            "_omitted": True,
            "length": length,
            "reason": "per_turn_tool_result_budget",
            "message": (
                "Per-turn tool-result budget exhausted — request a narrower "
                "query for further data."
            ),
        })
    budget["used"] = budget.get("used", 0) + length
    return content


# Bound on the free-text half of an is_error result. Load-bearing rather than
# cosmetic: this string is replayed on EVERY later turn of the session, so an
# unbounded error body is charged for repeatedly.
_ERROR_DETAIL_CAP: int = 1000


def _error_result_content(detail: Any, exc: BaseException, error_kind: str) -> str:
    """
    The MODEL-FACING content of an `is_error` tool_result: a typed kind we
    author, then the free-text detail, fenced.

    Distinct from the `tool_error` SSE frame, which goes to the user's own
    browser and may legitimately name a lock holder — that is the product's
    intent and the frontend reads `detail.lock` for its banner. THIS string goes
    to the third-party LLM provider and into `session.messages`, so it is
    replayed on every later turn.

    Two properties, both of which the previous `str(detail or exc)` broke:

    1. **No other user's identity.** A lock refusal's `detail` carries
       `{"lock": {"holder_email": ...}}` (`services/project_locks.py`), and
       flattening the dict sent that address to the provider on every turn
       (`findings/2026-08-27-lock-holder-email-reaches-the-model.md`). So a dict
       detail contributes ONLY its human-readable `message`; every other key
       exists for the frontend and the model has no use for it. A dict with no
       `message` contributes nothing but the kind — safe by default, rather than
       dumping unknown keys and hoping none of them identifies somebody.
       Note `_redact_secrets_in_str` does NOT cover this: it targets API keys,
       bearer tokens and `key=value` pairs, and has no notion of an address.

    2. **The free text is fenced.** `_result_to_anthropic_content` leaves the
       is_error path unwrapped on the grounds that it carries "short typed
       error_kinds the model must act on, not untrusted free text". True of the
       three sites that pass a constant; false here, where an exception message
       interpolates component names. The kind stays OUTSIDE the fence because we
       author it and the model must act on it; the detail goes INSIDE.

    Residual, stated rather than hidden: a bare (non-dict) exception whose own
    message embeds an address would still pass it through. No code path
    currently does that — `project_locks` puts the address in the dict, never in
    the message — so the structural fix covers the real path, and a general
    address scrub here would mangle more than it protects.
    """
    free_text: str | None = None
    if isinstance(detail, dict):
        message = detail.get("message")
        free_text = str(message) if message is not None else None
    elif detail is not None:
        free_text = str(detail)
    else:
        free_text = str(exc)

    if not free_text:
        # Nothing safe to say beyond the kind. The model can still act on it.
        return error_kind

    free_text = _redact_secrets_in_str(free_text[:_ERROR_DETAIL_CAP])
    free_text = harness_fence._neutralise_untrusted_delimiters(free_text)
    return f"{error_kind}\n{_UNTRUSTED_OPEN}\n{free_text}\n{_UNTRUSTED_CLOSE}"


def _result_to_anthropic_content(result: Any) -> Any:
    """
    Convert a Python tool result into the Anthropic tool_result content
    shape. The SDK accepts strings or content-block lists; for dicts/lists
    we stringify to keep the type contract simple.

    Oversized dict/list payloads are cut to `_RESULT_CONTENT_CAP` chars with an
    EXPLICIT marker appended — a silent cut yields invalid/partial JSON the
    model would wrongly treat as the complete result.

    Prompt-injection boundary (#2): the model-facing body is wrapped in
    `_UNTRUSTED_OPEN`/`_UNTRUSTED_CLOSE` delimiters so the system-prompt clause
    can treat tool-result text (which can echo user-controlled names, file
    contents, audit-log lines) as DATA, not instructions. The truncation marker
    stays INSIDE the closing delimiter so the model reads it as part of the
    data. Plain-string results are wrapped too — they carry the same untrusted
    free text.

    The wrap is only worth something because the body is run through
    `_neutralise_untrusted_delimiters` first: a result echoing a component name
    could otherwise carry the closing delimiter itself and end the data region
    early, which is a real prompt-injection primitive rather than a theoretical
    one (see
    `docs/superpowers/findings/2026-09-10-a-tool-result-can-close-the-untrusted-fence.md`).

    This wraps ONLY the success path. The `is_error` content is built by
    `_error_result_content`, which fences its own free-text half — so the error
    path is no longer unfenced, and this docstring no longer claims it is. It
    said the opposite (that leaving is_error unwrapped was a deliberate choice
    pending a decision) for one session after that decision was made and acted
    on; an independent QA review caught the contradiction. Kept as a pointer
    rather than deleted, because the reasoning still matters: three of the four
    is_error sites pass a typed constant and need no fence, and the fourth
    passes exception text, which does.
    """
    if isinstance(result, str):
        # Capped like the json branch. Previously only the `else` applied
        # `_RESULT_CONTENT_CAP`, leaving a second uncapped entry into the
        # neutraliser. No dispatcher returns a large raw string today, so this is
        # pre-emptive rather than a live fix -- but "no caller does that yet" is
        # the assumption the quadratic cost was hiding behind.
        body = result
        if len(body) > _RESULT_CONTENT_CAP:
            body = body[:_RESULT_CONTENT_CAP] + _truncation_marker(
                len(body), _RESULT_CONTENT_CAP,
            )
    else:
        try:
            import json
            body = json.dumps(result, default=str)
        except Exception:  # noqa: BLE001
            body = str(result)
        cap = _RESULT_CONTENT_CAP
        if len(body) > cap:
            body = body[:cap] + _truncation_marker(len(body), cap)
    # Neutralised at the single return point, not per branch: the string
    # passthrough and the json+truncate path both reach here, so one line
    # covers every body and a future third branch cannot forget it. Order
    # relative to the truncation cut is not load-bearing for safety -- a cut
    # only removes characters, so it cannot form a delimiter out of text that
    # no longer contains one.
    body = harness_fence._neutralise_untrusted_delimiters(body)
    return f"{_UNTRUSTED_OPEN}\n{body}\n{_UNTRUSTED_CLOSE}"
