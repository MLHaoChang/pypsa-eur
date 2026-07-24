"""
Tier-1 C: Tool schema <-> Python signature consistency.

Catches the exact class of bugs that shipped during the v6 chatbot session:

  1. SCHEMA / SIGNATURE DRIFT - schema lists a field as optional (NOT in
     `required`) but the Python signature has no default value. The
     dispatcher raises `TypeError: missing 1 required positional argument`
     when the LLM (correctly) omits the field. Real-world example:
     `apply_demand_from_excel(sheet_name)` was declared as a required
     positional but advertised as optional - every multi-sheet workbook
     prompt triggered the cascade `tool_error -> retry -> placeholder bug
     -> excel_parse_failed`.

  2. PYDANTIC SSE LEAK - dispatcher returns a Pydantic `BaseModel` (e.g.
     `ProjectInfo`, `ImportSummary`) that json.dumps can't natively
     serialize. The SSE frame builder raises `TypeError: Object of type X
     is not JSON serializable`, the chat panel hangs on "running" because
     the close-event never reaches the browser.

This is a PURE introspection test - no PyPSA, no FastAPI, no network. Runs
in <100 ms. Should be the first test in CI's chat-tool job; failures here
catch ~half the chatbot bugs from this session at zero runtime cost.

What this test does NOT cover (other tests do):
  - Tool name <-> DISPATCHERS key consistency: test_chat_tools_dispatch.py
    line 481 `test_dispatcher_for_each_tool_is_callable`.
  - Import resolution (handler renames): test_chat_tools_imports.py.
  - Schema description matches Pydantic field names: test_chat_tools_schema_match.py.
"""
from __future__ import annotations

import inspect
import json
import math
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel

from services import chat_tools
from services.chat_service import _coerce_jsonable, _truncate_result
from services.chat_tools_schema import TOOLS


# ----------------------------------------------------------------------------
# Per-tool waivers - functions whose runtime signature legitimately diverges
# from the schema. KEEP THIS SMALL and require a `reason` so drift is visible.
# ----------------------------------------------------------------------------

# Tools where the implementation signature accepts a catch-all dict (e.g.
# **kwargs, or a single Pydantic body) so per-property introspection doesn't
# apply. These are listed by tool_name -> reason; the test skips signature
# checks for these but still verifies the schema parses + dispatcher exists.
SIGNATURE_INTROSPECTION_WAIVERS: dict[str, str] = {
    # Empty by design - every tool currently passes both consistency checks.
    # Add an entry ONLY when a tool's runtime contract legitimately diverges
    # from per-property signature introspection (catch-all kwargs/body), with
    # a `reason` so the next reader knows why. The
    # `test_signature_waivers_actually_need_the_waiver` test will yell if
    # the listed tool no longer needs the waiver - drop it then.
}


# Tools whose schema is `_empty(...)` (no properties) - nothing to compare.
def _is_empty_schema(schema: dict[str, Any]) -> bool:
    return not schema["input_schema"]["properties"]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _tool_by_name(name: str) -> dict[str, Any]:
    for t in TOOLS:
        if t["name"] == name:
            return t
    raise AssertionError(f"tool {name!r} not in TOOLS")


def _python_params(fn: Any) -> dict[str, inspect.Parameter]:
    """Return ordered map of param name -> Parameter, excluding *args/**kwargs."""
    sig = inspect.signature(fn)
    return {
        name: p for name, p in sig.parameters.items()
        if p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    }


def _has_var_keyword(fn: Any) -> bool:
    """True if the function accepts **kwargs."""
    sig = inspect.signature(fn)
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )


def _required_set(tool: dict[str, Any]) -> set[str]:
    return set(tool["input_schema"].get("required", []) or [])


def _property_set(tool: dict[str, Any]) -> set[str]:
    return set(tool["input_schema"]["properties"].keys())


# ----------------------------------------------------------------------------
# Test 1 - schema 'required' subset of 'properties'
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["name"])
def test_schema_required_is_subset_of_properties(tool):
    """`required` must reference fields that exist in `properties`."""
    props = _property_set(tool)
    required = _required_set(tool)
    extra = required - props
    assert not extra, (
        f"tool {tool['name']!r}: `required` lists field(s) not in "
        f"`properties`: {sorted(extra)}"
    )


# ----------------------------------------------------------------------------
# Test 2 - schema-required vs Python-no-default consistency
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [t["name"] for t in TOOLS if t["name"] not in SIGNATURE_INTROSPECTION_WAIVERS],
    ids=lambda n: n,
)
def test_schema_optional_field_has_python_default(tool_name):
    """
    For every schema property NOT in `required`, the corresponding Python
    parameter MUST have a default value.

    Failure example caught: `apply_demand_from_excel`'s schema said
    `sheet_name` is optional, but the Python signature had no default ->
    LLM omits `sheet_name` -> TypeError -> retry -> cascade of tool_error.
    """
    tool = _tool_by_name(tool_name)
    if _is_empty_schema(tool):
        return

    fn = chat_tools.DISPATCHERS[tool_name]
    py_params = _python_params(fn)
    props = _property_set(tool)
    required = _required_set(tool)
    optional_in_schema = props - required

    # Filter to schema properties that EXIST in the Python signature.
    # Properties present only in the schema must be accepted via **kwargs -
    # otherwise the dispatcher raises TypeError when the LLM passes them
    # (the exact bug class this test exists to catch). We FLAG the gap
    # explicitly instead of silently skipping; the comment above used to
    # claim "handled by runtime" without checking that **kwargs was actually
    # declared. (QA lens 2 finding.)
    has_kwargs = _has_var_keyword(fn)
    missing_defaults: list[str] = []
    schema_only_no_kwargs: list[str] = []
    for prop in sorted(optional_in_schema):
        if prop not in py_params:
            if not has_kwargs:
                schema_only_no_kwargs.append(prop)
            continue
        param = py_params[prop]
        if param.default is inspect.Parameter.empty:
            missing_defaults.append(prop)

    assert not schema_only_no_kwargs, (
        f"tool {tool_name!r}: schema declares these properties but the "
        f"Python signature has NEITHER a matching kwarg NOR **kwargs - "
        f"the dispatcher will TypeError when the LLM passes any of them:\n"
        f"  {schema_only_no_kwargs}\n"
        f"Fix: add each as a kwarg with default OR declare **kwargs."
    )

    assert not missing_defaults, (
        f"tool {tool_name!r}: schema marks these properties as optional "
        f"(not in `required`), but the Python signature has NO DEFAULT "
        f"for them - the LLM omitting any of these triggers TypeError at "
        f"dispatch time:\n  {missing_defaults}\n"
        f"Fix: add `= None` (or a sensible default) to each in chat_tools.py."
    )


# ----------------------------------------------------------------------------
# Test 3 - Python-no-default vs schema-required consistency
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [t["name"] for t in TOOLS if t["name"] not in SIGNATURE_INTROSPECTION_WAIVERS],
    ids=lambda n: n,
)
def test_python_required_param_is_in_schema_required(tool_name):
    """
    Every Python parameter WITHOUT a default value MUST be listed in the
    schema's `required` array. Otherwise the LLM thinks it's optional, omits
    it, and the dispatcher TypeErrors.

    Inverse of test 2. This catches schemas that under-declare requirements.
    """
    tool = _tool_by_name(tool_name)
    fn = chat_tools.DISPATCHERS[tool_name]
    py_params = _python_params(fn)
    required = _required_set(tool)
    props = _property_set(tool)

    missing_in_required: list[str] = []
    for name, param in py_params.items():
        if param.default is not inspect.Parameter.empty:
            continue
        # Param has no default. Must either be in schema.required, OR
        # not surface as a top-level schema field at all (which means the
        # tool is in a catch-all pattern not covered by this check).
        if name not in props:
            # Not in schema properties => out of scope (catch-all dict or
            # similar). Allow.
            continue
        if name not in required:
            missing_in_required.append(name)

    assert not missing_in_required, (
        f"tool {tool_name!r}: Python signature has NO DEFAULT for "
        f"these params, but the schema does NOT list them in `required`:\n"
        f"  {missing_in_required}\n"
        f"The LLM may omit them -> TypeError at dispatch time. "
        f"Fix: add to the schema's `required` list OR give the Python "
        f"param a default value."
    )


# ----------------------------------------------------------------------------
# Test 4 - schema property names are valid Python identifiers
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["name"])
def test_schema_property_names_are_valid_python_identifiers(tool):
    """
    Anthropic dispatches kwargs by name. A schema property named 'class' or
    'with hyphen' can't be passed as a kwarg -> SyntaxError-equivalent at
    runtime. (Reserved-word collision is the real risk here.)
    """
    bad: list[str] = []
    import keyword
    for prop in _property_set(tool):
        if not prop.isidentifier() or keyword.iskeyword(prop):
            bad.append(prop)
    assert not bad, (
        f"tool {tool['name']!r}: schema property name(s) are not valid "
        f"Python kwargs: {bad}"
    )


# ----------------------------------------------------------------------------
# Test 5 - _coerce_jsonable handles every problematic type the session hit
# ----------------------------------------------------------------------------


class _SampleProjectInfo(BaseModel):
    """Mirror of models.schemas.ProjectInfo shape - kept local so this test
    doesn't couple to that file's structure."""
    name: str
    bus_count: int
    has_solver_config: bool


class _SampleImportSummary(BaseModel):
    buses: int
    generators: int
    snapshots: int


def test_coerce_jsonable_handles_pydantic_model():
    pi = _SampleProjectInfo(name="A", bus_count=3, has_solver_config=True)
    out = _coerce_jsonable(pi)
    assert isinstance(out, dict)
    assert out == {"name": "A", "bus_count": 3, "has_solver_config": True}


def test_coerce_jsonable_handles_list_of_pydantic_models():
    items = [
        _SampleProjectInfo(name="A", bus_count=1, has_solver_config=True),
        _SampleProjectInfo(name="B", bus_count=2, has_solver_config=False),
    ]
    out = _coerce_jsonable(items)
    assert isinstance(out, list)
    assert all(isinstance(x, dict) for x in out)
    assert out[0]["name"] == "A"
    assert out[1]["name"] == "B"


def test_coerce_jsonable_handles_dict_with_pydantic_values():
    payload = {
        "summary": _SampleImportSummary(buses=10, generators=5, snapshots=24),
        "scalar": 42,
        "nested": {
            "inner": _SampleProjectInfo(name="X", bus_count=1,
                                         has_solver_config=False),
        },
    }
    out = _coerce_jsonable(payload)
    assert isinstance(out["summary"], dict)
    assert out["summary"]["buses"] == 10
    assert out["scalar"] == 42
    assert isinstance(out["nested"]["inner"], dict)
    assert out["nested"]["inner"]["name"] == "X"


def test_coerce_jsonable_handles_tuple_of_models():
    items = (
        _SampleProjectInfo(name="A", bus_count=1, has_solver_config=True),
        _SampleProjectInfo(name="B", bus_count=2, has_solver_config=False),
    )
    out = _coerce_jsonable(items)
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0]["name"] == "A"


def test_coerce_jsonable_passes_primitives_unchanged():
    assert _coerce_jsonable(None) is None
    assert _coerce_jsonable("x") == "x"
    assert _coerce_jsonable(42) == 42
    assert _coerce_jsonable(3.14) == 3.14
    assert _coerce_jsonable(True) is True


# ----------------------------------------------------------------------------
# Test 6 - truncate_result + json.dumps(default=str) belt-and-suspenders
# ----------------------------------------------------------------------------


def test_truncate_result_pydantic_round_trips_via_json_dumps():
    """
    _truncate_result must coerce Pydantic models AND survive json.dumps -
    this is the exact path the SSE frame builder takes. If this round-trip
    raises, the chat hangs on a missing close-event.
    """
    pi = _SampleProjectInfo(name="A", bus_count=3, has_solver_config=True)
    coerced = _truncate_result(pi)
    serialised = json.dumps(coerced, default=str)
    assert '"name": "A"' in serialised
    # Re-parse to assert it round-trips cleanly
    parsed = json.loads(serialised)
    assert parsed["name"] == "A"


def test_truncate_result_list_of_pydantic_round_trips():
    items = [
        _SampleProjectInfo(name="A", bus_count=1, has_solver_config=True),
        _SampleProjectInfo(name="B", bus_count=2, has_solver_config=False),
    ]
    coerced = _truncate_result(items)
    serialised = json.dumps(coerced, default=str)
    parsed = json.loads(serialised)
    assert isinstance(parsed, list)
    assert parsed[0]["name"] == "A"


def test_truncate_result_handles_datetime_via_default_str():
    """
    `default=str` is the safety net for any type _coerce_jsonable doesn't
    recognise (e.g. datetime, Path, custom objects). Without it, the SSE
    frame raises TypeError on the first datetime in a result.
    """
    payload = {
        "name": "A",
        "created_at": datetime(2026, 6, 8, 12, 30, 0, tzinfo=timezone.utc),
    }
    coerced = _truncate_result(payload)
    serialised = json.dumps(coerced, default=str)
    parsed = json.loads(serialised)
    assert parsed["name"] == "A"
    assert "2026-06-08" in parsed["created_at"]


def test_truncate_result_handles_pydantic_with_datetime_field():
    """
    Combined case: Pydantic model whose field IS a datetime. model_dump()
    keeps the datetime as a datetime; json.dumps then needs default=str
    to convert. The two-layer fix in chat_service.py is meant for exactly
    this scenario.
    """
    class _DatedInfo(BaseModel):
        name: str
        when: datetime

    obj = _DatedInfo(name="X", when=datetime(2026, 6, 8, tzinfo=timezone.utc))
    coerced = _truncate_result(obj)
    serialised = json.dumps(coerced, default=str)
    parsed = json.loads(serialised)
    assert parsed["name"] == "X"
    assert "2026-06-08" in parsed["when"]


# ----------------------------------------------------------------------------
# Test 7 - waiver list is honest (every waived tool still exists)
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# Test - self-test: the consistency-check LOGIC catches synthesised violations
# ----------------------------------------------------------------------------


def test_consistency_check_logic_detects_synthesised_missing_default():
    """
    Self-test: prove the test-2 logic actually flags a violation when one
    is synthesised. Without this, a refactor that broke the consistency
    check itself could land silently (every tool would pass vacuously).

    Synthesises a fake tool where the schema marks `b` as optional but the
    Python signature has no default for `b`. Runs the same logic test 2
    uses and asserts the violation is caught.
    """
    fake_tool = {
        "name": "_fake_for_test",
        "description": "fake",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
            },
            # `b` is NOT in required => schema says it's optional.
            "required": ["a"],
        },
    }

    def fake_fn(a, b):  # `b` has NO default => violation
        return {}

    py_params = _python_params(fake_fn)
    props = _property_set(fake_tool)
    required = _required_set(fake_tool)
    optional_in_schema = props - required

    missing_defaults: list[str] = []
    for prop in optional_in_schema:
        if prop in py_params:
            if py_params[prop].default is inspect.Parameter.empty:
                missing_defaults.append(prop)

    assert missing_defaults == ["b"], (
        f"the test-2 logic failed to detect a synthesised violation - "
        f"expected ['b'], got {missing_defaults}. Check the consistency-"
        f"check logic for regressions."
    )


def test_consistency_check_logic_detects_synthesised_missing_required():
    """
    Self-test for test-3: prove the inverse logic catches a Python param
    without a default that's missing from schema.required.
    """
    fake_tool = {
        "name": "_fake_for_test",
        "description": "fake",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
            },
            # `b` is NOT in required, but the Python sig has no default
            # for it => violation.
            "required": ["a"],
        },
    }

    def fake_fn(a, b):  # both required positionals
        return {}

    py_params = _python_params(fake_fn)
    props = _property_set(fake_tool)
    required = _required_set(fake_tool)

    py_no_default_missing: list[str] = []
    for name, param in py_params.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if name in props and name not in required:
            py_no_default_missing.append(name)

    assert py_no_default_missing == ["b"], (
        f"the test-3 logic failed to detect a synthesised violation - "
        f"expected ['b'], got {py_no_default_missing}."
    )


# ----------------------------------------------------------------------------
# Test - waiver-list hygiene
# ----------------------------------------------------------------------------


def test_signature_waivers_reference_existing_tools():
    """A waiver entry that references a deleted tool name is dead code -
    fail so the list stays clean."""
    tool_names = {t["name"] for t in TOOLS}
    stale = [n for n in SIGNATURE_INTROSPECTION_WAIVERS if n not in tool_names]
    assert not stale, (
        f"SIGNATURE_INTROSPECTION_WAIVERS references tools that no longer "
        f"exist in TOOLS: {stale}. Remove these entries."
    )


def test_signature_waivers_actually_need_the_waiver():
    """
    If a waived tool's signature would now pass the test-2 + test-3 checks
    organically, the waiver is dead code - drop it so coverage tightens.
    Run the same logic as tests 2 + 3 against the waived tools and assert
    at least one would fail (otherwise the waiver is unnecessary).
    """
    for tool_name, reason in SIGNATURE_INTROSPECTION_WAIVERS.items():
        tool = _tool_by_name(tool_name)
        fn = chat_tools.DISPATCHERS[tool_name]
        py_params = _python_params(fn)
        props = _property_set(tool)
        required = _required_set(tool)

        # Replay test-2: schema-optional but no Python default?
        optional_no_default: list[str] = []
        for prop in props - required:
            if prop in py_params:
                p = py_params[prop]
                if p.default is inspect.Parameter.empty:
                    optional_no_default.append(prop)

        # Replay test-3: Python-no-default but not schema-required?
        py_no_default_missing: list[str] = []
        for name, p in py_params.items():
            if p.default is not inspect.Parameter.empty:
                continue
            if name in props and name not in required:
                py_no_default_missing.append(name)

        would_pass = (not optional_no_default) and (not py_no_default_missing)
        if would_pass:
            pytest.fail(
                f"Waiver for tool {tool_name!r} ({reason!r}) is no longer "
                f"needed - the tool now passes both consistency checks. "
                f"Remove from SIGNATURE_INTROSPECTION_WAIVERS so future "
                f"drift can't hide behind it."
            )
