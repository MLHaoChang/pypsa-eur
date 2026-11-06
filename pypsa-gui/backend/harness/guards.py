"""Required guards before any effect (assistant assurance issue 04, S4a).

Reliability contract §4 and GK B (27) G1-G4, G7. This is the REQUIRED path,
separate from the advisory `services.chat_tools.PRE_DISPATCH_VALIDATORS`: a
write or destructive tool with no entry in `REQUIRED_GUARDS`, or whose guard
reports a problem or raises, is denied `required_guard_failed` before
restrictions, task admission, cards, the confirmation, the reservation, the
dirty mark, the undo snapshot and the write note (fail closed).

Every catalogue write/destructive tool gets the schema guard: its offered
`input_schema` checked by a small JSON-Schema subset validator. The subset is
the keywords those schemas use; any other keyword raises `UnsupportedSchema`,
which the guard turns into a denial. The `jsonschema` package is a test-only
dependency, so it is never imported here; a test checks the two agree.

External MCP tools (the loop's `external`) are out of scope: their schemas
come from a third-party server, and the loop skips `check` for them.

`fingerprint` is the confirmation's binding: the loop takes it over the args
the handler will receive, before the card, and again after the approval,
right before the reservation; a mismatch is `confirmation_args_changed`.

The no-progress stop (G7) keys a failing call on (tool, fingerprint): a
`tool_error`, an is_error result or an outcome failed/denied. The count is
kept per turn and per run (on the ChatSession, across turns); a success on
the key resets both. At `budget.NO_PROGRESS_PER_TURN` or
`budget.NO_PROGRESS_PER_RUN` the next identical call is refused before
dispatch with `no_progress_stop`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Generator
from typing import Any

from harness import budget, catalogue

logger = logging.getLogger(__name__)

EFFECT_TIERS = frozenset({"write", "destructive"})
GUARD_FAILED = "required_guard_failed"
ARGS_CHANGED = "confirmation_args_changed"
NO_PROGRESS = "no_progress_stop"
# Literals for tests/test_tool_error_kind_manifest.py, which reads every kind.
_KINDS = (
    {"error_kind": "required_guard_failed"},
    {"error_kind": "confirmation_args_changed"},
    {"error_kind": "no_progress_stop"},
)


# ── The JSON-Schema subset ───────────────────────────────────────────────────


class UnsupportedSchema(ValueError):
    """A schema node uses a keyword or type this validator does not implement."""


_ANNOTATIONS = frozenset({"description", "title", "default", "examples", "$comment"})
_KEYWORDS = _ANNOTATIONS | {
    "type",
    "properties",
    "required",
    "enum",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "pattern",
    "additionalProperties",
}
_TYPES = frozenset(
    {"null", "boolean", "integer", "number", "string", "array", "object"}
)


def _node(schema: Any) -> dict:
    if not isinstance(schema, dict):
        raise UnsupportedSchema(
            f"schema node is {type(schema).__name__}, not an object"
        )
    unknown = set(schema) - _KEYWORDS
    if unknown:
        raise UnsupportedSchema(f"unsupported keyword(s) {sorted(unknown)}")
    types = schema.get("type")
    for t in types if isinstance(types, list) else [types] if types is not None else []:
        if t not in _TYPES:
            raise UnsupportedSchema(f"unsupported type {t!r}")
    return schema


def check_schema(schema: Any) -> None:
    """Walk a whole schema; raise UnsupportedSchema on any unknown keyword."""
    node = _node(schema)
    for child in (node.get("properties") or {}).values():
        check_schema(child)
    if "items" in node:
        check_schema(node["items"])
    extra = node.get("additionalProperties")
    if extra is not None and not isinstance(extra, bool):
        check_schema(extra)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_type(v: Any, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":  # 2020-12: 1.0 is an integer
        return _is_number(v) and (isinstance(v, int) or float(v).is_integer())
    if t == "number":
        return _is_number(v)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    raise UnsupportedSchema(f"unsupported type {t!r}")


def _json_equal(a: Any, b: Any) -> bool:
    """JSON equality: 1 == 1.0, but true is not 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _where(path: str) -> str:
    return path or "the arguments"


def schema_error(value: Any, schema: Any, path: str = "") -> str | None:
    """The first way `value` breaks `schema`, or None. The message names the
    path and the expectation, never the value."""
    node = _node(schema)
    types = node.get("type")
    if types is not None:
        wanted = types if isinstance(types, list) else [types]
        if not any(_is_type(value, t) for t in wanted):
            return f"{_where(path)}: expected {' or '.join(wanted)}"
    if "enum" in node and not any(_json_equal(value, e) for e in node["enum"]):
        return f"{_where(path)}: not one of the allowed values"
    if isinstance(value, str):
        if "minLength" in node and len(value) < node["minLength"]:
            return f"{_where(path)}: shorter than {node['minLength']} characters"
        if "maxLength" in node and len(value) > node["maxLength"]:
            return f"{_where(path)}: longer than {node['maxLength']} characters"
        if "pattern" in node and re.search(node["pattern"], value) is None:
            return f"{_where(path)}: does not match the required pattern"
    if _is_number(value):
        if "minimum" in node and value < node["minimum"]:
            return f"{_where(path)}: below the minimum {node['minimum']}"
        if "maximum" in node and value > node["maximum"]:
            return f"{_where(path)}: above the maximum {node['maximum']}"
        if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"]:
            return f"{_where(path)}: must be above {node['exclusiveMinimum']}"
    if isinstance(value, list):
        if "minItems" in node and len(value) < node["minItems"]:
            return f"{_where(path)}: fewer than {node['minItems']} items"
        if "maxItems" in node and len(value) > node["maxItems"]:
            return f"{_where(path)}: more than {node['maxItems']} items"
        if "items" in node:
            for i, item in enumerate(value):
                problem = schema_error(item, node["items"], f"{path}[{i}]")
                if problem:
                    return problem
    if isinstance(value, dict):
        for key in node.get("required") or ():
            if key not in value:
                return f"{_where(path)}: missing required {key!r}"
        props = node.get("properties") or {}
        extra = node.get("additionalProperties", True)
        for key, item in value.items():
            where = f"{path}.{key}" if path else str(key)
            if key in props:
                problem = schema_error(item, props[key], where)
            elif extra is False:
                problem = f"{_where(path)}: unexpected property {key!r}"
            elif isinstance(extra, dict):
                problem = schema_error(item, extra, where)
            else:
                problem = None
            if problem:
                return problem
    return None


# ── The registry ─────────────────────────────────────────────────────────────

Guard = Callable[[dict], "str | None"]


def schema_guards(schema: dict) -> tuple[Guard, ...]:
    """The guards every effectful tool has: its offered input schema."""

    def schema_guard(args: dict) -> str | None:
        check_schema(schema)  # an unknown keyword raises: the call is denied
        return schema_error(args, schema)

    return (schema_guard,)


def _build() -> dict[str, tuple[Guard, ...]]:
    return {
        tool["name"]: schema_guards(tool["input_schema"])
        for tool in catalogue.TOOLS
        if catalogue.safety_tier_for(tool["name"]) in EFFECT_TIERS
    }


REQUIRED_GUARDS: dict[str, tuple[Guard, ...]] = _build()


def check(tool_name: str, args: Any, tier: str) -> tuple[str, str] | None:
    """None to proceed, else (error_kind, message). A write/destructive tool
    with no entry, a guard that reports a problem and a guard that raises are
    all denied; nothing here ever passes a call it could not check."""
    if tier not in EFFECT_TIERS:
        return None
    entry = REQUIRED_GUARDS.get(tool_name)
    if not entry:
        return GUARD_FAILED, f"{tool_name!r} has no required guard, so it did not run."
    for guard in entry:
        try:
            problem = guard(args)
        except Exception:  # noqa: BLE001 — a guard that cannot run denies
            logger.exception("chat: required guard for %r raised", tool_name)
            return (
                GUARD_FAILED,
                f"A required check for {tool_name!r} could not run, so it did not run.",
            )
        if problem:
            return GUARD_FAILED, f"{tool_name!r} did not run: {problem}."
    return None


# ── The confirmation binding ─────────────────────────────────────────────────


def _is_string_schema(schema: dict) -> bool:
    types = schema.get("type")
    return types == "string" or (isinstance(types, list) and "string" in types)


def _canon(value: Any, schema: Any) -> Any:
    schema = schema if isinstance(schema, dict) else {}
    if _is_string_schema(schema) and (value is None or value == ""):
        return ""  # one missing string, as harness/outcomes.py `_canon_col`
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        extra = schema.get("additionalProperties")
        return {
            k: _canon(v, props.get(k, extra if isinstance(extra, dict) else None))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_canon(v, schema.get("items")) for v in value]
    return value


def schema_for(tool_name: str) -> dict | None:
    for tool in catalogue.TOOLS:
        if tool["name"] == tool_name:
            return tool["input_schema"]
    return None


def fingerprint(args: Any, schema: dict | None = None) -> str:
    """sha256 of the canonical JSON of `args`: sorted keys, an integral float
    as an int, a bool never a number, "" and None one value only where the
    schema says string, a missing key apart from an explicit null."""
    canon = _canon(args, schema)
    text = json.dumps(canon, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def denied(
    tool_use_id: str,
    tool_name: str,
    kind: str,
    message: str,
    collector: list[dict[str, Any]],
    detail: Any = None,
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """A pre-effect refusal: the `tool_error` frame carrying the denied outcome
    and the paired is_error result."""
    from harness.outcomes import denied as denied_outcome
    from harness.results import _error_result_content

    yield (
        "tool_error",
        {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "error_kind": kind,
            "message": message,
            "_outcome": denied_outcome(kind),
        },
    )
    collector.append(
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": _error_result_content(detail or message, None, kind),
        }
    )


# ── The edit-lock refusal (issue 04 S4b, G4) ────────────────────────────────

# The two refusals of `chat_tools._check_foreign_lock`: another user's edit lock
# or a harness run's hold (409), or a lock that could not be checked (503).
LOCK_KINDS = frozenset({"project_locked", "project_lock_unavailable"})


def lock_refusal(tool_name: str, args: Any) -> tuple[str, str] | None:
    """`(kind, message)` when the edit lock refuses this call, else None.

    The loop calls this after the confirmation and before any effect seam
    (reservation, undo snapshot, write note, dirty mark, revision bump), so a
    refusal here is `denied`. Only the tools the handler's own check gates;
    the same task_id argument, so a run control on the caller's run passes.
    """
    from fastapi import HTTPException

    from services import chat_tools

    if tool_name not in chat_tools._lock_gated_tool_names():
        return None
    task_id = args.get("task_id") if isinstance(args, dict) else None
    try:
        chat_tools._check_foreign_lock(tool_name, task_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        kind = detail.get("error_kind")
        if kind not in LOCK_KINDS:
            raise
        return kind, str(detail.get("message") or kind)
    return None


# ── The no-progress stop ─────────────────────────────────────────────────────


def start_turn(session: Any) -> None:
    """A new turn: the per-turn counts start again; the per-run ones stay."""
    session.turn_no_progress = {}


def _key(tu: dict[str, Any]) -> tuple[str, str]:
    name = str(tu.get("name"))
    try:
        return name, fingerprint(tu.get("input") or {}, schema_for(name))
    except (TypeError, ValueError):  # unfingerprintable args are one key
        return name, "unfingerprintable"


def no_progress(
    session: Any,
    tu: dict[str, Any],
    collector: list[dict[str, Any]],
) -> Generator[tuple[str, dict[str, Any]], None, bool]:
    """Refuse a call whose key already failed up to the bound. Returns True
    when it refused (nothing was dispatched)."""
    key = _key(tu)
    turn = session.turn_no_progress.get(key, 0)
    run = session.run_no_progress.get(key, 0)
    if turn + 1 < budget.NO_PROGRESS_PER_TURN and run + 1 < budget.NO_PROGRESS_PER_RUN:
        return False
    message = (
        f"{key[0]!r} failed {max(turn, run)} times on these exact arguments; it was "
        "not run again. Correct the arguments (the tool, the target or a value the "
        "last error named), or tell the user what blocks it."
    )
    yield from denied(tu.get("id"), key[0], NO_PROGRESS, message, collector)
    _count(session, key, failing=True)
    return True


def _count(session: Any, key: tuple[str, str], *, failing: bool) -> None:
    for counts in (session.turn_no_progress, session.run_no_progress):
        if failing:
            counts[key] = counts.get(key, 0) + 1
        else:
            counts.pop(key, None)


def observed(
    session: Any,
    tu: dict[str, Any],
    dispatch: Generator[tuple[str, dict[str, Any]], Any, Any],
    collector: list[dict[str, Any]],
) -> Generator[tuple[str, dict[str, Any]], Any, Any]:
    """Forward one dispatch's frames and return its value; afterwards count
    the call as failing (a tool_error, an is_error result, an outcome
    failed/denied) or reset its key on a success."""
    tool_use_id = tu.get("id")
    start = len(collector)
    failing = False
    try:
        frame = next(dispatch)
        while True:
            event, data = frame
            # A tool_error always pairs an is_error result, read below; a
            # result frame can still carry an outcome failed/denied.
            if (
                event == "tool_result"
                and isinstance(data, dict)
                and data.get("tool_use_id") == tool_use_id
            ):
                result = data.get("result")
                outcome = result.get("_outcome") if isinstance(result, dict) else None
                if isinstance(outcome, dict) and outcome.get("outcome") in (
                    "failed",
                    "denied",
                ):
                    failing = True
            try:
                sent = yield frame
            except GeneratorExit:
                dispatch.close()
                raise
            except BaseException as exc:  # noqa: BLE001 — forwarded as `yield from` does
                frame = dispatch.throw(exc)
            else:
                frame = dispatch.send(sent)
    except StopIteration as stop:
        value = stop.value
    failing = failing or any(
        r.get("tool_use_id") == tool_use_id and r.get("is_error")
        for r in collector[start:]
    )
    _count(session, _key(tu), failing=failing)
    return value
